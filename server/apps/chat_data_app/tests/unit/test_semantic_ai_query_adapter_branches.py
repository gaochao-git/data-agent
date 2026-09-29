"""``SemanticAIQueryAdapter`` 的分支补测。

与 ``test_semantic_bi_query_adapter_branches.py`` 互补：这里覆盖 AI 适配器
（``_generate_sql_via_unified_engine`` 及其辅助方法）里未被触达的分支，
重点是主表推断的兜底路径、HAVING 操作数递归和 order_by 的字段回退。

所有用例都是纯 dict 驱动，不接触数据库与数据源管理器。
"""

import pytest

from apps.chat_data_app.services.semantic.run.semantic_query_service import SemanticAIQueryAdapter

AI_CONFIG = {
    "source_type": "mysql",
    "dimensions": [
        {
            "name": "城市",
            "table": "orders",
            "table_alias": "o",
            "column": "city",
            "logical_type": "text",
        },
    ],
    "metrics": [
        {
            "name": "GMV",
            "table": "orders",
            "table_alias": "o",
            "column": "amount",
            "expression": "SUM(o.amount)",
        },
        # COUNT(*) 用于覆盖「聚合表达式列就是 *」的分支
        {
            "name": "总行数",
            "table": "orders",
            "table_alias": "o",
            "column": "*",
            "expression": "COUNT(*)",
        },
        # 无 expression 的指标，用于覆盖 order_by 回退到 column 的分支
        {
            "name": "原始金额",
            "table": "orders",
            "table_alias": "o",
            "column": "amount",
        },
    ],
    "relationships": [],
}


@pytest.fixture
def adapter():
    return SemanticAIQueryAdapter(dict(AI_CONFIG))


# --------------------------------------------------------------------------
# _collect_tables_from_having_operand
# --------------------------------------------------------------------------


def test_collect_tables_from_having_operand_ignores_non_dict(adapter):
    needed = set()
    adapter._collect_tables_from_having_operand("GMV", needed)
    assert needed == set()


def test_collect_tables_from_having_operand_skips_metric_without_table(adapter):
    """metric 操作数没带表名时不贡献表（130->132）。"""
    needed = set()
    adapter._collect_tables_from_having_operand({"type": "metric", "name": "GMV"}, needed)
    assert needed == set()


def test_collect_tables_from_having_operand_collects_metric_table(adapter):
    needed = set()
    adapter._collect_tables_from_having_operand({"type": "metric", "tableAlias": "o"}, needed)
    assert needed == {"o"}


def test_collect_tables_from_having_operand_recurses_binary(adapter):
    needed = set()
    adapter._collect_tables_from_having_operand(
        {
            "type": "binary",
            "left": {"type": "metric", "tableAlias": "o"},
            "right": {"type": "metric", "table": "customers"},
        },
        needed,
    )
    assert needed == {"o", "customers"}


def test_collect_tables_from_having_operand_ignores_unknown_type(adapter):
    needed = set()
    adapter._collect_tables_from_having_operand({"type": "literal", "value": 1}, needed)
    assert needed == set()


# --------------------------------------------------------------------------
# _collect_tables_from_filter_node
# --------------------------------------------------------------------------


def test_collect_tables_from_filter_node_ignores_non_dict(adapter):
    needed = set()
    adapter._collect_tables_from_filter_node("junk", needed)
    assert needed == set()


def test_collect_tables_from_filter_node_recurses_logical_groups(adapter):
    needed = set()
    adapter._collect_tables_from_filter_node(
        {
            "and": [
                {"tableAlias": "o"},
                {"or": [{"table": "customers"}, "skipped"]},
            ]
        },
        needed,
    )
    assert needed == {"o", "customers"}


def test_collect_tables_from_filter_node_collects_expression_tables(adapter):
    needed = set()
    adapter._collect_tables_from_filter_node(
        {"table": "orders", "expressionTables": ["o", "c", ""]},
        needed,
    )
    assert needed == {"orders", "o", "c"}


# --------------------------------------------------------------------------
# _map_having_operand_for_builder
# --------------------------------------------------------------------------


def test_map_having_operand_for_builder_falls_back_to_null_literal(adapter):
    """未知操作数类型退化成空 literal，而不是抛错（283->290）。"""
    assert adapter._map_having_operand_for_builder({"type": "column"}, {}, "orders") == {
        "type": "literal",
        "value": None,
    }


def test_map_having_operand_for_builder_maps_literal(adapter):
    assert adapter._map_having_operand_for_builder({"type": "literal", "value": 10}, {}, "orders") == {
        "type": "literal",
        "value": 10,
    }


def test_map_having_operand_for_builder_maps_metric_with_agg_id(adapter):
    mapped = adapter._map_having_operand_for_builder({"type": "metric", "name": "GMV"}, {"GMV": "agg-GMV"}, "orders")
    assert mapped["type"] == "metric"
    assert mapped["aggId"] == "agg-GMV"
    # _metric_aggregation_node 用的是 aggFunc，不是 func
    assert mapped["aggFunc"] == "SUM"
    assert mapped["column"] == "amount"


def test_map_having_operand_for_builder_recurses_binary(adapter):
    mapped = adapter._map_having_operand_for_builder(
        {
            "type": "binary",
            "operator": "+",
            "left": {"type": "metric", "name": "GMV"},
            "right": {"type": "literal", "value": 1},
        },
        {},
        "orders",
    )
    assert mapped["operator"] == "+"
    assert mapped["left"]["name"] == "GMV"
    assert mapped["right"] == {"type": "literal", "value": 1}


# --------------------------------------------------------------------------
# _collect_having_metric_names
# --------------------------------------------------------------------------


def test_collect_having_metric_names_skips_non_dict_items(adapter):
    """having 里混入非 dict 元素时跳过（313->314）。"""
    names = adapter._collect_having_metric_names(["junk", None, {"metric": "GMV"}])
    assert names == ["GMV"]


def test_collect_having_metric_names_ignores_non_dict_operand(adapter):
    """表达式型 having 的操作数不是 dict 时安全跳过（303->304）。"""
    names = adapter._collect_having_metric_names(
        [
            {
                "left": {"type": "metric", "name": "GMV"},
                "right": "junk",
                "operator": ">",
            }
        ]
    )
    assert names == ["GMV"]


def test_collect_having_metric_names_deduplicates(adapter):
    names = adapter._collect_having_metric_names(
        [
            {"metric": "GMV"},
            {"metric": "GMV"},
            {"left": {"type": "metric", "name": "GMV"}, "right": {"type": "literal", "value": 1}, "operator": ">"},
        ]
    )
    assert names == ["GMV"]


def test_collect_having_metric_names_collects_nested_binary(adapter):
    names = adapter._collect_having_metric_names(
        [
            {
                "left": {
                    "type": "binary",
                    "left": {"type": "metric", "name": "GMV"},
                    "right": {"type": "metric", "name": "总行数"},
                },
                "right": {"type": "literal", "value": 1},
                "operator": ">",
            }
        ]
    )
    assert names == ["GMV", "总行数"]


# --------------------------------------------------------------------------
# generate_sql：主表推断与字段回退
# --------------------------------------------------------------------------


def test_generate_sql_resolves_main_table_from_having_but_has_no_field(adapter):
    """主表只能从 HAVING 指标推断时走到 510->511，随后无可用字段报错（599->600）。"""
    result = adapter.generate_sql(
        metrics=[],
        dimensions=[],
        filters=[],
        having=[{"metric": "GMV", "operator": ">", "value": 1000}],
    )
    assert result["success"] is False
    assert "未找到可用于查询的字段" in result["error"]


def test_generate_sql_keeps_star_for_count_star_metric(adapter):
    """COUNT(*) 的列应保持 *，不做列名截取（558->560）。"""
    result = adapter.generate_sql(metrics=["总行数"], dimensions=[], filters=[], having=[])
    assert result["success"] is True
    assert "COUNT(*)" in result["sql"]


def test_generate_sql_order_by_metric_without_expression(adapter):
    """指标没有 expression 时，order_by 回退到 column 字段（652->654）。"""
    result = adapter.generate_sql(
        metrics=["原始金额"],
        dimensions=[],
        filters=[],
        having=[],
        order_by=[{"field": "原始金额", "direction": "ASC"}],
    )
    assert result["success"] is True
    assert "ORDER BY" in result["sql"]


def test_generate_sql_rejects_unknown_order_by_field(adapter):
    """未知排序字段在 validator 阶段就被拦下，走不到后面的维度兜底。

    ``validate_order_by`` 只接受已校验的维度名或指标名，因此 665 行
    ``self.dimensions_map.get(field_name, {})`` 的空 dict 兜底不可达 ——
    这里锁定前端拦截行为，避免后续误以为该兜底是活代码。
    """
    result = adapter.generate_sql(
        metrics=["GMV"],
        dimensions=["城市"],
        filters=[],
        having=[],
        order_by=[{"field": "未知字段", "direction": "DESC"}],
    )
    assert result["success"] is False
    assert "未知字段" in result["error"]


def test_generate_sql_requires_metric_or_dimension(adapter):
    result = adapter.generate_sql(metrics=[], dimensions=[], filters=[], having=[])
    assert result["success"] is False
    assert "至少需要一个指标或维度" in result["error"]
