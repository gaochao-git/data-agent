"""语义 SQL 的 SELECT 字段编译。"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SelectCompilation:
    """SELECT 片段以及后续分组、过滤和排序需要的字段映射。"""

    fields: List[str]
    dimension_expr_map: Dict[Tuple[str, str], str]
    group_expr_map: Dict[Tuple[str, str], str]
    dimension_alias_map: Dict[Tuple[str, str], str]


def compile_select_fields(
    *,
    dimensions: List[Dict[str, Any]],
    aggregations: List[Dict[str, Any]],
    main_alias: str,
    is_multi_table: bool,
    dialect: Any,
    quote_identifier: Callable[[str], str],
    to_sql_alias: Callable[[str, str], str],
    normalize_raw_expression: Callable[..., str],
    normalize_aggregate_expression: Callable[..., str],
    get_column_expr: Callable[..., str],
    is_aggregate_expression: Callable[[Optional[str]], bool],
) -> SelectCompilation:
    """编译维度和指标，保持 Builder 字段兼容。"""
    used_aliases: set[str] = set()

    def get_unique_alias(name: str, fallback: str) -> str:
        base = to_sql_alias(name, fallback)
        if base not in used_aliases:
            used_aliases.add(base)
            return base
        index = 2
        while f"{base}_{index}" in used_aliases:
            index += 1
        alias = f"{base}_{index}"
        used_aliases.add(alias)
        return alias

    fields: List[str] = []
    dimension_expr_map: Dict[Tuple[str, str], str] = {}
    group_expr_map: Dict[Tuple[str, str], str] = {}
    dimension_alias_map: Dict[Tuple[str, str], str] = {}

    for dimension in dimensions:
        table = _effective_table_ref(dimension, main_alias)
        physical_table = _physical_table_ref(dimension, table)
        column = dimension.get("column", "")
        transform = dimension.get("transform") or "raw"
        display_name = dimension.get("displayName") or dimension.get("alias") or column
        expression = normalize_raw_expression(
            dimension.get("expression"), table, column, is_multi_table, physical_table
        )
        dimension_alias = get_unique_alias(display_name, column or "col")

        if transform != "raw":
            transformed = dialect.format_date(expression, transform)
            transformed_alias = get_unique_alias(
                f"{dimension_alias}_{transform}", f"{column}_{transform}"
            )
            fields.append(f"{transformed} AS {quote_identifier(transformed_alias)}")
            dimension_expr_map[(table, column)] = expression
            group_expr_map[(table, column)] = transformed
            dimension_alias_map[(table, column)] = transformed_alias
        else:
            fields.append(f"{expression} AS {quote_identifier(dimension_alias)}")
            dimension_expr_map[(table, column)] = expression
            group_expr_map[(table, column)] = expression
            dimension_alias_map[(table, column)] = dimension_alias

    for aggregation in aggregations:
        table = _effective_table_ref(aggregation, main_alias)
        physical_table = _physical_table_ref(aggregation, table)
        column = aggregation.get("column", "")
        function = (aggregation.get("func") or "RAW").upper()
        expression = aggregation.get("expression")
        display_name = aggregation.get("displayName") or aggregation.get("alias") or (
            "all" if column == "*" else column
        )
        alias = get_unique_alias(display_name, "all" if column == "*" else (column or "metric"))

        if function == "RAW" and (expression or "").strip():
            normalized = normalize_raw_expression(
                expression, table, column, is_multi_table, physical_table
            )
            fields.append(f"{normalized} AS {quote_identifier(alias)}")
            continue
        if function != "RAW" and (expression or "").strip() and is_aggregate_expression(expression):
            normalized = normalize_aggregate_expression(
                expression, table, column, is_multi_table, physical_table
            )
            fields.append(f"{normalized} AS {quote_identifier(alias)}")
            continue

        column_expression = "*" if column == "*" else get_column_expr(table, column, is_multi_table)
        if function == "COUNT_DISTINCT":
            value = "COUNT(*)" if column == "*" else f"COUNT(DISTINCT {column_expression})"
        elif function == "RAW":
            value = "*" if column == "*" else f"{column_expression}"
        elif function == "COUNT":
            value = f"COUNT({'*' if column == '*' else column_expression})"
        else:
            value = f"{function}({column_expression})"
        fields.append(value if value == "*" else f"{value} AS {quote_identifier(alias)}")

    return SelectCompilation(fields, dimension_expr_map, group_expr_map, dimension_alias_map)


def _effective_table_ref(item: Dict[str, Any], fallback: str) -> str:
    return str(item.get("tableAlias") or item.get("table") or fallback or "").strip()


def _physical_table_ref(item: Dict[str, Any], fallback: str) -> str:
    return str(item.get("table") or fallback or "").strip()
