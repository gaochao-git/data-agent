import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "test")

from apps.chat_data_app.services import semantic_builder_compiler as compiler_module
from apps.chat_data_app.services.semantic_builder_compiler import (
    SemanticBuilderCompiler,
    _as_list,
    _field_keys,
    _normalize_alias,
    _parse_aggregate_expression,
)


def build_semantic_config():
    return {
        "id": "cfg_orders",
        "name": "订单分析",
        "source_type": "mysql",
        "metrics": [
            {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
            {"name": "订单数", "expression": "COUNT(DISTINCT orders.order_id)", "table": "orders"},
            {"name": "原始金额", "expression": "orders.amount", "table": "orders", "column": "amount"},
            {"name": "用户数", "expression": "COUNT(DISTINCT users.user_id)", "table": "users"},
        ],
        "dimensions": [
            {"name": "城市", "column": "city", "table": "orders", "type": "categorical", "expression": "orders.city"},
            {"name": "状态", "column": "status", "table": "orders", "type": "categorical"},
            {"name": "用户等级", "column": "level", "table": "users", "table_alias": "u", "type": "categorical"},
        ],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "users",
                "join_type": "left",
                "join_conditions": [{"left_column": "user_id", "right_column": "user_id"}],
            }
        ],
    }


def test_semantic_builder_helper_branches():
    compiler = SemanticBuilderCompiler(build_semantic_config())

    assert _as_list(None) == []
    assert _as_list([{"a": 1}, "skip", None]) == [{"a": 1}]
    with pytest.raises(ValueError, match="必须是数组"):
        _as_list({"bad": True})

    assert _normalize_alias({"table_alias": "o"}) == "o"
    assert _normalize_alias({"alias": "a"}) == "a"
    assert _normalize_alias({"table": "orders"}) == "orders"
    assert _field_keys("", "", "") == set()
    assert _field_keys("orders", "", "amount") == {("orders", "amount")}
    assert _parse_aggregate_expression(compiler.generator, "orders.amount") is None
    assert _parse_aggregate_expression(compiler.generator, "COUNT(*)") == ("COUNT", "*")


def test_semantic_builder_compile_validation_branches():
    compiler = SemanticBuilderCompiler(build_semantic_config())

    with pytest.raises(ValueError, match="格式错误"):
        compiler.compile([])
    with pytest.raises(ValueError, match="不允许包含"):
        compiler.compile({"mainTable": "orders", "dimensions": [{"semanticName": "城市"}]})
    with pytest.raises(ValueError, match="至少需要一个维度或指标"):
        compiler.compile({"dimensions": [], "aggregations": []})
    with pytest.raises(ValueError, match="LIMIT"):
        compiler.compile({"dimensions": [{"semanticName": "城市"}], "limit": 0})
    with pytest.raises(ValueError, match="OFFSET"):
        compiler.compile({"dimensions": [{"semanticName": "城市"}], "offset": -1})
    with pytest.raises(ValueError, match="filters 必须是数组"):
        compiler.compile({"dimensions": [{"semanticName": "城市"}], "filters": {"bad": True}})


def test_semantic_builder_resolves_dimensions_and_metrics_by_names_fields_and_expression():
    compiler = SemanticBuilderCompiler(build_semantic_config())

    assert compiler._resolve_dimension_config({"displayName": "城市"})["name"] == "城市"
    assert compiler._resolve_dimension_config({"table": "orders", "column": "city", "expression": "orders.city"})["name"] == "城市"
    with pytest.raises(ValueError, match="语义维度不存在"):
        compiler._resolve_dimension_config({"table": "orders", "column": "missing", "alias": "缺失字段"})

    assert compiler._resolve_metric_config({"displayName": "销售额"})["name"] == "销售额"
    assert compiler._resolve_metric_config({"expression": "SUM(orders.amount)"})["name"] == "销售额"
    assert compiler._resolve_metric_config({"func": "COUNT_DISTINCT", "table": "users", "column": "user_id"})["name"] == "用户数"
    assert compiler._resolve_metric_config({"func": "SUM", "table": "orders", "column": "missing"}) is None


def test_semantic_builder_rejects_ambiguous_metric_field_match():
    config = build_semantic_config()
    config["metrics"] = [
        {"name": "销售额A", "expression": "SUM(orders.amount)", "table": "orders"},
        {"name": "销售额B", "expression": "SUM(orders.amount)", "table": "orders"},
    ]
    compiler = SemanticBuilderCompiler(config)

    with pytest.raises(ValueError, match="匹配到多个语义指标"):
        compiler._resolve_metric_config({"func": "SUM", "table": "orders", "column": "amount"})


def test_semantic_builder_maps_dimension_as_raw_or_temporary_aggregate():
    compiler = SemanticBuilderCompiler(build_semantic_config())

    raw = compiler._map_aggregation({"semanticName": "城市", "func": "RAW", "alias": "城市原始值"})
    assert raw["func"] == "RAW"
    assert raw["column"] == "city"
    assert raw["alias"] == "城市原始值"

    temporary_aggregate = compiler._map_aggregation({"semanticName": "城市", "func": "COUNT_DISTINCT", "alias": "城市数"})
    assert temporary_aggregate["func"] == "COUNT_DISTINCT"
    assert temporary_aggregate["column"] == "city"
    assert temporary_aggregate["alias"] == "城市数"


def test_semantic_builder_filter_mapping_branches():
    compiler = SemanticBuilderCompiler(build_semantic_config())

    with pytest.raises(ValueError, match="and 过滤节点必须是数组"):
        compiler._map_filter_node({"and": "bad"})
    with pytest.raises(ValueError, match="or 过滤节点必须是数组"):
        compiler._map_filter_node({"or": "bad"})

    mapped_tree = compiler._map_filter_nodes([
        {
            "and": [
                {"semanticName": "城市", "operator": "=", "value": "上海"},
                {
                    "or": [
                        {"semanticName": "状态", "operator": "=", "value": "paid"},
                        "skip",
                    ]
                },
            ]
        }
    ])
    assert mapped_tree[0]["and"][0]["column"] == "city"
    assert mapped_tree[0]["and"][1]["or"][0]["column"] == "status"

    field_ref = compiler._map_dimension_filter({
        "semanticName": "城市",
        "operator": "=",
        "valueType": "field_ref",
        "rightTable": "users",
        "rightTableAlias": "u",
        "rightColumn": "level",
    })
    assert field_ref["rightTable"] == "users"
    assert field_ref["rightTableAlias"] == "u"
    assert field_ref["rightColumn"] == "level"

    having = compiler._map_having_filter({
        "semanticName": "销售额",
        "aggFunc": "SUM",
        "operator": ">",
        "value": 100,
    })
    assert having["aggFunc"] == "SUM"
    assert having["column"] == "amount"


def test_semantic_builder_order_and_selection_helpers():
    compiler = SemanticBuilderCompiler(build_semantic_config())
    dimensions = [compiler._map_dimension({"semanticName": "城市"})]
    aggregations = [compiler._map_aggregation({"semanticName": "销售额", "id": "agg_sales"})]

    with pytest.raises(ValueError, match="排序方向"):
        compiler._map_order_by({"semanticName": "城市", "direction": "sideways"}, aggregations, dimensions)

    assert compiler._find_selected_dimension({"table": "orders", "column": "city"}, dimensions)["alias"] == "城市"
    assert compiler._find_selected_dimension({"table": "orders", "column": "missing"}, dimensions) is None
    assert compiler._find_selected_aggregation({"aggId": "agg_sales"}, aggregations)["alias"] == "销售额"
    assert compiler._find_selected_aggregation({"expression": "SUM(orders.amount)"}, aggregations)["alias"] == "销售额"
    assert compiler._find_selected_aggregation({"expression": "SUM(orders.refund_amount)"}, aggregations) is None
    assert compiler._find_selected_aggregation({"displayName": "销售额"}, aggregations)["alias"] == "销售额"
    assert compiler._find_selected_aggregation({"displayName": "退款额"}, aggregations) is None
    assert compiler._find_selected_aggregation({"aggFunc": "SUM", "table": "orders", "column": "amount"}, aggregations)["alias"] == "销售额"
    assert compiler._find_selected_aggregation({"aggFunc": "COUNT", "table": "orders", "column": "amount"}, aggregations) is None

    order_by_selected_agg = compiler._map_order_by({"aggId": "agg_sales", "aggFunc": "SUM", "direction": "DESC"}, aggregations, dimensions)
    order_by_mapped_dim = compiler._map_order_by({"semanticName": "状态", "direction": "ASC"}, aggregations, dimensions)
    order_by_mapped_agg = compiler._map_order_by({"semanticName": "订单数", "aggFunc": "COUNT_DISTINCT", "direction": "DESC"}, aggregations, dimensions)

    assert order_by_selected_agg["aggId"] == "agg_sales"
    assert order_by_mapped_dim["column"] == "status"
    assert order_by_mapped_agg["column"] == "order_id"


def test_semantic_builder_table_collection_and_main_table_resolution():
    compiler = SemanticBuilderCompiler(build_semantic_config())
    filters = compiler._map_filter_nodes([
        {
            "and": [
                {"semanticName": "城市", "operator": "=", "value": "上海"},
                {"semanticName": "用户等级", "operator": "=", "value": "vip"},
            ]
        }
    ])
    dimensions = [compiler._map_dimension({"semanticName": "城市"})]
    aggregations = [compiler._map_aggregation({"semanticName": "用户数"})]
    order_by = [{"table": "", "tableAlias": "", "column": ""}]

    tables_needed = compiler._collect_tables_needed("orders", dimensions, aggregations, filters, order_by)
    assert tables_needed == {"orders", "u", "users"}
    assert compiler._resolve_main_table([], [], filters, [])[0] == "orders"
    assert compiler._resolve_main_table([], [], [], [compiler._map_order_by({"semanticName": "状态"}, [], [])]) == ("orders", "orders")
    assert compiler._first_filter_table({"or": ["skip", {"table": "users", "tableAlias": "u"}]}) == ("users", "u")
    assert compiler._first_filter_table({"and": []}) == ("", "")
    with pytest.raises(ValueError, match="无法确定主表"):
        compiler._resolve_main_table([], [], [], [])


def test_semantic_builder_compile_full_path_with_aliases_and_field_ref():
    compiler = SemanticBuilderCompiler(build_semantic_config())

    result = compiler.compile({
        "dimensions": [{"semanticName": "用户等级", "alias": "等级"}],
        "aggregations": [{"semanticName": "销售额", "alias": "销售额"}],
        "filters": [
            {
                "semanticName": "用户等级",
                "operator": "=",
                "valueType": "field_ref",
                "rightTable": "orders",
                "rightColumn": "status",
            },
            {
                "semanticName": "销售额",
                "aggFunc": "SUM",
                "operator": ">",
                "value": 100,
            },
        ],
        "orderBy": [{"semanticName": "销售额", "aggFunc": "SUM", "direction": "DESC"}],
        "limit": 20,
        "offset": 5,
    })

    sql = result["sql"]
    assert "FROM users u" in sql
    assert "`level` = `status`" in sql
    assert "HAVING SUM(amount) > 100" in sql
    assert "ORDER BY SUM(amount) DESC" in sql
    assert sql.endswith("LIMIT 5, 20")


@pytest.mark.asyncio
async def test_load_runtime_semantic_config_branches(monkeypatch):
    class RuntimeManager:
        async def get_datasource_async(self, datasource_id):
            return {"id": datasource_id, "source_type": "hive"} if datasource_id == "ds_a" else None

        async def get_runtime_semantic_config_async(self, datasource_id, config_id):
            if config_id == "disabled":
                return {"id": config_id, "enabled": False}
            if config_id == "missing_id":
                return {"relationships": [{"left_table": "a", "right_table": "b"}], "metrics": [], "dimensions": []}
            return None

    class FallbackManager:
        async def get_datasource_async(self, datasource_id):
            return {"id": datasource_id, "source_type": "mysql"}

        async def get_semantic_config_async(self, datasource_id, config_id):
            return {"id": config_id, "enabled": True, "joins": [], "relationships": [{"left_table": "x"}]}

    monkeypatch.setattr(compiler_module, "get_datasource_manager", lambda: RuntimeManager())

    with pytest.raises(ValueError, match="缺少"):
        await compiler_module.load_runtime_semantic_config("", "cfg")
    with pytest.raises(LookupError, match="数据源不存在"):
        await compiler_module.load_runtime_semantic_config("missing", "cfg")
    with pytest.raises(LookupError, match="语义配置不存在"):
        await compiler_module.load_runtime_semantic_config("ds_a", "missing")
    with pytest.raises(ValueError, match="未启用"):
        await compiler_module.load_runtime_semantic_config("ds_a", "disabled")

    loaded = await compiler_module.load_runtime_semantic_config("ds_a", "missing_id")
    assert loaded["id"] == "missing_id"
    assert loaded["source_type"] == "hive"
    assert loaded["joins"] == loaded["relationships"]

    monkeypatch.setattr(compiler_module, "get_datasource_manager", lambda: FallbackManager())
    fallback = await compiler_module.load_runtime_semantic_config("ds_b", "cfg_b")
    assert fallback["source_type"] == "mysql"
    assert fallback["joins"] == [{"left_table": "x"}]
