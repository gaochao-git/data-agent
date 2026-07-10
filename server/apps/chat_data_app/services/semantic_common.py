"""Shared semantic helpers."""

from __future__ import annotations

from typing import Any, Dict, Set

AGENT_ID = "chat_data"
SQL_ASSIST_AGENT_ID = "chat_data_sql_assist"
DATA_ANALYZE_AGENT_ID = "chat_data_data_analyze"
LOGICAL_TYPE_VALUES = {"text", "number", "date", "datetime", "enum"}


def _build_table_columns_map(datasource: Dict) -> Dict[str, Set[str]]:
    """构建数据源表字段索引。"""
    table_columns: Dict[str, Set[str]] = {}
    for table in datasource.get("ds_tables", []) or []:
        table_name = table.get("table_name")
        if not table_name:
            continue
        columns = table.get("columns", []) or []
        column_names = {col.get("name") for col in columns if col.get("name")}
        table_columns[table_name] = column_names
    return table_columns


def _normalize_logical_type(value: Any) -> str:
    """Normalize dimension logical type, returning empty string for unsupported values."""
    logical_type = str(value or "").strip().lower()
    return logical_type if logical_type in LOGICAL_TYPE_VALUES else ""


def ensure_dimension_logical_type(dimension: Dict[str, Any]) -> Dict[str, Any]:
    """Return a dimension copy with logical_type filled for runtime use only."""
    next_dimension = dict(dimension or {})
    logical_type = _normalize_logical_type(next_dimension.get("logical_type"))
    next_dimension["logical_type"] = logical_type or "text"
    next_dimension.pop("type", None)
    return next_dimension


def is_time_logical_dimension(config: Dict[str, Any]) -> bool:
    """Return whether a semantic dimension should be handled as date/datetime."""
    return _normalize_logical_type((config or {}).get("logical_type")) in {"date", "datetime"}


def is_number_logical_dimension(config: Dict[str, Any]) -> bool:
    """Return whether a semantic dimension should be handled as number."""
    return _normalize_logical_type((config or {}).get("logical_type")) == "number"


def _semantic_config_prompt_payload(config: Dict[str, Any]) -> Dict[str, Any]:
    """裁剪语义配置上下文，避免提示词过长。"""
    return {
        "name": config.get("name"),
        "description": config.get("description"),
        "ontology_rules": config.get("ontology_rules"),
        "metrics": [
            {
                "name": item.get("name"),
                "field_name": item.get("field_name"),
                "expression": item.get("expression"),
                "table": item.get("table"),
                "description": item.get("description"),
            }
            for item in (config.get("metrics") or [])[:30]
        ],
        "dimensions": [
            {
                "name": dimension.get("name"),
                "field_name": dimension.get("field_name"),
                "column": dimension.get("column"),
                "table": dimension.get("table"),
                "logical_type": dimension.get("logical_type"),
                "description": dimension.get("description"),
                "allowed_values": dimension.get("allowed_values"),
                "mapping": dimension.get("mapping"),
            }
            for dimension in [ensure_dimension_logical_type(item) for item in (config.get("dimensions") or [])[:40]]
        ],
        "relationships": (config.get("relationships") or [])[:20],
    }


def _semantic_config_scope_type(config: Dict[str, Any]) -> str:
    """读取指标集范围，缺省按公共指标集处理。"""
    return str((config or {}).get("scope_type") or "public").strip().lower()


def _is_public_semantic_config(config: Dict[str, Any]) -> bool:
    """判断是否为系统公共指标集。"""
    return bool(config and _semantic_config_scope_type(config) == "public")


def _is_private_semantic_config_owner(config: Dict[str, Any], user_id: str) -> bool:
    """判断当前用户是否拥有该个人指标集。"""
    return bool(
        config
        and _semantic_config_scope_type(config) == "private"
        and str((config or {}).get("create_by") or "").strip() == str(user_id or "").strip()
    )


def _can_mutate_semantic_config(config: Dict[str, Any], user_id: str, admin_manage: bool = False) -> bool:
    """判断当前入口是否允许修改指标集。"""
    return _is_public_semantic_config(config) if admin_manage else _is_private_semantic_config_owner(config, user_id)
