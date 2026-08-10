import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "test")

from apps.chat_data_app.services.semantic.run import semantic_query_service as compiler_module
from apps.chat_data_app.services.semantic.run import semantic_query_service as ai_compiler_module
from apps.chat_data_app.services.semantic.run.semantic_query_service import (
    SemanticBIQueryAdapter,
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
            {"name": "用户数", "expression": "COUNT(DISTINCT users.user_id)", "table": "users", "table_alias": "u"},
        ],
        "dimensions": [
            {"name": "城市", "column": "city", "table": "orders", "logical_type": "text", "expression": "orders.city"},
            {"name": "状态", "column": "status", "table": "orders", "logical_type": "text"},
            {"name": "下单时间", "column": "order_time", "table": "orders", "logical_type": "datetime"},
            {"name": "用户等级", "column": "level", "table": "users", "table_alias": "u", "logical_type": "text"},
        ],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "users",
                "right_alias": "u",
                "join_type": "left",
                "join_conditions": [{"left_column": "user_id", "right_column": "user_id"}],
            }
        ],
    }


def test_semantic_builder_helper_branches():
    compiler = SemanticBIQueryAdapter(build_semantic_config())

    assert _as_list(None) == []
    with pytest.raises(ValueError, match=r"字段\[1\] 必须是对象"):
        _as_list([{"a": 1}, "skip", None])
    with pytest.raises(ValueError, match="必须是数组"):
        _as_list({"bad": True})

    assert _normalize_alias({"table_alias": "o"}) == "o"
    assert _normalize_alias({"alias": "a"}) == "a"
    assert _normalize_alias({"table": "orders"}) == "orders"
    assert _field_keys("", "", "") == set()
    assert _field_keys("orders", "", "amount") == {("orders", "amount")}
    assert _parse_aggregate_expression("orders.amount") is None
    assert _parse_aggregate_expression("COUNT(*)") == ("COUNT", "*")


def test_semantic_builder_compile_validation_branches():
    compiler = SemanticBIQueryAdapter(build_semantic_config())

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
    with pytest.raises(ValueError, match=r"dimensions\[1\] 必须是对象"):
        compiler.compile({"dimensions": [{"semanticName": "城市"}, "bad"]})
    with pytest.raises(ValueError, match=r"filters\[1\] 必须是对象"):
        compiler.compile({"dimensions": [{"semanticName": "城市"}], "filters": [{"semanticName": "状态", "value": "paid"}, "bad"]})


def test_ai_and_bi_reject_disconnected_semantic_tables():
    config = build_semantic_config()
    config["relationships"] = []

    ai_result = ai_compiler_module.SemanticAIQueryAdapter(config).generate_sql(
        metrics=["销售额"],
        dimensions=["用户等级"],
        filters=[],
        having=[],
    )
    assert ai_result["success"] is False
    assert "缺少从主表" in ai_result["error"]

    with pytest.raises(ValueError, match="缺少从主表"):
        SemanticBIQueryAdapter(config).compile({
            "dimensions": [{"semanticName": "城市"}, {"semanticName": "用户等级"}],
        })


def test_semantic_builder_adapter_returns_config_without_compiling(monkeypatch):
    compiler = SemanticBIQueryAdapter(build_semantic_config())
    monkeypatch.setattr(
        "apps.chat_data_app.services.semantic.core.semantic_sql.query_plan_compiler.compile_query_plan_sql",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("adapt must not compile SQL")),
    )

    builder_config = compiler.adapt({
        "dimensions": [{"semanticName": "城市"}],
        "limit": 10,
    })

    assert builder_config["mainTable"] == "orders"
    assert builder_config["dimensions"][0]["column"] == "city"
    assert builder_config["limit"] == 10


def test_semantic_builder_resolves_dimensions_and_metrics_by_names_fields_and_expression():
    compiler = SemanticBIQueryAdapter(build_semantic_config())

    assert compiler._resolve_dimension_config({"displayName": "城市"})["name"] == "城市"
    assert compiler._resolve_dimension_config({"table": "orders", "column": "city", "expression": "orders.city"})["name"] == "城市"
    with pytest.raises(ValueError, match="语义维度不存在"):
        compiler._resolve_dimension_config({"table": "orders", "column": "city", "expression": "users.city", "alias": "冲突城市"})
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
    compiler = SemanticBIQueryAdapter(config)

    with pytest.raises(ValueError, match="匹配到多个语义指标"):
        compiler._resolve_metric_config({"func": "SUM", "table": "orders", "column": "amount"})


def test_semantic_builder_maps_dimension_as_raw_or_temporary_aggregate():
    compiler = SemanticBIQueryAdapter(build_semantic_config())

    raw = compiler._map_aggregation({"semanticName": "城市", "func": "RAW", "alias": "城市原始值"})
    assert raw["func"] == "RAW"
    assert raw["column"] == "city"
    assert raw["alias"] == "城市原始值"

    temporary_aggregate = compiler._map_aggregation({"semanticName": "城市", "func": "COUNT_DISTINCT", "alias": "城市数"})
    assert temporary_aggregate["func"] == "COUNT_DISTINCT"
    assert temporary_aggregate["column"] == "city"
    assert temporary_aggregate["alias"] == "城市数"

    with pytest.raises(ValueError, match="未配置为指标"):
        compiler._map_aggregation({"semanticName": "城市", "func": "MEDIAN", "alias": "城市中位数"})


def test_semantic_builder_filter_mapping_branches():
    compiler = SemanticBIQueryAdapter(build_semantic_config())

    with pytest.raises(ValueError, match="and 过滤节点必须是数组"):
        compiler._map_filter_node({"and": "bad"})
    with pytest.raises(ValueError, match="or 过滤节点必须是数组"):
        compiler._map_filter_node({"or": "bad"})

    with pytest.raises(ValueError, match=r"or\[1\] 必须是对象"):
        compiler._map_filter_nodes([
            {"or": [{"semanticName": "状态", "operator": "=", "value": "paid"}, "skip"]}
        ])

    mapped_tree = compiler._map_filter_nodes([
        {
            "and": [
                {"semanticName": "城市", "operator": "=", "value": "上海"},
                {
                    "or": [
                        {"semanticName": "状态", "operator": "=", "value": "paid"},
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


def test_semantic_builder_enforces_dimension_allowed_operators():
    config = build_semantic_config()
    config["dimensions"][1]["allowed_operators"] = ["=", "IN"]
    compiler = SemanticBIQueryAdapter(config)

    assert compiler._map_filter_nodes([
        {"semanticName": "状态", "operator": "in", "value": ["paid"]},
    ])[0]["operator"] == "IN"
    with pytest.raises(ValueError, match="不允许操作符 'LIKE'"):
        compiler._map_filter_nodes([
            {"semanticName": "状态", "operator": "LIKE", "value": "paid"},
        ])


def test_semantic_builder_normalizes_time_dimension_equal_filter():
    compiler = SemanticBIQueryAdapter(build_semantic_config())

    mapped = compiler._map_filter_nodes([
        {"semanticName": "下单时间", "operator": "=", "value": "2024"},
        {"semanticName": "状态", "operator": "=", "value": "paid"},
    ])

    assert [item["operator"] for item in mapped] == [">=", "<", "="]
    assert mapped[0]["value"] == "2024-01-01 00:00:00"
    assert mapped[1]["value"] == "2025-01-01 00:00:00"
    assert mapped[2]["column"] == "status"

    sql = compiler.compile({
        "dimensions": [{"semanticName": "状态"}],
        "aggregations": [{"semanticName": "订单数"}],
        "filters": [{"semanticName": "下单时间", "operator": "=", "value": "2024"}],
    })["sql"]

    assert "`order_time` >= '2024-01-01 00:00:00'" in sql
    assert "`order_time` < '2025-01-01 00:00:00'" in sql
    assert "`order_time` = 2024" not in sql


def test_semantic_builder_normalizes_time_dimension_inside_filter_trees():
    compiler = SemanticBIQueryAdapter(build_semantic_config())

    and_tree = compiler._map_filter_nodes([{
        "and": [
            {"semanticName": "状态", "operator": "=", "value": "paid"},
            {"semanticName": "下单时间", "operator": "=", "value": "2024-06"},
        ]
    }])
    or_tree = compiler._map_filter_nodes([{
        "or": [
            {"semanticName": "下单时间", "operator": "=", "value": "2024-06-01"},
            {"semanticName": "状态", "operator": "=", "value": "paid"},
        ]
    }])

    assert [item["operator"] for item in and_tree[0]["and"]] == ["=", ">=", "<"]
    assert and_tree[0]["and"][1]["value"] == "2024-06-01 00:00:00"
    assert and_tree[0]["and"][2]["value"] == "2024-07-01 00:00:00"
    assert [item["operator"] for item in or_tree[0]["or"][0]["and"]] == [">=", "<"]
    assert or_tree[0]["or"][0]["and"][1]["value"] == "2024-06-02 00:00:00"


def test_semantic_builder_order_and_selection_helpers():
    compiler = SemanticBIQueryAdapter(build_semantic_config())
    dimensions = [compiler._map_dimension({"semanticName": "城市"})]
    aggregations = [
        compiler._map_aggregation({"semanticName": "销售额", "id": "agg_sales"}),
        compiler._map_aggregation({"semanticName": "订单数", "id": "agg_count"}),
    ]

    with pytest.raises(ValueError, match="排序方向"):
        compiler._map_order_by({"semanticName": "城市", "direction": "sideways"}, aggregations, dimensions)

    assert compiler._find_selected_dimension({"table": "orders", "column": "city"}, dimensions)["alias"] == "城市"
    assert compiler._find_selected_dimension({"table": "orders", "column": "missing"}, dimensions) is None
    assert compiler._find_selected_aggregation({"aggId": "agg_sales"}, aggregations)["alias"] == "销售额"
    assert compiler._find_selected_aggregation({"aggId": "missing_agg"}, aggregations) is None
    assert compiler._find_selected_aggregation({"expression": "SUM(orders.amount)"}, aggregations)["alias"] == "销售额"
    assert compiler._find_selected_aggregation({"expression": "SUM(orders.refund_amount)"}, aggregations) is None
    assert compiler._find_selected_aggregation({"displayName": "销售额"}, aggregations)["alias"] == "销售额"
    assert compiler._find_selected_aggregation({"displayName": "退款额"}, aggregations) is None
    assert compiler._find_selected_aggregation({"aggFunc": "SUM", "table": "orders", "column": "amount"}, aggregations)["alias"] == "销售额"
    assert compiler._find_selected_aggregation({"aggFunc": "COUNT", "table": "orders", "column": "amount"}, aggregations) is None
    assert compiler._find_selected_aggregation({"table": "orders", "column": "amount"}, aggregations)["alias"] == "销售额"

    order_by_selected_agg = compiler._map_order_by({"aggId": "agg_sales", "aggFunc": "SUM", "direction": "DESC"}, aggregations, dimensions)
    order_by_mapped_dim = compiler._map_order_by({"semanticName": "状态", "direction": "ASC"}, aggregations, dimensions)
    order_by_mapped_agg = compiler._map_order_by({"semanticName": "订单数", "aggFunc": "COUNT_DISTINCT", "direction": "DESC"}, aggregations, dimensions)

    assert order_by_selected_agg["aggId"] == "agg_sales"
    assert order_by_mapped_dim["column"] == "status"
    assert order_by_mapped_agg["column"] == "order_id"


def test_semantic_builder_table_collection_and_main_table_resolution():
    compiler = SemanticBIQueryAdapter(build_semantic_config())
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
    assert tables_needed == {"orders", "u"}
    assert compiler._resolve_main_table([], [], filters, [])[0] == "orders"
    assert compiler._resolve_main_table([], [], [], [compiler._map_order_by({"semanticName": "状态"}, [], [])]) == ("orders", "orders")
    assert compiler._first_filter_table({"or": ["skip", {"table": "users", "tableAlias": "u"}]}) == ("users", "u")
    assert compiler._first_filter_table({"or": [{"table": ""}, {"table": "users", "tableAlias": "u"}]}) == ("users", "u")
    assert compiler._first_filter_table({"and": []}) == ("", "")
    with pytest.raises(ValueError, match="无法确定主表"):
        compiler._resolve_main_table([], [], [], [])


def test_semantic_builder_collects_or_filter_tables_and_empty_alias_branch():
    tables_needed = set()
    SemanticBIQueryAdapter._collect_filter_tables(
        {
            "or": [
                {"table": "", "tableAlias": ""},
                {"table": "orders", "tableAlias": "orders"},
                {"table": "users", "tableAlias": "u", "valueType": "field_ref", "rightTable": "orders"},
                "skip",
            ]
        },
        tables_needed,
    )

    assert tables_needed == {"orders", "u"}


def test_semantic_builder_collect_and_main_table_remaining_edges():
    tables_needed = set()
    SemanticBIQueryAdapter._collect_filter_tables(
        {"and": ["skip", {"table": "orders", "tableAlias": "o"}]},
        tables_needed,
    )
    assert tables_needed == {"o"}

    compiler = SemanticBIQueryAdapter(build_semantic_config())
    assert compiler._resolve_main_table(
        [{"table": "", "tableAlias": "", "column": ""}, {"table": "orders", "tableAlias": "o", "column": "city"}],
        [],
        [],
        [],
    ) == ("orders", "o")
    assert compiler._resolve_main_table(
        [],
        [],
        [{"and": [{"table": ""}, {"table": "users", "tableAlias": "u"}]}],
        [],
    ) == ("users", "u")


def test_semantic_builder_compile_full_path_with_aliases_and_field_ref():
    compiler = SemanticBIQueryAdapter(build_semantic_config())

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
    assert "u.`level` = orders.`status`" in sql
    assert "HAVING SUM(orders.amount) > 100" in sql
    assert "ORDER BY SUM(orders.amount) DESC" in sql
    assert sql.endswith("LIMIT 5, 20")


@pytest.mark.asyncio
async def test_load_runtime_semantic_config_branches(monkeypatch):
    class RuntimeManager:
        async def get_datasource_async(self, datasource_id):
            return {"id": datasource_id, "source_type": "hive"} if datasource_id == "ds_a" else None

        async def get_runtime_semantic_config_async(self, datasource_id, config_id):
            if config_id == "plain":
                return {"id": config_id, "enabled": True, "metrics": [], "dimensions": []}
            if config_id == "disabled":
                return {"id": config_id, "enabled": False}
            if config_id == "missing_id":
                return {"relationships": [{"left_table": "a", "right_table": "b"}], "metrics": [], "dimensions": []}
            return None

    class FallbackManager:
        async def get_datasource_async(self, datasource_id):
            return {"id": datasource_id}

        async def get_semantic_config_async(self, datasource_id, config_id):
            return {"id": config_id, "enabled": True, "joins": [], "relationships": [{"left_table": "x"}]}

    runtime_manager = RuntimeManager()

    class RuntimeSemanticService:
        async def get_runtime_config_async(self, datasource_id, config_id, user_id=""):
            return await runtime_manager.get_runtime_semantic_config_async(datasource_id, config_id)

    monkeypatch.setattr(compiler_module, "get_datasource_manager", lambda: runtime_manager)
    monkeypatch.setattr(compiler_module, "get_semantic_config_service", lambda: RuntimeSemanticService())

    with pytest.raises(ValueError, match="缺少"):
        await compiler_module.load_runtime_semantic_config("", "cfg")
    with pytest.raises(LookupError, match="数据源不存在"):
        await compiler_module.load_runtime_semantic_config("missing", "cfg")
    with pytest.raises(LookupError, match="语义配置不存在"):
        await compiler_module.load_runtime_semantic_config("ds_a", "missing")
    with pytest.raises(ValueError, match="未启用"):
        await compiler_module.load_runtime_semantic_config("ds_a", "disabled")

    loaded = await compiler_module.load_runtime_semantic_config("ds_a", "missing_id")
    plain = await compiler_module.load_runtime_semantic_config("ds_a", "plain")
    assert loaded["id"] == "missing_id"
    assert loaded["source_type"] == "hive"
    assert loaded["joins"] == loaded["relationships"]
    assert "joins" not in plain
    assert plain["source_type"] == "hive"

    fallback_manager = FallbackManager()

    class FallbackSemanticService:
        async def get_runtime_config_async(self, datasource_id, config_id, user_id=""):
            return await fallback_manager.get_semantic_config_async(datasource_id, config_id)

    monkeypatch.setattr(compiler_module, "get_datasource_manager", lambda: fallback_manager)
    monkeypatch.setattr(compiler_module, "get_semantic_config_service", lambda: FallbackSemanticService())
    fallback = await compiler_module.load_runtime_semantic_config("ds_b", "cfg_b")
    assert "source_type" not in fallback
    assert fallback["joins"] == [{"left_table": "x"}]


@pytest.mark.asyncio
async def test_compile_semantic_builder_sql_uses_runtime_config(monkeypatch):
    async def fake_load_runtime_semantic_config(datasource_id, semantic_config_id):
        assert datasource_id == "ds_runtime"
        assert semantic_config_id == "cfg_runtime"
        return build_semantic_config()

    monkeypatch.setattr(compiler_module, "load_runtime_semantic_config", fake_load_runtime_semantic_config)

    result = await compiler_module.compile_semantic_builder_sql(
        "ds_runtime",
        "cfg_runtime",
        {"dimensions": [{"semanticName": "城市"}], "limit": 10},
    )

    assert "SELECT" in result["sql"]
    assert result["builder_config"]["dimensions"][0]["column"] == "city"


@pytest.mark.asyncio
async def test_load_runtime_semantic_config_rejects_other_users_private_config(monkeypatch):
    class PrivateConfigManager:
        async def get_datasource_async(self, _datasource_id):
            return {"id": "ds_private", "source_type": "mysql"}

        async def get_runtime_semantic_config_async(self, _datasource_id, config_id, user_id=""):
            if config_id == "cfg_private" and user_id == "owner_user":
                return {"id": config_id, "enabled": True, "metrics": [], "dimensions": []}
            return None

    private_manager = PrivateConfigManager()

    class PrivateSemanticService:
        async def get_runtime_config_async(self, datasource_id, config_id, user_id=""):
            return await private_manager.get_runtime_semantic_config_async(datasource_id, config_id, user_id)

    monkeypatch.setattr(compiler_module, "get_datasource_manager", lambda: private_manager)
    monkeypatch.setattr(compiler_module, "get_semantic_config_service", lambda: PrivateSemanticService())

    owner_config = await compiler_module.load_runtime_semantic_config("ds_private", "cfg_private", user_id="owner_user")
    assert owner_config["id"] == "cfg_private"
    with pytest.raises(LookupError, match="语义配置不存在"):
        await compiler_module.load_runtime_semantic_config("ds_private", "cfg_private", user_id="other_user")


def test_semantic_query_service_remaining_join_and_metric_branches(monkeypatch):
    config = build_semantic_config()
    config["metrics"].append({"name": "总行数", "expression": "COUNT(*)", "table": "orders"})
    generator = ai_compiler_module.SemanticAIQueryAdapter(config)

    assert generator._normalize_metrics_allow_dimension_raw(["状态"])[0][0]["_from_dimension"] is True
    assert generator._collect_tables_from_filter_node("bad", set()) is None
    assert generator._map_filter_node_for_builder("bad") == {}

    generator = ai_compiler_module.SemanticAIQueryAdapter(config)
    reverse_joins = generator._build_builder_joins("u", {"u", "orders"})
    assert reverse_joins[0]["leftAlias"] == "u"
    assert reverse_joins[0]["rightAlias"] == "orders"

    no_path_config = build_semantic_config()
    no_path_config["relationships"] = []
    no_path_generator = ai_compiler_module.SemanticAIQueryAdapter(no_path_config)
    with pytest.raises(ValueError, match="缺少.*关联路径"):
        no_path_generator._build_builder_joins("orders", {"orders", "u"})

    result = generator.generate_sql(metrics=["总行数"], dimensions=[], filters=[], having=[], user_query="查询明细列表")
    assert result["success"] is True

    def fail_compile(*_args, **_kwargs):
        raise RuntimeError("compile down")

    monkeypatch.setattr("apps.chat_data_app.services.semantic.core.semantic_sql.query_plan_compiler.compile_query_plan_sql", fail_compile)
    failed = generator.generate_sql(metrics=["销售额"], dimensions=["城市"], filters=[], having=[])
    assert failed["success"] is False
    assert "SQL 转换失败" in failed["error"]


def test_semantic_query_service_table_validation_and_prompt_edges():
    config = {
        "source_type": "mysql",
        "tables": [{"table_name": "orders", "columns": [{"name": f"c{i}"} for i in range(12)]}],
        "metrics": [{"name": "销售额", "expression": "SUM(orders.c1)", "table": "orders"}],
        "dimensions": [{"name": "城市", "table": "orders", "column": "c2"}],
        "relationships": [],
    }
    generator = ai_compiler_module.SemanticAIQueryAdapter(config)

    assert generator._validate_table_column("missing", "c1").startswith("表 'missing' 不存在")
    missing_column = generator._validate_table_column("orders", "missing")
    assert "可用字段" in missing_column
    assert "..." in missing_column
