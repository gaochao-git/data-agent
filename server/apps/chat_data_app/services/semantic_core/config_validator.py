"""语义模型配置的纯结构校验。"""

import re
from typing import Any, Dict, List, Optional, Set

from .common import build_table_columns_map


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

    for metric in metrics or []:
        table_name = metric.get("table")
        expression = str(metric.get("expression") or "").strip()
        if not table_name or table_name not in table_columns_map:
            return f"指标 {metric.get('name', '')} 所属表不存在: {table_name}"
        if not expression_uses_physical_column(expression, table_columns_map[table_name]):
            return f"指标 {metric.get('name', '')} 表达式必须引用表 {table_name} 的物理字段"
    return None
