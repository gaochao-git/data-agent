"""语义 SQL 的 SELECT 字段编译。"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .filter_compiler import _effective_table_ref, _physical_table_ref


@dataclass(frozen=True)
class SelectCompilation:
    """SELECT 片段以及后续分组、过滤和排序需要的字段映射。"""

    fields: List[str]
    dimension_expr_map: Dict[Tuple[str, str], str]
    group_expr_map: Dict[Tuple[str, str], str]
    dimension_alias_map: Dict[Tuple[str, str], str]


class _AliasAllocator:
    """分配不重复的 SELECT 别名，保持 Builder 字段兼容。"""

    def __init__(self, to_sql_alias: Callable[[str, str], str]):
        self._to_sql_alias = to_sql_alias
        self._used: set[str] = set()

    def allocate(self, name: str, fallback: str) -> str:
        base = self._to_sql_alias(name, fallback)
        if base not in self._used:
            self._used.add(base)
            return base
        index = 2
        while f"{base}_{index}" in self._used:
            index += 1
        alias = f"{base}_{index}"
        self._used.add(alias)
        return alias


def _compile_dimension_field(
    dimension: Dict[str, Any],
    *,
    main_alias: str,
    is_multi_table: bool,
    dialect: Any,
    quote_identifier: Callable[[str], str],
    normalize_raw_expression: Callable[..., str],
    allocator: _AliasAllocator,
    fields: List[str],
    dimension_expr_map: Dict[Tuple[str, str], str],
    group_expr_map: Dict[Tuple[str, str], str],
    dimension_alias_map: Dict[Tuple[str, str], str],
) -> None:
    """编译单个维度字段；transform 非 raw 时按日期变换展开。"""
    table = _effective_table_ref(dimension, fallback=main_alias)
    physical_table = _physical_table_ref(dimension, fallback=table)
    column = dimension.get("column", "")
    transform = dimension.get("transform") or "raw"
    display_name = dimension.get("displayName") or dimension.get("alias") or column
    expression = normalize_raw_expression(
        dimension.get("expression"), table, column, is_multi_table, physical_table
    )
    dimension_alias = allocator.allocate(display_name, column or "col")

    if transform != "raw":
        transformed = dialect.format_date(expression, transform)
        transformed_alias = allocator.allocate(
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


def _aggregate_function_value(
    function: str,
    column: str,
    column_expression: str,
) -> str:
    if function == "COUNT_DISTINCT":
        return "COUNT(*)" if column == "*" else f"COUNT(DISTINCT {column_expression})"
    if function == "RAW":
        return "*" if column == "*" else f"{column_expression}"
    if function == "COUNT":
        return f"COUNT({'*' if column == '*' else column_expression})"
    return f"{function}({column_expression})"


def _metric_display_name(aggregation: Dict[str, Any], column: str) -> str:
    return aggregation.get("displayName") or aggregation.get("alias") or (
        "all" if column == "*" else column
    )


def _metric_fallback_alias(column: str) -> str:
    return "all" if column == "*" else (column or "metric")


def _compile_aggregation_field(
    aggregation: Dict[str, Any],
    *,
    main_alias: str,
    is_multi_table: bool,
    quote_identifier: Callable[[str], str],
    normalize_raw_expression: Callable[..., str],
    normalize_aggregate_expression: Callable[..., str],
    get_column_expr: Callable[..., str],
    is_aggregate_expression: Callable[[Optional[str]], bool],
    allocator: _AliasAllocator,
    fields: List[str],
) -> None:
    """编译单个指标字段，落入 SELECT 列表。"""
    table = _effective_table_ref(aggregation, fallback=main_alias)
    physical_table = _physical_table_ref(aggregation, fallback=table)
    column = aggregation.get("column", "")
    function = (aggregation.get("func") or "RAW").upper()
    expression = aggregation.get("expression")
    alias = allocator.allocate(_metric_display_name(aggregation, column), _metric_fallback_alias(column))

    if function == "RAW" and (expression or "").strip():
        normalized = normalize_raw_expression(
            expression, table, column, is_multi_table, physical_table
        )
        fields.append(f"{normalized} AS {quote_identifier(alias)}")
        return
    if function != "RAW" and (expression or "").strip() and is_aggregate_expression(expression):
        normalized = normalize_aggregate_expression(
            expression, table, column, is_multi_table, physical_table
        )
        fields.append(f"{normalized} AS {quote_identifier(alias)}")
        return

    column_expression = "*" if column == "*" else get_column_expr(table, column, is_multi_table)
    value = _aggregate_function_value(function, column, column_expression)
    fields.append(value if value == "*" else f"{value} AS {quote_identifier(alias)}")


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
    allocator = _AliasAllocator(to_sql_alias)
    fields: List[str] = []
    dimension_expr_map: Dict[Tuple[str, str], str] = {}
    group_expr_map: Dict[Tuple[str, str], str] = {}
    dimension_alias_map: Dict[Tuple[str, str], str] = {}

    for dimension in dimensions:
        _compile_dimension_field(
            dimension,
            main_alias=main_alias,
            is_multi_table=is_multi_table,
            dialect=dialect,
            quote_identifier=quote_identifier,
            normalize_raw_expression=normalize_raw_expression,
            allocator=allocator,
            fields=fields,
            dimension_expr_map=dimension_expr_map,
            group_expr_map=group_expr_map,
            dimension_alias_map=dimension_alias_map,
        )

    for aggregation in aggregations:
        _compile_aggregation_field(
            aggregation,
            main_alias=main_alias,
            is_multi_table=is_multi_table,
            quote_identifier=quote_identifier,
            normalize_raw_expression=normalize_raw_expression,
            normalize_aggregate_expression=normalize_aggregate_expression,
            get_column_expr=get_column_expr,
            is_aggregate_expression=is_aggregate_expression,
            allocator=allocator,
            fields=fields,
        )

    return SelectCompilation(fields, dimension_expr_map, group_expr_map, dimension_alias_map)




