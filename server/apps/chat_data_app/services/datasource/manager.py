"""
数据源管理服务。

当前运行时仅使用 MySQL 表，不再读写本地 JSON 文件。
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from utils.logger import get_logger

from ...daos.datasource_dao import DataSourceDao
from ...daos.datasource_dao_helpers import VALID_TABLE_INDEX_TYPES, _normalize_table_index_items
from .credentials import resolve_execution_source_config, sanitize_source_config
from .metadata import (
    DataSourceMetadataAdapter,
    DataSourceMetadataAdapterRegistry,
    MetadataAdapterNotFoundError,
    get_metadata_adapter_registry,
)
from .permission import (
    datasource_requires_http_permission,
    filter_datasource_members_by_allowed_org_codes,
    filter_datasources_by_allowed_org_codes,
    get_allowed_org_codes_by_executor,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class DatasourceWriteAudit:
    """数据源写入的操作人审计字段（创建人 / 更新人及其显示名）。

    四个字段总是结伴出现（写入组信息时同时用于组表、成员表与表结构），收敛成对象以保证
    ``_save_datasource_async`` 的形参数量落在 AGENTS.md §3 的阈值内。
    """

    create_by: str = ""
    update_by: str = ""
    create_by_name: str = ""
    update_by_name: str = ""


_UNSET = object()
SUPPORTED_QUERY_EXECUTOR_TYPES = {"direct", "http"}
MAX_TABLE_INDEX_COUNT = 5
MAX_INDEX_COLUMN_COUNT = 5
INDEX_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _read_positive_int_env(name: str, default: int) -> int:
    raw_value = str(os.getenv(name) or "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是合法整数，回退默认值 %s", name, raw_value, default)
        return default
    if value < 1:
        logger.warning("环境变量 %s=%s 小于 1，回退默认值 %s", name, value, default)
        return default
    return value


# 同步总超时已迁移到后台配置变量表（CHAT_DATA_DATASOURCE_SYNC_TIMEOUT_SECONDS，运行期读取），
# 统一从 get_datasource_sync_timeout_seconds 取值；本模块常量仅保留导入期兜底快照。
DATASOURCE_SYNC_TIMEOUT_SECONDS = _read_positive_int_env("CHAT_DATA_DATASOURCE_SYNC_TIMEOUT_SECONDS", 30)


async def get_datasource_sync_timeout_seconds() -> int:
    """数据源同步任务总超时（秒）：表覆盖 > env > 代码默认。"""
    from ..setting_service import get_runtime_setting

    return await get_runtime_setting("CHAT_DATA_DATASOURCE_SYNC_TIMEOUT_SECONDS")


def _validate_table_index_name(index_type: str, index_name: Any, physical_names: set[str]) -> str:
    normalized = str(index_name or "").strip()
    if index_type == "primary":
        return "PRIMARY"
    if not normalized:
        raise ValueError("唯一索引和普通索引必须填写索引名称")
    if len(normalized) > 32 or not INDEX_NAME_PATTERN.fullmatch(normalized):
        raise ValueError("索引名称必须是 1-32 位字母、数字、下划线或 $，且不能以数字开头")
    if index_type == "unique" and not normalized.startswith("uniq_"):
        raise ValueError("唯一索引名称必须以 uniq_ 开头")
    if index_type == "secondary" and not normalized.startswith("idx_"):
        raise ValueError("普通索引名称必须以 idx_ 开头")
    if normalized.lower() in physical_names:
        raise ValueError(f"索引名称与物理索引重复: {normalized}")
    return normalized


def _validate_table_index_columns(raw_columns: Any, available_columns: set[str]) -> list[str]:
    columns = [str(value or "").strip() for value in (raw_columns or [])]
    if not columns or any(not value for value in columns):
        raise ValueError("索引至少需要一个字段")
    if len(columns) > MAX_INDEX_COLUMN_COUNT:
        raise ValueError(f"单个组合索引不能超过 {MAX_INDEX_COLUMN_COUNT} 个字段")
    if len(set(columns)) != len(columns):
        raise ValueError("同一个索引内不能重复配置字段")
    missing = [value for value in columns if value not in available_columns]
    if missing:
        raise ValueError(f"索引字段不存在: {', '.join(missing)}")
    return columns


def _normalize_query_executor_type(source_type: str, query_executor_type: str | None = None) -> str:
    """标准化查询执行方式，并校验数据源类型约束。"""
    normalized_executor_type = str(query_executor_type or "direct").strip().lower() or "direct"
    if normalized_executor_type not in SUPPORTED_QUERY_EXECUTOR_TYPES:
        raise ValueError(f"不支持的查询执行方式: {normalized_executor_type}")
    return normalized_executor_type


def _build_source_line(source_config: dict | None) -> str:
    """将连接配置格式化为 host:port:database。"""
    config = sanitize_source_config(source_config)
    host = str(config.get("host") or "").strip()
    port = config.get("port")
    database = str(config.get("database") or "").strip()
    if not host or not port or not database:
        raise ValueError("数据源连接信息不完整")
    return f"{host}:{int(port)}:{database}"


def _build_persisted_source_line(
    source_type: str,
    source_config: dict | None,
    default_query_executor_type: str | None = None,
) -> str:
    """将成员执行源配置格式化为落库文本。"""
    normalized_config = _normalize_member_source_config(source_type, source_config, default_query_executor_type)
    org_name = str(normalized_config.get("org_name") or "").strip()
    org_code = str(normalized_config.get("org_code") or "").strip()
    query_executor_type = normalized_config.get("query_executor_type") or "direct"
    if query_executor_type == "http":
        database = str(normalized_config.get("database") or "").strip()
        return f"{org_name}|{org_code}|http|{database}"
    return f"{org_name}|{org_code}|direct|{_build_source_line(normalized_config)}"


def _build_template_source_line(
    source_type: str,
    source_config: dict | None,
    default_query_executor_type: str | None = None,
) -> str:
    """将模板执行源配置格式化为落库文本。"""
    config = sanitize_source_config(source_config)
    query_executor_type = _normalize_query_executor_type(
        source_type,
        config.get("query_executor_type") or default_query_executor_type,
    )
    if query_executor_type == "http":
        return _build_persisted_source_line(source_type, config, query_executor_type)
    return _build_source_line(config)


def _normalize_member_source_config(
    source_type: str,
    source_config: dict | None,
    default_query_executor_type: str | None = None,
) -> dict[str, Any]:
    """标准化成员执行源配置。"""
    config = sanitize_source_config(source_config)
    normalized_org_name = str(config.get("org_name") or "").strip()
    normalized_org_code = str(config.get("org_code") or "").strip()
    normalized_query_executor_type = _normalize_query_executor_type(
        source_type, config.get("query_executor_type") or default_query_executor_type
    )
    database = str(config.get("database") or "").strip()
    if not normalized_org_name:
        raise ValueError("机构名称不能为空")
    if not normalized_org_code:
        raise ValueError("机构编码不能为空")
    if not database:
        raise ValueError("数据库名不能为空")
    if normalized_query_executor_type == "direct":
        _build_source_line(config)
    elif str(config.get("host") or "").strip() or config.get("port") is not None:
        raise ValueError("HTTP 模式只填写数据库名，不填写 IP 和端口")
    config["query_executor_type"] = normalized_query_executor_type
    config["org_name"] = normalized_org_name
    config["org_code"] = normalized_org_code
    config["database"] = database
    return config


def _build_member_source_identity(
    source_config: dict | None,
    source_type: str,
    default_query_executor_type: str | None = None,
) -> str:
    """根据执行方式生成成员唯一标识。"""
    normalized_config = _normalize_member_source_config(source_type, source_config, default_query_executor_type)
    org_code = str(normalized_config.get("org_code") or "").strip()
    if org_code:
        return org_code
    return _build_source_line(normalized_config)


def _build_member_source_text_line(
    source_type: str,
    source_config: dict | None,
    default_query_executor_type: str | None = None,
) -> str:
    """将成员执行源配置格式化为持久化文本。"""
    return _build_persisted_source_line(source_type, source_config, default_query_executor_type)


def _dedupe_member_source_configs(
    source_configs: list[dict[str, Any]],
    source_type: str,
    default_query_executor_type: str | None = None,
) -> list[dict[str, Any]]:
    """按成员执行身份去重成员执行源配置。"""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_config in source_configs:
        normalized_config = _normalize_member_source_config(source_type, source_config, default_query_executor_type)
        member_identity = _build_member_source_identity(normalized_config, source_type, default_query_executor_type)
        if member_identity in seen:
            continue
        seen.add(member_identity)
        result.append(normalized_config)
    return result


def _validate_member_source_configs(
    source_configs: list[dict[str, Any]],
    source_type: str,
    default_query_executor_type: str | None = None,
) -> list[dict[str, Any]]:
    """校验成员执行源配置，禁止成员身份重复录入。"""
    result: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for index, source_config in enumerate(source_configs, start=1):
        normalized_config = _normalize_member_source_config(source_type, source_config, default_query_executor_type)
        member_identity = _build_member_source_identity(normalized_config, source_type, default_query_executor_type)
        existing_index = seen.get(member_identity)
        if existing_index is not None:
            raise ValueError(f"第 {index} 个机构编码重复，和第 {existing_index} 个相同: {member_identity}")
        seen[member_identity] = index
        result.append(normalized_config)
    return result


def _extract_member_source_configs(
    datasource: dict,
    source_type: str,
    default_query_executor_type: str | None = None,
) -> list[dict[str, Any]]:
    """从数据源对象中提取成员执行源配置。"""
    configs: list[dict[str, Any]] = []
    for member in datasource.get("member_sources") or []:
        configs.append(
            _normalize_member_source_config(
                source_type,
                {
                    "org_name": (member or {}).get("org_name"),
                    "org_code": (member or {}).get("org_code"),
                    "query_executor_type": (member or {}).get("query_executor_type"),
                    "host": (member or {}).get("host"),
                    "port": (member or {}).get("port"),
                    "database": (member or {}).get("database"),
                },
                default_query_executor_type,
            )
        )
    return _dedupe_member_source_configs(configs, source_type, default_query_executor_type)


def _merge_member_source_lines(
    current: dict,
    next_template_source_config: dict[str, Any],
    source_type: str,
    default_query_executor_type: str | None = None,
    member_source_configs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """合并模板源和成员源，模板源始终排在首位。"""
    merged_configs: list[dict[str, Any]] = [
        _normalize_member_source_config(source_type, next_template_source_config, default_query_executor_type)
    ]

    for item in member_source_configs or []:
        merged_configs.append(_normalize_member_source_config(source_type, item, default_query_executor_type))

    if member_source_configs is None:
        current_template_identity = str(
            current.get("template_datasource_id") or current.get("template_source") or ""
        ).strip()
        for source_config in _extract_member_source_configs(current, source_type, default_query_executor_type):
            if (
                _build_member_source_identity(source_config, source_type, default_query_executor_type)
                == current_template_identity
            ):
                continue
            merged_configs.append(source_config)

    return _dedupe_member_source_configs(merged_configs, source_type, default_query_executor_type)


def _build_member_sources_text(
    source_type: str,
    member_source_configs: list[dict[str, Any]],
    query_executor_type: str,
) -> str:
    lines: list[str] = []
    for item in _dedupe_member_source_configs(member_source_configs, source_type, query_executor_type):
        member_line = _build_member_source_text_line(source_type, item, query_executor_type)
        if member_line:
            lines.append(member_line)
    return "\n".join(lines)


def _get_template_member_source_config(datasource: dict) -> dict[str, Any]:
    """提取当前模板成员配置，避免仅刷新表结构时破坏模板成员名称。"""
    template_member_id = str(datasource.get("template_datasource_id") or "").strip()
    for member in datasource.get("member_sources") or []:
        member_id = str((member or {}).get("member_datasource_id") or "").strip()
        if template_member_id and member_id != template_member_id:
            continue
        return _member_source_config_fields(member, datasource)
    return {}


def _member_source_config_fields(member: Any, datasource: dict) -> dict[str, Any]:
    """按模板成员字段提取源配置；query_executor_type 缺省回退数据源配置再回退直连。"""
    member = member or {}
    return {
        "org_name": member.get("org_name"),
        "org_code": member.get("org_code"),
        "query_executor_type": member.get("query_executor_type") or datasource.get("query_executor_type") or "direct",
        "host": member.get("host"),
        "port": member.get("port"),
        "database": member.get("database"),
    }


def _build_meta_source_config(datasource: dict) -> dict[str, Any]:
    """构建元数据接口使用的模板源配置。"""
    source_config = dict(datasource.get("source_config") or {})
    source_config.pop("namespace", None)
    source_config.pop("orgCode", None)
    source_config.pop("orgName", None)
    template_source_config = _get_template_member_source_config(datasource)
    org_code = str(template_source_config.get("org_code") or "").strip()
    if org_code:
        source_config["org_code"] = org_code
    org_name = str(template_source_config.get("org_name") or "").strip()
    if org_name:
        source_config["org_name"] = org_name
    query_executor_type = (
        str(template_source_config.get("query_executor_type") or datasource.get("query_executor_type") or "direct")
        .strip()
        .lower()
    )
    source_config["query_executor_type"] = (
        query_executor_type if query_executor_type in SUPPORTED_QUERY_EXECUTOR_TYPES else "direct"
    )
    return source_config


class DataSourceManager:
    """数据源管理器。"""

    def __init__(
        self,
        metadata_adapter_registry: DataSourceMetadataAdapterRegistry | None = None,
    ):
        self.datasource_dao = DataSourceDao()
        self.metadata_adapter_registry = metadata_adapter_registry or get_metadata_adapter_registry()
        logger.info("数据源 DAO 已启用")

    async def _get_datasource_or_raise_async(self, datasource_id: str) -> dict:
        """异步读取数据源，不存在时抛错。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            raise ValueError(f"数据源不存在: {datasource_id}")
        return datasource

    async def _get_datasource_meta_or_raise_async(self, datasource_id: str) -> dict:
        """读取执行对象解析所需的数据源元信息，不加载表结构和指标集。"""
        datasource = await self.get_datasource_meta_async(datasource_id)
        if not datasource:
            raise ValueError(f"数据源不存在: {datasource_id}")
        return datasource

    async def _load_datasource_async(self, datasource_id: str) -> dict:
        """异步读取最新数据源。"""
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        if not datasource:
            raise ValueError(f"数据源写入后读取失败: {datasource_id}")
        return datasource

    def _build_physical_metadata_source_config(self, datasource: dict[str, Any]) -> dict[str, Any]:
        """构建物理元数据读取使用的模板源配置。"""
        source_type = datasource.get("source_type")
        if source_type == "hive":
            return _build_meta_source_config(datasource)
        return dict(datasource.get("source_config") or {})

    def _create_metadata_adapter(
        self,
        source_type: str,
        source_config: dict[str, Any],
        *,
        operation: str,
        user_id: str | None = None,
        username: str | None = None,
    ) -> DataSourceMetadataAdapter:
        if not source_config:
            raise ValueError("数据源连接配置不完整")
        access_type = str(source_config.get("query_executor_type") or "direct").strip().lower() or "direct"
        registry = getattr(self, "metadata_adapter_registry", None) or get_metadata_adapter_registry()
        try:
            return registry.create(
                source_type,
                access_type,
                source_config,
                user_id=user_id,
                username=username,
            )
        except MetadataAdapterNotFoundError as exc:
            if exc.access_type == "http" and exc.source_type != "hive":
                raise ValueError("HTTP 元数据模式当前仅支持 Hive 数据源") from exc
            raise ValueError(f"不支持{operation}: {source_type}，只支持 mysql/hive") from exc

    async def _load_physical_schema_async(
        self,
        source_type: str,
        source_config: dict[str, Any],
        table_names: list[str] | None = None,
        user_id: str | None = None,
        username: str | None = None,
    ) -> list[dict[str, Any]]:
        """按物理连接配置读取完整表结构。"""
        adapter = self._create_metadata_adapter(
            source_type,
            source_config,
            operation="实时获取",
            user_id=user_id,
            username=username,
        )
        return await asyncio.wait_for(
            adapter.load_schema(table_names),
            timeout=await get_datasource_sync_timeout_seconds(),
        )

    async def _load_physical_table_names_async(
        self,
        source_type: str,
        source_config: dict[str, Any],
        user_id: str | None = None,
        username: str | None = None,
    ) -> list[Any]:
        """按物理连接配置读取表名列表。"""
        adapter = self._create_metadata_adapter(
            source_type,
            source_config,
            operation="获取表列表",
            user_id=user_id,
            username=username,
        )
        return await asyncio.wait_for(
            adapter.list_tables(),
            timeout=await get_datasource_sync_timeout_seconds(),
        )

    async def _save_datasource_async(
        self,
        datasource_id: str,
        group_name: str,
        group_desc: str,
        source_type: str,
        query_executor_type: str,
        template_source: str,
        member_source_configs: list[dict[str, Any]],
        tables: Any = _UNSET,
        mapped_modules: Any = _UNSET,
        is_default: Any = _UNSET,
        query_context: str = "",
        recommended_questions: list[str] | None = None,
        audit: DatasourceWriteAudit | None = None,
    ) -> dict:
        """异步写入数据源组及表结构缓存。"""
        # 原写法把 DatasourceWriteAudit() 实例放在默认参数上（跨调用共享同一对象），
        # 改为进入函数后再构造，避免可变默认入参随调用累积状态。
        if audit is None:
            audit = DatasourceWriteAudit()
        normalized_query_executor_type = _normalize_query_executor_type(source_type, query_executor_type)
        validated_member_source_configs = _validate_member_source_configs(
            member_source_configs,
            source_type,
            normalized_query_executor_type,
        )
        persisted_member_source_lines: list[str] = []
        for item in _dedupe_member_source_configs(
            validated_member_source_configs, source_type, normalized_query_executor_type
        ):
            member_line = _build_member_source_text_line(source_type, item, normalized_query_executor_type)
            if member_line:
                persisted_member_source_lines.append(member_line)

        template_source_line = ""
        if validated_member_source_configs:
            template_source_line = _build_member_source_text_line(
                source_type, validated_member_source_configs[0], normalized_query_executor_type
            )
        if not template_source_line:
            template_source_line = template_source

        async with self.datasource_dao.transaction_async():
            await self.datasource_dao.upsert_datasource_group_async(
                group_id=datasource_id,
                group_name=group_name,
                group_desc=group_desc or "",
                query_context=query_context or "",
                source_type=source_type or "",
                template_source=template_source_line,
                member_sources_text="\n".join(persisted_member_source_lines),
                recommended_questions=recommended_questions or [],
                create_by=audit.create_by,
                update_by=audit.update_by,
                create_by_name=audit.create_by_name,
                update_by_name=audit.update_by_name,
            )
            update_kwargs: dict[str, Any] = {
                "group_id": datasource_id,
                "mapped_modules": [] if mapped_modules is _UNSET else mapped_modules,
                "update_by": audit.update_by,
                "update_by_name": audit.update_by_name,
            }
            if is_default is not _UNSET:
                update_kwargs["is_default"] = is_default
            await self.datasource_dao.update_datasource_group_async(**update_kwargs)
            if tables is not _UNSET:
                await self.datasource_dao.replace_datasource_tables_async(
                    group_id=datasource_id,
                    tables=tables or [],
                    create_by=audit.create_by,
                    update_by=audit.update_by,
                    create_by_name=audit.create_by_name,
                    update_by_name=audit.update_by_name,
                )
        return await self._load_datasource_async(datasource_id)

    async def get_all_datasources_async(self, include_tables: bool = True) -> list[dict[str, Any]]:
        """异步获取所有数据源。"""
        return await self.datasource_dao.get_all_datasources_async(include_tables=include_tables)

    async def get_authorized_datasources_async(
        self,
        include_tables: bool = True,
        user_id: str | None = None,
        username: str | None = None,
    ) -> list[dict[str, Any]]:
        """异步获取当前用户有机构权限的数据源。"""
        datasources = await self.get_all_datasources_async(include_tables=include_tables)
        include_http_permission = any(
            datasource_requires_http_permission(datasource)
            for datasource in datasources
            if isinstance(datasource, dict)
        )
        if include_http_permission:
            allowed_org_codes = await get_allowed_org_codes_by_executor(user_id, username)
        else:
            allowed_org_codes = await get_allowed_org_codes_by_executor(
                user_id,
                username,
                include_http_permission=False,
            )
        return filter_datasources_by_allowed_org_codes(datasources, allowed_org_codes)

    async def can_access_datasource_async(
        self,
        datasource_id: str,
        user_id: str | None = None,
        username: str | None = None,
    ) -> bool:
        """判断当前用户是否有权读取指定数据源。"""
        authorized = await self.get_authorized_datasources_async(
            include_tables=False,
            user_id=user_id,
            username=username,
        )
        normalized_id = str(datasource_id or "").strip()
        return any(str(item.get("id") or item.get("group_id") or "").strip() == normalized_id for item in authorized)

    async def has_datasource_async(self, datasource_id: str) -> bool:
        """异步判断数据源是否存在。"""
        return await self.datasource_dao.has_datasource_async(datasource_id)

    async def get_datasource_async(self, datasource_id: str) -> dict[str, Any] | None:
        """异步获取指定数据源。"""
        return await self.datasource_dao.get_datasource_async(datasource_id)

    async def get_datasource_meta_async(self, datasource_id: str) -> dict[str, Any] | None:
        """异步获取数据源轻量元信息，不加载表结构和语义配置。"""
        return await self.datasource_dao.get_datasource_meta_async(datasource_id)

    async def get_datasource_meta_by_group_name_async(self, group_name: str) -> dict[str, Any] | None:
        """异步根据数据源组名称获取轻量元信息，不加载表结构和语义配置。"""
        return await self.datasource_dao.get_datasource_meta_by_group_name_async(group_name)

    @staticmethod
    def _member_source_map(member_sources: list[Any]) -> dict[str, dict[str, Any]]:
        """按 member_datasource_id 建立成员索引，空配置直接报错。"""
        if not member_sources:
            raise ValueError("当前数据源组下没有可执行的数据源")
        member_map: dict[str, dict[str, Any]] = {}
        for item in member_sources:
            member_id = str(item.get("member_datasource_id") or "").strip()
            if member_id:
                member_map[member_id] = item
        if not member_map:
            raise ValueError("当前数据源组成员配置无效")
        return member_map

    @staticmethod
    def _default_member_id(datasource: dict, member_map: dict[str, dict[str, Any]]) -> str:
        default_member_id = str(datasource.get("template_datasource_id") or "").strip()
        if not default_member_id or default_member_id not in member_map:
            default_member_id = next(iter(member_map.keys()))
        return default_member_id

    @staticmethod
    def _normalize_selected_member_ids(
        datasource: dict,
        member_datasource_ids: list[str] | None,
        member_map: dict[str, dict[str, Any]],
    ) -> list[str]:
        """去重归一化指定成员；未指定时回落到模板成员或首个成员。"""
        normalized_selected_ids: list[str] = []
        for member_id in member_datasource_ids or []:
            normalized_member_id = str(member_id or "").strip()
            if normalized_member_id and normalized_member_id not in normalized_selected_ids:
                normalized_selected_ids.append(normalized_member_id)
        if not normalized_selected_ids:
            normalized_selected_ids = [DataSourceManager._default_member_id(datasource, member_map)]
        return normalized_selected_ids

    @staticmethod
    def _build_execution_target(datasource: dict, member_id: str, member: dict[str, Any]) -> dict[str, Any]:
        source_type = member.get("source_type") or datasource.get("source_type") or ""
        query_executor_type = member.get("query_executor_type") or datasource.get("query_executor_type") or "direct"
        org_code = str(member.get("org_code") or "").strip()
        source_config = {
            "host": member.get("host"),
            "port": member.get("port"),
            "database": member.get("database"),
        }
        source_config = resolve_execution_source_config(
            source_type,
            query_executor_type,
            source_config,
            org_code=org_code,
            org_name=member.get("org_name") or "",
        )
        return {
            "id": member_id,
            "member_datasource_id": member_id,
            "member_name": member.get("org_name")
            or org_code
            or f"{member.get('host')}:{member.get('port')}/{member.get('database')}",
            "org_code": org_code,
            "source_type": source_type,
            "query_executor_type": query_executor_type,
            "source_config": source_config,
        }

    def _build_execution_datasource_targets(
        self, datasource: dict, member_datasource_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """根据数据源配置构造可执行成员数据源。"""
        member_map = self._member_source_map(datasource.get("member_sources") or [])
        normalized_selected_ids = self._normalize_selected_member_ids(datasource, member_datasource_ids, member_map)
        invalid_ids = [member_id for member_id in normalized_selected_ids if member_id not in member_map]
        if invalid_ids:
            raise ValueError(f"成员数据源不存在或不属于当前组: {', '.join(invalid_ids)}")
        return [
            self._build_execution_target(datasource, member_id, member_map[member_id])
            for member_id in normalized_selected_ids
        ]

    async def get_execution_datasources_async(
        self, datasource_id: str, member_datasource_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """异步解析逻辑数据源下可执行的成员数据源列表。"""
        datasource = await self._get_datasource_meta_or_raise_async(datasource_id)
        return self._build_execution_datasource_targets(datasource, member_datasource_ids)

    async def _filter_authorized_datasource(
        self,
        datasource: dict,
        user_id: str | None = None,
        username: str | None = None,
    ) -> dict:
        """按当前用户机构权限过滤数据源成员，过滤后无任何可用成员时报错。"""
        if datasource_requires_http_permission(datasource):
            allowed_org_codes = await get_allowed_org_codes_by_executor(user_id, username)
        else:
            allowed_org_codes = await get_allowed_org_codes_by_executor(
                user_id,
                username,
                include_http_permission=False,
            )
        filtered_datasource = filter_datasource_members_by_allowed_org_codes(datasource, allowed_org_codes)
        if not filtered_datasource:
            raise ValueError("当前用户无可用执行对象权限")
        return filtered_datasource

    async def authorize_execution_datasources_async(
        self,
        datasource: dict,
        member_datasource_ids: list[str] | None = None,
        user_id: str | None = None,
        username: str | None = None,
    ) -> list[dict[str, Any]]:
        """按当前用户机构权限过滤已读取的数据源执行对象。"""
        filtered_datasource = await self._filter_authorized_datasource(datasource, user_id, username)
        return self._build_execution_datasource_targets(filtered_datasource, member_datasource_ids)

    async def list_authorized_execution_targets_async(
        self,
        datasource_id: str,
        user_id: str | None = None,
        username: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出当前用户在数据源组下有权限执行的全部成员数据源（不限定选中范围）。"""
        datasource = await self._get_datasource_meta_or_raise_async(datasource_id)
        filtered_datasource = await self._filter_authorized_datasource(datasource, user_id, username)
        member_map = self._member_source_map(filtered_datasource.get("member_sources") or [])
        return [
            self._build_execution_target(filtered_datasource, member_id, member_map[member_id])
            for member_id in member_map
        ]

    async def get_authorized_execution_datasources_async(
        self,
        datasource_id: str,
        member_datasource_ids: list[str] | None = None,
        user_id: str | None = None,
        username: str | None = None,
    ) -> list[dict[str, Any]]:
        """异步解析当前用户有机构权限的可执行成员数据源列表。"""
        datasource = await self._get_datasource_meta_or_raise_async(datasource_id)
        return await self.authorize_execution_datasources_async(
            datasource,
            member_datasource_ids,
            user_id,
            username,
        )

    async def get_datasource_by_group_name_async(self, group_name: str) -> dict[str, Any] | None:
        """异步根据数据源组名称获取数据源。"""
        return await self.datasource_dao.get_datasource_by_group_name_async(group_name)

    async def get_table_groups_async(self, datasource_id: str) -> list[dict[str, Any]]:
        """异步获取数据源内的表分组关系。"""
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        if not datasource:
            return []
        relations = await self.datasource_dao.get_table_group_relations_async(datasource.get("group_id"))
        groups: dict[str, list[str]] = {}
        for relation in relations:
            group_name = str(relation.get("table_group_name") or "").strip()
            table_name = str(relation.get("table_name") or "").strip()
            if not group_name or not table_name:
                continue
            groups.setdefault(group_name, [])
            if table_name not in groups[group_name]:
                groups[group_name].append(table_name)
        return [{"table_group_name": name, "table_names": table_names} for name, table_names in groups.items()]

    @staticmethod
    def _normalize_group_table_names(group: dict[str, Any], existing_tables: set[str]) -> list[str]:
        """归一化单个分组的表名列表：过滤不存在/重复/空的表名。"""
        table_names: list[str] = []
        for table_name in (group or {}).get("table_names") or []:
            normalized_table_name = str(table_name or "").strip()
            if (
                normalized_table_name
                and normalized_table_name in existing_tables
                and normalized_table_name not in table_names
            ):
                table_names.append(normalized_table_name)
        return table_names

    @staticmethod
    def _normalize_table_groups(groups: list[dict[str, Any]], existing_tables: set[str]) -> list[dict[str, Any]]:
        """过滤掉空组名、不存在的表名与重复表名。"""
        normalized_groups: list[dict[str, Any]] = []
        for group in groups or []:
            group_name = str((group or {}).get("table_group_name") or "").strip()
            if not group_name:
                continue
            table_names = DataSourceManager._normalize_group_table_names(group, existing_tables)
            if table_names:
                normalized_groups.append({"table_group_name": group_name, "table_names": table_names})
        return normalized_groups

    async def replace_table_groups_async(
        self, datasource_id: str, groups: list[dict[str, Any]], operator: str = "", operator_name: str = ""
    ) -> bool:
        """异步覆盖保存数据源内的表分组关系。"""
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        if not datasource:
            return False
        existing_tables = {str(table.get("table_name") or "").strip() for table in datasource.get("ds_tables") or []}
        normalized_groups = self._normalize_table_groups(groups, existing_tables)
        await self.datasource_dao.replace_table_group_relations_async(
            datasource.get("group_id"),
            normalized_groups,
            create_by=operator or "",
            update_by=operator or "",
            create_by_name=operator_name or "",
            update_by_name=operator_name or "",
        )
        return True

    async def update_table_business_desc_async(
        self, datasource_id: str, table_name: str, business_desc: str, operator: str = "", operator_name: str = ""
    ) -> dict[str, Any] | None:
        """异步更新单张表的业务说明。"""
        normalized_table_name = str(table_name or "").strip()
        if not normalized_table_name:
            return None
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        if not datasource:
            return None
        current_table = None
        for table in datasource.get("ds_tables") or []:
            if str(table.get("table_name") or "").strip() == normalized_table_name:
                current_table = dict(table)
                break
        if current_table is None:
            return None
        normalized_desc = str(business_desc or "").strip()[:255]
        await self.datasource_dao.update_datasource_table_business_desc_async(
            datasource.get("group_id") or "",
            normalized_table_name,
            normalized_desc,
            update_by=operator or "",
            update_by_name=operator_name or "",
        )
        current_table["business_desc"] = normalized_desc
        return current_table

    async def get_column_dict_async(
        self, datasource_id: str, table_name: str, column_name: str
    ) -> dict[str, Any] | None:
        """异步获取单个字段的数据字典。"""
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        table = self._find_table_column(datasource, table_name, column_name)
        if not datasource or table is None:
            return None
        return await self.datasource_dao.get_column_dict_async(
            datasource.get("group_id") or "", table_name, column_name
        )

    async def get_table_column_dicts_async(
        self, datasource_id: str, table_name: str
    ) -> dict[str, dict[str, Any]] | None:
        """异步获取单张表所有已维护字段的数据字典。"""
        normalized_table_name = str(table_name or "").strip()
        if not normalized_table_name:
            return None
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        if not datasource:
            return None
        current_table = None
        for table in datasource.get("ds_tables") or []:
            if str(table.get("table_name") or "").strip() == normalized_table_name:
                current_table = table
                break
        if current_table is None:
            return None
        column_names = {str(column.get("name") or "").strip() for column in current_table.get("columns") or []}
        column_names.discard("")
        column_dicts = await self.datasource_dao.get_column_dicts_by_table_async(
            datasource.get("group_id") or "", normalized_table_name
        )
        return {
            column_name: column_dict for column_name, column_dict in column_dicts.items() if column_name in column_names
        }

    async def get_datasource_column_dicts_async(self, datasource_id: str) -> dict[tuple[str, str], dict[str, Any]]:
        """异步获取数据源下全部已维护字段字典。"""
        datasource = await self.get_datasource_meta_async(datasource_id)
        group_id = str((datasource or {}).get("group_id") or "").strip()
        if not group_id:
            return {}
        return await self.datasource_dao.get_column_dicts_by_group_id_async(group_id)

    async def replace_column_dict_async(
        self,
        datasource_id: str,
        table_name: str,
        column_name: str,
        column_dict: dict[str, Any],
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any] | None:
        """异步覆盖保存单个字段的数据字典。"""
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        table = self._find_table_column(datasource, table_name, column_name)
        if not datasource or table is None:
            return None
        return await self.datasource_dao.replace_column_dict_async(
            datasource.get("group_id") or "",
            table_name,
            column_name,
            column_dict,
            create_by=operator or "",
            update_by=operator or "",
            create_by_name=operator_name or "",
            update_by_name=operator_name or "",
        )

    async def fill_column_dict_async(
        self,
        datasource_id: str,
        table_name: str,
        column_name: str,
        column_dict: dict[str, Any],
        operator: str = "",
        operator_name: str = "",
    ) -> list[str] | None:
        """仅填空保存单个字段的数据字典（Ossie 导入闭环用）。

        只填充现有字典为空的字段，已有值一律保留；返回实际填充的字段名列表，
        物理列不存在时返回 None（与 replace 的列校验口径一致）。
        """
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        table = self._find_table_column(datasource, table_name, column_name)
        if not datasource or table is None:
            return None
        return await self.datasource_dao.fill_column_dict_async(
            datasource.get("group_id") or "",
            table_name,
            column_name,
            column_dict,
            create_by=operator or "",
            update_by=operator or "",
            create_by_name=operator_name or "",
            update_by_name=operator_name or "",
        )

    async def get_table_indexes_async(
        self,
        datasource_id: str,
        table_name: str,
    ) -> list[dict[str, Any]] | None:
        """异步获取数据源表的手动索引配置。"""
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        if not datasource:
            return None
        table = self._find_table(datasource, table_name)
        if table is None:
            return None
        indexes = await self.datasource_dao.get_datasource_table_indexes_async(
            datasource.get("group_id") or "", str(table_name or "").strip()
        )
        if indexes is None:
            return None
        return list(indexes.get("manual") or [])

    async def replace_table_indexes_async(
        self,
        datasource_id: str,
        table_name: str,
        indexes: list[dict[str, Any]],
        operator: str = "",
        operator_name: str = "",
    ) -> list[dict[str, Any]] | None:
        """异步覆盖保存数据源表的手动索引配置。"""
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        if not datasource:
            return None
        table = self._find_table(datasource, table_name)
        if table is None:
            return None
        normalized = self._validate_table_indexes(table, indexes)
        return await self.datasource_dao.update_datasource_table_indexes_async(
            datasource.get("group_id") or "",
            str(table_name or "").strip(),
            normalized,
            update_by=operator or "",
            update_by_name=operator_name or "",
        )

    @staticmethod
    def _find_table(datasource: dict[str, Any] | None, table_name: str) -> dict[str, Any] | None:
        normalized = str(table_name or "").strip()
        if not datasource or not normalized:
            return None
        return next(
            (
                table
                for table in datasource.get("ds_tables") or []
                if str(table.get("table_name") or "").strip() == normalized
            ),
            None,
        )

    @staticmethod
    def _table_column_names(table: dict[str, Any]) -> set[str]:
        return {
            str(column.get("name") or "").strip()
            for column in table.get("columns") or []
            if isinstance(column, dict) and str(column.get("name") or "").strip()
        }

    @staticmethod
    def _existing_physical_index_names(table: dict[str, Any]) -> set[str]:
        return {
            str(item.get("name") or "").strip().lower()
            for item in [*(table.get("unique_indexes") or []), *(table.get("indexes") or [])]
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }

    @staticmethod
    def _validate_and_normalize_index_item(
        item: Any,
        table: dict[str, Any],
        columns: set[str],
        physical_names: set[str],
        seen: set[tuple[str, str]],
        primary_seen: bool,
    ) -> tuple[dict[str, Any], bool]:
        """校验并归一化单个手工索引项，返回 (索引项, 是否已出现主键)。"""
        if not isinstance(item, dict):
            raise ValueError("索引项必须是对象")
        index_type = str(item.get("index_type") or "").strip().lower()
        if index_type not in VALID_TABLE_INDEX_TYPES:
            raise ValueError("索引类型必须是 primary、unique 或 secondary")
        if index_type == "primary":
            if table.get("primary_keys"):
                raise ValueError("当前表已经存在物理主键，无需重复配置手工主键")
            if primary_seen:
                raise ValueError("同一张表只能配置一个主键索引")
            primary_seen = True
        index_name = _validate_table_index_name(index_type, item.get("index_name"), physical_names)
        index_columns = _validate_table_index_columns(item.get("columns"), columns)
        key = (index_type, index_name.lower())
        if key in seen:
            raise ValueError(f"索引名称重复: {index_name}")
        seen.add(key)
        return {
            "index_type": index_type,
            "index_name": index_name,
            "columns": index_columns,
        }, primary_seen

    @staticmethod
    def _validate_table_indexes(table: dict[str, Any], indexes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(indexes or []) > MAX_TABLE_INDEX_COUNT:
            raise ValueError(f"单表手工索引不能超过 {MAX_TABLE_INDEX_COUNT} 个")
        columns = DataSourceManager._table_column_names(table)
        physical_names = DataSourceManager._existing_physical_index_names(table)
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        primary_seen = False
        for item in indexes or []:
            entry, primary_seen = DataSourceManager._validate_and_normalize_index_item(
                item,
                table,
                columns,
                physical_names,
                seen,
                primary_seen,
            )
            normalized.append(entry)
        return _normalize_table_index_items(normalized)

    def _match_table_column(
        self, datasource: dict[str, Any] | None, normalized_table_name: str, normalized_column_name: str
    ) -> dict[str, Any] | None:
        """在数据源缓存中匹配指定表字段，命中返回表记录否则 None。"""
        for table in datasource.get("ds_tables") or []:
            if str(table.get("table_name") or "").strip() != normalized_table_name:
                continue
            for column in table.get("columns") or []:
                if str(column.get("name") or "").strip() == normalized_column_name:
                    return table
        return None

    def _find_table_column(
        self, datasource: dict[str, Any] | None, table_name: str, column_name: str
    ) -> dict[str, Any] | None:
        """校验数据源缓存中是否存在指定表字段。"""
        normalized_table_name = str(table_name or "").strip()
        normalized_column_name = str(column_name or "").strip()
        if not datasource or not normalized_table_name or not normalized_column_name:
            return None
        return self._match_table_column(datasource, normalized_table_name, normalized_column_name)

    async def create_datasource_group_from_sources_async(
        self,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: dict[str, Any],
        member_source_configs: list[dict[str, Any]] | None = None,
        query_context: str = "",
        recommended_questions: list[str] | None = None,
        create_by: str = "",
        create_by_name: str = "",
    ) -> dict[str, Any]:
        """异步按模板源和成员连接信息创建数据源组。"""
        now = datetime.now()
        datasource_id = f"ds_{now.strftime('%Y%m%d%H%M%S')}_{now.microsecond}"
        query_executor_type = (
            str(template_source_config.get("query_executor_type") or "direct").strip().lower() or "direct"
        )
        template_source = _build_persisted_source_line(source_type, template_source_config, query_executor_type)
        member_source_configs_merged = _merge_member_source_lines(
            current={},
            next_template_source_config=template_source_config,
            source_type=source_type,
            default_query_executor_type=query_executor_type,
            member_source_configs=member_source_configs,
        )
        created = await self._save_datasource_async(
            datasource_id=datasource_id,
            group_name=group_name,
            group_desc=group_desc,
            query_context=query_context,
            source_type=source_type,
            query_executor_type=query_executor_type,
            template_source=template_source,
            member_source_configs=member_source_configs_merged,
            tables=_UNSET,
            mapped_modules=[],
            is_default=False,
            recommended_questions=recommended_questions or [],
            audit=DatasourceWriteAudit(
                create_by=create_by,
                update_by=create_by,
                create_by_name=create_by_name,
                update_by_name=create_by_name,
            ),
        )
        logger.info("创建数据源组: %s - %s, members=%s", datasource_id, group_name, len(member_source_configs_merged))
        return created

    async def update_datasource_group_from_sources_async(
        self,
        datasource_id: str,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: dict[str, Any],
        member_source_configs: list[dict[str, Any]] | None = None,
        query_context: str = "",
        recommended_questions: list[str] | None = None,
        update_by: str = "",
        update_by_name: str = "",
    ) -> dict[str, Any] | None:
        """异步按模板源和成员连接信息更新数据源组。"""
        current = await self.get_datasource_meta_async(datasource_id)
        if not current:
            return None

        query_executor_type = (
            str(template_source_config.get("query_executor_type") or "direct").strip().lower() or "direct"
        )
        template_source = _build_persisted_source_line(source_type, template_source_config, query_executor_type)
        member_source_configs_merged = _merge_member_source_lines(
            current,
            template_source_config,
            source_type,
            query_executor_type,
            member_source_configs,
        )
        member_sources_text = _build_member_sources_text(source_type, member_source_configs_merged, query_executor_type)
        await self.datasource_dao.update_datasource_group_async(
            group_id=datasource_id,
            group_name=group_name,
            group_desc=group_desc or "",
            query_context=query_context or "",
            recommended_questions=recommended_questions
            if recommended_questions is not None
            else current.get("recommended_questions", []),
            source_type=source_type or "",
            template_source=template_source,
            member_sources_text=member_sources_text,
            update_by=update_by or current.get("update_by") or "",
            update_by_name=update_by_name or current.get("update_by_name") or "",
        )
        logger.info("更新数据源组: %s - %s, members=%s", datasource_id, group_name, len(member_source_configs_merged))
        return await self.datasource_dao.get_datasource_meta_async(datasource_id)

    async def sync_datasource_tables_async(
        self,
        datasource_id: str,
        tables: list[dict[str, Any]],
        update_by: str = "",
        update_by_name: str = "",
    ) -> dict[str, Any] | None:
        """异步同步数据源组表结构缓存。"""
        current = await self.get_datasource_async(datasource_id)
        if not current:
            return None

        await self.datasource_dao.replace_datasource_tables_async(
            group_id=current.get("group_id") or datasource_id,
            tables=tables or [],
            create_by=current.get("create_by") or "",
            create_by_name=current.get("create_by_name") or "",
            update_by=update_by or current.get("update_by") or "",
            update_by_name=update_by_name or current.get("update_by_name") or "",
        )
        logger.info("同步数据源组表结构: %s, tables=%s", datasource_id, len(tables or []))
        return await self._load_datasource_async(datasource_id)

    async def sync_datasource_schema_from_source_async(
        self,
        datasource_id: str,
        table_names: list[str] | None = None,
        update_by: str = "",
        update_by_name: str = "",
        user_id: str | None = None,
        username: str | None = None,
    ) -> dict[str, Any] | None:
        """从模板物理源读取表结构并写入数据源缓存。"""
        if not await self.get_datasource_async(datasource_id):
            return None
        tables = await self.get_live_schema_async(
            datasource_id,
            table_names=table_names,
            user_id=user_id,
            username=username,
        )
        return await self.sync_datasource_tables_async(
            datasource_id=datasource_id,
            tables=tables,
            update_by=update_by,
            update_by_name=update_by_name,
        )

    @staticmethod
    def _resolve_template_source_update(
        current: dict[str, Any],
        source_type: str | None,
        source_config: dict | None,
    ) -> tuple[str, dict[str, Any]]:
        """解析更新后的模板源行与模板源配置。"""
        next_template_source = str(
            current.get("template_source") or current.get("template_datasource_id") or ""
        ).strip()
        next_template_source_config: dict[str, Any] = _get_template_member_source_config(current)
        if source_config is not None:
            sanitized_source_config = sanitize_source_config(source_config)
            next_template_source_config = {
                **next_template_source_config,
                **sanitized_source_config,
            }
            next_template_source = _build_template_source_line(
                source_type if source_type is not None else current.get("source_type") or "",
                next_template_source_config,
                current.get("query_executor_type") or "direct",
            )
        return next_template_source, next_template_source_config

    @staticmethod
    def _build_update_identity_fields(
        current: dict[str, Any],
        datasource_id: str,
        name: str | None,
        description: str | None,
        update_by: str,
        update_by_name: str,
    ) -> dict[str, Any]:
        """构造更新时的名称/描述/操作人字段（未提供时沿用现值）。"""
        return {
            "group_name": name if name is not None else current.get("group_name") or datasource_id,
            "group_desc": description if description is not None else current.get("ds_description") or "",
            "audit": DatasourceWriteAudit(
                create_by=current.get("create_by") or "",
                create_by_name=current.get("create_by_name") or "",
                update_by=update_by or current.get("update_by") or "",
                update_by_name=update_by_name or current.get("update_by_name") or "",
            ),
        }

    @staticmethod
    def _build_update_content_fields(
        current: dict[str, Any],
        source_type: str | None,
        tables: list[dict] | None,
        mapped_modules: list[str] | None,
        next_template_source: str,
        next_template_source_config: dict[str, Any],
    ) -> dict[str, Any]:
        """构造更新时的源配置/表结构/模块映射字段（未提供时沿用现值）。"""
        return {
            "query_context": current.get("query_context") or "",
            "recommended_questions": current.get("recommended_questions", []),
            "source_type": source_type if source_type is not None else current.get("source_type") or "",
            "query_executor_type": current.get("query_executor_type") or "direct",
            "template_source": next_template_source,
            "member_source_configs": _merge_member_source_lines(
                current,
                next_template_source_config,
                source_type if source_type is not None else current.get("source_type") or "",
                current.get("query_executor_type") or "direct",
            ),
            "tables": tables if tables is not None else current.get("ds_tables", []),
            "mapped_modules": mapped_modules if mapped_modules is not None else current.get("mapped_modules", []),
            "is_default": current.get("is_default", False),
        }

    async def update_datasource_async(
        self,
        datasource_id: str,
        name: str | None = None,
        description: str | None = None,
        tables: list[dict] | None = None,
        source_type: str | None = None,
        source_config: dict | None = None,
        mapped_modules: list[str] | None = None,
        update_by: str = "",
        update_by_name: str = "",
    ) -> dict[str, Any] | None:
        """异步更新数据源。"""
        current = await self.get_datasource_async(datasource_id)
        if not current:
            return None

        next_template_source, next_template_source_config = self._resolve_template_source_update(
            current,
            source_type,
            source_config,
        )
        save_fields = {
            **self._build_update_identity_fields(current, datasource_id, name, description, update_by, update_by_name),
            **self._build_update_content_fields(
                current,
                source_type,
                tables,
                mapped_modules,
                next_template_source,
                next_template_source_config,
            ),
        }
        updated = await self._save_datasource_async(datasource_id=datasource_id, **save_fields)

        logger.info("更新数据源: %s", datasource_id)
        return updated

    async def delete_datasource_async(
        self, datasource_id: str, force: bool = False, update_by: str = "", update_by_name: str = ""
    ) -> bool:
        """异步删除数据源。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            return False

        async with self.datasource_dao.transaction_async():
            await self.datasource_dao.soft_delete_group_async(
                datasource.get("group_id") or datasource_id,
                update_by=update_by or "",
                update_by_name=update_by_name or "",
            )
        logger.info("删除数据源: %s, force=%s", datasource_id, force)
        return True

    async def set_default_datasource_async(
        self, datasource_id: str, update_by: str = "", update_by_name: str = ""
    ) -> bool:
        """异步设置默认数据源。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            return False

        operator = update_by or datasource.get("update_by") or ""
        operator_name = update_by_name or datasource.get("update_by_name") or ""
        await self.datasource_dao.clear_all_default_datasources_async(update_by=operator, update_by_name=operator_name)
        await self.datasource_dao.update_datasource_group_async(
            datasource.get("group_id"),
            is_default=True,
            update_by=operator,
            update_by_name=operator_name,
        )
        logger.info("设置默认数据源: %s", datasource_id)
        return True

    async def get_default_datasource_async(self) -> dict[str, Any] | None:
        """异步获取默认数据源。"""
        return await self.datasource_dao.get_default_datasource_async()

    async def clear_default_datasource_async(
        self, datasource_id: str, update_by: str = "", update_by_name: str = ""
    ) -> bool:
        """异步取消数据源的默认标记。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource or not datasource.get("is_default"):
            return False

        await self.datasource_dao.update_datasource_group_async(
            datasource.get("group_id"),
            is_default=False,
            update_by=update_by or datasource.get("update_by") or "",
            update_by_name=update_by_name or datasource.get("update_by_name") or "",
        )
        logger.info("取消默认数据源: %s", datasource_id)
        return True

    async def get_cached_schema_async(
        self,
        datasource_id: str,
        table_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """从业务库缓存读取数据源表结构。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            raise ValueError(f"数据源不存在: {datasource_id}")
        tables = datasource.get("ds_tables", []) or []
        if table_names:
            selected_names = {
                str(table_name or "").strip() for table_name in table_names if str(table_name or "").strip()
            }
            tables = [table for table in tables if str(table.get("table_name") or "").strip() in selected_names]
        return tables

    async def get_live_schema_async(
        self,
        datasource_id: str,
        table_names: list[str] | None = None,
        user_id: str | None = None,
        username: str | None = None,
    ) -> list[dict[str, Any]]:
        """从模板物理源实时读取数据源表结构。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            raise ValueError(f"数据源不存在: {datasource_id}")

        source_type = datasource.get("source_type")
        source_config = self._build_physical_metadata_source_config(datasource)
        sync_timeout = await get_datasource_sync_timeout_seconds()

        try:
            return await self._load_physical_schema_async(
                source_type,
                source_config,
                table_names,
                user_id=user_id,
                username=username,
            )
        except TimeoutError as err:
            logger.error(
                "异步获取表结构超时: datasource_id=%s source_type=%s timeout=%ss",
                datasource_id,
                source_type,
                sync_timeout,
            )
            raise ConnectionError(f"数据库连接超时（{sync_timeout}秒）") from err
        except ValueError:
            raise
        except Exception as err:
            logger.error("异步获取表结构失败: %s", err)
            raise ConnectionError(f"数据库连接失败: {str(err)}") from err

    async def get_physical_table_names_async(
        self,
        source_type: str,
        source_config: dict[str, Any],
        user_id: str | None = None,
        username: str | None = None,
    ) -> list[Any]:
        """从物理源实时读取表名列表。"""
        sync_timeout = await get_datasource_sync_timeout_seconds()
        try:
            return await self._load_physical_table_names_async(
                source_type,
                source_config,
                user_id=user_id,
                username=username,
            )
        except TimeoutError:
            logger.error("获取表列表超时: source_type=%s timeout=%ss", source_type, sync_timeout)
            raise
        except ValueError:
            raise
        except Exception as err:
            logger.error("获取表列表失败: %s", err, exc_info=True)
            raise ConnectionError(f"数据库连接失败: {str(err)}") from err


_datasource_manager_instance: DataSourceManager | None = None


def get_datasource_manager() -> DataSourceManager:
    """获取数据源管理器单例。"""
    global _datasource_manager_instance
    if _datasource_manager_instance is None:
        _datasource_manager_instance = DataSourceManager()
    return _datasource_manager_instance
