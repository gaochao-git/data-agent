"""当前 DSL2SQL 主链路测试。"""

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.schema import ExecuteSemanticInput
from apps.chat_data_app.semantic.compiler import SemanticSQLGenerator


def build_config(source_type: str = "mysql") -> dict:
    """构造当前 compiler 使用的语义配置。"""
    return {
        "id": "orders_config",
        "name": "订单配置",
        "source_type": source_type,
        "metrics": [
            {"name": "订单数", "expression": "COUNT(DISTINCT orders.order_id)", "table": "orders"},
            {"name": "销售额", "expression": "SUM(order_items.amount)", "table": "order_items"},
        ],
        "dimensions": [
            {"name": "下单时间", "column": "order_time", "table": "orders", "type": "time"},
            {"name": "订单状态", "column": "status", "table": "orders", "type": "categorical", "allowed_values": ["paid", "cancelled"], "mapping": {"已支付": "paid"}},
            {"name": "用户等级", "column": "level", "table": "users", "type": "categorical"},
        ],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "users",
                "join_type": "left",
                "join_conditions": [{"left_column": "user_id", "right_column": "user_id"}],
                "relationship_type": "many_to_one",
            },
            {
                "left_table": "orders",
                "right_table": "order_items",
                "join_type": "left",
                "join_conditions": [{"left_column": "order_id", "right_column": "order_id"}],
                "relationship_type": "one_to_many",
            },
        ],
    }


def generate(config: dict, payload: dict) -> str:
    """生成 SQL，失败时抛出错误便于断言。"""
    result = SemanticSQLGenerator(config).generate_sql(
        metrics=payload.get("metrics", []),
        dimensions=payload.get("dimensions", []),
        filters=payload.get("filters", []),
        having=payload.get("having", []),
        order_by=payload.get("order_by", []),
        limit=payload.get("limit"),
        offset=payload.get("offset"),
        user_query=payload.get("user_query"),
        compare=payload.get("compare"),
    )
    if not result.get("success"):
        raise ValueError(result.get("error"))
    return result["sql"]


def generate_result(config: dict, payload: dict) -> dict:
    """直接返回生成结果，便于断言失败路径。"""
    return SemanticSQLGenerator(config).generate_sql(
        metrics=payload.get("metrics", []),
        dimensions=payload.get("dimensions", []),
        filters=payload.get("filters", []),
        having=payload.get("having", []),
        order_by=payload.get("order_by", []),
        limit=payload.get("limit"),
        offset=payload.get("offset"),
        user_query=payload.get("user_query"),
        compare=payload.get("compare"),
    )


def test_generate_aggregate_sql_with_filter_does_not_add_default_limit():
    sql = generate(
        build_config(),
        {
            "metrics": ["订单数"],
            "dimensions": [],
            "filters": [{"dimension": "订单状态", "operator": "=", "value": "paid"}],
        },
    )

    assert "COUNT(DISTINCT order_id) AS `订单数`" in sql
    assert "FROM orders" in sql
    assert "WHERE `status` = 'paid'" in sql
    assert "LIMIT" not in sql


def test_generate_aggregate_order_by_metric_uses_aggregate_expression_for_hive():
    sql = generate(
        build_config("hive"),
        {
            "metrics": ["订单数"],
            "dimensions": ["订单状态"],
            "order_by": [{"field": "订单数", "direction": "DESC"}],
            "limit": 10,
        },
    )

    assert "COUNT(DISTINCT order_id) AS `订单数`" in sql
    assert "GROUP BY `status`" in sql
    assert "ORDER BY COUNT(DISTINCT order_id) DESC" in sql
    assert "ORDER BY orders.order_id DESC" not in sql


def test_generate_having_keeps_metric_expression_when_metric_not_selected():
    config = build_config()
    config["metrics"].append({
        "name": "明细销售额",
        "expression": "SUM(order_items.quantity * order_items.unit_price)",
        "table": "order_items",
    })
    sql = generate(
        config,
        {
            "metrics": [],
            "dimensions": ["用户等级"],
            "having": [{"metric": "明细销售额", "operator": ">", "value": 100}],
        },
    )

    assert "HAVING SUM(order_items.quantity * order_items.unit_price) > 100" in sql
    assert "GROUP BY users.`level`" in sql
    assert "SUM(order_items.`unit_price`)" not in sql


def test_generate_order_by_rejects_field_outside_query_outputs():
    with pytest.raises(ValueError, match="不在当前查询输出字段中"):
        generate(
            build_config("hive"),
            {
                "metrics": ["订单数"],
                "dimensions": ["订单状态"],
                "order_by": [{"field": "下单时间", "direction": "DESC"}],
                "limit": 10,
            },
        )


def test_generate_time_grain_uses_source_type_dialect():
    sql = generate(
        build_config("hive"),
        {
            "metrics": ["销售额"],
            "dimensions": [{"name": "下单时间", "time_grain": "month"}],
            "limit": 20,
        },
    )

    assert "DATE_FORMAT(orders.`order_time`, 'yyyy-MM')" in sql
    assert "%Y-%m" not in sql
    assert "GROUP BY DATE_FORMAT(orders.`order_time`, 'yyyy-MM')" in sql


def test_generate_join_path_for_cross_table_metric_and_dimension():
    sql = generate(
        build_config(),
        {
            "metrics": ["销售额"],
            "dimensions": ["用户等级"],
            "filters": [{"dimension": "订单状态", "operator": "=", "value": "paid"}],
        },
    )

    assert "FROM users" in sql
    assert "JOIN orders ON users.user_id = orders.user_id" in sql
    assert "JOIN order_items ON orders.order_id = order_items.order_id" in sql
    assert "SUM(order_items.amount) AS `销售额`" in sql


def test_compare_is_rejected_by_dsl2sql_policy():
    with pytest.raises(ValueError, match="不再支持 compare"):
        generate(
            build_config(),
            {
                "metrics": ["订单数"],
                "dimensions": [{"name": "下单时间", "time_grain": "month"}],
                "compare": {"type": "yoy", "mode": "trend", "strategy": "calc"},
            },
        )


def test_generator_returns_config_not_support_errors():
    result = generate_result(build_config(), {"metrics": [], "dimensions": []})
    missing_metric = generate_result(build_config(), {"metrics": ["不存在指标"]})
    missing_dimension = generate_result(build_config(), {"metrics": ["订单数"], "dimensions": ["不存在维度"]})
    bad_limit = generate_result(build_config(), {"metrics": ["订单数"], "limit": 0})
    bad_offset = generate_result(build_config(), {"metrics": ["订单数"], "offset": -1})

    assert result["success"] is False
    assert "至少需要一个指标或维度" in result["error"]
    assert "指标 '不存在指标' 不存在" in missing_metric["error"]
    assert "维度 '不存在维度' 不存在" in missing_dimension["error"]
    assert "LIMIT 必须为正整数" in bad_limit["error"]
    assert "OFFSET 必须为非负整数" in bad_offset["error"]


def test_invalid_allowed_value_is_rejected():
    with pytest.raises(ValueError, match="不在允许值范围"):
        generate(
            build_config(),
            {
                "metrics": ["订单数"],
                "filters": [{"dimension": "订单状态", "operator": "=", "value": "unknown"}],
            },
        )


def test_allowed_value_mapping_is_accepted():
    sql = generate(
        build_config(),
        {
            "metrics": ["订单数"],
            "filters": [{"dimension": "订单状态", "operator": "=", "value": "已支付"}],
        },
    )

    assert "WHERE `status` = 'paid'" in sql


def test_tree_filter_normalizes_allowed_value_mapping():
    sql = generate(
        build_config(),
        {
            "metrics": ["订单数"],
            "filters": [
                {
                    "and": [
                        {"dimension": "订单状态", "operator": "=", "value": "已支付"},
                        {"dimension": "下单时间", "operator": "IS NOT NULL"},
                    ]
                }
            ],
        },
    )

    assert "`status` = 'paid'" in sql
    assert "已支付" not in sql


def test_literal_value_type_is_preserved_for_sql_like_function_text():
    sql = generate(
        build_config(),
        {
            "metrics": ["订单数"],
            "filters": [
                {"dimension": "下单时间", "operator": "<=", "value": "current_date()", "value_type": "literal"}
            ],
        },
    )

    assert "`order_time` <= 'current_date()'" in sql


def test_expression_filter_fields_are_preserved():
    sql = generate(
        build_config(),
        {
            "metrics": ["订单数"],
            "filters": [
                {
                    "dimension": "下单时间",
                    "operator": ">=",
                    "valueType": "expression",
                    "rightExpression": "DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)",
                }
            ],
        },
    )

    assert "`order_time` >= DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)" in sql
    assert "NULL" not in sql


def test_tree_filter_preserves_advanced_filter_fields():
    sql = generate(
        build_config(),
        {
            "metrics": ["订单数"],
            "filters": [
                {
                    "and": [
                        {"dimension": "订单状态", "operator": "=", "value": "paid"},
                        {
                            "dimension": "下单时间",
                            "operator": ">=",
                            "valueType": "expression",
                            "rightExpression": "DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)",
                        },
                    ]
                }
            ],
        },
    )

    assert "`status` = 'paid'" in sql
    assert "`order_time` >= DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)" in sql


def test_generate_having_order_offset_and_params_block():
    result = generate_result(
        build_config(),
        {
            "metrics": ["订单数"],
            "dimensions": ["订单状态"],
            "having": [{"metric": "订单数", "operator": ">", "value": 5}],
            "order_by": [{"field": "订单状态", "direction": "ASC"}],
            "limit": 10,
            "offset": 5,
        },
    )

    assert result["success"] is True
    assert "HAVING COUNT(DISTINCT order_id) > 5" in result["sql"]
    assert "ORDER BY `订单状态` ASC" in result["sql"]
    assert "LIMIT 5, 10" in result["sql"]
    assert "**分组过滤**: 订单数 > 5" in result["params_block"]
    assert "**跳过条数**: 5" in result["params_block"]


def test_generate_rejects_having_unknown_metric_and_order_direction():
    bad_having = generate_result(build_config(), {
        "metrics": ["订单数"],
        "having": [{"metric": "不存在指标", "operator": ">", "value": 1}],
    })
    bad_order = generate_result(build_config(), {
        "metrics": ["订单数"],
        "order_by": [{"field": "订单数", "direction": "bad"}],
    })

    assert bad_having["success"] is False
    assert "HAVING 条件中的指标" in bad_having["error"]
    assert bad_order["success"] is False
    assert "排序方向" in bad_order["error"]


def test_generate_detail_intent_turns_aggregate_metric_into_raw_field():
    config = build_config()
    config["metrics"].append({"name": "订单金额最大值", "expression": "MAX(orders.amount)", "table": "orders"})

    sql = generate(
        config,
        {
            "metrics": ["订单金额最大值"],
            "dimensions": [],
            "filters": [],
            "user_query": "查看最大订单金额对应的明细列表",
            "limit": 10,
        },
    )

    assert "COUNT(" not in sql
    assert "MAX(" not in sql
    assert "amount AS `订单金额最大值`" in sql
    assert "GROUP BY" not in sql


def test_generate_metric_name_with_detail_word_is_not_detail_intent():
    config = build_config()
    config["metrics"] = [
        {"name": "订单明细数量", "expression": "COUNT(orders.order_id)", "table": "orders"},
    ]

    sql = generate(
        config,
        {
            "metrics": ["订单明细数量"],
            "user_query": "查询订单明细数量",
        },
    )

    assert "COUNT(order_id) AS `订单明细数量`" in sql


def test_generate_dimension_in_metrics_as_raw_detail_field():
    sql = generate(
        build_config(),
        {
            "metrics": ["订单状态"],
            "filters": [{"dimension": "订单状态", "operator": "=", "value": "paid"}],
            "limit": 10,
        },
    )

    assert "status AS `订单状态`" in sql
    assert "COUNT(" not in sql


def test_generate_raw_metric_becomes_group_dimension_for_aggregate_query():
    config = build_config()
    config["metrics"].append({"name": "订单编号", "expression": "orders.order_id", "table": "orders", "column": "order_id"})

    sql = generate(
        config,
        {
            "metrics": ["订单编号", "订单数"],
            "dimensions": ["订单状态"],
            "limit": 10,
        },
    )

    assert "order_id AS `订单编号`" in sql
    assert "GROUP BY `status`, order_id" in sql


def test_generate_compile_failure_returns_error(monkeypatch):
    import apps.chat_data_app.semantic.compiler.semantic_sql_generator as generator_module

    monkeypatch.setattr(generator_module, "compile_builder_sql", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("compile boom")))

    result = generate_result(build_config(), {"metrics": ["订单数"]})

    assert result["success"] is False
    assert result["error"] == "错误：SQL 转换失败 - compile boom"


def test_generator_helper_branches_for_tables_and_joins():
    generator = SemanticSQLGenerator(build_config())

    assert generator._parse_aggregate_expression("") is None
    assert generator._parse_aggregate_expression("orders.amount") is None
    assert generator._has_detail_intent("查看列表") is True
    assert generator._has_detail_intent("") is False
    assert generator._detail_intent_only_from_metric_name("查询订单明细数量", [{"name": "订单明细数量"}]) is True
    assert generator._validate_table_column("orders", "missing") is None
    assert generator._validate_table_column("missing", "id").startswith("表 'missing' 不存在")
    assert generator._map_filter_node_for_builder({"or": [{"dimension": "订单状态", "operator": "=", "value": "paid"}]}) == {
        "or": [{"table": "orders", "tableAlias": "orders", "column": "status", "expression": None, "operator": "=", "value": "paid", "logic": "AND"}]
    }
    tables_needed = set()
    generator._collect_tables_from_filter_node({"and": [{"table": "orders"}, {"or": [{"table": "users"}]}]}, tables_needed)
    assert tables_needed == {"orders", "users"}


def test_generator_table_column_validation_with_explicit_tables():
    config = build_config()
    config["tables"] = [{"table_name": "orders", "columns": [{"name": "order_id"}]}]
    generator = SemanticSQLGenerator(config)

    assert generator._validate_table_column("orders", "order_id") is None
    assert "不存在字段 'status'" in generator._validate_table_column("orders", "status")


def test_generator_helper_edges_for_non_dict_nodes_and_missing_joins():
    generator = SemanticSQLGenerator(build_config())

    tables_needed = set()
    generator._collect_tables_from_filter_node("bad", tables_needed)
    assert tables_needed == set()
    assert generator._map_filter_node_for_builder("bad") == {}

    config = build_config()
    config["relationships"] = [
        {"left_table": "", "right_table": "users", "join_conditions": [{"left_column": "id", "right_column": "id"}]},
        {"left_table": "orders", "right_table": "users", "join_conditions": [{"left_column": "", "right_column": "user_id"}]},
    ]
    generator = SemanticSQLGenerator(config)
    joins = generator._build_builder_joins("orders", {"orders", "users"})
    assert joins[0]["joinConditions"] == []
    assert generator._build_builder_joins("orders", {"orders", "payments"}) == []


def test_generator_reversed_join_and_legacy_left_right_columns(monkeypatch):
    import apps.chat_data_app.semantic.compiler.semantic_sql_generator as generator_module

    captured = {}

    def fake_compile_builder_sql(builder_config, dialect="mysql"):
        captured["builder_config"] = builder_config
        captured["dialect"] = dialect
        return "SELECT 1"

    monkeypatch.setattr(generator_module, "compile_builder_sql", fake_compile_builder_sql)
    config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "销售额", "expression": "SUM(order_items.amount)", "table": "order_items", "table_alias": "items"},
        ],
        "dimensions": [
            {"name": "订单状态", "column": "status", "table": "orders", "table_alias": "orders"},
        ],
        "relationships": [
            {
                "left_table": "orders",
                "left_alias": "orders",
                "right_table": "order_items",
                "right_alias": "items",
                "join_type": "left",
                "left_column": "order_id",
                "right_column": "order_id",
            }
        ],
    }

    result = SemanticSQLGenerator(config).generate_sql(
        metrics=["销售额"],
        dimensions=[],
        filters=[{"dimension": "订单状态", "operator": "=", "value": "paid"}],
        having=[],
        order_by=[],
        limit=10,
    )

    assert result["success"] is True
    joins = captured["builder_config"]["joins"]
    assert joins[0]["id"] == "items-orders"
    assert joins[0]["leftTable"] == "order_items"
    assert joins[0]["rightTable"] == "orders"
    assert joins[0]["leftColumn"] == "order_id"
    assert joins[0]["rightColumn"] == "order_id"


def test_generator_captures_builder_config_for_count_star_and_metric_columns(monkeypatch):
    import apps.chat_data_app.semantic.compiler.semantic_sql_generator as generator_module

    captured = {}

    def fake_compile_builder_sql(builder_config, dialect="mysql"):
        captured["builder_config"] = builder_config
        return "SELECT captured"

    monkeypatch.setattr(generator_module, "compile_builder_sql", fake_compile_builder_sql)
    config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "订单数", "expression": "COUNT(*)", "table": "orders"},
            {"name": "支付金额", "expression": "SUM(`pay_amount`)", "table": "orders"},
            {"name": "最大金额", "expression": "MAX(pay_amount)", "table": "orders", "column": "pay_amount"},
            {"name": "原始金额", "expression": "", "table": "orders", "column": "pay_amount"},
        ],
        "dimensions": [
            {"name": "订单状态", "column": "status", "table": "orders"},
        ],
        "relationships": [],
    }
    generator = SemanticSQLGenerator(config)

    detail = generator.generate_sql(
        metrics=["订单数"],
        dimensions=[],
        filters=[],
        having=[],
        order_by=[],
        user_query="查看明细列表",
    )
    assert detail["success"] is False
    assert "未找到可用于查询的字段" in detail["error"]

    result = generator.generate_sql(
        metrics=["支付金额"],
        dimensions=["订单状态"],
        filters=[],
        having=[
            {"metric": "最大金额", "operator": ">", "value": 100},
            {"metric": "原始金额", "operator": ">", "value": 10},
        ],
        order_by=[
            {"field": "支付金额", "direction": "DESC"},
            {"field": "订单状态", "direction": "ASC"},
        ],
    )

    assert result["success"] is True
    builder_config = captured["builder_config"]
    assert builder_config["aggregations"][0]["column"] == "pay_amount"
    having_items = [item for item in builder_config["filters"] if str(item.get("id", "")).startswith("having-")]
    assert having_items[0]["column"] == "pay_amount"
    assert having_items[1]["column"] == "pay_amount"
    assert builder_config["orderBy"][0]["column"] == "pay_amount"
    assert builder_config["orderBy"][1]["column"] == "status"


def test_generator_dimension_metric_raw_fallback_and_table_collection(monkeypatch):
    import apps.chat_data_app.semantic.compiler.semantic_sql_generator as generator_module

    captured = {}

    def fake_compile_builder_sql(builder_config, dialect="mysql"):
        captured["builder_config"] = builder_config
        return "SELECT raw"

    monkeypatch.setattr(generator_module, "compile_builder_sql", fake_compile_builder_sql)
    config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "订单数", "expression": "COUNT(*)", "table": "orders"},
        ],
        "dimensions": [
            {"name": "表达式状态", "column": "status", "table": "orders", "expression": "CASE WHEN status='paid' THEN '已支付' END"},
            {"name": "无表字段", "column": "", "table": ""},
        ],
        "relationships": [],
    }
    generator = SemanticSQLGenerator(config)
    monkeypatch.setattr(generator.validator, "validate_dimensions", lambda dimensions: (dimensions, None))
    monkeypatch.setattr(generator.validator, "validate_filters", lambda filters: (filters, None))
    monkeypatch.setattr(generator.validator, "validate_having", lambda having: (having, None))
    monkeypatch.setattr(generator.validator, "validate_order_by", lambda order_by, metrics, dimensions: (order_by, None))
    monkeypatch.setattr(generator.validator, "validate_limit", lambda limit: (limit, None))
    monkeypatch.setattr(generator.validator, "validate_offset", lambda offset: (offset, None))

    result = generator.generate_sql(
        metrics=["表达式状态"],
        dimensions=[],
        filters=[
            {
                "and": [
                    {"dimension": "表达式状态", "operator": "=", "value": "已支付", "rightExpression": "status"},
                    {"or": [{"dimension": "表达式状态", "operator": "!=", "value": "取消"}]},
                ]
            }
        ],
        having=[],
        order_by=[],
        user_query="查看表达式状态列表",
    )
    assert result["success"] is True
    builder_config = captured["builder_config"]
    assert builder_config["aggregations"][0]["func"] == "RAW"
    assert builder_config["aggregations"][0]["expression"] == "CASE WHEN status='paid' THEN '已支付' END"
    assert builder_config["filters"][0]["and"][0]["rightExpression"] == "status"

    missing_expression = generator._normalize_metrics_allow_dimension_raw(["无表字段"])
    assert missing_expression[0][0]["expression"] == ""


def test_generator_additional_helper_and_join_edges(monkeypatch):
    import apps.chat_data_app.semantic.compiler.semantic_sql_generator as generator_module

    captured = {}

    def fake_compile_builder_sql(builder_config, dialect="mysql"):
        captured["builder_config"] = builder_config
        captured["dialect"] = dialect
        return "SELECT joined"

    monkeypatch.setattr(generator_module, "compile_builder_sql", fake_compile_builder_sql)

    config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "订单数", "expression": "COUNT(DISTINCT orders.order_id)", "table": "orders"},
            {"name": "销售额", "expression": "SUM(order_items.amount)", "table": "order_items", "table_alias": "items"},
            {"name": "原始订单号", "expression": "orders.order_no", "table": "orders", "column": "order_no"},
        ],
        "dimensions": [
            {"name": "订单状态", "column": "status", "table": "orders"},
            {"name": "商品类型", "column": "category", "table": "products", "table_alias": "p"},
        ],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "order_items",
                "right_alias": "items",
                "join_type": "left",
                "join_conditions": [
                    {"left_column": "", "right_column": "ignored"},
                    {"left_column": "order_id", "right_column": "order_id"},
                ],
            },
            {
                "left_table": "order_items",
                "left_alias": "items",
                "right_table": "products",
                "right_alias": "p",
                "join_type": "inner",
                "left_column": "product_id",
                "right_column": "id",
            },
            {"left_table": "", "right_table": "ignored"},
        ],
        "tables": [
            {"table_name": "", "columns": [{"name": "ignored"}]},
            {"table_name": "orders", "columns": [{"name": "order_id"}, {}, {"name": "status"}, {"name": "order_no"}]},
        ],
    }
    generator = SemanticSQLGenerator(config)

    assert generator._parse_aggregate_expression("count(distinct orders.order_id)") == ("COUNT_DISTINCT", "orders.order_id")
    assert generator._has_detail_intent("detail list") is True
    assert generator._detail_intent_only_from_metric_name("", [{"name": "订单明细数量"}]) is False
    assert generator._validate_table_column("orders", "status") is None
    assert "不存在字段" in generator._validate_table_column("orders", "missing")
    assert generator._determine_query_intent_type(
        metrics=["原始订单号"],
        matched_metrics=[config["metrics"][2]],
        dimensions=[],
        having=[{"metric": "订单数", "operator": ">", "value": 1}],
        user_query="查看明细",
    ) == "detail"
    assert generator._determine_query_intent_type(
        metrics=[],
        matched_metrics=[],
        dimensions=[],
        having=[{"metric": "订单数", "operator": ">", "value": 1}],
        user_query="订单明细数量",
    ) == "detail"

    result = generator.generate_sql(
        metrics=["销售额"],
        dimensions=["商品类型"],
        filters=[{"dimension": "订单状态", "operator": "=", "value": "paid"}],
        having=[],
        order_by=[{"field": "商品类型", "direction": "ASC"}],
    )

    assert result["success"] is True
    builder_config = captured["builder_config"]
    assert builder_config["mainAlias"] == "p"
    assert builder_config["joins"][0]["rightAlias"] == "items"
    assert builder_config["joins"][0]["joinConditions"] == [{"leftTable": "products", "leftAlias": "p", "leftColumn": "id", "rightColumn": "product_id"}]
    assert builder_config["joins"][1]["rightAlias"] == "orders"
    assert builder_config["joins"][1]["joinConditions"] == [{"leftTable": "order_items", "leftAlias": "items", "leftColumn": "order_id", "rightColumn": "order_id"}]
    assert builder_config["orderBy"][0]["column"] == "category"


def test_generator_builder_config_branches_for_metric_columns_and_table_sets(monkeypatch):
    import apps.chat_data_app.semantic.compiler.semantic_sql_generator as generator_module

    captured = {}

    def fake_compile_builder_sql(builder_config, dialect="mysql"):
        captured["builder_config"] = builder_config
        return "SELECT branches"

    monkeypatch.setattr(generator_module, "compile_builder_sql", fake_compile_builder_sql)

    config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "订单数", "expression": "COUNT(*)", "table": "orders"},
            {"name": "支付金额", "expression": "SUM(`pay_amount`)", "table": "orders"},
            {"name": "最大金额", "expression": "MAX(pay_amount)", "table": "orders", "column": "pay_amount"},
            {"name": "明细金额", "expression": "orders.pay_amount", "table": "orders", "column": "pay_amount"},
        ],
        "dimensions": [
            {"name": "订单状态", "column": "status", "table": "orders"},
        ],
        "relationships": [],
    }
    generator = SemanticSQLGenerator(config)
    monkeypatch.setattr(generator.validator, "validate_dimensions", lambda dimensions: (dimensions, None))
    monkeypatch.setattr(generator.validator, "validate_filters", lambda filters: (filters, None))
    monkeypatch.setattr(generator.validator, "validate_having", lambda having: (having, None))
    monkeypatch.setattr(generator.validator, "validate_order_by", lambda order_by, metrics, dimensions: (order_by, None))
    monkeypatch.setattr(generator.validator, "validate_limit", lambda limit: (limit, None))
    monkeypatch.setattr(generator.validator, "validate_offset", lambda offset: (offset, None))

    result = generator.generate_sql(
        metrics=["支付金额", "最大金额"],
        dimensions=["订单状态"],
        filters=[],
        having=[],
        order_by=[
            {"field": "最大金额", "direction": "DESC"},
            {"field": "订单状态", "direction": "ASC"},
        ],
    )

    assert result["success"] is True
    builder_config = captured["builder_config"]
    assert builder_config["aggregations"][0]["column"] == "pay_amount"
    assert builder_config["orderBy"][0]["column"] == "pay_amount"
    assert builder_config["orderBy"][1]["column"] == "status"

    mixed = generator.generate_sql(
        metrics=["明细金额", "支付金额"],
        dimensions=[],
        filters=[],
        having=[],
        order_by=[],
    )
    assert mixed["success"] is True
    assert captured["builder_config"]["dimensions"][0]["alias"] == "明细金额"
    assert captured["builder_config"]["aggregations"][0]["alias"] == "支付金额"

    result = generator.generate_sql(
        metrics=["订单数"],
        dimensions=[],
        filters=[],
        having=[],
        order_by=[],
    )
    assert result["success"] is True
    assert captured["builder_config"]["aggregations"][0]["column"] == "*"


def test_generator_error_validation_edges():
    config = build_config()
    generator = SemanticSQLGenerator(config)

    compare = generator.generate_sql(metrics=["订单数"], dimensions=[], filters=[], having=[], compare={"type": "yoy"})
    assert compare["success"] is False
    assert "compare" in compare["error"]

    bad_filter = generator.generate_sql(
        metrics=["订单数"],
        dimensions=[],
        filters=[{"dimension": "不存在维度", "operator": "=", "value": 1}],
        having=[],
    )
    assert bad_filter["success"] is False
    assert "过滤条件中的维度" in bad_filter["error"]

    bad_having = generator.generate_sql(
        metrics=["订单数"],
        dimensions=[],
        filters=[],
        having=[{"metric": "不存在指标", "operator": ">", "value": 1}],
    )
    assert bad_having["success"] is False
    assert "HAVING 条件中的指标" in bad_having["error"]


def test_generator_remaining_branch_edges(monkeypatch):
    import apps.chat_data_app.semantic.compiler.semantic_sql_generator as generator_module

    captured = {}

    def fake_compile_builder_sql(builder_config, dialect="mysql"):
        captured["builder_config"] = builder_config
        return "SELECT remaining"

    monkeypatch.setattr(generator_module, "compile_builder_sql", fake_compile_builder_sql)

    config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "数量", "expression": "COUNT(id)", "table": "orders"},
            {"name": "原始金额", "expression": "orders.amount", "table": "orders", "column": "amount"},
            {"name": "无列指标", "expression": "", "table": "orders"},
        ],
        "dimensions": [
            {"name": "缺失维度", "table": "orders", "column": ""},
            {"name": "状态", "table": "orders", "column": "status"},
        ],
        "relationships": [
            {"left_table": "orders", "right_table": "users", "join_type": "left", "join_conditions": []},
        ],
    }
    generator = SemanticSQLGenerator(config)

    assert generator._extract_dimension_name({"bad": "x"}) == ""
    assert generator._extract_time_grain("状态") is None
    assert generator._relationship_left_alias({"left_alias": "o", "left_table": "orders"}) == "o"
    assert generator._relationship_right_alias({"right_alias": "u", "right_table": "users"}) == "u"
    assert generator._build_builder_joins("orders", {"orders"}) == []
    assert generator._build_builder_joins("orders", {"orders", "users"})[0]["joinConditions"] == []

    result = generator.generate_sql(
        metrics=["无列指标"],
        dimensions=[],
        filters=[],
        having=[],
        order_by=[],
        user_query="查看明细",
    )
    assert result["success"] is True
    assert captured["builder_config"]["aggregations"][0]["column"] == "*"
    assert captured["builder_config"]["aggregations"][0]["expression"] == ""

    aggregate_raw_star = generator.generate_sql(
        metrics=["数量", "无列指标"],
        dimensions=[],
        filters=[],
        having=[],
        order_by=[],
    )
    assert aggregate_raw_star["success"] is True
    assert any(item["alias"] == "无列指标" and item["func"] == "RAW" for item in captured["builder_config"]["aggregations"])

    missing_dim_cfg = generator.generate_sql(
        metrics=[],
        dimensions=["状态"],
        filters=[],
        having=[],
        order_by=[],
    )
    assert missing_dim_cfg["success"] is True
    assert captured["builder_config"]["dimensions"][0]["transform"] == "raw"

    no_fields = generator.generate_sql(metrics=[], dimensions=["缺失维度"], filters=[], having=[], order_by=[])
    assert no_fields["success"] is True
    assert captured["builder_config"]["dimensions"][0]["column"] == ""


def test_generator_disconnected_join_and_missing_dimension_metric_branches(monkeypatch):
    import apps.chat_data_app.semantic.compiler.semantic_sql_generator as generator_module

    captured = {}

    def fake_compile_builder_sql(builder_config, dialect="mysql"):
        captured["builder_config"] = builder_config
        return "SELECT disconnected"

    monkeypatch.setattr(generator_module, "compile_builder_sql", fake_compile_builder_sql)
    config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "订单数", "expression": "COUNT(*)", "table": "orders"},
            {"name": "无表达式指标", "expression": "", "table": "orders", "column": "status"},
        ],
        "dimensions": [
            {"name": "订单状态", "table": "orders", "column": "status"},
            {"name": "缺列维度", "table": "orders", "column": ""},
            {"name": "商品类型", "table": "products", "column": "category"},
        ],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "users",
                "left_column": "user_id",
                "right_column": "id",
                "join_type": "left",
            }
        ],
    }
    generator = SemanticSQLGenerator(config)

    assert generator._build_builder_joins("orders", {"orders", "products"}) == []
    monkeypatch.setattr(
        generator.validator,
        "validate_dimensions",
        lambda _dimensions: (["不存在维度", "订单状态"], None),
    )

    result = generator.generate_sql(
        metrics=["无表达式指标"],
        dimensions=["不存在维度", "订单状态"],
        filters=[],
        having=[],
        order_by=[
            {"field": "无表达式指标", "direction": "DESC"},
            {"field": "订单状态", "direction": "ASC"},
        ],
        user_query="查看明细",
    )

    assert result["success"] is True
    builder_config = captured["builder_config"]
    assert [item["alias"] for item in builder_config["dimensions"]] == ["订单状态"]
    assert builder_config["aggregations"][0]["column"] == "status"
    assert builder_config["orderBy"][0]["column"] == "status"


def test_generator_tables_needed_empty_objects_do_not_add_tables(monkeypatch):
    import apps.chat_data_app.semantic.compiler.semantic_sql_generator as generator_module

    captured = {}

    def fake_compile_builder_sql(builder_config, dialect="mysql"):
        captured["builder_config"] = builder_config
        return "SELECT empty tables"

    monkeypatch.setattr(generator_module, "compile_builder_sql", fake_compile_builder_sql)
    config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "原始空表", "expression": "", "table": "", "column": ""},
        ],
        "dimensions": [
            {"name": "空表维度", "table": "", "column": ""},
        ],
        "relationships": [],
    }
    generator = SemanticSQLGenerator(config)

    result = generator.generate_sql(
        metrics=["原始空表"],
        dimensions=["空表维度"],
        filters=[],
        having=[],
        order_by=[],
        user_query="查看明细",
    )

    assert result["success"] is False
    assert "查询至少需要一个指标或维度" in result["error"]


def test_generator_remaining_builder_mapping_edges(monkeypatch):
    import apps.chat_data_app.semantic.compiler.semantic_sql_generator as generator_module

    captured = {}

    def fake_compile_builder_sql(builder_config, dialect="mysql"):
        captured["builder_config"] = builder_config
        return "SELECT mapped"

    monkeypatch.setattr(generator_module, "compile_builder_sql", fake_compile_builder_sql)
    config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "总数", "expression": "COUNT(*)", "table": "orders"},
            {"name": "原始状态", "expression": "", "table": "orders", "column": "status"},
            {"name": "列指标", "expression": "", "table": "orders", "column": "amount"},
        ],
        "dimensions": [
            {"name": "订单状态", "table": "orders", "column": "status"},
        ],
        "relationships": [],
    }
    generator = SemanticSQLGenerator(config)
    monkeypatch.setattr(generator.validator, "validate_dimensions", lambda dimensions: (dimensions, None))
    monkeypatch.setattr(generator.validator, "validate_filters", lambda filters: (filters, None))
    monkeypatch.setattr(generator.validator, "validate_having", lambda having: (having, None))
    monkeypatch.setattr(generator.validator, "validate_order_by", lambda order_by, metrics, dimensions: (order_by, None))
    monkeypatch.setattr(generator.validator, "validate_limit", lambda limit: (limit, None))
    monkeypatch.setattr(generator.validator, "validate_offset", lambda offset: (offset, None))

    result = generator.generate_sql(
        metrics=["总数", "原始状态"],
        dimensions=["订单状态"],
        filters=[{"and": [{"dimension": "订单状态", "operator": "=", "value": "paid"}, {"value": "skip-no-table"}]}],
        having=[{"metric": "列指标", "operator": ">", "value": 1}],
        order_by=[{"field": "列指标", "direction": "DESC"}],
    )

    assert result["success"] is True
    builder_config = captured["builder_config"]
    assert any(item["column"] == "*" for item in builder_config["aggregations"])
    assert [item["alias"] for item in builder_config["dimensions"]].count("原始状态") == 0
    having_node = next(item for item in builder_config["filters"] if item.get("id") == "having-列指标")
    assert having_node["column"] == "amount"
    assert builder_config["orderBy"][0]["column"] == "amount"


def test_generator_reversed_join_fallback_and_no_selected_relation():
    config = {
        "source_type": "mysql",
        "metrics": [],
        "dimensions": [],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "users",
                "left_column": "user_id",
                "right_column": "id",
                "join_type": "left",
            },
            {
                "left_table": "products",
                "right_table": "categories",
                "left_column": "category_id",
                "right_column": "id",
                "join_type": "left",
            },
        ],
    }
    generator = SemanticSQLGenerator(config)

    reversed_join = generator._build_builder_joins("users", {"users", "orders"})
    assert reversed_join[0]["leftTable"] == "users"
    assert reversed_join[0]["leftColumn"] == "id"
    assert reversed_join[0]["rightTable"] == "orders"
    assert reversed_join[0]["rightColumn"] == "user_id"

    assert generator._build_builder_joins("orders", {"orders", "categories"}) == []


def test_generator_join_condition_and_star_metric_remaining_branches(monkeypatch):
    import apps.chat_data_app.semantic.compiler.semantic_sql_generator as generator_module

    captured = {}

    def fake_compile_builder_sql(builder_config, dialect="mysql"):
        captured["builder_config"] = builder_config
        return "SELECT branch coverage"

    monkeypatch.setattr(generator_module, "compile_builder_sql", fake_compile_builder_sql)
    config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "总数", "expression": "COUNT(*)", "table": "orders"},
            {"name": "状态原始值", "expression": "", "table": "orders", "column": "status"},
        ],
        "dimensions": [
            {"name": "用户等级", "table": "users", "column": "level"},
        ],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "users",
                "join_type": "left",
                "join_conditions": [{"left_column": "user_id", "right_column": "id"}],
            }
        ],
    }
    generator = SemanticSQLGenerator(config)
    monkeypatch.setattr(generator.validator, "validate_having", lambda having: (having, None))
    monkeypatch.setattr(generator.validator, "validate_order_by", lambda order_by, metrics, dimensions: (order_by, None))

    joins = generator._build_builder_joins("orders", {"orders", "users"})
    assert joins[0]["joinConditions"][0]["leftColumn"] == "user_id"

    result = generator.generate_sql(
        metrics=["总数"],
        dimensions=["用户等级"],
        filters=[],
        having=[{"metric": "总数", "operator": ">", "value": 1}],
        order_by=[{"field": "总数", "direction": "DESC"}, {"field": "用户等级", "direction": "ASC"}],
    )

    assert result["success"] is True
    builder_config = captured["builder_config"]
    assert builder_config["aggregations"][0]["column"] == "*"
    having_node = next(item for item in builder_config["filters"] if item.get("id") == "having-总数")
    assert having_node["column"] == "*"
    assert builder_config["orderBy"][0]["column"] == "*"
    assert builder_config["orderBy"][1]["column"] == "level"


def test_generator_raw_metric_duplicate_dimension_skips_added_dimension(monkeypatch):
    import apps.chat_data_app.semantic.compiler.semantic_sql_generator as generator_module

    captured = {}

    def fake_compile_builder_sql(builder_config, dialect="mysql"):
        captured["builder_config"] = builder_config
        return "SELECT duplicate"

    monkeypatch.setattr(generator_module, "compile_builder_sql", fake_compile_builder_sql)
    config = {
        "source_type": "mysql",
        "metrics": [{"name": "状态原始值", "expression": "", "table": "orders", "column": "status"}],
        "dimensions": [{"name": "订单状态", "table": "orders", "column": "status"}],
        "relationships": [],
    }
    generator = SemanticSQLGenerator(config)

    result = generator.generate_sql(
        metrics=["状态原始值"],
        dimensions=["订单状态"],
        filters=[],
        having=[],
        order_by=[],
    )

    assert result["success"] is True
    builder_config = captured["builder_config"]
    assert [item["column"] for item in builder_config["dimensions"]] == ["status"]


class TestSemanticToolInput:
    """测试语义工具入参模型（mode 已废弃）。"""

    def test_execute_semantic_input_accepts_without_mode(self):
        payload = {
            "metrics": ["订单数"],
            "dimensions": [],
            "filters": [],
            "having": [],
            "order_by": [],
            "limit": 10,
        }
        model = ExecuteSemanticInput.model_validate(payload)
        assert model.metrics == ["订单数"]

    def test_execute_semantic_input_rejects_mode(self):
        payload = {
            "mode": "detail",
            "metrics": ["订单数"],
            "dimensions": [],
            "filters": [],
            "having": [],
            "order_by": [],
            "limit": 10,
        }
        with pytest.raises(ValidationError):
            ExecuteSemanticInput.model_validate(payload)

    def test_execute_semantic_input_allows_injected_state(self):
        payload = {
            "metrics": ["订单数"],
            "dimensions": [],
            "filters": [],
            "having": [],
            "order_by": [],
            "limit": 10,
            "state": {"messages": []},
        }
        model = ExecuteSemanticInput.model_validate(payload)
        assert model.metrics == ["订单数"]

    def test_execute_semantic_input_ignores_visualization(self):
        payload = {
            "metrics": ["总销量"],
            "dimensions": [],
            "filters": [],
            "having": [],
            "order_by": [],
            "limit": 10,
            "visualization": '{"type":"kpi","x_field":null,"y_field":"总销量"}',
        }
        model = ExecuteSemanticInput.model_validate(payload)
        assert not hasattr(model, "visualization")
