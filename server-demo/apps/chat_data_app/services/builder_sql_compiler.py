"""
Builder DSL -> SQL 编译器

将前端可视化查询的 builder_config 编译为 SQL，供后端统一执行。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from .compare_sql_builder import build_compare_sql


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
    safe = (name or "").replace("`", "``")
    return f"`{safe}`"


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


def _build_where_leaf_clause(
    leaf: Dict[str, Any],
    is_multi_table: bool,
    dimension_expr_map: Dict[Tuple[str, str], str],
    main_table: str,
) -> str:
    table = leaf.get("table", main_table)
    column = leaf.get("column", "")
    operator = str(leaf.get("operator", "=")).upper()
    value_type = leaf.get("value_type")

    if (leaf.get("expression") or "").strip():
        where_expr = _normalize_raw_expression(leaf.get("expression"), table, column, is_multi_table)
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
            str(leaf.get("rightTable")).strip(),
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
    table = leaf.get("table", main_table)
    column = leaf.get("column", "")
    operator = str(leaf.get("operator", "=")).upper()
    value_type = leaf.get("value_type")
    agg_id = leaf.get("aggId")

    matched = agg_by_id.get(str(agg_id)) if agg_id else None
    if not matched:
        for agg in aggregations:
            if agg.get("table") == table and agg.get("column") == column and (agg.get("func") or "").upper() == agg_func:
                matched = agg
                break

    matched_expr = (matched or {}).get("expression")
    if agg_func == "RAW" and (matched_expr or "").strip():
        agg_expr = matched_expr.strip()
    elif agg_func != "RAW" and (matched_expr or "").strip() and _is_aggregate_expression(matched_expr):
        agg_expr = matched_expr.strip()
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
            str(leaf.get("rightTable")).strip(),
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


def _normalize_raw_expression(expression: Optional[str], table: str, column: str, is_multi_table: bool) -> str:
    expr = (expression or "").strip()
    if not expr:
        return _get_column_expr(table, column, is_multi_table)
    if _is_simple_column_expr(expr):
        return _get_column_expr(table, expr.replace("`", ""), is_multi_table)
    if _is_qualified_column_expr(expr):
        if is_multi_table:
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


def _normalize_aggregate_expression(expression: Optional[str], table: str, column: str, is_multi_table: bool) -> str:
    expr = (expression or "").strip()
    if not expr:
        return expr
    match = AGG_EXPR_PATTERN.match(expr)
    if not match:
        return expr
    func = (match.group(1) or "").upper()
    distinct = "DISTINCT " if (match.group(2) or "").strip() else ""
    inner = (match.group(3) or "").strip()
    normalized_inner = _normalize_raw_expression(inner, table, column, is_multi_table)
    return f"{func}({distinct}{normalized_inner})"


def _build_join_clauses(main_table: str, joins: List[Dict[str, Any]]) -> List[str]:
    parts: List[str] = [f"FROM {main_table}"]
    for join in joins:
        right_table = join.get("rightTable")
        if not right_table:
            continue
        join_type = (join.get("joinType") or "LEFT").upper()
        clause = f"{join_type} JOIN {right_table}"

        on_expr = ""
        join_conditions = join.get("joinConditions") or []
        if join_conditions:
            pairs = []
            for cond in join_conditions:
                left_table = cond.get("leftTable") or join.get("leftTable") or main_table
                left_col = cond.get("leftColumn")
                right_col = cond.get("rightColumn")
                if left_table and left_col and right_col:
                    pairs.append(f"{left_table}.{left_col} = {right_table}.{right_col}")
            on_expr = " AND ".join(pairs)
        else:
            left_table = join.get("leftTable") or main_table
            left_column = join.get("leftColumn")
            right_column = join.get("rightColumn")
            if left_table and left_column and right_column:
                on_expr = f"{left_table}.{left_column} = {right_table}.{right_column}"
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
    dimensions: List[Dict[str, Any]] = builder_config.get("dimensions") or []
    aggregations: List[Dict[str, Any]] = builder_config.get("aggregations") or []
    joins: List[Dict[str, Any]] = builder_config.get("joins") or []
    filters: List[Dict[str, Any]] = builder_config.get("filters") or []
    order_by: List[Dict[str, Any]] = builder_config.get("orderBy") or []
    limit = builder_config.get("limit") or 100

    if not main_table:
        raise ValueError("builder_config 缺少 mainTable")
    if not dimensions and not aggregations:
        raise ValueError("请至少选择一个维度或指标")

    compare = builder_config.get("compare") or {}
    compare_type = str(compare.get("type") or "").strip().lower()
    compare_strategy = str(compare.get("strategy") or "calc").strip().lower()
    if compare_type:
        if compare_strategy != "calc":
            raise ValueError(f"同比/环比当前仅支持 strategy=calc，暂不支持 {compare_strategy}")
        if order_by:
            raise ValueError("同比/环比当前不支持排序，请使用默认时间顺序")
        return build_compare_sql(
            builder_config,
            dialect=dialect,
            quote_identifier=_quote_identifier,
            to_sql_alias=_to_sql_alias,
            normalize_raw_expression=_normalize_raw_expression,
            is_aggregate_expression=_is_aggregate_expression,
            normalize_aggregate_expression=_normalize_aggregate_expression,
            get_column_expr=_get_column_expr,
            is_filter_tree_node=_is_filter_tree_node,
            get_filter_tree_scope=_get_filter_tree_scope,
            build_filter_tree_clause=_build_filter_tree_clause,
            build_where_leaf_clause=_build_where_leaf_clause,
        )

    has_raw_metric = any(str(a.get("func") or "RAW").upper() == "RAW" for a in aggregations)
    has_agg_metric = any(str(a.get("func") or "RAW").upper() != "RAW" for a in aggregations)
    if has_raw_metric and has_agg_metric:
        raise ValueError("指标区不支持原始值与聚合函数混用，请统一为原始值或聚合函数")

    selected_tables = [main_table] + [j.get("rightTable") for j in joins if j.get("rightTable")]
    is_multi_table = len(selected_tables) > 1
    dialect_name = (dialect or "mysql").strip().lower()

    def get_time_transform_expr(transform: str, dim_expr: str) -> str:
        if dialect_name == "hive":
            transform_map = {
                "year": f"DATE_FORMAT({dim_expr}, 'yyyy')",
                "quarter": f"CONCAT(YEAR({dim_expr}), '-Q', QUARTER({dim_expr}))",
                "month": f"DATE_FORMAT({dim_expr}, 'yyyy-MM')",
                "week": f"CONCAT(CAST(YEAR({dim_expr}) AS STRING), '-W', LPAD(CAST(WEEKOFYEAR({dim_expr}) AS STRING), 2, '0'))",
                "day": f"DATE_FORMAT({dim_expr}, 'yyyy-MM-dd')",
            }
            return transform_map.get(transform, dim_expr)
        transform_map = {
            "year": f"YEAR({dim_expr})",
            "quarter": f"QUARTER({dim_expr})",
            "month": f"DATE_FORMAT({dim_expr}, '%Y-%m')",
            "week": f"YEARWEEK({dim_expr})",
            "day": f"DATE({dim_expr})",
        }
        return transform_map.get(transform, dim_expr)

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
    agg_by_id: Dict[str, Dict[str, Any]] = {str(a.get("id")): a for a in aggregations if a.get("id")}

    for dim in dimensions:
        table = dim.get("table", main_table)
        column = dim.get("column", "")
        transform = dim.get("transform") or "raw"
        display_name = dim.get("displayName") or dim.get("alias") or column
        dim_expr = _normalize_raw_expression(dim.get("expression"), table, column, is_multi_table)
        dim_alias = get_unique_alias(display_name, column or "col")

        if transform != "raw":
            transformed_expr = get_time_transform_expr(transform, dim_expr)
            transformed_alias = get_unique_alias(f"{dim_alias}_{transform}", f"{column}_{transform}")
            select_fields.append(f"{transformed_expr} AS {_quote_identifier(transformed_alias)}")
            dimension_expr_map[(table, column)] = transformed_expr
        else:
            select_fields.append(f"{dim_expr} AS {_quote_identifier(dim_alias)}")
            dimension_expr_map[(table, column)] = dim_expr

    for agg in aggregations:
        table = agg.get("table", main_table)
        column = agg.get("column", "")
        func = (agg.get("func") or "RAW").upper()
        expression = agg.get("expression")
        display_name = agg.get("displayName") or agg.get("alias") or ("all" if column == "*" else column)
        alias_col = get_unique_alias(display_name, "all" if column == "*" else (column or "metric"))

        if func == "RAW" and (expression or "").strip():
            select_fields.append(
                f"{_normalize_raw_expression(expression, table, column, is_multi_table)} AS {_quote_identifier(alias_col)}"
            )
            continue
        if func != "RAW" and (expression or "").strip() and _is_aggregate_expression(expression):
            normalized_agg_expr = _normalize_aggregate_expression(expression, table, column, is_multi_table)
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

    parts: List[str] = [f"SELECT {', '.join(select_fields)}", f"FROM {main_table}"]

    for join in joins:
        right_table = join.get("rightTable")
        if not right_table:
            continue
        join_type = (join.get("joinType") or "LEFT").upper()
        parts.append(f"{join_type} JOIN {right_table}")

        on_expr = ""
        join_conditions = join.get("joinConditions") or []
        if join_conditions:
            pairs = []
            for cond in join_conditions:
                left_table = cond.get("leftTable") or join.get("leftTable") or main_table
                left_col = cond.get("leftColumn")
                right_col = cond.get("rightColumn")
                if left_table and left_col and right_col:
                    pairs.append(f"{left_table}.{left_col} = {right_table}.{right_col}")
            on_expr = " AND ".join(pairs)
        else:
            left_table = join.get("leftTable") or main_table
            left_column = join.get("leftColumn")
            right_column = join.get("rightColumn")
            if left_table and left_column and right_column:
                on_expr = f"{left_table}.{left_column} = {right_table}.{right_column}"
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
                main_table=main_table,
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
                clause = _build_where_leaf_clause(f, is_multi_table, dimension_expr_map, main_table)
                where_clauses.append(clause if idx == 0 else f"{logic} {clause}")
            parts.append(f"WHERE {' '.join(where_clauses)}")

    has_real_aggregation = any((a.get("func") or "RAW").upper() != "RAW" for a in aggregations)
    if dimensions and has_real_aggregation:
        group_fields = [dimension_expr_map[(d.get("table", main_table), d.get("column", ""))] for d in dimensions]
        parts.append(f"GROUP BY {', '.join(group_fields)}")

    def get_agg_expr(agg_func: str, table: str, column: str, agg_id: Optional[str]) -> str:
        matched = agg_by_id.get(str(agg_id)) if agg_id else None
        if not matched:
            for a in aggregations:
                if a.get("table") == table and a.get("column") == column and (a.get("func") or "").upper() == agg_func:
                    matched = a
                    break

        matched_expr = (matched or {}).get("expression")
        if agg_func == "RAW" and (matched_expr or "").strip():
            return matched_expr.strip()
        if agg_func != "RAW" and (matched_expr or "").strip() and _is_aggregate_expression(matched_expr):
            return _normalize_aggregate_expression(matched_expr, table, column, is_multi_table)

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
                main_table=main_table,
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
                table = f.get("table", main_table)
                column = f.get("column", "")
                operator = str(f.get("operator", "=")).upper()
                value_type = f.get("value_type")
                logic = str(f.get("logic", "AND")).upper()
                agg_expr = get_agg_expr(agg_func, table, column, f.get("aggId"))
                if operator in ("IS NULL", "IS NOT NULL"):
                    clause = f"{agg_expr} {operator}"
                elif (
                    f.get("valueType") == "field_ref"
                    and (f.get("rightTable") or "").strip()
                    and (f.get("rightColumn") or "").strip()
                ):
                    right_expr = _get_column_expr(
                        str(f.get("rightTable")).strip(),
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
            table = o.get("table", main_table)
            column = o.get("column", "")
            agg_func = o.get("aggFunc")
            if agg_func:
                order_fields.append(f"{get_agg_expr(str(agg_func).upper(), table, column, o.get('aggId'))} {direction}")
            elif (table, column) in dimension_expr_map:
                order_fields.append(f"{dimension_expr_map[(table, column)]} {direction}")
            else:
                order_fields.append(f"{table}.{column} {direction}")
        parts.append(f"ORDER BY {', '.join(order_fields)}")

    parts.append(f"LIMIT {int(limit)}")
    return "\n".join(parts)
