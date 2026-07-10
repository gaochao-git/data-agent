"""Semantic config save-time validation."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from .semantic_common import _build_table_columns_map


def normalize_semantic_text(value: Any) -> str:
    """Normalize semantic field names for duplicate checks."""
    return str(value or "").strip().lower().replace("`", "")


def expression_uses_physical_column(expression: str, table_columns: Set[str]) -> bool:
    """Return whether an expression references at least one physical column."""
    if not expression:
        return False
    expr = expression.replace("`", "")
    for column in table_columns:
        escaped_col = re.escape(column)
        pattern = rf"(^|[^A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\.)?{escaped_col}([^A-Za-z0-9_]|$)"
        if re.search(pattern, expr, flags=re.IGNORECASE):
            return True
    return False


def validate_semantic_fields(datasource: Dict, metrics: List[Dict], dimensions: List[Dict]) -> Optional[str]:
    """Validate semantic fields against datasource physical schema."""
    table_columns_map = _build_table_columns_map(datasource)
    if not table_columns_map:
        return "当前数据源缺少表结构信息，请先完成数据源同步"

    seen_names: Set[str] = set()
    for item in [*(dimensions or []), *(metrics or [])]:
        display_name = str((item or {}).get("name") or "").strip()
        normalized_name = normalize_semantic_text(display_name)
        if not normalized_name:
            return "字段显示名称不能为空"
        if normalized_name in seen_names:
            return f"字段显示名称重复: {display_name}"
        seen_names.add(normalized_name)

    for dim in dimensions or []:
        table_name = dim.get("table")
        column_name = dim.get("column")
        if not table_name or table_name not in table_columns_map:
            return f"维度 {dim.get('name', '')} 所属表不存在: {table_name}"
        if not column_name or column_name not in table_columns_map[table_name]:
            return f"维度 {dim.get('name', '')} 字段不存在: {table_name}.{column_name}"

    for metric in metrics or []:
        table_name = metric.get("table")
        expression = (metric.get("expression") or "").strip()
        if not table_name or table_name not in table_columns_map:
            return f"指标 {metric.get('name', '')} 所属表不存在: {table_name}"
        if not expression_uses_physical_column(expression, table_columns_map[table_name]):
            return f"指标 {metric.get('name', '')} 表达式必须引用表 {table_name} 的物理字段"

    return None


async def validate_semantic_logical_types(ds_manager: Any, datasource_id: str, dimensions: List[Dict]) -> Optional[str]:
    """Validate save-time logical type dependencies."""
    for dim in dimensions or []:
        if str((dim or {}).get("logical_type") or "").strip().lower() != "enum":
            continue
        table_name = str((dim or {}).get("table") or "").strip()
        column_name = str((dim or {}).get("column") or "").strip()
        column_dict = await ds_manager.get_column_dict_async(datasource_id, table_name, column_name)
        if not column_dict_has_enum_mapping(column_dict):
            return f"维度 {dim.get('name', '')} 选择了枚举逻辑类型，请先维护字段字典枚举值: {table_name}.{column_name}"
    return None


def column_dict_has_enum_mapping(column_dict: Optional[Dict[str, Any]]) -> bool:
    enum_values = (column_dict or {}).get("enum_values") if isinstance(column_dict, dict) else []
    if not isinstance(enum_values, list):
        return False
    return any(str((item or {}).get("value") or "").strip() for item in enum_values if isinstance(item, dict))


async def validate_semantic_config_for_save(
    ds_manager: Any,
    datasource_id: str,
    datasource: Dict,
    metrics: List[Dict],
    dimensions: List[Dict],
) -> Optional[str]:
    """Validate semantic config before create/update persistence."""
    validation_error = validate_semantic_fields(datasource, metrics, dimensions)
    if validation_error:
        return validation_error
    return await validate_semantic_logical_types(ds_manager, datasource_id, dimensions)
