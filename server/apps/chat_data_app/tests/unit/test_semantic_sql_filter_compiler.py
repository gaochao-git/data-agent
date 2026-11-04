import sqlglot

from apps.chat_data_app.services.semantic.core.semantic_sql.dialect import get_dialect
from apps.chat_data_app.services.semantic.core.semantic_sql.filter_compiler import (
    _escape_like_value,
    _sql_literal,
    build_filter_tree_clause,
    build_where_leaf_clause,
)


def test_build_where_leaf_clause_supports_between_and_field_reference():
    def column(table, name, multi):
        return f"{table}.`{name}`" if multi else f"`{name}`"

    result = build_where_leaf_clause(
        {"table": "orders", "column": "amount", "operator": "BETWEEN", "value": [10, 20]},
        is_multi_table=False,
        dimension_expr_map={},
        main_table="orders",
        normalize_raw_expression=lambda expression, *args: expression,
        get_column_expr=column,
        safe_functions=set(),
    )

    assert result == "`amount` BETWEEN 10 AND 20"


def test_build_where_leaf_clause_respects_sql_native_quoted_value():
    """筛选值按 SQL 原生语法解释："'paid'" 编译为 'paid' 而非 '''paid'''。

    回归背景：模型/界面把 SQL 字面量引号带进 value 时，二次 escape 产出
    '''paid'''（语义为比较带引号字符串，等值恒假，结果错误）。
    """

    def column(table, name, multi):
        return f"{table}.`{name}`" if multi else f"`{name}`"

    kwargs = {
        "is_multi_table": False,
        "dimension_expr_map": {},
        "main_table": "orders",
        "normalize_raw_expression": lambda expression, *args: expression,
        "get_column_expr": column,
        "safe_functions": set(),
    }
    # 成对单引号包裹形态：剥引号后统一 escape，输出规范字面量
    result = build_where_leaf_clause(
        {"table": "orders", "column": "status", "operator": "=", "value": "'paid'"},
        **kwargs,
    )
    assert result == "`status` = 'paid'"
    # 裸值兜底行为不变
    result = build_where_leaf_clause(
        {"table": "orders", "column": "status", "operator": "=", "value": "paid"},
        **kwargs,
    )
    assert result == "`status` = 'paid'"
    # SQL 转义字面量形态（内部 '' 转义）解析还原后恒等输出
    result = build_where_leaf_clause(
        {"table": "orders", "column": "customer", "operator": "=", "value": "'O''Brien'"},
        **kwargs,
    )
    assert result == "`customer` = 'O''Brien'"
    # 含未转义单引号的非法字面量形态不放大错误，按裸值 escape
    result = build_where_leaf_clause(
        {"table": "orders", "column": "customer", "operator": "=", "value": "'O'Brien'"},
        **kwargs,
    )
    assert result == "`customer` = '''O''Brien'''"


def test_build_filter_tree_clause_preserves_nested_parentheses():
    def column(table, name, multi):
        return f"`{name}`"

    result = build_filter_tree_clause(
        {
            "and": [
                {"table": "orders", "column": "status", "operator": "=", "value": "paid"},
                {
                    "or": [
                        {"table": "orders", "column": "region", "operator": "=", "value": "east"},
                        {"table": "orders", "column": "region", "operator": "=", "value": "west"},
                    ]
                },
            ]
        },
        for_having=False,
        is_root=True,
        is_multi_table=False,
        dimension_expr_map={},
        main_table="orders",
        normalize_raw_expression=lambda expression, *args: expression,
        get_column_expr=column,
        safe_functions=set(),
    )

    assert result == "`status` = 'paid' AND (`region` = 'east' OR `region` = 'west')"


def test_build_where_leaf_clause_rejects_operator_outside_whitelist():
    import pytest

    def column(table, name, multi):
        return f"`{name}`"

    with pytest.raises(ValueError, match="不支持的筛选操作符"):
        build_where_leaf_clause(
            {"table": "orders", "column": "amount", "operator": "= 1 OR (SELECT 1)", "value": 1},
            is_multi_table=False,
            dimension_expr_map={},
            main_table="orders",
            normalize_raw_expression=lambda expression, *args: expression,
            get_column_expr=column,
            safe_functions=set(),
        )


def test_build_where_leaf_clause_rejects_unsafe_right_expression():
    import pytest

    def column(table, name, multi):
        return f"`{name}`"

    for unsafe in ("1; DROP TABLE users", "1 -- comment", "1 /* block */", "1 OR INSERT INTO t VALUES(1)"):
        with pytest.raises(ValueError, match="只允许单条只读 SQL 表达式"):
            build_where_leaf_clause(
                {
                    "table": "orders",
                    "column": "amount",
                    "operator": "=",
                    "valueType": "expression",
                    "rightExpression": unsafe,
                },
                is_multi_table=False,
                dimension_expr_map={},
                main_table="orders",
                normalize_raw_expression=lambda expression, *args: expression,
                get_column_expr=column,
                safe_functions=set(),
            )


def test_build_where_leaf_clause_allows_subquery_in_expression():
    # IN (SELECT ...) 是 Builder 的显式产品能力，黑名单不拦，由执行层 sql_guard 兜底
    def column(table, name, multi):
        return f"`{name}`"

    result = build_where_leaf_clause(
        {
            "table": "orders",
            "column": "city",
            "operator": "IN",
            "valueType": "expression",
            "rightExpression": "(SELECT city FROM white_list)",
        },
        is_multi_table=False,
        dimension_expr_map={},
        main_table="orders",
        normalize_raw_expression=lambda expression, *args: expression,
        get_column_expr=column,
        safe_functions=set(),
    )

    assert result == "`city` IN ((SELECT city FROM white_list))"


def test_build_where_leaf_clause_rejects_unsafe_dimension_expression():
    import pytest

    def column(table, name, multi):
        return f"`{name}`"

    with pytest.raises(ValueError, match="维度表达式"):
        build_where_leaf_clause(
            {
                "table": "orders",
                "column": "amount",
                "operator": "=",
                "expression": "amount; DROP TABLE users",
                "value": 1,
            },
            is_multi_table=False,
            dimension_expr_map={},
            main_table="orders",
            normalize_raw_expression=lambda expression, *args: expression,
            get_column_expr=column,
            safe_functions=set(),
        )


def test_sql_literal_escapes_backslash_before_quote():
    """反斜杠+单引号不能提前闭合字符串（SQL 注入 P1 回归）。

    修复前输入 ``a\\' OR 1=1 -- `` 会生成 ``'a\\'' OR 1=1 -- '``，
    MySQL 把 ``\\'`` 解释成字面单引号后字符串提前结束，WHERE 变成恒真。
    """
    payload = "a\\' OR 1=1 -- "
    literal = _sql_literal(payload, "literal", set())
    sql = f"SELECT * FROM t WHERE c = {literal}"
    parsed = sqlglot.parse_one(sql, read="mysql")
    where = parsed.args.get("where")

    assert where is not None
    assert isinstance(where.this, sqlglot.exp.EQ)
    assert where.this.right.is_string
    assert "\\" in literal


def test_like_value_escapes_backslash_before_quote():
    payload = "a\\' OR 1=1 -- "
    escaped = _escape_like_value(payload)
    assert "\\" in escaped
    sql = f"SELECT * FROM t WHERE c LIKE '{escaped}%'"
    where = sqlglot.parse_one(sql, read="mysql").args.get("where")
    assert where is not None
    assert isinstance(where.this, sqlglot.exp.Like)
    assert where.this.right.is_string


def test_dialect_quote_identifier_escapes_backtick():
    dialect = get_dialect("mysql")
    assert dialect.quote_identifier("a`b") == "`a``b`"
