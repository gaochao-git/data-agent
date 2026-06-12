"""Tests for accuracy scorers."""

from scorers import score_dsl


def test_score_dsl_treats_plain_dimension_and_dict_without_grain_as_equal():
    predicted = {
        "metrics": [],
        "dimensions": [{"name": "订单状态"}],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 100,
    }
    expected = {
        "metrics": [],
        "dimensions": ["订单状态"],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 100,
    }

    result = score_dsl(predicted, expected)

    assert result["dsl_exact_match"] is True
    assert result["slot_scores"]["dimensions"] == 1.0


def test_score_dsl_treats_closed_year_between_as_half_open_year_range():
    predicted = {
        "metrics": ["销量"],
        "dimensions": [{"name": "下单时间", "time_grain": "month"}],
        "filters": [{
            "dimension": "下单时间",
            "operator": "between",
            "value": ["2024-01-01 00:00:00", "2024-12-31 23:59:59"],
        }],
        "having": [],
        "order_by": [{"field": "下单时间", "direction": "ASC"}],
        "limit": 100,
    }
    expected = {
        "metrics": ["销量"],
        "dimensions": [{"name": "下单时间", "time_grain": "month"}],
        "filters": [
            {"dimension": "下单时间", "operator": ">=", "value": "2024-01-01"},
            {"dimension": "下单时间", "operator": "<", "value": "2025-01-01"},
        ],
        "having": [],
        "order_by": [{"field": "下单时间", "direction": "ASC"}],
        "limit": 100,
    }

    result = score_dsl(predicted, expected)

    assert result["dsl_exact_match"] is True
    assert result["slot_scores"]["filters"] == 1.0


def test_score_dsl_ignores_extra_order_by_when_expected_has_no_order():
    predicted = {
        "metrics": ["销售额"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [{"field": "销售额", "direction": "DESC"}],
        "limit": 100,
    }
    expected = {
        "metrics": ["销售额"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 100,
    }

    result = score_dsl(predicted, expected)

    assert result["dsl_exact_match"] is True
    assert result["slot_scores"]["order_by"] == 1.0


def test_score_dsl_requires_order_by_when_expected_has_order():
    predicted = {
        "metrics": ["销售额"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 100,
    }
    expected = {
        "metrics": ["销售额"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [{"field": "销售额", "direction": "DESC"}],
        "limit": 100,
    }

    result = score_dsl(predicted, expected)

    assert result["dsl_exact_match"] is False
    assert result["slot_scores"]["order_by"] == 0.0


def test_score_dsl_keeps_missing_offset_as_null_in_display_dsl():
    predicted = {
        "metrics": ["销售额"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 100,
    }
    expected = {
        "metrics": ["销售额"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 100,
    }

    result = score_dsl(predicted, expected)

    assert result["dsl_exact_match"] is True
    assert result["expected_dsl"]["offset"] is None
    assert result["predicted_dsl"]["offset"] is None


def test_score_dsl_compares_offset_when_expected_has_value():
    predicted = {
        "metrics": ["销售额"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 10,
    }
    expected = {
        "metrics": ["销售额"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [],
        "limit": 10,
        "offset": 20,
    }

    result = score_dsl(predicted, expected)

    assert result["dsl_exact_match"] is False
    assert result["slot_scores"]["offset"] == 0.0


def test_score_dsl_keeps_missing_limit_and_offset_as_null_in_display_dsl():
    dsl = {
        "metrics": ["订单数量"],
        "dimensions": [],
        "filters": [],
        "having": [],
        "order_by": [],
    }

    result = score_dsl(dsl, dsl)

    assert result["expected_dsl"]["limit"] is None
    assert result["expected_dsl"]["offset"] is None
    assert result["predicted_dsl"]["limit"] is None
    assert result["predicted_dsl"]["offset"] is None
