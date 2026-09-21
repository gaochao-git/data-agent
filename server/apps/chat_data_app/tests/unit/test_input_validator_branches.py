"""input_validator 分支补测。

覆盖目标：`services/semantic/core/input_validator.py` 中未被触达的**错误分支**与
部分纯函数边界（原 25 条缺失分支弧 + 26 行缺失）。

本模块是纯校验逻辑，无 IO、无外部依赖，因此直接构造维度/指标配置字典即可驱动，
不需要任何 mock。

注：`_validate_single_logical_value` 的 `text` 分支与「类型不在 number/date/datetime
时的兜底 return None」在业务路径上不可达 —— 调用方 `_validate_logical_values` 已用
394/396/398 三道前置拦截把 text / 非法类型 / enum 全部提前 return，只剩下
number/date/datetime 三种。这两条分支只能直接调用静态方法触达，下方用例已标注。
"""

from __future__ import annotations

import pytest

from apps.chat_data_app.services.semantic.core.input_validator import InputValidator


# ---------------------------------------------------------------------------
# 维度/指标配置：刻意包含各类「坏配置」以驱动配置校验分支
# ---------------------------------------------------------------------------

DIMENSIONS = {
    # 枚举维度，带 allowed_values + mapping
    "city": {
        "logical_type": "enum",
        "allowed_values": ["北京", "上海"],
        "mapping": {"北京": "BJ", "上海": "SH"},
    },
    # 枚举维度但配置残缺：缺 allowed_values 与 mapping
    "city_broken": {"logical_type": "enum"},
    # logical_type 非法
    "weird": {"logical_type": "bogus"},
    "amount": {"logical_type": "number"},
    "day": {"logical_type": "date"},
    "ts": {"logical_type": "datetime"},
    # allowed_operators 配成了字符串而非数组
    "bad_op_config": {"allowed_operators": "not-a-list"},
}

METRICS = {
    "gmv": {"name": "gmv"},
    "cnt": {"name": "cnt"},
}


@pytest.fixture
def validator() -> InputValidator:
    return InputValidator(metrics_map=METRICS, dimensions_map=DIMENSIONS)


# ---------------------------------------------------------------------------
# 树形过滤：_validate_tree_filter
# ---------------------------------------------------------------------------


def test_tree_filter_rejects_non_dict_child(validator):
    """and/or 的子节点不是对象时报错（原缺失行 162）。"""
    _, err = validator.validate_filters([{"and": ["not-a-dict"]}])
    assert err == "错误：无效的过滤条件格式，必须是对象"


def test_tree_filter_rejects_non_list_children(validator):
    """and/or 的值不是数组时报错。"""
    _, err = validator.validate_filters([{"or": {"bad": 1}}])
    assert err == "错误：'or' 节点的值必须是数组"


def test_tree_filter_rejects_non_dict_leaf(validator):
    """叶子节点既非 and/or 也非合法叶子的兜底分支。"""
    _, err = validator.validate_filters([{"and": [{"foo": "bar"}]}])
    assert "无效的过滤条件格式" in err


# ---------------------------------------------------------------------------
# 叶子过滤：_validate_filter_leaf
# ---------------------------------------------------------------------------


def test_leaf_rejects_builder_only_fields(validator):
    """语义 DSL 不支持 rightTable/rightTableAlias/rightColumn（原缺失行 197）。"""
    _, err = validator.validate_filters(
        [{"dimension": "city", "operator": "=", "value": "北京", "rightTable": "t_order"}]
    )
    assert "不支持字段 rightTable" in err
    assert "left/right" in err


def test_leaf_rejects_mixing_structured_and_free_expression(validator):
    """结构化 left/right 不能与 valueType/rightExpression 混用。"""
    _, err = validator.validate_filters(
        [
            {
                "left": {"type": "dimension", "name": "amount"},
                "right": {"type": "literal", "value": 1},
                "operator": "=",
                "rightExpression": "1",
            }
        ]
    )
    assert "不能混用" in err


def test_leaf_value_type_only_supports_expression(validator):
    """valueType 传了非 expression 的值（原缺失行 231）。

    该分支无法经 validate_filters 触达：_validate_free_right_expression 会先因
    「valueType 非 expression 或缺少 rightExpression」而提前返回，因此直接调静态方法。
    """
    err = InputValidator._validate_filter_value_type({"valueType": "literal"})
    assert err == "错误：valueType 仅支持 expression"


def test_leaf_value_type_empty_list_rejected(validator):
    """value_type 为空数组时报错（原缺失行 238）。"""
    err = InputValidator._validate_filter_value_type({"value_type": []})
    assert err == "错误：value_type 仅支持 literal 或 sql_function"


def test_leaf_value_type_invalid_item_rejected(validator):
    """value_type 含非法取值时报错（原缺失行 300 附近的 241）。"""
    err = InputValidator._validate_filter_value_type({"value_type": "raw_sql"})
    assert err == "错误：value_type 仅支持 literal 或 sql_function"


def test_free_expression_rejects_unsupported_operator(validator):
    """自由右侧表达式不支持 LIKE 类操作符（原缺失行 254）。"""
    _, err = validator.validate_filters(
        [
            {
                "dimension": "city",
                "operator": "LIKE",
                "valueType": "expression",
                "rightExpression": "1",
            }
        ]
    )
    assert "自由右侧表达式不支持操作符 'LIKE'" in err


def test_free_expression_rejects_blank_expression(validator):
    """rightExpression 必须是非空字符串。"""
    _, err = validator.validate_filters(
        [{"dimension": "city", "operator": "=", "valueType": "expression", "rightExpression": "   "}]
    )
    assert "rightExpression 必须是非空字符串" in err


def test_free_expression_rejects_unsafe_sql(validator):
    """rightExpression 不允许注释/分号/写操作。"""
    _, err = validator.validate_filters(
        [
            {
                "dimension": "city",
                "operator": "=",
                "valueType": "expression",
                "rightExpression": "1; DROP TABLE t",
            }
        ]
    )
    assert "只允许单条只读 SQL 表达式" in err


def test_free_expression_rejects_value_type_without_expression(validator):
    """只给 valueType 不给 rightExpression 时报错。"""
    _, err = validator.validate_filters(
        [{"dimension": "city", "operator": "=", "valueType": "expression"}]
    )
    assert "必须同时使用" in err


# ---------------------------------------------------------------------------
# 表达式过滤：_validate_expression_filter / validate_dimension_operator
# ---------------------------------------------------------------------------


def test_expression_filter_propagates_logical_value_error(validator):
    """right 为 literal 但值不符合维度 logical_type（原缺失分支 286->287）。"""
    _, err = validator.validate_filters(
        [
            {
                "left": {"type": "dimension", "name": "amount"},
                "right": {"type": "literal", "value": "abc"},
                "operator": "=",
            }
        ]
    )
    assert "必须是数字" in err


def test_dimension_operator_rejects_unknown_operator(validator):
    """操作符不在 FILTER_OPERATORS 全集内（原缺失行 294）。"""
    err = validator.validate_dimension_operator("amount", "~~")
    assert "过滤条件操作符 '~~' 不支持" in err


def test_dimension_operator_rejects_non_list_config(validator):
    """维度的 allowed_operators 配成非数组时报错（原缺失行 300）。"""
    err = validator.validate_dimension_operator("bad_op_config", "=")
    assert "allowed_operators 配置必须是数组" in err


def test_dimension_operator_rejects_disallowed_operator(validator):
    """操作符合法但不在维度的 allowed_operators 白名单内。"""
    dims = {"city": {"allowed_operators": ["="]}}
    v = InputValidator(metrics_map=METRICS, dimensions_map=dims)
    err = v.validate_dimension_operator("city", ">")
    assert "不允许操作符 '>'" in err


# ---------------------------------------------------------------------------
# 逻辑类型校验：_validate_logical_values / _validate_enum_config
# ---------------------------------------------------------------------------


def test_logical_values_rejects_unsupported_logical_type(validator):
    """维度的 logical_type 不在 LOGICAL_TYPES 内（原缺失行 397）。"""
    _, err = validator.validate_filters([{"dimension": "weird", "operator": "=", "value": "x"}])
    assert "logical_type 'bogus' 不支持" in err


def test_enum_config_requires_allowed_values_and_mapping(validator):
    """枚举维度必须同时配置 allowed_values 与 mapping（原缺失行 419）。"""
    _, err = validator.validate_filters(
        [{"dimension": "city_broken", "operator": "=", "value": "北京"}]
    )
    assert "必须同时配置 allowed_values 和 mapping" in err


def test_enum_config_rejects_missing_mapping_entry(validator):
    """枚举值缺少物理值映射时报错。"""
    dims = {
        "city": {
            "logical_type": "enum",
            "allowed_values": ["北京", "上海"],
            "mapping": {"北京": "BJ"},  # 上海缺映射
        }
    }
    v = InputValidator(metrics_map=METRICS, dimensions_map=dims)
    _, err = v.validate_filters([{"dimension": "city", "operator": "=", "value": "北京"}])
    assert "缺少物理值映射" in err


def test_single_logical_value_text_branch(validator):
    """logical_type 为 text 时直接放行（原缺失行 432）。

    业务路径不可达：_validate_logical_values 第 394 行已对 text 提前 return None，
    故此处直接调用静态方法覆盖。
    """
    assert InputValidator._validate_single_logical_value("d", "text", "任意值") is None


def test_single_logical_value_unknown_type_falls_through(validator):
    """logical_type 非 number/date/datetime 时兜底放行（原缺失行 439）。

    业务路径不可达同上（396 行已拦截非法类型），故直接调用静态方法。
    """
    assert InputValidator._validate_single_logical_value("d", "boolean", "x") is None


def test_number_rejects_bool_and_none(validator):
    """bool 是 int 子类，必须被显式排除（原缺失行 464）。"""
    assert InputValidator._is_number_value(True) is False
    assert InputValidator._is_number_value(None) is False
    assert InputValidator._is_number_value(1) is True
    # 经业务路径：number 维度传入布尔值应报错
    _, err = validator.validate_filters([{"dimension": "amount", "operator": "=", "value": True}])
    assert "必须是数字" in err


def test_date_rejects_non_string(validator):
    """date 维度传入非字符串时报错（顺带覆盖 _is_sql_function_literal 的非 str 分支 444）。"""
    _, err = validator.validate_filters([{"dimension": "day", "operator": "=", "value": 20260101}])
    assert "必须是 yyyy-MM-dd 格式" in err


def test_datetime_rejects_calendar_invalid_value(validator):
    """格式匹配但日期不存在（如 2 月 30 日），strptime 抛 ValueError（原缺失行 485-486）。"""
    assert InputValidator._is_datetime_text("2026-02-30 10:00:00") is False
    _, err = validator.validate_filters(
        [{"dimension": "ts", "operator": "=", "value": "2026-02-30 10:00:00"}]
    )
    assert "必须是 yyyy-MM-dd HH:mm:ss 格式" in err


def test_datetime_accepts_sql_function_literal(validator):
    """date/datetime 维度允许 SQL 函数字面量（覆盖 403 的 continue 分支）。"""
    ok, err = validator.validate_filters(
        [{"dimension": "ts", "operator": "=", "value": "CURRENT_TIMESTAMP()"}]
    )
    assert err is None
    assert ok


# ---------------------------------------------------------------------------
# 枚举值归一化：_normalize_filter_allowed_value
# ---------------------------------------------------------------------------


def test_normalize_keeps_filter_untouched_when_value_is_none(validator):
    """value 为 None 时不做归一化，直接返回副本（原缺失分支 554->556）。"""
    ok, err = validator.validate_filters([{"dimension": "city", "operator": "IS NULL"}])
    assert err is None
    assert ok == [{"dimension": "city", "operator": "IS NULL"}]


def test_normalize_maps_synonym_to_enum_value(validator):
    """mapping 生效：过滤值被规范成物理值。"""
    ok, err = validator.validate_filters([{"dimension": "city", "operator": "=", "value": "北京"}])
    assert err is None
    assert ok[0]["value"] == "BJ"


def test_normalize_maps_each_item_in_list(validator):
    """value 为数组时逐项归一化（覆盖 552-553 分支）。"""
    ok, err = validator.validate_filters(
        [{"dimension": "city", "operator": "IN", "value": ["北京", "上海"]}]
    )
    assert err is None
    assert ok[0]["value"] == ["BJ", "SH"]


# ---------------------------------------------------------------------------
# HAVING：validate_having / _validate_expression_having / _validate_metric_expression_operand
# ---------------------------------------------------------------------------


def test_having_rejects_non_dict_item(validator):
    """HAVING 元素不是对象时报错（原缺失行 589）。"""
    _, err = validator.validate_having(["not-a-dict"])
    assert err == "错误：HAVING 条件格式错误，必须是对象"


def test_having_rejects_mixing_structured_and_free_expression(validator):
    """结构化 left/right 与 valueType/rightExpression 混用（原缺失行 592）。"""
    _, err = validator.validate_having(
        [
            {
                "left": {"type": "metric", "name": "gmv"},
                "right": {"type": "literal", "value": 1},
                "operator": "=",
                "valueType": "expression",
            }
        ]
    )
    assert "不能混用" in err


def test_having_propagates_free_expression_error(validator):
    """普通 HAVING 上的自由右侧表达式校验失败（原缺失分支 601->602）。"""
    _, err = validator.validate_having(
        [{"metric": "gmv", "operator": "LIKE", "valueType": "expression", "rightExpression": "1"}]
    )
    assert "自由右侧表达式不支持操作符 'LIKE'" in err


def test_having_rejects_unknown_metric(validator):
    """HAVING 引用了不存在的指标。"""
    _, err = validator.validate_having([{"metric": "nope", "operator": ">", "value": 1}])
    assert "指标 'nope' 不存在" in err


def test_expression_having_rejects_unsupported_operator(validator):
    """HAVING 表达式操作符不在 EXPRESSION_OPERATORS 内（原缺失行 616）。"""
    _, err = validator.validate_having(
        [
            {
                "left": {"type": "metric", "name": "gmv"},
                "right": {"type": "literal", "value": 1},
                "operator": "LIKE",
            }
        ]
    )
    assert "HAVING 表达式暂不支持操作符 'LIKE'" in err


def test_expression_having_requires_metric_on_left(validator):
    """HAVING 表达式 left 只支持 metric（原缺失行 621）。"""
    _, err = validator.validate_having(
        [
            {
                "left": {"type": "dimension", "name": "gmv"},
                "right": {"type": "literal", "value": 1},
                "operator": "=",
            }
        ]
    )
    assert "HAVING 表达式 left 只支持 metric" in err


def test_expression_having_rejects_unknown_left_metric(validator):
    """HAVING 表达式 left 引用了不存在的指标。"""
    _, err = validator.validate_having(
        [
            {
                "left": {"type": "metric", "name": "nope"},
                "right": {"type": "literal", "value": 1},
                "operator": "=",
            }
        ]
    )
    assert "指标 'nope' 不存在" in err


def test_metric_operand_rejects_non_dict(validator):
    """操作数不是对象时报错（原缺失行 630）。"""
    _, err = validator.validate_having(
        [{"left": {"type": "metric", "name": "gmv"}, "right": "oops", "operator": "="}]
    )
    assert "HAVING 表达式 right 必须是对象" in err


def test_metric_operand_rejects_unknown_metric_ref(validator):
    """操作数引用的指标不存在（原缺失行 635）。"""
    _, err = validator.validate_having(
        [
            {
                "left": {"type": "metric", "name": "gmv"},
                "right": {"type": "metric", "name": "nope"},
                "operator": "=",
            }
        ]
    )
    assert "HAVING 表达式引用的指标 'nope' 不存在" in err


@pytest.mark.parametrize("bad_value", [{"nested": 1}, [1, 2]])
def test_metric_operand_rejects_container_literal(validator, bad_value):
    """literal 的 value 不支持 dict/list（原缺失行 640）。"""
    _, err = validator.validate_having(
        [
            {
                "left": {"type": "metric", "name": "gmv"},
                "right": {"type": "literal", "value": bad_value},
                "operator": "=",
            }
        ]
    )
    assert "只支持字符串、数字、布尔值或空值" in err


def test_metric_operand_rejects_unsupported_binary_operator(validator):
    """二元运算符不在 + - * / 内（原缺失行 645）。"""
    _, err = validator.validate_having(
        [
            {
                "left": {"type": "metric", "name": "gmv"},
                "right": {
                    "type": "binary",
                    "operator": "%",
                    "left": {"type": "literal", "value": 1},
                    "right": {"type": "literal", "value": 2},
                },
                "operator": "=",
            }
        ]
    )
    assert "HAVING 表达式二元运算符 '%' 不支持" in err


def test_metric_operand_propagates_left_error(validator):
    """二元表达式左操作数递归校验失败（原缺失分支 649->650）。"""
    _, err = validator.validate_having(
        [
            {
                "left": {"type": "metric", "name": "gmv"},
                "right": {
                    "type": "binary",
                    "operator": "+",
                    "left": {"type": "literal", "value": {"bad": 1}},
                    "right": {"type": "literal", "value": 1},
                },
                "operator": "=",
            }
        ]
    )
    assert "right.left.value 只支持" in err


def test_metric_operand_propagates_right_error(validator):
    """二元表达式右操作数递归校验失败（原缺失分支 652->653）。"""
    _, err = validator.validate_having(
        [
            {
                "left": {"type": "metric", "name": "gmv"},
                "right": {
                    "type": "binary",
                    "operator": "+",
                    "left": {"type": "literal", "value": 1},
                    "right": {"type": "literal", "value": [1]},
                },
                "operator": "=",
            }
        ]
    )
    assert "right.right.value 只支持" in err


def test_metric_operand_rejects_division_by_zero(validator):
    """除以 0 被拒绝。"""
    _, err = validator.validate_having(
        [
            {
                "left": {"type": "metric", "name": "gmv"},
                "right": {
                    "type": "binary",
                    "operator": "/",
                    "left": {"type": "metric", "name": "gmv"},
                    "right": {"type": "literal", "value": 0},
                },
                "operator": "=",
            }
        ]
    )
    assert "不允许除以 0" in err


def test_metric_operand_rejects_unknown_type(validator):
    """操作数 type 不在 metric/literal/binary 内的兜底分支。"""
    _, err = validator.validate_having(
        [
            {
                "left": {"type": "metric", "name": "gmv"},
                "right": {"type": "unknown"},
                "operator": "=",
            }
        ]
    )
    assert "HAVING 表达式 right.type 只支持" in err
