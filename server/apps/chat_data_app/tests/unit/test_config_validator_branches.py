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
from apps.chat_data_app.services.semantic.core.common import build_table_columns_map
from apps.chat_data_app.services.semantic.run.semantic_quality_checker import first_structure_error


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
# 常量聚合豁免（COUNT(1)/COUNT(*) 不引用标识符，不可能引用错列）
# ---------------------------------------------------------------------------


def test_constant_aggregate_expression_passes_metric_validation():
    """COUNT(1) 是合法口径，不应被「必须引用物理字段」误杀。"""
    metrics = [{"name": "查询执行次数", "table": "orders", "expression": "COUNT(1)"}]
    assert cv._validate_metrics_against_tables(metrics, TABLE_COLUMNS) is None
    assert cv._is_constant_aggregate_expression("COUNT(*)") is True
    assert cv._is_constant_aggregate_expression("count( 1 )") is True


def test_constant_aggregate_exemption_does_not_leak_to_column_refs():
    """引用了标识符（哪怕全错）不豁免；函数名剥离只对后跟 '(' 的 token 生效。"""
    assert cv._is_constant_aggregate_expression("SUM(typo_col)") is False
    assert cv._is_constant_aggregate_expression("SUM(amount)") is False  # 合法引用走原校验，不豁免
    metrics = [{"name": "错误引用", "table": "orders", "expression": "SUM(typo_col)"}]
    error = cv._validate_metrics_against_tables(metrics, TABLE_COLUMNS)
    assert error is not None and "必须引用表 orders 的物理字段" in error


# ---------------------------------------------------------------------------
# 表达式多标识符全量校验（锚定校验只保证至少命中一列，其余标识符在此兜底）
# ---------------------------------------------------------------------------


def test_unknown_expression_column_is_reported():
    """锚定校验放行的多标识符表达式，写错的其余列必须被拦截。"""
    metrics = [{"name": "混合口径", "table": "orders", "expression": "SUM(amount) + SUM(typo_col)"}]
    error = cv._validate_metrics_against_tables(metrics, TABLE_COLUMNS)
    assert error is not None and "引用了表 orders 不存在的字段: typo_col" in error


def test_unknown_expression_columns_are_aggregated():
    """多个未知列在同一条错误消息里聚合列出并去重（带合法锚定列，避免被锚定校验先拦）。"""
    metrics = [{"name": "双错", "table": "orders", "expression": "SUM(amount) + SUM(typo_a) + SUM(typo_b) + SUM(typo_a)"}]
    error = cv._validate_metrics_against_tables(metrics, TABLE_COLUMNS)
    assert error is not None and "typo_a" in error and "typo_b" in error
    assert error.index("typo_a") < error.index("typo_b")  # 保持首次出现顺序


def test_known_columns_case_insensitive_pass_full_check():
    """大小写不敏感命中列集合，与锚定校验口径一致。"""
    metrics = [{"name": "大小写", "table": "orders", "expression": "SUM(amount) + COUNT(CITY)"}]
    assert cv._validate_metrics_against_tables(metrics, TABLE_COLUMNS) is None


def test_table_prefix_and_keywords_pass_full_check():
    """表前缀、SQL 关键字、字符串字面量内容、类型名不参与未知列判定。"""
    expressions = [
        "SUM(amount) + SUM(orders.city)",                      # 表前缀
        "SUM(CASE WHEN city = 'beijing' THEN amount ELSE 0 END)",  # 字面量 + 关键字
        "COUNT(DISTINCT user_id)",                             # DISTINCT 关键字
        "CAST(amount AS DECIMAL(10, 2))",                      # 类型名 + 数字
        "`amount` + `city`",                                   # 反引号壳
    ]
    metrics = [{"name": f"合法{i}", "table": "orders", "expression": expr} for i, expr in enumerate(expressions)]
    assert cv._validate_metrics_against_tables(metrics, TABLE_COLUMNS) is None


def test_find_unknown_expression_columns_direct():
    """纯函数边界：空表达式、纯字面量、无前缀剥离。"""
    assert cv._find_unknown_expression_columns("", TABLE_COLUMNS["orders"]) == []
    assert cv._find_unknown_expression_columns("SUM(amount)", TABLE_COLUMNS["orders"]) == []
    # 字符串里的标识符样词被剥离，不算未知列
    assert cv._find_unknown_expression_columns("SUM(CASE WHEN city='typo_word' THEN 1 END)", TABLE_COLUMNS["orders"]) == []
    unknown = cv._find_unknown_expression_columns("SUM(orders.typo_x)", TABLE_COLUMNS["orders"])
    assert unknown == ["typo_x"]


def test_non_ascii_bad_column_is_reported():
    """中文（非 ASCII）坏字段名同样要被提取拦截：锚定放行的混合表达式必须报未知列。"""
    unknown = cv._find_unknown_expression_columns("SUM(`amount` + `不存在字段测试`)", TABLE_COLUMNS["orders"])
    assert unknown == ["不存在字段测试"]
    metrics = [{"name": "中文坏列", "table": "orders", "expression": "SUM(`amount` + `不存在字段测试`)"}]
    error = cv._validate_metrics_against_tables(metrics, TABLE_COLUMNS)
    assert error is not None and "不存在字段测试" in error


def test_non_ascii_only_aggregate_is_not_constant():
    """SUM(中文坏字段) 不是常量聚合：不再因提取不到 ASCII 标识符而豁免物理校验。"""
    assert cv._is_constant_aggregate_expression("SUM(`不存在字段测试`)") is False
    assert cv._is_constant_aggregate_expression("COUNT(1)") is True  # 真·常量聚合仍豁免
    metrics = [{"name": "纯中文坏列", "table": "orders", "expression": "SUM(`不存在字段测试`)"}]
    error = cv._validate_metrics_against_tables(metrics, TABLE_COLUMNS)
    # 纯中文无合法锚定列，被锚定校验拦截；关键回归是不能再被误判成常量聚合而豁免
    assert error is not None and "物理字段" in error


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
# 结构硬门槛入口（first_structure_error，checker 薄壳）：map 显式传入 / datasource 构建
# ---------------------------------------------------------------------------


def test_first_structure_error_accepts_explicit_table_columns_map():
    """显式传 table_columns_map 时直接使用，不再构建。"""
    assert first_structure_error(
        metrics=[{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        dimensions=[{"name": "城市", "table": "orders", "column": "city"}],
        relationships=[],
        table_columns_map=TABLE_COLUMNS,
    ) is None


def test_first_structure_error_accepts_none_dimensions_and_metrics():
    assert first_structure_error(
        [], [], [], table_columns_map=build_table_columns_map(DATASOURCE)
    ) is None


def test_first_structure_error_builds_map_from_datasource():
    """datasource 形态由调用方经 build_table_columns_map 转换。"""
    assert first_structure_error(
        metrics=[{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        dimensions=[{"name": "城市", "table": "orders", "column": "city"}],
        relationships=[],
        table_columns_map=build_table_columns_map(DATASOURCE),
    ) is None


def test_first_structure_error_rejects_non_list_allowed_operators():
    error = first_structure_error(
        metrics=[],
        dimensions=[
            {"name": "城市", "table": "orders", "column": "city", "allowed_operators": "=,IN"}
        ],
        relationships=[],
        table_columns_map=TABLE_COLUMNS,
    )
    assert "allowed_operators 必须是数组" in error
