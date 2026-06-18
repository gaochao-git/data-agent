import asyncio
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services import sql_executor
import apps.chat_data_app.services.sql_parser as sql_parser
from apps.chat_data_app.services.sql_parser import prepare_sql_for_execution, validate_single_sql_statement


def test_default_allowed_sql_types_only_select():
    assert sql_executor.ALLOWED_SQL_TYPES == ["SELECT"]


def test_serialize_value_formats_datetime_without_iso_t_separator():
    assert sql_executor.resolve_query_timeout("mysql", 12) == 12
    assert sql_executor.resolve_query_timeout("mysql", 0) == sql_executor.DEFAULT_MYSQL_QUERY_TIMEOUT_SECONDS
    assert sql_executor.resolve_query_timeout("hive", None) == sql_executor.DEFAULT_HIVE_QUERY_TIMEOUT_SECONDS
    assert sql_executor.resolve_query_timeout("sqlite", None) == sql_executor.DEFAULT_MYSQL_QUERY_TIMEOUT_SECONDS
    assert sql_executor._friendly_sql_error("MySQL", RuntimeError("Query execution was interrupted")) == "已人工取消"
    assert sql_executor._serialize_value(Decimal("12.50")) == 12.5
    assert sql_executor._serialize_value(datetime(2024, 1, 4, 8, 1, 0)) == "2024-01-04 08:01:00"
    assert sql_executor._serialize_value(date(2024, 1, 4)) == "2024-01-04"
    assert sql_executor._serialize_value(b"\xe4\xbd\xa0\xe5\xa5\xbd") == "你好"


def test_http_payload_extractors_and_normalizers():
    assert sql_executor._http_response_summary(["bad"]) == {"payload_type": "list"}
    assert sql_executor._http_response_summary({"code": 20000, "data": {"data": [{"a": 1}], "columns": ["a"], "tables": ["t1"]}}) == {
        "code": 20000,
        "row_count": 1,
        "column_count": 1,
        "table_count": 1,
    }
    assert sql_executor._http_response_summary({"message": "ok", "rows": [[1]], "status": "done", "taskId": "task_1"}) == {
        "msg": "ok",
        "status": "done",
        "task_id": "task_1",
        "row_count": 1,
    }
    assert sql_executor._http_response_summary({"records": [{"a": 1}], "columns": [{"name": "a"}]}) == {
        "row_count": 1,
        "column_count": 1,
    }
    assert sql_executor._extract_remote_data({"data": []}) == {}
    assert sql_executor._extract_remote_data(["bad"]) == {}
    assert sql_executor._extract_task_id({"data": {"task_id": "task_1"}}) == "task_1"
    assert sql_executor._extract_task_id(["bad"]) == ""
    assert sql_executor._extract_response_payload({"code": 20000}) == {"code": 20000}
    assert sql_executor._extract_response_payload(["bad"]) == {}
    assert sql_executor._extract_remote_data({"data": {"status": "running"}}) == {"status": "running"}
    assert sql_executor._extract_task_id({"data": {"job_id": "job_1"}}) == "job_1"
    assert sql_executor._extract_task_id({"id": "id_1"}) == "id_1"
    assert sql_executor._extract_task_id({"task_id": ""}) == ""
    assert sql_executor._extract_remote_code({}) is None
    assert sql_executor._extract_remote_code({"code": "20000"}) == 20000
    assert sql_executor._extract_remote_code({"code": "bad"}) is None
    assert sql_executor._extract_remote_message(["bad"], "默认") == "默认"
    assert sql_executor._extract_remote_message({}, "默认") == "默认"
    assert sql_executor._extract_remote_message({"detail": "无权限"}, "默认") == "无权限"
    assert sql_executor._normalize_remote_task_status("queued") == "pending"
    assert sql_executor._normalize_remote_task_status("finished") == "succeeded"
    assert sql_executor._normalize_remote_task_status("killed") == "cancelled"
    assert sql_executor._normalize_remote_task_status("failed") == "failed"
    assert sql_executor._normalize_remote_task_status("custom") == "custom"
    assert sql_executor._normalize_remote_task_status("") == "pending"
    assert sql_executor._extract_remote_status({"status": "finished"}) == "succeeded"
    assert sql_executor._extract_remote_status({"data": {"status": "processing"}}) == "running"
    assert sql_executor._extract_result_payload({"data": {"query_result": {"columns": ["a"]}}}) == {"columns": ["a"]}
    assert sql_executor._extract_result_payload({"data": {"result": {"columns": ["b"]}}}) == {"columns": ["b"]}
    assert sql_executor._extract_result_payload({"data": "raw text", "result": {"columns": ["root"]}}) == {"columns": ["root"]}
    assert sql_executor._extract_result_payload({"data": "raw text", "status": "running"}) == {"data": "raw text", "status": "running"}
    assert sql_executor._extract_result_payload({"data": []}) == {"data": []}
    assert sql_executor._extract_result_payload({"rows": [[1]]}) == {"rows": [[1]]}
    assert sql_executor._extract_result_payload({"data": {"status": "done"}}) == {"data": {"status": "done"}}
    assert sql_executor._extract_result_payload({"data": [], "meta": "only"}) == {"data": [], "meta": "only"}
    assert sql_executor._extract_result_payload({"data": {"meta": "only"}, "meta": "root-only"}) == {"data": {"meta": "only"}, "meta": "root-only"}
    assert sql_executor._extract_result_payload({"meta": "root-only"}) == {}
    assert sql_executor._extract_result_payload(["bad"]) == {}

    rows = [{"a": 1, "b": date(2024, 1, 1)}]
    assert sql_executor._normalize_http_columns([{"name": "a"}, "b"], rows) == ["a", "b"]
    assert sql_executor._normalize_http_columns([{}, " "], rows) == ["a", "b"]
    assert sql_executor._normalize_http_columns([{"column_name": "c"}], []) == ["c"]
    assert sql_executor._normalize_http_columns([123, None], []) == []
    assert sql_executor._normalize_http_columns([], rows) == ["a", "b"]
    assert sql_executor._normalize_http_columns("bad", rows) == ["a", "b"]
    assert sql_executor._normalize_http_columns([], []) == []
    assert sql_executor._normalize_http_rows("bad", ["a"]) == []
    assert sql_executor._normalize_http_rows([[1, 2]], ["a", "b"]) == [{"a": 1, "b": 2}]
    assert sql_executor._normalize_http_rows([[1, 2]], []) == []
    assert sql_executor._normalize_http_rows(rows, ["a", "b"]) == [{"a": 1, "b": "2024-01-01"}]


def test_extract_result_payload_skips_non_dict_remote_data(monkeypatch):
    monkeypatch.setattr(sql_executor, "_extract_remote_data", lambda _payload: ["not", "dict"])

    assert sql_executor._extract_result_payload({"result": {"columns": ["root"]}}) == {"columns": ["root"]}


def test_get_executor_returns_hive_http_executor():
    executor = sql_executor.get_executor({
        "source_type": "hive",
        "query_executor_type": "http",
        "member_name": "hive-localhost",
        "source_config": {"org_code": "ORG_HIVE", "database": "dw"},
    })

    assert isinstance(executor, sql_executor.HiveHttpExecutor)
    assert executor.namespace == "ORG_HIVE"
    assert executor.source_type == "hive"
    assert executor.database == "dw"
    assert executor.org_code == "ORG_HIVE"
    assert executor.org_name == ""


def test_get_executor_returns_mysql_http_executor():
    executor = sql_executor.get_executor({
        "source_type": "mysql",
        "query_executor_type": "http",
        "member_name": "mysql-http-a",
        "source_config": {"org_code": "ORG_MYSQL", "database": "test_db"},
    })

    assert isinstance(executor, sql_executor.HiveHttpExecutor)
    assert executor.namespace == "ORG_MYSQL"
    assert executor.source_type == "mysql"
    assert executor.database == "test_db"


def test_get_executor_rejects_unknown_source_types():
    with pytest.raises(ValueError, match="只支持 mysql/hive"):
        sql_executor.get_executor({"source_type": "sqlite", "source_config": {"db_path": "/tmp/test.db"}})
    with pytest.raises(ValueError, match="不支持的数据源类型"):
        sql_executor.get_executor({"source_type": "oracle", "source_config": {}})


def test_get_executor_keeps_http_org_fields():
    executor = sql_executor.get_executor({
        "source_type": "hive",
        "query_executor_type": "http",
        "member_name": "上海付费通支付服务有限公司",
        "source_config": {
            "database": "dw",
            "org_code": "Z2002131000014",
            "org_name": "上海付费通支付服务有限公司",
        },
    })

    assert executor.namespace == "Z2002131000014"
    assert executor.org_code == "Z2002131000014"
    assert executor.org_name == "上海付费通支付服务有限公司"


@pytest.mark.parametrize("sql", [
    "select 1; drop table orders",
    "show tables; drop table orders",
    "desc orders; drop table orders",
    "select 1--1; drop table orders",
])
def test_validate_sql_rejects_multiple_statements(sql):
    is_valid, error = sql_executor.validate_sql(sql, source_type="mysql")

    assert is_valid is False
    assert "一次只能执行一条 SQL" in error


def test_validate_single_sql_statement_does_not_normalize_sql():
    result = validate_single_sql_statement("select * from orders limit 10,5; -- 注释", "mysql")

    assert result.success is True
    assert result.sql == "select * from orders limit 10,5"


def test_validate_sql_analyzes_raw_sql_without_comment_cleanup():
    assert sql_executor.validate_sql("select 1--1; drop table orders", source_type="mysql")[0] is False
    assert sql_executor.validate_sql("select 1 limit 100 ; -- 1; limit 500;", source_type="mysql")[0] is False
    assert sql_executor.validate_sql("/* leading */ select * from orders", source_type="mysql") == (True, "")


@pytest.mark.parametrize("sql", [
    "WITH t AS (select 1) select * from t",
    "(select 1)",
    "select 1 union select 2",
])
def test_validate_sql_allows_query_shapes_when_select_is_allowed(sql):
    assert sql_executor.validate_sql(sql, source_type="mysql") == (True, "")


def test_get_sql_operation_type_parser_error_edges(monkeypatch):
    original_import = __import__

    def fail_sqlglot_import(name, *args, **kwargs):
        if name == "sqlglot":
            raise ImportError("missing sqlglot")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fail_sqlglot_import)
    assert sql_executor._get_sql_operation_type("select 1", "mysql") == ""
    monkeypatch.setattr("builtins.__import__", original_import)

    monkeypatch.setattr(
        sql_parser,
        "_parse_sql",
        lambda *_args, **_kwargs: [],
    )
    assert sql_executor._get_sql_operation_type("select 1", "mysql") == ""

    def raise_parse(*_args, **_kwargs):
        raise RuntimeError("parse failed")

    monkeypatch.setattr(sql_parser, "_parse_sql", raise_parse)
    assert sql_executor._get_sql_operation_type("select 1", "mysql") == ""


@pytest.mark.parametrize("sql", [
    "show tables",
    "show create table orders",
    "describe orders",
    "desc orders",
    "explain select * from orders",
])
def test_validate_sql_rejects_metadata_commands_by_default(sql):
    is_valid, error = sql_executor.validate_sql(sql, source_type="mysql")

    assert is_valid is False
    assert error == "只允许执行以下类型的 SQL: SELECT"


@pytest.mark.parametrize("sql", [
    "delete from orders",
    "drop table orders",
    "alter table orders add column x int",
    "truncate table orders",
    "create table t(id int)",
    "update orders set amount = 1",
    "insert into orders(id) values (1)",
])
def test_validate_sql_rejects_disallowed_operations(sql):
    is_valid, error = sql_executor.validate_sql(sql, source_type="mysql")

    assert is_valid is False
    assert "只允许执行以下类型" in error


def test_auto_add_limit_only_for_query_sql():
    assert sql_executor.validate_sql("delete from orders")[0] is False
    assert sql_executor._should_auto_add_limit("", "mysql") is False
    assert sql_executor._add_limit_if_needed("select * from orders", 10) == "select * from orders LIMIT 10"
    assert sql_executor._add_limit_if_needed("WITH t AS (select 1) select * from t", 10).endswith("LIMIT 10")
    assert sql_executor._add_limit_if_needed("show create table orders", 10) == "show create table orders"
    assert sql_executor._add_limit_if_needed("describe orders", 10) == "describe orders"
    assert sql_executor._add_limit_if_needed("explain select * from orders", 10) == "explain select * from orders"
    assert sql_executor._add_limit_if_needed("select * from orders limit 5", 10) == "select * from orders limit 5"
    assert sql_executor._add_limit_if_needed("select * from orders limit 5 offset 10", 20) == "select * from orders limit 5 offset 10"
    assert sql_executor._add_limit_if_needed("select * from orders limit 10,5", 20) == "select * from orders limit 10,5"
    assert sql_executor._add_limit_if_needed("select * from (select * from orders limit 5) t", 10) == "select * from (select * from orders limit 5) t LIMIT 10"


def test_auto_add_limit_ignores_nested_limit_strings_and_comments():
    assert sql_executor._add_limit_if_needed("select 'limit 5' as txt from orders", 10) == "select 'limit 5' as txt from orders LIMIT 10"
    assert sql_executor._add_limit_if_needed('select "limit 5" as txt from orders', 10) == 'select "limit 5" as txt from orders LIMIT 10'
    assert sql_executor._add_limit_if_needed("select `limit` from orders", 10) == "select `limit` from orders LIMIT 10"
    assert sql_executor._add_limit_if_needed("select * from orders -- limit 5\nwhere id > 0", 10) == "select * from orders -- limit 5\nwhere id > 0 LIMIT 10"
    assert sql_executor._add_limit_if_needed("select * from orders /* limit 5 */ where id > 0", 10) == "select * from orders /* limit 5 */ where id > 0 LIMIT 10"
    assert sql_executor._add_limit_if_needed("select * from orders -- trailing comment", 10) == "select * from orders -- trailing comment\nLIMIT 10"
    assert sql_executor._add_limit_if_needed("select '-- not comment' as txt from orders", 10) == "select '-- not comment' as txt from orders LIMIT 10"
    assert sql_executor._line_has_comment_marker("select 'can\\'t -- comment' as txt") is False
    assert sql_executor._line_has_comment_marker("select 'it''s -- not comment' as txt") is False


def test_auto_add_limit_detects_only_top_level_limit():
    assert sql_executor._add_limit_if_needed("select * from (select * from orders limit 5 offset 2) t", 10).endswith("LIMIT 10")
    assert sql_executor._add_limit_if_needed("select * from (select * from orders limit 2,5) t", 10).endswith("LIMIT 10")
    assert sql_executor._add_limit_if_needed("with t as (select * from orders limit 5) select * from t", 10).endswith("LIMIT 10")
    assert sql_executor._add_limit_if_needed("select * from orders where name = 'limit 5' limit 20", 10).endswith("limit 20")


def test_build_effective_sql_only_adds_limit():
    assert sql_executor.build_effective_sql("select amount as 销售额 from orders", 10) == "select amount as 销售额 from orders LIMIT 10"
    assert sql_executor.build_effective_sql("select * from orders limit 5", 10) == "select * from orders limit 5"
    assert sql_executor.normalize_sql_aliases("select amount AS 销售额 from orders ORDER BY 销售额 GROUP BY 状态") == (
        "select amount AS `销售额` from orders ORDER BY `销售额` GROUP BY `状态`"
    )


def test_prepare_sql_for_execution_removes_comments_without_touching_string_literals():
    result = prepare_sql_for_execution("select '-- 你好','# 你好' from orders -- 注释\nwhere id = 1", "mysql")

    assert result.success is True
    assert result.sql == "SELECT '-- 你好', '# 你好' FROM orders WHERE id = 1"


def test_prepare_sql_for_execution_normalizes_mysql_limit_offset():
    result = prepare_sql_for_execution("select * from orders limit 10,5", "mysql")

    assert result.success is True
    assert result.sql == "SELECT * FROM orders LIMIT 5 OFFSET 10"


def test_prepare_sql_for_execution_keeps_explain_command_semantics():
    result = prepare_sql_for_execution("explain select * from orders -- 注释\nwhere id = 1", "mysql")

    assert result.success is True
    assert result.sql == "explain select * from orders \nwhere id = 1"


def test_prepare_sql_for_execution_rejects_multiple_sql_statements():
    result = prepare_sql_for_execution("select 1; drop table orders", "mysql")

    assert result.success is False
    assert "一次只能执行一条 SQL" in result.error


def test_prepare_sql_for_execution_allows_trailing_comment_statement():
    result = prepare_sql_for_execution("select 1; -- 注释", "mysql")

    assert result.success is True
    assert result.sql == "SELECT 1"


def test_auto_add_limit_rejects_limit_over_global_max():
    over_limit = sql_executor.DEFAULT_ROW_LIMIT + 1
    with pytest.raises(ValueError, match=f"最多返回 {sql_executor.DEFAULT_ROW_LIMIT} 条"):
        sql_executor._add_limit_if_needed(f"select * from orders limit {over_limit}", sql_executor.DEFAULT_ROW_LIMIT)
    with pytest.raises(ValueError, match=f"最多返回 {sql_executor.DEFAULT_ROW_LIMIT} 条"):
        sql_executor._add_limit_if_needed(f"select * from orders limit 10,{over_limit}", sql_executor.DEFAULT_ROW_LIMIT)
    with pytest.raises(ValueError, match=f"最多返回 {sql_executor.DEFAULT_ROW_LIMIT} 条"):
        sql_executor._add_limit_if_needed(f"select * from orders limit {over_limit} offset 10", sql_executor.DEFAULT_ROW_LIMIT)


def test_hive_http_executor_returns_limit_error_without_generic_sql_error(monkeypatch):
    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    executor = sql_executor.HiveHttpExecutor(
        namespace="hive-localhost",
        database="dw",
        org_code="Z2002131000014",
        org_name="上海付费通支付服务有限公司",
    )
    over_limit = sql_executor.DEFAULT_ROW_LIMIT + 1

    result = asyncio.run(executor.execute(f"select 1 limit {over_limit}", limit=sql_executor.DEFAULT_ROW_LIMIT))

    assert result["success"] is False
    assert f"查询结果最多返回 {sql_executor.DEFAULT_ROW_LIMIT} 条" in result["error"]
    assert "SQL执行错误" not in result["error"]


def test_friendly_sql_error_keeps_raw_database_error():
    raw_error = "(1146, \"Table 'demo.categories' doesn't exist\")"

    result = sql_executor._friendly_sql_error("MySQL", RuntimeError(raw_error))

    assert result == raw_error
    assert "表不存在" not in result
    assert "SQL执行错误" not in result


def test_friendly_sql_error_keeps_raw_syntax_error():
    raw_error = "ParseException line 1:8 cannot recognize input near 'from'"

    result = sql_executor._friendly_sql_error("Hive", RuntimeError(raw_error))

    assert result == raw_error
    assert not result.startswith("SQL语法错误:")


def test_hive_http_executor_submits_sql_org_code_limit_and_user_id(monkeypatch):
    captured_requests = []
    captured_remote_tasks = []

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        captured_requests.append({
            "method": method,
            "url": url,
            "json_payload": json_payload,
            "task_id": task_id,
            "params": params,
        })
        if method == "POST":
            return {"code": 20000, "data": {"task_id": "task_001"}, "msg": "提交成功"}
        return {
            "code": 20000,
            "data": {
                "status": "succeeded",
                "result": {
                    "columns": ["a"],
                    "data": [{"a": 1}],
                    "total_count": 1,
                    "execution_time_ms": 12,
                },
            },
            "msg": "执行成功",
        }

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    executor = sql_executor.HiveHttpExecutor(
        namespace="hive-localhost",
        database="dw",
        org_code="Z2002131000014",
        org_name="上海付费通支付服务有限公司",
    )
    result = asyncio.run(executor.execute("select 1", limit=50, user_id="10001", username="测试用户", remote_task_callback=captured_remote_tasks.append))

    assert result["success"] is True
    assert captured_requests[0]["json_payload"] == {
        "sql": "select 1 LIMIT 50",
        "database": "dw",
        "orgCode": "Z2002131000014",
        "orgName": "上海付费通支付服务有限公司",
        "limit": 50,
        "userId": "10001",
        "username": "测试用户",
    }
    assert "namespace" not in captured_requests[0]["json_payload"]
    assert captured_requests[1]["params"] == {"userId": "10001", "username": "测试用户"}
    assert captured_remote_tasks == [{
        "remote_task_id": "task_001",
        "namespace": "hive-localhost",
        "source_type": "hive",
    }]


def test_mysql_http_executor_uses_mysql_timeout(monkeypatch):
    executor = sql_executor.HiveHttpExecutor(namespace="mysql-http-a", source_type="mysql")

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://query/submit")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://query/status/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    result = asyncio.run(executor.execute("select 1"))

    assert result["success"] is False
    assert result["error"] == "查询超时（60秒）"


def test_hive_http_executor_returns_submit_msg_when_code_is_not_20000(monkeypatch):
    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        return {"code": 50001, "task_id": "", "msg": "SQL 不合法"}

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    executor = sql_executor.HiveHttpExecutor(namespace="hive-localhost")
    result = asyncio.run(executor.execute("select 1"))

    assert result["success"] is False
    assert result["error"] == "SQL 不合法"


def test_hive_http_executor_rejects_disallowed_sql_before_submit(monkeypatch):
    captured = {"called": False}

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        captured["called"] = True
        return {}

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    result = asyncio.run(sql_executor.HiveHttpExecutor(namespace="ns_a").execute("delete from orders"))

    assert result["success"] is False
    assert "只允许执行以下类型" in result["error"]
    assert captured["called"] is False


def test_hive_http_executor_rejects_multiple_sql_before_submit(monkeypatch):
    captured = {"called": False}

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        captured["called"] = True
        return {}

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    result = asyncio.run(sql_executor.HiveHttpExecutor(namespace="ns_a").execute("select 1; drop table orders"))

    assert result["success"] is False
    assert "一次只能执行一条 SQL" in result["error"]
    assert captured["called"] is False


def test_hive_http_executor_requires_namespace_and_urls(monkeypatch):
    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "")

    result_without_namespace = asyncio.run(sql_executor.HiveHttpExecutor(namespace="").execute("select 1"))
    result_without_urls = asyncio.run(sql_executor.HiveHttpExecutor(namespace="ns_a").execute("select 1"))

    assert "缺少 HTTP 执行标识" in result_without_namespace["error"]
    assert "未配置 HTTP 查询接口" in result_without_urls["error"]


def test_hive_http_executor_returns_missing_task_id_message(monkeypatch):
    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        return {"code": 20000, "msg": "提交成功但无任务"}

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    result = asyncio.run(sql_executor.HiveHttpExecutor(namespace="ns_a").execute("select 1"))

    assert result["success"] is False
    assert result["error"] == "提交成功但无任务"


def test_hive_http_executor_handles_cancelled_and_failed_status(monkeypatch):
    statuses = [
        {"code": 20000, "data": {"status": "cancelled"}, "msg": "用户取消"},
        {"code": 20000, "data": {"status": "failed"}, "msg": "表不存在"},
    ]

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        if method == "POST":
            return {"code": 20000, "data": {"task_id": "task_001"}, "msg": "提交成功"}
        return statuses.pop(0)

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    cancelled = asyncio.run(sql_executor.HiveHttpExecutor(namespace="ns_a").execute("select 1"))
    failed = asyncio.run(sql_executor.HiveHttpExecutor(namespace="ns_a").execute("select 1"))

    assert cancelled["success"] is False
    assert cancelled["error"] == "用户取消"
    assert failed["success"] is False
    assert failed["error"] == "表不存在"


def test_hive_http_executor_polls_running_then_success_and_async_callback(monkeypatch):
    responses = [
        {"code": 20000, "data": {"task_id": "task_001"}, "msg": "提交成功"},
        {"code": 20000, "data": {"status": "running"}, "msg": "运行中"},
        {"code": 20000, "data": {"status": "succeeded", "columns": ["a"], "rows": [[1]], "row_count": 1, "duration_ms": 5}},
    ]
    captured_remote_tasks = []

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        return responses.pop(0)

    async def fake_sleep(_seconds):
        return None

    async def fake_callback(payload):
        captured_remote_tasks.append(payload)

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor, "DEFAULT_HTTP_POLL_INTERVAL_MS", 1)
    monkeypatch.setattr(sql_executor.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    result = asyncio.run(sql_executor.HiveHttpExecutor(namespace="ns_a").execute("select 1", remote_task_callback=fake_callback))

    assert result["success"] is True
    assert result["remote_task_id"] == "task_001"
    assert result["source_type"] == "hive"
    assert result["data"] == [{"a": 1}]
    assert captured_remote_tasks == [{"remote_task_id": "task_001", "namespace": "ns_a", "source_type": "hive"}]


def test_hive_http_executor_returns_status_code_error_without_failed_status(monkeypatch):
    responses = [
        {"code": 20000, "data": {"task_id": "task_001"}},
        {"code": 50000, "data": {"status": "running"}, "msg": "网关异常"},
    ]

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        return responses.pop(0)

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    result = asyncio.run(sql_executor.HiveHttpExecutor(namespace="ns_a").execute("select 1"))

    assert result["success"] is False
    assert result["error"] == "网关异常"


def test_hive_http_executor_handles_generic_request_error(monkeypatch):
    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        raise RuntimeError("connect failed")

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    result = asyncio.run(sql_executor.HiveHttpExecutor(namespace="ns_a").execute("select 1"))

    assert result["success"] is False
    assert result["error"] == "connect failed"


def test_hive_http_executor_returns_status_msg_when_status_query_failed(monkeypatch):
    captured_calls = []

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        captured_calls.append(method)
        if method == "POST":
            return {"code": 20000, "data": {"task_id": "task_001"}, "msg": "提交成功"}
        return {"code": 50003, "data": {"status": "failed"}, "msg": "无权限访问该表"}

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    executor = sql_executor.HiveHttpExecutor(namespace="hive-localhost")
    result = asyncio.run(executor.execute("select 1"))

    assert captured_calls == ["POST", "GET"]
    assert result["success"] is False
    assert result["error"] == "无权限访问该表"


def test_stop_http_query_task_uses_stop_url(monkeypatch):
    captured = {}

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        captured.update({
            "method": method,
            "url": url,
            "task_id": task_id,
            "json_payload": json_payload,
        })
        return {"code": 20000, "msg": "已取消"}

    monkeypatch.setattr(sql_executor, "HTTP_STOP_URL", "http://hive/query-tasks/cancel")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    result = asyncio.run(sql_executor.stop_http_query_task("remote_001", "hive", user_id="user_a", username="测试用户"))

    assert result["success"] is True
    assert captured == {
        "method": "POST",
        "url": "http://hive/query-tasks/cancel",
        "task_id": "",
        "json_payload": {"task_id": "remote_001", "userId": "user_a", "username": "测试用户"},
    }


def test_stop_http_query_task_requires_stop_url(monkeypatch):
    monkeypatch.setattr(sql_executor, "HTTP_STOP_URL", "")

    result = asyncio.run(sql_executor.stop_http_query_task("remote_001", "hive"))

    assert result["success"] is False
    assert "CHAT_DATA_HTTP_STOP_URL" in result["error"]


def test_stop_http_query_task_returns_remote_error_and_exception(monkeypatch):
    responses = [
        {"code": 50000, "msg": "不能取消"},
    ]

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        return responses.pop(0)

    async def fake_request_json_error(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        raise RuntimeError("stop failed")

    monkeypatch.setattr(sql_executor, "HTTP_STOP_URL", "http://hive/query-tasks/cancel")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)
    failed = asyncio.run(sql_executor.stop_http_query_task("remote_001", "hive"))

    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json_error)
    error = asyncio.run(sql_executor.stop_http_query_task("remote_002", "hive"))

    assert failed == {"success": False, "error": "不能取消"}
    assert error == {"success": False, "error": "stop failed"}


def test_fetch_http_query_task_result_status_variants(monkeypatch):
    responses = [
        {"code": 20000, "data": {"status": "succeeded", "result": {"columns": ["a"], "rows": [[1]], "row_count": 1, "duration_ms": 9}}},
        {"code": 20000, "data": {"status": "running"}},
        {"code": 20000, "data": {"status": "cancelled"}, "msg": "已取消"},
        {"code": 50000, "data": {"status": "failed"}, "msg": "执行失败"},
    ]

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        return responses.pop(0)

    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    succeeded = asyncio.run(sql_executor.fetch_http_query_task_result("task_1"))
    running = asyncio.run(sql_executor.fetch_http_query_task_result("task_2"))
    cancelled = asyncio.run(sql_executor.fetch_http_query_task_result("task_3"))
    failed = asyncio.run(sql_executor.fetch_http_query_task_result("task_4"))

    assert succeeded["success"] is True
    assert succeeded["columns"] == ["a"]
    assert succeeded["data"] == [{"a": 1}]
    assert running["status"] == "running"
    assert running["success"] is False
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"] == "已取消"
    assert failed["status"] == "failed"
    assert failed["error"] == "执行失败"


def test_fetch_http_query_task_result_without_user_params(monkeypatch):
    captured = {}

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        captured.update({"method": method, "task_id": task_id, "params": params})
        return {"code": 20000, "data": {"status": "running"}}

    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    result = asyncio.run(sql_executor.fetch_http_query_task_result("task_no_user"))

    assert result["status"] == "running"
    assert captured == {"method": "GET", "task_id": "task_no_user", "params": None}


def test_fetch_http_query_task_result_with_user_params(monkeypatch):
    captured = {}

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        captured.update({"method": method, "task_id": task_id, "params": params})
        return {"code": 20000, "data": {"status": "running"}}

    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    result = asyncio.run(sql_executor.fetch_http_query_task_result("task_user", user_id="u1", username="张三"))

    assert result["status"] == "running"
    assert captured["params"]["userId"] == "u1"
    assert captured["params"]["username"] == "张三"


def test_fetch_http_query_task_result_non_failed_error_status(monkeypatch):
    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id="", params=None):
        return {"code": 50000, "data": {"status": "running"}, "msg": "网关异常"}

    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    result = asyncio.run(sql_executor.fetch_http_query_task_result("task_1"))

    assert result["status"] == "running"
    assert result["success"] is False
    assert result["error"] == "网关异常"


def test_stop_http_query_task_rejects_missing_task_id():
    result = asyncio.run(sql_executor.stop_http_query_task(""))

    assert result == {"success": False, "error": "缺少远端任务ID"}


def test_hive_executor_sync_success_value_error_and_connection_test(monkeypatch):
    class FakeCursor:
        description = [("id",)]

        def __init__(self):
            self.executed = []

        def execute(self, sql):
            self.executed.append(sql)

        def fetchall(self):
            return [(1,)]

    class FakeConnection:
        def __init__(self):
            self.cursor_obj = FakeCursor()
            self.closed = False

        def cursor(self):
            return self.cursor_obj

        def close(self):
            self.closed = True

    connection = FakeConnection()
    executor = sql_executor.HiveExecutor("127.0.0.1", 10000, "default")
    monkeypatch.setattr(executor, "_get_connection", lambda: connection)

    success = executor._execute_sync("select 1", 10)
    connected = executor._test_sync()
    close_result = asyncio.run(executor.close())

    assert success["success"] is True
    assert success["data"] == [{"id": 1}]
    assert connection.cursor_obj.executed[0] == "select 1 LIMIT 10"
    assert connected is True
    assert close_result is None

    monkeypatch.setattr(sql_executor, "build_effective_sql", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("limit too large")))
    failed = executor._execute_sync("select 1", 10)

    assert failed["success"] is False
    assert failed["error"] == "limit too large"


def test_kill_mysql_query_rejects_non_mysql_and_closes(monkeypatch):
    class FakeExecutor:
        closed = False

        async def close(self):
            self.closed = True

    fake_executor = FakeExecutor()
    monkeypatch.setattr(sql_executor, "get_executor", lambda _datasource: fake_executor)

    result = asyncio.run(sql_executor.kill_mysql_query({"source_type": "sqlite", "source_config": {}}, "123"))

    assert result == {"success": False, "error": "当前执行器不支持直连停止"}
    assert fake_executor.closed is True


def test_kill_mysql_query_success_and_init_error(monkeypatch):
    class FakeMySQLExecutor(sql_executor.MySQLExecutor):
        def __init__(self):
            pass

        async def kill_query(self, connection_id):
            return {"success": True, "connection_id": connection_id}

        async def close(self):
            self.closed = True

    fake_executor = FakeMySQLExecutor()
    monkeypatch.setattr(sql_executor, "get_executor", lambda _datasource: fake_executor)
    success = asyncio.run(sql_executor.kill_mysql_query({"source_type": "mysql", "source_config": {}}, "123"))

    monkeypatch.setattr(sql_executor, "get_executor", lambda _datasource: (_ for _ in ()).throw(RuntimeError("init failed")))
    failed = asyncio.run(sql_executor.kill_mysql_query({"source_type": "mysql", "source_config": {}}, "123"))

    assert success == {"success": True, "connection_id": "123"}
    assert fake_executor.closed is True
    assert failed == {"success": False, "error": "init failed"}


class _FakeAcquireContext:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeCursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    async def __aenter__(self):
        return self.cursor

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakePool:
    def __init__(self, cursor):
        self.cursor = cursor
        self.closed = False
        self.waited = False

    def acquire(self):
        return _FakeAcquireContext(_FakeConn(self.cursor))

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.waited = True


class _FakeConn:
    def __init__(self, cursor):
        self.cursor_obj = cursor

    def cursor(self):
        return _FakeCursorContext(self.cursor_obj)


class _FakeMySQLCursor:
    description = [("id",), ("amount",)]

    def __init__(self):
        self.executed = []
        self.fetch_count = 0

    async def execute(self, sql):
        self.executed.append(sql)

    async def fetchone(self):
        return (12345,)

    async def fetchall(self):
        self.fetch_count += 1
        return [(1, Decimal("12.5"))]


def test_mysql_executor_success_callbacks_kill_and_close(monkeypatch):
    cursor = _FakeMySQLCursor()
    pool = _FakePool(cursor)
    callback_payloads = []
    executor = sql_executor.MySQLExecutor("127.0.0.1", 3306, "demo", "u", "p")
    monkeypatch.setattr(executor, "_get_pool", lambda: asyncio.sleep(0, result=pool))

    async def db_callback(payload):
        callback_payloads.append(payload)

    result = asyncio.run(executor.execute("select * from orders", limit=10, db_connection_callback=db_callback))
    connection_ok = asyncio.run(executor.test_connection())
    kill_result = asyncio.run(executor.kill_query("12345"))
    executor._pool = pool
    close_result = asyncio.run(executor.close())

    assert result["success"] is True
    assert result["columns"] == ["id", "amount"]
    assert result["data"] == [{"id": 1, "amount": 12.5}]
    assert result["db_connection_id"] == "12345"
    assert callback_payloads == [{"db_connection_id": "12345"}]
    assert cursor.executed[0] == "SELECT CONNECTION_ID()"
    assert cursor.executed[1] == "select * from orders LIMIT 10"
    assert connection_ok is True
    assert "SELECT 1" in cursor.executed
    assert kill_result == {"success": True, "error": None}
    assert "KILL QUERY 12345" in cursor.executed
    assert close_result is None
    assert pool.closed is True
    assert pool.waited is True
    assert executor._pool is None


def test_mysql_executor_optional_connection_and_sync_callback_branches(monkeypatch):
    class EmptyConnectionCursor(_FakeMySQLCursor):
        async def fetchone(self):
            return None

    no_connection_cursor = EmptyConnectionCursor()
    executor = sql_executor.MySQLExecutor("127.0.0.1", 3306, "demo", "u", "p")
    monkeypatch.setattr(executor, "_get_pool", lambda: asyncio.sleep(0, result=_FakePool(no_connection_cursor)))

    no_connection = asyncio.run(executor.execute("select 1", limit=10))
    assert no_connection["success"] is True
    assert no_connection["db_connection_id"] == ""
    assert no_connection_cursor.executed[1] == "select 1 LIMIT 10"

    callback_payloads = []
    tuple_cursor = _FakeMySQLCursor()
    tuple_cursor.fetchone = lambda: asyncio.sleep(0, result=67890)
    monkeypatch.setattr(executor, "_get_pool", lambda: asyncio.sleep(0, result=_FakePool(tuple_cursor)))

    result = asyncio.run(executor.execute(
        "select 1",
        limit=10,
        db_connection_callback=lambda payload: callback_payloads.append(payload),
    ))

    assert result["db_connection_id"] == "67890"
    assert callback_payloads == [{"db_connection_id": "67890"}]

    empty_executor = sql_executor.MySQLExecutor("127.0.0.1", 3306, "demo", "u", "p")
    assert asyncio.run(empty_executor.close()) is None


def test_mysql_executor_error_timeout_test_connection_and_invalid_kill(monkeypatch):
    executor = sql_executor.MySQLExecutor("127.0.0.1", 3306, "demo", "u", "p")

    invalid_sql = asyncio.run(executor.execute("delete from orders"))
    assert invalid_sql["success"] is False
    assert "只允许执行以下类型" in invalid_sql["error"]

    class TimeoutCursor(_FakeMySQLCursor):
        async def execute(self, sql):
            if sql == "SELECT CONNECTION_ID()":
                await super().execute(sql)
                return
            raise asyncio.TimeoutError()

    timeout_pool = _FakePool(TimeoutCursor())
    monkeypatch.setattr(executor, "_get_pool", lambda: asyncio.sleep(0, result=timeout_pool))
    timeout = asyncio.run(executor.execute("select 1", timeout=1))
    assert timeout["success"] is False
    assert timeout["error"] == "查询超时（1秒）"

    class FailingCursor(_FakeMySQLCursor):
        async def execute(self, sql):
            raise RuntimeError("table missing")

    failing_pool = _FakePool(FailingCursor())
    monkeypatch.setattr(executor, "_get_pool", lambda: asyncio.sleep(0, result=failing_pool))
    assert asyncio.run(executor.test_connection()) is False
    failed = asyncio.run(executor.execute("select 1"))
    assert failed["success"] is False
    assert failed["error"] == "table missing"
    assert asyncio.run(executor.kill_query("abc")) == {"success": False, "error": "无效的 MySQL 连接ID"}


def test_mysql_executor_get_pool_lazy_initialization(monkeypatch):
    created = {}

    class FakeAiomysql:
        async def create_pool(self, **kwargs):
            created.update(kwargs)
            return "pool"

    monkeypatch.setitem(sys.modules, "aiomysql", FakeAiomysql())
    executor = sql_executor.MySQLExecutor("127.0.0.1", 3306, "demo", "u", "")

    pool_first = asyncio.run(executor._get_pool())
    pool_second = asyncio.run(executor._get_pool())

    assert pool_first == "pool"
    assert pool_second == "pool"
    assert created["password"] is None
    assert created["connect_timeout"] == sql_executor.CONNECT_TIMEOUT_SECONDS


def test_hive_executor_async_execute_timeout_error_and_test_connection_error(monkeypatch):
    executor = sql_executor.HiveExecutor("127.0.0.1", 10000, "default")

    invalid_sql = asyncio.run(executor.execute("delete from orders"))
    assert invalid_sql["success"] is False
    assert "只允许执行以下类型" in invalid_sql["error"]

    monkeypatch.setattr(sql_executor, "resolve_query_timeout", lambda *_args, **_kwargs: 0.001)
    monkeypatch.setattr(executor, "_execute_sync", lambda *_args: __import__("time").sleep(0.02) or {"success": True})
    timeout = asyncio.run(executor.execute("select 1"))
    assert timeout["success"] is False
    assert timeout["error"] == "查询超时（0.001秒）"

    monkeypatch.setattr(executor, "_test_sync", lambda: (_ for _ in ()).throw(RuntimeError("connect failed")))
    assert asyncio.run(executor.test_connection()) is False


def test_hive_executor_connection_and_sync_generic_error(monkeypatch):
    executor = sql_executor.HiveExecutor("127.0.0.1", 10000, "default", username="u", password="")

    class FakeHiveModule:
        @staticmethod
        def connect(**kwargs):
            FakeHiveModule.kwargs = kwargs
            return SimpleNamespace(_transport=None)

    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, "pyhive", SimpleNamespace(hive=FakeHiveModule))
    monkeypatch.setitem(sys.modules, "pyhive.hive", FakeHiveModule)
    monkeypatch.setattr(sql_executor, "_apply_socket_timeout", lambda transport, timeout: None)

    conn = executor._get_connection()
    assert conn._transport is None
    assert FakeHiveModule.kwargs["host"] == "127.0.0.1"

    monkeypatch.setattr(executor, "_get_connection", lambda: (_ for _ in ()).throw(RuntimeError("hive down")))
    failed = executor._execute_sync("select 1", 10)
    assert failed["success"] is False
    assert failed["error"] == "hive down"


def test_hive_http_request_json_builds_get_and_post_requests(monkeypatch):
    requests = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 20000, "data": {"ok": True}}

    class FakeAsyncClient:
        def __init__(self, verify=False):
            self.verify = verify

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, **kwargs):
            requests.append((method, url, kwargs, self.verify))
            return FakeResponse()

    import types
    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=FakeAsyncClient))
    monkeypatch.setattr(sql_executor, "HTTP_AUTH_TOKEN", "token_a")
    monkeypatch.setattr(sql_executor, "HTTP_VERIFY_TLS", True)
    executor = sql_executor.HiveHttpExecutor(namespace="ns_a")

    get_payload = asyncio.run(executor._request_json("GET", "http://host/tasks", task_id="task_1", params={"userId": "u1"}))
    post_payload = asyncio.run(executor._request_json("POST", "http://host/tasks/{task_id}", task_id="task_2", json_payload={"sql": "select 1"}))

    assert get_payload == {"code": 20000, "data": {"ok": True}}
    assert post_payload == {"code": 20000, "data": {"ok": True}}
    assert requests[0][2]["headers"]["Authorization"] == "Bearer token_a"
    assert requests[0][2]["params"] == {"userId": "u1", "task_id": "task_1"}
    assert requests[1][1] == "http://host/tasks/task_2"
    assert requests[1][2]["json"] == {"sql": "select 1"}
    assert requests[0][3] is True


def test_hive_http_request_json_get_without_task_id_uses_plain_url(monkeypatch):
    requests = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return ["not dict"]

    class FakeAsyncClient:
        def __init__(self, verify=False):
            self.verify = verify

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, **kwargs):
            requests.append((method, url, kwargs))
            return FakeResponse()

    import types
    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=FakeAsyncClient))
    monkeypatch.setattr(sql_executor, "HTTP_AUTH_TOKEN", "")
    executor = sql_executor.HiveHttpExecutor(namespace="ns_a")

    payload = asyncio.run(executor._request_json("GET", "http://host/tasks", task_id="", params={}))

    assert payload == {}
    assert requests == [("GET", "http://host/tasks", {
        "headers": {"Content-Type": "application/json"},
        "timeout": sql_executor.DEFAULT_HIVE_QUERY_TIMEOUT_SECONDS,
    })]


def test_hive_http_payload_and_connection_helpers(monkeypatch):
    hive_executor = sql_executor.HiveHttpExecutor(namespace="ORG_A", source_type="hive", database="dw", org_code="ORG_A", org_name="机构A")
    mysql_executor = sql_executor.HiveHttpExecutor(namespace="ORG_M", source_type="mysql")

    assert hive_executor._build_headers() == {"Content-Type": "application/json"}
    hive_payload = hive_executor._build_submit_payload("select 1", 10, user_id="u1", username="张三")
    mysql_payload = mysql_executor._build_submit_payload("select 1", 10, user_id="u1", username="张三")

    assert hive_payload["username"] == "张三"
    assert "namespace" not in hive_payload
    assert mysql_payload["userId"] == "u1"
    assert mysql_payload["username"] == "张三"
    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://status")
    assert asyncio.run(hive_executor.test_connection()) is False
    assert asyncio.run(hive_executor.close()) is None


def test_hive_http_normalize_result_falls_back_to_records_and_row_count():
    executor = sql_executor.HiveHttpExecutor(namespace="ns_a")

    records_result = executor._normalize_result({
        "data": {
            "status": "succeeded",
            "records": [{"id": 1}],
            "columns": [{"name": "id"}],
            "row_count": 3,
        }
    })
    rows_result = executor._normalize_result({
        "data": {
            "status": "succeeded",
            "rows": [[2]],
            "columns": ["id"],
        }
    })

    assert records_result["data"] == [{"id": 1}]
    assert records_result["total_count"] == 3
    assert rows_result["data"] == [{"id": 2}]
    assert rows_result["total_count"] == 1


def test_hive_http_execute_deadline_timeout_and_value_error(monkeypatch):
    class FakeLoop:
        def __init__(self):
            self.calls = 0

        def time(self):
            self.calls += 1
            return 100 if self.calls == 1 else 101

    async def fake_request_json(self, method, url, *, json_payload=None, task_id="", params=None):
        if method == "POST":
            return {"code": 20000, "data": {"task_id": "task_1"}}
        return {"code": 20000, "data": {"status": "running"}}

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://submit")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://status/{task_id}")
    monkeypatch.setattr(sql_executor, "resolve_query_timeout", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(sql_executor.asyncio, "get_running_loop", lambda: FakeLoop())
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    timeout = asyncio.run(sql_executor.HiveHttpExecutor(namespace="ns").execute("select 1"))
    assert timeout["success"] is False
    assert "查询超时" in timeout["error"]

    monkeypatch.setattr(sql_executor, "build_effective_sql", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("limit too large")))
    value_error = asyncio.run(sql_executor.HiveHttpExecutor(namespace="ns").execute("select 1"))
    assert value_error["success"] is False
    assert value_error["error"] == "limit too large"


def test_get_executor_fills_direct_credentials(monkeypatch):
    monkeypatch.setattr(sql_executor, "resolve_mysql_credentials", lambda config: {"user": "mysql_user", "password": "mysql_pwd"})
    mysql_executor = sql_executor.get_executor({"source_type": "mysql", "source_config": {"host": "h", "port": 3306, "database": "d"}})
    explicit_mysql_executor = sql_executor.get_executor({"source_type": "mysql", "source_config": {"host": "h", "port": 3306, "database": "d", "user": "explicit", "password": "pwd"}})
    assert isinstance(mysql_executor, sql_executor.MySQLExecutor)
    assert mysql_executor.user == "mysql_user"
    assert mysql_executor.password == "mysql_pwd"
    assert explicit_mysql_executor.user == "explicit"
    assert explicit_mysql_executor.password == "pwd"

    monkeypatch.setattr(sql_executor, "resolve_hive_credentials", lambda: {"username": "hive_user", "password": "hive_pwd"})
    hive_executor = sql_executor.get_executor({"source_type": "hive", "source_config": {"host": "h", "port": 10000, "database": "d"}})
    explicit_hive_executor = sql_executor.get_executor({"source_type": "hive", "source_config": {"host": "h", "port": 10000, "database": "d", "username": "explicit", "password": "pwd"}})
    assert isinstance(hive_executor, sql_executor.HiveExecutor)
    assert hive_executor.username == "hive_user"
    assert hive_executor.password == "hive_pwd"
    assert explicit_hive_executor.username == "explicit"
    assert explicit_hive_executor.password == "pwd"

    monkeypatch.setattr(sql_executor, "resolve_hive_credentials", lambda: {"username": "hive_only"})
    hive_username_only = sql_executor.get_executor({"source_type": "hive", "source_config": {"host": "h", "database": "d", "password": ""}})
    assert hive_username_only.username == "hive_only"
    assert hive_username_only.password is None

    monkeypatch.setattr(sql_executor, "resolve_hive_credentials", lambda: {"password": "pwd_only"})
    hive_password_only = sql_executor.get_executor({"source_type": "hive", "source_config": {"host": "h", "database": "d", "username": ""}})
    assert hive_password_only.username == ""
    assert hive_password_only.password == "pwd_only"


def test_abstract_executor_and_mysql_error_branches(monkeypatch):
    class BareExecutor(sql_executor.SQLExecutor):
        async def execute(self, sql, limit=100, timeout=None, user_id=None, remote_task_callback=None, db_connection_callback=None):
            return await super().execute(sql, limit, timeout, user_id, remote_task_callback, db_connection_callback)

        async def test_connection(self):
            return await super().test_connection()

        async def close(self):
            return await super().close()

    bare = BareExecutor()
    assert asyncio.run(bare.execute("select 1")) is None
    assert asyncio.run(bare.test_connection()) is None
    assert asyncio.run(bare.close()) is None

    mysql = sql_executor.MySQLExecutor("localhost", 3306, "demo", "u", "p")

    class FakeCursor:
        description = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _sql):
            return None

        async def fetchone(self):
            return [123]

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, *_args):
            return None

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    monkeypatch.setattr(sql_executor, "validate_sql", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(sql_executor, "build_effective_sql", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("limit invalid")))
    monkeypatch.setattr(mysql, "_get_pool", lambda: asyncio.sleep(0, result=FakePool()))
    invalid = asyncio.run(mysql.execute("select 1"))
    assert invalid["success"] is False
    assert invalid["error"] == "limit invalid"

    monkeypatch.setattr(mysql, "_get_pool", lambda: asyncio.sleep(0, result=SimpleNamespace(acquire=lambda: (_ for _ in ()).throw(RuntimeError("pool down")))))
    assert asyncio.run(mysql.test_connection()) is False
    assert asyncio.run(mysql.kill_query("123"))["success"] is False
