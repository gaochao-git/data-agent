"""semantic/core/config_validator 缺失分支：派生指标周期/过滤与关系别名校验。"""

from apps.chat_data_app.services.semantic.core import config_validator as validator

LABEL = "派生指标 m 的统计周期"


def test_derived_period_rejects_non_object():
    assert validator._validate_derived_period("m", "daily", {}) == f"{LABEL}必须是对象"


def test_derived_period_range_rejects_bad_last_n_days():
    assert "n 必须是正整数" in validator._validate_derived_period_range(LABEL, "last_n_days", {"n": 0})
    assert "n 必须是正整数" in validator._validate_derived_period_range(LABEL, "last_n_days", {"n": True})


def test_derived_filters_rejects_non_list():
    assert validator._validate_derived_filters("m", "bad", {}) == "派生指标 m 的修饰词过滤必须是数组"


def test_derived_filter_item_rejects_non_object():
    assert validator._validate_derived_filter_item("m", 0, "bad", {}) == "派生指标 m 的修饰词过滤[1]必须是对象"


def test_between_and_in_filter_value_require_complete_values():
    assert "BETWEEN 需要完整的起止值" in validator._validate_between_filter_value("过滤", ["a"])
    assert "IN 过滤需要至少一个有效值" in validator._validate_in_filter_value("过滤", [])


def test_relationship_sides_reject_blank_alias():
    error = validator._validate_relationship_sides(
        {"users": {"id"}},
        {},
        "关系",
        (("左表", "users", ""),),
    )
    assert error == "关系左表别名不能为空"
