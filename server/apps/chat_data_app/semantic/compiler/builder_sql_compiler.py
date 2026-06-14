"""
Builder DSL -> SQL 编译器

将前端可视化查询的 builder_config 编译为 SQL，供后端统一执行。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from ...services.runtime_config import normalize_row_limit
from ..engine import get_dialect

AGG_EXPR_PATTERN = re.compile(r"^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$", re.IGNORECASE)
SQL_FUNCTION_PATTERN = re.compile(r"^([a-z_][a-z0-9_]*)\s*\((.*)\)$", re.IGNORECASE)
SQL_SAFE_FUNCTIONS = {
    "current_date", "current_timestamp", "now", "curdate", "curtime",
    "date_sub", "date_add", "datediff", "date_format", "date",
    "year", "month", "day", "hour", "minute", "second",
    "quarter", "week", "dayofweek", "dayofyear", "dayofmonth",
    "to_date", "from_unixtime", "unix_timestamp",
    "concat", "substring", "substr", "length", "char_length",
    "upper", "lower", "trim", "ltrim", "rtrim",
    "replace", "reverse", "left", "right",
    "abs", "round", "ceil", "ceiling", "floor", "mod",
    "pow", "power", "sqrt", "rand",
    "count", "sum", "avg", "max", "min",
    "if", "ifnull", "nullif", "coalesce", "nvl",
    "case", "when", "then", "else", "end",
}


def _quote_identifier(name: str) -> str:
    return get_dialect("mysql").quote_identifier(name)


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


def _is_simple_column_expr(expr: str) -> bool:
    return bool(re.match(r"^`?[\w\u4e00-\u9fa5]+`?$", expr or ""))


def _is_qualified_column_expr(expr: str) -> bool:
    return bool(re.match(r"^`?[\w\u4e00-\u9fa5]+`?\.`?[\w\u4e00-\u9fa5]+`?$", expr or ""))


def _is_numeric_literal(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return True
    value_str = str(value).strip()
    if value_str == "":
        return False
    try:
        float(value_str)
        return True
    except Exception:
        return False


def _unwrap_quoted_function(value: str) -> Optional[str]:
    text = (value or "").strip()
    if len(text) < 3:
        return None
    if (text[0] == "'" and text[-1] == "'") or (text[0] == '"' and text[-1] == '"'):
        inner = text[1:-1].strip()
        return inner or None
    return None


def _is_sql_function(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    raw = value.strip()
    if not raw:
        return False
    match = SQL_FUNCTION_PATTERN.match(raw)
    if not match:
        return False
    func_name = match.group(1).lower()
    if func_name not in SQL_SAFE_FUNCTIONS:
        return False
    # 防止函数表达式中夹带注释/分号等危险片段
    if ";" in raw or "--" in raw or "/*" in raw or "*/" in raw:
        return False
    return _has_balanced_parentheses(raw)


def _has_balanced_parentheses(expression: str) -> bool:
    balance = 0
    in_single = False
    in_double = False
    escaped = False
    for ch in expression:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if in_single or in_double:
            continue
        if ch == "(":
            balance += 1
        elif ch == ")":
            balance -= 1
            if balance < 0:
                return False
    return balance == 0 and not in_single and not in_double


def _normalize_filter_value_type(value_type: Any) -> Optional[str]:
    if not isinstance(value_type, str):
        return None
    normalized = value_type.strip().lower()
    return normalized if normalized in {"literal", "sql_function"} else None


def _value_type_for_index(value_type: Any, idx: int) -> Optional[str]:
    if isinstance(value_type, list):
        if idx < len(value_type):
            return _normalize_filter_value_type(value_type[idx])
        return None
    return _normalize_filter_value_type(value_type)


def _sql_literal(value: Any, value_type: Any = None) -> str:
    if value is None:
        return "NULL"
    if _is_numeric_literal(value):
        return str(value).strip()
    if isinstance(value, str):
        raw = value.strip()
        unwrapped = _unwrap_quoted_function(raw)
        normalized_type = _normalize_filter_value_type(value_type)
        if normalized_type == "literal":
            escaped = str(value).replace("'", "''")
            return f"'{escaped}'"
        if normalized_type == "sql_function":
            if _is_sql_function(raw):
                return raw
            if unwrapped and _is_sql_function(unwrapped):
                return unwrapped
            escaped = str(value).replace("'", "''")
            return f"'{escaped}'"
        # 未显式标注时，才做自动函数识别兜底
        if _is_sql_function(raw):
            return raw
        if unwrapped and _is_sql_function(unwrapped):
            return unwrapped
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _sql_list_literal(values: Any, value_type: Any = None) -> str:
    if not isinstance(values, list):
        return f"({_sql_literal(values, value_type)})"
    if not values:
        return "(NULL)"
    return f"({', '.join(_sql_literal(v, _value_type_for_index(value_type, idx)) for idx, v in enumerate(values))})"


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
    return _is_aggregate_expression(node.get("expression"))


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


def _build_where_leaf_clause(
    leaf: Dict[str, Any],
    is_multi_table: bool,
    dimension_expr_map: Dict[Tuple[str, str], str],
    main_table: str,
) -> str:
    table = _effective_table_ref(leaf, fallback=main_table)
    physical_table = _physical_table_ref(leaf, fallback=table)
    column = leaf.get("column", "")
    operator = str(leaf.get("operator", "=")).upper()
    value_type = leaf.get("value_type")

    if (leaf.get("expression") or "").strip():
        where_expr = _normalize_raw_expression(leaf.get("expression"), table, column, is_multi_table, physical_table)
    elif (table, column) in dimension_expr_map:
        where_expr = dimension_expr_map[(table, column)]
    else:
        where_expr = _get_column_expr(table, column, is_multi_table)

    if operator in ("IS NULL", "IS NOT NULL"):
        return f"{where_expr} {operator}"
    if (
        leaf.get("valueType") == "field_ref"
        and (leaf.get("rightTable") or "").strip()
        and (leaf.get("rightColumn") or "").strip()
    ):
        right_expr = _get_column_expr(
            str(leaf.get("rightTableAlias") or leaf.get("rightTable")).strip(),
            str(leaf.get("rightColumn")).strip(),
            is_multi_table,
        )
        return f"{where_expr} {operator} {right_expr}"
    if operator in ("IN", "NOT IN") and (leaf.get("rightExpression") or "").strip():
        expr = str(leaf.get("rightExpression")).strip()
        return f"{where_expr} {operator} ({expr})"
    if leaf.get("valueType") == "expression" and (leaf.get("rightExpression") or "").strip():
        expr = str(leaf.get("rightExpression")).strip()
        if operator in ("IN", "NOT IN"):
            return f"{where_expr} {operator} ({expr})"
        return f"{where_expr} {operator} {expr}"
    if operator == "EMPTY STRING":
        return f"{where_expr} = ''"
    if operator == "STARTS WITH":
        start_value = str(leaf.get("value", "")).replace("'", "''")
        return f"{where_expr} LIKE '{start_value}%'"
    if operator == "ENDS WITH":
        end_value = str(leaf.get("value", "")).replace("'", "''")
        return f"{where_expr} LIKE '%{end_value}'"
    if operator in ("LIKE", "NOT LIKE"):
        like_value = str(leaf.get("value", "")).replace("'", "''")
        normalized = like_value if ("%" in like_value or "_" in like_value) else f"%{like_value}%"
        return f"{where_expr} {operator} '{normalized}'"
    if operator in ("IN", "NOT IN"):
        return f"{where_expr} {operator} {_sql_list_literal(leaf.get('value'), value_type)}"
    if operator == "BETWEEN" and isinstance(leaf.get("value"), list) and len(leaf.get("value")) == 2:
        left = _sql_literal(leaf.get("value")[0], _value_type_for_index(value_type, 0))
        right = _sql_literal(leaf.get("value")[1], _value_type_for_index(value_type, 1))
        return f"{where_expr} BETWEEN {left} AND {right}"
    return f"{where_expr} {operator} {_sql_literal(leaf.get('value'), value_type)}"


def _build_having_leaf_clause(
    leaf: Dict[str, Any],
    is_multi_table: bool,
    main_table: str,
    aggregations: List[Dict[str, Any]],
    agg_by_id: Dict[str, Dict[str, Any]],
) -> str:
    agg_func = str(leaf.get("aggFunc", "RAW")).upper()
    table = _effective_table_ref(leaf, fallback=main_table)
    physical_table = _physical_table_ref(leaf, fallback=table)
    column = leaf.get("column", "")
    operator = str(leaf.get("operator", "=")).upper()
    value_type = leaf.get("value_type")
    agg_id = leaf.get("aggId")

    matched = agg_by_id.get(str(agg_id)) if agg_id else None
    if not matched and not (leaf.get("expression") or "").strip():
        for agg in aggregations:
            if _effective_table_ref(agg, fallback=main_table) == table and agg.get("column") == column and (agg.get("func") or "").upper() == agg_func:
                matched = agg
                break

    matched_physical_table = _physical_table_ref(matched or {}, fallback=physical_table)
    matched_expr = (matched or {}).get("expression") or leaf.get("expression")
    if agg_func == "RAW" and (matched_expr or "").strip():
        agg_expr = _rewrite_expression_table_qualifier(matched_expr.strip(), table, matched_physical_table, is_multi_table)
    elif agg_func != "RAW" and (matched_expr or "").strip() and _is_aggregate_expression(matched_expr):
        agg_expr = _rewrite_expression_table_qualifier(matched_expr.strip(), table, matched_physical_table, is_multi_table)
    else:
        col_expr = "*" if column == "*" else _get_column_expr(table, column, is_multi_table)
        if agg_func == "RAW":
            agg_expr = col_expr
        elif agg_func == "COUNT_DISTINCT":
            agg_expr = f"COUNT(DISTINCT {col_expr})"
        else:
            agg_expr = f"{agg_func}({col_expr})"

    if operator in ("IS NULL", "IS NOT NULL"):
        return f"{agg_expr} {operator}"
    if (
        leaf.get("valueType") == "field_ref"
        and (leaf.get("rightTable") or "").strip()
        and (leaf.get("rightColumn") or "").strip()
    ):
        right_expr = _get_column_expr(
            str(leaf.get("rightTableAlias") or leaf.get("rightTable")).strip(),
            str(leaf.get("rightColumn")).strip(),
            is_multi_table,
        )
        return f"{agg_expr} {operator} {right_expr}"
    if operator in ("IN", "NOT IN") and (leaf.get("rightExpression") or "").strip():
        expr = str(leaf.get("rightExpression")).strip()
        return f"{agg_expr} {operator} ({expr})"
    if leaf.get("valueType") == "expression" and (leaf.get("rightExpression") or "").strip():
        expr = str(leaf.get("rightExpression")).strip()
        if operator in ("IN", "NOT IN"):
            return f"{agg_expr} {operator} ({expr})"
        return f"{agg_expr} {operator} {expr}"
    if operator == "EMPTY STRING":
        return f"{agg_expr} = ''"
    if operator == "STARTS WITH":
        start_value = str(leaf.get("value", "")).replace("'", "''")
        return f"{agg_expr} LIKE '{start_value}%'"
    if operator == "ENDS WITH":
        end_value = str(leaf.get("value", "")).replace("'", "''")
        return f"{agg_expr} LIKE '%{end_value}'"
    if operator in ("LIKE", "NOT LIKE"):
        like_value = str(leaf.get("value", "")).replace("'", "''")
        normalized = like_value if ("%" in like_value or "_" in like_value) else f"%{like_value}%"
        return f"{agg_expr} {operator} '{normalized}'"
    if operator in ("IN", "NOT IN"):
        return f"{agg_expr} {operator} {_sql_list_literal(leaf.get('value'), value_type)}"
    if operator == "BETWEEN" and isinstance(leaf.get("value"), list) and len(leaf.get("value")) == 2:
        left = _sql_literal(leaf.get("value")[0], _value_type_for_index(value_type, 0))
        right = _sql_literal(leaf.get("value")[1], _value_type_for_index(value_type, 1))
        return f"{agg_expr} BETWEEN {left} AND {right}"
    return f"{agg_expr} {operator} {_sql_literal(leaf.get('value'), value_type)}"


def _build_filter_tree_clause(
    node: Dict[str, Any],
    *,
    for_having: bool,
    is_multi_table: bool,
    main_table: str,
    dimension_expr_map: Dict[Tuple[str, str], str],
    aggregations: List[Dict[str, Any]],
    agg_by_id: Dict[str, Dict[str, Any]],
) -> str:
    if "and" in node:
        children = node.get("and") or []
        child_clauses = [
            _build_filter_tree_clause(
                child,
                for_having=for_having,
                is_multi_table=is_multi_table,
                main_table=main_table,
                dimension_expr_map=dimension_expr_map,
                aggregations=aggregations,
                agg_by_id=agg_by_id,
            )
            for child in children
            if isinstance(child, dict)
        ]
        child_clauses = [c for c in child_clauses if c]
        if not child_clauses:
            return ""
        if len(child_clauses) == 1:
            return child_clauses[0]
        return f"({' AND '.join(child_clauses)})"

    if "or" in node:
        children = node.get("or") or []
        child_clauses = [
            _build_filter_tree_clause(
                child,
                for_having=for_having,
                is_multi_table=is_multi_table,
                main_table=main_table,
                dimension_expr_map=dimension_expr_map,
                aggregations=aggregations,
                agg_by_id=agg_by_id,
            )
            for child in children
            if isinstance(child, dict)
        ]
        child_clauses = [c for c in child_clauses if c]
        if not child_clauses:
            return ""
        if len(child_clauses) == 1:
            return child_clauses[0]
        return f"({' OR '.join(child_clauses)})"

    if for_having:
        return _build_having_leaf_clause(node, is_multi_table, main_table, aggregations, agg_by_id)
    return _build_where_leaf_clause(node, is_multi_table, dimension_expr_map, main_table)


def _rewrite_expression_table_qualifier(expression: str, table: str, physical_table: str, is_multi_table: bool) -> str:
    if not table or not physical_table or table == physical_table:
        return expression
    escaped = re.escape(physical_table)
    return re.sub(rf"(?<![\w`])`?{escaped}`?\.", f"{table}.", expression)


def _normalize_raw_expression(expression: Optional[str], table: str, column: str, is_multi_table: bool, physical_table: Optional[str] = None) -> str:
    expr = (expression or "").strip()
    if not expr:
        return _get_column_expr(table, column, is_multi_table)
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


def _build_join_clauses(main_table: str, joins: List[Dict[str, Any]]) -> List[str]:
    parts: List[str] = [f"FROM {_build_from_ref(main_table)}"]
    for join in joins:
        right_table = join.get("rightTable")
        if not right_table:
            continue
        right_alias = str(join.get("rightAlias") or right_table).strip()
        join_type = (join.get("joinType") or "LEFT").upper()
        clause = f"{join_type} JOIN {_build_from_ref(right_table, right_alias)}"

        on_expr = ""
        join_conditions = join.get("joinConditions") or []
        if join_conditions:
            pairs = []
            for cond in join_conditions:
                left_table = cond.get("leftAlias") or cond.get("leftTable") or join.get("leftAlias") or join.get("leftTable") or main_table
                left_col = cond.get("leftColumn")
                right_col = cond.get("rightColumn")
                if left_table and left_col and right_col:
                    pairs.append(f"{left_table}.{left_col} = {right_alias}.{right_col}")
            on_expr = " AND ".join(pairs)
        else:
            left_table = join.get("leftAlias") or join.get("leftTable") or main_table
            left_column = join.get("leftColumn")
            right_column = join.get("rightColumn")
            if left_table and left_column and right_column:
                on_expr = f"{left_table}.{left_column} = {right_alias}.{right_column}"
        if on_expr:
            clause += f" ON {on_expr}"
        parts.append(clause)
    return parts


def compile_builder_sql(builder_config: Dict[str, Any], dialect: str = "mysql") -> str:
    """
    将 builder_config 编译为 SQL。

    Args:
        builder_config: 前端可视化查询 DSL

    Returns:
        SQL 字符串

    Raises:
        ValueError: builder_config 不完整或无法编译
    """
    if not isinstance(builder_config, dict):
        raise ValueError("builder_config 格式错误")

    main_table = (builder_config.get("mainTable") or "").strip()
    main_alias = (builder_config.get("mainAlias") or main_table).strip()
    dimensions: List[Dict[str, Any]] = builder_config.get("dimensions") or []
    aggregations: List[Dict[str, Any]] = builder_config.get("aggregations") or []
    joins: List[Dict[str, Any]] = builder_config.get("joins") or []
    filters: List[Dict[str, Any]] = builder_config.get("filters") or []
    order_by: List[Dict[str, Any]] = builder_config.get("orderBy") or []
    raw_limit = builder_config.get("limit")
    limit = normalize_row_limit(raw_limit) if raw_limit is not None else None
    offset = builder_config.get("offset")

    if not main_table:
        raise ValueError("builder_config 缺少 mainTable")
    if not dimensions and not aggregations:
        raise ValueError("请至少选择一个维度或指标")

    if builder_config.get("compare"):
        raise ValueError("DSL2SQL 不再支持 compare 同比/环比 SQL；请拆成基础 DSL 查询；复杂同比/环比请使用智能分析")

    has_raw_metric = any(str(a.get("func") or "RAW").upper() == "RAW" for a in aggregations)
    has_agg_metric = any(str(a.get("func") or "RAW").upper() != "RAW" for a in aggregations)
    if has_raw_metric and has_agg_metric:
        raise ValueError("指标区不支持原始值与聚合函数混用，请统一为原始值或聚合函数")

    selected_tables = [main_alias] + [j.get("rightAlias") or j.get("rightTable") for j in joins if j.get("rightTable")]
    is_multi_table = len(selected_tables) > 1
    sql_dialect = get_dialect(dialect)

    used_aliases = set()

    def get_unique_alias(name: str, fallback: str) -> str:
        base = _to_sql_alias(name, fallback)
        if base not in used_aliases:
            used_aliases.add(base)
            return base
        idx = 2
        while f"{base}_{idx}" in used_aliases:
            idx += 1
        next_alias = f"{base}_{idx}"
        used_aliases.add(next_alias)
        return next_alias

    select_fields: List[str] = []
    dimension_expr_map: Dict[Tuple[str, str], str] = {}
    group_expr_map: Dict[Tuple[str, str], str] = {}
    dimension_alias_map: Dict[Tuple[str, str], str] = {}
    agg_by_id: Dict[str, Dict[str, Any]] = {str(a.get("id")): a for a in aggregations if a.get("id")}

    for dim in dimensions:
        table = _effective_table_ref(dim, fallback=main_alias)
        physical_table = _physical_table_ref(dim, fallback=table)
        column = dim.get("column", "")
        transform = dim.get("transform") or "raw"
        display_name = dim.get("displayName") or dim.get("alias") or column
        dim_expr = _normalize_raw_expression(dim.get("expression"), table, column, is_multi_table, physical_table)
        dim_alias = get_unique_alias(display_name, column or "col")

        if transform != "raw":
            transformed_expr = sql_dialect.format_date(dim_expr, transform)
            transformed_alias = get_unique_alias(f"{dim_alias}_{transform}", f"{column}_{transform}")
            select_fields.append(f"{transformed_expr} AS {_quote_identifier(transformed_alias)}")
            dimension_expr_map[(table, column)] = dim_expr
            group_expr_map[(table, column)] = transformed_expr
            dimension_alias_map[(table, column)] = transformed_alias
        else:
            select_fields.append(f"{dim_expr} AS {_quote_identifier(dim_alias)}")
            dimension_expr_map[(table, column)] = dim_expr
            group_expr_map[(table, column)] = dim_expr
            dimension_alias_map[(table, column)] = dim_alias

    for agg in aggregations:
        table = _effective_table_ref(agg, fallback=main_alias)
        physical_table = _physical_table_ref(agg, fallback=table)
        column = agg.get("column", "")
        func = (agg.get("func") or "RAW").upper()
        expression = agg.get("expression")
        display_name = agg.get("displayName") or agg.get("alias") or ("all" if column == "*" else column)
        alias_col = get_unique_alias(display_name, "all" if column == "*" else (column or "metric"))

        if func == "RAW" and (expression or "").strip():
            select_fields.append(
                f"{_normalize_raw_expression(expression, table, column, is_multi_table, physical_table)} AS {_quote_identifier(alias_col)}"
            )
            continue
        if func != "RAW" and (expression or "").strip() and _is_aggregate_expression(expression):
            normalized_agg_expr = _normalize_aggregate_expression(expression, table, column, is_multi_table, physical_table)
            select_fields.append(f"{normalized_agg_expr} AS {_quote_identifier(alias_col)}")
            continue

        col_expr = "*" if column == "*" else _get_column_expr(table, column, is_multi_table)
        if func == "COUNT_DISTINCT":
            select_fields.append(
                f"{'COUNT(*)' if column == '*' else f'COUNT(DISTINCT {col_expr})'} AS {_quote_identifier(alias_col)}"
            )
        elif func == "RAW":
            select_fields.append("*" if column == "*" else f"{col_expr} AS {_quote_identifier(alias_col)}")
        elif func == "COUNT":
            select_fields.append(f"COUNT({'*' if column == '*' else col_expr}) AS {_quote_identifier(alias_col)}")
        else:
            select_fields.append(f"{func}({col_expr}) AS {_quote_identifier(alias_col)}")

    parts: List[str] = [f"SELECT {', '.join(select_fields)}", f"FROM {_build_from_ref(main_table, main_alias)}"]

    for join in joins:
        right_table = join.get("rightTable")
        if not right_table:
            continue
        right_alias = str(join.get("rightAlias") or right_table).strip()
        join_type = (join.get("joinType") or "LEFT").upper()
        parts.append(f"{join_type} JOIN {_build_from_ref(right_table, right_alias)}")

        on_expr = ""
        join_conditions = join.get("joinConditions") or []
        if join_conditions:
            pairs = []
            for cond in join_conditions:
                left_table = cond.get("leftAlias") or cond.get("leftTable") or join.get("leftAlias") or join.get("leftTable") or main_alias
                left_col = cond.get("leftColumn")
                right_col = cond.get("rightColumn")
                if left_table and left_col and right_col:
                    pairs.append(f"{left_table}.{left_col} = {right_alias}.{right_col}")
            on_expr = " AND ".join(pairs)
        else:
            left_table = join.get("leftAlias") or join.get("leftTable") or main_alias
            left_column = join.get("leftColumn")
            right_column = join.get("rightColumn")
            if left_table and left_column and right_column:
                on_expr = f"{left_table}.{left_column} = {right_alias}.{right_column}"
        if on_expr:
            parts[-1] += f" ON {on_expr}"

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

    if where_filters:
        if len(where_filters) == 1 and isinstance(where_filters[0], dict) and _is_filter_tree_node(where_filters[0]):
            where_clause = _build_filter_tree_clause(
                where_filters[0],
                for_having=False,
                is_multi_table=is_multi_table,
                main_table=main_alias,
                dimension_expr_map=dimension_expr_map,
                aggregations=aggregations,
                agg_by_id=agg_by_id,
            )
            if where_clause:
                parts.append(f"WHERE {where_clause}")
        else:
            where_clauses = []
            for idx, f in enumerate(where_filters):
                logic = str(f.get("logic", "AND")).upper()
                clause = _build_where_leaf_clause(f, is_multi_table, dimension_expr_map, main_alias)
                where_clauses.append(clause if idx == 0 else f"{logic} {clause}")
            parts.append(f"WHERE {' '.join(where_clauses)}")

    has_real_aggregation = (
        any(_is_real_aggregation_func(a.get("func")) for a in aggregations)
        or any(_filter_has_real_aggregation(f) for f in having_filters)
        or any(_order_by_has_real_aggregation(o) for o in order_by)
    )
    if dimensions and has_real_aggregation:
        group_fields = [group_expr_map[(_effective_table_ref(d, fallback=main_alias), d.get("column", ""))] for d in dimensions]
        parts.append(f"GROUP BY {', '.join(group_fields)}")

    def get_agg_expr(
        agg_func: str,
        table: str,
        column: str,
        agg_id: Optional[str],
        expression: Optional[str] = None,
        physical_table: Optional[str] = None,
    ) -> str:
        matched = agg_by_id.get(str(agg_id)) if agg_id else None
        if not matched and not (expression or "").strip():
            for a in aggregations:
                if _effective_table_ref(a, fallback=main_alias) == table and a.get("column") == column and (a.get("func") or "").upper() == agg_func:
                    matched = a
                    break

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

    if having_filters:
        if len(having_filters) == 1 and isinstance(having_filters[0], dict) and _is_filter_tree_node(having_filters[0]):
            having_clause = _build_filter_tree_clause(
                having_filters[0],
                for_having=True,
                is_multi_table=is_multi_table,
                main_table=main_alias,
                dimension_expr_map=dimension_expr_map,
                aggregations=aggregations,
                agg_by_id=agg_by_id,
            )
            if having_clause:
                parts.append(f"HAVING {having_clause}")
        else:
            having_clauses = []
            for idx, f in enumerate(having_filters):
                agg_func = str(f.get("aggFunc", "RAW")).upper()
                table = _effective_table_ref(f, fallback=main_alias)
                column = f.get("column", "")
                operator = str(f.get("operator", "=")).upper()
                value_type = f.get("value_type")
                logic = str(f.get("logic", "AND")).upper()
                physical_table = _physical_table_ref(f, fallback=table)
                agg_expr = get_agg_expr(agg_func, table, column, f.get("aggId"), f.get("expression"), physical_table)
                if operator in ("IS NULL", "IS NOT NULL"):
                    clause = f"{agg_expr} {operator}"
                elif (
                    f.get("valueType") == "field_ref"
                    and (f.get("rightTable") or "").strip()
                    and (f.get("rightColumn") or "").strip()
                ):
                    right_expr = _get_column_expr(
                        str(f.get("rightTableAlias") or f.get("rightTable")).strip(),
                        str(f.get("rightColumn")).strip(),
                        is_multi_table,
                    )
                    clause = f"{agg_expr} {operator} {right_expr}"
                elif operator in ("IN", "NOT IN") and (f.get("rightExpression") or "").strip():
                    expr = str(f.get("rightExpression")).strip()
                    clause = f"{agg_expr} {operator} ({expr})"
                elif f.get("valueType") == "expression" and (f.get("rightExpression") or "").strip():
                    expr = str(f.get("rightExpression")).strip()
                    if operator in ("IN", "NOT IN"):
                        clause = f"{agg_expr} {operator} ({expr})"
                    else:
                        clause = f"{agg_expr} {operator} {expr}"
                elif operator == "EMPTY STRING":
                    clause = f"{agg_expr} = ''"
                elif operator == "STARTS WITH":
                    start_value = str(f.get("value", "")).replace("'", "''")
                    clause = f"{agg_expr} LIKE '{start_value}%'"
                elif operator == "ENDS WITH":
                    end_value = str(f.get("value", "")).replace("'", "''")
                    clause = f"{agg_expr} LIKE '%{end_value}'"
                elif operator in ("LIKE", "NOT LIKE"):
                    like_value = str(f.get("value", "")).replace("'", "''")
                    normalized = like_value if ("%" in like_value or "_" in like_value) else f"%{like_value}%"
                    clause = f"{agg_expr} {operator} '{normalized}'"
                elif operator in ("IN", "NOT IN"):
                    clause = f"{agg_expr} {operator} {_sql_list_literal(f.get('value'), value_type)}"
                elif operator == "BETWEEN" and isinstance(f.get("value"), list) and len(f.get("value")) == 2:
                    left = _sql_literal(f.get("value")[0], _value_type_for_index(value_type, 0))
                    right = _sql_literal(f.get("value")[1], _value_type_for_index(value_type, 1))
                    clause = f"{agg_expr} BETWEEN {left} AND {right}"
                else:
                    clause = f"{agg_expr} {operator} {_sql_literal(f.get('value'), value_type)}"
                having_clauses.append(clause if idx == 0 else f"{logic} {clause}")
            parts.append(f"HAVING {' '.join(having_clauses)}")

    if order_by:
        order_fields = []
        for o in order_by:
            direction = str(o.get("direction", "ASC")).upper()
            table = _effective_table_ref(o, fallback=main_alias)
            column = o.get("column", "")
            agg_func = o.get("aggFunc")
            if agg_func:
                physical_table = _physical_table_ref(o, fallback=table)
                order_fields.append(f"{get_agg_expr(str(agg_func).upper(), table, column, o.get('aggId'), o.get('expression'), physical_table)} {direction}")
            elif (table, column) in dimension_alias_map:
                order_fields.append(f"{_quote_identifier(dimension_alias_map[(table, column)])} {direction}")
            elif (table, column) in group_expr_map:
                order_fields.append(f"{group_expr_map[(table, column)]} {direction}")
            elif (table, column) in dimension_expr_map:
                order_fields.append(f"{dimension_expr_map[(table, column)]} {direction}")
            elif has_real_aggregation:
                matched_aggs = [
                    a
                    for a in aggregations
                    if (a.get("func") or "RAW").upper() != "RAW"
                    and _effective_table_ref(a, fallback=main_alias) == table
                    and a.get("column", "") == column
                ]
                if len(matched_aggs) == 1:
                    matched_agg = matched_aggs[0]
                    order_fields.append(
                        f"{get_agg_expr(str(matched_agg.get('func') or '').upper(), table, column, matched_agg.get('id'))} {direction}"
                    )
                    continue
                raise ValueError("聚合查询不能按未分组的明细字段排序，请改用已选择的维度或聚合指标排序")
            else:
                order_fields.append(f"{table}.{column} {direction}")
        parts.append(f"ORDER BY {', '.join(order_fields)}")

    if offset is not None:
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("OFFSET 必须为非负整数")
        if limit is None:
            raise ValueError("OFFSET 必须配合 LIMIT 使用")
    if limit is not None or offset is not None:
        parts.append(sql_dialect.limit_offset(int(limit), int(offset or 0)))
    return "\n".join(parts)
