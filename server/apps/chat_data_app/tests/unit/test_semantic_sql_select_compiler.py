from apps.chat_data_app.services.semantic.core.semantic_sql.dialect import get_dialect
from apps.chat_data_app.services.semantic.core.semantic_sql.select_compiler import compile_select_fields


def test_compile_select_fields_returns_alias_and_group_maps():
    result = compile_select_fields(
        dimensions=[{"table": "orders", "column": "status", "alias": "状态"}],
        aggregations=[{"table": "orders", "column": "amount", "func": "SUM", "alias": "销售额"}],
        main_alias="orders",
        is_multi_table=False,
        dialect=get_dialect("hive"),
        quote_identifier=lambda value: f"`{value}`",
        to_sql_alias=lambda value, fallback: value or fallback,
        normalize_raw_expression=lambda expression, table, column, multi, physical: f"`{column}`",
        normalize_aggregate_expression=lambda expression, table, column, multi, physical: expression,
        get_column_expr=lambda table, column, multi: f"`{column}`",
        is_aggregate_expression=lambda expression: False,
    )

    assert result.fields == ["`status` AS `状态`", "SUM(`amount`) AS `销售额`"]
    assert result.group_expr_map[("orders", "status")] == "`status`"
    assert result.dimension_alias_map[("orders", "status")] == "状态"
