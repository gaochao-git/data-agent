import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services.semantic.run.semantic_query_service import SemanticAIQueryAdapter
from apps.chat_data_app.services.semantic.core.semantic_sql.query_plan_compiler import compile_query_plan_sql
from apps.chat_data_app.services.semantic.core.semantic_sql.dialect import get_dialect
import apps.chat_data_app.services.semantic.core.semantic_sql.query_plan_compiler as compiler
from apps.chat_data_app.services.runtime_config import DEFAULT_ROW_LIMIT


def test_builder_sql_compiler_helper_branches():
    assert compiler._quote_identifier("订单 状态") == "`订单 状态`"
    assert compiler._to_sql_alias("`订单 状态`[A]", "fallback") == "订单_状态A"
    assert compiler._to_sql_alias("!!!", "fallback") == "___"
    assert compiler._normalize_column_token("") == "``"
    assert compiler._normalize_column_token("`amount`") == "`amount`"
    assert compiler._is_numeric_literal("") is False
    assert compiler._is_numeric_literal("12.5") is True
    assert compiler._is_numeric_literal("abc") is False
    assert compiler._unwrap_quoted_function("'current_date()'") == "current_date()"
    assert compiler._unwrap_quoted_function("x") is None
    assert compiler._is_sql_function(123) is False
    assert compiler._is_sql_function("") is False
    assert compiler._is_sql_function("unsafe_fn()") is False
    assert compiler._is_sql_function("current_date(); drop table t") is False
    assert compiler._is_sql_function("current_date() -- comment") is False
    assert compiler._is_sql_function("current_date(") is False
    assert compiler._is_sql_function("date_add(current_date(), interval 1 day)") is True
    assert compiler._is_sql_function("add_months(dt, 1)") is True
    assert compiler._is_sql_function("instr(name, 'a')") is True
    assert compiler._is_sql_function("locate('a', name)") is True
    assert compiler._is_sql_function("lpad(code, 4, '0')") is True
    assert compiler._is_sql_function("repeat(code, 2)") is True
    assert compiler._is_sql_function("current_date() /* comment */") is False
    assert compiler._has_balanced_parentheses("concat('(', col)") is True
    assert compiler._has_balanced_parentheses("concat('x'") is False
    assert compiler._normalize_filter_value_type(" SQL_FUNCTION ") == "sql_function"
    assert compiler._normalize_filter_value_type("bad") is None
    assert compiler._value_type_for_index(["literal", "sql_function"], 3) is None
    assert compiler._sql_literal(None) == "NULL"
    assert compiler._sql_literal("O'Reilly", "literal") == "'O''Reilly'"
    assert compiler._sql_literal("'current_date()'", "sql_function") == "current_date()"
    assert compiler._sql_literal("bad_fn()", "sql_function") == "'bad_fn()'"
    assert compiler._sql_literal("current_date()", "literal") == "'current_date()'"
    assert compiler._sql_list_literal([], None) == "(NULL)"
    assert compiler._sql_list_literal("paid", None) == "('paid')"
    assert compiler._sql_list_literal(["current_date()", "paid"], ["sql_function", "literal"]) == "(current_date(), 'paid')"
    assert compiler._get_filter_tree_scope({"and": []}) is None
    assert compiler._get_filter_tree_scope({"and": "bad"}) is None
    assert compiler._normalize_raw_expression("orders.amount", "orders", "amount", False) == "amount"
    assert compiler._normalize_raw_expression("orders.amount", "orders", "amount", True) == "orders.amount"
    assert compiler._normalize_aggregate_expression("", "orders", "amount", False) == ""
    assert compiler._normalize_aggregate_expression("amount + tax", "orders", "amount", False) == "amount + tax"
    assert compiler._has_balanced_parentheses(")") is False
    assert compiler._has_balanced_parentheses("'unterminated") is False
    assert compiler._has_balanced_parentheses('"unterminated') is False
    assert compiler._has_balanced_parentheses(r"concat('x\\', col)") is True
    assert compiler._is_sql_function("date_format(order_time, '%Y-%m'); drop table orders") is False
    assert compiler._sql_literal("'current_date()'") == "current_date()"
    assert compiler._get_filter_tree_scope("bad") is None
    assert compiler._normalize_raw_expression("`amount`", "orders", "amount", False) == "`amount`"
    assert compiler._get_column_expr("orders", "*", True) == "*"
    assert compiler._build_from_ref("", "o") == ""
    assert compiler._build_from_ref("orders", "") == "orders"
    assert compiler._build_from_ref("orders", "orders") == "orders"
    assert compiler._build_from_ref("orders", "o") == "orders o"
    assert compiler._rewrite_expression_table_qualifier("orders.amount + `orders`.tax", "o", "orders", True) == "o.amount + o.tax"
    assert compiler._rewrite_expression_table_qualifier("orders.amount", "orders", "orders", True) == "orders.amount"
    assert compiler._normalize_raw_expression("", "orders", "amount", False) == "`amount`"
    assert compiler._normalize_raw_expression("amount", "orders", "amount", True) == "orders.`amount`"
    assert compiler._normalize_raw_expression("orders.amount", "o", "orders", True, physical_table="orders") == "o.amount"
    assert compiler._normalize_aggregate_expression("SUM(orders.amount)", "o", "amount", True, physical_table="orders") == "SUM(o.amount)"
    assert compiler._normalize_aggregate_expression("COUNT(DISTINCT orders.user_id)", "o", "user_id", True, physical_table="orders") == "COUNT(DISTINCT o.user_id)"
    assert compiler._filter_has_real_aggregation("bad") is False
    assert compiler._filter_has_real_aggregation({"and": "bad"}) is False
    assert compiler._filter_has_real_aggregation({"and": [{"aggFunc": "SUM"}]}) is True
    assert compiler._filter_has_real_aggregation({"expression": "SUM(orders.amount)"}) is True
    assert compiler._filter_has_real_aggregation({"expression": "orders.amount"}) is False
    assert compiler._order_by_has_real_aggregation("bad") is False
    assert compiler._order_by_has_real_aggregation({"aggFunc": ""}) is False
    assert compiler._order_by_has_real_aggregation({"aggFunc": "RAW", "expression": "SUM(orders.amount)"}) is True
    assert compiler._order_by_has_real_aggregation({"aggFunc": "SUM"}) is True


def test_builder_sql_compiler_remaining_helper_branch_edges():
    assert compiler._is_sql_function("now()") is True
    assert compiler._is_sql_function("concat('a;b')") is False
    assert compiler._is_sql_function("now();") is False
    assert compiler._is_sql_function("now()--comment") is False
    assert compiler._has_balanced_parentheses(r"concat(\"x\", col)") is True
    assert compiler._build_having_leaf_clause(
        {"table": "orders", "column": "score", "operator": "IN", "valueType": "expression", "rightExpression": "select score from s", "aggFunc": "RAW", "expression": "orders.score"},
        is_multi_table=False,
        main_table="orders",
        aggregations=[],
        agg_by_id={},
    ) == "orders.score IN (select score from s)"
    assert compiler._build_having_leaf_clause(
        {"table": "orders", "column": "score", "operator": "NOT IN", "valueType": "expression", "rightExpression": "select score from s", "aggFunc": "RAW", "expression": "orders.score"},
        is_multi_table=False,
        main_table="orders",
        aggregations=[],
        agg_by_id={},
    ) == "orders.score NOT IN (select score from s)"
    assert compiler._build_where_leaf_clause(
        {"table": "orders", "column": "score", "operator": "IN", "valueType": "expression", "rightExpression": "select score from s"},
        is_multi_table=False,
        main_table="orders",
        dimension_expr_map={},
    ) == "`score` IN (select score from s)"
    assert compiler._build_having_leaf_clause(
        {"table": "orders", "column": "amount", "operator": ">", "valueType": "expression", "rightExpression": "10 + 1", "aggFunc": "SUM"},
        is_multi_table=False,
        main_table="orders",
        aggregations=[],
        agg_by_id={},
    ) == "SUM(`amount`) > 10 + 1"


def test_builder_join_clause_helper_supports_multi_condition_and_missing_right_table():
    clauses = compiler._build_join_clauses("orders", [
        {
            "leftTable": "orders",
            "rightTable": "order_items",
            "joinType": "INNER",
            "joinConditions": [
                {"leftColumn": "order_id", "rightColumn": "order_id"},
                {"leftTable": "orders", "leftColumn": "tenant_id", "rightColumn": "tenant_id"},
            ],
        },
        {
            "leftTable": "orders",
            "rightTable": "users",
            "leftColumn": "user_id",
            "rightColumn": "id",
        },
    ])

    assert clauses[0] == "FROM orders"
    assert "INNER JOIN order_items ON orders.order_id = order_items.order_id AND orders.tenant_id = order_items.tenant_id" in clauses
    assert "LEFT JOIN users ON orders.user_id = users.id" in clauses


def test_builder_join_clause_helper_rejects_join_without_on():
    with pytest.raises(ValueError, match="缺少有效关联条件"):
        compiler._build_join_clauses("orders", [
            {"rightTable": "users", "joinConditions": [{"leftColumn": "", "rightColumn": "id"}]},
        ])
    with pytest.raises(ValueError, match="缺少有效关联条件"):
        compiler._build_join_clauses("orders", [
            {"rightTable": "items", "leftColumn": "", "rightColumn": "order_id"},
        ])
    with pytest.raises(ValueError, match="缺少 rightTable"):
        compiler._build_join_clauses("orders", [{"rightTable": ""}])


def test_compile_query_plan_sql_validates_shape_and_required_fields():
    with pytest.raises(ValueError, match="格式错误"):
        compile_query_plan_sql([])
    with pytest.raises(ValueError, match="缺少 mainTable"):
        compile_query_plan_sql({"dimensions": [{"table": "orders", "column": "id"}]})
    with pytest.raises(ValueError, match="至少选择"):
        compile_query_plan_sql({"mainTable": "orders", "dimensions": [], "aggregations": []})


def test_compile_query_plan_sql_rejects_raw_and_aggregate_metric_mix():
    with pytest.raises(ValueError, match="原始值与聚合函数混用"):
        compile_query_plan_sql({
            "mainTable": "orders",
            "dimensions": [],
            "aggregations": [
                {"id": "a1", "func": "RAW", "table": "orders", "column": "amount", "alias": "原始金额"},
                {"id": "a2", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"},
            ],
        })


def test_sql_dialect_time_grain_exprs_are_isolated_from_compiler():
    assert get_dialect("hive").format_date("order_time", "month") == "DATE_FORMAT(order_time, 'yyyy-MM')"
    assert get_dialect("hive").format_date("order_time", "week") == "CONCAT(CAST(YEAR(order_time) AS STRING), '-W', LPAD(CAST(WEEKOFYEAR(order_time) AS STRING), 2, '0'))"
    assert get_dialect("mysql").format_date("order_time", "month") == "DATE_FORMAT(order_time, '%Y-%m')"


def test_compile_query_plan_sql_detail_query():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "order_no", "alias": "订单编号", "transform": "raw"},
            {"id": "d2", "table": "orders", "column": "user_id", "alias": "用户ID", "transform": "raw"},
        ],
        "aggregations": [],
        "filters": [
            {"id": "f1", "table": "orders", "column": "status", "operator": "=", "value": "paid", "logic": "AND"}
        ],
        "orderBy": [{"id": "o1", "table": "orders", "column": "order_no", "direction": "DESC"}],
        "limit": 50,
    }

    sql = compile_query_plan_sql(builder_config)

    assert "FROM orders" in sql
    assert "GROUP BY" not in sql
    assert "WHERE `status` = 'paid'" in sql
    assert "LIMIT 50" in sql


def test_compile_query_plan_sql_duplicate_alias_raw_metric_and_join_conditions():
    builder_config = {
        "mainTable": "orders",
        "joins": [
            {
                "leftTable": "orders",
                "rightTable": "order_items",
                "joinType": "INNER",
                "joinConditions": [
                    {"leftColumn": "order_id", "rightColumn": "order_id"},
                    {"leftColumn": "tenant_id", "rightColumn": "tenant_id"},
                    {"leftColumn": "", "rightColumn": "ignored"},
                ],
            },
        ],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "status", "displayName": "重复名", "transform": "raw"},
            {"id": "d2", "table": "order_items", "column": "product_id", "displayName": "重复名", "transform": "raw"},
        ],
        "aggregations": [
            {"id": "a1", "func": "RAW", "table": "orders", "column": "order_no", "alias": "订单编号", "expression": "orders.order_no"},
        ],
        "filters": [
            {"id": "f1", "table": "orders", "column": "status", "operator": "IS NOT NULL", "logic": "AND"},
            {"id": "f2", "table": "orders", "column": "status", "operator": "STARTS WITH", "value": "pa", "logic": "AND"},
            {"id": "f3", "table": "orders", "column": "status", "operator": "ENDS WITH", "value": "id", "logic": "OR"},
            {"id": "f4", "table": "orders", "column": "status", "operator": "NOT LIKE", "value": "cancel", "logic": "AND"},
            {"id": "f5", "table": "orders", "column": "status", "operator": "EMPTY STRING", "logic": "AND"},
            {"id": "f6", "table": "orders", "column": "status", "operator": "IN", "rightExpression": "select status from valid_status", "logic": "AND"},
            {"id": "f7", "table": "orders", "column": "amount", "operator": ">", "value": "0", "expression": "orders.amount + orders.tax", "logic": "AND"},
        ],
        "orderBy": [{"id": "o1", "table": "orders", "column": "status", "direction": "ASC"}],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config)

    assert "orders.`status` AS `重复名`" in sql
    assert "order_items.`product_id` AS `重复名_2`" in sql
    assert "orders.order_no AS `订单编号`" in sql
    assert "INNER JOIN order_items ON orders.order_id = order_items.order_id AND orders.tenant_id = order_items.tenant_id" in sql
    assert "orders.`status` IS NOT NULL" in sql
    assert "orders.`status` LIKE 'pa%'" in sql
    assert "OR orders.`status` LIKE '%id'" in sql
    assert "orders.`status` NOT LIKE '%cancel%'" in sql
    assert "orders.`status` = ''" in sql
    assert "orders.`status` IN (select status from valid_status)" in sql
    assert "orders.amount + orders.tax > 0" in sql
    assert "ORDER BY `重复名` ASC" in sql


def test_compile_query_plan_sql_alias_suffix_skips_existing_duplicate():
    sql = compile_query_plan_sql({
        "mainTable": "orders",
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "status", "alias": "重复名"},
            {"id": "d2", "table": "orders", "column": "city", "alias": "重复名"},
            {"id": "d3", "table": "orders", "column": "province", "alias": "重复名"},
        ],
        "aggregations": [],
        "orderBy": [],
    })

    assert "`status` AS `重复名`" in sql
    assert "`city` AS `重复名_2`" in sql
    assert "`province` AS `重复名_3`" in sql


def test_compile_query_plan_sql_where_operator_branches_and_tree_empty_nodes():
    assert compiler._build_filter_tree_clause(
        {"and": []},
        for_having=False,
        is_multi_table=False,
        main_table="orders",
        dimension_expr_map={},
        aggregations=[],
        agg_by_id={},
    ) == ""
    assert compiler._build_filter_tree_clause(
        {"or": []},
        for_having=False,
        is_multi_table=False,
        main_table="orders",
        dimension_expr_map={},
        aggregations=[],
        agg_by_id={},
    ) == ""

    builder_config = {
        "mainTable": "orders",
        "mainAlias": "o",
        "joins": [
            {
                "leftTable": "orders",
                "leftAlias": "o",
                "rightTable": "users",
                "rightAlias": "u",
                "leftColumn": "user_id",
                "rightColumn": "user_id",
            }
        ],
        "dimensions": [{"id": "d1", "table": "orders", "tableAlias": "o", "column": "status", "alias": "状态"}],
        "aggregations": [],
        "filters": [
            {
                "or": [
                    {"id": "f1", "table": "orders", "tableAlias": "o", "column": "status", "operator": "=", "valueType": "field_ref", "rightTable": "users", "rightTableAlias": "u", "rightColumn": "level"},
                    {"id": "f2", "table": "orders", "tableAlias": "o", "column": "status", "operator": "IN", "valueType": "expression", "rightExpression": "select status from valid_status"},
                    {"id": "f3", "table": "orders", "tableAlias": "o", "column": "status", "operator": "NOT IN", "valueType": "expression", "rightExpression": "select status from blocked_status"},
                    {"id": "f4", "table": "orders", "tableAlias": "o", "column": "created_at", "operator": "BETWEEN", "value": ["2024-01-01", "current_date()"], "value_type": ["literal", "sql_function"]},
                ]
            },
        ],
        "orderBy": [],
    }

    sql = compile_query_plan_sql(builder_config)

    assert "WHERE" in sql
    assert "o.`status` = u.`level`" in sql
    assert "o.`status` IN (select status from valid_status)" in sql
    assert "o.`status` NOT IN (select status from blocked_status)" in sql
    assert "o.`created_at` BETWEEN '2024-01-01' AND current_date()" in sql


def test_compile_query_plan_sql_rejects_mixed_filter_tree_scope():
    with pytest.raises(ValueError, match="不能混用明细过滤和结果过滤"):
        compile_query_plan_sql({
            "mainTable": "orders",
            "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
            "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
            "filters": [
                {
                    "and": [
                        {"table": "orders", "column": "status", "operator": "=", "value": "paid"},
                        {"table": "orders", "column": "amount", "operator": ">", "value": 100, "aggFunc": "SUM"},
                    ]
                }
            ],
        })


def test_compile_query_plan_sql_supports_same_physical_table_with_aliases():
    builder_config = {
        "mainTable": "orders",
        "mainAlias": "orders",
        "joins": [
            {
                "leftTable": "orders",
                "leftAlias": "orders",
                "rightTable": "users",
                "rightAlias": "users",
                "joinType": "LEFT",
                "joinConditions": [
                    {"leftTable": "orders", "leftAlias": "orders", "leftColumn": "buyer_id", "rightColumn": "id"},
                ],
            },
            {
                "leftTable": "orders",
                "leftAlias": "orders",
                "rightTable": "users",
                "rightAlias": "users_2",
                "joinType": "LEFT",
                "joinConditions": [
                    {"leftTable": "orders", "leftAlias": "orders", "leftColumn": "seller_id", "rightColumn": "id"},
                ],
            },
        ],
        "dimensions": [
            {"id": "d1", "table": "users", "tableAlias": "users", "column": "name", "alias": "买家名称", "expression": "users.name"},
            {"id": "d2", "table": "users", "tableAlias": "users_2", "column": "name", "alias": "卖家名称", "expression": "users.name"},
        ],
        "aggregations": [],
        "filters": [
            {"id": "f1", "table": "users", "tableAlias": "users_2", "column": "status", "operator": "=", "value": "active"}
        ],
        "orderBy": [{"id": "o1", "table": "users", "tableAlias": "users_2", "column": "name", "direction": "ASC"}],
        "limit": 20,
    }

    sql = compile_query_plan_sql(builder_config)

    assert "FROM orders" in sql
    assert "LEFT JOIN users ON orders.buyer_id = users.id" in sql
    assert "LEFT JOIN users users_2 ON orders.seller_id = users_2.id" in sql
    assert "users.name AS `买家名称`" in sql
    assert "users_2.name AS `卖家名称`" in sql
    assert "WHERE users_2.`status` = 'active'" in sql
    assert "ORDER BY `卖家名称` ASC" in sql


def test_compile_query_plan_sql_rewrites_alias_when_alias_is_only_table():
    builder_config = {
        "mainTable": "users",
        "mainAlias": "users_2",
        "dimensions": [
            {"id": "d1", "table": "users", "tableAlias": "users_2", "column": "name", "alias": "卖家名称", "expression": "users.name"},
        ],
        "aggregations": [],
        "filters": [],
        "orderBy": [],
    }

    sql = compile_query_plan_sql(builder_config)

    assert "FROM users users_2" in sql
    assert "users_2.name AS `卖家名称`" in sql


def test_compile_query_plan_sql_count_variants_and_order_by_metric_expression():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [],
        "aggregations": [
            {"id": "a1", "func": "COUNT", "table": "orders", "column": "*", "alias": "总数"},
            {"id": "a2", "func": "COUNT_DISTINCT", "table": "orders", "column": "user_id", "alias": "用户数"},
            {"id": "a3", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额", "expression": "SUM(DISTINCT orders.amount)"},
        ],
        "filters": [
            {"id": "h1", "table": "orders", "column": "user_id", "operator": ">", "value": 3, "aggFunc": "COUNT_DISTINCT", "aggId": "a2"}
        ],
        "orderBy": [
            {"id": "o1", "table": "orders", "column": "amount", "direction": "DESC", "aggFunc": "SUM", "aggId": "a3"}
        ],
        "limit": 10,
    }

    sql = compile_query_plan_sql(builder_config)

    assert "COUNT(*) AS `总数`" in sql
    assert "COUNT(DISTINCT `user_id`) AS `用户数`" in sql
    assert "SUM(DISTINCT amount) AS `销售额`" in sql
    assert "HAVING COUNT(DISTINCT `user_id`) > 3" in sql
    assert "ORDER BY SUM(DISTINCT amount) DESC" in sql
    assert "GROUP BY" not in sql


def test_compile_query_plan_sql_count_distinct_star_and_raw_star_branches():
    count_distinct_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "dimensions": [],
        "aggregations": [{"id": "a1", "func": "COUNT_DISTINCT", "table": "orders", "column": "*", "alias": "去重总数"}],
    })
    raw_star_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "dimensions": [],
        "aggregations": [{"id": "a1", "func": "RAW", "table": "orders", "column": "*", "alias": "全部"}],
    })

    assert "COUNT(*) AS `去重总数`" in count_distinct_sql
    assert raw_star_sql.startswith("SELECT *\nFROM orders")


def test_compile_query_plan_sql_having_order_expression_fallback_uses_table_alias():
    builder_config = {
        "mainTable": "orders",
        "mainAlias": "o",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "tableAlias": "o", "column": "city", "alias": "城市"}],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "tableAlias": "o", "column": "amount", "alias": "销售额"},
        ],
        "filters": [
            {
                "id": "h1",
                "table": "orders",
                "tableAlias": "o",
                "column": "amount",
                "operator": ">",
                "value": 100,
                "aggFunc": "SUM",
                "expression": "SUM(orders.amount * 1.06)",
            }
        ],
        "orderBy": [
            {
                "id": "o1",
                "table": "orders",
                "tableAlias": "o",
                "column": "amount",
                "direction": "DESC",
                "aggFunc": "SUM",
                "expression": "SUM(orders.amount * 1.06)",
            }
        ],
    }

    sql = compile_query_plan_sql(builder_config)

    assert "HAVING SUM(o.amount * 1.06) > 100" in sql
    assert "ORDER BY SUM(o.amount * 1.06) DESC" in sql
    assert "SUM(orders.amount * 1.06)" not in sql


def test_compile_query_plan_sql_groups_dimension_for_unselected_having_order_metric():
    builder_config = {
        "mainTable": "users",
        "mainAlias": "u",
        "joins": [
            {
                "leftTable": "users",
                "leftAlias": "u",
                "rightTable": "orders",
                "rightAlias": "o",
                "joinType": "LEFT",
                "joinConditions": [{"leftAlias": "u", "leftColumn": "user_id", "rightColumn": "user_id"}],
            }
        ],
        "dimensions": [{"id": "d1", "table": "users", "tableAlias": "u", "column": "level", "alias": "用户等级"}],
        "aggregations": [],
        "filters": [
            {
                "id": "h1",
                "table": "orders",
                "tableAlias": "o",
                "column": "amount",
                "operator": ">",
                "value": 100,
                "aggFunc": "SUM",
                "expression": "SUM(orders.amount)",
            }
        ],
        "orderBy": [
            {
                "id": "o1",
                "table": "orders",
                "tableAlias": "o",
                "column": "amount",
                "direction": "DESC",
                "aggFunc": "SUM",
                "expression": "SUM(orders.amount)",
            }
        ],
    }

    sql = compile_query_plan_sql(builder_config)

    assert "GROUP BY u.`level`" in sql
    assert "HAVING SUM(o.amount) > 100" in sql
    assert "ORDER BY SUM(o.amount) DESC" in sql


def test_compile_query_plan_sql_having_tree_and_leaf_operator_branches():
    builder_config = {
        "mainTable": "orders",
        "mainAlias": "o",
        "joins": [
            {"rightTable": "targets", "rightAlias": "t", "leftAlias": "o", "leftColumn": "status", "rightColumn": "status"}
        ],
        "dimensions": [{"id": "d1", "table": "orders", "tableAlias": "o", "column": "status", "alias": "状态"}],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "tableAlias": "o", "column": "amount", "alias": "销售额"},
        ],
        "filters": [
            {
                "and": [
                    {"id": "h0", "table": "orders", "tableAlias": "o", "column": "score", "operator": "IS NOT NULL", "aggFunc": "RAW", "expression": "orders.score"},
                    {"id": "h1", "table": "orders", "tableAlias": "o", "column": "amount", "operator": "=", "aggFunc": "SUM", "valueType": "field_ref", "rightTable": "targets", "rightTableAlias": "t", "rightColumn": "target_amount"},
                    {"id": "h2", "table": "orders", "tableAlias": "o", "column": "amount", "operator": "IN", "aggFunc": "SUM", "rightExpression": "select amount from amount_whitelist"},
                    {"id": "h3", "table": "orders", "tableAlias": "o", "column": "amount", "operator": "NOT IN", "aggFunc": "SUM", "valueType": "expression", "rightExpression": "select amount from amount_blacklist"},
                    {"id": "h4", "table": "orders", "tableAlias": "o", "column": "amount", "operator": "EMPTY STRING", "aggFunc": "SUM"},
                    {"id": "h5", "table": "orders", "tableAlias": "o", "column": "amount", "operator": "STARTS WITH", "aggFunc": "SUM", "value": "1"},
                    {"id": "h6", "table": "orders", "tableAlias": "o", "column": "amount", "operator": "ENDS WITH", "aggFunc": "SUM", "value": "9"},
                    {"id": "h7", "table": "orders", "tableAlias": "o", "column": "amount", "operator": "NOT LIKE", "aggFunc": "SUM", "value": "bad"},
                    {"id": "h8", "table": "orders", "tableAlias": "o", "column": "amount", "operator": "IN", "aggFunc": "SUM", "value": [1, 2]},
                    {"id": "h9", "table": "orders", "tableAlias": "o", "column": "amount", "operator": "BETWEEN", "aggFunc": "SUM", "value": [10, 20]},
                ]
            }
        ],
        "orderBy": [],
    }

    sql = compile_query_plan_sql(builder_config)

    assert "HAVING" in sql
    assert "o.score IS NOT NULL" in sql
    assert "SUM(o.`amount`) = t.`target_amount`" in sql
    assert "SUM(o.`amount`) IN (select amount from amount_whitelist)" in sql
    assert "SUM(o.`amount`) NOT IN (select amount from amount_blacklist)" in sql
    assert "SUM(o.`amount`) = ''" in sql
    assert "SUM(o.`amount`) LIKE '1%'" in sql
    assert "SUM(o.`amount`) LIKE '%9'" in sql
    assert "SUM(o.`amount`) NOT LIKE '%bad%'" in sql
    assert "SUM(o.`amount`) IN (1, 2)" in sql
    assert "SUM(o.`amount`) BETWEEN 10 AND 20" in sql


def test_compile_query_plan_sql_empty_having_tree_is_ignored():
    sql = compile_query_plan_sql({
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
        "filters": [{"and": ["skip"]}],
    })

    assert "HAVING" not in sql


def test_compile_query_plan_sql_empty_having_tree_with_order_by_branch():
    sql = compile_query_plan_sql({
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
        "filters": [{"and": ["skip"]}],
        "orderBy": [{"table": "orders", "column": "status", "direction": "ASC", "displayName": "missing"}],
    })

    assert "HAVING" not in sql
    assert "ORDER BY `状态` ASC" in sql


def test_compile_query_plan_sql_single_child_filter_trees_are_unwrapped():
    sql = compile_query_plan_sql({
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
        "filters": [
            {"and": [{"table": "orders", "column": "status", "operator": "=", "value": "paid"}]},
            {"or": [{"table": "orders", "column": "amount", "operator": ">", "value": 100, "aggFunc": "SUM"}]},
        ],
    })

    assert "WHERE `status` = 'paid'" in sql
    assert "HAVING SUM(`amount`) > 100" in sql
    assert "(SUM(`amount`) > 100)" not in sql


def test_compile_query_plan_sql_having_expression_branches_without_tree():
    sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [{"id": "d1", "table": "orders", "tableAlias": "o", "column": "status", "alias": "状态"}],
        "aggregations": [
            {"id": "raw_score", "func": "RAW", "table": "orders", "tableAlias": "o", "column": "score", "alias": "分数", "expression": "orders.score"},
        ],
        "filters": [
            {"id": "h1", "table": "orders", "tableAlias": "o", "column": "score", "operator": ">", "valueType": "expression", "rightExpression": "10 + 1", "aggFunc": "RAW", "aggId": "raw_score"},
            {"id": "h2", "table": "orders", "tableAlias": "o", "column": "score", "operator": "IN", "valueType": "expression", "rightExpression": "select score from whitelist", "aggFunc": "RAW", "aggId": "raw_score", "logic": "OR"},
        ],
    })

    assert "HAVING o.score > 10 + 1 OR o.score IN (select score from whitelist)" in sql


def test_compile_query_plan_sql_having_non_tree_expression_in_branch():
    sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [{"id": "d1", "table": "orders", "tableAlias": "o", "column": "status", "alias": "状态"}],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "tableAlias": "o", "column": "amount", "alias": "销售额"}],
        "filters": [
            {
                "id": "h1",
                "table": "orders",
                "tableAlias": "o",
                "column": "amount",
                "operator": "IN",
                "aggFunc": "SUM",
                "valueType": "expression",
                "rightExpression": "select amount from whitelist",
            }
        ],
    })

    assert "HAVING SUM(`amount`) IN (select amount from whitelist)" in sql


def test_compile_query_plan_sql_having_structured_right_operand():
    sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [{"id": "d1", "table": "orders", "tableAlias": "o", "column": "pid", "alias": "分账户PID"}],
        "aggregations": [
            {"id": "out", "func": "SUM", "table": "orders", "tableAlias": "o", "column": "out_amount", "alias": "出金总额", "expression": "SUM(orders.out_amount)"},
            {"id": "in", "func": "SUM", "table": "orders", "tableAlias": "o", "column": "in_amount", "alias": "入金总额", "expression": "SUM(orders.in_amount)"},
        ],
        "filters": [
            {
                "id": "h1",
                "table": "orders",
                "tableAlias": "o",
                "column": "out_amount",
                "operator": ">",
                "aggFunc": "SUM",
                "aggId": "out",
                "expression": "SUM(orders.out_amount)",
                "rightOperand": {
                    "type": "binary",
                    "operator": "*",
                    "left": {
                        "type": "metric",
                        "table": "orders",
                        "tableAlias": "o",
                        "column": "in_amount",
                        "aggFunc": "SUM",
                        "aggId": "in",
                        "expression": "SUM(orders.in_amount)",
                    },
                    "right": {"type": "literal", "value": 10},
                },
            }
        ],
    })

    assert "HAVING SUM(o.out_amount) > (SUM(o.in_amount) * 10)" in sql


def test_compile_query_plan_sql_having_and_order_remaining_branches():
    raw_order_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
        "filters": [],
        "orderBy": [{"table": "orders", "column": "status", "direction": "ASC", "aggFunc": "RAW"}],
    })
    assert "ORDER BY `status` ASC" in raw_order_sql

    group_expr_order_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "city", "alias": "城市", "expression": "orders.city"}],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
        "filters": [],
        "orderBy": [{"table": "orders", "column": "city", "direction": "DESC", "displayName": "missing"}],
    })
    assert "ORDER BY `城市` DESC" in group_expr_order_sql

    raw_star_having_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
        "aggregations": [],
        "filters": [{"table": "orders", "column": "*", "operator": "IS NOT NULL", "aggFunc": "RAW"}],
    })
    assert "HAVING * IS NOT NULL" in raw_star_having_sql

    with pytest.raises(ValueError, match="聚合查询不能按未分组"):
        compile_query_plan_sql({
            "mainTable": "orders",
            "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
            "aggregations": [
                {"id": "sum_amount", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"},
                {"id": "avg_amount", "func": "AVG", "table": "orders", "column": "amount", "alias": "均额"},
            ],
            "orderBy": [{"table": "orders", "column": "amount", "direction": "DESC"}],
        })


def test_compile_query_plan_sql_order_by_dimension_expression_fallback():
    sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [{"id": "d1", "table": "orders", "tableAlias": "o", "column": "status", "alias": "状态", "expression": "orders.status"}],
        "aggregations": [{"id": "a1", "func": "RAW", "table": "orders", "tableAlias": "o", "column": "amount", "alias": "金额"}],
        "orderBy": [{"table": "orders", "tableAlias": "o", "column": "status", "direction": "ASC"}],
    })

    assert "ORDER BY `状态` ASC" in sql


def test_compile_query_plan_sql_rejects_limit_over_global_max():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "order_no", "alias": "订单编号", "transform": "raw"}],
        "aggregations": [],
        "filters": [],
        "orderBy": [],
        "limit": DEFAULT_ROW_LIMIT + 1,
    }

    with pytest.raises(ValueError, match="查询结果最多返回"):
        compile_query_plan_sql(builder_config)


def test_compile_query_plan_sql_does_not_add_default_limit():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "order_no", "alias": "订单编号", "transform": "raw"}],
        "aggregations": [],
        "filters": [],
        "orderBy": [],
    }

    sql = compile_query_plan_sql(builder_config)

    assert "LIMIT" not in sql


def test_compile_query_plan_sql_rejects_offset_without_limit():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "order_no", "alias": "订单编号", "transform": "raw"}],
        "aggregations": [],
        "filters": [],
        "orderBy": [],
        "offset": 20,
    }

    with pytest.raises(ValueError, match="OFFSET 必须配合 LIMIT 使用"):
        compile_query_plan_sql(builder_config)


def test_compile_query_plan_sql_rejects_invalid_offset():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "order_no", "alias": "订单编号", "transform": "raw"}],
        "aggregations": [],
        "filters": [],
        "orderBy": [],
        "limit": 10,
        "offset": -1,
    }

    with pytest.raises(ValueError, match="OFFSET 必须为非负整数"):
        compile_query_plan_sql(builder_config)


def test_compile_query_plan_sql_mysql_offset_uses_dialect_pagination():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "order_no", "alias": "订单编号", "transform": "raw"}],
        "aggregations": [],
        "filters": [],
        "orderBy": [],
        "limit": 10,
        "offset": 20,
    }

    sql = compile_query_plan_sql(builder_config, dialect="mysql")

    assert sql.endswith("LIMIT 20, 10")


def test_compile_query_plan_sql_hive_offset_uses_mysql_compatible_pagination():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "order_no", "alias": "订单编号", "transform": "raw"}],
        "aggregations": [],
        "filters": [],
        "orderBy": [],
        "limit": 10,
        "offset": 20,
    }

    sql = compile_query_plan_sql(builder_config, dialect="hive")

    assert sql.endswith("LIMIT 20, 10")
    assert "OFFSET" not in sql


def test_compile_query_plan_sql_aggregate_query():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "status", "alias": "订单状态", "transform": "raw"},
        ],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "订单金额"}
        ],
        "filters": [],
        "orderBy": [{"id": "o1", "table": "orders", "column": "amount", "direction": "DESC", "aggFunc": "SUM", "aggId": "a1"}],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config)

    assert "SUM(`amount`)" in sql
    assert "GROUP BY `status`" in sql
    assert "ORDER BY SUM(`amount`) DESC" in sql


def test_compile_query_plan_sql_aggregate_query_rejects_detail_order_by():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "status", "alias": "订单状态", "transform": "raw"},
        ],
        "aggregations": [
            {"id": "a1", "func": "COUNT", "table": "orders", "column": "order_id", "alias": "订单数量"}
        ],
        "filters": [],
        "orderBy": [{"id": "o1", "table": "orders", "column": "user_id", "direction": "DESC"}],
        "limit": 10,
    }

    with pytest.raises(ValueError, match="聚合查询不能按未分组的明细字段排序"):
        compile_query_plan_sql(builder_config, dialect="hive")


def test_compile_query_plan_sql_aggregate_query_repairs_metric_order_by_without_agg_metadata():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "status", "alias": "订单状态", "transform": "raw"},
        ],
        "aggregations": [
            {
                "id": "a1",
                "func": "COUNT",
                "table": "orders",
                "column": "order_id",
                "alias": "订单数量",
                "expression": "COUNT(orders.order_id)",
            }
        ],
        "filters": [],
        "orderBy": [{"id": "o1", "table": "orders", "column": "order_id", "direction": "DESC"}],
        "limit": 10,
    }

    sql = compile_query_plan_sql(builder_config, dialect="hive")

    assert "ORDER BY COUNT(order_id) DESC" in sql
    assert "ORDER BY orders.order_id DESC" not in sql


def test_compile_query_plan_sql_order_by_group_expression_and_detail_fallback():
    aggregate_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "created_at", "alias": "创建时间", "transform": "month"},
        ],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
        "orderBy": [{"table": "orders", "column": "created_at", "direction": "DESC"}],
    })
    detail_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
        "aggregations": [],
        "orderBy": [{"table": "orders", "column": "created_at", "direction": "DESC"}],
    })

    assert "ORDER BY `创建时间_month` DESC" in aggregate_sql
    assert "ORDER BY orders.created_at DESC" in detail_sql


def test_compile_query_plan_sql_compare_query_is_rejected():
    builder_config = {
        "mainTable": "orders",
        "joins": [
            {
                "id": "j1",
                "leftTable": "orders",
                "rightTable": "order_items",
                "leftColumn": "order_id",
                "rightColumn": "order_id",
                "joinType": "LEFT",
            }
        ],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "order_time", "alias": "下单月份", "transform": "month"},
        ],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "order_items", "column": "quantity", "alias": "销量总和"}
        ],
        "filters": [],
        "orderBy": [],
        "limit": 100,
        "compare": {"type": "yoy", "mode": "trend", "strategy": "calc"},
    }

    with pytest.raises(ValueError, match="不再支持 compare"):
        compile_query_plan_sql(builder_config, dialect="hive")


def test_compile_query_plan_sql_regular_query_hive_time_transform():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "order_time", "alias": "下单月份", "transform": "month"},
        ],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}
        ],
        "filters": [],
        "orderBy": [],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config, dialect="hive")

    assert "DATE_FORMAT(`order_time`, 'yyyy-MM')" in sql
    assert "GROUP BY DATE_FORMAT(`order_time`, 'yyyy-MM')" in sql
    assert "%Y-%m" not in sql


def test_compile_query_plan_sql_hive_time_filter_uses_raw_column_when_grouped_by_month():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "order_time", "alias": "下单时间", "transform": "month"},
        ],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}
        ],
        "filters": [
            {"id": "f1", "table": "orders", "column": "order_time", "operator": ">=", "value": "2024-01-01 00:00:00", "logic": "AND"},
            {"id": "f2", "table": "orders", "column": "order_time", "operator": "<=", "value": "2024-12-31 23:59:59", "logic": "AND"},
        ],
        "orderBy": [],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config, dialect="hive")

    assert "WHERE `order_time` >= '2024-01-01 00:00:00' AND `order_time` <= '2024-12-31 23:59:59'" in sql
    assert "GROUP BY DATE_FORMAT(`order_time`, 'yyyy-MM')" in sql


def test_compile_query_plan_sql_hive_time_order_by_uses_selected_dimension_alias():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "order_time", "alias": "下单时间", "transform": "month"},
        ],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}
        ],
        "filters": [],
        "orderBy": [{"id": "o1", "table": "orders", "column": "order_time", "direction": "ASC"}],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config, dialect="hive")

    assert "DATE_FORMAT(`order_time`, 'yyyy-MM') AS `下单时间_month`" in sql
    assert "GROUP BY DATE_FORMAT(`order_time`, 'yyyy-MM')" in sql
    assert "ORDER BY `下单时间_month` ASC" in sql


def test_compile_query_plan_sql_hive_expression_time_order_by_uses_selected_alias():
    builder_config = {
        "mainTable": "ods_pay_org_subsidiary_ledger_balance_incr_d",
        "joins": [],
        "dimensions": [
            {
                "id": "d1",
                "table": "ods_pay_org_subsidiary_ledger_balance_incr_d",
                "column": "ymd",
                "alias": "日期分区",
                "expression": "from_unixtime(unix_timestamp(`ymd`, 'yyyyMMdd'), 'yyyy-MM-dd')",
                "transform": "day",
            },
        ],
        "aggregations": [
            {
                "id": "a1",
                "func": "SUM",
                "table": "ods_pay_org_subsidiary_ledger_balance_incr_d",
                "column": "balance",
                "alias": "汇总金额",
            }
        ],
        "filters": [],
        "orderBy": [
            {
                "id": "o1",
                "table": "ods_pay_org_subsidiary_ledger_balance_incr_d",
                "column": "ymd",
                "direction": "ASC",
            },
        ],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config, dialect="hive")

    assert "TO_DATE(from_unixtime(unix_timestamp(`ymd`, 'yyyyMMdd'), 'yyyy-MM-dd')) AS `日期分区_day`" in sql
    assert "GROUP BY TO_DATE(from_unixtime(unix_timestamp(`ymd`, 'yyyyMMdd'), 'yyyy-MM-dd'))" in sql
    assert "ORDER BY `日期分区_day` ASC" in sql


def test_compile_query_plan_sql_regular_query_hive_week_transform():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "order_time", "alias": "下单周", "transform": "week"},
        ],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}
        ],
        "filters": [],
        "orderBy": [],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config, dialect="hive")

    assert "CONCAT(CAST(YEAR(`order_time`) AS STRING), '-W', LPAD(CAST(WEEKOFYEAR(`order_time`) AS STRING), 2, '0'))" in sql
    assert "DATE_FORMAT(`order_time`, 'YYYY')" not in sql


def test_compile_query_plan_sql_regular_query_hour_transform_uses_dialect():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "order_time", "alias": "下单小时", "transform": "hour"},
        ],
        "aggregations": [
            {"id": "a1", "func": "COUNT", "table": "orders", "column": "order_id", "alias": "订单数"}
        ],
        "filters": [],
        "orderBy": [],
        "limit": 100,
    }

    hive_sql = compile_query_plan_sql(builder_config, dialect="hive")
    mysql_sql = compile_query_plan_sql(builder_config, dialect="mysql")

    assert "DATE_FORMAT(`order_time`, 'yyyy-MM-dd HH:00')" in hive_sql
    assert "%Y-%m-%d %H:00" not in hive_sql
    assert "DATE_FORMAT(`order_time`, '%Y-%m-%d %H:00')" in mysql_sql


def test_compile_query_plan_sql_compare_query_week_is_rejected_by_compare_policy():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "order_time", "alias": "下单周", "transform": "week"},
        ],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}
        ],
        "filters": [],
        "orderBy": [],
        "limit": 100,
        "compare": {"type": "yoy", "mode": "trend", "strategy": "calc"},
    }

    with pytest.raises(ValueError, match="不再支持 compare"):
        compile_query_plan_sql(builder_config, dialect="mysql")
    with pytest.raises(ValueError, match="不再支持 compare"):
        compile_query_plan_sql(builder_config, dialect="hive")


def test_compile_query_plan_sql_field_ref_filter():
    builder_config = {
        "mainTable": "orders",
        "joins": [
            {
                "id": "j1",
                "leftTable": "orders",
                "rightTable": "order_items",
                "leftColumn": "order_id",
                "rightColumn": "order_id",
                "joinType": "LEFT",
            }
        ],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "order_id", "alias": "订单ID", "transform": "raw"},
            {"id": "d2", "table": "order_items", "column": "product_id", "alias": "商品ID", "transform": "raw"},
        ],
        "aggregations": [],
        "filters": [
            {
                "id": "f1",
                "table": "orders",
                "column": "user_id",
                "operator": "=",
                "logic": "AND",
                "valueType": "field_ref",
                "rightTable": "order_items",
                "rightColumn": "product_id",
            }
        ],
        "orderBy": [],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config)
    assert "WHERE orders.`user_id` = order_items.`product_id`" in sql


def test_compile_query_plan_sql_expression_filter():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "order_id", "alias": "订单ID", "transform": "raw"},
        ],
        "aggregations": [],
        "filters": [
            {
                "id": "f1",
                "table": "orders",
                "column": "create_time",
                "operator": ">=",
                "logic": "AND",
                "valueType": "expression",
                "rightExpression": "DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)",
            }
        ],
        "orderBy": [],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config)
    assert "WHERE `create_time` >= DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)" in sql


def test_compile_query_plan_sql_between_with_mixed_value_type_array():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "order_id", "alias": "订单ID", "transform": "raw"}],
        "aggregations": [],
        "filters": [
            {
                "id": "f1",
                "table": "orders",
                "column": "order_time",
                "operator": "BETWEEN",
                "logic": "AND",
                "value": ["2025-01-01", "current_date()"],
                "value_type": ["literal", "sql_function"],
            }
        ],
        "orderBy": [],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config)
    assert "BETWEEN '2025-01-01' AND current_date()" in sql


def test_compile_query_plan_sql_rejects_raw_and_aggregate_metric_mix():
    builder_config = {
        "mainTable": "orders",
        "dimensions": [],
        "aggregations": [
            {"id": "raw_1", "func": "RAW", "table": "orders", "column": "order_no", "alias": "订单编号"},
            {"id": "sum_1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"},
        ],
    }

    with pytest.raises(ValueError, match="原始值与聚合函数混用"):
        compile_query_plan_sql(builder_config)


def test_compile_query_plan_sql_filter_tree_and_mixed_tree_rejection():
    where_tree_config = {
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "order_id", "alias": "订单ID"}],
        "aggregations": [],
        "filters": [
            {
                "or": [
                    {"table": "orders", "column": "status", "operator": "=", "value": "paid"},
                    {
                        "and": [
                            {"table": "orders", "column": "amount", "operator": ">", "value": 100},
                            {"table": "orders", "column": "status", "operator": "LIKE", "value": "refund%"},
                        ]
                    },
                ]
            }
        ],
    }
    mixed_tree_config = {
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
        "filters": [
            {
                "and": [
                    {"table": "orders", "column": "status", "operator": "=", "value": "paid"},
                    {"table": "orders", "column": "amount", "operator": ">", "value": 100, "aggFunc": "SUM", "aggId": "a1"},
                ]
            }
        ],
    }

    sql = compile_query_plan_sql(where_tree_config)

    assert "WHERE `status` = 'paid' OR (`amount` > 100 AND `status` LIKE 'refund%')" in sql
    with pytest.raises(ValueError, match="不能混用明细过滤和结果过滤"):
        compile_query_plan_sql(mixed_tree_config)


def test_compile_query_plan_sql_having_tree_and_special_operators():
    tree_config = {
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额", "expression": "SUM(orders.amount)"},
            {"id": "a2", "func": "MAX", "table": "orders", "column": "amount", "alias": "最大金额"},
        ],
        "filters": [
            {
                "or": [
                    {"table": "orders", "column": "amount", "operator": "BETWEEN", "value": [10, 20], "aggFunc": "SUM", "aggId": "a1"},
                    {"table": "orders", "column": "amount", "operator": "IN", "rightExpression": "select amount from whitelist", "aggFunc": "SUM", "aggId": "a1"},
                ]
            },
        ],
    }
    leaf_config = {
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"},
            {"id": "a2", "func": "MAX", "table": "orders", "column": "amount", "alias": "最大金额"},
        ],
        "filters": [
            {
                "table": "orders",
                "column": "amount",
                "operator": ">",
                "valueType": "field_ref",
                "rightTable": "orders",
                "rightColumn": "discount",
                "aggFunc": "MAX",
                "aggId": "a2",
                "logic": "AND",
            },
            {
                "table": "orders",
                "column": "amount",
                "operator": "NOT IN",
                "valueType": "expression",
                "rightExpression": "select amount from blacklist",
                "aggFunc": "MAX",
                "aggId": "a2",
                "logic": "OR",
            },
        ],
    }

    tree_sql = compile_query_plan_sql(tree_config)
    leaf_sql = compile_query_plan_sql(leaf_config)

    assert "HAVING SUM(orders.amount) BETWEEN 10 AND 20 OR SUM(orders.amount) IN (select amount from whitelist)" in tree_sql
    assert "HAVING MAX(`amount`) > `discount`" in leaf_sql
    assert "OR MAX(`amount`) NOT IN (select amount from blacklist)" in leaf_sql


def test_compile_query_plan_sql_having_leaf_operator_variants():
    builder_config = {
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
        "filters": [
            {"table": "orders", "column": "amount", "operator": "IS NULL", "aggFunc": "SUM", "aggId": "a1"},
            {"table": "orders", "column": "amount", "operator": "EMPTY STRING", "aggFunc": "SUM", "aggId": "a1", "logic": "OR"},
            {"table": "orders", "column": "amount", "operator": "STARTS WITH", "value": "1", "aggFunc": "SUM", "aggId": "a1", "logic": "OR"},
            {"table": "orders", "column": "amount", "operator": "ENDS WITH", "value": "9", "aggFunc": "SUM", "aggId": "a1", "logic": "OR"},
            {"table": "orders", "column": "amount", "operator": "NOT LIKE", "value": "0", "aggFunc": "SUM", "aggId": "a1", "logic": "OR"},
            {"table": "orders", "column": "amount", "operator": "IN", "value": [1, 2], "aggFunc": "SUM", "aggId": "a1", "logic": "OR"},
        ],
    }

    sql = compile_query_plan_sql(builder_config)

    assert "HAVING SUM(`amount`) IS NULL" in sql
    assert "OR SUM(`amount`) = ''" in sql
    assert "OR SUM(`amount`) LIKE '1%'" in sql
    assert "OR SUM(`amount`) LIKE '%9'" in sql
    assert "OR SUM(`amount`) NOT LIKE '%0%'" in sql
    assert "OR SUM(`amount`) IN (1, 2)" in sql


def test_compile_query_plan_sql_order_by_ambiguous_aggregations_rejected():
    builder_config = {
        "mainTable": "orders",
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "状态"}],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"},
            {"id": "a2", "func": "AVG", "table": "orders", "column": "amount", "alias": "平均金额"},
        ],
        "orderBy": [{"table": "orders", "column": "amount", "direction": "DESC"}],
        "limit": 10,
    }

    with pytest.raises(ValueError, match="聚合查询不能按未分组的明细字段排序"):
        compile_query_plan_sql(builder_config)


def test_compile_query_plan_sql_between_with_wrong_literal_keeps_string_literal():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "order_id", "alias": "订单ID", "transform": "raw"}],
        "aggregations": [],
        "filters": [
            {
                "id": "f1",
                "table": "orders",
                "column": "order_time",
                "operator": "BETWEEN",
                "logic": "AND",
                "value": ["2025-01-01", "current_date()"],
                "value_type": "literal",
            }
        ],
        "orderBy": [],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config)
    assert "BETWEEN '2025-01-01' AND 'current_date()'" in sql


def test_compile_query_plan_sql_between_without_value_type_auto_unquotes_safe_function():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "order_id", "alias": "订单ID", "transform": "raw"}],
        "aggregations": [],
        "filters": [
            {
                "id": "f1",
                "table": "orders",
                "column": "order_time",
                "operator": "BETWEEN",
                "logic": "AND",
                "value": ["2025-01-01", "current_date()"],
            }
        ],
        "orderBy": [],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config)
    assert "BETWEEN '2025-01-01' AND current_date()" in sql


def test_compile_query_plan_sql_where_or_tree_and_empty_tree_are_handled():
    or_builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "order_id", "alias": "订单ID", "transform": "raw"}],
        "aggregations": [],
        "filters": [
            {
                "or": [
                    {"id": "f1", "table": "orders", "column": "status", "operator": "=", "value": "paid"},
                    {"id": "f2", "table": "orders", "column": "status", "operator": "=", "value": "refunded"},
                ]
            }
        ],
        "orderBy": [],
        "limit": 100,
    }
    single_child_tree_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "order_id", "alias": "订单ID", "transform": "raw"}],
        "aggregations": [],
        "filters": [{"and": [{"id": "f1", "table": "orders", "column": "status", "operator": "=", "value": "paid"}]}],
        "orderBy": [],
        "limit": 100,
    }

    or_sql = compile_query_plan_sql(or_builder_config)
    single_child_sql = compile_query_plan_sql(single_child_tree_config)

    assert "WHERE `status` = 'paid' OR `status` = 'refunded'" in or_sql
    assert "WHERE `status` = 'paid'" in single_child_sql


def test_compile_query_plan_sql_aggregate_filter_tree_goes_to_having():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "status", "alias": "订单状态", "transform": "raw"},
        ],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "订单金额"}
        ],
        "filters": [
            {
                "and": [
                    {
                        "id": "f1",
                        "table": "orders",
                        "column": "amount",
                        "operator": ">",
                        "value": "100",
                        "logic": "AND",
                        "aggFunc": "SUM",
                        "aggId": "a1",
                    }
                ]
            }
        ],
        "orderBy": [],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config)
    assert "WHERE" not in sql
    assert "HAVING" in sql
    assert "SUM(`amount`) > 100" in sql


def test_compile_query_plan_sql_having_leaf_operator_variants():
    builder_config = {
        "mainTable": "orders",
        "joins": [
            {
                "leftTable": "orders",
                "rightTable": "order_items",
                "joinType": "LEFT",
                "leftColumn": "order_id",
                "rightColumn": "order_id",
            }
        ],
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "订单状态", "transform": "raw"}],
        "aggregations": [
            {"id": "a_raw", "func": "RAW", "table": "orders", "column": "status", "alias": "原始状态", "expression": "orders.status"},
        ],
        "filters": [
            {"id": "h0", "table": "orders", "column": "status", "operator": "IS NOT NULL", "aggFunc": "RAW", "aggId": "a_raw"},
            {"id": "h1", "table": "orders", "column": "status", "operator": "=", "aggFunc": "RAW", "aggId": "a_raw", "valueType": "field_ref", "rightTable": "order_items", "rightColumn": "status", "logic": "AND"},
            {"id": "h2", "table": "orders", "column": "status", "operator": "NOT IN", "aggFunc": "RAW", "rightExpression": "select bad_status from status_blacklist", "logic": "AND"},
            {"id": "h3", "table": "orders", "column": "status", "operator": "IN", "aggFunc": "RAW", "valueType": "expression", "rightExpression": "select status from valid_status", "logic": "AND"},
            {"id": "h4", "table": "orders", "column": "status", "operator": "EMPTY STRING", "aggFunc": "RAW", "logic": "AND"},
            {"id": "h5", "table": "orders", "column": "status", "operator": "STARTS WITH", "value": "pa", "aggFunc": "RAW", "logic": "AND"},
            {"id": "h6", "table": "orders", "column": "status", "operator": "ENDS WITH", "value": "id", "aggFunc": "RAW", "logic": "AND"},
            {"id": "h7", "table": "orders", "column": "status", "operator": "LIKE", "value": "pay", "aggFunc": "RAW", "logic": "AND"},
            {"id": "h8", "table": "orders", "column": "status", "operator": "IN", "value": ["paid", "finished"], "aggFunc": "RAW", "logic": "AND"},
            {"id": "h9", "table": "orders", "column": "status", "operator": "BETWEEN", "value": ["a", "z"], "aggFunc": "RAW", "logic": "AND"},
        ],
        "orderBy": [],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config)

    assert "HAVING orders.status IS NOT NULL" in sql
    assert "AND orders.status = order_items.`status`" in sql
    assert "orders.status NOT IN (select bad_status from status_blacklist)" in sql
    assert "orders.status IN (select status from valid_status)" in sql
    assert "orders.status = ''" in sql
    assert "orders.status LIKE 'pa%'" in sql
    assert "orders.status LIKE '%id'" in sql
    assert "orders.status LIKE '%pay%'" in sql
    assert "orders.status IN ('paid', 'finished')" in sql
    assert "orders.status BETWEEN 'a' AND 'z'" in sql


def test_compile_query_plan_sql_having_tree_or_and_empty_tree_variants():
    or_tree_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "订单状态", "transform": "raw"}],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
        "filters": [
            {
                "or": [
                    {"id": "h1", "table": "orders", "column": "amount", "operator": ">", "value": 100, "aggFunc": "SUM", "aggId": "a1"},
                    {"id": "h2", "table": "orders", "column": "amount", "operator": "<", "value": 10, "aggFunc": "SUM", "aggId": "a1"},
                ]
            }
        ],
        "orderBy": [],
        "limit": 100,
    }
    empty_tree_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [{"id": "d1", "table": "orders", "column": "status", "alias": "订单状态", "transform": "raw"}],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "销售额"}],
        "filters": [{"or": [{"bad": "node"}]}],
        "orderBy": [],
        "limit": 100,
    }

    or_sql = compile_query_plan_sql(or_tree_config)
    empty_sql = compile_query_plan_sql(empty_tree_config)

    assert "HAVING SUM(`amount`) > 100 OR SUM(`amount`) < 10" in or_sql
    assert "HAVING" not in empty_sql


def test_compile_query_plan_sql_mixed_filter_tree_raise_error():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "status", "alias": "订单状态", "transform": "raw"},
        ],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "column": "amount", "alias": "订单金额"}
        ],
        "filters": [
            {
                "and": [
                    {
                        "id": "f1",
                        "table": "orders",
                        "column": "status",
                        "operator": "=",
                        "value": "paid",
                        "logic": "AND",
                    },
                    {
                        "id": "f2",
                        "table": "orders",
                        "column": "amount",
                        "operator": ">",
                        "value": "100",
                        "logic": "AND",
                        "aggFunc": "SUM",
                        "aggId": "a1",
                    },
                ]
            }
        ],
        "orderBy": [],
        "limit": 100,
    }

    with pytest.raises(ValueError, match="不能混用明细过滤和结果过滤"):
        compile_query_plan_sql(builder_config)


def test_compile_query_plan_sql_mixed_raw_and_agg_metrics_raise_error():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [],
        "aggregations": [
            {"id": "a1", "func": "RAW", "table": "orders", "column": "order_id", "alias": "订单编号"},
            {"id": "a2", "func": "COUNT", "table": "orders", "column": "order_id", "alias": "订单数量"},
        ],
        "filters": [],
        "orderBy": [],
        "limit": 100,
    }

    with pytest.raises(ValueError, match="指标区不支持原始值与聚合函数混用"):
        compile_query_plan_sql(builder_config)


def test_compile_query_plan_sql_raw_star_metric_and_alias_collision_window():
    builder_config = {
        "mainTable": "orders",
        "joins": [],
        "dimensions": [
            {"id": "d1", "table": "orders", "column": "status", "alias": "重复名", "transform": "raw"},
        ],
        "aggregations": [
            {"id": "a1", "func": "RAW", "table": "orders", "column": "*", "alias": "重复名"},
            {"id": "a2", "func": "RAW", "table": "orders", "column": "order_no", "alias": "重复名_2"},
        ],
        "filters": [],
        "orderBy": [{"id": "o1", "table": "orders", "column": "not_selected", "direction": "ASC"}],
        "limit": 100,
    }

    sql = compile_query_plan_sql(builder_config)

    assert "*, `order_no` AS `重复名_2_2`" in sql
    assert "ORDER BY orders.not_selected ASC" in sql


def test_semantic_unified_engine_allows_dimension_in_metrics():
    semantic_config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "订单金额", "table": "orders", "column": "amount", "expression": "SUM(orders.amount)"}
        ],
        "dimensions": [
            {"name": "订单编号", "table": "orders", "column": "order_no", "expression": "orders.order_no", "logical_type": "text"},
            {"name": "用户ID", "table": "orders", "column": "user_id", "expression": "orders.user_id", "logical_type": "text"},
        ],
        "relationships": [],
        "tables": [
            {"table_name": "orders", "columns": [{"name": "order_no"}, {"name": "user_id"}, {"name": "amount"}]}
        ],
    }

    generator = SemanticAIQueryAdapter(semantic_config)
    result = generator.generate_sql(
        metrics=["订单编号", "用户ID", "订单金额"],
        dimensions=[],
        filters=[],
        having=[],
        order_by=[],
        limit=100
    )
    assert result["success"] is True
    sql = result["sql"]
    assert "order_no AS `订单编号`" in sql
    assert "user_id AS `用户ID`" in sql
    assert "SUM(orders.amount)" in sql or "SUM(amount)" in sql
    assert "GROUP BY order_no, user_id" in sql


def test_semantic_unified_engine_uses_dsl_metric_expression():
    semantic_config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "订单数量", "table": "orders", "column": "order_id", "expression": "COUNT(orders.order_id)"}
        ],
        "dimensions": [
            {"name": "订单编号", "table": "orders", "column": "order_id", "expression": "orders.order_id", "logical_type": "text"},
            {"name": "用户ID", "table": "orders", "column": "user_id", "expression": "orders.user_id", "logical_type": "text"},
        ],
        "relationships": [],
        "tables": [
            {"table_name": "orders", "columns": [{"name": "order_id"}, {"name": "user_id"}]}
        ],
    }

    generator = SemanticAIQueryAdapter(semantic_config)
    result = generator.generate_sql(
        metrics=["订单数量"],
        dimensions=[],
        filters=[],
        having=[],
        order_by=[],
        limit=100,
    )
    assert result["success"] is True
    sql = result["sql"]
    assert "COUNT(order_id)" in sql
    assert "GROUP BY" not in sql.upper()


def test_semantic_unified_engine_dimensions_only_query_is_detail():
    semantic_config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "订单数量", "table": "orders", "column": "order_id", "expression": "COUNT(orders.order_id)"}
        ],
        "dimensions": [
            {"name": "订单编号", "table": "orders", "column": "order_id", "expression": "orders.order_id", "logical_type": "text"},
            {"name": "订单金额", "table": "orders", "column": "amount", "expression": "orders.amount", "logical_type": "number"},
        ],
        "relationships": [],
        "tables": [
            {"table_name": "orders", "columns": [{"name": "order_id"}, {"name": "amount"}]}
        ],
    }

    result = SemanticAIQueryAdapter(semantic_config).generate_sql(
        metrics=[],
        dimensions=["订单编号", "订单金额"],
        filters=[],
        having=[],
        order_by=[],
        limit=100,
    )

    assert result["success"] is True
    sql = result["sql"]
    assert "GROUP BY" not in sql.upper()
    assert "COUNT(" not in sql.upper()
    assert "order_id AS `订单编号`" in sql
    assert "amount AS `订单金额`" in sql


def test_semantic_unified_engine_uses_aliases_for_same_physical_table_roles():
    semantic_config = {
        "source_type": "mysql",
        "metrics": [],
        "dimensions": [
            {"name": "订单编号", "table": "orders", "table_alias": "orders", "column": "order_no", "expression": "orders.order_no", "logical_type": "text"},
            {"name": "买家名称", "table": "users", "table_alias": "users", "column": "name", "expression": "users.name", "logical_type": "text"},
            {"name": "卖家名称", "table": "users", "table_alias": "users_2", "column": "name", "expression": "users.name", "logical_type": "text"},
        ],
        "relationships": [
            {
                "left_table": "orders",
                "left_alias": "orders",
                "right_table": "users",
                "right_alias": "users",
                "join_type": "left",
                "join_conditions": [{"left_column": "buyer_id", "right_column": "id"}],
                "relationship_type": "many_to_one",
            },
            {
                "left_table": "orders",
                "left_alias": "orders",
                "right_table": "users",
                "right_alias": "users_2",
                "join_type": "left",
                "join_conditions": [{"left_column": "seller_id", "right_column": "id"}],
                "relationship_type": "many_to_one",
            },
        ],
        "tables": [
            {"table_name": "orders", "columns": [{"name": "order_no"}, {"name": "buyer_id"}, {"name": "seller_id"}]},
            {"table_name": "users", "columns": [{"name": "id"}, {"name": "name"}]},
        ],
    }

    generator = SemanticAIQueryAdapter(semantic_config)
    result = generator.generate_sql(
        metrics=[],
        dimensions=["订单编号", "买家名称", "卖家名称"],
        filters=[],
        having=[],
        order_by=[],
        limit=100,
    )

    assert result["success"] is True
    sql = result["sql"]
    assert "FROM orders" in sql
    assert "LEFT JOIN users ON orders.buyer_id = users.id" in sql
    assert "LEFT JOIN users users_2 ON orders.seller_id = users_2.id" in sql
    assert "users.name AS `买家名称`" in sql
    assert "users_2.name AS `卖家名称`" in sql


def test_builder_sql_compiler_remaining_helper_edges():
    assert compiler._is_sql_function("current_date() /* unsafe */") is False
    assert compiler._sql_literal({"bad": True}) == "'{''bad'': True}'"
    assert compiler._sql_literal("current_timestamp()") == "current_timestamp()"
    assert compiler._sql_literal('"current_timestamp()"') == "current_timestamp()"
    assert compiler._sql_literal("'current_date(); drop table orders'", "sql_function") == "'''current_date(); drop table orders'''"
    assert compiler._sql_list_literal(["a", "current_date()"], ["literal"]) == "('a', current_date())"

    assert compiler._build_where_leaf_clause(
        {"table": "orders", "column": "status", "operator": "IN", "value": ["paid", "cancelled"]},
        is_multi_table=False,
        dimension_expr_map={},
        main_table="orders",
    ) == "`status` IN ('paid', 'cancelled')"
    assert compiler._build_where_leaf_clause(
        {"table": "orders", "column": "status", "operator": "IN", "valueType": "expression", "rightExpression": "select status from t"},
        is_multi_table=False,
        dimension_expr_map={},
        main_table="orders",
    ) == "`status` IN (select status from t)"
    assert compiler._build_where_leaf_clause(
        {"table": "orders", "column": "status", "operator": "NOT IN", "valueType": "field_ref", "rightTable": "users", "rightColumn": "status"},
        is_multi_table=True,
        dimension_expr_map={},
        main_table="orders",
    ) == "orders.`status` NOT IN users.`status`"

    assert compiler._build_having_leaf_clause(
        {"table": "orders", "column": "amount", "operator": ">", "value": 10, "aggFunc": "RAW"},
        is_multi_table=False,
        main_table="orders",
        aggregations=[],
        agg_by_id={},
    ) == "`amount` > 10"
    assert compiler._build_having_leaf_clause(
        {"table": "orders", "column": "user_id", "operator": ">", "value": 1, "aggFunc": "COUNT_DISTINCT"},
        is_multi_table=False,
        main_table="orders",
        aggregations=[],
        agg_by_id={},
    ) == "COUNT(DISTINCT `user_id`) > 1"
    assert compiler._build_having_leaf_clause(
        {"table": "orders", "column": "amount", "operator": "IN", "valueType": "expression", "rightExpression": "select amount from t", "aggFunc": "SUM"},
        is_multi_table=False,
        main_table="orders",
        aggregations=[],
        agg_by_id={},
    ) == "SUM(`amount`) IN (select amount from t)"


def test_compile_query_plan_sql_additional_branch_edges():
    sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "joins": [
            {"rightTable": "items", "rightAlias": "i", "leftAlias": "o", "leftColumn": "order_id", "rightColumn": "order_id"},
            {"rightTable": "users", "rightAlias": "u", "leftAlias": "o", "leftColumn": "user_id", "rightColumn": "id"},
        ],
        "dimensions": [
            {"id": "d1", "table": "orders", "tableAlias": "o", "column": "created_at", "alias": "月份", "transform": "month"},
        ],
        "aggregations": [
            {"id": "a1", "func": "SUM", "table": "orders", "tableAlias": "o", "column": "amount", "alias": "销售额"},
        ],
        "filters": [
            {"table": "orders", "tableAlias": "o", "column": "status", "operator": "=", "value": "paid"},
            {"table": "orders", "tableAlias": "o", "column": "created_at", "operator": "<=", "value": "current_date()", "value_type": "sql_function", "logic": "AND"},
        ],
        "orderBy": [
            {"table": "orders", "tableAlias": "o", "column": "created_at", "direction": "ASC"},
        ],
        "limit": 10,
        "offset": 0,
    })

    assert "LEFT JOIN items i ON o.order_id = i.order_id" in sql
    assert "DATE_FORMAT(o.`created_at`, '%Y-%m') AS `月份_month`" in sql
    assert "WHERE o.`status` = 'paid' AND o.`created_at` <= current_date()" in sql
    assert "ORDER BY `月份_month` ASC" in sql
    assert "LIMIT 10" in sql

    with pytest.raises(ValueError, match="compare"):
        compile_query_plan_sql({
            "mainTable": "orders",
            "dimensions": [{"table": "orders", "column": "status"}],
            "compare": {"type": "yoy"},
        })

    with pytest.raises(ValueError, match="OFFSET"):
        compile_query_plan_sql({
            "mainTable": "orders",
            "dimensions": [{"table": "orders", "column": "status"}],
            "offset": 1,
        })

    with pytest.raises(ValueError, match="OFFSET"):
        compile_query_plan_sql({
            "mainTable": "orders",
            "dimensions": [{"table": "orders", "column": "status"}],
            "limit": 10,
            "offset": "bad",
        })


def test_compile_query_plan_sql_order_by_fallbacks_and_rejections():
    detail_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [],
        "aggregations": [{"id": "a1", "func": "RAW", "table": "orders", "tableAlias": "o", "column": "amount", "alias": "金额"}],
        "orderBy": [{"table": "orders", "tableAlias": "o", "column": "missing", "direction": "DESC"}],
    })
    assert "ORDER BY o.missing DESC" in detail_sql

    aggregate_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "tableAlias": "o", "column": "amount", "alias": "销售额"}],
        "orderBy": [{"table": "orders", "tableAlias": "o", "column": "amount", "direction": "DESC"}],
    })
    assert "ORDER BY SUM(`amount`) DESC" in aggregate_sql

    with pytest.raises(ValueError, match="聚合查询不能按未分组"):
        compile_query_plan_sql({
            "mainTable": "orders",
            "mainAlias": "o",
            "dimensions": [{"table": "orders", "tableAlias": "o", "column": "status", "alias": "状态"}],
            "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "tableAlias": "o", "column": "amount", "alias": "销售额"}],
            "orderBy": [{"table": "orders", "tableAlias": "o", "column": "unknown", "direction": "DESC"}],
        })


def test_compile_query_plan_sql_unselected_transformed_dimension_order_fallbacks():
    group_expr_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [{"table": "orders", "tableAlias": "o", "column": "created_at", "alias": "下单月", "transform": "month"}],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "tableAlias": "o", "column": "amount", "alias": "销售额"}],
        "orderBy": [{"table": "orders", "tableAlias": "o", "column": "created_at", "direction": "DESC"}],
    })
    assert "ORDER BY `下单月_month` DESC" in group_expr_sql

    dimension_expr_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [{"table": "orders", "tableAlias": "o", "column": "created_at", "alias": "下单月", "transform": "month"}],
        "aggregations": [],
        "orderBy": [{"table": "orders", "tableAlias": "o", "column": "created_at", "direction": "ASC"}],
    })
    assert "ORDER BY `下单月_month` ASC" in dimension_expr_sql


def test_compile_query_plan_sql_having_and_order_expression_remaining_branches():
    having_tree_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [{"table": "orders", "tableAlias": "o", "column": "status", "alias": "状态"}],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "tableAlias": "o", "column": "amount", "alias": "销售额"}],
        "filters": [
            {
                "and": [
                    {
                        "table": "orders",
                        "tableAlias": "o",
                        "column": "amount",
                        "aggFunc": "SUM",
                        "operator": "IN",
                        "valueType": "expression",
                        "rightExpression": "select amount from threshold",
                    }
                ]
            }
        ],
    })
    assert "HAVING SUM(`amount`) IN (select amount from threshold)" in having_tree_sql

    order_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [
            {"table": "orders", "tableAlias": "o", "column": "status", "alias": "状态", "expression": "UPPER(o.status)"},
        ],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "tableAlias": "o", "column": "amount", "alias": "销售额"}],
        "orderBy": [{"table": "orders", "tableAlias": "o", "column": "status", "direction": "ASC"}],
    })
    assert "ORDER BY `状态` ASC" in order_sql

    dimension_expr_order_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [
            {"table": "orders", "tableAlias": "o", "column": "status", "alias": "状态", "expression": "UPPER(o.status)"},
            {"table": "orders", "tableAlias": "o", "column": "amount", "alias": "金额"},
        ],
        "aggregations": [],
        "orderBy": [{"table": "orders", "tableAlias": "o", "column": "status", "direction": "DESC"}],
    })
    assert "ORDER BY `状态` DESC" in dimension_expr_order_sql


def test_compile_query_plan_sql_remaining_branch_edges():
    where_expression_in_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [{"table": "orders", "tableAlias": "o", "column": "status", "alias": "状态"}],
        "filters": [
            {
                "table": "orders",
                "tableAlias": "o",
                "column": "status",
                "operator": "IN",
                "valueType": "expression",
                "rightExpression": "select status from valid_status",
            }
        ],
    })
    assert "`status` IN (select status from valid_status)" in where_expression_in_sql

    having_expression_in_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [],
        "aggregations": [{"id": "a1", "func": "RAW", "table": "orders", "tableAlias": "o", "column": "score", "alias": "分数"}],
        "filters": [
            {
                "table": "orders",
                "tableAlias": "o",
                "column": "score",
                "operator": "IN",
                "aggFunc": "RAW",
                "valueType": "expression",
                "rightExpression": "select score from whitelist",
            }
        ],
    })
    assert "HAVING `score` IN (select score from whitelist)" in having_expression_in_sql

    order_by_group_expr_sql = compile_query_plan_sql({
        "mainTable": "orders",
        "mainAlias": "o",
        "dimensions": [
            {"table": "orders", "tableAlias": "o", "column": "created_at", "alias": "下单月", "transform": "month"},
            {"table": "orders", "tableAlias": "o", "column": "status", "alias": "状态"},
        ],
        "aggregations": [{"id": "a1", "func": "SUM", "table": "orders", "tableAlias": "o", "column": "amount", "alias": "销售额"}],
        "orderBy": [{"table": "orders", "tableAlias": "o", "column": "created_at", "direction": "DESC"}],
    })
    assert "ORDER BY `下单月_month` DESC" in order_by_group_expr_sql
