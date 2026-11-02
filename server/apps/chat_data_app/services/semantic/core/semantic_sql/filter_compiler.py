"""语义 SQL 的 WHERE/HAVING 过滤编译。"""

import re
from collections.abc import Callable
from typing import Any

#: 过滤操作符唯一白名单：WHERE/HAVING、AI/BI 各链路统一在编译层收口。
#: operator 字段来自请求侧，不允许携带任意 SQL 片段进入拼接。
FILTER_OPERATOR_WHITELIST = frozenset(
    {
        "=",
        "!=",
        "<>",
        ">",
        "<",
        ">=",
        "<=",
        "IN",
        "NOT IN",
        "BETWEEN",
        "LIKE",
        "NOT LIKE",
        "STARTS WITH",
        "ENDS WITH",
        "IS NULL",
        "IS NOT NULL",
        "EMPTY STRING",
    }
)

#: 自由 SQL 表达式黑名单：注释、分号与写操作关键字。
#: WHERE/HAVING 的 rightExpression、维度/指标左侧 expression 都必须过这道检查；
#: 子查询（IN (SELECT ...)）是 Builder 的显式产品能力，不在黑名单内，
#: 其安全由执行层 sql_guard（仅 SELECT + 危险函数黑名单）兜底。
UNSAFE_EXPRESSION_PATTERN = re.compile(
    r";|--|/\*|\*/|\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|MERGE|CALL|EXECUTE)\b",
    re.IGNORECASE,
)


def ensure_safe_sql_expression(expression: str | None, *, label: str = "表达式") -> str:
    """校验自由 SQL 表达式不含注释、分号或写操作关键字；不合法抛 ValueError。"""
    text = str(expression or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空")
    if UNSAFE_EXPRESSION_PATTERN.search(text):
        raise ValueError(f"{label}只允许单条只读 SQL 表达式，不允许注释、分号或写操作")
    return text


def _is_numeric_literal(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value).strip())
        return bool(str(value).strip())
    except (TypeError, ValueError):
        return False


def _scan_parenthesis_char(
    char: str,
    escaped: bool,
    in_single: bool,
    in_double: bool,
    balance: int,
) -> tuple[bool, bool, bool, int]:
    """按字符推进括号/引号扫描状态；字符串内的内容一律不计入括号。"""
    if escaped:
        return in_single, in_double, False, balance
    if char == "\\":
        return in_single, in_double, True, balance
    if char == "'" and not in_double:
        return (not in_single), in_double, escaped, balance
    if char == '"' and not in_single:
        return in_single, (not in_double), escaped, balance
    if in_single or in_double:
        return in_single, in_double, escaped, balance
    if char == "(":
        return in_single, in_double, escaped, balance + 1
    if char == ")":
        return in_single, in_double, escaped, balance - 1
    return in_single, in_double, escaped, balance


def _has_balanced_parentheses(expression: str) -> bool:
    balance = 0
    in_single = in_double = escaped = False
    for char in expression:
        in_single, in_double, escaped, balance = _scan_parenthesis_char(
            char,
            escaped,
            in_single,
            in_double,
            balance,
        )
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


def _escape_sql_string(value: Any) -> str:
    """单引号包裹并按 SQL 规则转义：先反斜杠、再单引号。

    MySQL/Hive 默认把反斜杠当转义符：只双写单引号时，反斜杠加单引号会被
    数据库解释成“字面单引号”，攻击者可用反斜杠加引号提前闭合字符串并注入
    条件。因此必须先转义反斜杠，再双写单引号，顺序不能反。
    """
    escaped = str(value).replace("\\", "\\\\").replace(chr(39), chr(39) * 2)
    return f"'{escaped}'"


def _unwrap_quoted_literal(raw: str) -> str:
    """剥去成对的首尾引号；非成对引号返回空串。"""
    return raw[1:-1].strip() if len(raw) >= 3 and raw[0] == raw[-1] and raw[0] in "'\"" else ""


def _parse_quoted_sql_literal(raw: str) -> str | None:
    """值整体为成对引号包裹的 SQL 字面量时解析出裸值；非包裹形态返回 None。

    筛选值按 SQL 原生语法填写（"'paid'" 即字符串 paid）：包裹形态先解析出
    裸值再统一 escape，避免二次包裹产出 '''paid'''（等值比较恒假）。
    单引号形态内部只允许 ``''`` 转义对（'O''Brien' → O'Brien），出现孤立
    单引号即非法字面量形态返回 None，交回裸值 escape 兜底，不放大错误。
    """
    inner = _unwrap_quoted_literal(raw)
    if not inner and raw not in ("''", '""'):
        return None
    if not raw.startswith("'"):
        return inner
    index = 0
    while index < len(inner):
        if inner[index] != "'":
            index += 1
            continue
        if index + 1 < len(inner) and inner[index + 1] == "'":
            index += 2
            continue
        return None
    return inner.replace("''", "'")


def _sql_literal(value: Any, value_type: Any = None, safe_functions: set[str] | None = None) -> str:
    if value is None:
        return "NULL"
    if _is_numeric_literal(value):
        return str(value).strip()
    raw = str(value).strip()
    normalized_type = str(value_type or "").strip().lower()
    if normalized_type == "literal":
        return _escape_sql_string(value)
    # 显式声明 sql_function 与未声明时行为一致：raw 或去引号形态命中安全函数白名单即原样输出
    unwrapped = _unwrap_quoted_literal(raw)
    functions = safe_functions or set()
    if _is_sql_function(raw, functions):
        return raw
    if unwrapped and _is_sql_function(unwrapped, functions):
        return unwrapped
    # SQL 原生语法口径：值已是成对引号包裹的字面量形态时解析出裸值再 escape，
    # 保证 "'paid'" 编译为 'paid' 而非 '''paid'''；非包裹形态保持原值兜底
    parsed = _parse_quoted_sql_literal(raw)
    return _escape_sql_string(value if parsed is None else parsed)


def _value_type_for_index(value_type: Any, index: int) -> str | None:
    if isinstance(value_type, list):
        return value_type[index] if index < len(value_type) else None
    return value_type if isinstance(value_type, str) else None


def _sql_list_literal(values: Any, value_type: Any, safe_functions: set[str]) -> str:
    if not isinstance(values, list):
        return f"({_sql_literal(values, value_type, safe_functions)})"
    if not values:
        return "(NULL)"
    return (
        "("
        + ", ".join(
            _sql_literal(value, _value_type_for_index(value_type, index), safe_functions)
            for index, value in enumerate(values)
        )
        + ")"
    )


def _effective_table_ref(item: dict[str, Any], fallback: str = "") -> str:
    return str(item.get("tableAlias") or item.get("table") or fallback or "").strip()


def _physical_table_ref(item: dict[str, Any], fallback: str = "") -> str:
    return str(item.get("table") or fallback or "").strip()


def _operator_clause(
    expression: str,
    leaf: dict[str, Any],
    *,
    right_expression: str | None = None,
    safe_functions: set[str],
) -> str:
    operator = str(leaf.get("operator", "=")).upper()
    if operator not in FILTER_OPERATOR_WHITELIST:
        raise ValueError(f"不支持的筛选操作符 '{operator}'。可选值：{', '.join(sorted(FILTER_OPERATOR_WHITELIST))}")
    # rightExpression 是请求侧自由文本，渲染前统一过黑名单；
    # field_ref 分支的 right_expression 由列名引用构造，不受此影响。
    raw_right_expression = str(leaf.get("rightExpression") or "").strip()
    if raw_right_expression:
        ensure_safe_sql_expression(raw_right_expression, label="rightExpression")
    if operator in ("IS NULL", "IS NOT NULL"):
        return f"{expression} {operator}"
    if (
        leaf.get("valueType") == "field_ref" and leaf.get("rightTable") and leaf.get("rightColumn")
    ) or right_expression is not None:
        return f"{expression} {operator} {right_expression}"
    if operator in ("IN", "NOT IN") and leaf.get("rightExpression"):
        return f"{expression} {operator} ({str(leaf['rightExpression']).strip()})"
    if leaf.get("valueType") == "expression" and leaf.get("rightExpression"):
        return f"{expression} {operator} {str(leaf['rightExpression']).strip()}"
    special = _render_special_operator_clause(expression, operator, leaf)
    if special is not None:
        return special
    return _render_value_operator_clause(expression, operator, leaf, safe_functions)


def _escape_like_value(value: str) -> str:
    """LIKE/STARTS WITH/ENDS WITH 的值同样先转义反斜杠，再双写单引号。"""
    return str(value).replace("\\", "\\\\").replace(chr(39), chr(39) * 2)


def _render_special_operator_clause(expression: str, operator: str, leaf: dict[str, Any]) -> str | None:
    """渲染 EMPTY STRING / STARTS WITH / ENDS WITH / LIKE 族；不匹配返回 None。"""
    if operator == "EMPTY STRING":
        return f"{expression} = ''"
    if operator == "STARTS WITH":
        return f"{expression} LIKE '{_escape_like_value(str(leaf.get('value', '')))}%'"
    if operator == "ENDS WITH":
        return f"{expression} LIKE '%{_escape_like_value(str(leaf.get('value', '')))}'"
    if operator in ("LIKE", "NOT LIKE"):
        value = _escape_like_value(str(leaf.get("value", "")))
        value = value if "%" in value or "_" in value else f"%{value}%"
        return f"{expression} {operator} '{value}'"
    return None


def _render_value_operator_clause(
    expression: str,
    operator: str,
    leaf: dict[str, Any],
    safe_functions: set[str],
) -> str:
    """渲染 IN / BETWEEN / 通用标量值的子句（兜底分支）。"""
    if operator in ("IN", "NOT IN"):
        return f"{expression} {operator} {_sql_list_literal(leaf.get('value'), leaf.get('value_type'), safe_functions)}"
    if operator == "BETWEEN" and isinstance(leaf.get("value"), list) and len(leaf["value"]) == 2:
        values = leaf["value"]
        left = _sql_literal(values[0], _value_type_for_index(leaf.get("value_type"), 0), safe_functions)
        right = _sql_literal(values[1], _value_type_for_index(leaf.get("value_type"), 1), safe_functions)
        return f"{expression} BETWEEN {left} AND {right}"
    return f"{expression} {operator} {_sql_literal(leaf.get('value'), leaf.get('value_type'), safe_functions)}"


def build_where_leaf_clause(
    leaf: dict[str, Any],
    *,
    is_multi_table: bool,
    dimension_expr_map: dict[tuple[str, str], str],
    main_table: str,
    normalize_raw_expression: Callable[..., str],
    get_column_expr: Callable[..., str],
    safe_functions: set[str],
) -> str:
    table = _effective_table_ref(leaf, fallback=main_table)
    physical_table = _physical_table_ref(leaf, fallback=table)
    column = leaf.get("column", "")
    if (leaf.get("expression") or "").strip():
        ensure_safe_sql_expression(leaf.get("expression"), label="维度表达式")
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


def _match_aggregation(
    leaf: dict[str, Any],
    agg_by_id: dict[str, dict[str, Any]],
    aggregations: list[dict[str, Any]],
    main_table: str,
    table: str,
    column: Any,
    agg_func: str,
) -> dict[str, Any] | None:
    """优先按 aggId 匹配聚合定义，否则按表/列/聚合函数兜底匹配。"""
    matched = agg_by_id.get(str(leaf.get("aggId"))) if leaf.get("aggId") else None
    if not matched and not str(leaf.get("expression") or "").strip():
        matched = next(
            (
                item
                for item in aggregations
                if _effective_table_ref(item, fallback=main_table) == table
                and item.get("column") == column
                and str(item.get("func") or "").upper() == agg_func
            ),
            None,
        )
    return matched


def _aggregate_expression_output(
    expression: Any,
    agg_func: str,
    table: str,
    column: Any,
    physical_table: str,
    is_multi_table: bool,
    normalize_expression: bool,
    normalize_raw_expression: Callable[..., str],
    normalize_aggregate_expression: Callable[..., str],
    is_aggregate_expression: Callable[[str | None], bool],
) -> str | None:
    """RAW/聚合表达式命中时返回规整后的表达式，未命中返回 None。"""
    if agg_func == "RAW" and str(expression or "").strip():
        if normalize_expression:
            return normalize_raw_expression(expression, table, column, is_multi_table, physical_table)
        return _rewrite_expression_table_qualifier(expression.strip(), table, physical_table, is_multi_table)
    if agg_func != "RAW" and str(expression or "").strip() and is_aggregate_expression(expression):
        if normalize_expression:
            return normalize_aggregate_expression(expression, table, column, is_multi_table, physical_table)
        return _rewrite_expression_table_qualifier(expression.strip(), table, physical_table, is_multi_table)
    return None


def _apply_aggregation_func(agg_func: str, column_expression: str) -> str:
    if agg_func == "RAW":
        return column_expression
    if agg_func == "COUNT_DISTINCT":
        return f"COUNT(DISTINCT {column_expression})"
    return f"{agg_func}({column_expression})"


def _build_aggregate_expr(
    leaf: dict[str, Any],
    *,
    is_multi_table: bool,
    main_table: str,
    aggregations: list[dict[str, Any]],
    agg_by_id: dict[str, dict[str, Any]],
    normalize_raw_expression: Callable[..., str],
    normalize_aggregate_expression: Callable[..., str],
    get_column_expr: Callable[..., str],
    is_aggregate_expression: Callable[[str | None], bool],
    safe_functions: set[str],
    normalize_expression: bool = False,
) -> str:
    agg_func = str(leaf.get("aggFunc", "RAW")).upper()
    table = _effective_table_ref(leaf, fallback=main_table)
    column = leaf.get("column", "")
    matched = _match_aggregation(leaf, agg_by_id, aggregations, main_table, table, column, agg_func)
    physical_table = _physical_table_ref(matched or leaf, fallback=table)
    expression = (matched or {}).get("expression") or leaf.get("expression")
    normalized = _aggregate_expression_output(
        expression,
        agg_func,
        table,
        column,
        physical_table,
        is_multi_table,
        normalize_expression,
        normalize_raw_expression,
        normalize_aggregate_expression,
        is_aggregate_expression,
    )
    if normalized is not None:
        return normalized
    column_expression = "*" if column == "*" else get_column_expr(table, column, is_multi_table)
    return _apply_aggregation_func(agg_func, column_expression)


def _rewrite_expression_table_qualifier(expression: str, table: str, physical_table: str, is_multi_table: bool) -> str:
    if not table or not physical_table or table == physical_table:
        return expression
    escaped = re.escape(physical_table)
    return re.sub(rf"(?<![\w`])`?{escaped}`?\.", f"{table}.", expression)


def build_having_leaf_clause(leaf: dict[str, Any], **kwargs: Any) -> str:
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
    return _operator_clause(
        expression, leaf, right_expression=right_expression, safe_functions=kwargs["safe_functions"]
    )


def build_having_operand_expr(operand: dict[str, Any], **kwargs: Any) -> str:
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


def build_filter_tree_clause(node: dict[str, Any], *, for_having: bool, is_root: bool = False, **kwargs: Any) -> str:
    key = "and" if "and" in node else "or" if "or" in node else ""
    if key:
        children = [
            build_filter_tree_clause(child, for_having=for_having, is_root=False, **kwargs)
            for child in node.get(key) or []
            if isinstance(child, dict)
        ]
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
