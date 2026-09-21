"""query_plan_compiler 分支补测。

覆盖目标：`services/semantic/core/semantic_sql/query_plan_compiler.py`
（原 15 条缺失分支弧 + 16 行）。

分两类：
1. 纯判定函数（`_normalize_filter_value_type` / `_having_operand_has_real_aggregation` /
   `_filter_has_real_aggregation`）直接单元调用即可覆盖。
2. `get_agg_expr` 是 `_compile_query_plan_sql` 的内嵌函数，只能通过构造完整查询计划
   的 orderBy 触达，因此下面用真实查询计划驱动。

## 三条不可达分支（已确认，下方有用例固化）

- `489->506`（HAVING 树编译结果为空时不追加）：`having_filters` 里的树节点必须先被
  `_get_filter_tree_scope` 判定为 `aggregate`，而这要求至少存在一个带 `aggFunc` 的
  后代叶子；该叶子编译结果必然非空，且 `build_filter_tree_clause` 会过滤掉空串。
- `518->519` / `520->521`（orderBy 落在 `group_expr_map` / `dimension_expr_map`）：
  `compile_select_fields` 对每个维度**同时**写入 `dimension_alias_map`、
  `group_expr_map`、`dimension_expr_map` 三个映射且键完全相同，因此 516 行的
  `dimension_alias_map` 判断必然先命中，后两个分支永远走不到。
"""

from __future__ import annotations

from apps.chat_data_app.services.semantic.core.semantic_sql import query_plan_compiler as qpc
from apps.chat_data_app.services.semantic.core.semantic_sql.query_plan_compiler import (
    compile_query_plan_sql,
)


def _plan(**overrides):
    """构造一个最小可用的单表查询计划。"""
    plan = {
        "mainTable": "orders",
        "dimensions": [{"table": "orders", "column": "city", "name": "城市"}],
        "aggregations": [],
        "filters": [],
        "orderBy": [],
    }
    plan.update(overrides)
    return plan


# ---------------------------------------------------------------------------
# _normalize_filter_value_type（101->102 非字符串）
# ---------------------------------------------------------------------------


def test_normalize_filter_value_type_accepts_only_known_strings():
    assert qpc._normalize_filter_value_type("literal") == "literal"
    # 大小写与前后空格归一化
    assert qpc._normalize_filter_value_type("  SQL_Function  ") == "sql_function"
    # 白名单外的值回落 None
    assert qpc._normalize_filter_value_type("unknown") is None
    assert qpc._normalize_filter_value_type("") is None


def test_normalize_filter_value_type_rejects_non_string():
    """非 str 入参直接返回 None（101->102 真分支）。"""
    for value in (None, 123, ["literal"], {"type": "literal"}, True):
        assert qpc._normalize_filter_value_type(value) is None


# ---------------------------------------------------------------------------
# _having_operand_has_real_aggregation（160->162 / 163->164 / 163->167 /
# 164->165 / 164->166 / 167->168 / 167->169）
# ---------------------------------------------------------------------------


def test_having_operand_has_real_aggregation_rejects_non_dict():
    for operand in (None, "metric", 42, [{"type": "metric"}]):
        assert qpc._having_operand_has_real_aggregation(operand) is False


def test_having_operand_metric_with_real_agg_func():
    """metric 操作数带非 RAW 聚合函数（163->164 + 164->165）。"""
    assert qpc._having_operand_has_real_aggregation({"type": "metric", "aggFunc": "SUM"}) is True
    # type 大小写/空格归一化
    assert qpc._having_operand_has_real_aggregation({"type": "  METRIC ", "aggFunc": "avg"}) is True


def test_having_operand_metric_falls_back_to_expression():
    """RAW / 缺省 aggFunc 时看 expression 是否聚合表达式（164->166）。"""
    assert qpc._having_operand_has_real_aggregation({"type": "metric", "aggFunc": "RAW"}) is False
    assert qpc._having_operand_has_real_aggregation({"type": "metric"}) is False
    assert qpc._having_operand_has_real_aggregation(
        {"type": "metric", "aggFunc": "RAW", "expression": "SUM(amount)"}
    ) is True
    # 非聚合表达式仍然算 False
    assert qpc._having_operand_has_real_aggregation(
        {"type": "metric", "aggFunc": "RAW", "expression": "amount"}
    ) is False


def test_having_operand_binary_recurses_into_both_sides():
    """binary 操作数递归左右子树（163->167 + 167->168）。"""

    def literal(value=1):
        return {"type": "literal", "value": value}

    assert qpc._having_operand_has_real_aggregation(
        {"type": "binary", "operator": "/", "left": literal(), "right": {"type": "metric", "aggFunc": "SUM"}}
    ) is True
    assert qpc._having_operand_has_real_aggregation(
        {"type": "binary", "operator": "/", "left": {"type": "metric", "aggFunc": "COUNT"}, "right": literal()}
    ) is True
    # 两侧都没有聚合 → False
    assert qpc._having_operand_has_real_aggregation(
        {"type": "binary", "operator": "/", "left": literal(10), "right": literal(2)}
    ) is False
    # 嵌套多层也要能穿透
    assert qpc._having_operand_has_real_aggregation(
        {
            "type": "binary",
            "operator": "+",
            "left": literal(),
            "right": {"type": "binary", "operator": "*", "left": literal(), "right": {"type": "metric", "aggFunc": "MAX"}},
        }
    ) is True


def test_having_operand_unknown_type_is_not_aggregation():
    """literal / 未知类型 / 缺 type 一律 False（167->169）。"""
    assert qpc._having_operand_has_real_aggregation({"type": "literal", "value": 1}) is False
    assert qpc._having_operand_has_real_aggregation({"type": "column", "column": "amount"}) is False
    assert qpc._having_operand_has_real_aggregation({}) is False


# ---------------------------------------------------------------------------
# _filter_has_real_aggregation（154->155：右操作数里藏了聚合）
# ---------------------------------------------------------------------------


def test_filter_has_real_aggregation_detects_aggregation_in_right_operand():
    node = {
        "column": "amount",
        "operator": ">",
        "value": 100,
        "rightOperand": {"type": "metric", "column": "amount", "aggFunc": "SUM"},
    }

    assert qpc._filter_has_real_aggregation(node) is True


def test_filter_has_real_aggregation_ignores_right_operand_without_aggregation():
    node = {
        "column": "amount",
        "operator": ">",
        "value": 100,
        "rightOperand": {"type": "literal", "value": 100},
    }

    assert qpc._filter_has_real_aggregation(node) is False


def test_filter_has_real_aggregation_tree_node_recurses():
    assert qpc._filter_has_real_aggregation(
        {"and": [{"column": "a", "operator": ">=", "value": 1}, {"aggFunc": "RAW", "expression": "SUM(amount)"}]}
    ) is True


def test_filter_has_real_aggregation_rejects_non_dict():
    assert qpc._filter_has_real_aggregation(None) is False
    assert qpc._filter_has_real_aggregation("not-a-node") is False


def test_having_operand_aggregation_drives_group_by_through_full_compile():
    """端到端：右操作数带聚合时，明细 + 聚合查询应产出 GROUP BY。

    `aggFunc: "raw"` 让 `_get_filter_tree_scope` 判定为 aggregate（只要 truthy 即可），
    但 `_is_real_aggregation_func("raw")` 为假，于是走到 154 行检查 rightOperand，
    这正是 154->155 在真实链路上的入口。
    """
    sql = compile_query_plan_sql(
        _plan(
            aggregations=[{"id": "a1", "table": "orders", "column": "amount", "func": "SUM", "name": "销售额"}],
            filters=[
                {
                    "and": [
                        {
                            "aggFunc": "raw",
                            "column": "amount",
                            "operator": ">",
                            "value": 100,
                            "rightOperand": {"type": "metric", "column": "amount", "aggFunc": "SUM"},
                        }
                    ]
                }
            ],
        )
    )

    assert "GROUP BY" in sql
    assert "HAVING" in sql


# ---------------------------------------------------------------------------
# get_agg_expr：通过 orderBy 驱动（454->455 / 460->461 / 468->469）
# ---------------------------------------------------------------------------


def test_order_by_matches_aggregation_by_table_column_and_func():
    """aggId 缺失时按 (表, 字段, 函数) 回表匹配（454->455）。"""
    sql = compile_query_plan_sql(
        _plan(
            aggregations=[{"id": "a1", "table": "orders", "column": "amount", "func": "SUM", "name": "销售额"}],
            orderBy=[{"table": "orders", "column": "amount", "aggFunc": "SUM", "direction": "DESC"}],
        )
    )

    assert "ORDER BY SUM(`amount`) DESC" in sql


def test_order_by_raw_aggregation_uses_matched_expression():
    """RAW + 命中表达式时走原始表达式归一化（460->461）。"""
    sql = compile_query_plan_sql(
        _plan(
            aggregations=[
                {
                    "id": "a1",
                    "table": "orders",
                    "column": "amount",
                    "func": "RAW",
                    "name": "金额",
                    "expression": "price * quantity",
                }
            ],
            orderBy=[{"table": "orders", "column": "amount", "aggFunc": "RAW", "direction": "ASC"}],
        )
    )

    assert "ORDER BY price * quantity ASC" in sql


def test_order_by_count_distinct_without_expression():
    """COUNT_DISTINCT 且无表达式时展开成 COUNT(DISTINCT col)（468->469）。"""
    sql = compile_query_plan_sql(
        _plan(
            aggregations=[
                {"id": "a1", "table": "orders", "column": "user_id", "func": "COUNT_DISTINCT", "name": "用户数"}
            ],
            orderBy=[{"table": "orders", "column": "user_id", "aggFunc": "COUNT_DISTINCT", "direction": "DESC"}],
        )
    )

    assert "ORDER BY COUNT(DISTINCT `user_id`) DESC" in sql


def test_order_by_prefers_agg_id_over_table_column_matching():
    """显式 aggId 命中时直接用该指标，不再走回表匹配。"""
    sql = compile_query_plan_sql(
        _plan(
            aggregations=[
                {"id": "a1", "table": "orders", "column": "amount", "func": "SUM", "name": "销售额"},
                {"id": "a2", "table": "orders", "column": "fee", "func": "MAX", "name": "最大手续费"},
            ],
            orderBy=[
                {"table": "orders", "column": "fee", "aggId": "a2", "aggFunc": "MAX", "direction": "DESC"}
            ],
        )
    )

    assert "ORDER BY MAX(`fee`) DESC" in sql


# ---------------------------------------------------------------------------
# 不可达分支固化
# ---------------------------------------------------------------------------


def test_select_compilation_writes_identical_keys_to_all_three_maps():
    """三个映射键集完全相同 → orderBy 后两个兜底分支不可达。

    `compile_select_fields` 对 raw 与 transform 两种维度都同时写入
    `dimension_expr_map` / `group_expr_map` / `dimension_alias_map`，
    因此 516 行 `dimension_alias_map` 判断必然先命中，518 / 520 属于死分支。
    """
    compilation = qpc.compile_select_fields(
        dimensions=[
            {"table": "orders", "column": "city", "name": "城市"},
            {"table": "orders", "column": "order_time", "name": "下单时间", "transform": "day"},
        ],
        aggregations=[{"table": "orders", "column": "amount", "func": "SUM", "name": "销售额"}],
        main_alias="orders",
        is_multi_table=False,
        dialect=qpc.get_dialect("mysql"),
        quote_identifier=qpc._quote_identifier,
        to_sql_alias=qpc._to_sql_alias,
        normalize_raw_expression=qpc._normalize_raw_expression,
        normalize_aggregate_expression=qpc._normalize_aggregate_expression,
        get_column_expr=qpc._get_column_expr,
        is_aggregate_expression=qpc._is_aggregate_expression,
    )

    assert set(compilation.dimension_alias_map) == set(compilation.group_expr_map)
    assert set(compilation.dimension_alias_map) == set(compilation.dimension_expr_map)
    # 聚合字段不会进入这三个映射
    assert ("orders", "amount") not in compilation.dimension_alias_map


def test_having_tree_clause_is_never_empty():
    """HAVING 树节点只要被判定为 aggregate，其编译结果必然非空 → 489->506 不可达。

    判定为 aggregate 要求存在一个 truthy `aggFunc` 的后代叶子，而
    `build_having_leaf_clause` 对任何叶子都返回 `expr <op> ...` 形式的非空串，
    且 `build_filter_tree_clause` 会过滤掉空串子节点。
    """
    leaf = {"aggFunc": "raw", "column": "amount", "operator": ">", "value": 1}

    # 空子树 / 非 dict 子节点的 scope 是 None，会落到 WHERE 而不是 HAVING
    assert qpc._get_filter_tree_scope({"and": []}) is None
    assert qpc._get_filter_tree_scope({"and": ["junk"]}) is None
    # 只有带 aggFunc 的叶子才能让整棵树被划入 HAVING
    assert qpc._get_filter_tree_scope({"and": [leaf]}) == "aggregate"
    assert qpc._get_filter_tree_scope({"and": [{"or": None}, leaf]}) == "aggregate"
