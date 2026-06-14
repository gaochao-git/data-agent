"""当前 DSL2SQL 主链路测试。"""

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
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
