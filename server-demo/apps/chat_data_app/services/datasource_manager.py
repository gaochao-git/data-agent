"""
数据源管理服务。

当前运行时仅使用 MySQL 表，不再读写本地 JSON 文件。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import get_logger
from utils.storage_paths import NL2SQL_BASE_DIR
from .datasource_store import DataSourceDBStore
from .datasource_credentials import resolve_hive_credentials, resolve_mysql_credentials, sanitize_source_config

logger = get_logger(__name__)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_UNSET = object()
SUPPORTED_QUERY_EXECUTOR_TYPES = {"direct", "http"}


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


DATASOURCE_SYNC_TIMEOUT_SECONDS = _read_positive_int_env("CHAT_DATA_DATASOURCE_SYNC_TIMEOUT_SECONDS", 30)


def _format_time(dt: Optional[datetime] = None) -> str:
    """格式化时间为 YYYY-MM-DD HH:MM:SS。"""
    if dt is None:
        dt = datetime.now()
    return dt.strftime(TIME_FORMAT)


def _normalize_query_executor_type(source_type: str, query_executor_type: Optional[str] = None) -> str:
    """标准化查询执行方式，并校验数据源类型约束。"""
    normalized_executor_type = str(query_executor_type or "direct").strip().lower() or "direct"
    if normalized_executor_type not in SUPPORTED_QUERY_EXECUTOR_TYPES:
        raise ValueError(f"不支持的查询执行方式: {normalized_executor_type}")
    return normalized_executor_type


def _build_source_line(source_config: Optional[dict]) -> str:
    """将连接配置格式化为 host:port:database。"""
    config = sanitize_source_config(source_config)
    host = str(config.get("host") or "").strip()
    port = config.get("port")
    database = str(config.get("database") or "").strip()
    if not host or not port or not database:
        raise ValueError("数据源连接信息不完整")
    return f"{host}:{int(port)}:{database}"


def _normalize_member_source_config(
    source_type: str,
    source_config: Optional[dict],
    default_query_executor_type: Optional[str] = None,
) -> Dict[str, Any]:
    """标准化成员执行源配置。"""
    config = sanitize_source_config(source_config)
    normalized_name = str(config.get("datasource_name") or "").strip()
    normalized_query_executor_type = _normalize_query_executor_type(source_type, config.get("query_executor_type") or default_query_executor_type)
    if not normalized_name:
        raise ValueError("数据源名称不能为空，请使用固定格式：名称|direct/http|ip:port:db_name")
    config["query_executor_type"] = normalized_query_executor_type
    config["datasource_name"] = normalized_name
    return config


def _build_member_source_identity(
    source_config: Optional[dict],
    source_type: str,
    default_query_executor_type: Optional[str] = None,
) -> str:
    """根据执行方式生成成员唯一标识。"""
    normalized_config = _normalize_member_source_config(source_type, source_config, default_query_executor_type)
    normalized_query_executor_type = normalized_config.get("query_executor_type") or "direct"
    if normalized_query_executor_type == "http":
        namespace = str(normalized_config.get("datasource_name") or "").strip()
        if not namespace:
            raise ValueError("HTTP 模式下名称不能为空，名称将作为执行标识使用")
        return namespace
    return _build_source_line(normalized_config)


def _build_member_source_text_line(
    source_type: str,
    source_config: Optional[dict],
    default_query_executor_type: Optional[str] = None,
) -> str:
    """将成员执行源配置格式化为持久化文本。"""
    normalized_config = _normalize_member_source_config(source_type, source_config, default_query_executor_type)
    datasource_name = str(normalized_config.get("datasource_name") or "").strip()
    query_executor_type = normalized_config.get("query_executor_type") or "direct"
    source_line = _build_source_line(normalized_config)
    return f"{datasource_name}|{query_executor_type}|{source_line}"


def _dedupe_member_source_configs(
    source_configs: List[Dict[str, Any]],
    source_type: str,
    default_query_executor_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """按成员执行身份去重成员执行源配置。"""
    result: List[Dict[str, Any]] = []
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
    source_configs: List[Dict[str, Any]],
    source_type: str,
    default_query_executor_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """校验成员执行源配置，禁止成员身份重复录入。"""
    result: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for index, source_config in enumerate(source_configs, start=1):
        normalized_config = _normalize_member_source_config(source_type, source_config, default_query_executor_type)
        member_identity = _build_member_source_identity(normalized_config, source_type, default_query_executor_type)
        existing_index = seen.get(member_identity)
        if existing_index is not None:
            if normalized_config.get("query_executor_type") == "http":
                raise ValueError(f"第 {index} 个数据源名称重复，和第 {existing_index} 个相同: {member_identity}")
            raise ValueError(f"第 {index} 个数据源连接重复，和第 {existing_index} 个相同: {member_identity}")
        seen[member_identity] = index
        result.append(normalized_config)
    return result


def _extract_member_source_configs(
    datasource: dict,
    source_type: str,
    default_query_executor_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """从数据源对象中提取成员执行源配置。"""
    configs: List[Dict[str, Any]] = []
    for member in datasource.get("member_sources") or []:
        configs.append(_normalize_member_source_config(source_type, {
            "datasource_name": (member or {}).get("datasource_name"),
            "query_executor_type": (member or {}).get("query_executor_type"),
            "host": (member or {}).get("host"),
            "port": (member or {}).get("port"),
            "database": (member or {}).get("database"),
        }, default_query_executor_type))
    if not configs:
        source_config = datasource.get("source_config") or {}
        if source_config.get("host") and source_config.get("port") and source_config.get("database"):
            configs.append(_normalize_member_source_config(source_type, {
                "datasource_name": datasource.get("ds_name"),
                "query_executor_type": datasource.get("query_executor_type"),
                "host": source_config.get("host"),
                "port": source_config.get("port"),
                "database": source_config.get("database"),
            }, default_query_executor_type))
    return _dedupe_member_source_configs(configs, source_type, default_query_executor_type)


def _merge_member_source_lines(
    current: dict,
    next_template_source_config: Dict[str, Any],
    source_type: str,
    default_query_executor_type: Optional[str] = None,
    member_source_configs: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """合并模板源和成员源，模板源始终排在首位。"""
    merged_configs: List[Dict[str, Any]] = [_normalize_member_source_config(source_type, next_template_source_config, default_query_executor_type)]

    for item in member_source_configs or []:
        merged_configs.append(_normalize_member_source_config(source_type, item, default_query_executor_type))

    if member_source_configs is None:
        current_template_identity = str(current.get("template_datasource_id") or current.get("template_source") or "").strip()
        for source_config in _extract_member_source_configs(current, source_type, default_query_executor_type):
            if _build_member_source_identity(source_config, source_type, default_query_executor_type) == current_template_identity:
                continue
            merged_configs.append(source_config)

    return _dedupe_member_source_configs(merged_configs, source_type, default_query_executor_type)


def _get_template_member_source_config(datasource: dict) -> Dict[str, Any]:
    """提取当前模板成员配置，避免仅刷新表结构时破坏模板成员名称。"""
    template_member_id = str(datasource.get("template_datasource_id") or "").strip()
    for member in datasource.get("member_sources") or []:
        member_id = str((member or {}).get("member_datasource_id") or "").strip()
        if template_member_id and member_id != template_member_id:
            continue
        return {
            "datasource_name": (member or {}).get("datasource_name"),
            "query_executor_type": (member or {}).get("query_executor_type") or datasource.get("query_executor_type") or "direct",
            "host": (member or {}).get("host"),
            "port": (member or {}).get("port"),
            "database": (member or {}).get("database"),
        }

    source_config = datasource.get("source_config") or {}
    return {
        "datasource_name": datasource.get("ds_name"),
        "query_executor_type": datasource.get("query_executor_type") or "direct",
        "host": source_config.get("host"),
        "port": source_config.get("port"),
        "database": source_config.get("database"),
    }


class DataSourceManager:
    """数据源管理器。"""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = Path(base_dir or NL2SQL_BASE_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_store = DataSourceDBStore()
        logger.info("数据源 DB 存储已启用")

    async def _run_sync(self, func, *args, **kwargs):
        """在线程池执行同步数据库管理逻辑，避免阻塞事件循环。"""
        return await asyncio.to_thread(func, *args, **kwargs)

    def _normalize_semantic_relationships(self, datasource: dict):
        """规范化语义关系：统一使用 join_conditions，并同步首条到 left/right 字段。"""
        configs = datasource.get("semantic_configs", [])
        for config in configs:
            relationships = config.get("relationships", [])
            normalized_relationships = []
            for rel in relationships:
                next_rel = dict(rel or {})
                join_conditions = next_rel.get("join_conditions") or []
                normalized_conditions = []
                for cond in join_conditions:
                    left_col = (cond or {}).get("left_column")
                    right_col = (cond or {}).get("right_column")
                    if left_col and right_col:
                        normalized_conditions.append({"left_column": left_col, "right_column": right_col})

                if not normalized_conditions:
                    left_col = next_rel.get("left_column")
                    right_col = next_rel.get("right_column")
                    if left_col and right_col:
                        normalized_conditions.append({"left_column": left_col, "right_column": right_col})

                if not normalized_conditions:
                    continue

                next_rel["join_conditions"] = normalized_conditions
                next_rel["left_column"] = normalized_conditions[0]["left_column"]
                next_rel["right_column"] = normalized_conditions[0]["right_column"]
                normalized_relationships.append(next_rel)

            config["relationships"] = normalized_relationships

    def _get_datasource_or_raise(self, datasource_id: str) -> dict:
        """读取数据源，不存在时抛错。"""
        datasource = self.get_datasource(datasource_id)
        if not datasource:
            raise ValueError(f"数据源不存在: {datasource_id}")
        return datasource

    def _load_datasource(self, datasource_id: str) -> dict:
        """读取最新数据源并补充语义配置。"""
        datasource = self.db_store.get_datasource(datasource_id)
        if not datasource:
            raise ValueError(f"数据源写入后读取失败: {datasource_id}")
        datasource["semantic_configs"] = self.db_store.get_semantic_configs_by_group_id(datasource.get("group_id"))
        return datasource

    def _save_datasource(
        self,
        datasource_id: str,
        group_name: str,
        group_desc: str,
        source_type: str,
        query_executor_type: str,
        template_source: str,
        member_source_configs: List[Dict[str, Any]],
        tables: Any = _UNSET,
        mapped_modules: Any = _UNSET,
        is_default: Any = _UNSET,
        create_by: str = "",
        update_by: str = "",
    ) -> dict:
        """写入数据源组及表结构缓存。"""
        normalized_query_executor_type = _normalize_query_executor_type(source_type, query_executor_type)
        validated_member_source_configs = _validate_member_source_configs(
            member_source_configs,
            source_type,
            normalized_query_executor_type,
        )
        persisted_member_source_lines: List[str] = []
        for item in _dedupe_member_source_configs(validated_member_source_configs, source_type, normalized_query_executor_type):
            member_line = _build_member_source_text_line(source_type, item, normalized_query_executor_type)
            if member_line:
                persisted_member_source_lines.append(member_line)

        template_source_line = ""
        if validated_member_source_configs:
            template_source_line = _build_member_source_text_line(source_type, validated_member_source_configs[0], normalized_query_executor_type)
        if not template_source_line:
            template_source_line = template_source

        with self.db_store.transaction():
            self.db_store.upsert_datasource_group(
                group_id=datasource_id,
                group_name=group_name,
                group_desc=group_desc or "",
                source_type=source_type or "",
                template_source=template_source_line,
                member_sources_text="\n".join(persisted_member_source_lines),
                create_by=create_by,
                update_by=update_by,
            )
            update_kwargs: Dict[str, Any] = {
                "group_id": datasource_id,
                "mapped_modules": [] if mapped_modules is _UNSET else mapped_modules,
                "update_by": update_by,
            }
            if is_default is not _UNSET:
                update_kwargs["is_default"] = is_default
            self.db_store.update_datasource_group(**update_kwargs)
            if tables is not _UNSET:
                self.db_store.replace_datasource_tables(
                    group_id=datasource_id,
                    tables=tables or [],
                    create_by=create_by,
                    update_by=update_by,
                )
        return self._load_datasource(datasource_id)

    def get_all_datasources(self, include_tables: bool = True) -> List[Dict[str, Any]]:
        """获取所有数据源。"""
        return self.db_store.get_all_datasources(include_tables=include_tables)

    async def get_all_datasources_async(self, include_tables: bool = True) -> List[Dict[str, Any]]:
        """异步获取所有数据源。"""
        return await self._run_sync(self.get_all_datasources, include_tables)

    def has_datasource(self, datasource_id: str) -> bool:
        """判断数据源是否存在。"""
        return self.db_store.has_datasource(datasource_id)

    async def has_datasource_async(self, datasource_id: str) -> bool:
        """异步判断数据源是否存在。"""
        return await self._run_sync(self.has_datasource, datasource_id)

    def get_datasource(self, datasource_id: str) -> Optional[Dict[str, Any]]:
        """获取指定数据源。"""
        datasource = self.db_store.get_datasource(datasource_id)
        if not datasource:
            return None
        datasource["semantic_configs"] = self.db_store.get_semantic_configs_by_group_id(datasource.get("group_id"))
        return datasource

    async def get_datasource_async(self, datasource_id: str) -> Optional[Dict[str, Any]]:
        """异步获取指定数据源。"""
        return await self._run_sync(self.get_datasource, datasource_id)

    def get_execution_datasources(self, datasource_id: str, member_datasource_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """解析逻辑数据源下可执行的成员数据源列表。"""
        datasource = self._get_datasource_or_raise(datasource_id)

        member_sources = list(datasource.get("member_sources") or [])
        if not member_sources:
            source_config = datasource.get("source_config") or {}
            host = source_config.get("host")
            port = source_config.get("port")
            database = source_config.get("database")
            if host and port and database:
                member_sources = [{
                    "member_datasource_id": datasource.get("template_datasource_id") or datasource_id,
                    "datasource_name": datasource.get("ds_name") or datasource_id,
                    "source_type": datasource.get("source_type") or "",
                    "query_executor_type": datasource.get("query_executor_type") or "direct",
                    "host": host,
                    "port": port,
                    "database": database,
                }]

        if not member_sources:
            raise ValueError("当前数据源组下没有可执行的数据源")

        member_map: Dict[str, Dict[str, Any]] = {}
        for item in member_sources:
            member_id = str(item.get("member_datasource_id") or "").strip()
            if member_id:
                member_map[member_id] = item

        if not member_map:
            raise ValueError("当前数据源组成员配置无效")

        normalized_selected_ids: List[str] = []
        for member_id in member_datasource_ids or []:
            normalized_member_id = str(member_id or "").strip()
            if normalized_member_id and normalized_member_id not in normalized_selected_ids:
                normalized_selected_ids.append(normalized_member_id)

        if not normalized_selected_ids:
            default_member_id = str(datasource.get("template_datasource_id") or "").strip()
            if not default_member_id or default_member_id not in member_map:
                default_member_id = next(iter(member_map.keys()))
            normalized_selected_ids = [default_member_id]

        invalid_ids = [member_id for member_id in normalized_selected_ids if member_id not in member_map]
        if invalid_ids:
            raise ValueError(f"成员数据源不存在或不属于当前组: {', '.join(invalid_ids)}")

        result: List[Dict[str, Any]] = []
        for member_id in normalized_selected_ids:
            member = member_map[member_id]
            source_type = member.get("source_type") or datasource.get("source_type") or ""
            query_executor_type = member.get("query_executor_type") or datasource.get("query_executor_type") or "direct"
            namespace = str(member.get("namespace") or member.get("datasource_name") or "").strip()
            source_config = {
                "host": member.get("host"),
                "port": member.get("port"),
                "database": member.get("database"),
            }
            if query_executor_type == "http":
                if not namespace:
                    raise ValueError("HTTP 执行对象缺少名称，请在“名称”位置配置执行标识")
                source_config["namespace"] = namespace
            elif source_type == "mysql":
                credentials = resolve_mysql_credentials(source_config)
                source_config["user"] = credentials["user"]
                source_config["password"] = credentials["password"]
            elif source_type == "hive" and query_executor_type == "direct":
                credentials = resolve_hive_credentials(source_config)
                source_config["username"] = credentials["username"]
                source_config["password"] = credentials["password"]
            result.append({
                "id": member_id,
                "member_datasource_id": member_id,
                "member_name": namespace or member.get("datasource_name") or f"{member.get('host')}:{member.get('port')}/{member.get('database')}",
                "source_type": source_type,
                "query_executor_type": query_executor_type,
                "source_config": source_config,
            })
        return result

    async def get_execution_datasources_async(self, datasource_id: str, member_datasource_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """异步解析逻辑数据源下可执行的成员数据源列表。"""
        return await self._run_sync(self.get_execution_datasources, datasource_id, member_datasource_ids)

    def get_datasource_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """根据名称获取数据源。"""
        datasource = self.db_store.get_datasource_by_name(name)
        if not datasource:
            return None
        datasource["semantic_configs"] = self.db_store.get_semantic_configs_by_group_id(datasource.get("group_id"))
        return datasource

    async def get_datasource_by_name_async(self, name: str) -> Optional[Dict[str, Any]]:
        """异步根据名称获取数据源。"""
        return await self._run_sync(self.get_datasource_by_name, name)

    def create_datasource_group_from_sources(
        self,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: Dict[str, Any],
        member_source_configs: Optional[List[Dict[str, Any]]] = None,
        tables: Optional[List[Dict[str, Any]]] = None,
        create_by: str = "",
    ) -> Dict[str, Any]:
        """按模板源和成员连接信息创建数据源组。"""
        now = datetime.now()
        datasource_id = f"ds_{now.strftime('%Y%m%d%H%M%S')}_{now.microsecond}"
        template_source = _build_source_line(template_source_config)
        query_executor_type = str(template_source_config.get("query_executor_type") or "direct").strip().lower() or "direct"
        member_source_configs_merged = _merge_member_source_lines(
            current={},
            next_template_source_config=template_source_config,
            source_type=source_type,
            default_query_executor_type=query_executor_type,
            member_source_configs=member_source_configs,
        )
        created = self._save_datasource(
            datasource_id=datasource_id,
            group_name=group_name,
            group_desc=group_desc,
            source_type=source_type,
            query_executor_type=query_executor_type,
            template_source=template_source,
            member_source_configs=member_source_configs_merged,
            tables=tables or [],
            mapped_modules=[],
            is_default=False,
            create_by=create_by,
            update_by=create_by,
        )
        logger.info("创建数据源组: %s - %s, members=%s", datasource_id, group_name, len(member_source_configs_merged))
        return created

    async def create_datasource_group_from_sources_async(
        self,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: Dict[str, Any],
        member_source_configs: Optional[List[Dict[str, Any]]] = None,
        tables: Optional[List[Dict[str, Any]]] = None,
        create_by: str = "",
    ) -> Dict[str, Any]:
        """异步按模板源和成员连接信息创建数据源组。"""
        return await self._run_sync(
            self.create_datasource_group_from_sources,
            group_name,
            group_desc,
            source_type,
            template_source_config,
            member_source_configs,
            tables,
            create_by,
        )

    def update_datasource_group_from_sources(
        self,
        datasource_id: str,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: Dict[str, Any],
        member_source_configs: Optional[List[Dict[str, Any]]] = None,
        tables: Optional[List[Dict[str, Any]]] = None,
        update_by: str = "",
    ) -> Optional[Dict[str, Any]]:
        """按模板源和成员连接信息更新数据源组。"""
        current = self.get_datasource(datasource_id)
        if not current:
            return None

        template_source = _build_source_line(template_source_config)
        query_executor_type = str(template_source_config.get("query_executor_type") or "direct").strip().lower() or "direct"
        member_source_configs_merged = _merge_member_source_lines(
            current,
            template_source_config,
            source_type,
            query_executor_type,
            member_source_configs,
        )
        updated = self._save_datasource(
            datasource_id=datasource_id,
            group_name=group_name,
            group_desc=group_desc,
            source_type=source_type,
            query_executor_type=query_executor_type,
            template_source=template_source,
            member_source_configs=member_source_configs_merged,
            tables=tables if tables is not None else current.get("ds_tables", []),
            mapped_modules=current.get("mapped_modules", []),
            is_default=current.get("is_default", False),
            create_by=current.get("create_by") or "",
            update_by=update_by or current.get("update_by") or "",
        )
        logger.info("更新数据源组: %s - %s, members=%s", datasource_id, group_name, len(member_source_configs_merged))
        return updated

    async def update_datasource_group_from_sources_async(
        self,
        datasource_id: str,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: Dict[str, Any],
        member_source_configs: Optional[List[Dict[str, Any]]] = None,
        tables: Optional[List[Dict[str, Any]]] = None,
        update_by: str = "",
    ) -> Optional[Dict[str, Any]]:
        """异步按模板源和成员连接信息更新数据源组。"""
        return await self._run_sync(
            self.update_datasource_group_from_sources,
            datasource_id,
            group_name,
            group_desc,
            source_type,
            template_source_config,
            member_source_configs,
            tables,
            update_by,
        )

    def update_datasource(
        self,
        datasource_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tables: Optional[List[dict]] = None,
        source_type: Optional[str] = None,
        source_config: Optional[dict] = None,
        semantic_config: Optional[dict] = None,
        semantic_configs: Optional[List[dict]] = None,
        mapped_modules: Optional[List[str]] = None,
        update_by: str = "",
    ) -> Optional[Dict[str, Any]]:
        """更新数据源。"""
        current = self.get_datasource(datasource_id)
        if not current:
            return None

        next_template_source = str(current.get("template_source") or current.get("template_datasource_id") or "").strip()
        next_template_source_config: Dict[str, Any] = _get_template_member_source_config(current)
        if source_config is not None:
            sanitized_source_config = sanitize_source_config(source_config)
            next_template_source = _build_source_line(source_config)
            next_template_source_config = {
                **next_template_source_config,
                **sanitized_source_config,
            }

        updated = self._save_datasource(
            datasource_id=datasource_id,
            group_name=name if name is not None else current.get("ds_name") or datasource_id,
            group_desc=description if description is not None else current.get("ds_description") or "",
            source_type=source_type if source_type is not None else current.get("source_type") or "",
            query_executor_type=current.get("query_executor_type") or "direct",
            template_source=next_template_source,
            member_source_configs=_merge_member_source_lines(
                current,
                next_template_source_config,
                source_type if source_type is not None else current.get("source_type") or "",
                current.get("query_executor_type") or "direct",
            ),
            tables=tables if tables is not None else current.get("ds_tables", []),
            mapped_modules=mapped_modules if mapped_modules is not None else current.get("mapped_modules", []),
            is_default=current.get("is_default", False),
            create_by=current.get("create_by") or "",
            update_by=update_by or current.get("update_by") or "",
        )

        if semantic_config is not None:
            logger.warning("单 semantic_config 已废弃，忽略本次 update 请求: datasource_id=%s", datasource_id)
        if semantic_configs is not None:
            logger.warning("update_datasource 不再直接处理 semantic_configs，请使用语义配置接口: datasource_id=%s", datasource_id)

        logger.info("更新数据源: %s", datasource_id)
        return updated

    async def update_datasource_async(
        self,
        datasource_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tables: Optional[List[dict]] = None,
        source_type: Optional[str] = None,
        source_config: Optional[dict] = None,
        semantic_config: Optional[dict] = None,
        semantic_configs: Optional[List[dict]] = None,
        mapped_modules: Optional[List[str]] = None,
        update_by: str = "",
    ) -> Optional[Dict[str, Any]]:
        """异步更新数据源。"""
        return await self._run_sync(
            self.update_datasource,
            datasource_id,
            name,
            description,
            tables,
            source_type,
            source_config,
            semantic_config,
            semantic_configs,
            mapped_modules,
            update_by,
        )

    def delete_datasource(self, datasource_id: str, force: bool = False) -> bool:
        """删除数据源。"""
        datasource = self.get_datasource(datasource_id)
        if not datasource:
            return False

        self.db_store.soft_delete_group(datasource.get("group_id") or datasource_id)
        logger.info("删除数据源: %s, force=%s", datasource_id, force)
        return True

    async def delete_datasource_async(self, datasource_id: str, force: bool = False) -> bool:
        """异步删除数据源。"""
        return await self._run_sync(self.delete_datasource, datasource_id, force)

    def get_datasource_tables(self, datasource_id: str) -> List[dict]:
        """获取数据源的所有表。"""
        datasource = self.get_datasource(datasource_id)
        if datasource:
            return datasource.get("ds_tables", [])
        return []

    def get_table_info(self, datasource_id: str, table_name: str) -> Optional[dict]:
        """获取指定表的信息。"""
        tables = self.get_datasource_tables(datasource_id)
        for table in tables:
            if table.get("table_name") == table_name:
                return table
        return None

    def get_semantic_configs(self, datasource_id: str) -> List[dict]:
        """获取数据源的所有语义配置。"""
        return self.db_store.get_semantic_configs(datasource_id)

    async def get_semantic_configs_async(self, datasource_id: str) -> List[dict]:
        """异步获取数据源的所有语义配置。"""
        return await self._run_sync(self.get_semantic_configs, datasource_id)

    def get_semantic_config(self, datasource_id: str, config_id: str) -> Optional[dict]:
        """获取指定的语义配置。"""
        return self.db_store.get_semantic_config(datasource_id, config_id)

    async def get_semantic_config_async(self, datasource_id: str, config_id: str) -> Optional[dict]:
        """异步获取指定的语义配置。"""
        return await self._run_sync(self.get_semantic_config, datasource_id, config_id)

    def create_semantic_config(
        self,
        datasource_id: str,
        name: str,
        description: Optional[str] = None,
        enabled: bool = True,
        metrics: Optional[List[dict]] = None,
        dimensions: Optional[List[dict]] = None,
        relationships: Optional[List[dict]] = None,
    ) -> Optional[dict]:
        """创建语义配置。"""
        datasource = self.get_datasource(datasource_id)
        if not datasource:
            return None

        now = datetime.now()
        config_id = f"sc_{now.strftime('%Y%m%d%H%M%S')}_{now.microsecond}"
        now_str = _format_time(now)
        config = {
            "id": config_id,
            "group_id": datasource.get("group_id"),
            "name": name,
            "description": description or "",
            "enabled": enabled,
            "metrics": metrics or [],
            "dimensions": dimensions or [],
            "relationships": relationships or [],
            "create_time": now_str,
            "update_time": now_str,
            "create_by": datasource.get("create_by") or "",
            "update_by": datasource.get("update_by") or "",
        }
        self._normalize_semantic_relationships({"semantic_configs": [config]})
        self.db_store.upsert_semantic_config(datasource_id, config)

        logger.info("创建语义配置: %s - %s", config_id, name)
        return self.get_semantic_config(datasource_id, config_id)

    async def create_semantic_config_async(
        self,
        datasource_id: str,
        name: str,
        description: Optional[str] = None,
        enabled: bool = True,
        metrics: Optional[List[dict]] = None,
        dimensions: Optional[List[dict]] = None,
        relationships: Optional[List[dict]] = None,
    ) -> Optional[dict]:
        """异步创建语义配置。"""
        return await self._run_sync(
            self.create_semantic_config,
            datasource_id,
            name,
            description,
            enabled,
            metrics,
            dimensions,
            relationships,
        )

    def update_semantic_config(
        self,
        datasource_id: str,
        config_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
        metrics: Optional[List[dict]] = None,
        dimensions: Optional[List[dict]] = None,
        relationships: Optional[List[dict]] = None,
    ) -> Optional[dict]:
        """更新语义配置。"""
        current = self.get_semantic_config(datasource_id, config_id)
        if not current:
            return None

        config = {
            **current,
            "name": name if name is not None else current.get("name"),
            "description": description if description is not None else current.get("description"),
            "enabled": enabled if enabled is not None else current.get("enabled", True),
            "metrics": metrics if metrics is not None else current.get("metrics", []),
            "dimensions": dimensions if dimensions is not None else current.get("dimensions", []),
            "relationships": relationships if relationships is not None else current.get("relationships", []),
            "update_time": _format_time(),
        }
        self._normalize_semantic_relationships({"semantic_configs": [config]})
        self.db_store.upsert_semantic_config(datasource_id, config)

        logger.info("更新语义配置: %s", config_id)
        return self.get_semantic_config(datasource_id, config_id)

    async def update_semantic_config_async(
        self,
        datasource_id: str,
        config_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
        metrics: Optional[List[dict]] = None,
        dimensions: Optional[List[dict]] = None,
        relationships: Optional[List[dict]] = None,
    ) -> Optional[dict]:
        """异步更新语义配置。"""
        return await self._run_sync(
            self.update_semantic_config,
            datasource_id,
            config_id,
            name,
            description,
            enabled,
            metrics,
            dimensions,
            relationships,
        )

    def delete_semantic_config(self, datasource_id: str, config_id: str) -> bool:
        """删除语义配置。"""
        config = self.get_semantic_config(datasource_id, config_id)
        if not config:
            return False
        self.db_store.soft_delete_semantic_config(config_id)
        logger.info("删除语义配置: %s", config_id)
        return True

    async def delete_semantic_config_async(self, datasource_id: str, config_id: str) -> bool:
        """异步删除语义配置。"""
        return await self._run_sync(self.delete_semantic_config, datasource_id, config_id)

    def get_enabled_semantic_configs(self, datasource_id: str) -> List[dict]:
        """获取数据源所有启用的语义配置。"""
        configs = self.get_semantic_configs(datasource_id)
        return [c for c in configs if c.get("enabled", True)]

    def set_default_datasource(self, datasource_id: str) -> bool:
        """设置默认数据源。"""
        datasource = self.get_datasource(datasource_id)
        if not datasource:
            return False

        self.db_store.clear_all_default_datasources(update_by=datasource.get("update_by") or "")
        self.db_store.update_datasource_group(
            datasource.get("group_id"),
            is_default=True,
            update_by=datasource.get("update_by") or "",
        )
        logger.info("设置默认数据源: %s", datasource_id)
        return True

    async def set_default_datasource_async(self, datasource_id: str) -> bool:
        """异步设置默认数据源。"""
        return await self._run_sync(self.set_default_datasource, datasource_id)

    def get_default_datasource(self) -> Optional[Dict[str, Any]]:
        """获取默认数据源。"""
        datasource = self.db_store.get_default_datasource()
        if not datasource:
            return None
        datasource["semantic_configs"] = self.db_store.get_semantic_configs_by_group_id(datasource.get("group_id"))
        return datasource

    async def get_default_datasource_async(self) -> Optional[Dict[str, Any]]:
        """异步获取默认数据源。"""
        return await self._run_sync(self.get_default_datasource)

    def clear_default_datasource(self, datasource_id: str) -> bool:
        """取消数据源的默认标记。"""
        datasource = self.get_datasource(datasource_id)
        if not datasource or not datasource.get("is_default"):
            return False

        self.db_store.update_datasource_group(
            datasource.get("group_id"),
            is_default=False,
            update_by=datasource.get("update_by") or "",
        )
        logger.info("取消默认数据源: %s", datasource_id)
        return True

    async def clear_default_datasource_async(self, datasource_id: str) -> bool:
        """异步取消数据源的默认标记。"""
        return await self._run_sync(self.clear_default_datasource, datasource_id)

    async def get_schema_async(
        self,
        datasource_id: str,
        source: str = "cache",
        table_names: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """异步获取数据源的表结构。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            raise ValueError(f"数据源不存在: {datasource_id}")

        if source == "cache":
            tables = datasource.get("ds_tables", [])
            if table_names:
                tables = [t for t in tables if t.get("table_name") in table_names]
            return tables

        source_type = datasource.get("source_type")
        source_config = datasource.get("source_config", {})
        if source_type not in ("mysql", "hive"):
            raise ValueError(f"不支持实时获取: {source_type}，只支持 mysql/hive")
        if not source_config:
            raise ValueError("数据源连接配置不完整")

        try:
            if source_type == "mysql":
                from apps.chat_data_app.services.mysql_sync import MySQLSyncService

                mysql_credentials = resolve_mysql_credentials(source_config)
                service = MySQLSyncService(
                    host=source_config.get("host"),
                    port=source_config.get("port", 3306),
                    database=source_config.get("database"),
                    user=mysql_credentials["user"],
                    password=mysql_credentials["password"],
                )
                return await asyncio.wait_for(
                    service.get_full_schema_async(table_names),
                    timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
                )

            from apps.chat_data_app.services.hive_sync import HiveSyncService

            hive_credentials = resolve_hive_credentials(source_config)
            service = HiveSyncService(
                host=source_config.get("host"),
                port=source_config.get("port", 10000),
                database=source_config.get("database", "default"),
                username=hive_credentials["username"],
                password=hive_credentials.get("password"),
            )
            return await asyncio.wait_for(
                service.get_full_schema_async(table_names),
                timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as err:
            logger.error("异步获取表结构超时: datasource_id=%s source_type=%s timeout=%ss", datasource_id, source_type, DATASOURCE_SYNC_TIMEOUT_SECONDS)
            raise ConnectionError(f"数据库连接超时（{DATASOURCE_SYNC_TIMEOUT_SECONDS}秒）") from err
        except Exception as err:
            logger.error("异步获取表结构失败: %s", err)
            raise ConnectionError(f"数据库连接失败: {str(err)}")


_datasource_manager_instance: Optional[DataSourceManager] = None


def get_datasource_manager() -> DataSourceManager:
    """获取数据源管理器单例。"""
    global _datasource_manager_instance
    if _datasource_manager_instance is None:
        _datasource_manager_instance = DataSourceManager()
    return _datasource_manager_instance
