"""semantic_query_plan_builder 分支覆盖补测。

覆盖 2026-09-18 审计出的 15 条未覆盖分支弧：
between 周期校验、周期描述、维度映射（list/大小写/未命中）、派生指标
过滤叶子与基础指标解析出口、复合指标成员校验与口述化分支。
"""

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.services.semantic.run.semantic_query_plan_builder import (
    _between_period_range,
    _describe_period,
    _mapped_dimension_value,
    composite_metric_verbalization,
    derived_metric_filter_leaves,
    derived_metric_verbalization,
    resolve_derived_metric,
    resolve_composite_metric,
    substitute_composite_formula,
)


# ---------------------------------------------------------------- 周期

def test_between_period_range_requires_start_and_end():
    with pytest.raises(ValueError, match="缺少 start/end"):
        _between_period_range({})


def test_describe_period_between_kind():
    assert _describe_period({"kind": "between", "start": "2026-01-01", "end": "2026-01-31"}) == "2026-01-01~2026-01-31"


# ---------------------------------------------------------------- 维度映射

def test_mapped_dimension_value_passthrough_without_mapping():
    dim = {"name": "状态"}
    assert _mapped_dimension_value(dim, "已支付") == "已支付"
    assert _mapped_dimension_value({"mapping": {"a": "b"}}, None) is None


def test_mapped_dimension_value_maps_list_recursively():
    dim = {"mapping": {"paid": "已支付"}}
    assert _mapped_dimension_value(dim, ["paid", "paid"]) == ["已支付", "已支付"]


def test_mapped_dimension_value_case_insensitive_match_and_miss():
    dim = {"mapping": {"Paid": "已支付", "Refund": "已退款"}}
    assert _mapped_dimension_value(dim, "paid") == "已支付"  # 键忽略大小写命中
    assert _mapped_dimension_value(dim, "unknown") == "unknown"  # 遍历完未命中原样返回


# ---------------------------------------------------------------- 派生指标过滤叶子

_TIME_DIM = {"name": "下单时间", "logical_type": "datetime", "table": "orders"}
_STATUS_DIM = {"name": "订单状态", "logical_type": "enum", "table": "orders",
               "mapping": {"paid": "已支付"}}


def test_filter_leaves_skips_period_when_absent():
    """无 period 但有 filters → 直接走 filters 循环（224->232）。"""
    leaves = derived_metric_filter_leaves(
        {"name": "近状态", "filters": [{"dimension": "订单状态", "operator": "=", "value": "paid"}]},
        {"订单状态": _STATUS_DIM},
    )
    assert leaves == [{"dimension": "订单状态", "operator": "=", "value": "已支付"}]


def test_filter_leaves_without_period_and_filters_returns_empty():
    assert derived_metric_filter_leaves({"name": "裸指标"}, {}) == []


def test_resolve_derived_metric_rejects_composite_base():
    """基础指标是复合指标 → 报错（265->266）。"""
    metrics_map = {
        "客单价": {"name": "客单价", "metric_kind": "composite", "formula": "{a}/{b}"},
    }
    with pytest.raises(ValueError, match="基础指标不能是复合指标"):
        resolve_derived_metric(
            {"name": "本月客单价", "metric_kind": "derived", "base_metric": "客单价"},
            metrics_map,
            {},
        )


def test_resolve_derived_metric_rejects_derived_base():
    metrics_map = {
        "一层派生": {"name": "一层派生", "metric_kind": "derived", "base_metric": "销售额"},
    }
    with pytest.raises(ValueError, match="基础指标不能是派生指标"):
        resolve_derived_metric(
            {"name": "二层派生", "metric_kind": "derived", "base_metric": "一层派生"},
            metrics_map,
            {},
        )


# ---------------------------------------------------------------- 派生指标口述化

def test_verbalization_without_base_name_and_with_list_value():
    """无 base_metric（275->277）、list 值拼接（284->285）、空 dimension 跳过（288->280）。"""
    text = derived_metric_verbalization({
        "period": {"kind": "this_month"},
        "filters": [
            {"dimension": "订单状态", "operator": "=", "value": ["paid", "refund"]},
            {"dimension": "", "operator": "=", "value": "x"},
        ],
    })
    assert text == "本月 · 订单状态=paid、refund"


# ---------------------------------------------------------------- 复合指标

def _metrics_map():
    return {
        "销售额": {"name": "销售额", "expression": "SUM(orders.amount)", "table": "orders"},
        "订单数": {"name": "订单数", "expression": "COUNT(orders.order_id)", "table": "orders"},
        "成本": {"name": "成本", "expression": "SUM(costs.amount)", "table": "costs"},
        "非聚合": {"name": "非聚合", "expression": "orders.amount", "table": "orders"},
        "无表": {"name": "无表", "expression": "SUM(x.y)", "table": ""},
    }


def test_resolve_composite_rejects_member_without_table():
    """成员缺物理表 → 报错（325->326）。"""
    with pytest.raises(ValueError, match="缺少物理表"):
        resolve_composite_metric(
            {"name": "毛利", "metric_kind": "composite", "formula": "{销售额}/{无表}"},
            _metrics_map(),
        )


def test_resolve_composite_rejects_cross_table_members():
    metrics_map = _metrics_map()
    with pytest.raises(ValueError, match="跨表"):
        resolve_composite_metric(
            {"name": "毛利", "metric_kind": "composite", "formula": "{销售额}/{成本}"},
            metrics_map,
        )


def test_substitute_formula_keeps_unresolvable_ref():
    """引用不在成员表 → 原样保留占位（347->348）。"""
    members = [{"name": "a", "expression": "SUM(t.x)"}]
    assert substitute_composite_formula("{a} + {ghost}", members) == "SUM(t.x) + {ghost}"


def test_substitute_formula_wraps_division_with_nullif():
    members = [
        {"name": "a", "expression": "SUM(t.x)"},
        {"name": "b", "expression": "COUNT(t.y)"},
    ]
    assert substitute_composite_formula("{a} / {b}", members) == "SUM(t.x) / NULLIF(COUNT(t.y), 0)"


def test_composite_verbalization_returns_empty_for_non_composite():
    assert composite_metric_verbalization({"name": "销售额"}, _metrics_map()) == ""


def test_composite_verbalization_returns_empty_without_formula():
    assert composite_metric_verbalization({"name": "x", "metric_kind": "composite"}, _metrics_map()) == ""
