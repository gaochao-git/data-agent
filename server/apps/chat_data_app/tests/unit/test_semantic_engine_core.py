import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

import apps.chat_data_app.services.sql_run.parser as sql_parser
from apps.chat_data_app.services.semantic.core.formatter import (
    format_error_message,
    format_params_for_display,
    format_sql_for_display,
)
from apps.chat_data_app.services.semantic.core.input_validator import InputValidator
from apps.chat_data_app.services.semantic.core.semantic_sql.dialect import (
    DialectRegistry,
    HiveDialect,
    MySQLDialect,
    SQLDialect,
    get_dialect,
)
from apps.chat_data_app.services.semantic.run.semantic_model_repository import load_semantic_model
from apps.chat_data_app.services.semantic.run.semantic_query_service import (
    SemanticCompileOptions,
    compile_semantic_ai_dsl,
)
from apps.chat_data_app.services.sql_run.parser import (
    _is_query_sql,
    _parse_sql,
    _split_sql_statements,
    _strip_comments_by_scan,
    extract_physical_tables,
    extract_top_level_limit_count,
    prepare_sql_for_execution,
)


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

    assert hive.date_add("dt", 1, "day") == "DATE_ADD(dt, 1)"
    assert hive.date_add("dt", 1, "month") == "(dt + INTERVAL 1 MONTH)"
    assert hive.date_sub("dt", 1, "day") == "DATE_SUB(dt, 1)"
    assert hive.date_sub("dt", 1, "year") == "(dt - INTERVAL 1 YEAR)"
    assert hive.substring("name", 1, 3) == "SUBSTR(name, 1, 3)"
    assert hive.ifnull("a", "0") == "NVL(a, 0)"

    assert DialectRegistry.has("mysql") is True
    assert "hive" in DialectRegistry.list()

    class CustomDialect(MySQLDialect):
        name = "custom"

    custom = CustomDialect()
    DialectRegistry.register(custom)
    assert DialectRegistry.get("custom") is custom
    for unsupported in ("oracle", "postgresql"):
        with pytest.raises(ValueError, match="未知的 SQL 方言"):
            DialectRegistry.get(unsupported)


def test_sql_dialects_cover_all_builtin_function_variants():
    mysql = MySQLDialect()
    hive = HiveDialect()

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

    assert SQLDialect.quote_identifier(object(), "x") is None
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


def test_input_validator_covers_metrics_dimensions_filters_having_order_limit_offset():
    validator = InputValidator(
        metrics_map={"订单数": {"name": "订单数"}, "出金总额": {"name": "出金总额"}, "入金总额": {"name": "入金总额"}},
        dimensions_map={
            "订单状态": {
                "name": "订单状态",
                "logical_type": "enum",
                "allowed_values": ["已支付", "已取消"],
                "mapping": {"已支付": "paid", "已取消": "cancelled"},
            },
            "下单时间": {"name": "下单时间"},
            "出金金额": {"name": "出金金额"},
            "入金金额": {"name": "入金金额"},
            "受限状态": {"name": "受限状态", "allowed_operators": ["=", "IN"]},
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
    assert (
        "不在允许值范围"
        in validator.validate_filters([{"dimension": "订单状态", "operator": "=", "value": "unknown"}])[1]
    )
    assert (
        validator.validate_filters(
            [
                {
                    "dimension": "下单时间",
                    "operator": "BETWEEN",
                    "value": ["2026-01-01", "current_date()"],
                    "value_type": ["literal", "sql_function"],
                }
            ]
        )[1]
        is None
    )
    assert (
        "value_type 仅支持"
        in validator.validate_filters(
            [{"dimension": "下单时间", "operator": "=", "value": "today", "value_type": "expression"}]
        )[1]
    )
    assert (
        "value_type 仅支持"
        in validator.validate_filters(
            [{"dimension": "下单时间", "operator": "IN", "value": ["today"], "value_type": []}]
        )[1]
    )
    assert validator.validate_filters([{"dimension": "订单状态", "operator": "LIKE", "value": "pa"}])[1] is None
    assert (
        "操作符 'DROP TABLE'"
        in validator.validate_filters([{"dimension": "订单状态", "operator": "DROP TABLE", "value": "paid"}])[1]
    )
    assert validator.validate_filters([{"dimension": "受限状态", "operator": "in", "value": ["paid"]}])[1] is None
    assert (
        "不允许操作符 'LIKE'"
        in validator.validate_filters([{"dimension": "受限状态", "operator": "LIKE", "value": "paid"}])[1]
    )
    assert (
        "不允许操作符 '>'"
        in validator.validate_filters(
            [
                {
                    "left": {"type": "dimension", "name": "受限状态"},
                    "operator": ">",
                    "right": {"type": "literal", "value": "paid"},
                }
            ]
        )[1]
    )
    structured_filters, structured_err = validator.validate_filters(
        [
            {
                "left": {"type": "dimension", "name": "出金金额"},
                "operator": ">",
                "right": {
                    "type": "binary",
                    "operator": "*",
                    "left": {"type": "dimension", "name": "入金金额"},
                    "right": {"type": "literal", "value": 10},
                },
            }
        ]
    )
    assert structured_err is None
    assert structured_filters[0]["right"]["operator"] == "*"
    assert (
        "除以 0"
        in validator.validate_filters(
            [
                {
                    "left": {"type": "dimension", "name": "出金金额"},
                    "operator": ">",
                    "right": {
                        "type": "binary",
                        "operator": "/",
                        "left": {"type": "dimension", "name": "入金金额"},
                        "right": {"type": "literal", "value": 0},
                    },
                }
            ]
        )[1]
    )
    assert "必须是数组" in validator.validate_filters([{"or": {"dimension": "订单状态"}}])[1]
    assert "必须是数组" in validator.validate_filters([{"and": {"dimension": "订单状态"}}])[1]
    assert "无效的过滤条件格式" in validator.validate_filters([{"bad": []}])[1]
    assert "无效的过滤条件格式" in validator.validate_filters([{"and": [{"bad": []}]}])[1]

    assert validator.validate_having([{"metric": "订单数", "operator": ">", "value": 1}])[1] is None
    structured_having, structured_having_err = validator.validate_having(
        [
            {
                "left": {"type": "metric", "name": "出金总额"},
                "operator": ">",
                "right": {
                    "type": "binary",
                    "operator": "*",
                    "left": {"type": "metric", "name": "入金总额"},
                    "right": {"type": "literal", "value": 10},
                },
            }
        ]
    )
    assert structured_having_err is None
    assert structured_having[0]["right"]["operator"] == "*"
    assert validator.validate_having([{}]) == ([], None)
    assert "HAVING 条件中的指标" in validator.validate_having([{"metric": "未知指标"}])[1]
    assert (
        "HAVING 条件中的指标"
        in validator.validate_having(
            [
                {
                    "left": {"type": "metric", "name": "未知指标"},
                    "operator": ">",
                    "right": {"type": "literal", "value": 1},
                }
            ]
        )[1]
    )
    assert (
        "不允许除以 0"
        in validator.validate_having(
            [
                {
                    "left": {"type": "metric", "name": "出金总额"},
                    "operator": ">",
                    "right": {
                        "type": "binary",
                        "operator": "/",
                        "left": {"type": "metric", "name": "入金总额"},
                        "right": {"type": "literal", "value": 0},
                    },
                }
            ]
        )[1]
    )
    assert (
        "只支持 metric、literal、binary"
        in validator.validate_having(
            [
                {
                    "left": {"type": "metric", "name": "出金总额"},
                    "operator": ">",
                    "right": {"type": "dimension", "name": "订单状态"},
                }
            ]
        )[1]
    )
    assert validator.validate_order_by([{"field": "订单数", "direction": "asc"}], ["订单数"], [])[0] == [
        {"field": "订单数", "direction": "ASC"}
    ]
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
            "订单状态": {
                "name": "订单状态",
                "logical_type": "enum",
                "allowed_values": ["已支付"],
                "mapping": {"已支付": "paid"},
            },
            "下单时间": {"name": "下单时间", "allowed_values": ["today"]},
        },
    )

    filters, err = validator.validate_filters(
        [
            {
                "and": [
                    {"dimension": "订单状态", "operator": "=", "value": "已支付"},
                    {"dimension": "下单时间", "operator": "IS NOT NULL"},
                ]
            }
        ]
    )

    assert err is None
    assert filters == [
        {
            "and": [
                {"dimension": "订单状态", "operator": "=", "value": "paid"},
                {"dimension": "下单时间", "operator": "IS NOT NULL"},
            ]
        }
    ]
    assert validator._normalize_allowed_value({"mapping": None}, "x") == "x"
    assert validator._normalize_allowed_value({"mapping": []}, "x") == "x"
    assert validator._normalize_allowed_value({"mapping": {"A": "a"}}, None) is None
    assert validator.validate_filters([{"dimension": "未知维度", "operator": "=", "value": "x"}])[1].startswith(
        "错误：过滤条件中的维度"
    )


def test_input_validator_or_tree_validation_and_normalization_branches():
    validator = InputValidator(
        metrics_map={"订单数": {"name": "订单数"}},
        dimensions_map={
            "订单状态": {
                "name": "订单状态",
                "logical_type": "enum",
                "allowed_values": ["已支付", "已取消"],
                "mapping": {"已支付": "paid", "已取消": "cancelled"},
            },
            "用户等级": {"name": "用户等级"},
        },
    )

    filters, err = validator.validate_filters(
        [
            {
                "or": [
                    {"dimension": "订单状态", "operator": "=", "value": "已支付"},
                    {"dimension": "订单状态", "operator": "IN", "value": ["已取消", "已支付"]},
                ]
            }
        ]
    )

    assert err is None
    assert filters == [
        {
            "or": [
                {"dimension": "订单状态", "operator": "=", "value": "paid"},
                {"dimension": "订单状态", "operator": "IN", "value": ["cancelled", "paid"]},
            ]
        }
    ]
    assert validator.validate_filters([{"or": [{"dimension": "未知维度", "operator": "=", "value": "x"}]}])[
        1
    ].startswith("错误：过滤条件中的维度")
    assert (
        "不在允许值范围"
        in validator.validate_filters([{"or": [{"dimension": "订单状态", "operator": "=", "value": "unknown"}]}])[1]
    )
    assert (
        validator.validate_filters(
            [
                {"dimension": "用户等级", "operator": "=", "value": "VIP"},
                {"dimension": "订单状态", "operator": "=", "value": "已支付"},
            ]
        )[0][-1]["value"]
        == "paid"
    )


def test_input_validator_expression_filter_error_and_normalization_edges():
    validator = InputValidator(
        metrics_map={"订单数": {"name": "订单数"}},
        dimensions_map={
            "订单状态": {
                "name": "订单状态",
                "logical_type": "enum",
                "allowed_values": ["已支付", "已取消"],
                "mapping": {"已支付": "paid", "已取消": "cancelled"},
            },
            "出金金额": {"name": "出金金额"},
            "入金金额": {"name": "入金金额"},
        },
    )

    assert "必须是对象" in validator.validate_filters(["bad"])[1]
    assert (
        "left 只支持 dimension"
        in validator.validate_filters(
            [
                {
                    "left": {"type": "literal", "value": "x"},
                    "operator": "=",
                    "right": {"type": "literal", "value": "paid"},
                }
            ]
        )[1]
    )
    assert (
        "不能混用"
        in validator.validate_filters(
            [
                {
                    "left": {"type": "dimension", "name": "出金金额"},
                    "operator": ">",
                    "right": {"type": "literal", "value": 1},
                    "valueType": "expression",
                    "rightExpression": "1 + 1",
                }
            ]
        )[1]
    )
    free_expression, err = validator.validate_filters(
        [
            {
                "dimension": "出金金额",
                "operator": ">",
                "valueType": "expression",
                "rightExpression": "1 + 1",
            }
        ]
    )
    assert err is None
    assert free_expression[0]["rightExpression"] == "1 + 1"
    assert (
        "必须同时使用"
        in validator.validate_filters([{"dimension": "出金金额", "operator": ">", "rightExpression": "1 + 1"}])[1]
    )
    assert (
        "非空字符串"
        in validator.validate_filters(
            [{"dimension": "出金金额", "operator": ">", "valueType": "expression", "rightExpression": ""}]
        )[1]
    )
    for unsafe_expression in ("1; DROP TABLE orders", "1 -- comment", "DELETE FROM orders"):
        assert (
            "只允许单条只读 SQL 表达式"
            in validator.validate_filters(
                [
                    {
                        "dimension": "出金金额",
                        "operator": ">",
                        "valueType": "expression",
                        "rightExpression": unsafe_expression,
                    }
                ]
            )[1]
        )
    assert (
        "过滤条件中的维度 '未知维度' 不存在"
        in validator.validate_filters(
            [
                {
                    "left": {"type": "dimension", "name": "未知维度"},
                    "operator": "=",
                    "right": {"type": "literal", "value": "paid"},
                }
            ]
        )[1]
    )
    assert (
        "right 必须是对象"
        in validator.validate_filters(
            [{"left": {"type": "dimension", "name": "出金金额"}, "operator": ">", "right": 10}]
        )[1]
    )
    assert (
        "right.value 只支持字符串"
        in validator.validate_filters(
            [
                {
                    "left": {"type": "dimension", "name": "出金金额"},
                    "operator": ">",
                    "right": {"type": "literal", "value": {"raw": 1}},
                }
            ]
        )[1]
    )
    assert (
        "right.left 必须是对象"
        in validator.validate_filters(
            [
                {
                    "left": {"type": "dimension", "name": "出金金额"},
                    "operator": ">",
                    "right": {
                        "type": "binary",
                        "operator": "+",
                        "left": None,
                        "right": {"type": "literal", "value": 1},
                    },
                }
            ]
        )[1]
    )
    assert (
        "right.right.type 只支持"
        in validator.validate_filters(
            [
                {
                    "left": {"type": "dimension", "name": "出金金额"},
                    "operator": ">",
                    "right": {
                        "type": "binary",
                        "operator": "+",
                        "left": {"type": "dimension", "name": "入金金额"},
                        "right": {"type": "unknown", "value": 1},
                    },
                }
            ]
        )[1]
    )

    normalized, err = validator.validate_filters(
        [
            {
                "left": {"type": "dimension", "name": "订单状态"},
                "operator": "=",
                "right": {"type": "literal", "value": "已支付"},
            }
        ]
    )
    assert err is None
    assert normalized[0]["right"]["value"] == "paid"
    none_literal, err = validator.validate_filters(
        [
            {
                "left": {"type": "dimension", "name": "订单状态"},
                "operator": "=",
                "right": {"type": "literal", "value": None},
            }
        ]
    )
    assert err is None
    assert none_literal[0]["right"]["value"] is None
    assert (
        "不在允许值范围"
        in validator.validate_filters(
            [
                {
                    "left": {"type": "dimension", "name": "订单状态"},
                    "operator": "=",
                    "right": {"type": "literal", "value": "unknown"},
                }
            ]
        )[1]
    )
    assert validator._is_zero_literal("not-number") is False


def test_compile_semantic_ai_dsl_is_the_shared_compile_facade():
    class FakeGenerator:
        def __init__(self, config):
            assert config == {"id": "model"}

        def generate_sql(self, **kwargs):
            assert kwargs["metrics"] == ["指标"]
            assert kwargs["limit"] == 25
            assert kwargs["offset"] == 3
            return {"success": True, "sql": " SELECT 1 ", "warnings": ["warning"]}

    result = compile_semantic_ai_dsl(
        {"id": "model"},
        {"metrics": ["指标"]},
        options=SemanticCompileOptions(max_rows=25, offset=3),
        generator_cls=FakeGenerator,
    )

    assert result == {"success": True, "sql": "SELECT 1", "error": None, "warnings": ["warning"]}
    assert compile_semantic_ai_dsl(None, {})["error"] == "语义模型必须是对象"
    assert compile_semantic_ai_dsl({}, [])["error"] == "语义 DSL 必须是对象"


@pytest.mark.asyncio
async def test_semantic_model_repository_keeps_runtime_and_full_modes_distinct():
    class FakeManager:
        async def get_datasource_async(self, datasource_id):
            return {
                "id": datasource_id,
                "source_type": "hive",
                "semantic_configs": [{"id": "full", "enabled": True, "tables": ["stale"]}],
            }

        async def get_runtime_semantic_config_async(self, datasource_id, config_id):
            return {
                "id": config_id,
                "enabled": True,
                "relationships": [{"left_table": "a", "right_table": "b"}],
            }

        async def get_semantic_configs_async(self, datasource_id, user_id=""):
            return [{"id": "full", "enabled": True, "tables": ["stale"]}]

        async def get_cached_schema_async(self, datasource_id):
            return [{"name": "orders"}]

    manager = FakeManager()

    class FakeSemanticService:
        async def get_runtime_config_async(self, datasource_id, config_id, user_id=""):
            return await manager.get_runtime_semantic_config_async(datasource_id, config_id)

        async def get_config_async(self, datasource_id, config_id, user_id=""):
            return (await manager.get_semantic_configs_async(datasource_id, user_id))[0]

    semantic_service = FakeSemanticService()
    runtime = await load_semantic_model(
        "ds", "runtime", mode="runtime", manager=manager, semantic_service=semantic_service
    )
    full = await load_semantic_model("ds", "full", mode="full", manager=manager, semantic_service=semantic_service)

    assert runtime["source_type"] == "hive"
    assert runtime["joins"] == runtime["relationships"]
    assert "tables" not in runtime
    assert full["tables"] == [{"name": "orders"}]


@pytest.mark.asyncio
async def test_semantic_model_repository_validates_mode_and_availability():
    class MissingManager:
        async def get_datasource_async(self, datasource_id):
            return None

    with pytest.raises(ValueError, match="缺少语义模型ID"):
        await load_semantic_model("ds", None, manager=MissingManager())
    with pytest.raises(ValueError, match="不支持的语义模型加载模式"):
        await load_semantic_model("ds", "cfg", mode="bad", manager=MissingManager())
    with pytest.raises(ValueError, match="数据源不存在"):
        await load_semantic_model("ds", "cfg", manager=MissingManager())


def test_input_validator_logical_type_filter_values():
    validator = InputValidator(
        metrics_map={"订单数": {"name": "订单数"}},
        dimensions_map={
            "文本字段": {"name": "文本字段", "logical_type": ""},
            "显式文本字段": {
                "name": "显式文本字段",
                "logical_type": "text",
                "allowed_values": ["A"],
                "mapping": {"A": "a"},
            },
            "数值字段": {"name": "数值字段", "logical_type": "number"},
            "日期字段": {"name": "日期字段", "logical_type": "date"},
            "时间字段": {"name": "时间字段", "logical_type": "datetime"},
            "状态": {
                "name": "状态",
                "logical_type": "enum",
                "allowed_values": ["有效", "无效"],
                "mapping": {"有效": "1", "无效": "0"},
            },
        },
    )

    assert validator.validate_filters([{"dimension": "文本字段", "operator": "=", "value": "100"}])[1] is None
    text_filters, text_err = validator.validate_filters(
        [{"dimension": "显式文本字段", "operator": "=", "value": "100"}]
    )
    assert text_err is None
    assert text_filters[0]["value"] == "100"
    assert validator.validate_filters([{"dimension": "数值字段", "operator": "=", "value": 100}])[1] is None
    assert "必须是数字" in validator.validate_filters([{"dimension": "数值字段", "operator": "=", "value": "100"}])[1]
    assert validator.validate_filters([{"dimension": "日期字段", "operator": "=", "value": "2026-07-10"}])[1] is None
    assert (
        "yyyy-MM-dd 格式"
        in validator.validate_filters([{"dimension": "日期字段", "operator": "=", "value": "20260710"}])[1]
    )
    assert (
        "yyyy-MM-dd 格式"
        in validator.validate_filters([{"dimension": "日期字段", "operator": "=", "value": "2026-02-30"}])[1]
    )
    assert (
        validator.validate_filters([{"dimension": "时间字段", "operator": "=", "value": "2026-07-10 12:30:45"}])[1]
        is None
    )
    assert (
        "yyyy-MM-dd HH:mm:ss 格式"
        in validator.validate_filters([{"dimension": "时间字段", "operator": "=", "value": "2026-07-10"}])[1]
    )

    filters, err = validator.validate_filters([{"dimension": "状态", "operator": "=", "value": "有效"}])
    assert err is None
    assert filters[0]["value"] == "1"
    assert "不在允许值范围" in validator.validate_filters([{"dimension": "状态", "operator": "=", "value": "1"}])[1]


def test_input_validator_enum_logical_type_requires_complete_mapping():
    validator = InputValidator(
        metrics_map={"订单数": {"name": "订单数"}},
        dimensions_map={
            "状态": {
                "name": "状态",
                "logical_type": "enum",
                "allowed_values": ["有效", "无效"],
                "mapping": {"有效": "1"},
            },
        },
    )

    assert "缺少物理值映射" in validator.validate_filters([{"dimension": "状态", "operator": "=", "value": "有效"}])[1]


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

    monkeypatch.setattr("apps.chat_data_app.services.sql_run.parser._parse_sql", fake_parse_sql)
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

    monkeypatch.setattr("apps.chat_data_app.services.sql_run.parser._parse_sql", lambda *_args, **_kwargs: [])
    assert prepare_sql_for_execution("select 1", "mysql").error == "SQL 解析失败"
    assert extract_top_level_limit_count("select 1", "mysql") is None

    monkeypatch.setattr(
        "apps.chat_data_app.services.sql_run.parser._parse_sql", lambda *_args, **_kwargs: [EmptyExpression()]
    )
    assert "注释移除后为空" in prepare_sql_for_execution("select 1", "mysql").error

    monkeypatch.setattr(
        "apps.chat_data_app.services.sql_run.parser._parse_sql", lambda *_args, **_kwargs: [BadLimitExpression()]
    )
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
    monkeypatch.setattr(
        sql_parser, "_parse_sql", lambda *_args, **_kwargs: [ValidExpression("SELECT 1"), ValidExpression("SELECT 2")]
    )
    assert "一次只能执行一条 SQL" in prepare_sql_for_execution("select 1", "mysql").error

    assert extract_top_level_limit_count("", "mysql") is None
    monkeypatch.setattr(
        sql_parser, "_parse_sql", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse boom"))
    )
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
