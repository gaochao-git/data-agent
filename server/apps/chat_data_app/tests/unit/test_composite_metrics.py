"""复合指标（多指标四则运算公式）端到端测试。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services.semantic.core.config_validator import validate_semantic_fields
from apps.chat_data_app.services.semantic.core.query_plan import is_composite_metric, parse_formula_refs
from apps.chat_data_app.services.semantic.run.chat_data_ossie_converter import ChatDataToOssieConverter
from apps.chat_data_app.services.semantic.run.semantic_ai_query_adapter import SemanticAIQueryAdapter
from apps.chat_data_app.services.semantic.run.semantic_bi_query_adapter import SemanticBIQueryAdapter
from apps.chat_data_app.services.semantic.run.semantic_model_search import _compact_metric_summary
from apps.chat_data_app.services.semantic.run.semantic_query_plan_builder import (
    composite_metric_verbalization,
    resolve_composite_metric,
    substitute_composite_formula,
)
from apps.chat_data_app.services.dashboard.spec_service import _is_aggregate_metric


def build_composite_config() -> dict:
    """构造含复合指标的语义配置。"""
    return {
        "id": "composite_config",
        "name": "复合指标配置",
        "source_type": "mysql",
        "metrics": [
            {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
            {"name": "订单数", "expression": "COUNT(DISTINCT orders.order_id)", "table": "orders"},
            {"name": "成本额", "expression": "SUM(orders.cost)", "table": "orders"},
            {
                "name": "客单价",
                "metric_kind": "composite",
                "formula": "{销售额} / {订单数}",
            },
            {
                "name": "毛利率",
                "metric_kind": "composite",
                "formula": "({销售额} - {成本额}) / {销售额}",
            },
        ],
        "dimensions": [
            {"name": "产品分类", "column": "category_name", "table": "orders"},
        ],
        "relationships": [],
    }


# ---------------------------------------------------------------------------
# query_plan：formula 解析
# ---------------------------------------------------------------------------

def test_parse_formula_refs_ordered_dedup():
    assert parse_formula_refs("{B} + {A} - {B}") == ["B", "A"]


def test_parse_formula_refs_empty_or_bare():
    assert parse_formula_refs("") == []
    assert parse_formula_refs("1 + 2") == []
    assert parse_formula_refs("{ 空格 }") == ["空格"]


def test_is_composite_metric():
    assert is_composite_metric({"metric_kind": "composite"})
    assert is_composite_metric({"metric_kind": " Composite "})
    assert not is_composite_metric({"metric_kind": "derived"})
    assert not is_composite_metric({})
    assert not is_composite_metric(None)


# ---------------------------------------------------------------------------
# plan_builder：解析与代入
# ---------------------------------------------------------------------------

def test_resolve_composite_metric_returns_members_and_table():
    config = build_composite_config()
    metrics_map = {m["name"]: m for m in config["metrics"]}
    plan = resolve_composite_metric(config["metrics"][3], metrics_map)
    assert plan["table"] == "orders"
    assert plan["members"] == [
        {"name": "销售额", "expression": "SUM(orders.amount)"},
        {"name": "订单数", "expression": "COUNT(DISTINCT orders.order_id)"},
    ]


def test_resolve_composite_metric_non_composite_returns_none():
    config = build_composite_config()
    metrics_map = {m["name"]: m for m in config["metrics"]}
    assert resolve_composite_metric(config["metrics"][0], metrics_map) is None


def test_resolve_composite_metric_rejects_formula_without_refs():
    with pytest.raises(ValueError, match="没有任何"):
        resolve_composite_metric(
            {"name": "X", "metric_kind": "composite", "formula": "1 + 2"}, {},
        )


def test_resolve_composite_metric_rejects_missing_ref():
    config = build_composite_config()
    metrics_map = {m["name"]: m for m in config["metrics"]}
    with pytest.raises(ValueError, match="引用的指标不存在"):
        resolve_composite_metric(
            {"name": "X", "metric_kind": "composite", "formula": "{不存在} + 1"}, metrics_map,
        )


def test_resolve_composite_metric_rejects_composite_ref():
    config = build_composite_config()
    metrics_map = {m["name"]: m for m in config["metrics"]}
    with pytest.raises(ValueError, match="不能引用其他复合指标"):
        resolve_composite_metric(
            {"name": "X", "metric_kind": "composite", "formula": "{客单价} + 1"}, metrics_map,
        )


def test_resolve_composite_metric_rejects_derived_ref():
    metrics_map = {
        "派生": {"name": "派生", "metric_kind": "derived", "base_metric": "销售额"},
        "销售额": {"name": "销售额", "expression": "SUM(t.a)", "table": "t"},
    }
    with pytest.raises(ValueError, match="暂不支持派生指标"):
        resolve_composite_metric(
            {"name": "X", "metric_kind": "composite", "formula": "{派生} + 1"}, metrics_map,
        )


def test_resolve_composite_metric_rejects_raw_ref():
    metrics_map = {"原始值": {"name": "原始值", "expression": "order_id", "table": "orders"}}
    with pytest.raises(ValueError, match="不是聚合指标"):
        resolve_composite_metric(
            {"name": "X", "metric_kind": "composite", "formula": "{原始值} + 1"}, metrics_map,
        )


def test_resolve_composite_metric_rejects_cross_table_members():
    metrics_map = {
        "A": {"name": "A", "expression": "SUM(t1.a)", "table": "t1"},
        "B": {"name": "B", "expression": "SUM(t2.b)", "table": "t2"},
    }
    with pytest.raises(ValueError, match="跨表"):
        resolve_composite_metric(
            {"name": "X", "metric_kind": "composite", "formula": "{A} / {B}"}, metrics_map,
        )


def test_substitute_composite_formula_wraps_division_with_nullif():
    sql = substitute_composite_formula("{A} / {B}", [
        {"name": "A", "expression": "SUM(t.a)"},
        {"name": "B", "expression": "COUNT(t.b)"},
    ])
    assert sql == "SUM(t.a) / NULLIF(COUNT(t.b), 0)"


def test_substitute_composite_formula_keeps_addition():
    sql = substitute_composite_formula("{A} + {B} * 2", [
        {"name": "A", "expression": "SUM(t.a)"},
        {"name": "B", "expression": "SUM(t.b)"},
    ])
    assert sql == "SUM(t.a) + SUM(t.b) * 2"


def test_composite_metric_verbalization_with_members():
    config = build_composite_config()
    metrics_map = {m["name"]: m for m in config["metrics"]}
    verbal = composite_metric_verbalization(config["metrics"][4], metrics_map)
    assert verbal == "(销售额 - 成本额) / 销售额（销售额 = SUM(orders.amount)；成本额 = SUM(orders.cost)）"


def test_composite_metric_verbalization_without_members():
    verbal = composite_metric_verbalization(
        {"name": "X", "metric_kind": "composite", "formula": "{A} / {B}"}, {},
    )
    assert verbal == "A / B"


# ---------------------------------------------------------------------------
# config_validator：复合指标结构校验
# ---------------------------------------------------------------------------

DATASOURCE = {"ds_tables": [{"table_name": "orders", "columns": [
    {"name": "amount"}, {"name": "cost"}, {"name": "order_id"}, {"name": "category_name"},
]}]}


def validate(metrics, dimensions):
    return validate_semantic_fields(DATASOURCE, metrics, dimensions, [])


def base_metrics():
    return [
        {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
        {"name": "订单数", "expression": "COUNT(DISTINCT orders.order_id)", "table": "orders"},
    ]


def composite(**overrides):
    metric = {
        "name": "客单价",
        "metric_kind": "composite",
        "formula": "{销售额} / {订单数}",
    }
    metric.update(overrides)
    return metric


def test_validator_accepts_valid_composite_metric():
    assert validate([*base_metrics(), composite()], []) is None


def test_validator_accepts_composite_with_parentheses_and_numbers():
    metric = composite(name="毛利率", formula="({销售额} - 10) / {订单数}")
    assert validate([*base_metrics(), metric], []) is None


def test_validator_rejects_composite_with_expression():
    error = validate([*base_metrics(), composite(expression="SUM(orders.amount)")], [])
    assert "不能配置 expression" in error


def test_validator_rejects_composite_without_formula():
    error = validate([*base_metrics(), composite(formula="")], [])
    assert "缺少 formula" in error


def test_validator_rejects_composite_formula_without_refs():
    error = validate([*base_metrics(), composite(formula="1 + 2")], [])
    assert "没有任何" in error


def test_validator_rejects_composite_formula_bad_characters():
    error = validate([*base_metrics(), composite(formula="{销售额}; DROP TABLE x")], [])
    assert "不支持的字符" in error


def test_validator_rejects_composite_unknown_ref():
    error = validate([*base_metrics(), composite(formula="{不存在} + 1")], [])
    assert "引用的指标不存在" in error


def test_validator_rejects_composite_ref_to_composite():
    error = validate([*base_metrics(), composite(), composite(name="嵌套", formula="{客单价} + 1")], [])
    assert "不能引用其他复合指标" in error


def test_validator_rejects_composite_derived_ref():
    derived_metric = {
        "name": "本月成交额",
        "metric_kind": "derived",
        "base_metric": "销售额",
    }
    error = validate(
        [*base_metrics(), derived_metric, composite(formula="{本月成交额} + 1")], [],
    )
    assert "暂不支持派生指标" in error


def test_validator_rejects_composite_raw_ref():
    raw_metric = {"name": "原始值", "expression": "order_id", "table": "orders"}
    error = validate([*base_metrics(), raw_metric, composite(formula="{原始值} + 1")], [])
    assert "不是聚合指标" in error


def test_validator_rejects_composite_cross_table():
    other_metric = {"name": "其他额", "expression": "SUM(other.cost)", "table": "other"}
    datasource = {"ds_tables": [
        {"table_name": "orders", "columns": [{"name": "amount"}, {"name": "order_id"}]},
        {"table_name": "other", "columns": [{"name": "cost"}]},
    ]}
    error = validate_semantic_fields(
        datasource,
        [*base_metrics(), other_metric, composite(formula="{销售额} / {其他额}")],
        [],
        [],
    )
    assert "跨表" in error


# ---------------------------------------------------------------------------
# AI 适配器：SQL 生成
# ---------------------------------------------------------------------------

def ai_sql(config, payload):
    result = SemanticAIQueryAdapter(config).generate_sql(
        metrics=payload.get("metrics", []),
        dimensions=payload.get("dimensions", []),
        filters=payload.get("filters", []),
        having=payload.get("having", []),
        order_by=payload.get("order_by", []),
        limit=payload.get("limit"),
        offset=payload.get("offset"),
    )
    assert result["success"], result["error"]
    return result["sql"]


def test_ai_composite_metric_generates_formula_sql():
    config = build_composite_config()
    sql = ai_sql(config, {"metrics": ["客单价"]})
    assert "SUM(orders.amount) / NULLIF(COUNT(DISTINCT orders.order_id), 0) AS `客单价`" in sql


def test_ai_composite_metric_with_dimension_and_atomic_mix():
    config = build_composite_config()
    sql = ai_sql(config, {"metrics": ["销售额", "客单价"], "dimensions": ["产品分类"]})
    assert "SUM(amount) AS `销售额`" in sql
    assert "SUM(orders.amount) / NULLIF(COUNT(DISTINCT orders.order_id), 0) AS `客单价`" in sql
    assert "GROUP BY" in sql


def test_ai_composite_metric_order_by():
    config = build_composite_config()
    sql = ai_sql(config, {
        "metrics": ["客单价"],
        "dimensions": ["产品分类"],
        "order_by": [{"field": "客单价", "direction": "DESC"}],
    })
    assert "ORDER BY" in sql
    assert "客单价" in sql


def test_ai_invalid_composite_fails_gracefully():
    """配置无效的复合指标降级为不可选，返回友好错误而不是异常。"""
    config = build_composite_config()
    config["metrics"].append({
        "name": "坏复合", "metric_kind": "composite", "formula": "{不存在} / 2",
    })
    result = SemanticAIQueryAdapter(config).generate_sql(
        metrics=["坏复合"], dimensions=[], filters=[], having=[],
    )
    assert result["success"] is False
    assert "至少需要一个指标" in result["error"]


# ---------------------------------------------------------------------------
# BI 适配器：查询计划
# ---------------------------------------------------------------------------

def test_bi_composite_metric_maps_to_raw_expression():
    config = build_composite_config()
    plan = SemanticBIQueryAdapter(config).adapt({
        "aggregations": [{"semanticName": "客单价", "alias": "客单价"}],
        "dimensions": [{"semanticName": "产品分类"}],
    })
    agg = plan["aggregations"][0]
    assert agg["func"] == "RAW"
    assert agg["column"] == "*"
    assert agg["table"] == "orders"
    assert agg["expression"] == "SUM(orders.amount) / NULLIF(COUNT(DISTINCT orders.order_id), 0)"
    assert agg["alias"] == "客单价"


def test_bi_composite_metric_compiles_sql():
    config = build_composite_config()
    result = SemanticBIQueryAdapter(config).compile({
        "aggregations": [{"semanticName": "毛利率", "alias": "毛利率"}],
    })
    assert "(SUM(orders.amount) - SUM(orders.cost)) / NULLIF(SUM(orders.amount), 0)" in result["sql"]


def test_bi_composite_metric_no_extra_filters():
    config = build_composite_config()
    plan = SemanticBIQueryAdapter(config).adapt({
        "aggregations": [{"semanticName": "客单价"}],
    })
    assert plan["filters"] == []


# ---------------------------------------------------------------------------
# 周边消费：看板聚合判定 / 检索摘要 / Ossie 导出
# ---------------------------------------------------------------------------

def test_spec_service_treats_composite_metric_as_aggregate():
    composite_metric = next(m for m in build_composite_config()["metrics"] if m["name"] == "客单价")
    assert _is_aggregate_metric(composite_metric) is True


def test_search_summary_includes_composite_fixed_definition():
    config = build_composite_config()
    metrics_map = {m["name"]: m for m in config["metrics"]}
    composite_metric = config["metrics"][3]
    summary = _compact_metric_summary(composite_metric, metrics_map)
    assert summary["fixed_definition"] == (
        "销售额 / 订单数（销售额 = SUM(orders.amount)；订单数 = COUNT(DISTINCT orders.order_id)）"
    )
    assert "fixed_definition" not in _compact_metric_summary(config["metrics"][0])


def test_ossie_converter_exports_composite_metric_with_formula():
    config = build_composite_config()
    doc = ChatDataToOssieConverter().convert(config, source_type="mysql")
    assert "客单价" in doc
    assert '"metric_kind": "composite"' in doc
    assert '"formula": "{销售额} / {订单数}"' in doc
    # 复合指标表达式代入成员表达式（YAML 折行后做空白归一断言）
    normalized = " ".join(doc.split())
    assert "SUM(orders.amount) / NULLIF(COUNT(DISTINCT orders.order_id), 0)" in normalized


def test_composite_metric_survives_request_model_and_save_validation():
    """端到端防漂移：PUT payload 经 Pydantic SemanticMetric 解析后仍保留 formula 并通过保存校验。"""
    from apps.chat_data_app.schema import SemanticMetric

    metric = SemanticMetric(
        name="测试1",
        expression="",
        table="",
        metric_kind="composite",
        formula="{销售额} / {订单数}",
    ).model_dump()
    assert metric["metric_kind"] == "composite"
    assert metric["formula"] == "{销售额} / {订单数}"

    metrics = [
        {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
        {"name": "订单数", "expression": "COUNT(orders.order_id)", "table": "orders"},
        metric,
    ]
    dimensions = [{"name": "下单时间", "column": "order_time", "table": "orders", "logical_type": "datetime"}]
    datasource = {"ds_tables": [{"table_name": "orders", "columns": [
        {"name": "amount"}, {"name": "order_id"}, {"name": "order_time"},
    ]}]}
    error = validate_semantic_fields(datasource, metrics, dimensions, [])
    assert error is None
