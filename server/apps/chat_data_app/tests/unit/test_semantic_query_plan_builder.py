import pytest

from apps.chat_data_app.services.semantic.run.semantic_query_plan_builder import (
    assemble_dimension_filter_node,
    builder_node_alias,
    collect_filter_node_tables,
    collect_operand_tables,
    metric_func_column,
    normalize_text,
)


def test_normalize_text_strips_and_null_safes():
    assert normalize_text("  x  ") == "x"
    assert normalize_text(None) == ""
    # value or "" 语义：0 等假值归一为空串（与原 _normalize_text 行为一致）
    assert normalize_text(0) == ""


def test_metric_func_column_parses_aggregates_and_extracts_column():
    assert metric_func_column("SUM(orders.amount)") == ("SUM", "amount")
    assert metric_func_column("count(distinct orders.order_id)") == ("COUNT_DISTINCT", "order_id")
    assert metric_func_column("COUNT(*)") == ("COUNT", "*")
    assert metric_func_column("*") is None
    assert metric_func_column("") is None
    assert metric_func_column(None) is None


def test_builder_node_alias_prefers_camel_table_alias():
    assert builder_node_alias({"tableAlias": "o", "table_alias": "cfg", "table": "orders"}) == "o"


def test_builder_node_alias_falls_back_to_config_alias_chain():
    assert builder_node_alias({"table_alias": "cfg", "table": "orders"}) == "cfg"
    assert builder_node_alias({"alias": "a", "table": "orders"}) == "a"
    assert builder_node_alias({"table": "orders"}) == "orders"
    assert builder_node_alias({}) == ""
    assert builder_node_alias(None) == ""


def test_assemble_dimension_filter_node_uses_config_identity():
    dim_cfg = {"table": "orders", "table_alias": "o", "column": "region", "expression": None}
    node = assemble_dimension_filter_node(
        dim_cfg,
        node_id="filter-地区",
        operator="=",
        logic="AND",
    )
    assert node == {
        "id": "filter-地区",
        "table": "orders",
        "tableAlias": "o",
        "column": "region",
        "expression": None,
        "operator": "=",
        "logic": "AND",
    }


def test_assemble_dimension_filter_node_expression_override_and_table_alias_fallback():
    dim_cfg = {"table": "orders", "column": "create_time"}
    node = assemble_dimension_filter_node(
        dim_cfg,
        node_id="filter-t",
        operator=">=",
        expression="DATE(create_time)",
    )
    assert node["tableAlias"] == "orders"
    assert node["expression"] == "DATE(create_time)"
    assert node["logic"] == "AND"

    assert assemble_dimension_filter_node(None, node_id="x", operator="=")["table"] is None


def test_collect_filter_node_tables_walks_and_or_tree():
    tables: set = set()
    tree = {
        "and": [
            {"tableAlias": "a", "column": "x"},
            {"or": [{"table": "b"}, "junk", {"expressionTables": ["c"]}]},
        ]
    }

    def extractor(node, acc):
        alias = builder_node_alias(node) or node.get("table")
        if alias:
            acc.add(alias)
        for table in node.get("expressionTables") or []:
            if table:
                acc.add(table)

    collect_filter_node_tables(tree, tables, extractor)
    assert tables == {"a", "b", "c"}
    collect_filter_node_tables("junk-root", tables, extractor)
    assert tables == {"a", "b", "c"}


def test_collect_operand_tables_walks_metric_and_binary():
    tables: set = set()
    operand = {
        "type": "binary",
        "left": {"type": "metric", "tableAlias": "m1"},
        "right": {
            "type": "binary",
            "left": {"type": "metric", "table": "m2"},
            "right": {"type": "literal", "value": 1},
        },
    }
    collect_operand_tables(operand, tables, lambda op, acc: acc.add(op.get("tableAlias") or op.get("table")))
    assert tables == {"m1", "m2"}
    collect_operand_tables(None, tables, lambda op, acc: acc.add("never"))
    collect_operand_tables({"type": "literal"}, tables, lambda op, acc: acc.add("never"))
    assert "never" not in tables


def test_shared_primitives_are_importable_from_facade():
    from apps.chat_data_app.services.semantic.run import semantic_query_service as facade

    assert facade.SemanticAIQueryAdapter.__name__ == "SemanticAIQueryAdapter"
    assert facade.SemanticBIQueryAdapter.__name__ == "SemanticBIQueryAdapter"
    assert facade._parse_aggregate_expression("SUM(a)") == ("SUM", "a")
    assert facade._normalize_alias({"tableAlias": "x"}) == "x"
    assert facade._as_list(None) == []
    with pytest.raises(ValueError):
        facade._as_list("bad")
