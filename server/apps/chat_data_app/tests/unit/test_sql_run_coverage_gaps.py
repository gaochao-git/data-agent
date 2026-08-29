"""补充 sql_run runtime 与 task_service 未覆盖分支的单元测试。"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "test")

from apps.chat_data_app.schema import ExecuteSqlRequest, QueryTaskCreateRequest
from apps.chat_data_app.services.sql_run import cache as query_cache
from apps.chat_data_app.services.sql_run import runtime as query_runtime
from apps.chat_data_app.services.sql_run.task_service import QueryTaskService


def _make_service(**overrides):
    defaults = dict(
        query_history_service=SimpleNamespace(),
        datasource_manager_factory=lambda: SimpleNamespace(),
        fetch_http_query_task_result=lambda *_args, **_kwargs: {},
        stop_http_query_task=lambda *_args, **_kwargs: {},
        kill_mysql_query=lambda *_args, **_kwargs: {},
    )
    defaults.update(overrides)
    return QueryTaskService(**defaults)


def test_validate_before_task_submit_skips_empty_sql_and_blocks_dangerous(monkeypatch):
    alarms = []
    monkeypatch.setattr(query_runtime, "emit_dangerous_sql_alarm", lambda *args, **kwargs: alarms.append((args, kwargs)))

    empty_sql = SimpleNamespace(sql="", datasource_id="ds_a", user_id="u1", limit=None)
    query_runtime._validate_sql_before_task_submit(empty_sql, {"source_type": "mysql"})
    assert alarms == []

    monkeypatch.setattr(
        query_runtime,
        "run_sql_guard",
        lambda *_args, **_kwargs: SimpleNamespace(
            rule="command_type",
            message="检测到危险 SQL",
            internal=False,
            syntax_error=False,
            reason_code="SQL_TYPE_NOT_ALLOWED",
        ),
    )

    request = ExecuteSqlRequest(sql="delete from orders", datasource_id="ds_a", user_id="u1")
    with pytest.raises(ValueError, match="检测到危险 SQL"):
        query_runtime._validate_sql_before_task_submit(request, {"source_type": "mysql"})
    assert alarms[0][0][0] == "delete from orders"
    assert alarms[0][1]["execution_scope"] == "task_submit"
    assert alarms[0][1]["reason_code"] == "SQL_TYPE_NOT_ALLOWED"


def test_normalize_execution_time_and_datetime_parse_branches():
    assert query_runtime.normalize_execution_time_ms("abc") is None
    assert query_runtime.resolve_execution_time_ms(None, "", "not-a-date") is None
    assert query_runtime.resolve_execution_time_ms(None, "  ", "2026-01-01T00:00:00") is None
    assert query_runtime.resolve_execution_time_ms(None, "2026-01-01T00:00:00", "bad-date") is None


def test_prepare_sql_request_compiles_semantic_builder_with_user(monkeypatch):
    async def fake_compile(datasource_id, semantic_config_id, semantic_builder_config, user_id=None):
        assert user_id == "u1"
        return {"sql": "select city from orders where user_id = 'u1'"}

    monkeypatch.setattr(query_runtime, "compile_semantic_builder_sql", fake_compile)
    request = ExecuteSqlRequest(
        sql="select bad",
        datasource_id="ds_a",
        user_id="u1",
        semantic_config_id="cfg_a",
        semantic_builder_config={"dimensions": []},
    )

    prepared = asyncio.run(query_runtime.prepare_sql_request_for_execution(request))
    assert prepared.sql == "select city from orders where user_id = 'u1'"


def test_prepare_sql_batch_request_builder_and_multi_statement_paths(monkeypatch):
    async def fake_compile(datasource_id, semantic_config_id, semantic_builder_config, user_id=None):
        return {"sql": "select 1"}

    monkeypatch.setattr(query_runtime, "compile_semantic_builder_sql", fake_compile)

    builder_request = QueryTaskCreateRequest(
        datasource_id="ds_a",
        query_type="builder",
        sql=["select bad"],
        semantic_config_id="cfg_a",
        semantic_builder_config={"dimensions": []},
    )
    prepared_builder = asyncio.run(query_runtime.prepare_sql_batch_request_for_execution(builder_request))
    assert prepared_builder.sql == ["select 1"]

    single = QueryTaskCreateRequest(datasource_id="ds_a", sql=["select 1"])
    prepared_single = asyncio.run(query_runtime.prepare_sql_batch_request_for_execution(single))
    assert prepared_single.sql == ["select 1"]

    multi = QueryTaskCreateRequest(datasource_id="ds_a", sql=["select 1", "select 2"])
    prepared_multi = asyncio.run(query_runtime.prepare_sql_batch_request_for_execution(multi))
    assert prepared_multi is multi


def test_create_task_for_execute_request_builds_internal_payload(monkeypatch):
    captured = {}

    async def fake_create_task(payload):
        captured.update(payload.model_dump())
        return {"task_id": "task_1", "task_type": "sql", "status": "pending"}

    monkeypatch.setattr(query_runtime, "create_query_task", fake_create_task)
    request = ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["m1"],
        user_id="u1",
        conversation_id="conv_1",
        query_text="查一下",
        limit=10,
        timeout=30,
        query_type="sql",
        skip_query_cache=True,
    )

    result = asyncio.run(query_runtime.create_task_for_execute_request(request, query_type="agent", operator_name="张三"))
    assert result["task_id"] == "task_1"
    assert captured["query_type"] == "agent"
    assert captured["operator_name"] == "张三"
    assert captured["sql"] == ["select 1"]
    assert captured["member_datasource_ids"] == ["m1"]
    assert captured["skip_query_cache"] is True


def test_execute_sql_for_member_direct_cache_hit_returns_cached(monkeypatch):
    cached = {"status": "succeeded", "columns": ["id"], "data": [{"id": 1}], "total_count": 1, "execution_time_ms": 5}

    async def fake_get_cache(_identity):
        return dict(cached)

    executed = []

    async def should_not_execute(**_kwargs):
        executed.append(True)
        return {}

    monkeypatch.setattr(query_cache, "is_sql_result_cache_enabled", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(query_cache, "get_sql_result_cache", fake_get_cache)
    monkeypatch.setattr(query_runtime, "run_with_sql_user_datasource_limit", should_not_execute)

    result = asyncio.run(query_runtime.execute_sql_for_member(
        execution_target={"member_datasource_id": "m1", "member_name": "成员1", "query_executor_type": "direct"},
        sql="select 1",
        limit=10,
        timeout=None,
    ))

    assert executed == []
    assert result["data"] == [{"id": 1}]
    assert result["execution_time_ms"] == 5


def test_run_sql_batch_query_task_cancelled_and_empty_statements(monkeypatch):
    updates = []
    cancelled_values = iter([True, False])

    async def is_cancelled(_task_id):
        return next(cancelled_values)

    async def update_task(task_id, **kwargs):
        updates.append((task_id, kwargs))

    monkeypatch.setattr(query_runtime, "is_query_task_cancelled", is_cancelled)
    monkeypatch.setattr(query_runtime, "update_query_task", update_task)

    asyncio.run(query_runtime.run_sql_batch_query_task("task_cancelled", {"request_payload": "{}"}))
    assert updates == []

    asyncio.run(query_runtime.run_sql_batch_query_task(
        "task_empty",
        {"request_payload": '{"sql": ["  "], "operator_name": "张三", "datasource_id": "ds_a"}'},
    ))
    assert updates[0][1]["status"] == "failed"
    assert updates[0][1]["error_message"] == "SQL 批次为空"


def test_run_sql_batch_query_task_cancel_before_statement_marks_remaining(monkeypatch):
    cancelled_values = iter([False, True])
    updates = []

    async def is_cancelled(_task_id):
        return next(cancelled_values)

    async def update_task(task_id, **kwargs):
        updates.append((task_id, kwargs))

    async def create_executions(_task_id, _request, operator_name=None):
        return {"m1": "exec_1"}

    async def mark_remaining(task_id, statement_results, execution_maps, start_index):
        updates.append((task_id, {"marked_from": start_index}))

    monkeypatch.setattr(query_runtime, "is_query_task_cancelled", is_cancelled)
    monkeypatch.setattr(query_runtime, "update_query_task", update_task)
    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", create_executions)
    monkeypatch.setattr(query_runtime, "mark_sql_batch_remaining_cancelled", mark_remaining)

    asyncio.run(query_runtime.run_sql_batch_query_task(
        "task_1",
        {"request_payload": '{"sql": ["select 1"], "operator_name": "张三", "datasource_id": "ds_a"}'},
    ))
    assert {"marked_from": 0} in [item[1] for item in updates]


def test_run_sql_batch_query_task_statement_exception_marks_failed(monkeypatch):
    updates = []

    async def is_cancelled(_task_id):
        return False

    async def update_task(task_id, **kwargs):
        updates.append((task_id, kwargs))

    async def create_executions(_task_id, _request, operator_name=None):
        return {"m1": "exec_1"}

    async def execute_raises(*_args, **_kwargs):
        raise RuntimeError("statement boom")

    monkeypatch.setattr(query_runtime, "is_query_task_cancelled", is_cancelled)
    monkeypatch.setattr(query_runtime, "update_query_task", update_task)
    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", create_executions)
    monkeypatch.setattr(query_runtime, "build_execute_sql_result_data", execute_raises)

    asyncio.run(query_runtime.run_sql_batch_query_task(
        "task_1",
        {"request_payload": '{"sql": ["select 1"], "operator_name": "张三", "datasource_id": "ds_a"}'},
    ))

    final_update = updates[-1][1]
    assert final_update["status"] == "failed"
    statement = final_update["result_payload"]["statement_results"][0]
    assert statement["status"] == "failed"
    assert "statement boom" in statement["error"]


def test_mark_sql_batch_precreation_failed_survives_execution_update_error(monkeypatch):
    async def update_execution_raises(_execution_id, **_kwargs):
        raise RuntimeError("update exec failed")

    monkeypatch.setattr(query_runtime, "update_query_execution", update_execution_raises)

    statement_results = [
        {"statement_index": 0, "sql": "select 1", "status": "succeeded", "results": []},
        {"statement_index": 1, "sql": "select 2", "status": "pending", "results": []},
    ]

    asyncio.run(query_runtime.mark_sql_batch_precreation_failed(
        statement_results,
        {0: {"m1": "exec_1"}},
        1,
        "创建执行记录失败",
    ))
    assert statement_results[1]["status"] == "failed"


def test_execute_agent_sql_prevalidation_error_returns_failed(monkeypatch):
    class Manager:
        async def get_datasource_async(self, datasource_id):
            return {"id": datasource_id}

    async def prepare_raises(*_args, **_kwargs):
        raise ValueError("SQL 预校验失败")

    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: Manager())
    monkeypatch.setattr(query_runtime, "prepare_sql_request_for_execution", prepare_raises)

    result = asyncio.run(query_runtime.execute_agent_sql("select 1", "ds_a"))
    assert result["status"] == "failed"
    assert result["error"] == "SQL 预校验失败"


def test_execute_agent_sql_task_partial_failure_and_update_error(monkeypatch):
    class Manager:
        async def get_datasource_async(self, datasource_id):
            return {"id": datasource_id}

    async def create_task_ok(*_args, **_kwargs):
        return {"task_id": "task_1"}

    async def create_executions_raises(*_args, **_kwargs):
        raise RuntimeError("executions boom")

    update_calls = []

    async def update_task(_task_id, **kwargs):
        update_calls.append(kwargs)
        if kwargs.get("status") == "failed":
            raise RuntimeError("update task failed")

    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: Manager())
    monkeypatch.setattr(query_runtime, "create_task_for_execute_request", create_task_ok)
    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", create_executions_raises)
    monkeypatch.setattr(query_runtime, "update_query_task", update_task)

    result = asyncio.run(query_runtime.execute_agent_sql("select 1", "ds_a"))
    assert result["status"] == "failed"
    assert result["error"] == "executions boom"

    class AuthorizedRaisesManager(Manager):
        async def get_authorized_execution_datasources_async(self, *_args, **_kwargs):
            raise RuntimeError("authorized boom")

    async def create_executions_ok(*_args, **_kwargs):
        return {"m1": "exec_1"}

    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: AuthorizedRaisesManager())
    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", create_executions_ok)

    generic_result = asyncio.run(query_runtime.execute_agent_sql("select 1", "ds_a"))
    assert generic_result["status"] == "failed"
    assert "authorized boom" in generic_result["error"]
    assert update_calls[-1]["status"] == "failed"


def test_create_query_task_for_user_rejects_unknown_query_type():
    body = QueryTaskCreateRequest(datasource_id="ds_a", sql=["select 1"]).model_copy(update={"query_type": "agent"})
    service = _make_service()

    with pytest.raises(ValueError, match="不支持的查询类型"):
        asyncio.run(service.create_query_task_for_user(body, user_id="u1", operator_name="张三"))


def test_build_sql_task_results_falls_back_to_execution_views_without_results_key():
    results = QueryTaskService.build_sql_task_results(
        {"sql": "select 1"},
        [{
            "execution_id": "exec_1",
            "member_datasource_id": "m1",
            "member_name": "成员1",
            "sql_status": "succeeded",
            "sql_text": "select 1",
            "execution_time_ms": 12,
            "result_row_count": 3,
        }],
    )

    assert results[0]["member_datasource_id"] == "m1"
    assert results[0]["execution_time_ms"] == 12
    assert results[0]["total_count"] == 3


def test_resolve_sql_task_execution_view_positional_fallback_out_of_range():
    assert QueryTaskService.resolve_sql_task_execution_view({"member_datasource_id": ""}, 2, [], {}) is None
    views = [{"execution_id": "exec_0"}, {"execution_id": "exec_1"}]
    assert QueryTaskService.resolve_sql_task_execution_view({}, 5, views, {}) is None


def test_build_sql_batch_statement_results_skips_invalid_statement_index(monkeypatch=None):
    fetched = []

    async def fake_fetch(remote_task_id, source_type, user_id=None, username=None):
        fetched.append(remote_task_id)
        return {"status": "succeeded", "columns": ["id"], "data": [{"id": 1}], "total_count": 1}

    service = _make_service(fetch_http_query_task_result=fake_fetch)
    response_data = {
        "status": "running",
        "request_payload": {"user_id": "u1", "operator_name": "张三"},
        "result_payload": {"statement_results": [{"sql": "select 1"}]},
        "remote_tasks": [
            {"remote_task_id": "rt_bad", "statement_index": None, "member_datasource_id": "m1", "member_name": "成员1"},
            {"remote_task_id": "rt_range", "statement_index": 9, "member_datasource_id": "m2", "member_name": "成员2"},
            {"remote_task_id": "rt_ok", "statement_index": 0, "member_datasource_id": "m3", "member_name": "成员3"},
        ],
    }

    statements = asyncio.run(service.build_sql_batch_statement_results(response_data, []))
    assert fetched == ["rt_ok"]
    assert statements[0]["results"][0]["member_datasource_id"] == "m3"


def test_build_sql_batch_statement_results_marks_cancelled_execution():
    async def fake_fetch(remote_task_id, source_type, user_id=None, username=None):
        return {"status": "running", "columns": [], "data": [], "total_count": 0}

    service = _make_service(fetch_http_query_task_result=fake_fetch)
    response_data = {
        "status": "running",
        "request_payload": {"user_id": "u1", "operator_name": "张三"},
        "result_payload": {"statement_results": [{"sql": "select 1"}]},
        "remote_tasks": [
            {"remote_task_id": "rt_1", "statement_index": 0, "member_datasource_id": "m1", "member_name": "成员1"},
        ],
    }
    executions = [{
        "remote_task_id": "rt_1",
        "sql_status": "kill_failed",
        "error_message": "停止执行失败",
        "execution_time_ms": 8,
    }]

    statements = asyncio.run(service.build_sql_batch_statement_results(response_data, executions))
    member = statements[0]["results"][0]
    assert member["status"] == "failed"
    assert member["error"] == "停止执行失败"


def test_build_sql_batch_statement_results_marks_pending_cancelled_when_task_cancelled():
    async def fake_fetch(remote_task_id, source_type, user_id=None, username=None):
        return {"status": "running", "columns": [], "data": [], "total_count": 0}

    service = _make_service(fetch_http_query_task_result=fake_fetch)
    response_data = {
        "status": "cancelled",
        "request_payload": {"user_id": "u1", "operator_name": "张三"},
        "result_payload": {"statement_results": [
            {"sql": "select 1", "results": [{"member_datasource_id": "m1", "status": "running"}]},
            {"sql": "select 2", "status": "pending"},
        ]},
        "remote_tasks": [
            {"remote_task_id": "rt_1", "statement_index": 0, "member_datasource_id": "m1", "member_name": "成员1"},
        ],
    }

    statements = asyncio.run(service.build_sql_batch_statement_results(response_data, []))
    assert statements[0]["results"][0]["status"] == "cancelled"
    assert statements[0]["results"][0]["error"] == "已人工取消"
    assert statements[1]["status"] == "cancelled"


def test_resolve_sql_batch_status_branch_matrix():
    resolve = QueryTaskService.resolve_sql_batch_status

    status, _progress, error = resolve([{"status": "failed", "error": "boom"}], fallback_status="failed", fallback_progress_text="执行失败", fallback_error_message="boom")
    assert (status, error) == ("failed", "boom")

    assert resolve([{"status": "pending"}], fallback_status="running")[0] == "running"

    status, progress, error = resolve([{"status": "cancelled"}, {"status": "cancelled"}], fallback_status="running")
    assert (status, progress, error) == ("cancelled", "已停止", "已人工取消")

    status, _progress, error = resolve([{"status": "succeeded"}, {"status": "cancelled"}], fallback_status="running")
    assert (status, error) == ("partial_success", "部分 SQL 已停止")

    status, _progress, error = resolve([{"status": "succeeded"}, {"status": "failed", "error": "e1"}], fallback_status="running")
    assert (status, error) == ("partial_success", "部分 SQL 执行失败")

    status, progress, error = resolve([{"status": "succeeded"}], fallback_status="running")
    assert (status, progress, error) == ("succeeded", "执行完成", None)

    status, _progress, error = resolve([{"status": "failed", "error": "e1"}, {"status": "failed", "error": "e2"}], fallback_status="running")
    assert (status, error) == ("failed", "e1；e2")


def test_resolve_live_error_message_returns_none_for_running_or_succeeded():
    assert QueryTaskService.resolve_live_error_message("running", [{"status": "failed", "error": "boom"}]) is None
    assert QueryTaskService.resolve_live_error_message("failed", [{"status": "succeeded"}]) is None


def test_dispatch_query_task_routes_sql_batch_and_rejects_unknown():
    routed = []

    async def run_sql_batch(task_id, task):
        routed.append(("sql_batch", task_id))

    async def run_sql(task_id, task):
        routed.append(("sql", task_id))

    service = _make_service()
    service.run_sql_batch_query_task = run_sql_batch
    service.run_sql_query_task = run_sql

    asyncio.run(service.dispatch_query_task("task_1", {"task_type": "sql_batch"}))
    asyncio.run(service.dispatch_query_task("task_2", {"task_type": "sql"}))
    with pytest.raises(ValueError, match="不支持的任务类型"):
        asyncio.run(service.dispatch_query_task("task_3", {"task_type": "unknown"}))

    assert routed == [("sql_batch", "task_1"), ("sql", "task_2")]
