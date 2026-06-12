import pytest

from apps.chat_data_app.services.builder_sql_compiler import compile_builder_sql
from apps.chat_data_app.services.semantic_sql_generator import SemanticSQLGenerator


def test_compile_builder_sql_detail_query():
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

    sql = compile_builder_sql(builder_config)

    assert "FROM orders" in sql
    assert "GROUP BY" not in sql
    assert "WHERE `status` = 'paid'" in sql
    assert "LIMIT 50" in sql


def test_compile_builder_sql_aggregate_query():
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

    sql = compile_builder_sql(builder_config)

    assert "SUM(`amount`)" in sql
    assert "GROUP BY `status`" in sql
    assert "ORDER BY SUM(`amount`) DESC" in sql


def test_compile_builder_sql_compare_query_hive_dialect():
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

    sql = compile_builder_sql(builder_config, dialect="hive")

    assert "TRUNC(orders.`order_time`, 'MM')" in sql
    assert "DATE_FORMAT(cur.stat_time, 'yyyy-MM')" in sql
    assert "ADD_MONTHS(cur.stat_time, -12)" in sql


def test_compile_builder_sql_regular_query_hive_time_transform():
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

    sql = compile_builder_sql(builder_config, dialect="hive")

    assert "DATE_FORMAT(`order_time`, 'yyyy-MM')" in sql
    assert "GROUP BY DATE_FORMAT(`order_time`, 'yyyy-MM')" in sql
    assert "%Y-%m" not in sql


def test_compile_builder_sql_regular_query_hive_week_transform():
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

    sql = compile_builder_sql(builder_config, dialect="hive")

    assert "CONCAT(CAST(YEAR(`order_time`) AS STRING), '-W', LPAD(CAST(WEEKOFYEAR(`order_time`) AS STRING), 2, '0'))" in sql
    assert "DATE_FORMAT(`order_time`, 'YYYY')" not in sql


def test_compile_builder_sql_compare_query_week_is_rejected():
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

    with pytest.raises(ValueError, match="暂不支持 week"):
        compile_builder_sql(builder_config, dialect="mysql")
    with pytest.raises(ValueError, match="暂不支持 week"):
        compile_builder_sql(builder_config, dialect="hive")


def test_compile_builder_sql_field_ref_filter():
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

    sql = compile_builder_sql(builder_config)
    assert "WHERE orders.`user_id` = order_items.`product_id`" in sql


def test_compile_builder_sql_expression_filter():
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

    sql = compile_builder_sql(builder_config)
    assert "WHERE `create_time` >= DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)" in sql


def test_compile_builder_sql_between_with_mixed_value_type_array():
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

    sql = compile_builder_sql(builder_config)
    assert "BETWEEN '2025-01-01' AND current_date()" in sql


def test_compile_builder_sql_between_with_wrong_literal_keeps_string_literal():
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

    sql = compile_builder_sql(builder_config)
    assert "BETWEEN '2025-01-01' AND 'current_date()'" in sql


def test_compile_builder_sql_between_without_value_type_auto_unquotes_safe_function():
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

    sql = compile_builder_sql(builder_config)
    assert "BETWEEN '2025-01-01' AND current_date()" in sql


def test_compile_builder_sql_aggregate_filter_tree_goes_to_having():
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

    sql = compile_builder_sql(builder_config)
    assert "WHERE" not in sql
    assert "HAVING" in sql
    assert "SUM(`amount`) > 100" in sql


def test_compile_builder_sql_mixed_filter_tree_raise_error():
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
        compile_builder_sql(builder_config)


def test_compile_builder_sql_mixed_raw_and_agg_metrics_raise_error():
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
        compile_builder_sql(builder_config)


def test_semantic_unified_engine_allows_dimension_in_metrics():
    semantic_config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "订单金额", "table": "orders", "column": "amount", "expression": "SUM(orders.amount)"}
        ],
        "dimensions": [
            {"name": "订单编号", "table": "orders", "column": "order_no", "expression": "orders.order_no", "type": "categorical"},
            {"name": "用户ID", "table": "orders", "column": "user_id", "expression": "orders.user_id", "type": "categorical"},
        ],
        "relationships": [],
        "tables": [
            {"table_name": "orders", "columns": [{"name": "order_no"}, {"name": "user_id"}, {"name": "amount"}]}
        ],
    }

    generator = SemanticSQLGenerator(semantic_config)
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


def test_semantic_unified_engine_detail_intent_avoids_aggregate_card_sql():
    semantic_config = {
        "source_type": "mysql",
        "metrics": [
            {"name": "订单数量", "table": "orders", "column": "order_id", "expression": "COUNT(orders.order_id)"}
        ],
        "dimensions": [
            {"name": "订单编号", "table": "orders", "column": "order_id", "expression": "orders.order_id", "type": "categorical"},
            {"name": "用户ID", "table": "orders", "column": "user_id", "expression": "orders.user_id", "type": "categorical"},
        ],
        "relationships": [],
        "tables": [
            {"table_name": "orders", "columns": [{"name": "order_id"}, {"name": "user_id"}]}
        ],
    }

    generator = SemanticSQLGenerator(semantic_config)
    result = generator.generate_sql(
        metrics=["订单数量"],
        dimensions=[],
        filters=[],
        having=[],
        order_by=[],
        limit=100,
        user_query="查看订单明细",
    )
    assert result["success"] is True
    sql = result["sql"]
    assert "COUNT(" not in sql.upper()
    assert "GROUP BY" not in sql.upper()
    assert "order_id AS `订单数量`" in sql
