"""语义 SQL 的 WHERE/HAVING 过滤编译。"""

import re
from typing import Any, Callable, Dict, List, Optional, Tuple


def _is_numeric_literal(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value).strip())
        return bool(str(value).strip())
    except (TypeError, ValueError):
        return False


def _has_balanced_parentheses(expression: str) -> bool:
    balance = 0
    in_single = in_double = escaped = False
    for char in expression:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if char == "(":
                balance += 1
            elif char == ")":
                balance -= 1
                if balance < 0:
                    return False
    return balance == 0 and not in_single and not in_double


def _is_sql_function(value: Any, safe_functions: set[str]) -> bool:
    if not isinstance(value, str):
        return False
    raw = value.strip()
    match = re.match(r"^([a-z_][a-z0-9_]*)\s*\(.*\)$", raw, re.IGNORECASE)
    if not match or match.group(1).lower() not in safe_functions:
        return False
    return not any(token in raw for token in (";", "--", "/*", "*/")) and _has_balanced_parentheses(raw)


def _sql_literal(value: Any, value_type: Any = None, safe_functions: Optional[set[str]] = None) -> str:
    if value is None:
        return "NULL"
    if _is_numeric_literal(value):
        return str(value).strip()
    raw = str(value).strip()
    normalized_type = str(value_type or "").strip().lower()
    unwrapped = raw[1:-1].strip() if len(raw) >= 3 and raw[0] == raw[-1] and raw[0] in "'\"" else ""
    functions = safe_functions or set()
    if normalized_type == "literal":
        return f"'{str(value).replace(chr(39), chr(39) * 2)}'"
    if normalized_type == "sql_function":
        if _is_sql_function(raw, functions):
            return raw
        if unwrapped and _is_sql_function(unwrapped, functions):
            return unwrapped
    elif _is_sql_function(raw, functions):
        return raw
    elif unwrapped and _is_sql_function(unwrapped, functions):
        return unwrapped
    return f"'{str(value).replace(chr(39), chr(39) * 2)}'"


def _value_type_for_index(value_type: Any, index: int) -> Optional[str]:
    if isinstance(value_type, list):
        return value_type[index] if index < len(value_type) else None
    return value_type if isinstance(value_type, str) else None


def _sql_list_literal(values: Any, value_type: Any, safe_functions: set[str]) -> str:
    if not isinstance(values, list):
        return f"({_sql_literal(values, value_type, safe_functions)})"
    if not values:
        return "(NULL)"
    return "(" + ", ".join(
        _sql_literal(value, _value_type_for_index(value_type, index), safe_functions)
        for index, value in enumerate(values)
    ) + ")"


def _effective_table_ref(item: Dict[str, Any], fallback: str) -> str:
    return str(item.get("tableAlias") or item.get("table") or fallback or "").strip()


def _physical_table_ref(item: Dict[str, Any], fallback: str) -> str:
    return str(item.get("table") or fallback or "").strip()


def _operator_clause(
    expression: str,
    leaf: Dict[str, Any],
    *,
    right_expression: Optional[str] = None,
    safe_functions: set[str],
) -> str:
    operator = str(leaf.get("operator", "=")).upper()
    if operator in ("IS NULL", "IS NOT NULL"):
        return f"{expression} {operator}"
    if leaf.get("valueType") == "field_ref" and leaf.get("rightTable") and leaf.get("rightColumn"):
        return f"{expression} {operator} {right_expression}"
    if right_expression is not None:
        return f"{expression} {operator} {right_expression}"
    if operator in ("IN", "NOT IN") and leaf.get("rightExpression"):
        return f"{expression} {operator} ({str(leaf['rightExpression']).strip()})"
    if leaf.get("valueType") == "expression" and leaf.get("rightExpression"):
        return f"{expression} {operator} {str(leaf['rightExpression']).strip()}"
    if operator == "EMPTY STRING":
        return f"{expression} = ''"
    if operator == "STARTS WITH":
        return f"{expression} LIKE '{str(leaf.get('value', '')).replace(chr(39), chr(39) * 2)}%'"
    if operator == "ENDS WITH":
        return f"{expression} LIKE '%{str(leaf.get('value', '')).replace(chr(39), chr(39) * 2)}'"
    if operator in ("LIKE", "NOT LIKE"):
        value = str(leaf.get("value", "")).replace(chr(39), chr(39) * 2)
        value = value if "%" in value or "_" in value else f"%{value}%"
        return f"{expression} {operator} '{value}'"
    if operator in ("IN", "NOT IN"):
        return f"{expression} {operator} {_sql_list_literal(leaf.get('value'), leaf.get('value_type'), safe_functions)}"
    if operator == "BETWEEN" and isinstance(leaf.get("value"), list) and len(leaf["value"]) == 2:
        values = leaf["value"]
        left = _sql_literal(values[0], _value_type_for_index(leaf.get("value_type"), 0), safe_functions)
        right = _sql_literal(values[1], _value_type_for_index(leaf.get("value_type"), 1), safe_functions)
        return f"{expression} BETWEEN {left} AND {right}"
    return f"{expression} {operator} {_sql_literal(leaf.get('value'), leaf.get('value_type'), safe_functions)}"


def build_where_leaf_clause(
    leaf: Dict[str, Any],
    *,
    is_multi_table: bool,
    dimension_expr_map: Dict[Tuple[str, str], str],
    main_table: str,
    normalize_raw_expression: Callable[..., str],
    get_column_expr: Callable[..., str],
    safe_functions: set[str],
) -> str:
    table = _effective_table_ref(leaf, main_table)
    physical_table = _physical_table_ref(leaf, table)
    column = leaf.get("column", "")
    if (leaf.get("expression") or "").strip():
        expression = normalize_raw_expression(leaf.get("expression"), table, column, is_multi_table, physical_table)
    else:
        expression = dimension_expr_map.get((table, column)) or get_column_expr(table, column, is_multi_table)
    right_expression = None
    if leaf.get("valueType") == "field_ref" and leaf.get("rightTable") and leaf.get("rightColumn"):
        right_expression = get_column_expr(
            str(leaf.get("rightTableAlias") or leaf.get("rightTable")).strip(),
            str(leaf.get("rightColumn")).strip(),
            is_multi_table,
        )
    return _operator_clause(expression, leaf, right_expression=right_expression, safe_functions=safe_functions)


def _build_aggregate_expr(
    leaf: Dict[str, Any],
    *,
    is_multi_table: bool,
    main_table: str,
    aggregations: List[Dict[str, Any]],
    agg_by_id: Dict[str, Dict[str, Any]],
    normalize_raw_expression: Callable[..., str],
    normalize_aggregate_expression: Callable[..., str],
    get_column_expr: Callable[..., str],
    is_aggregate_expression: Callable[[Optional[str]], bool],
    safe_functions: set[str],
    normalize_expression: bool = False,
) -> str:
    agg_func = str(leaf.get("aggFunc", "RAW")).upper()
    table = _effective_table_ref(leaf, main_table)
    column = leaf.get("column", "")
    matched = agg_by_id.get(str(leaf.get("aggId"))) if leaf.get("aggId") else None
    if not matched and not str(leaf.get("expression") or "").strip():
        matched = next((item for item in aggregations if _effective_table_ref(item, main_table) == table and item.get("column") == column and str(item.get("func") or "").upper() == agg_func), None)
    physical_table = _physical_table_ref(matched or leaf, table)
    expression = (matched or {}).get("expression") or leaf.get("expression")
    if agg_func == "RAW" and str(expression or "").strip():
        if normalize_expression:
            return normalize_raw_expression(expression, table, column, is_multi_table, physical_table)
        return _rewrite_expression_table_qualifier(expression.strip(), table, physical_table, is_multi_table)
    if agg_func != "RAW" and str(expression or "").strip() and is_aggregate_expression(expression):
        if normalize_expression:
            return normalize_aggregate_expression(expression, table, column, is_multi_table, physical_table)
        return _rewrite_expression_table_qualifier(expression.strip(), table, physical_table, is_multi_table)
    column_expression = "*" if column == "*" else get_column_expr(table, column, is_multi_table)
    if agg_func == "RAW":
        return column_expression
    if agg_func == "COUNT_DISTINCT":
        return f"COUNT(DISTINCT {column_expression})"
    return f"{agg_func}({column_expression})"


def _rewrite_expression_table_qualifier(expression: str, table: str, physical_table: str, is_multi_table: bool) -> str:
    if not table or not physical_table or table == physical_table:
        return expression
    escaped = re.escape(physical_table)
    return re.sub(rf"(?<![\w`])`?{escaped}`?\.", f"{table}.", expression)


def build_having_leaf_clause(leaf: Dict[str, Any], **kwargs: Any) -> str:
    expression = _build_aggregate_expr(
        leaf,
        is_multi_table=kwargs["is_multi_table"],
        main_table=kwargs["main_table"],
        aggregations=kwargs["aggregations"],
        agg_by_id=kwargs["agg_by_id"],
        normalize_raw_expression=kwargs["normalize_raw_expression"],
        normalize_aggregate_expression=kwargs["normalize_aggregate_expression"],
        get_column_expr=kwargs["get_column_expr"],
        is_aggregate_expression=kwargs["is_aggregate_expression"],
        safe_functions=kwargs["safe_functions"],
        normalize_expression=kwargs.get("normalize_expression", False),
    )
    right_expression = None
    operand = leaf.get("rightOperand")
    if isinstance(operand, dict):
        right_expression = build_having_operand_expr(
            operand,
            is_multi_table=kwargs["is_multi_table"],
            main_table=kwargs["main_table"],
            aggregations=kwargs["aggregations"],
            agg_by_id=kwargs["agg_by_id"],
            normalize_raw_expression=kwargs["normalize_raw_expression"],
            normalize_aggregate_expression=kwargs["normalize_aggregate_expression"],
            get_column_expr=kwargs["get_column_expr"],
            is_aggregate_expression=kwargs["is_aggregate_expression"],
            safe_functions=kwargs["safe_functions"],
            normalize_expression=kwargs.get("normalize_expression", False),
        )
    elif leaf.get("valueType") == "field_ref" and leaf.get("rightTable") and leaf.get("rightColumn"):
        right_expression = kwargs["get_column_expr"](
            str(leaf.get("rightTableAlias") or leaf.get("rightTable")).strip(),
            str(leaf.get("rightColumn")).strip(),
            kwargs["is_multi_table"],
        )
    return _operator_clause(expression, leaf, right_expression=right_expression, safe_functions=kwargs["safe_functions"])


def build_having_operand_expr(operand: Dict[str, Any], **kwargs: Any) -> str:
    operand_type = str(operand.get("type") or "").strip().lower()
    if operand_type == "metric":
        return _build_aggregate_expr(
            operand,
            is_multi_table=kwargs["is_multi_table"],
            main_table=kwargs["main_table"],
            aggregations=kwargs["aggregations"],
            agg_by_id=kwargs["agg_by_id"],
            normalize_raw_expression=kwargs["normalize_raw_expression"],
            normalize_aggregate_expression=kwargs["normalize_aggregate_expression"],
            get_column_expr=kwargs["get_column_expr"],
            is_aggregate_expression=kwargs["is_aggregate_expression"],
            safe_functions=kwargs["safe_functions"],
            normalize_expression=kwargs.get("normalize_expression", False),
        )
    if operand_type == "literal":
        return _sql_literal(operand.get("value"), "literal", kwargs["safe_functions"])
    if operand_type == "binary":
        left = build_having_operand_expr(operand.get("left") or {}, **kwargs)
        right = build_having_operand_expr(operand.get("right") or {}, **kwargs)
        return f"({left} {str(operand.get('operator') or '').strip()} {right})"
    return "NULL"


def build_filter_tree_clause(node: Dict[str, Any], *, for_having: bool, is_root: bool = False, **kwargs: Any) -> str:
    key = "and" if "and" in node else "or" if "or" in node else ""
    if key:
        children = [build_filter_tree_clause(child, for_having=for_having, is_root=False, **kwargs) for child in node.get(key) or [] if isinstance(child, dict)]
        children = [child for child in children if child]
        if not children:
            return ""
        if len(children) == 1:
            return children[0]
        clause = f" {'AND' if key == 'and' else 'OR'} ".join(children)
        return clause if is_root else f"({clause})"
    if for_having:
        return build_having_leaf_clause(node, **kwargs)
    return build_where_leaf_clause(
        node,
        is_multi_table=kwargs["is_multi_table"],
        dimension_expr_map=kwargs["dimension_expr_map"],
        main_table=kwargs["main_table"],
        normalize_raw_expression=kwargs["normalize_raw_expression"],
        get_column_expr=kwargs["get_column_expr"],
        safe_functions=kwargs["safe_functions"],
    )
