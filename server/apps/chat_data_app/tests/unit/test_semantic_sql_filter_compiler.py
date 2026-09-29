from apps.chat_data_app.services.semantic.core.semantic_sql.filter_compiler import (
    build_filter_tree_clause,
    build_where_leaf_clause,
)


def test_build_where_leaf_clause_supports_between_and_field_reference():
    column = lambda table, name, multi: f"{table}.`{name}`" if multi else f"`{name}`"
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


def test_build_filter_tree_clause_preserves_nested_parentheses():
    column = lambda table, name, multi: f"`{name}`"
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

    column = lambda table, name, multi: f"`{name}`"
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

    column = lambda table, name, multi: f"`{name}`"
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
    column = lambda table, name, multi: f"`{name}`"
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

    column = lambda table, name, multi: f"`{name}`"
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
