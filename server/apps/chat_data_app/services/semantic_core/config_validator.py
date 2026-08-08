"""语义模型配置的纯结构校验。"""

import re
from typing import Any, Dict, List, Optional, Set

from .common import build_table_columns_map
from .input_validator import FILTER_OPERATORS


def normalize_semantic_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("`", "")


def expression_uses_physical_column(expression: str, table_columns: Set[str]) -> bool:
    if not expression:
        return False
    expression_without_quotes = expression.replace("`", "")
    for column in table_columns:
        escaped_column = re.escape(column)
        pattern = rf"(^|[^A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\.)?{escaped_column}([^A-Za-z0-9_]|$)"
        if re.search(pattern, expression_without_quotes, flags=re.IGNORECASE):
            return True
    return False


def validate_semantic_fields(
    datasource: Dict,
    metrics: List[Dict],
    dimensions: List[Dict],
    relationships: Optional[List[Dict]] = None,
) -> Optional[str]:
    table_columns_map = build_table_columns_map(datasource)
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

    for dimension in dimensions or []:
        table_name = dimension.get("table")
        column_name = dimension.get("column")
        if not table_name or table_name not in table_columns_map:
            return f"维度 {dimension.get('name', '')} 所属表不存在: {table_name}"
        if not column_name or column_name not in table_columns_map[table_name]:
            return f"维度 {dimension.get('name', '')} 字段不存在: {table_name}.{column_name}"
        allowed_operators_error = _validate_allowed_operators(dimension)
        if allowed_operators_error:
            return allowed_operators_error

    for metric in metrics or []:
        table_name = metric.get("table")
        expression = str(metric.get("expression") or "").strip()
        if not table_name or table_name not in table_columns_map:
            return f"指标 {metric.get('name', '')} 所属表不存在: {table_name}"
        if not expression_uses_physical_column(expression, table_columns_map[table_name]):
            return f"指标 {metric.get('name', '')} 表达式必须引用表 {table_name} 的物理字段"
    return validate_semantic_relationships(
        table_columns_map,
        metrics or [],
        dimensions or [],
        relationships or [],
    )


def _validate_allowed_operators(dimension: Dict[str, Any]) -> Optional[str]:
    configured = dimension.get("allowed_operators")
    if configured is None:
        return None
    if not isinstance(configured, list):
        return f"维度 {dimension.get('name', '')} 的 allowed_operators 必须是数组"
    for operator in configured:
        normalized = str(operator or "").strip().upper()
        if normalized not in FILTER_OPERATORS:
            return f"维度 {dimension.get('name', '')} 配置了不支持的过滤操作符: {operator}"
    return None


def _semantic_alias(item: Dict[str, Any]) -> str:
    return str(item.get("table_alias") or item.get("table") or "").strip()


def _relationship_alias(relationship: Dict[str, Any], side: str) -> str:
    return str(
        relationship.get(f"{side}_alias")
        or relationship.get(f"{side}_table")
        or ""
    ).strip()


def _relationship_conditions(relationship: Dict[str, Any]) -> List[Dict[str, str]]:
    conditions = relationship.get("join_conditions") or []
    if conditions:
        return conditions
    if relationship.get("left_column") or relationship.get("right_column"):
        return [{
            "left_column": relationship.get("left_column"),
            "right_column": relationship.get("right_column"),
        }]
    return []


def _register_alias(alias_tables: Dict[str, str], alias: str, table: str) -> Optional[str]:
    existing = alias_tables.get(alias)
    if existing and existing != table:
        return f"语义表别名 {alias} 同时指向了 {existing} 和 {table}"
    alias_tables[alias] = table
    return None


def validate_semantic_relationships(
    table_columns_map: Dict[str, Set[str]],
    metrics: List[Dict],
    dimensions: List[Dict],
    relationships: List[Dict],
) -> Optional[str]:
    """Validate relationship endpoints, aliases, columns and field connectivity."""
    alias_tables: Dict[str, str] = {}
    field_aliases: Set[str] = set()
    for item in [*dimensions, *metrics]:
        table = str((item or {}).get("table") or "").strip()
        alias = _semantic_alias(item or {})
        if table and alias:
            error = _register_alias(alias_tables, alias, table)
            if error:
                return error
            field_aliases.add(alias)

    adjacency: Dict[str, Set[str]] = {}
    for index, relationship in enumerate(relationships):
        left_table = str((relationship or {}).get("left_table") or "").strip()
        right_table = str((relationship or {}).get("right_table") or "").strip()
        left_alias = _relationship_alias(relationship or {}, "left")
        right_alias = _relationship_alias(relationship or {}, "right")
        label = f"关系[{index + 1}]"
        for side_name, table, alias in (
            ("左表", left_table, left_alias),
            ("右表", right_table, right_alias),
        ):
            if not table or table not in table_columns_map:
                return f"{label}{side_name}不存在: {table}"
            if not alias:
                return f"{label}{side_name}别名不能为空"
            error = _register_alias(alias_tables, alias, table)
            if error:
                return error
        if left_alias == right_alias:
            return f"{label}左右表别名不能相同: {left_alias}"

        conditions = _relationship_conditions(relationship or {})
        if not conditions:
            return f"{label}缺少关联条件"
        for condition_index, condition in enumerate(conditions):
            left_column = str((condition or {}).get("left_column") or "").strip()
            right_column = str((condition or {}).get("right_column") or "").strip()
            condition_label = f"{label}关联条件[{condition_index + 1}]"
            if not left_column or not right_column:
                return f"{condition_label}左右字段不能为空"
            if left_column not in table_columns_map[left_table]:
                return f"{condition_label}左字段不存在: {left_table}.{left_column}"
            if right_column not in table_columns_map[right_table]:
                return f"{condition_label}右字段不存在: {right_table}.{right_column}"
        adjacency.setdefault(left_alias, set()).add(right_alias)
        adjacency.setdefault(right_alias, set()).add(left_alias)

    if len(field_aliases) <= 1:
        return None
    start = next(iter(field_aliases))
    reachable = {start}
    pending = [start]
    while pending:
        current = pending.pop()
        for neighbor in adjacency.get(current, set()):
            if neighbor not in reachable:
                reachable.add(neighbor)
                pending.append(neighbor)
    disconnected = sorted(field_aliases - reachable)
    if disconnected:
        return f"语义字段所在表缺少关联路径: {'、'.join(disconnected)}"
    return None
