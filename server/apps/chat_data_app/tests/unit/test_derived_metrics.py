"""派生指标（原子指标 + 统计周期 + 修饰词）端到端测试。"""

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services.semantic.core.config_validator import validate_semantic_fields
from apps.chat_data_app.services.semantic.core.query_plan import is_derived_metric
from apps.chat_data_app.services.semantic.run.chat_data_ossie_converter import ChatDataToOssieConverter
from apps.chat_data_app.services.semantic.run.semantic_ai_query_adapter import SemanticAIQueryAdapter
from apps.chat_data_app.services.semantic.run.semantic_bi_query_adapter import SemanticBIQueryAdapter
from apps.chat_data_app.services.semantic.run.semantic_model_search import _compact_metric_summary
from apps.chat_data_app.services.semantic.run.semantic_query_plan_builder import (
    derive_period_date_range,
    derived_metric_filter_leaves,
    derived_metric_verbalization,
    resolve_derived_metric,
)


NOW = datetime(2026, 9, 5, 14, 30, 45)


def build_derived_config() -> dict:
    """构造含派生指标的语义配置。"""
    return {
        "id": "derived_config",
        "name": "派生指标配置",
        "source_type": "mysql",
        "metrics": [
            {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
            {"name": "订单数", "expression": "COUNT(DISTINCT orders.order_id)", "table": "orders"},
            {
                "name": "本月成交额",
                "metric_kind": "derived",
                "base_metric": "销售额",
                "period": {"kind": "this_month", "time_dimension": "下单时间"},
                "filters": [{"dimension": "订单状态", "operator": "=", "value": "已支付"}],
            },
            {
                "name": "近7天下单量",
                "metric_kind": "derived",
                "base_metric": "订单数",
                "period": {"kind": "last_n_days", "n": 7, "time_dimension": "下单时间"},
            },
        ],
        "dimensions": [
            {"name": "下单时间", "column": "order_time", "table": "orders", "logical_type": "datetime"},
            {
                "name": "订单状态",
                "column": "status",
                "table": "orders",
                "logical_type": "enum",
                "allowed_values": ["已支付", "已取消"],
                "mapping": {"已支付": "paid", "已取消": "cancelled"},
            },
        ],
        "relationships": [],
    }


# ---------------------------------------------------------------------------
# plan_builder：周期区间
# ---------------------------------------------------------------------------

def test_derive_period_date_range_today():
    assert derive_period_date_range({"kind": "today"}, NOW) == (
        "2026-09-05 00:00:00", "2026-09-06 00:00:00",
    )


def test_derive_period_date_range_yesterday():
    assert derive_period_date_range({"kind": "yesterday"}, NOW) == (
        "2026-09-04 00:00:00", "2026-09-05 00:00:00",
    )


def test_derive_period_date_range_last_n_days():
    assert derive_period_date_range({"kind": "last_n_days", "n": 7}, NOW) == (
        "2026-08-30 00:00:00", "2026-09-06 00:00:00",
    )


def test_derive_period_date_range_this_week():
    assert derive_period_date_range({"kind": "this_week"}, NOW) == (
        "2026-08-31 00:00:00", "2026-09-07 00:00:00",
    )


def test_derive_period_date_range_this_month():
    assert derive_period_date_range({"kind": "this_month"}, NOW) == (
        "2026-09-01 00:00:00", "2026-10-01 00:00:00",
    )


def test_derive_period_date_range_between_date_end_inclusive():
    assert derive_period_date_range(
        {"kind": "between", "start": "2026-08-01", "end": "2026-08-31"}, NOW,
    ) == ("2026-08-01 00:00:00", "2026-09-01 00:00:00")


def test_derive_period_date_range_between_datetime_end_kept():
    assert derive_period_date_range(
        {"kind": "between", "start": "2026-08-01", "end": "2026-08-31 12:00:00"}, NOW,
    ) == ("2026-08-01 00:00:00", "2026-08-31 12:00:00")


def test_derive_period_date_range_rejects_bad_kind():
    with pytest.raises(ValueError, match="不支持的派生指标周期类型"):
        derive_period_date_range({"kind": "bad"}, NOW)


def test_derive_period_date_range_rejects_invalid_n():
    with pytest.raises(ValueError, match="必须是正整数"):
        derive_period_date_range({"kind": "last_n_days", "n": 0}, NOW)


def test_derive_period_date_range_rejects_reversed_between():
    with pytest.raises(ValueError, match="end 不能早于 start"):
        derive_period_date_range(
            {"kind": "between", "start": "2026-08-10", "end": "2026-08-01"}, NOW,
        )


# ---------------------------------------------------------------------------
# plan_builder：过滤叶子与解析
# ---------------------------------------------------------------------------

def test_derived_metric_filter_leaves_maps_enum_value():
    config = build_derived_config()
    dims_map = {dim["name"]: dim for dim in config["dimensions"]}
    metric = next(m for m in config["metrics"] if m["name"] == "本月成交额")
    leaves = derived_metric_filter_leaves(metric, dims_map, now=NOW)
    assert leaves[0] == {"dimension": "下单时间", "operator": ">=", "value": "2026-09-01 00:00:00"}
    assert leaves[1] == {"dimension": "下单时间", "operator": "<", "value": "2026-10-01 00:00:00"}
    assert leaves[2] == {"dimension": "订单状态", "operator": "=", "value": "paid"}


def test_derived_metric_filter_leaves_rejects_non_time_dimension():
    dims_map = {
        "订单状态": {"name": "订单状态", "logical_type": "enum"},
    }
    metric = {"name": "X", "period": {"kind": "today", "time_dimension": "订单状态"}}
    with pytest.raises(ValueError, match="周期维度不是时间维度"):
        derived_metric_filter_leaves(metric, dims_map, now=NOW)


def test_resolve_derived_metric_atomic_passthrough():
    config = build_derived_config()
    metrics_map = {m["name"]: m for m in config["metrics"]}
    dims_map = {dim["name"]: dim for dim in config["dimensions"]}
    base_cfg, leaves = resolve_derived_metric(config["metrics"][0], metrics_map, dims_map, now=NOW)
    assert base_cfg is config["metrics"][0]
    assert leaves == []


def test_resolve_derived_metric_returns_base_copy_and_leaves():
    config = build_derived_config()
    metrics_map = {m["name"]: m for m in config["metrics"]}
    dims_map = {dim["name"]: dim for dim in config["dimensions"]}
    derived = config["metrics"][2]
    base_cfg, leaves = resolve_derived_metric(derived, metrics_map, dims_map, now=NOW)
    assert base_cfg["name"] == "销售额"
    assert base_cfg is not metrics_map["销售额"]
    assert len(leaves) == 3


def test_resolve_derived_metric_rejects_missing_base():
    derived = {"name": "X", "metric_kind": "derived", "base_metric": "不存在"}
    with pytest.raises(ValueError, match="基础指标不存在"):
        resolve_derived_metric(derived, {"销售额": {"name": "销售额"}}, {}, now=NOW)


def test_resolve_derived_metric_rejects_derived_base():
    derived = {"name": "X", "metric_kind": "derived", "base_metric": "Y"}
    base = {"name": "Y", "metric_kind": "derived", "base_metric": "Z"}
    with pytest.raises(ValueError, match="不能是派生指标"):
        resolve_derived_metric(derived, {"X": derived, "Y": base}, {}, now=NOW)


def test_derived_metric_verbalization_parts():
    config = build_derived_config()
    metric = config["metrics"][2]
    assert derived_metric_verbalization(metric) == "指标[销售额] · 本月 · 订单状态=已支付"
    bare = {"name": "X", "base_metric": "销售额"}
    assert derived_metric_verbalization(bare) == "指标[销售额]"


def test_is_derived_metric():
    assert is_derived_metric({"metric_kind": "derived"})
    assert is_derived_metric({"metric_kind": "DERIVED "})
    assert not is_derived_metric({})
    assert not is_derived_metric(None)


# ---------------------------------------------------------------------------
# config_validator：派生指标结构校验
# ---------------------------------------------------------------------------

DATASOURCE = {"ds_tables": [{"table_name": "orders", "columns": [
    {"name": "amount"}, {"name": "order_id"}, {"name": "order_time"}, {"name": "status"},
]}]}


def validate(metrics, dimensions):
    return validate_semantic_fields(DATASOURCE, metrics, dimensions, [])


def base_dimensions():
    return [
        {"name": "下单时间", "column": "order_time", "table": "orders", "logical_type": "datetime"},
        {"name": "订单状态", "column": "status", "table": "orders", "logical_type": "enum",
         "allowed_values": ["已支付", "已取消"], "mapping": {"已支付": "paid"}},
    ]


def base_metrics():
    return [
        {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
        {"name": "原始值", "expression": "order_id", "table": "orders"},
    ]


def derived(**overrides):
    metric = {
        "name": "本月成交额",
        "metric_kind": "derived",
        "base_metric": "销售额",
        "period": {"kind": "this_month", "time_dimension": "下单时间"},
    }
    metric.update(overrides)
    return metric


def test_validator_accepts_valid_derived_metric():
    assert validate([*base_metrics(), derived()], base_dimensions()) is None


def test_validator_accepts_derived_without_period():
    metric = derived()
    metric.pop("period")
    assert validate([*base_metrics(), metric], base_dimensions()) is None


def test_validator_rejects_derived_with_expression():
    error = validate([*base_metrics(), derived(expression="SUM(orders.amount)")], base_dimensions())
    assert "不能配置 expression" in error


def test_validator_rejects_missing_base_metric():
    error = validate([*base_metrics(), derived(base_metric="不存在")], base_dimensions())
    assert "基础指标不存在" in error


def test_validator_rejects_derived_base_metric():
    nested = derived(name="嵌套指标", base_metric="本月成交额")
    error = validate([*base_metrics(), derived(), nested], base_dimensions())
    assert "不能是派生指标" in error


def test_validator_rejects_composite_base_metric():
    composite = {"name": "客单价", "metric_kind": "composite", "formula": "{销售额} / {原始值}"}
    error = validate([*base_metrics(), composite, derived(base_metric="客单价")], base_dimensions())
    assert "不能是复合指标" in error


def test_validator_rejects_raw_base_metric():
    error = validate([*base_metrics(), derived(base_metric="原始值")], base_dimensions())
    assert "基础指标必须是聚合指标" in error


def test_validator_rejects_unknown_period_kind():
    metric = derived(period={"kind": "bad", "time_dimension": "下单时间"})
    error = validate([*base_metrics(), metric], base_dimensions())
    assert "类型不支持" in error


def test_validator_rejects_invalid_last_n_days():
    metric = derived(period={"kind": "last_n_days", "n": 0, "time_dimension": "下单时间"})
    error = validate([*base_metrics(), metric], base_dimensions())
    assert "必须是正整数" in error


def test_validator_rejects_between_without_range():
    metric = derived(period={"kind": "between", "start": "2026-08-01", "time_dimension": "下单时间"})
    error = validate([*base_metrics(), metric], base_dimensions())
    assert "between 必须提供 start 和 end" in error


def test_validator_rejects_non_time_period_dimension():
    metric = derived(period={"kind": "today", "time_dimension": "订单状态"})
    error = validate([*base_metrics(), metric], base_dimensions())
    assert "时间维度必须 logical_type 为 date/datetime" in error


def test_validator_rejects_unknown_period_dimension():
    metric = derived(period={"kind": "today", "time_dimension": "不存在"})
    error = validate([*base_metrics(), metric], base_dimensions())
    assert "时间维度不存在" in error


def test_validator_rejects_unknown_filter_dimension():
    metric = derived(filters=[{"dimension": "不存在", "operator": "=", "value": "x"}])
    error = validate([*base_metrics(), metric], base_dimensions())
    assert "引用的维度不存在" in error


def test_validator_rejects_unsupported_filter_operator():
    metric = derived(filters=[{"dimension": "订单状态", "operator": "DROP", "value": "x"}])
    error = validate([*base_metrics(), metric], base_dimensions())
    assert "操作符不支持" in error


def test_validator_rejects_filter_without_value():
    metric = derived(filters=[{"dimension": "订单状态", "operator": "="}])
    error = validate([*base_metrics(), metric], base_dimensions())
    assert "缺少筛选值" in error


def test_validator_accepts_no_value_operators():
    metric = derived(filters=[{"dimension": "订单状态", "operator": "IS NULL"}])
    assert validate([*base_metrics(), metric], base_dimensions()) is None


def test_validator_accepts_between_and_in_filter_values():
    metric = derived(filters=[
        {"dimension": "订单状态", "operator": "IN", "value": ["已支付", "已取消"]},
        {"dimension": "下单时间", "operator": "BETWEEN", "value": ["2026-08-01", "2026-08-31"]},
    ])
    assert validate([*base_metrics(), metric], base_dimensions()) is None


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


def test_ai_derived_metric_expands_base_with_fixed_filters():
    config = build_derived_config()
    sql = ai_sql(config, {"metrics": ["本月成交额"]})
    assert "SUM(amount) AS `本月成交额`" in sql
    assert "`order_time` >= '2026-09-01 00:00:00'" in sql
    assert "`order_time` < '2026-10-01 00:00:00'" in sql
    assert "`status` = 'paid'" in sql


def test_ai_mixed_atomic_and_derived_metrics():
    config = build_derived_config()
    sql = ai_sql(config, {"metrics": ["订单数", "本月成交额"], "dimensions": ["订单状态"]})
    assert "COUNT(DISTINCT order_id) AS `订单数`" in sql
    assert "SUM(amount) AS `本月成交额`" in sql
    assert "GROUP BY" in sql
    assert "`status` = 'paid'" in sql


def test_ai_derived_metric_having_and_order_by():
    config = build_derived_config()
    sql = ai_sql(config, {
        "metrics": ["近7天下单量"],
        "dimensions": ["订单状态"],
        "having": [{"metric": "近7天下单量", "operator": ">", "value": 100}],
        "order_by": [{"field": "近7天下单量", "direction": "DESC"}],
    })
    assert "SUM(amount)" not in sql
    assert "COUNT(DISTINCT order_id) AS `近7天下单量`" in sql
    assert "HAVING COUNT(DISTINCT order_id) > 100" in sql
    assert "ORDER BY COUNT(DISTINCT order_id) DESC" in sql
    assert "`order_time` >= '2026-08-30 00:00:00'" in sql


def test_ai_derived_metric_respects_explicit_limit():
    config = build_derived_config()
    sql = ai_sql(config, {"metrics": ["本月成交额"], "limit": 5})
    assert "LIMIT 5" in sql


def test_ai_rejects_order_by_field_outside_query_fields():
    """ORDER BY 只能使用请求的指标/维度：排序字段必须在输出字段中。"""
    config = build_derived_config()
    result = SemanticAIQueryAdapter(config).generate_sql(
        metrics=["订单数"], dimensions=[], filters=[], having=[],
        order_by=[{"field": "本月成交额", "direction": "DESC"}],
    )
    assert not result["success"]
    assert "不在当前查询输出字段中" in result["error"]


def test_ai_rejects_mixed_derived_periods():
    """混用不同周期的派生指标会被拒绝（固定过滤交集会算错）。"""
    config = build_derived_config()
    result = SemanticAIQueryAdapter(config).generate_sql(
        metrics=["本月成交额", "近7天下单量"], dimensions=[], filters=[], having=[], order_by=[],
    )
    assert not result["success"]
    assert "不同周期的派生指标" in result["error"]


def test_ai_allows_same_period_derived_metrics():
    """周期相同的派生指标可安全混用。"""
    config = build_derived_config()
    config["metrics"].append({
        "name": "本月下单量",
        "metric_kind": "derived",
        "base_metric": "订单数",
        "period": {"kind": "this_month", "time_dimension": "下单时间"},
    })
    result = SemanticAIQueryAdapter(config).generate_sql(
        metrics=["本月成交额", "本月下单量"], dimensions=[], filters=[], having=[], order_by=[],
    )
    assert result["success"], result.get("error")


# ---------------------------------------------------------------------------
# BI 适配器：查询计划
# ---------------------------------------------------------------------------

def test_bi_derived_metric_injects_fixed_filters():
    config = build_derived_config()
    plan = SemanticBIQueryAdapter(config).adapt({
        "aggregations": [{"semanticName": "本月成交额", "alias": "本月成交额"}],
        "dimensions": [{"semanticName": "订单状态"}],
    })
    agg = plan["aggregations"][0]
    assert agg["func"] == "SUM"
    assert agg["alias"] == "本月成交额"
    assert agg["expression"] == "SUM(orders.amount)"
    filters = plan["filters"]
    period_filters = [f for f in filters if f.get("column") == "order_time"]
    assert {(f["operator"], f["value"]) for f in period_filters} == {
        (">=", "2026-09-01 00:00:00"), ("<", "2026-10-01 00:00:00"),
    }
    modifier_filters = [f for f in filters if f.get("column") == "status"]
    assert modifier_filters[0]["value"] == "paid"


def test_bi_atomic_metric_without_derived_has_no_fixed_filters():
    config = build_derived_config()
    plan = SemanticBIQueryAdapter(config).adapt({
        "aggregations": [{"semanticName": "销售额"}],
    })
    assert plan["filters"] == []


def test_bi_derived_filters_do_not_leak_across_queries():
    config = build_derived_config()
    adapter = SemanticBIQueryAdapter(config)
    adapter.adapt({"aggregations": [{"semanticName": "本月成交额"}]})
    plan = adapter.adapt({"aggregations": [{"semanticName": "销售额"}]})
    assert plan["filters"] == []


def test_bi_order_by_only_derived_metric_injects_fixed_filters():
    """order_by 引用未在 SELECT 中的派生指标时，固定过滤同样注入。"""
    config = build_derived_config()
    plan = SemanticBIQueryAdapter(config).adapt({
        "aggregations": [{"semanticName": "销售额"}],
        "orderBy": [{"semanticName": "本月成交额", "aggFunc": "SUM", "direction": "DESC"}],
    })
    period_filters = [f for f in plan["filters"] if f.get("column") == "order_time"]
    assert {(f["operator"], f["value"]) for f in period_filters} == {
        (">=", "2026-09-01 00:00:00"), ("<", "2026-10-01 00:00:00"),
    }
    assert [f for f in plan["filters"] if f.get("column") == "status"]


def test_bi_rejects_mixed_derived_periods():
    """混用不同周期的派生指标会被拒绝（固定过滤交集会算错）。"""
    config = build_derived_config()
    with pytest.raises(ValueError, match="不同周期的派生指标"):
        SemanticBIQueryAdapter(config).adapt({
            "aggregations": [
                {"semanticName": "本月成交额"},
                {"semanticName": "近7天下单量"},
            ],
        })


# ---------------------------------------------------------------------------
# 周边消费：看板聚合判定 / 检索摘要 / Ossie 导出
# ---------------------------------------------------------------------------

def test_spec_service_treats_derived_metric_as_aggregate():
    from apps.chat_data_app.services.dashboard.spec_service import (
        _is_aggregate_metric,
        remove_mixed_raw_metrics_for_aggregate_dsl,
    )

    derived_metric = next(m for m in build_derived_config()["metrics"] if m["name"] == "本月成交额")
    assert _is_aggregate_metric(derived_metric) is True

    semantic_config = build_derived_config()
    semantic_config["metrics"].append({"name": "原始值", "expression": "order_id", "table": "orders"})
    dsl, removed = remove_mixed_raw_metrics_for_aggregate_dsl(
        {"metrics": ["本月成交额", "原始值"]},
        semantic_config,
    )
    assert dsl["metrics"] == ["本月成交额"]
    assert removed == ["已移除聚合卡片中不能混用的原始值指标：原始值"]


def test_search_summary_includes_derived_fixed_definition():
    config = build_derived_config()
    derived_metric = config["metrics"][2]
    summary = _compact_metric_summary(derived_metric)
    assert summary["fixed_definition"] == "指标[销售额] · 本月 · 订单状态=已支付"
    assert "fixed_definition" not in _compact_metric_summary(config["metrics"][0])


def test_ossie_converter_exports_derived_metric_with_base_expression():
    config = build_derived_config()
    doc = ChatDataToOssieConverter().convert(config, source_type="mysql")
    assert "本月成交额" in doc
    assert '"metric_kind": "derived"' in doc
    assert "指标[销售额] · 本月 · 订单状态=已支付" in doc
    # 派生指标复用基础指标表达式
    assert "SUM(orders.amount)" in doc


# ---------------------------------------------------------------------------
# 端到端防漂移：PUT payload 经 Pydantic SemanticMetric 解析后仍通过保存校验
# ---------------------------------------------------------------------------

def test_derived_metric_survives_request_model_and_save_validation():
    from apps.chat_data_app.schema import SemanticMetric

    metric = SemanticMetric(
        name="本周新注册用户数量",
        expression="",
        table="",
        metric_kind="derived",
        base_metric="销售额",
        period={"kind": "this_week", "time_dimension": "下单时间"},
        filters=[{"dimension": "订单状态", "operator": "=", "value": "已支付"}],
    ).model_dump()
    assert metric["metric_kind"] == "derived"
    assert metric["period"]["kind"] == "this_week"
    assert metric["filters"] == [{"dimension": "订单状态", "operator": "=", "value": "已支付"}]

    metrics = [
        {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
        metric,
    ]
    error = validate(metrics, base_dimensions())
    assert error is None


# ---------------------------------------------------------------------------
# schema 按 metric_kind 分型校验（P3 收敛：字段归属 + base_metric 双语义 + 操作符）
# ---------------------------------------------------------------------------

def _schema_metric(**overrides):
    from apps.chat_data_app.schema import SemanticMetric

    payload = {"name": "测试指标", "expression": "SUM(orders.amount)", "table": "orders"}
    payload.update(overrides)
    return SemanticMetric(**payload)


def test_schema_rejects_atomic_with_formula():
    import pytest
    from apps.chat_data_app.schema import SemanticMetric

    with pytest.raises(ValueError, match="不能配置 formula"):
        SemanticMetric(name="测试指标", expression="SUM(amount)", table="orders", formula="{销售额} / 100")


def test_schema_rejects_atomic_base_metric_without_compare():
    import pytest
    from apps.chat_data_app.schema import SemanticMetric

    with pytest.raises(ValueError, match="不允许配置 base_metric"):
        SemanticMetric(name="测试指标", expression="SUM(amount)", table="orders", base_metric="销售额")


def test_schema_allows_legacy_compare_metric_with_base_metric():
    metric = _schema_metric(base_metric="销售额", compare_type="mom", compare_mode="value")
    assert metric.base_metric == "销售额"


def test_schema_rejects_derived_missing_base_metric():
    import pytest
    from apps.chat_data_app.schema import SemanticMetric

    with pytest.raises(ValueError, match="缺少 base_metric"):
        SemanticMetric(name="测试指标", expression="", table="", metric_kind="derived")


def test_schema_rejects_composite_with_base_metric():
    import pytest
    from apps.chat_data_app.schema import SemanticMetric

    with pytest.raises(ValueError, match="不能配置 base_metric"):
        SemanticMetric(
            name="测试指标", expression="", table="",
            metric_kind="composite", formula="{销售额} / 100", base_metric="销售额",
        )


def test_schema_rejects_atomic_with_period_filters():
    import pytest
    from apps.chat_data_app.schema import SemanticMetric

    with pytest.raises(ValueError, match="不能配置 period/filters"):
        SemanticMetric(
            name="测试指标", expression="SUM(amount)", table="orders",
            period={"kind": "this_month", "time_dimension": "下单时间"},
        )


def test_schema_normalizes_filter_operator_and_rejects_unknown():
    import pytest
    from apps.chat_data_app.schema import SemanticMetric

    metric = _schema_metric(
        expression="", metric_kind="derived", base_metric="销售额",
        filters=[{"dimension": "状态", "operator": "in", "value": ["1"]}],
    )
    assert metric.filters[0].operator == "IN"

    with pytest.raises(ValueError, match="操作符不支持"):
        SemanticMetric(
            name="测试指标", expression="", table="",
            metric_kind="derived", base_metric="销售额",
            filters=[{"dimension": "状态", "operator": "~~", "value": "1"}],
        )
