"""config_validator 分支补测。

覆盖目标：`services/semantic/core/config_validator.py` 中未被触达的错误分支与
纯函数边界（原 11 条缺失分支弧）。

本模块是**纯结构校验**，无 IO、无外部依赖，直接构造字典即可驱动，不需要 mock。
`validate_semantic_relationships` 支持直接传入 `table_columns_map`，因此大部分用例
绕开 `build_table_columns_map`，避免构造数据源字典的噪音。

不可达分支说明：`validate_semantic_relationships` 第 150 行的 `if not alias` 属于
防御性检查 —— `_relationship_alias` 在取不到 `{side}_alias` 时会回落到 `{side}_table`，
而第 148 行已经保证 `table` 非空且存在于表结构中，因此走不到 150 的真分支。
下方 `test_relationship_blank_alias_guard_is_unreachable` 用断言固化了这个结论，
避免后人误以为是漏测。
"""

from __future__ import annotations

from apps.chat_data_app.services.semantic.core import config_validator as cv


# ---------------------------------------------------------------------------
# 表结构：直接喂给 validate_semantic_relationships
# ---------------------------------------------------------------------------

TABLE_COLUMNS = {
    "orders": {"user_id", "amount", "city"},
    "users": {"id", "level"},
}

# 通过 validate_semantic_fields 入口时需要的等价数据源字典
DATASOURCE = {
    "ds_tables": [
        {"table_name": "orders", "columns": [{"name": "user_id"}, {"name": "amount"}, {"name": "city"}]},
        {"table_name": "users", "columns": [{"name": "id"}, {"name": "level"}]},
    ]
}


# ---------------------------------------------------------------------------
# normalize_semantic_text / expression_uses_physical_column
# ---------------------------------------------------------------------------


def test_normalize_semantic_text_strips_backticks_and_case():
    assert cv.normalize_semantic_text("  OrderID  ") == "orderid"
    assert cv.normalize_semantic_text("`orders`.`city`") == "orders.city"
    # 空值/假值统一回落空串
    assert cv.normalize_semantic_text(None) == ""
    assert cv.normalize_semantic_text(0) == ""
    assert cv.normalize_semantic_text("") == ""


def test_expression_uses_physical_column():
    columns = {"amount", "city"}
    # 空表达式直接短路
    assert cv.expression_uses_physical_column("", columns) is False
    assert cv.expression_uses_physical_column(None, columns) is False
    # 反引号不影响识别
    assert cv.expression_uses_physical_column("SUM(`amount`)", columns) is True
    # 带表前缀
    assert cv.expression_uses_physical_column("sum(orders.amount) / 100", columns) is True
    # 大小写不敏感
    assert cv.expression_uses_physical_column("COUNT(CITY)", columns) is True
    # 前缀/后缀是单词字符时不能误判（city_code 不应命中 city）
    assert cv.expression_uses_physical_column("SUM(city_code)", columns) is False
    assert cv.expression_uses_physical_column("SUM(acity)", columns) is False
    # 完全无关
    assert cv.expression_uses_physical_column("COUNT(*)", columns) is False


# ---------------------------------------------------------------------------
# _validate_allowed_operators（78->79：非数组）
# ---------------------------------------------------------------------------


def test_validate_allowed_operators_accepts_absent_and_valid():
    assert cv._validate_allowed_operators({"name": "城市"}) is None
    assert cv._validate_allowed_operators({"name": "城市", "allowed_operators": []}) is None
    # 大小写 + 前后空格归一化后合法
    assert cv._validate_allowed_operators({"name": "城市", "allowed_operators": [" = ", "in"]}) is None


def test_validate_allowed_operators_rejects_non_list():
    error = cv._validate_allowed_operators({"name": "城市", "allowed_operators": "=,IN"})
    assert error is not None
    assert "allowed_operators 必须是数组" in error


def test_validate_allowed_operators_rejects_unknown_operator():
    error = cv._validate_allowed_operators({"name": "城市", "allowed_operators": ["contains"]})
    assert "不支持的过滤操作符" in error


# ---------------------------------------------------------------------------
# _relationship_conditions（101->102：显式 join_conditions 优先）
# ---------------------------------------------------------------------------


def test_relationship_conditions_prefers_explicit_join_conditions():
    conditions = [{"left_column": "user_id", "right_column": "id"}]
    relationship = {"join_conditions": conditions, "left_column": "x", "right_column": "y"}
    assert cv._relationship_conditions(relationship) is conditions


def test_relationship_conditions_falls_back_to_legacy_columns():
    # join_conditions 为空数组 / None 时都回落到 left_column / right_column
    for empty in ([], None):
        relationship = {"join_conditions": empty, "left_column": "user_id", "right_column": "id"}
        assert cv._relationship_conditions(relationship) == [
            {"left_column": "user_id", "right_column": "id"}
        ]


def test_relationship_conditions_returns_empty_when_nothing_configured():
    assert cv._relationship_conditions({}) == []
    # 只配了一侧也算「无关联条件」
    assert cv._relationship_conditions({"left_column": "user_id"}) == [
        {"left_column": "user_id", "right_column": None}
    ]


# ---------------------------------------------------------------------------
# _register_alias（113->114：别名冲突）
# ---------------------------------------------------------------------------


def test_register_alias_is_idempotent_for_same_table():
    alias_tables: dict[str, str] = {}
    assert cv._register_alias(alias_tables, "o", "orders") is None
    assert alias_tables["o"] == "orders"
    # 同一别名重复指向同一张表，不算冲突
    assert cv._register_alias(alias_tables, "o", "orders") is None
    assert alias_tables == {"o": "orders"}


def test_register_alias_detects_conflict():
    alias_tables = {"o": "orders"}
    error = cv._register_alias(alias_tables, "o", "users")
    assert error is not None
    assert "同时指向了" in error
    # 冲突时不覆写已有映射
    assert alias_tables["o"] == "orders"


# ---------------------------------------------------------------------------
# validate_semantic_relationships：字段别名注册（131->128 / 133->134）
# ---------------------------------------------------------------------------


def test_field_without_table_is_skipped_when_registering_alias():
    """字段名/表名为空的条目不参与别名注册（131->128 回路）。"""
    assert cv.validate_semantic_relationships(TABLE_COLUMNS, [], [{"table": ""}], []) is None
    # 条目本身是 None 也不应炸
    assert cv.validate_semantic_relationships(TABLE_COLUMNS, [None], [None], []) is None


def test_field_alias_conflict_is_reported():
    """两个字段共用别名但指向不同表时报错（133->134）。"""
    dimensions = [
        {"name": "订单状态", "table": "orders", "table_alias": "shared"},
        {"name": "用户等级", "table": "users", "table_alias": "shared"},
    ]
    error = cv.validate_semantic_relationships(TABLE_COLUMNS, [], dimensions, [])
    assert error is not None
    assert "shared" in error and "同时指向了" in error


def test_field_alias_may_be_reused_across_fields_of_same_table():
    dimensions = [
        {"name": "订单金额", "table": "orders", "table_alias": "o", "column": "amount"},
        {"name": "订单城市", "table": "orders", "table_alias": "o", "column": "city"},
    ]
    assert cv.validate_semantic_relationships(TABLE_COLUMNS, [], dimensions, []) is None


# ---------------------------------------------------------------------------
# validate_semantic_relationships：关系端点校验（148->149 / 153->154 / 155->156）
# ---------------------------------------------------------------------------


def test_relationship_rejects_unknown_left_table():
    relationship = {
        "left_table": "missing",
        "right_table": "users",
        "left_column": "user_id",
        "right_column": "id",
    }
    error = cv.validate_semantic_relationships(TABLE_COLUMNS, [], [], [relationship])
    assert "关系[1]左表不存在" in error


def test_relationship_rejects_unknown_right_table():
    relationship = {
        "left_table": "orders",
        "right_table": "missing",
        "left_column": "user_id",
        "right_column": "id",
    }
    error = cv.validate_semantic_relationships(TABLE_COLUMNS, [], [], [relationship])
    assert "关系[1]右表不存在" in error


def test_relationship_rejects_blank_table():
    relationship = {
        "left_table": "   ",
        "right_table": "users",
        "left_column": "user_id",
        "right_column": "id",
    }
    error = cv.validate_semantic_relationships(TABLE_COLUMNS, [], [], [relationship])
    assert "关系[1]左表不存在" in error


def test_relationship_blank_alias_guard_is_unreachable():
    """第 150 行 `not alias` 是防御分支。

    `_relationship_alias` 取不到 `{side}_alias` 时会回落到 `{side}_table`；
    而第 148 行已保证 `table` 非空，因此别名不可能为空。这里固化该结论：
    显式给空别名仍会拿到表名，真正为空时会被 148 行先拦下。
    """
    relationship = {"left_table": "orders", "right_table": "users", "left_alias": ""}
    assert cv._relationship_alias(relationship, "left") == "orders"

    blank_table = {
        "left_table": "",
        "right_table": "users",
        "left_alias": "",
        "left_column": "user_id",
        "right_column": "id",
    }
    assert "左表不存在" in cv.validate_semantic_relationships(TABLE_COLUMNS, [], [], [blank_table])


def test_relationship_alias_conflicting_with_field_alias_is_reported():
    """关系侧别名与已注册字段别名冲突（153->154）。"""
    dimensions = [{"name": "用户等级", "table": "users", "table_alias": "u", "column": "level"}]
    relationship = {
        "left_table": "orders",
        "right_table": "orders",
        "right_alias": "u",  # u 已指向 users，这里却指向 orders
        "left_column": "user_id",
        "right_column": "user_id",
    }
    error = cv.validate_semantic_relationships(TABLE_COLUMNS, [], dimensions, [relationship])
    assert "同时指向了" in error


def test_relationship_rejects_identical_left_and_right_alias():
    """左右表别名相同（155->156）。"""
    relationship = {
        "left_table": "orders",
        "right_table": "orders",
        "left_column": "user_id",
        "right_column": "user_id",
    }
    error = cv.validate_semantic_relationships(TABLE_COLUMNS, [], [], [relationship])
    assert "关系[1]左右表别名不能相同" in error


# ---------------------------------------------------------------------------
# validate_semantic_relationships：关联条件校验（165->166 / 167->168）
# ---------------------------------------------------------------------------


def test_relationship_rejects_blank_join_columns():
    for condition in (
        {"left_column": "   ", "right_column": "id"},
        {"left_column": "user_id", "right_column": ""},
        {"left_column": None, "right_column": None},
    ):
        relationship = {
            "left_table": "orders",
            "right_table": "users",
            "join_conditions": [condition],
        }
        error = cv.validate_semantic_relationships(TABLE_COLUMNS, [], [], [relationship])
        assert "关联条件[1]左右字段不能为空" in error


def test_relationship_rejects_unknown_left_join_column():
    relationship = {
        "left_table": "orders",
        "right_table": "users",
        "join_conditions": [{"left_column": "missing", "right_column": "id"}],
    }
    error = cv.validate_semantic_relationships(TABLE_COLUMNS, [], [], [relationship])
    assert "关联条件[1]左字段不存在" in error
    assert "orders.missing" in error


def test_relationship_rejects_unknown_right_join_column():
    relationship = {
        "left_table": "orders",
        "right_table": "users",
        "join_conditions": [{"left_column": "user_id", "right_column": "missing"}],
    }
    error = cv.validate_semantic_relationships(TABLE_COLUMNS, [], [], [relationship])
    assert "关联条件[1]右字段不存在" in error


def test_relationship_blank_condition_dict_is_reported():
    """关联条件里塞了空字典 / None 也要走「左右字段不能为空」。"""
    relationship = {
        "left_table": "orders",
        "right_table": "users",
        "join_conditions": [None],
    }
    error = cv.validate_semantic_relationships(TABLE_COLUMNS, [], [], [relationship])
    assert "关联条件[1]左右字段不能为空" in error


def test_multiple_relationships_report_failing_index():
    """第二条关系出问题时，报错前缀应为 关系[2]。"""
    relationships = [
        {
            "left_table": "orders",
            "right_table": "users",
            "left_column": "user_id",
            "right_column": "id",
        },
        {
            "left_table": "orders",
            "right_table": "users",
            "join_conditions": [{"left_column": "nope", "right_column": "id"}],
        },
    ]
    error = cv.validate_semantic_relationships(TABLE_COLUMNS, [], [], relationships)
    assert "关系[2]关联条件[1]左字段不存在" in error


# ---------------------------------------------------------------------------
# validate_semantic_fields 入口：table_columns_map 显式传入 / None 参
# ---------------------------------------------------------------------------


def test_validate_semantic_fields_accepts_explicit_table_columns_map():
    """显式传 table_columns_map 时不再调用 build_table_columns_map。"""
    assert cv.validate_semantic_fields(
        {},
        metrics=[{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        dimensions=[{"name": "城市", "table": "orders", "column": "city"}],
        table_columns_map=TABLE_COLUMNS,
    ) is None


def test_validate_semantic_fields_accepts_none_dimensions_and_metrics():
    assert cv.validate_semantic_fields(
        DATASOURCE, dimensions=None, metrics=None, relationships=None
    ) is None


def test_validate_semantic_fields_builds_map_from_datasource():
    """不传 table_columns_map 时走 build_table_columns_map。"""
    assert cv.validate_semantic_fields(
        DATASOURCE,
        metrics=[{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        dimensions=[{"name": "城市", "table": "orders", "column": "city"}],
    ) is None


def test_validate_semantic_fields_rejects_non_list_allowed_operators():
    error = cv.validate_semantic_fields(
        DATASOURCE,
        metrics=[],
        dimensions=[
            {"name": "城市", "table": "orders", "column": "city", "allowed_operators": "=,IN"}
        ],
    )
    assert "allowed_operators 必须是数组" in error
