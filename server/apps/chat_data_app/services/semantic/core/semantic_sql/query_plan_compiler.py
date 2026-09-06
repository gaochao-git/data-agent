"""标准化查询计划到 SQL 的编译实现。"""

import re
from contextvars import ContextVar
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from .dialect import get_dialect
from . import filter_compiler
from .join_compiler import build_join_clauses
from .row_limit import normalize_row_limit
from .select_compiler import compile_select_fields
from .filter_compiler import (
    build_filter_tree_clause as compile_filter_tree_clause,
    build_having_leaf_clause as compile_having_leaf_clause,
    build_where_leaf_clause as compile_where_leaf_clause,
    _has_balanced_parentheses,
    _is_numeric_literal,
    _is_sql_function,
    _sql_list_literal,
    _sql_literal,
    ensure_safe_sql_expression,
    _value_type_for_index,
)

AGG_EXPR_PATTERN = re.compile(r"^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$", re.IGNORECASE)
SQL_SAFE_FUNCTIONS = {
    "current_date", "current_timestamp", "now", "curdate", "curtime",
    "date_sub", "date_add", "datediff", "date_format", "date",
    "year", "month", "day", "hour", "minute", "second",
    "quarter", "week", "dayofweek", "dayofyear", "dayofmonth",
    "to_date", "from_unixtime", "unix_timestamp", "add_months",
    "concat", "substring", "substr", "length", "char_length",
    "upper", "lower", "trim", "ltrim", "rtrim",
    "replace", "reverse", "left", "right",
    "instr", "locate", "lpad", "rpad", "repeat",
    "abs", "round", "ceil", "ceiling", "floor", "mod",
    "pow", "power", "sqrt", "rand",
    "count", "sum", "avg", "max", "min",
    "if", "ifnull", "nullif", "coalesce", "nvl",
    "case", "when", "then", "else", "end",
}


_DIALECT_CONTEXT: ContextVar[Optional[str]] = ContextVar(
    "semantic_builder_sql_dialect",
    default=None,
)


def _quote_identifier(name: str) -> str:
    dialect_name = _DIALECT_CONTEXT.get() or "mysql"
    return get_dialect(dialect_name).quote_identifier(name)


def _to_sql_alias(name: str, fallback: str) -> str:
    raw = (name or "").strip() or fallback
    normalized = re.sub(r'[`"\'\[\]]', "", raw)
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"[^\w\u4e00-\u9fa5]", "_", normalized)
    return normalized or fallback


def _normalize_column_token(token: str) -> str:
    cleaned = (token or "").strip()
    if not cleaned:
        return _quote_identifier("")
    if cleaned.startswith("`") and cleaned.endswith("`"):
        return cleaned
    return _quote_identifier(cleaned.replace("`", ""))


# Compatibility exports; filter behavior has one implementation in
# filter_compiler while the Builder module keeps its historical test surface.
def _is_numeric_literal(value: Any) -> bool:
    return filter_compiler._is_numeric_literal(value)


def _has_balanced_parentheses(expression: str) -> bool:
    return filter_compiler._has_balanced_parentheses(expression)


def _is_sql_function(value: Any) -> bool:
    return filter_compiler._is_sql_function(value, SQL_SAFE_FUNCTIONS)


def _unwrap_quoted_function(value: str) -> Optional[str]:
    text = (value or "").strip()
    if len(text) < 3 or text[0] not in "'\"" or text[-1] != text[0]:
        return None
    return text[1:-1].strip() or None


def _sql_literal(value: Any, value_type: Any = None) -> str:
    return filter_compiler._sql_literal(value, value_type, SQL_SAFE_FUNCTIONS)


def _value_type_for_index(value_type: Any, idx: int) -> Optional[str]:
    return filter_compiler._value_type_for_index(value_type, idx)


def _normalize_filter_value_type(value_type: Any) -> Optional[str]:
    if not isinstance(value_type, str):
        return None
    normalized = value_type.strip().lower()
    return normalized if normalized in {"literal", "sql_function"} else None


def _sql_list_literal(values: Any, value_type: Any = None) -> str:
    return filter_compiler._sql_list_literal(values, value_type, SQL_SAFE_FUNCTIONS)


def _is_simple_column_expr(expr: str) -> bool:
    return bool(re.match(r"^`?[\w\u4e00-\u9fa5]+`?$", expr or ""))


def _is_qualified_column_expr(expr: str) -> bool:
    return bool(re.match(r"^`?[\w\u4e00-\u9fa5]+`?\.`?[\w\u4e00-\u9fa5]+`?$", expr or ""))


def _is_filter_tree_node(node: Dict[str, Any]) -> bool:
    return isinstance(node, dict) and ("and" in node or "or" in node)


def _get_filter_tree_scope(node: Dict[str, Any]) -> Optional[str]:
    if not isinstance(node, dict):
        return None
    if _is_filter_tree_node(node):
        children = node.get("and") if "and" in node else node.get("or")
        if not isinstance(children, list):
            return None
        child_scopes = {_get_filter_tree_scope(child) for child in children}
        child_scopes.discard(None)
        if not child_scopes:
            return None
        if len(child_scopes) > 1:
            return "mixed"
        return child_scopes.pop()
    return "aggregate" if node.get("aggFunc") else "detail"


def _is_real_aggregation_func(func: Any) -> bool:
    return str(func or "RAW").upper() != "RAW"


def _filter_has_real_aggregation(node: Dict[str, Any]) -> bool:
    if not isinstance(node, dict):
        return False
    if _is_filter_tree_node(node):
        children = node.get("and") if "and" in node else node.get("or")
        if not isinstance(children, list):
            return False
        return any(_filter_has_real_aggregation(child) for child in children if isinstance(child, dict))
    if _is_real_aggregation_func(node.get("aggFunc")):
        return True
    if _having_operand_has_real_aggregation(node.get("rightOperand")):
        return True
    return _is_aggregate_expression(node.get("expression"))


def _having_operand_has_real_aggregation(operand: Any) -> bool:
    if not isinstance(operand, dict):
        return False
    operand_type = str(operand.get("type") or "").strip().lower()
    if operand_type == "metric":
        if _is_real_aggregation_func(operand.get("aggFunc")):
            return True
        return _is_aggregate_expression(operand.get("expression"))
    if operand_type == "binary":
        return _having_operand_has_real_aggregation(operand.get("left")) or _having_operand_has_real_aggregation(operand.get("right"))
    return False


def _order_by_has_real_aggregation(item: Dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    agg_func = item.get("aggFunc")
    if not agg_func:
        return False
    if _is_real_aggregation_func(agg_func):
        return True
    return _is_aggregate_expression(item.get("expression"))


def _effective_table_ref(item: Dict[str, Any], table_key: str = "table", alias_key: str = "tableAlias", fallback: str = "") -> str:
    return str(item.get(alias_key) or item.get(table_key) or fallback or "").strip()


def _physical_table_ref(item: Dict[str, Any], table_key: str = "table", fallback: str = "") -> str:
    return str(item.get(table_key) or fallback or "").strip()


def _build_from_ref(table: str, alias: Optional[str] = None) -> str:
    table_name = str(table or "").strip()
    alias_name = str(alias or "").strip()
    if not table_name:
        return ""
    if not alias_name or alias_name == table_name:
        return table_name
    return f"{table_name} {alias_name}"


def _rewrite_expression_table_qualifier(expression: str, table: str, physical_table: str, is_multi_table: bool) -> str:
    if not table or not physical_table or table == physical_table:
        return expression
    escaped = re.escape(physical_table)
    return re.sub(rf"(?<![\w`])`?{escaped}`?\.", f"{table}.", expression)


def _normalize_raw_expression(expression: Optional[str], table: str, column: str, is_multi_table: bool, physical_table: Optional[str] = None) -> str:
    expr = (expression or "").strip()
    if not expr:
        return _get_column_expr(table, column, is_multi_table)
    # 维度/指标左侧表达式可能来自请求侧（语义配置缺省时的 item 回退），
    # 与筛选 rightExpression 同口径过黑名单。
    ensure_safe_sql_expression(expr, label="维度表达式")
    expr = _rewrite_expression_table_qualifier(expr, table, physical_table or table, is_multi_table)
    if _is_simple_column_expr(expr):
        return _get_column_expr(table, expr.replace("`", ""), is_multi_table)
    if _is_qualified_column_expr(expr):
        if is_multi_table or (physical_table and table != physical_table):
            return expr
        return expr.split(".")[-1] if "." in expr else expr
    return expr


def _get_column_expr(table: str, column: str, is_multi_table: bool) -> str:
    if column == "*":
        return "*"
    col_expr = _normalize_column_token(column)
    return f"{table}.{col_expr}" if is_multi_table else col_expr


def _is_aggregate_expression(expression: Optional[str]) -> bool:
    expr = (expression or "").strip()
    return bool(expr) and bool(AGG_EXPR_PATTERN.match(expr))


def _normalize_aggregate_expression(expression: Optional[str], table: str, column: str, is_multi_table: bool, physical_table: Optional[str] = None) -> str:
    expr = (expression or "").strip()
    if not expr:
        return expr
    match = AGG_EXPR_PATTERN.match(expr)
    if not match:
        return expr
    func = (match.group(1) or "").upper()
    distinct = "DISTINCT " if (match.group(2) or "").strip() else ""
    inner = (match.group(3) or "").strip()
    normalized_inner = _normalize_raw_expression(inner, table, column, is_multi_table, physical_table)
    return f"{func}({distinct}{normalized_inner})"


def _build_where_leaf_clause(
    leaf: Dict[str, Any],
    is_multi_table: bool,
    dimension_expr_map: Dict[Tuple[str, str], str],
    main_table: str,
) -> str:
    return compile_where_leaf_clause(
        leaf,
        is_multi_table=is_multi_table,
        dimension_expr_map=dimension_expr_map,
        main_table=main_table,
        normalize_raw_expression=_normalize_raw_expression,
        get_column_expr=_get_column_expr,
        safe_functions=SQL_SAFE_FUNCTIONS,
    )


def _build_having_leaf_clause(
    leaf: Dict[str, Any],
    is_multi_table: bool,
    main_table: str,
    aggregations: List[Dict[str, Any]],
    agg_by_id: Dict[str, Dict[str, Any]],
    normalize_expression: bool = False,
) -> str:
    return compile_having_leaf_clause(
        leaf,
        is_multi_table=is_multi_table,
        main_table=main_table,
        aggregations=aggregations,
        agg_by_id=agg_by_id,
        normalize_raw_expression=_normalize_raw_expression,
        normalize_aggregate_expression=_normalize_aggregate_expression,
        get_column_expr=_get_column_expr,
        is_aggregate_expression=_is_aggregate_expression,
        safe_functions=SQL_SAFE_FUNCTIONS,
        normalize_expression=normalize_expression,
    )


def _build_join_clauses(main_table: str, joins: List[Dict[str, Any]]) -> List[str]:
    return build_join_clauses(main_table, joins)


def _build_filter_tree_clause(
    node: Dict[str, Any],
    *,
    for_having: bool,
    is_multi_table: bool,
    main_table: str,
    dimension_expr_map: Dict[Tuple[str, str], str],
    aggregations: List[Dict[str, Any]],
    agg_by_id: Dict[str, Dict[str, Any]],
    is_root: bool = False,
) -> str:
    return compile_filter_tree_clause(
        node,
        for_having=for_having,
        is_root=is_root,
        is_multi_table=is_multi_table,
        main_table=main_table,
        dimension_expr_map=dimension_expr_map,
        aggregations=aggregations,
        agg_by_id=agg_by_id,
        normalize_raw_expression=_normalize_raw_expression,
        normalize_aggregate_expression=_normalize_aggregate_expression,
        get_column_expr=_get_column_expr,
        is_aggregate_expression=_is_aggregate_expression,
        safe_functions=SQL_SAFE_FUNCTIONS,
    )


def _validate_query_plan(query_plan: Dict[str, Any]) -> None:
    """编译前置校验；不合法直接 raise ValueError。"""
    if not isinstance(query_plan, dict):
        raise ValueError("查询计划格式错误")
    if not (query_plan.get("mainTable") or "").strip():
        raise ValueError("查询计划缺少 mainTable")
    if not query_plan.get("dimensions") and not query_plan.get("aggregations"):
        raise ValueError("请至少选择一个维度或指标")
    if query_plan.get("compare"):
        raise ValueError("DSL2SQL 不再支持 compare 同比/环比 SQL；请拆成基础 DSL 查询；复杂同比/环比请使用智能分析")
    aggregations = query_plan.get("aggregations") or []
    has_raw_metric = any(str(a.get("func") or "RAW").upper() == "RAW" for a in aggregations)
    has_agg_metric = any(str(a.get("func") or "RAW").upper() != "RAW" for a in aggregations)
    if has_raw_metric and has_agg_metric:
        raise ValueError("指标区不支持原始值与聚合函数混用，请统一为原始值或聚合函数")


def _split_where_and_having(filters: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按节点类型把过滤分流到 WHERE / HAVING 两桶；混合筛选树不合法。"""
    where_filters: List[Dict[str, Any]] = []
    having_filters: List[Dict[str, Any]] = []
    for f in filters:
        if _is_filter_tree_node(f):
            tree_scope = _get_filter_tree_scope(f)
            if tree_scope == "mixed":
                raise ValueError("过滤条件不合法：同一组合筛选树不能混用明细过滤和结果过滤")
            if tree_scope == "aggregate":
                having_filters.append(f)
            else:
                where_filters.append(f)
            continue
        if f.get("aggFunc"):
            having_filters.append(f)
        else:
            where_filters.append(f)
    return where_filters, having_filters


def _append_filter_clause(
    parts: List[str],
    filter_list: List[Dict[str, Any]],
    *,
    for_having: bool,
    is_multi_table: bool,
    main_alias: str,
    dimension_expr_map: Dict[Tuple[str, str], str],
    aggregations: List[Dict[str, Any]],
    agg_by_id: Dict[str, Dict[str, Any]],
) -> None:
    """WHERE / HAVING 共用的子句组装：单棵筛选树走树编译器，否则平铺拼接。"""
    if not filter_list:
        return
    keyword = "HAVING" if for_having else "WHERE"
    if len(filter_list) == 1 and isinstance(filter_list[0], dict) and _is_filter_tree_node(filter_list[0]):
        clause = compile_filter_tree_clause(
            filter_list[0],
            for_having=for_having,
            is_root=True,
            is_multi_table=is_multi_table,
            main_table=main_alias,
            dimension_expr_map=dimension_expr_map,
            aggregations=aggregations,
            agg_by_id=agg_by_id,
            normalize_raw_expression=_normalize_raw_expression,
            normalize_aggregate_expression=_normalize_aggregate_expression,
            get_column_expr=_get_column_expr,
            is_aggregate_expression=_is_aggregate_expression,
            safe_functions=SQL_SAFE_FUNCTIONS,
        )
        if clause:
            parts.append(f"{keyword} {clause}")
        return
    clauses = []
    for idx, f in enumerate(filter_list):
        logic = str(f.get("logic", "AND")).upper()
        if for_having:
            clause = _build_having_leaf_clause(
                f,
                is_multi_table,
                main_alias,
                aggregations,
                agg_by_id,
                normalize_expression=True,
            )
        else:
            clause = _build_where_leaf_clause(f, is_multi_table, dimension_expr_map, main_alias)
        clauses.append(clause if idx == 0 else f"{logic} {clause}")
    parts.append(f"{keyword} {' '.join(clauses)}")


def _find_agg_block(
    aggregations: List[Dict[str, Any]],
    agg_by_id: Dict[str, Dict[str, Any]],
    main_alias: str,
    agg_func: str,
    table: str,
    column: str,
    agg_id: Optional[str],
    expression: Optional[str],
) -> Optional[Dict[str, Any]]:
    """按 aggId 优先、表列特征次之回溯聚合块。"""
    matched = agg_by_id.get(str(agg_id)) if agg_id else None
    if not matched and not (expression or "").strip():
        for a in aggregations:
            if _effective_table_ref(a, fallback=main_alias) == table and a.get("column") == column and (a.get("func") or "").upper() == agg_func:
                matched = a
                break
    return matched


def _resolve_agg_expr(
    aggregations: List[Dict[str, Any]],
    agg_by_id: Dict[str, Dict[str, Any]],
    main_alias: str,
    is_multi_table: bool,
    agg_func: str,
    table: str,
    column: str,
    agg_id: Optional[str],
    expression: Optional[str] = None,
    physical_table: Optional[str] = None,
) -> str:
    """按 aggId / 表列特征回溯聚合块，产出聚合表达式。"""
    matched = _find_agg_block(aggregations, agg_by_id, main_alias, agg_func, table, column, agg_id, expression)

    matched_expr = (matched or {}).get("expression") or expression
    matched_physical_table = _physical_table_ref(matched or {}, fallback=physical_table or table)
    if agg_func == "RAW" and (matched_expr or "").strip():
        return _normalize_raw_expression(matched_expr, table, column, is_multi_table, matched_physical_table)
    if agg_func != "RAW" and (matched_expr or "").strip() and _is_aggregate_expression(matched_expr):
        return _normalize_aggregate_expression(matched_expr, table, column, is_multi_table, matched_physical_table)

    col_expr = "*" if column == "*" else _get_column_expr(table, column, is_multi_table)
    if agg_func == "RAW":
        return col_expr
    if agg_func == "COUNT_DISTINCT":
        return f"COUNT(DISTINCT {col_expr})"
    return f"{agg_func}({col_expr})"


def _match_aggregation_for_order(
    aggregations: List[Dict[str, Any]],
    main_alias: str,
    table: str,
    column: str,
) -> Optional[Dict[str, Any]]:
    """聚合查询按明细字段排序时，回溯唯一的非 RAW 聚合块。"""
    matched_aggs = [
        a
        for a in aggregations
        if (a.get("func") or "RAW").upper() != "RAW"
        and _effective_table_ref(a, fallback=main_alias) == table
        and a.get("column", "") == column
    ]
    if len(matched_aggs) == 1:
        return matched_aggs[0]
    return None


def _build_order_by_fields(
    order_by: List[Dict[str, Any]],
    *,
    aggregations: List[Dict[str, Any]],
    agg_by_id: Dict[str, Dict[str, Any]],
    main_alias: str,
    is_multi_table: bool,
    dimension_alias_map: Dict[Tuple[str, str], str],
    group_expr_map: Dict[Tuple[str, str], str],
    dimension_expr_map: Dict[Tuple[str, str], str],
    has_real_aggregation: bool,
) -> List[str]:
    """ORDER BY 字段解析：aggFunc > 维度别名 > 分组表达式 > 维度表达式 > 聚合回溯 > 裸列。"""
    order_fields = []
    for o in order_by:
        direction = str(o.get("direction", "ASC")).upper()
        table = _effective_table_ref(o, fallback=main_alias)
        column = o.get("column", "")
        agg_func = o.get("aggFunc")
        if agg_func:
            physical_table = _physical_table_ref(o, fallback=table)
            order_fields.append(
                f"{_resolve_agg_expr(aggregations, agg_by_id, main_alias, is_multi_table, str(agg_func).upper(), table, column, o.get('aggId'), o.get('expression'), physical_table)} {direction}"
            )
        elif (table, column) in dimension_alias_map:
            order_fields.append(f"{_quote_identifier(dimension_alias_map[(table, column)])} {direction}")
        elif (table, column) in group_expr_map:
            order_fields.append(f"{group_expr_map[(table, column)]} {direction}")
        elif (table, column) in dimension_expr_map:
            order_fields.append(f"{dimension_expr_map[(table, column)]} {direction}")
        elif has_real_aggregation:
            matched_agg = _match_aggregation_for_order(aggregations, main_alias, table, column)
            if matched_agg is not None:
                order_fields.append(
                    f"{_resolve_agg_expr(aggregations, agg_by_id, main_alias, is_multi_table, str(matched_agg.get('func') or '').upper(), table, column, matched_agg.get('id'))} {direction}"
                )
                continue
            raise ValueError("聚合查询不能按未分组的明细字段排序，请改用已选择的维度或聚合指标排序")
        else:
            order_fields.append(f"{table}.{column} {direction}")
    return order_fields


def _has_real_aggregation(
    aggregations: List[Dict[str, Any]],
    having_filters: List[Dict[str, Any]],
    order_by: List[Dict[str, Any]],
) -> bool:
    """聚合判定：输出、HAVING、ORDER BY 任一含真实聚合函数。"""
    return (
        any(_is_real_aggregation_func(a.get("func")) for a in aggregations)
        or any(_filter_has_real_aggregation(f) for f in having_filters)
        or any(_order_by_has_real_aggregation(o) for o in order_by)
    )


def _append_limit_offset(
    parts: List[str],
    sql_dialect: Any,
    limit: Optional[int],
    offset: Optional[int],
) -> None:
    """校验并追加 LIMIT/OFFSET 子句。"""
    if offset is not None:
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("OFFSET 必须为非负整数")
        if limit is None:
            raise ValueError("OFFSET 必须配合 LIMIT 使用")
    if limit is not None or offset is not None:
        parts.append(sql_dialect.limit_offset(int(limit), int(offset or 0)))


class _QueryPlanInputs(NamedTuple):
    """从查询计划归一化出的编译输入。"""

    main_table: str
    main_alias: str
    dimensions: List[Dict[str, Any]]
    aggregations: List[Dict[str, Any]]
    joins: List[Dict[str, Any]]
    filters: List[Dict[str, Any]]
    order_by: List[Dict[str, Any]]
    offset: Any
    limit: Optional[int]


def _plan_compilation_inputs(query_plan: Dict[str, Any], max_rows: int) -> _QueryPlanInputs:
    """提取编译输入；行数限制在此归一化（先于结构校验，保持原报错优先级）。"""
    raw_limit = query_plan.get("limit")
    main_table = (query_plan.get("mainTable") or "").strip()
    return _QueryPlanInputs(
        main_table=main_table,
        main_alias=(query_plan.get("mainAlias") or main_table).strip(),
        dimensions=query_plan.get("dimensions") or [],
        aggregations=query_plan.get("aggregations") or [],
        joins=query_plan.get("joins") or [],
        filters=query_plan.get("filters") or [],
        order_by=query_plan.get("orderBy") or [],
        offset=query_plan.get("offset"),
        limit=normalize_row_limit(raw_limit, max_rows=max_rows) if raw_limit is not None else None,
    )


def _compile_query_plan_sql(
    query_plan: Dict[str, Any],
    dialect: str = "mysql",
    max_rows: int = 100,
) -> str:
    """
    将标准化查询计划编译为 SQL。

    Args:
        query_plan: 两种输入适配器生成的内部查询计划

    Returns:
        SQL 字符串

    Raises:
        ValueError: 查询计划不完整或无法编译
    """
    if not isinstance(query_plan, dict):
        raise ValueError("查询计划格式错误")
    # 行数限制校验先于结构校验，保持与拆分前一致的报错优先级。
    inputs = _plan_compilation_inputs(query_plan, max_rows)
    _validate_query_plan(query_plan)

    main_table = inputs.main_table
    main_alias = inputs.main_alias
    dimensions = inputs.dimensions
    aggregations = inputs.aggregations
    joins = inputs.joins
    filters = inputs.filters
    order_by = inputs.order_by
    offset = inputs.offset
    limit = inputs.limit

    selected_tables = [main_alias] + [j.get("rightAlias") or j.get("rightTable") for j in joins if j.get("rightTable")]
    is_multi_table = len(selected_tables) > 1
    sql_dialect = get_dialect(dialect)

    agg_by_id: Dict[str, Dict[str, Any]] = {str(a.get("id")): a for a in aggregations if a.get("id")}
    select_compilation = compile_select_fields(
        dimensions=dimensions,
        aggregations=aggregations,
        main_alias=main_alias,
        is_multi_table=is_multi_table,
        dialect=sql_dialect,
        quote_identifier=_quote_identifier,
        to_sql_alias=_to_sql_alias,
        normalize_raw_expression=_normalize_raw_expression,
        normalize_aggregate_expression=_normalize_aggregate_expression,
        get_column_expr=_get_column_expr,
        is_aggregate_expression=_is_aggregate_expression,
    )
    select_fields = select_compilation.fields
    dimension_expr_map = select_compilation.dimension_expr_map
    group_expr_map = select_compilation.group_expr_map
    dimension_alias_map = select_compilation.dimension_alias_map

    parts: List[str] = [f"SELECT {', '.join(select_fields)}"]
    parts.extend(build_join_clauses(main_table, joins, main_alias))

    where_filters, having_filters = _split_where_and_having(filters)
    _append_filter_clause(
        parts,
        where_filters,
        for_having=False,
        is_multi_table=is_multi_table,
        main_alias=main_alias,
        dimension_expr_map=dimension_expr_map,
        aggregations=aggregations,
        agg_by_id=agg_by_id,
    )

    has_real_aggregation = _has_real_aggregation(aggregations, having_filters, order_by)
    if dimensions and has_real_aggregation:
        group_fields = [group_expr_map[(_effective_table_ref(d, fallback=main_alias), d.get("column", ""))] for d in dimensions]
        parts.append(f"GROUP BY {', '.join(group_fields)}")

    _append_filter_clause(
        parts,
        having_filters,
        for_having=True,
        is_multi_table=is_multi_table,
        main_alias=main_alias,
        dimension_expr_map=dimension_expr_map,
        aggregations=aggregations,
        agg_by_id=agg_by_id,
    )

    if order_by:
        order_fields = _build_order_by_fields(
            order_by,
            aggregations=aggregations,
            agg_by_id=agg_by_id,
            main_alias=main_alias,
            is_multi_table=is_multi_table,
            dimension_alias_map=dimension_alias_map,
            group_expr_map=group_expr_map,
            dimension_expr_map=dimension_expr_map,
            has_real_aggregation=has_real_aggregation,
        )
        parts.append(f"ORDER BY {', '.join(order_fields)}")

    _append_limit_offset(parts, sql_dialect, limit, offset)
    return "\n".join(parts)

def compile_query_plan_sql(
    query_plan: Dict[str, Any],
    dialect: str = "mysql",
    max_rows: int = 100,
) -> str:
    """建立本次编译的方言上下文。"""
    dialect_name = str(dialect or "mysql").strip().lower() or "mysql"
    get_dialect(dialect_name)
    token = _DIALECT_CONTEXT.set(dialect_name)
    try:
        return _compile_query_plan_sql(query_plan, dialect=dialect_name, max_rows=max_rows)
    finally:
        _DIALECT_CONTEXT.reset(token)
