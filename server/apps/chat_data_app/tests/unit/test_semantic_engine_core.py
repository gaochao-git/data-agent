import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.semantic.engine import (
    DialectRegistry,
    FilterBuilder,
    FilterValidationError,
    HiveDialect,
    JoinBuilder,
    MySQLDialect,
    PostgreSQLDialect,
    SQLDialect,
    build_metric_filter,
    get_dialect,
)
from apps.chat_data_app.semantic.models import SemanticConfig, SemanticDimension, SemanticMetric, SemanticRelationship
from apps.chat_data_app.semantic.utils import (
    JSONParseError,
    TreeDepthExceededError,
    TreeVisitor,
    flatten_filters,
    format_error_message,
    format_params_for_display,
    format_sql_for_display,
    is_leaf_node,
    is_tree_node,
    parse_llm_json,
    safe_get,
    safe_get_dict,
    safe_get_list,
)
from apps.chat_data_app.semantic.validators import InputValidator
from apps.chat_data_app.services.sql_parser import (
    _is_query_sql,
    _parse_sql,
    _split_sql_statements,
    _strip_comments_by_scan,
    extract_physical_tables,
    extract_top_level_limit_count,
    prepare_sql_for_execution,
)
import apps.chat_data_app.services.sql_parser as sql_parser


def _semantic_config() -> SemanticConfig:
    return SemanticConfig.from_dict({
        "id": "cfg_orders",
        "name": "订单分析",
        "metrics": [
            {
                "name": "订单数",
                "expression": "COUNT(DISTINCT orders.order_id)",
                "table": "orders",
                "filter": "orders.status = 'paid'",
            },
            {"name": "销售额", "expression": "SUM(order_items.amount)", "table": "order_items"},
        ],
        "dimensions": [
            {
                "name": "订单状态",
                "column": "status",
                "table": "orders",
                "type": "categorical",
                "allowed_values": ["paid", "cancelled"],
                "mapping": {"已支付": "paid"},
            },
            {"name": "下单时间", "column": "order_time", "table": "orders", "type": "time"},
            {"name": "商品ID", "column": "product_id", "table": "order_items", "type": "categorical"},
        ],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "order_items",
                "left_column": "order_id",
                "right_column": "order_id",
                "join_conditions": [{"left_column": "order_id", "right_column": "order_id"}],
                "join_type": "left",
                "relationship_type": "one_to_many",
            },
            {
                "left_table": "order_items",
                "right_table": "products",
                "left_column": "product_id",
                "right_column": "product_id",
                "join_conditions": [{"left_column": "product_id", "right_column": "product_id"}],
                "join_type": "inner",
                "relationship_type": "many_to_one",
            },
        ],
    })


def test_filter_builder_builds_where_tree_and_collects_tables():
    tables_used = set()
    builder = FilterBuilder(_semantic_config(), get_dialect("mysql"))

    where = builder.build_where(
        [
            {
                "or": [
                    {"dimension": "订单状态", "operator": "=", "value": "已支付"},
                    {"dimension": "下单时间", "operator": ">=", "value": "current_date()", "value_type": "sql_function"},
                ]
            }
        ],
        tables_used,
    )

    assert where == "(orders.status = 'paid' OR orders.order_time >= current_date())"
    assert tables_used == {"orders"}


def test_filter_builder_supports_in_between_null_like_and_literal_value_types():
    builder = FilterBuilder(_semantic_config(), get_dialect("mysql"))

    where = builder.build_where([
        {"dimension": "订单状态", "operator": "IN", "value": ["paid", "cancelled"]},
        {"dimension": "下单时间", "operator": "BETWEEN", "value": ["2026-01-01", "current_date()"], "value_type": ["literal", "sql_function"]},
        {"dimension": "下单时间", "operator": "IS NOT NULL"},
        {"dimension": "下单时间", "operator": "LIKE", "value": "2026%", "value_type": "literal"},
    ])

    assert "orders.status IN ('paid', 'cancelled')" in where
    assert "orders.order_time BETWEEN '2026-01-01' AND current_date()" in where
    assert "orders.order_time IS NOT NULL" in where
    assert "orders.order_time LIKE '2026%'" in where


def test_filter_builder_rejects_invalid_dimension_value_and_between_shape():
    builder = FilterBuilder(_semantic_config(), get_dialect("mysql"))

    with pytest.raises(FilterValidationError, match="不在允许列表"):
        builder.build_where([{"dimension": "订单状态", "operator": "=", "value": "unknown"}])

    with pytest.raises(FilterValidationError, match="BETWEEN 需要两个值"):
        builder.build_where([{"dimension": "下单时间", "operator": "BETWEEN", "value": ["2026-01-01"]}])

    with pytest.raises(FilterValidationError, match="未知的维度"):
        builder.build_where([{"dimension": "不存在维度", "operator": "=", "value": "x"}])


def test_filter_builder_builds_having_and_metric_filter():
    config = _semantic_config()
    builder = FilterBuilder(config, get_dialect("mysql"))

    having = builder.build_having([
        {"metric": "订单数", "operator": ">", "value": 10},
        {"metric": "销售额", "operator": "<=", "value": 999.5},
    ])

    assert "COUNT(DISTINCT orders.order_id) > 10" in having
    assert "SUM(order_items.amount) <= 999.5" in having
    assert build_metric_filter(config.get_metric("订单数")) == "orders.status = 'paid'"
    assert build_metric_filter(SemanticMetric(name="客单价", expression="AVG(amount)", table="orders")) is None


def test_filter_builder_extra_value_and_error_branches():
    builder = FilterBuilder(_semantic_config(), get_dialect("mysql"))
    builder.set_table_aliases({"orders": "o"})

    assert builder.build_where([]) == ""
    assert builder.build_where([{"and": []}]) == ""
    assert builder.build_having([]) == ""
    assert builder.build_having([{"and": []}]) == ""
    assert builder.build_where([{"dimension": "订单状态", "operator": "NOT IN", "value": "paid"}]) == "orders.status NOT IN ('paid')"
    assert builder.build_where([{"dimension": "下单时间", "operator": "<=", "value": "'current_date()'"}]) == "orders.order_time <= current_date()"
    assert builder.build_where([{"dimension": "下单时间", "operator": "<=", "value": "unsafe_func()", "value_type": "sql_function"}]) == "orders.order_time <= 'unsafe_func()'"
    assert builder.build_where([{"dimension": "下单时间", "operator": "=", "value": True}]) == "orders.order_time = TRUE"
    assert builder.build_where([{"dimension": "下单时间", "operator": "=", "value": None}]) == "orders.order_time = NULL"
    assert builder.build_where([{"dimension": "下单时间", "operator": "=", "value": {"raw": "x"}}]) == "orders.order_time = {'raw': 'x'}"
    assert builder._normalize_value_type(["literal", "bad"]) is None
    assert builder._normalize_value_type("bad") is None
    assert builder._normalize_scalar_value_type(["literal", "sql_function"]) == "literal"
    assert builder._normalize_value_type([1]) is None
    assert builder._get_list_item_value_type(["literal"], 3) is None
    assert builder._contains_sql_function_type(["literal", "sql_function"]) is True
    assert builder._is_sql_function(123) is False
    assert builder._unwrap_quoted_function(123) is None
    assert builder._unwrap_quoted_function("x") is None

    with pytest.raises(FilterValidationError, match="缺少 dimension 字段"):
        builder.build_where([{"operator": "=", "value": "paid"}])
    with pytest.raises(FilterValidationError, match="缺少 metric 字段"):
        builder.build_having([{"operator": ">", "value": 1}])
    with pytest.raises(FilterValidationError, match="未知的指标"):
        builder.build_having([{"metric": "未知指标", "operator": ">", "value": 1}])


def test_filter_builder_allowed_value_validation_edge_branches():
    builder = FilterBuilder(_semantic_config(), get_dialect("mysql"))

    assert builder.build_where([
        {"dimension": "订单状态", "operator": "=", "value": "'current_date()'", "value_type": "sql_function"}
    ]) == "orders.status = current_date()"
    assert builder.build_where([
        {"dimension": "订单状态", "operator": "=", "value": "current_date()"}
    ]) == "orders.status = current_date()"
    assert builder.build_where([
        {"dimension": "订单状态", "operator": "=", "value": "'current_date()'"}
    ]) == "orders.status = current_date()"
    assert builder.build_where([
        {"dimension": "订单状态", "operator": "=", "value": "paid", "value_type": "literal"}
    ]) == "orders.status = 'paid'"
    assert builder.build_where([
        {"dimension": "订单状态", "operator": "=", "value": 1}
    ]) == "orders.status = 1"

    with pytest.raises(FilterValidationError, match="不在允许列表"):
        builder.build_where([{"dimension": "订单状态", "operator": "IN", "value": ["paid", "bad"]}])


def test_filter_builder_having_depth_limit_is_converted_to_validation_error():
    builder = FilterBuilder(_semantic_config(), get_dialect("mysql"))
    node = {"metric": "订单数", "operator": ">", "value": 1}
    for _ in range(12):
        node = {"and": [node]}

    with pytest.raises(FilterValidationError, match="嵌套层级过深"):
        builder.build_having([node])


def test_filter_builder_allowed_value_sql_function_validation_branches():
    builder = FilterBuilder(_semantic_config(), get_dialect("mysql"))

    ok = builder.build_where([
        {"dimension": "订单状态", "operator": "IN", "value": ["paid", "current_date()"], "value_type": ["literal", "sql_function"]}
    ])
    assert "orders.status IN ('paid', current_date())" == ok

    with pytest.raises(FilterValidationError, match="不在允许列表"):
        builder.build_where([
            {"dimension": "订单状态", "operator": "IN", "value": ["paid", "unsafe_func()"], "value_type": ["literal", "sql_function"]}
        ])
    with pytest.raises(FilterValidationError, match="不在允许列表"):
        builder.build_where([
            {"dimension": "订单状态", "operator": "=", "value": "unsafe_func()", "value_type": "sql_function"}
        ])


def test_filter_builder_depth_limit_is_converted_to_validation_error():
    builder = FilterBuilder(_semantic_config(), get_dialect("mysql"))
    node = {"dimension": "订单状态", "operator": "=", "value": "paid"}
    for _ in range(12):
        node = {"and": [node]}

    with pytest.raises(FilterValidationError, match="嵌套层级过深"):
        builder.build_where([node])


def test_join_builder_builds_direct_reverse_and_path_joins():
    builder = JoinBuilder(_semantic_config())

    assert builder.can_join("orders", "orders") is True
    assert builder.can_join("orders", "order_items") is True
    assert builder.find_join_path("orders", "orders") == ["orders"]
    assert builder.find_join_path("orders", "products") == ["orders", "order_items", "products"]

    direct_sql = builder.build("orders", {"orders", "order_items"})
    path_sql = builder.build("orders", {"orders", "products"})
    reverse_clause = builder._build_join_clause(
        _semantic_config().find_relationship("orders", "order_items"),
        "order_items",
        "orders",
    )

    assert direct_sql.startswith("FROM orders")
    assert "LEFT JOIN order_items ON orders.order_id = order_items.order_id" in direct_sql
    assert "INNER JOIN products ON order_items.product_id = products.product_id" in path_sql
    assert reverse_clause == "LEFT JOIN orders ON order_items.order_id = orders.order_id"


def test_join_builder_missing_path_returns_from_main_table_only_and_aliases_are_unique():
    config = _semantic_config()
    alias_builder = JoinBuilder(config)

    assert alias_builder.build("orders", {"orders"}, table_aliases={"orders": "ord"}) == "FROM orders"
    assert alias_builder.get_table_alias("orders") == "ord"

    builder = JoinBuilder(config)
    sql = builder.build("orders", {"orders", "unrelated"})
    first_alias = builder.get_table_alias("orders")
    second_alias = builder.get_table_alias("order_items")
    third_alias = builder.get_table_alias("products")

    assert sql == "FROM orders"
    assert first_alias == "o"
    assert second_alias == "o1"
    assert third_alias == "p"
    assert builder.get_all_aliases() == {"orders": "o", "order_items": "o1", "products": "p"}


def test_join_builder_skips_already_joined_table_inside_path(monkeypatch):
    builder = JoinBuilder(_semantic_config())
    monkeypatch.setattr(builder, "find_join_path", lambda _joined, table: ["orders", "orders", table])

    sql = builder.build("orders", {"orders", "order_items"})

    assert "LEFT JOIN order_items ON orders.order_id = order_items.order_id" in sql


def test_join_builder_condition_relationship_and_alias_collision_branches():
    builder = JoinBuilder(_semantic_config())
    relationship = SemanticRelationship(
        left_table="orders",
        right_table="payments",
        left_column="order_id",
        right_column="order_id",
        join_conditions=[{"left_column": "order_id"}],
        condition="orders.order_id = payments.order_id AND payments.deleted = 0",
        join_type="inner",
    )

    assert builder._build_join_clause(relationship, "orders", "payments") == (
        "INNER JOIN payments ON orders.order_id = payments.order_id AND payments.deleted = 0"
    )

    partial_conditions_rel = SemanticRelationship(
        left_table="orders",
        right_table="order_logs",
        left_column="order_id",
        right_column="order_id",
        join_conditions=[
            {"left_column": "missing_right"},
            {"left_column": "order_id", "right_column": "order_id"},
        ],
    )
    assert builder._build_join_clause(partial_conditions_rel, "orders", "order_logs") == (
        "LEFT JOIN order_logs ON orders.order_id = order_logs.order_id"
    )

    builder._table_aliases = {"orders": "o", "old_orders": "o1"}
    assert builder.get_table_alias("order_items") == "o2"


def test_join_builder_rejects_relationship_without_join_conditions():
    config = SemanticConfig.from_dict({
        "id": "cfg_bad_rel",
        "name": "坏关系",
        "metrics": [{"name": "订单数", "expression": "COUNT(*)", "table": "orders"}],
        "dimensions": [],
        "relationships": [
            {
                "left_table": "orders",
                "right_table": "users",
                "left_column": "user_id",
                "right_column": "user_id",
                "join_conditions": [],
            }
        ],
    })

    with pytest.raises(ValueError, match="缺少 join_conditions"):
        JoinBuilder(config).build("orders", {"orders", "users"})


def test_tree_visitor_helpers_and_depth_errors():
    visitor = TreeVisitor(max_depth=2)
    tree = {
        "and": [
            {"dimension": "状态", "operator": "=", "value": "paid"},
            {"or": [
                {"dimension": "城市", "operator": "=", "value": "杭州"},
                {"dimension": "城市", "operator": "=", "value": "宁波"},
            ]},
        ]
    }

    rendered = visitor.visit(tree, lambda node: f"{node['dimension']}{node['operator']}{node['value']}")
    assert rendered == "(状态=paid AND (城市=杭州 OR 城市=宁波))"
    assert is_tree_node(tree) is True
    assert is_leaf_node(tree["and"][0]) is True
    assert flatten_filters([tree]) == [
        {"dimension": "状态", "operator": "=", "value": "paid"},
        {"dimension": "城市", "operator": "=", "value": "杭州"},
        {"dimension": "城市", "operator": "=", "value": "宁波"},
    ]

    with pytest.raises(ValueError, match="必须是列表"):
        visitor.visit({"and": {"dimension": "状态"}}, lambda node: "")
    with pytest.raises(TreeDepthExceededError):
        visitor.visit({"and": [{"and": [{"and": [{"dimension": "状态"}]}]}]}, lambda node: "")


def test_tree_visitor_empty_single_context_and_collect_branches():
    visitor = TreeVisitor(max_depth=2)
    context = set()

    assert visitor.visit({"and": []}, lambda _node: "x") == ""
    assert visitor.visit({"or": []}, lambda _node: "x") == ""
    assert visitor.visit({"and": [{"skip": True}]}, lambda _node: "") == ""
    assert visitor.visit({"and": [{"skip": True}, {"dimension": "城市", "value": "杭州"}]}, lambda node: "" if node.get("skip") else node["value"]) == "杭州"
    assert visitor.visit({"or": [{"skip": True}]}, lambda _node: "") == ""
    assert visitor.visit({"or": [{"dimension": "城市", "value": "杭州"}]}, lambda node: node["value"]) == "杭州"
    with pytest.raises(ValueError, match="必须是列表"):
        visitor.visit({"or": {"dimension": "状态"}}, lambda node: "")

    assert visitor.visit_with_context({"and": [{"dimension": "城市"}]}, lambda node, ctx: ctx.add(node["dimension"]) or node["dimension"], context) == "城市"
    assert context == {"城市"}
    assert visitor.visit_with_context({"and": [{"skip": True}]}, lambda _node, _ctx: "") == ""
    assert visitor.visit_with_context(
        {"and": [{"dimension": "城市"}, {"dimension": "状态"}]},
        lambda node, ctx: ctx.add(node["dimension"]) or node["dimension"],
    ) == "(城市 AND 状态)"
    assert visitor.visit_with_context({"or": []}, lambda _node, _ctx: "x") == ""
    assert visitor.visit_with_context({"or": [{"skip": True}]}, lambda _node, _ctx: "") == ""
    assert visitor.visit_with_context(
        {"or": [{"skip": True}, {"dimension": "城市"}]},
        lambda node, _ctx: "" if node.get("skip") else node["dimension"],
    ) == "城市"
    assert visitor.visit_with_context(
        {"or": [{"dimension": "城市"}, {"dimension": "状态"}]},
        lambda node, _ctx: node["dimension"],
    ) == "(城市 OR 状态)"
    assert is_tree_node("bad") is False
    assert is_leaf_node("bad") is False
    assert flatten_filters([{"dimension": "城市"}, {"or": [{"dimension": "状态"}]}]) == [{"dimension": "城市"}, {"dimension": "状态"}]
    with pytest.raises(TreeDepthExceededError):
        visitor.collect_leaves({"and": [{"and": [{"and": [{"dimension": "状态"}]}]}]})


def test_json_parser_handles_llm_formats_and_safe_getters():
    assert parse_llm_json('{"metrics":["订单数"]}') == {"metrics": ["订单数"]}
    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_llm_json("说明：{'a': 1,}") == {"a": 1}
    assert parse_llm_json("不是 json") == {}
    with pytest.raises(JSONParseError):
        parse_llm_json("", strict=True)

    data = {"items": [1], "obj": {"a": 1}, "name": "订单"}
    assert safe_get(data, "name") == "订单"
    assert safe_get("bad", "name", "默认") == "默认"
    assert safe_get_list(data, "items") == [1]
    assert safe_get_list(data, "obj") == []
    assert safe_get_dict(data, "obj") == {"a": 1}
    assert safe_get_dict(data, "items") == {}


def test_json_parser_strict_and_fix_failure_branches():
    assert parse_llm_json("```json\n{bad}\n```") == {}
    assert parse_llm_json("说明 {a: 1, b: 2}") == {"a": 1, "b": 2}
    with pytest.raises(JSONParseError):
        parse_llm_json("not json", strict=True)


def test_formatter_outputs_display_text_and_truncates_sql():
    params = format_params_for_display(
        metrics=["订单数"],
        dimensions=["订单状态"],
        filters=[{"dimension": "订单状态", "operator": "=", "value": "paid"}],
        having=[{"metric": "订单数", "operator": ">", "value": 10}],
        order_by=[{"field": "订单数", "direction": "DESC"}],
        limit=20,
        offset=5,
    )

    assert "**指标**: **订单数**" in params
    assert "**数据筛选**: 订单状态 = paid" in params
    assert "**排序**: 订单数 降序" in params
    assert "**跳过条数**: 5" in params
    assert format_params_for_display([], [], [], []) == "无参数"
    assert format_sql_for_display("select " + "x" * 20, max_length=10) == "select xxx..."
    assert format_sql_for_display("select 1", max_length=100) == "select 1"
    assert format_error_message("字段不存在", "指标集") == "错误：字段不存在\n上下文：指标集"
    assert format_error_message("字段不存在") == "错误：字段不存在"


def test_sql_dialects_cover_common_functions_and_registry_errors():
    mysql = get_dialect("mysql")
    hive = get_dialect("hive")
    pg = get_dialect("postgresql")

    assert mysql.quote_string("a'b") == "'a''b'"
    assert mysql.current_date() == "CURRENT_DATE()"
    assert mysql.current_timestamp() == "NOW()"
    assert mysql.date_add("dt", 1, "day") == "DATE_ADD(dt, INTERVAL 1 DAY)"
    assert mysql.date_sub("dt", 2, "month") == "DATE_SUB(dt, INTERVAL 2 MONTH)"
    assert mysql.date_diff("start_dt", "end_dt") == "DATEDIFF(end_dt, start_dt)"
    assert mysql.concat("a", "b") == "CONCAT(a, b)"
    assert mysql.substring("name", 1, 3) == "SUBSTRING(name, 1, 3)"
    assert mysql.count_distinct("user_id") == "COUNT(DISTINCT user_id)"
    assert mysql.limit_offset(10) == "LIMIT 10"
    assert mysql.limit_offset(10, 20) == "LIMIT 20, 10"
    assert mysql.coalesce("a", "b") == "COALESCE(a, b)"
    assert mysql.ifnull("a", "0") == "IFNULL(a, 0)"

    assert hive.quote_string("a'b") == "'a\\'b'"
    assert hive.date_add("dt", 1, "day") == "DATE_ADD(dt, 1)"
    assert hive.date_add("dt", 1, "month") == "(dt + INTERVAL 1 MONTH)"
    assert hive.date_sub("dt", 1, "day") == "DATE_SUB(dt, 1)"
    assert hive.date_sub("dt", 1, "year") == "(dt - INTERVAL 1 YEAR)"
    assert hive.substring("name", 1, 3) == "SUBSTR(name, 1, 3)"
    assert hive.ifnull("a", "0") == "NVL(a, 0)"

    assert pg.quote_identifier("order") == '"order"'
    assert pg.format_date("dt", "week") == "TO_CHAR(dt, 'IYYY-IW')"
    assert pg.format_date("dt", "unknown") == "dt"
    assert pg.date_add("dt", 1, "day") == "(dt + INTERVAL '1 day')"
    assert pg.date_sub("dt", 1, "day") == "(dt - INTERVAL '1 day')"
    assert pg.date_diff("start_dt", "end_dt") == "(end_dt::date - start_dt::date)"
    assert pg.concat("a", "b", "c") == "a || b || c"
    assert pg.limit_offset(10, 20) == "LIMIT 10 OFFSET 20"
    assert pg.ifnull("a", "0") == "COALESCE(a, 0)"
    assert DialectRegistry.has("mysql") is True
    assert "hive" in DialectRegistry.list()

    custom_pg = PostgreSQLDialect()
    DialectRegistry.register(custom_pg)
    assert DialectRegistry.get("postgresql") is custom_pg
    with pytest.raises(ValueError, match="未知的 SQL 方言"):
        DialectRegistry.get("oracle")


def test_sql_dialects_cover_all_builtin_function_variants():
    mysql = MySQLDialect()
    hive = HiveDialect()
    pg = PostgreSQLDialect()

    assert mysql.format_date("dt", "year") == "DATE_FORMAT(dt, '%Y')"
    assert mysql.format_date("dt", "quarter") == "CONCAT(YEAR(dt), '-Q', QUARTER(dt))"
    assert mysql.format_date("dt", "week") == "YEARWEEK(dt)"
    assert mysql.format_date("dt", "day") == "DATE(dt)"
    assert mysql.format_date("dt", "hour") == "DATE_FORMAT(dt, '%Y-%m-%d %H:00')"
    assert mysql.format_date("dt", "minute") == "DATE_FORMAT(dt, '%Y-%m-%d %H:%i')"
    assert mysql.format_date("dt", "unknown") == "dt"

    assert hive.quote_identifier("order") == "`order`"
    assert hive.current_date() == "CURRENT_DATE()"
    assert hive.current_timestamp() == "CURRENT_TIMESTAMP()"
    assert hive.format_date("dt", "year") == "DATE_FORMAT(dt, 'yyyy')"
    assert hive.format_date("dt", "quarter") == "CONCAT(YEAR(dt), '-Q', QUARTER(dt))"
    assert hive.format_date("dt", "day") == "TO_DATE(dt)"
    assert hive.format_date("dt", "hour") == "DATE_FORMAT(dt, 'yyyy-MM-dd HH:00')"
    assert hive.format_date("dt", "minute") == "DATE_FORMAT(dt, 'yyyy-MM-dd HH:mm')"
    assert hive.format_date("dt", "unknown") == "dt"
    assert hive.date_diff("start_dt", "end_dt") == "DATEDIFF(end_dt, start_dt)"
    assert hive.concat("a", "b") == "CONCAT(a, b)"
    assert hive.count_distinct("user_id") == "COUNT(DISTINCT user_id)"
    assert hive.limit_offset(10) == "LIMIT 10"
    assert hive.limit_offset(10, 5) == "LIMIT 5, 10"
    assert hive.coalesce("a", "b") == "COALESCE(a, b)"

    assert pg.quote_string("a'b") == "'a''b'"
    assert pg.current_date() == "CURRENT_DATE"
    assert pg.current_timestamp() == "NOW()"
    assert pg.format_date("dt", "year") == "TO_CHAR(dt, 'YYYY')"
    assert pg.format_date("dt", "quarter") == "TO_CHAR(dt, 'YYYY-\"Q\"Q')"
    assert pg.format_date("dt", "month") == "TO_CHAR(dt, 'YYYY-MM')"
    assert pg.format_date("dt", "day") == "DATE(dt)"
    assert pg.format_date("dt", "hour") == "TO_CHAR(dt, 'YYYY-MM-DD HH24:00')"
    assert pg.format_date("dt", "minute") == "TO_CHAR(dt, 'YYYY-MM-DD HH24:MI')"
    assert pg.substring("name", 1, 3) == "SUBSTRING(name FROM 1 FOR 3)"
    assert pg.count_distinct("user_id") == "COUNT(DISTINCT user_id)"
    assert pg.limit_offset(10) == "LIMIT 10"
    assert pg.coalesce("a", "b") == "COALESCE(a, b)"

    assert SQLDialect.quote_identifier(object(), "x") is None
    assert SQLDialect.quote_string(object(), "x") is None
    assert SQLDialect.format_date(object(), "dt", "day") is None
    assert SQLDialect.current_date(object()) is None
    assert SQLDialect.current_timestamp(object()) is None
    assert SQLDialect.date_add(object(), "dt", 1, "day") is None
    assert SQLDialect.date_sub(object(), "dt", 1, "day") is None
    assert SQLDialect.date_diff(object(), "a", "b") is None
    assert SQLDialect.concat(object(), "a", "b") is None
    assert SQLDialect.substring(object(), "name", 1, 2) is None
    assert SQLDialect.count_distinct(object(), "id") is None
    assert SQLDialect.limit_offset(object(), 10, 0) is None
    assert SQLDialect.coalesce(object(), "a", "b") is None
    assert SQLDialect.ifnull(object(), "a", "0") is None


def test_semantic_models_relationship_and_indexes():
    config = _semantic_config()
    rel = config.find_relationship("orders", "order_items")

    assert config.get_tables() == {"orders", "order_items"}
    assert config.find_relationship("orders", "missing") is None
    assert config.get_metric("订单数").get_alias() == "订单数"
    assert config.get_metric("订单数").has_filter() is True
    assert config.get_dimension("订单状态").validate_value("已支付") is True
    assert config.get_dimension("订单状态").validate_value("unknown") is False
    assert config.get_dimension("订单状态").normalize_value("已支付") == "paid"
    assert config.get_dimension("订单状态").normalize_value("unknown") == "unknown"
    assert config.get_dimension("下单时间").is_time_dimension() is True
    assert config.get_dimension("下单时间").validate_value("任意时间") is True
    assert config.get_dimension("商品ID").get_column_ref("i") == "i.product_id"
    assert SemanticDimension(
        name="金额等级",
        column="amount",
        table="orders",
        type="categorical",
        expression="CASE WHEN amount > 100 THEN '高' ELSE '低' END",
    ).get_column_ref() == "CASE WHEN amount > 100 THEN '高' ELSE '低' END"
    assert config.get_dimension_tables(["订单状态", "商品ID", "不存在维度"]) == {"orders", "order_items"}
    assert config.get_metric_tables(["订单数", "销售额", "不存在指标"]) == {"orders", "order_items"}
    assert config.can_join("orders", "order_items") is True
    assert config.get_relationships_for_table("orders") == [rel]
    assert rel.get_join_condition("o", "i") == "o.order_id = i.order_id"
    assert rel.get_other_table("orders") == "order_items"
    assert rel.get_other_table("order_items") == "orders"
    assert rel.get_other_table("missing") is None
    assert rel.get_join_keyword() == "LEFT JOIN"
    assert config.to_dict()["id"] == "cfg_orders"

    custom_rel = SemanticRelationship(
        left_table="a",
        right_table="b",
        left_column="id",
        right_column="a_id",
        join_conditions=[],
        condition="a.id = b.a_id AND b.deleted = 0",
        join_type="right",
    )
    assert custom_rel.get_join_condition() == "a.id = b.a_id AND b.deleted = 0"
    assert custom_rel.get_join_keyword() == "RIGHT JOIN"
    with pytest.raises(ValueError, match="join_conditions 不能为空"):
        SemanticRelationship(
            left_table="a",
            right_table="b",
            left_column="id",
            right_column="a_id",
            join_conditions=[],
        ).get_join_condition()


def test_input_validator_covers_metrics_dimensions_filters_having_order_limit_offset():
    validator = InputValidator(
        metrics_map={"订单数": {"name": "订单数"}},
        dimensions_map={
            "订单状态": {"name": "订单状态", "allowed_values": ["paid", "cancelled"], "mapping": {"已支付": "paid"}},
            "下单时间": {"name": "下单时间"},
        },
    )

    assert validator.validate_metrics(["订单数"])[1] is None
    assert "指标 '未知指标' 不存在" in validator.validate_metrics(["未知指标"])[1]
    assert "请至少选择一个指标" in validator.validate_metrics([])[1]
    assert validator.validate_dimensions([{"name": "下单时间", "time_grain": "month"}])[1] is None
    assert "time_grain 'bad' 无效" in validator.validate_dimensions([{"name": "下单时间", "time_grain": "bad"}])[1]
    assert "维度 '未知维度' 不存在" in validator.validate_dimensions(["未知维度"])[1]

    filters, err = validator.validate_filters([{"dimension": "订单状态", "operator": "=", "value": "已支付"}])
    assert err is None
    assert filters[0]["value"] == "paid"
    assert "不在允许值范围" in validator.validate_filters([{"dimension": "订单状态", "operator": "=", "value": "unknown"}])[1]
    assert "必须是数组" in validator.validate_filters([{"or": {"dimension": "订单状态"}}])[1]
    assert "必须是数组" in validator.validate_filters([{"and": {"dimension": "订单状态"}}])[1]
    assert validator.validate_filters([{"bad": []}]) == ([], None)
    assert "无效的过滤条件格式" in validator.validate_filters([{"and": [{"bad": []}]}])[1]

    assert validator.validate_having([{"metric": "订单数", "operator": ">", "value": 1}])[1] is None
    assert validator.validate_having([{}]) == ([], None)
    assert "HAVING 条件中的指标" in validator.validate_having([{"metric": "未知指标"}])[1]
    assert validator.validate_order_by([{"field": "订单数", "direction": "asc"}], ["订单数"], [])[0] == [{"field": "订单数", "direction": "ASC"}]
    assert "不是字典" in validator.validate_order_by(["bad"], ["订单数"], [])[1]
    assert "缺少 'field'" in validator.validate_order_by([{"direction": "DESC"}], ["订单数"], [])[1]
    assert "不在当前查询输出字段中" in validator.validate_order_by([{"field": "下单时间"}], ["订单数"], [])[1]
    assert "排序方向" in validator.validate_order_by([{"field": "订单数", "direction": "bad"}], ["订单数"], [])[1]
    assert validator.validate_limit(10) == (10, None)
    assert "LIMIT 必须为正整数" in validator.validate_limit(0)[1]
    assert "LIMIT 不能超过" in validator.validate_limit(101, max_limit=100)[1]
    assert validator.validate_offset(0) == (0, None)
    assert "OFFSET 必须为非负整数" in validator.validate_offset(-1)[1]


def test_input_validator_tree_mapping_and_null_allowed_values():
    validator = InputValidator(
        metrics_map={"订单数": {"name": "订单数"}},
        dimensions_map={
            "订单状态": {"name": "订单状态", "allowed_values": ["paid"], "mapping": {"已支付": "paid"}},
            "下单时间": {"name": "下单时间", "allowed_values": ["today"]},
        },
    )

    filters, err = validator.validate_filters([{"and": [
        {"dimension": "订单状态", "operator": "=", "value": "已支付"},
        {"dimension": "下单时间", "operator": "IS NOT NULL"},
    ]}])

    assert err is None
    assert filters == [{"and": [
        {"dimension": "订单状态", "operator": "=", "value": "paid"},
        {"dimension": "下单时间", "operator": "IS NOT NULL"},
    ]}]
    assert validator._normalize_allowed_value({"mapping": None}, "x") == "x"
    assert validator._normalize_allowed_value({"mapping": []}, "x") == "x"
    assert validator._normalize_allowed_value({"mapping": {"A": "a"}}, None) is None
    assert validator.validate_filters([{"dimension": "未知维度", "operator": "=", "value": "x"}])[1].startswith("错误：过滤条件中的维度")


def test_input_validator_or_tree_validation_and_normalization_branches():
    validator = InputValidator(
        metrics_map={"订单数": {"name": "订单数"}},
        dimensions_map={
            "订单状态": {"name": "订单状态", "allowed_values": ["paid", "cancelled"], "mapping": {"已支付": "paid", "已取消": "cancelled"}},
            "用户等级": {"name": "用户等级"},
        },
    )

    filters, err = validator.validate_filters([{"or": [
        {"dimension": "订单状态", "operator": "=", "value": "已支付"},
        {"dimension": "订单状态", "operator": "IN", "value": ["已取消", "paid"]},
    ]}])

    assert err is None
    assert filters == [{"or": [
        {"dimension": "订单状态", "operator": "=", "value": "paid"},
        {"dimension": "订单状态", "operator": "IN", "value": ["cancelled", "paid"]},
    ]}]
    assert validator.validate_filters([
        {"or": [{"dimension": "未知维度", "operator": "=", "value": "x"}]}
    ])[1].startswith("错误：过滤条件中的维度")
    assert "不在允许值范围" in validator.validate_filters([
        {"or": [{"dimension": "订单状态", "operator": "=", "value": "unknown"}]}
    ])[1]
    assert validator.validate_filters([
        {"dimension": "用户等级", "operator": "=", "value": "VIP"},
        {"dimension": "订单状态", "operator": "=", "value": "已支付"},
    ])[0][-1]["value"] == "paid"


def test_sql_parser_strips_comments_splits_statements_and_detects_query_sql():
    cleaned = _strip_comments_by_scan("select '-- keep', `#col` from t # drop\nwhere note = 'a; b'; -- tail")
    statements = _split_sql_statements(cleaned)

    assert "'-- keep'" in cleaned
    assert "`#col`" in cleaned
    assert "# drop" not in cleaned
    assert statements == ["select '-- keep', `#col` from t \nwhere note = 'a; b'"]
    assert _is_query_sql("( select 1") is True
    assert _is_query_sql("with t as (select 1) select * from t") is True
    assert _is_query_sql("show tables") is False


def test_sql_parser_comment_and_statement_escape_branches():
    assert _strip_comments_by_scan("select 'a\\'--keep' as v -- drop") == "select 'a\\'--keep' as v "
    assert _strip_comments_by_scan('select "a;#keep" as v # drop') == 'select "a;#keep" as v '
    assert _strip_comments_by_scan("select `a--b` from t /* no end") == "select `a--b` from t  "
    assert _strip_comments_by_scan("select 'abc\\") == "select 'abc\\"
    assert _split_sql_statements("select 'a\\';b'; select 2") == ["select 'a\\';b'", "select 2"]
    assert _split_sql_statements("select 'a'';b'; select 2") == ["select 'a'';b'", "select 2"]
    assert _split_sql_statements(" ; select 1; ;") == ["select 1"]
    assert _split_sql_statements("select 'abc\\") == ["select 'abc\\"]


def test_sql_parser_prepare_handles_empty_invalid_and_commands(monkeypatch):
    assert prepare_sql_for_execution("", "mysql").error == "SQL 为空"
    assert "注释移除后为空" in prepare_sql_for_execution("-- only comment", "mysql").error
    assert prepare_sql_for_execution("desc orders; -- comment", "mysql").sql == "desc orders"

    def fake_parse_sql(*_args, **_kwargs):
        raise RuntimeError("parse boom")

    monkeypatch.setattr("apps.chat_data_app.services.sql_parser._parse_sql", fake_parse_sql)
    result = prepare_sql_for_execution("select * from", "mysql")
    assert result.success is False
    assert result.error == "parse boom"


def test_sql_parser_parse_empty_expression_branches(monkeypatch):
    class EmptyExpression:
        def sql(self, dialect=None, comments=False):
            return ""

    class BadLiteral:
        is_string = False
        this = "bad"

    class BadLimit:
        expression = BadLiteral()

    class BadLimitExpression:
        args = {"limit": BadLimit()}

    monkeypatch.setattr("apps.chat_data_app.services.sql_parser._parse_sql", lambda *_args, **_kwargs: [])
    assert prepare_sql_for_execution("select 1", "mysql").error == "SQL 解析失败"
    assert extract_top_level_limit_count("select 1", "mysql") is None

    monkeypatch.setattr("apps.chat_data_app.services.sql_parser._parse_sql", lambda *_args, **_kwargs: [EmptyExpression()])
    assert "注释移除后为空" in prepare_sql_for_execution("select 1", "mysql").error

    monkeypatch.setattr("apps.chat_data_app.services.sql_parser._parse_sql", lambda *_args, **_kwargs: [BadLimitExpression()])
    assert extract_top_level_limit_count("select * from orders limit bad", "mysql") is None


def test_sql_parser_parse_filters_none_and_empty_sql_expressions(monkeypatch):
    class EmptyExpression:
        def sql(self, dialect=None, comments=False):
            return ""

    class ValidExpression:
        def sql(self, dialect=None, comments=False):
            return "SELECT 1"

    monkeypatch.setattr("sqlglot.parse", lambda *_args, **_kwargs: [None, EmptyExpression(), ValidExpression()])

    parsed = _parse_sql("select 1", "mysql")

    assert len(parsed) == 1
    assert parsed[0].sql() == "SELECT 1"


def test_sql_parser_prepare_and_limit_error_branches(monkeypatch):
    class ValidExpression:
        def __init__(self, text):
            self.text = text

        def sql(self, dialect=None, comments=False):
            return self.text

    class StringLimitExpression:
        args = {}

    class BadNumberLimitExpression:
        args = {}

    from sqlglot import exp

    StringLimitExpression.args = {"limit": exp.Limit(expression=exp.Literal.string("5"))}
    BadNumberLimitExpression.args = {"limit": exp.Limit(expression=exp.Literal(this="bad", is_string=False))}

    monkeypatch.setattr(sql_parser, "_split_sql_statements", lambda _sql: [])
    assert "注释移除后为空" in prepare_sql_for_execution("select 1", "mysql").error

    monkeypatch.setattr(sql_parser, "_split_sql_statements", _split_sql_statements)
    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: [ValidExpression("SELECT 1"), ValidExpression("SELECT 2")])
    assert "一次只能执行一条 SQL" in prepare_sql_for_execution("select 1", "mysql").error

    assert extract_top_level_limit_count("", "mysql") is None
    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse boom")))
    assert extract_top_level_limit_count("select 1", "mysql") is None
    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: [StringLimitExpression()])
    assert extract_top_level_limit_count("select * from orders limit '5'", "mysql") is None
    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: [BadNumberLimitExpression()])
    assert extract_top_level_limit_count("select * from orders limit bad", "mysql") is None


def test_sql_parser_extracts_limit_and_physical_tables():
    assert extract_top_level_limit_count("select * from orders limit 5 offset 10", "mysql") == 5
    assert extract_top_level_limit_count("select * from orders limit 'x'", "mysql") is None
    assert extract_top_level_limit_count("bad sql", "mysql") is None

    result = extract_physical_tables(
        """
        WITH recent_orders AS (
            SELECT * FROM orders WHERE order_time >= '2026-01-01'
        )
        SELECT r.order_id, u.user_id
        FROM recent_orders r
        JOIN users u ON r.user_id = u.user_id
        """,
        "mysql",
    )

    assert result.success is True
    assert result.tables == {"orders", "users"}
    assert extract_physical_tables("", "mysql").error == "SQL 为空"
    bad_result = extract_physical_tables("select * from", "mysql")
    assert bad_result.success is False
    assert bad_result.error


def test_sql_parser_extract_physical_tables_skips_blank_table_names(monkeypatch):
    from sqlglot import exp

    class BlankTable:
        name = ""

    class PhysicalTable:
        name = "orders"

    class FakeExpression:
        def find_all(self, expression_type):
            if expression_type is exp.CTE:
                return []
            if expression_type is exp.Table:
                return [BlankTable(), PhysicalTable()]
            return []

    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: [FakeExpression()])

    result = extract_physical_tables("select * from orders", "mysql")

    assert result.success is True
    assert result.tables == {"orders"}
