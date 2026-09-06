import asyncio
from types import SimpleNamespace

from apps.chat_data_app.services.sql_run.result import (
    build_query_result_payload,
    query_result_status,
    query_result_succeeded,
)
from apps.chat_data_app.services.sql_run.history_service import QueryHistoryService
from apps.chat_data_app.services.sql_run.task_service import QueryTaskService


def test_query_result_status_normalizes_legacy_success_and_cancelled_alias():
    assert query_result_status({"success": True}) == "succeeded"
    assert query_result_status({"status": " CANCELED "}) == "cancelled"
    assert query_result_status({"status": "running"}) == "running"
    assert query_result_succeeded({"status": "succeeded"}) is True
    assert query_result_succeeded({"status": "failed"}) is False


def test_build_query_result_payload_keeps_data_only_in_member_results():
    payload = build_query_result_payload(
        sql="select 1",
        member_results=[
            {
                "success": True,
                "member_datasource_id": "m1",
                "member_name": "机构1",
                "columns": ["a"],
                "data": [{"a": 1}],
                "total_count": 1,
                "execution_time_ms": 12,
            },
            {
                "status": "failed",
                "member_datasource_id": "m2",
                "error": "执行失败",
            },
        ],
    )

    assert payload["status"] == "succeeded"
    assert payload["total_count"] == 1
    assert payload["execution_time_ms"] == 12
    assert payload["error"] is None
    assert "data" not in payload
    assert "columns" not in payload
    assert payload["results"][0]["status"] == "succeeded"
    assert "success" not in payload["results"][0]


def test_build_query_result_payload_uses_member_errors_for_failed_result():
    payload = build_query_result_payload(
        sql="select 1",
        member_results=[
            {"status": "failed", "error": "错误A"},
            {"status": "cancelled", "error": "已停止"},
        ],
        fallback_error="兜底错误",
    )

    assert payload["status"] == "failed"
    assert payload["error"] == "错误A；已停止"
    assert payload["total_count"] == 0


def test_build_query_result_payload_running_wins_over_partial_success():
    """任一成员仍在运行时聚合为 running，即使已有成员成功。"""
    payload = build_query_result_payload(
        sql="select 1",
        member_results=[
            {
                "status": "succeeded",
                "member_datasource_id": "m1",
                "columns": ["a"],
                "data": [{"a": 1}],
                "total_count": 1,
            },
            {"status": "running", "member_datasource_id": "m2"},
        ],
    )

    assert payload["status"] == "running"
    assert payload["error"] == "部分执行对象仍在查询中"
    assert payload["total_count"] == 1


def test_build_query_result_payload_all_cancelled_reports_cancelled():
    """全部成员取消时聚合为 cancelled，error 聚合成员错误。"""
    payload = build_query_result_payload(
        sql="select 1",
        member_results=[
            {"status": "cancelled", "member_datasource_id": "m1", "error": "已停止"},
            {"status": "cancelled", "member_datasource_id": "m2", "error": ""},
        ],
    )

    assert payload["status"] == "cancelled"
    assert payload["error"] == "已停止"


def test_build_sql_task_results_merges_execution_metadata_and_normalizes_status():
    results = QueryTaskService.build_sql_task_results(
        {
            "sql": "select * from orders",
            "results": [{
                "member_datasource_id": "m1",
                "member_name": "杭州库",
                "success": True,
                "columns": ["id"],
                "data": [{"id": 1}],
                "total_count": 1,
                "execution_time_ms": 12,
                "error": None,
            }],
        },
        [{
            "execution_id": "exec_1",
            "member_datasource_id": "m1",
            "member_name": "杭州库",
            "executor_type": "direct",
            "sql_status": "succeeded",
            "remote_task_id": "",
            "db_connection_id": "conn_1",
            "query": "select * from orders",
            "started_time": "2026-07-16T10:00:00",
            "finished_time": "2026-07-16T10:00:01",
        }],
    )

    assert results == [{
        "member_datasource_id": "m1",
        "member_name": "杭州库",
        "status": "succeeded",
        "columns": ["id"],
        "data": [{"id": 1}],
        "total_count": 1,
        "execution_time_ms": 12,
        "error": None,
        "execution_id": "exec_1",
        "executor_type": "direct",
        "remote_task_id": "",
        "db_connection_id": "conn_1",
        "sql": "select * from orders",
        "started_time": "2026-07-16T10:00:00",
        "finished_time": "2026-07-16T10:00:01",
    }]
    assert "success" not in results[0]


def test_build_sql_task_results_uses_execution_rows_while_task_is_running():
    results = QueryTaskService.build_sql_task_results(None, [{
        "execution_id": "exec_1",
        "member_datasource_id": "m1",
        "member_name": "杭州库",
        "executor_type": "http",
        "sql_status": "running",
        "remote_task_id": "remote_1",
        "db_connection_id": "",
        "query": "select 1",
    }])

    assert results == [{
        "execution_id": "exec_1",
        "member_datasource_id": "m1",
        "member_name": "杭州库",
        "executor_type": "http",
        "remote_task_id": "remote_1",
        "db_connection_id": "",
        "status": "running",
        "sql": "select 1",
        "columns": [],
        "data": [],
        "total_count": 0,
        "error": None,
    }]


def test_build_sql_task_results_keeps_failed_execution_error_without_task_payload():
    results = QueryTaskService.build_sql_task_results(None, [{
        "execution_id": "exec_1",
        "member_datasource_id": "m1",
        "member_name": "机构1",
        "sql_status": "failed",
        "sql_text": "select * from orders limit1 100",
        "error_message": "SQL 语法错误",
    }])

    assert results == [{
        "execution_id": "exec_1",
        "member_datasource_id": "m1",
        "member_name": "机构1",
        "status": "failed",
        "sql": "select * from orders limit1 100",
        "columns": [],
        "data": [],
        "total_count": 0,
        "error": "SQL 语法错误",
    }]


def test_cancel_query_executions_continues_when_one_execution_stops_with_error():
    updates = []

    class HistoryService:
        async def cancel_query_execution_for_batch(self, execution, *, cancel_execution, update_execution, operator_name=None):
            try:
                return await cancel_execution(execution, operator_name=operator_name)
            except Exception:
                await update_execution(execution.execution_id, sql_status="kill_failed")
                return {"execution_id": execution.execution_id, "status": "kill_failed", "success": False}

    service = QueryTaskService(
        query_history_service=HistoryService(),
        datasource_manager_factory=lambda: None,
        fetch_http_query_task_result=lambda *_args, **_kwargs: None,
        stop_http_query_task=lambda *_args, **_kwargs: None,
        kill_mysql_query=lambda *_args, **_kwargs: None,
    )

    async def cancel_execution(execution, **_kwargs):
        if execution.execution_id == "exec_failed":
            raise RuntimeError("stop failed")
        return {"execution_id": execution.execution_id, "status": "cancelled", "success": True}

    async def update_execution(execution_id, **kwargs):
        updates.append((execution_id, kwargs))

    service.cancel_query_execution = cancel_execution
    service.update_query_execution = update_execution

    results = asyncio.run(service.cancel_query_executions([
        SimpleNamespace(execution_id="exec_failed"),
        SimpleNamespace(execution_id="exec_ok"),
    ]))

    assert [item["status"] for item in results] == ["kill_failed", "cancelled"]
    assert updates == [("exec_failed", {"sql_status": "kill_failed"})]


def test_build_sql_task_results_reads_sql_text_from_execution_rows():
    results = QueryTaskService.build_sql_task_results(None, [{
        "execution_id": "exec_1",
        "member_datasource_id": "m1",
        "member_name": "杭州库",
        "sql_status": "running",
        "sql_text": "select * from orders",
    }])

    assert results[0]["sql"] == "select * from orders"


def test_build_sql_task_results_uses_execution_result_payload_when_task_payload_missing():
    results = QueryTaskService.build_sql_task_results(None, [{
        "execution_id": "exec_1",
        "member_datasource_id": "m1",
        "member_name": "杭州库",
        "sql_status": "succeeded",
        "query": "select id from orders",
        "result_payload": {
            "status": "succeeded",
            "columns": ["id"],
            "data": [{"id": 1}],
            "total_count": 1,
            "execution_time_ms": 9,
            "error": None,
            "cache_hit": True,
            "cache_age_seconds": 3,
            "cache_expires_in_seconds": 30,
        },
    }])

    assert results[0]["columns"] == ["id"]
    assert results[0]["data"] == [{"id": 1}]
    assert results[0]["total_count"] == 1
    assert results[0]["execution_time_ms"] == 9
    assert results[0]["cache_hit"] is True


def test_build_sql_task_results_falls_back_to_position_when_member_id_is_missing():
    results = QueryTaskService.build_sql_task_results(
        {
            "sql": "select 1",
            "results": [{"columns": ["a"], "data": [{"a": 1}], "total_count": 1, "success": True}],
        },
        [{"execution_id": "exec_1", "sql_status": "succeeded", "member_name": "默认库"}],
    )

    assert results[0]["execution_id"] == "exec_1"
    assert results[0]["member_name"] == "默认库"
    assert results[0]["status"] == "succeeded"
    assert results[0]["sql"] == "select 1"


def test_build_sql_task_results_returns_none_without_payload_or_executions():
    assert QueryTaskService.build_sql_task_results(None, []) is None
    assert QueryTaskService.build_sql_task_results({"results": []}, []) == []
    assert QueryTaskService.build_sql_task_results({"results": {"bad": "shape"}}, []) == []


def test_normalize_history_result_payload_removes_legacy_success():
    class Execution:
        sql_status = "succeeded"
        result_row_count = None
        error_message = None

    payload = QueryHistoryService.normalize_history_result_payload(
        {
            "success": False,
            "columns": ["id"],
            "data": [{"id": 1}],
            "error": "执行失败",
        },
        Execution(),
        12,
    )

    assert payload == {
        "status": "failed",
        "columns": ["id"],
        "data": [{"id": 1}],
        "total_count": 1,
        "execution_time_ms": 12,
        "error": "执行失败",
    }
