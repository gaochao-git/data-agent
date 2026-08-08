from apps.chat_data_app.services.semantic_core.semantic_sql.filter_compiler import (
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
                {"or": [
                    {"table": "orders", "column": "region", "operator": "=", "value": "east"},
                    {"table": "orders", "column": "region", "operator": "=", "value": "west"},
                ]},
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
