"""
数据源管理服务。

当前运行时仅使用 MySQL 表，不再读写本地 JSON 文件。
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import get_logger
from utils.storage_paths import NL2SQL_BASE_DIR
from ..daos.datasource_dao import DataSourceDao
from .datasource_credentials import resolve_hive_credentials, resolve_mysql_credentials, sanitize_source_config
from .execution_permission import (
    filter_datasource_members_by_allowed_org_codes,
    filter_datasources_by_allowed_org_codes,
    get_allowed_org_codes_by_executor,
)
from .hive_meta_http import get_hive_meta_schema_async, get_hive_meta_tables_async
from .hive_sync import HiveSyncService
from .mysql_sync import MySQLSyncService
from .semantic_common import ensure_dimension_logical_type

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


def _build_persisted_source_line(
    source_type: str,
    source_config: Optional[dict],
    default_query_executor_type: Optional[str] = None,
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
    source_config: Optional[dict],
    default_query_executor_type: Optional[str] = None,
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
    source_config: Optional[dict],
    default_query_executor_type: Optional[str] = None,
) -> Dict[str, Any]:
    """标准化成员执行源配置。"""
    config = sanitize_source_config(source_config)
    normalized_org_name = str(config.get("org_name") or "").strip()
    normalized_org_code = str(config.get("org_code") or "").strip()
    normalized_query_executor_type = _normalize_query_executor_type(source_type, config.get("query_executor_type") or default_query_executor_type)
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
    source_config: Optional[dict],
    source_type: str,
    default_query_executor_type: Optional[str] = None,
) -> str:
    """根据执行方式生成成员唯一标识。"""
    normalized_config = _normalize_member_source_config(source_type, source_config, default_query_executor_type)
    org_code = str(normalized_config.get("org_code") or "").strip()
    if org_code:
        return org_code
    return _build_source_line(normalized_config)


def _build_member_source_text_line(
    source_type: str,
    source_config: Optional[dict],
    default_query_executor_type: Optional[str] = None,
) -> str:
    """将成员执行源配置格式化为持久化文本。"""
    return _build_persisted_source_line(source_type, source_config, default_query_executor_type)


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
            raise ValueError(f"第 {index} 个机构编码重复，和第 {existing_index} 个相同: {member_identity}")
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
            "org_name": (member or {}).get("org_name"),
            "org_code": (member or {}).get("org_code"),
            "query_executor_type": (member or {}).get("query_executor_type"),
            "host": (member or {}).get("host"),
            "port": (member or {}).get("port"),
            "database": (member or {}).get("database"),
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


def _build_member_sources_text(
    source_type: str,
    member_source_configs: List[Dict[str, Any]],
    query_executor_type: str,
) -> str:
    lines: List[str] = []
    for item in _dedupe_member_source_configs(member_source_configs, source_type, query_executor_type):
        member_line = _build_member_source_text_line(source_type, item, query_executor_type)
        if member_line:
            lines.append(member_line)
    return "\n".join(lines)


def _get_template_member_source_config(datasource: dict) -> Dict[str, Any]:
    """提取当前模板成员配置，避免仅刷新表结构时破坏模板成员名称。"""
    template_member_id = str(datasource.get("template_datasource_id") or "").strip()
    for member in datasource.get("member_sources") or []:
        member_id = str((member or {}).get("member_datasource_id") or "").strip()
        if template_member_id and member_id != template_member_id:
            continue
        return {
            "org_name": (member or {}).get("org_name"),
            "org_code": (member or {}).get("org_code"),
            "query_executor_type": (member or {}).get("query_executor_type") or datasource.get("query_executor_type") or "direct",
            "host": (member or {}).get("host"),
            "port": (member or {}).get("port"),
            "database": (member or {}).get("database"),
        }

    return {}


def _build_meta_source_config(datasource: dict) -> Dict[str, Any]:
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
    query_executor_type = str(template_source_config.get("query_executor_type") or datasource.get("query_executor_type") or "direct").strip().lower()
    source_config["query_executor_type"] = query_executor_type if query_executor_type in SUPPORTED_QUERY_EXECUTOR_TYPES else "direct"
    return source_config


class DataSourceManager:
    """数据源管理器。"""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = Path(base_dir or NL2SQL_BASE_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.datasource_dao = DataSourceDao()
        logger.info("数据源 DAO 已启用")

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

    async def _get_datasource_or_raise_async(self, datasource_id: str) -> dict:
        """异步读取数据源，不存在时抛错。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            raise ValueError(f"数据源不存在: {datasource_id}")
        return datasource

    async def _load_datasource_async(self, datasource_id: str) -> dict:
        """异步读取最新数据源并补充语义配置。"""
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        if not datasource:
            raise ValueError(f"数据源写入后读取失败: {datasource_id}")
        datasource["semantic_configs"] = await self.datasource_dao.get_semantic_configs_by_group_id_async(datasource.get("group_id"))
        self._enrich_semantic_runtime_fields(datasource)
        return datasource

    def _enrich_semantic_runtime_fields(self, datasource: dict):
        """补齐语义配置运行时字段，供 DSL2SQL 识别方言和表结构。"""
        source_type = datasource.get("source_type") or "mysql"
        tables = datasource.get("ds_tables") or []
        for config in datasource.get("semantic_configs") or []:
            config["source_type"] = config.get("source_type") or source_type
            config["tables"] = config.get("tables") or tables
            if config.get("relationships") and not config.get("joins"):
                config["joins"] = config.get("relationships")

    def _build_physical_metadata_source_config(self, datasource: Dict[str, Any]) -> Dict[str, Any]:
        """构建物理元数据读取使用的模板源配置。"""
        source_type = datasource.get("source_type")
        if source_type == "hive":
            return _build_meta_source_config(datasource)
        return dict(datasource.get("source_config") or {})

    async def _load_physical_schema_async(
        self,
        source_type: str,
        source_config: Dict[str, Any],
        table_names: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """按物理连接配置读取完整表结构。"""
        if source_type not in ("mysql", "hive"):
            raise ValueError(f"不支持实时获取: {source_type}，只支持 mysql/hive")
        if not source_config:
            raise ValueError("数据源连接配置不完整")
        if str(source_config.get("query_executor_type") or "direct").strip().lower() == "http":
            if source_type != "hive":
                raise ValueError("HTTP 元数据模式当前仅支持 Hive 数据源")
            return await get_hive_meta_schema_async(source_config, table_names, user_id=user_id, username=username)

        if source_type == "mysql":
            mysql_credentials = resolve_mysql_credentials(source_config)
            service = MySQLSyncService(
                host=source_config.get("host", "localhost"),
                port=source_config.get("port", 3306),
                database=source_config.get("database", ""),
                user=mysql_credentials["user"],
                password=mysql_credentials["password"],
            )
            return await asyncio.wait_for(
                service.get_full_schema_async(table_names),
                timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
            )

        hive_credentials = resolve_hive_credentials(source_config)
        service = HiveSyncService(
            host=source_config.get("host", "localhost"),
            port=source_config.get("port", 10000),
            database=source_config.get("database", "default"),
            username=hive_credentials.get("username"),
            password=hive_credentials.get("password"),
        )
        return await asyncio.wait_for(
            service.get_full_schema_async(table_names),
            timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
        )

    async def _load_physical_table_names_async(
        self,
        source_type: str,
        source_config: Dict[str, Any],
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> List[Any]:
        """按物理连接配置读取表名列表。"""
        if source_type not in ("mysql", "hive"):
            raise ValueError(f"不支持获取表列表: {source_type}，只支持 mysql/hive")
        if not source_config:
            raise ValueError("数据源连接配置不完整")
        if str(source_config.get("query_executor_type") or "direct").strip().lower() == "http":
            if source_type != "hive":
                raise ValueError("HTTP 元数据模式当前仅支持 Hive 数据源")
            return await get_hive_meta_tables_async(source_config, user_id=user_id, username=username)

        if source_type == "mysql":
            mysql_credentials = resolve_mysql_credentials(source_config)
            service = MySQLSyncService(
                host=source_config.get("host", "localhost"),
                port=source_config.get("port", 3306),
                database=source_config.get("database", ""),
                user=mysql_credentials["user"],
                password=mysql_credentials["password"],
            )
            return await asyncio.wait_for(
                service.get_tables_async(),
                timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
            )

        hive_credentials = resolve_hive_credentials(source_config)
        service = HiveSyncService(
            host=source_config.get("host", "localhost"),
            port=source_config.get("port", 10000),
            database=source_config.get("database", "default"),
            username=hive_credentials.get("username"),
            password=hive_credentials.get("password"),
        )
        return await asyncio.wait_for(
            service.get_tables_async(),
            timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
        )

    def _extract_metric_source_column(self, metric: Dict[str, Any]) -> str:
        """从指标表达式中提取单字段物理列名。"""
        expression = str(metric.get("expression") or "").strip()
        match = re.match(r"^(?:COUNT|SUM|AVG|MAX|MIN)\s*\(\s*(?:DISTINCT\s+)?`?([^`(),\s]+)`?\s*\)$", expression, re.IGNORECASE)
        if match:
            return match.group(1).split(".")[-1].strip("`")
        match = re.match(r"^`?([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)`?$", expression)
        if match:
            return match.group(1).split(".")[-1].strip("`")
        field_name = str(metric.get("field") or "").strip()
        if field_name:
            return field_name.split(".")[-1].strip("`")
        return ""

    def _format_column_dict_supplement(self, column_dict: Dict[str, Any]) -> str:
        """将字段数据字典格式化为模型可读的字段补充说明。"""
        parts: List[str] = []
        enum_values = [
            f"{item.get('value')}={item.get('label')}" if item.get("label") else str(item.get("value"))
            for item in column_dict.get("enum_values") or []
            if item.get("value")
        ]
        if enum_values:
            parts.append(f"枚举值：{'；'.join(enum_values[:30])}")
        storage_format = str(column_dict.get("storage_format") or "").strip()
        if storage_format:
            parts.append(f"存储格式：{storage_format}")
        unit = str(column_dict.get("unit") or "").strip()
        if unit:
            parts.append(f"单位：{unit}")
        return "\n".join(parts)

    def _merge_metadata_supplement(self, current: Any, column_dict_supplement: str) -> str:
        """合并指标集补充和物理字段字典补充，仅用于运行时提示词。"""
        current_text = str(current or "").strip()
        dict_text = str(column_dict_supplement or "").strip()
        if not current_text:
            return dict_text
        if not dict_text or dict_text in current_text:
            return current_text
        return f"{current_text}\n{dict_text}"

    @staticmethod
    def _column_dict_enum_allowed_values(column_dict: Dict[str, Any]) -> List[str]:
        return [
            str(enum_item.get("label") or enum_item.get("value")).strip()
            for enum_item in column_dict.get("enum_values") or []
            if str(enum_item.get("label") or enum_item.get("value") or "").strip()
        ]

    @staticmethod
    def _column_dict_enum_mapping(column_dict: Dict[str, Any]) -> Dict[str, str]:
        return {
            str(enum_item.get("label") or enum_item.get("value")).strip(): str(enum_item.get("value")).strip()
            for enum_item in column_dict.get("enum_values") or []
            if str(enum_item.get("label") or enum_item.get("value") or "").strip() and str(enum_item.get("value") or "").strip()
        }

    @staticmethod
    def _enum_mapping_covers_allowed_values(allowed_values: Any, mapping: Any) -> bool:
        allowed_set = {str(item).strip() for item in allowed_values or [] if str(item or "").strip()} if isinstance(allowed_values, list) else set()
        mapping_keys = {str(key).strip() for key, value in (mapping or {}).items() if str(key or "").strip() and str(value or "").strip()} if isinstance(mapping, dict) else set()
        return bool(allowed_set and allowed_set <= mapping_keys)

    def _merge_column_dict_enum_config(self, dimension: Dict[str, Any], column_dict: Dict[str, Any]) -> None:
        allowed_values = self._column_dict_enum_allowed_values(column_dict)
        mapping = self._column_dict_enum_mapping(column_dict)
        if allowed_values and not dimension.get("allowed_values"):
            dimension["allowed_values"] = allowed_values
        if mapping and not dimension.get("mapping"):
            dimension["mapping"] = mapping
        if not dimension.get("logical_type") and self._enum_mapping_covers_allowed_values(dimension.get("allowed_values"), dimension.get("mapping")):
            dimension["logical_type"] = "enum"

    def _merge_dimension_column_dict(self, dimension: Dict[str, Any], column_dict: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not column_dict:
            return ensure_dimension_logical_type(dimension)
        self._merge_column_dict_enum_config(dimension, column_dict)
        dimension = ensure_dimension_logical_type(dimension)
        supplement = self._format_column_dict_supplement(column_dict)
        if supplement:
            dimension["metadata_supplement"] = self._merge_metadata_supplement(dimension.get("metadata_supplement"), supplement)
        return dimension

    @staticmethod
    def _normalize_runtime_semantic_config(config: Dict[str, Any]) -> Dict[str, Any]:
        next_config = dict(config or {})
        next_config["dimensions"] = [ensure_dimension_logical_type(item) for item in next_config.get("dimensions") or []]
        return next_config

    def _merge_metric_column_dict(self, metric: Dict[str, Any], column_dict: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not column_dict:
            return metric
        supplement = self._format_column_dict_supplement(column_dict)
        if supplement:
            metric["metadata_supplement"] = self._merge_metadata_supplement(metric.get("metadata_supplement"), supplement)
        return metric

    def _merge_column_dict_into_semantic_config(self, config: Dict[str, Any], column_dicts: Dict[tuple[str, str], Dict[str, Any]]) -> Dict[str, Any]:
        """把物理字段数据字典临时合并到语义配置运行时对象。"""
        next_config = dict(config or {})
        next_dimensions: List[Dict[str, Any]] = []
        for item in next_config.get("dimensions") or []:
            dimension = dict(item or {})
            key = (str(dimension.get("table") or "").strip(), str(dimension.get("column") or dimension.get("field_name") or "").strip())
            next_dimensions.append(self._merge_dimension_column_dict(dimension, column_dicts.get(key)))

        next_metrics: List[Dict[str, Any]] = []
        for item in next_config.get("metrics") or []:
            metric = dict(item or {})
            key = (str(metric.get("table") or "").strip(), self._extract_metric_source_column(metric))
            next_metrics.append(self._merge_metric_column_dict(metric, column_dicts.get(key)))

        next_config["dimensions"] = next_dimensions
        next_config["metrics"] = next_metrics
        return next_config

    async def _enrich_semantic_config_column_dict_async(self, datasource_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """按物理字段字典增强单个语义配置运行时对象。"""
        group_id = str((config or {}).get("group_id") or "").strip()
        if not group_id:
            datasource = await self.datasource_dao.get_datasource_async(datasource_id)
            group_id = str((datasource or {}).get("group_id") or "").strip()
        if not group_id:
            return self._normalize_runtime_semantic_config(config)
        try:
            column_dicts = await self.datasource_dao.get_column_dicts_by_group_id_async(group_id)
        except AttributeError:
            column_dicts = {}
        if not column_dicts:
            return self._normalize_runtime_semantic_config(config)
        return self._merge_column_dict_into_semantic_config(config, column_dicts)

    async def _save_datasource_async(
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
        query_context: str = "",
        recommended_questions: Optional[List[str]] = None,
        create_by: str = "",
        update_by: str = "",
        create_by_name: str = "",
        update_by_name: str = "",
    ) -> dict:
        """异步写入数据源组及表结构缓存。"""
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
                create_by=create_by,
                update_by=update_by,
                create_by_name=create_by_name,
                update_by_name=update_by_name,
            )
            update_kwargs: Dict[str, Any] = {
                "group_id": datasource_id,
                "mapped_modules": [] if mapped_modules is _UNSET else mapped_modules,
                "update_by": update_by,
                "update_by_name": update_by_name,
            }
            if is_default is not _UNSET:
                update_kwargs["is_default"] = is_default
            await self.datasource_dao.update_datasource_group_async(**update_kwargs)
            if tables is not _UNSET:
                await self.datasource_dao.replace_datasource_tables_async(
                    group_id=datasource_id,
                    tables=tables or [],
                    create_by=create_by,
                    update_by=update_by,
                    create_by_name=create_by_name,
                    update_by_name=update_by_name,
                )
        return await self._load_datasource_async(datasource_id)

    async def get_all_datasources_async(self, include_tables: bool = True) -> List[Dict[str, Any]]:
        """异步获取所有数据源。"""
        return await self.datasource_dao.get_all_datasources_async(include_tables=include_tables)

    async def get_authorized_datasources_async(
        self,
        include_tables: bool = True,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """异步获取当前用户有机构权限的数据源。"""
        datasources = await self.get_all_datasources_async(include_tables=include_tables)
        allowed_org_codes = await get_allowed_org_codes_by_executor(user_id, username)
        return filter_datasources_by_allowed_org_codes(datasources, allowed_org_codes)

    async def has_datasource_async(self, datasource_id: str) -> bool:
        """异步判断数据源是否存在。"""
        return await self.datasource_dao.has_datasource_async(datasource_id)

    async def get_datasource_async(self, datasource_id: str) -> Optional[Dict[str, Any]]:
        """异步获取指定数据源。"""
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        if not datasource:
            return None
        datasource["semantic_configs"] = await self.datasource_dao.get_semantic_configs_by_group_id_async(datasource.get("group_id"))
        self._enrich_semantic_runtime_fields(datasource)
        return datasource

    async def get_datasource_meta_async(self, datasource_id: str) -> Optional[Dict[str, Any]]:
        """异步获取数据源轻量元信息，不加载表结构和语义配置。"""
        return await self.datasource_dao.get_datasource_meta_async(datasource_id)

    async def get_datasource_meta_by_group_name_async(self, group_name: str) -> Optional[Dict[str, Any]]:
        """异步根据数据源组名称获取轻量元信息，不加载表结构和语义配置。"""
        return await self.datasource_dao.get_datasource_meta_by_group_name_async(group_name)

    def _build_execution_datasource_targets(self, datasource: dict, member_datasource_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """根据数据源配置构造可执行成员数据源。"""
        member_sources = list(datasource.get("member_sources") or [])
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
            org_code = str(member.get("org_code") or "").strip()
            source_config = {
                "host": member.get("host"),
                "port": member.get("port"),
                "database": member.get("database"),
            }
            if query_executor_type == "http":
                if not org_code:
                    raise ValueError("HTTP 执行对象缺少机构编码")
                source_config["org_code"] = org_code
                source_config["org_name"] = str(member.get("org_name") or "").strip()
            elif source_type == "mysql":
                credentials = resolve_mysql_credentials(source_config)
                source_config["user"] = credentials["user"]
                source_config["password"] = credentials["password"]
            elif source_type == "hive" and query_executor_type == "direct":
                credentials = resolve_hive_credentials(source_config)
                if credentials.get("username"):
                    source_config["username"] = credentials["username"]
                if credentials.get("password"):
                    source_config["password"] = credentials["password"]
            result.append({
                "id": member_id,
                "member_datasource_id": member_id,
                "member_name": member.get("org_name") or org_code or f"{member.get('host')}:{member.get('port')}/{member.get('database')}",
                "org_code": org_code,
                "source_type": source_type,
                "query_executor_type": query_executor_type,
                "source_config": source_config,
            })
        return result

    async def get_execution_datasources_async(self, datasource_id: str, member_datasource_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """异步解析逻辑数据源下可执行的成员数据源列表。"""
        datasource = await self._get_datasource_or_raise_async(datasource_id)
        return self._build_execution_datasource_targets(datasource, member_datasource_ids)

    async def get_authorized_execution_datasources_async(
        self,
        datasource_id: str,
        member_datasource_ids: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """异步解析当前用户有机构权限的可执行成员数据源列表。"""
        datasource = await self._get_datasource_or_raise_async(datasource_id)
        allowed_org_codes = await get_allowed_org_codes_by_executor(user_id, username)
        filtered_datasource = filter_datasource_members_by_allowed_org_codes(datasource, allowed_org_codes)
        if not filtered_datasource:
            raise ValueError("当前用户无可用执行对象权限")
        return self._build_execution_datasource_targets(filtered_datasource, member_datasource_ids)

    async def get_datasource_by_group_name_async(self, group_name: str) -> Optional[Dict[str, Any]]:
        """异步根据数据源组名称获取数据源。"""
        datasource = await self.datasource_dao.get_datasource_by_group_name_async(group_name)
        if not datasource:
            return None
        datasource["semantic_configs"] = await self.datasource_dao.get_semantic_configs_by_group_id_async(datasource.get("group_id"))
        self._enrich_semantic_runtime_fields(datasource)
        return datasource

    async def get_table_groups_async(self, datasource_id: str) -> List[Dict[str, Any]]:
        """异步获取数据源内的表分组关系。"""
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        if not datasource:
            return []
        relations = await self.datasource_dao.get_table_group_relations_async(datasource.get("group_id"))
        groups: Dict[str, List[str]] = {}
        for relation in relations:
            group_name = str(relation.get("table_group_name") or "").strip()
            table_name = str(relation.get("table_name") or "").strip()
            if not group_name or not table_name:
                continue
            groups.setdefault(group_name, [])
            if table_name not in groups[group_name]:
                groups[group_name].append(table_name)
        return [{"table_group_name": name, "table_names": table_names} for name, table_names in groups.items()]

    async def replace_table_groups_async(self, datasource_id: str, groups: List[Dict[str, Any]], operator: str = "", operator_name: str = "") -> bool:
        """异步覆盖保存数据源内的表分组关系。"""
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        if not datasource:
            return False
        existing_tables = {str(table.get("table_name") or "").strip() for table in datasource.get("ds_tables") or []}
        normalized_groups: List[Dict[str, Any]] = []
        for group in groups or []:
            group_name = str((group or {}).get("table_group_name") or "").strip()
            if not group_name:
                continue
            table_names: List[str] = []
            for table_name in (group or {}).get("table_names") or []:
                normalized_table_name = str(table_name or "").strip()
                if normalized_table_name and normalized_table_name in existing_tables and normalized_table_name not in table_names:
                    table_names.append(normalized_table_name)
            if table_names:
                normalized_groups.append({"table_group_name": group_name, "table_names": table_names})
        await self.datasource_dao.replace_table_group_relations_async(
            datasource.get("group_id"),
            normalized_groups,
            create_by=operator or "",
            update_by=operator or "",
            create_by_name=operator_name or "",
            update_by_name=operator_name or "",
        )
        return True

    async def update_table_business_desc_async(self, datasource_id: str, table_name: str, business_desc: str, operator: str = "", operator_name: str = "") -> Optional[Dict[str, Any]]:
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

    async def get_column_dict_async(self, datasource_id: str, table_name: str, column_name: str) -> Optional[Dict[str, Any]]:
        """异步获取单个字段的数据字典。"""
        datasource = await self.datasource_dao.get_datasource_async(datasource_id)
        table = self._find_table_column(datasource, table_name, column_name)
        if not datasource or table is None:
            return None
        return await self.datasource_dao.get_column_dict_async(datasource.get("group_id") or "", table_name, column_name)

    async def get_table_column_dicts_async(self, datasource_id: str, table_name: str) -> Optional[Dict[str, Dict[str, Any]]]:
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
        column_dicts = await self.datasource_dao.get_column_dicts_by_table_async(datasource.get("group_id") or "", normalized_table_name)
        return {column_name: column_dict for column_name, column_dict in column_dicts.items() if column_name in column_names}

    async def replace_column_dict_async(
        self,
        datasource_id: str,
        table_name: str,
        column_name: str,
        column_dict: Dict[str, Any],
        operator: str = "",
        operator_name: str = "",
    ) -> Optional[Dict[str, Any]]:
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

    def _find_table_column(self, datasource: Optional[Dict[str, Any]], table_name: str, column_name: str) -> Optional[Dict[str, Any]]:
        """校验数据源缓存中是否存在指定表字段。"""
        normalized_table_name = str(table_name or "").strip()
        normalized_column_name = str(column_name or "").strip()
        if not datasource or not normalized_table_name or not normalized_column_name:
            return None
        for table in datasource.get("ds_tables") or []:
            if str(table.get("table_name") or "").strip() != normalized_table_name:
                continue
            for column in table.get("columns") or []:
                if str(column.get("name") or "").strip() == normalized_column_name:
                    return table
        return None

    async def create_datasource_group_from_sources_async(
        self,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: Dict[str, Any],
        member_source_configs: Optional[List[Dict[str, Any]]] = None,
        query_context: str = "",
        recommended_questions: Optional[List[str]] = None,
        create_by: str = "",
        create_by_name: str = "",
    ) -> Dict[str, Any]:
        """异步按模板源和成员连接信息创建数据源组。"""
        now = datetime.now()
        datasource_id = f"ds_{now.strftime('%Y%m%d%H%M%S')}_{now.microsecond}"
        query_executor_type = str(template_source_config.get("query_executor_type") or "direct").strip().lower() or "direct"
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
            create_by=create_by,
            update_by=create_by,
            create_by_name=create_by_name,
            update_by_name=create_by_name,
        )
        logger.info("创建数据源组: %s - %s, members=%s", datasource_id, group_name, len(member_source_configs_merged))
        return created

    async def update_datasource_group_from_sources_async(
        self,
        datasource_id: str,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: Dict[str, Any],
        member_source_configs: Optional[List[Dict[str, Any]]] = None,
        query_context: str = "",
        recommended_questions: Optional[List[str]] = None,
        update_by: str = "",
        update_by_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        """异步按模板源和成员连接信息更新数据源组。"""
        current = await self.get_datasource_meta_async(datasource_id)
        if not current:
            return None

        query_executor_type = str(template_source_config.get("query_executor_type") or "direct").strip().lower() or "direct"
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
            recommended_questions=recommended_questions if recommended_questions is not None else current.get("recommended_questions", []),
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
        tables: List[Dict[str, Any]],
        update_by: str = "",
        update_by_name: str = "",
    ) -> Optional[Dict[str, Any]]:
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
        table_names: Optional[List[str]] = None,
        update_by: str = "",
        update_by_name: str = "",
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
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
        update_by_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        """异步更新数据源。"""
        current = await self.get_datasource_async(datasource_id)
        if not current:
            return None

        next_template_source = str(current.get("template_source") or current.get("template_datasource_id") or "").strip()
        next_template_source_config: Dict[str, Any] = _get_template_member_source_config(current)
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

        updated = await self._save_datasource_async(
            datasource_id=datasource_id,
            group_name=name if name is not None else current.get("group_name") or datasource_id,
            group_desc=description if description is not None else current.get("ds_description") or "",
            query_context=current.get("query_context") or "",
            recommended_questions=current.get("recommended_questions", []),
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
            create_by_name=current.get("create_by_name") or "",
            update_by=update_by or current.get("update_by") or "",
            update_by_name=update_by_name or current.get("update_by_name") or "",
        )

        if semantic_config is not None:
            logger.warning("单 semantic_config 已废弃，忽略本次 update 请求: datasource_id=%s", datasource_id)
        if semantic_configs is not None:
            logger.warning("update_datasource 不再直接处理 semantic_configs，请使用语义配置接口: datasource_id=%s", datasource_id)

        logger.info("更新数据源: %s", datasource_id)
        return updated

    async def delete_datasource_async(self, datasource_id: str, force: bool = False, update_by: str = "", update_by_name: str = "") -> bool:
        """异步删除数据源。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            return False

        await self.datasource_dao.soft_delete_group_async(datasource.get("group_id") or datasource_id, update_by=update_by or "", update_by_name=update_by_name or "")
        logger.info("删除数据源: %s, force=%s", datasource_id, force)
        return True

    async def get_semantic_configs_async(self, datasource_id: str, user_id: str = "") -> List[dict]:
        """异步获取数据源的所有语义配置。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            return []
        configs = datasource.get("semantic_configs") or []
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return configs
        return [
            config for config in configs
            if config.get("scope_type", "public") == "public"
            or (
                config.get("scope_type") == "private"
                and str(config.get("create_by") or "").strip() == normalized_user_id
            )
        ]

    async def get_runtime_semantic_configs_async(self, datasource_id: str, user_id: str = "") -> List[dict]:
        """异步获取智能问数运行时语义配置列表，并临时合并物理字段字典。"""
        configs = await self.get_semantic_configs_async(datasource_id, user_id)
        if not configs:
            return []
        try:
            datasource = await self.datasource_dao.get_datasource_async(datasource_id)
            group_id = str((datasource or {}).get("group_id") or "").strip()
            if not group_id:
                return [self._normalize_runtime_semantic_config(config) for config in configs]
            column_dicts = await self.datasource_dao.get_column_dicts_by_group_id_async(group_id)
        except AttributeError:
            column_dicts = {}
        except Exception as e:
            logger.warning("读取字段数据字典失败，指标集选择降级使用原始配置: datasource_id=%s error=%s", datasource_id, e)
            column_dicts = {}
        if not column_dicts:
            return [self._normalize_runtime_semantic_config(config) for config in configs]
        return [self._merge_column_dict_into_semantic_config(config, column_dicts) for config in configs]

    async def get_semantic_config_async(self, datasource_id: str, config_id: str, user_id: str = "") -> Optional[dict]:
        """异步获取指定的语义配置。"""
        configs = await self.get_semantic_configs_async(datasource_id, user_id)
        for config in configs:
            if config.get("id") == config_id or config.get("semantic_config_id") == config_id:
                return config
        return None

    async def get_runtime_semantic_config_async(self, datasource_id: str, config_id: str, user_id: str = "") -> Optional[dict]:
        """异步获取智能问数运行时语义配置，并临时合并物理字段字典。"""
        config = await self.get_semantic_config_async(datasource_id, config_id, user_id)
        if not config:
            return None
        return await self._enrich_semantic_config_column_dict_async(datasource_id, config)

    async def _semantic_config_name_exists_async(self, datasource_id: str, name: str, exclude_config_id: Optional[str] = None, scope_type: str = "public", create_by: str = "") -> bool:
        """异步检查当前可见范围内是否已有同名未删除语义配置。"""
        normalized_name = str(name or "").strip()
        normalized_exclude_id = str(exclude_config_id or "").strip()
        normalized_scope_type = str(scope_type or "public").strip().lower()
        normalized_create_by = str(create_by or "").strip()
        if not normalized_name:
            return False
        for config in await self.get_semantic_configs_async(datasource_id):
            config_id = str(config.get("id") or config.get("semantic_config_id") or "").strip()
            if normalized_exclude_id and config_id == normalized_exclude_id:
                continue
            config_name = str(config.get("name") or config.get("semantic_config_name") or "").strip()
            if config_name != normalized_name:
                continue
            config_scope_type = str(config.get("scope_type") or "public").strip().lower()
            config_create_by = str(config.get("create_by") or "").strip()
            if normalized_scope_type == "private":
                if config_scope_type == "public" or (config_scope_type == "private" and config_create_by == normalized_create_by):
                    return True
            elif config_scope_type == "public" or config_name == normalized_name:
                return True
        return False

    async def create_semantic_config_async(
        self,
        datasource_id: str,
        name: str,
        description: Optional[str] = None,
        ontology_rules: Optional[str] = None,
        enabled: bool = True,
        metrics: Optional[List[dict]] = None,
        dimensions: Optional[List[dict]] = None,
        relationships: Optional[List[dict]] = None,
        scope_type: str = "private",
        create_by: str = "",
        create_by_name: str = "",
    ) -> Optional[dict]:
        """异步创建语义配置。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            return None

        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("语义配置名称不能为空")
        normalized_scope_type = str(scope_type or "private").strip().lower()
        if normalized_scope_type not in {"public", "private"}:
            raise ValueError("语义配置范围只能是 public 或 private")
        operator = str(create_by or "").strip()
        if await self._semantic_config_name_exists_async(datasource_id, normalized_name, scope_type=normalized_scope_type, create_by=operator):
            raise ValueError(f"语义配置名称已存在: {normalized_name}")

        now = datetime.now()
        config_id = f"sc_{now.strftime('%Y%m%d%H%M%S')}_{now.microsecond}"
        now_str = _format_time(now)
        config = {
            "id": config_id,
            "group_id": datasource.get("group_id"),
            "name": normalized_name,
            "description": description or "",
            "ontology_rules": ontology_rules or "",
            "enabled": enabled,
            "metrics": metrics or [],
            "dimensions": dimensions or [],
            "relationships": relationships or [],
            "scope_type": normalized_scope_type,
            "create_time": now_str,
            "update_time": now_str,
            "create_by": operator,
            "create_by_name": create_by_name or "",
            "update_by": operator,
            "update_by_name": create_by_name or "",
        }
        self._normalize_semantic_relationships({"semantic_configs": [config]})
        await self.datasource_dao.upsert_semantic_config_async(datasource_id, config)

        logger.info("创建语义配置: %s - %s", config_id, name)
        return await self.get_semantic_config_async(datasource_id, config_id)

    async def update_semantic_config_async(
        self,
        datasource_id: str,
        config_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        ontology_rules: Optional[str] = None,
        enabled: Optional[bool] = None,
        metrics: Optional[List[dict]] = None,
        dimensions: Optional[List[dict]] = None,
        relationships: Optional[List[dict]] = None,
        update_by: str = "",
        update_by_name: str = "",
    ) -> Optional[dict]:
        """异步更新语义配置。"""
        current = await self.get_semantic_config_async(datasource_id, config_id)
        if not current:
            return None

        next_name = str(name).strip() if name is not None else str(current.get("name") or "").strip()
        if not next_name:
            raise ValueError("语义配置名称不能为空")
        if await self._semantic_config_name_exists_async(datasource_id, next_name, exclude_config_id=config_id, scope_type=current.get("scope_type", "public"), create_by=current.get("create_by", "")):
            raise ValueError(f"语义配置名称已存在: {next_name}")

        config = {
            **current,
            "name": next_name,
            "description": description if description is not None else current.get("description"),
            "ontology_rules": ontology_rules if ontology_rules is not None else current.get("ontology_rules"),
            "enabled": enabled if enabled is not None else current.get("enabled", True),
            "metrics": metrics if metrics is not None else current.get("metrics", []),
            "dimensions": dimensions if dimensions is not None else current.get("dimensions", []),
            "relationships": relationships if relationships is not None else current.get("relationships", []),
            "update_time": _format_time(),
            "update_by": str(update_by or current.get("update_by") or "").strip(),
            "update_by_name": str(update_by_name or current.get("update_by_name") or "").strip(),
        }
        self._normalize_semantic_relationships({"semantic_configs": [config]})
        await self.datasource_dao.upsert_semantic_config_async(datasource_id, config)

        logger.info("更新语义配置: %s", config_id)
        return await self.get_semantic_config_async(datasource_id, config_id)

    async def delete_semantic_config_async(self, datasource_id: str, config_id: str, update_by: str = "", update_by_name: str = "") -> bool:
        """异步删除语义配置。"""
        config = await self.get_semantic_config_async(datasource_id, config_id)
        if not config:
            return False
        await self.datasource_dao.soft_delete_semantic_config_async(config_id, update_by=update_by or config.get("update_by") or "", update_by_name=update_by_name or config.get("update_by_name") or "")
        logger.info("删除语义配置: %s", config_id)
        return True

    async def set_default_datasource_async(self, datasource_id: str, update_by: str = "", update_by_name: str = "") -> bool:
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

    async def get_default_datasource_async(self) -> Optional[Dict[str, Any]]:
        """异步获取默认数据源。"""
        datasource = await self.datasource_dao.get_default_datasource_async()
        if not datasource:
            return None
        datasource["semantic_configs"] = await self.datasource_dao.get_semantic_configs_by_group_id_async(datasource.get("group_id"))
        return datasource

    async def clear_default_datasource_async(self, datasource_id: str, update_by: str = "", update_by_name: str = "") -> bool:
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
        table_names: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """从业务库缓存读取数据源表结构。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            raise ValueError(f"数据源不存在: {datasource_id}")
        tables = datasource.get("ds_tables", []) or []
        if table_names:
            selected_names = {str(table_name or "").strip() for table_name in table_names if str(table_name or "").strip()}
            tables = [table for table in tables if str(table.get("table_name") or "").strip() in selected_names]
        return tables

    async def get_live_schema_async(
        self,
        datasource_id: str,
        table_names: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """从模板物理源实时读取数据源表结构。"""
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            raise ValueError(f"数据源不存在: {datasource_id}")

        source_type = datasource.get("source_type")
        source_config = self._build_physical_metadata_source_config(datasource)

        try:
            return await self._load_physical_schema_async(
                source_type,
                source_config,
                table_names,
                user_id=user_id,
                username=username,
            )
        except asyncio.TimeoutError as err:
            logger.error("异步获取表结构超时: datasource_id=%s source_type=%s timeout=%ss", datasource_id, source_type, DATASOURCE_SYNC_TIMEOUT_SECONDS)
            raise ConnectionError(f"数据库连接超时（{DATASOURCE_SYNC_TIMEOUT_SECONDS}秒）") from err
        except ValueError:
            raise
        except Exception as err:
            logger.error("异步获取表结构失败: %s", err)
            raise ConnectionError(f"数据库连接失败: {str(err)}")

    async def get_physical_table_names_async(
        self,
        source_type: str,
        source_config: Dict[str, Any],
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> List[Any]:
        """从物理源实时读取表名列表。"""
        try:
            return await self._load_physical_table_names_async(
                source_type,
                source_config,
                user_id=user_id,
                username=username,
            )
        except asyncio.TimeoutError:
            logger.error("获取表列表超时: source_type=%s timeout=%ss", source_type, DATASOURCE_SYNC_TIMEOUT_SECONDS)
            raise
        except ValueError:
            raise
        except Exception as err:
            logger.error("获取表列表失败: %s", err, exc_info=True)
            raise ConnectionError(f"数据库连接失败: {str(err)}")


_datasource_manager_instance: Optional[DataSourceManager] = None


def get_datasource_manager() -> DataSourceManager:
    """获取数据源管理器单例。"""
    global _datasource_manager_instance
    if _datasource_manager_instance is None:
        _datasource_manager_instance = DataSourceManager()
    return _datasource_manager_instance
