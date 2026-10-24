"""sql_run 任务/历史/运行时剩余分支：任务视图、取消收口与查询任务响应。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from apps.chat_data_app.schema import ExecuteSqlRequest
from apps.chat_data_app.services.sql_run import runtime as query_runtime
from apps.chat_data_app.services.sql_run.history_service import QueryHistoryService
from apps.chat_data_app.services.sql_run.task_service import QueryTaskService


def _task_service() -> QueryTaskService:
    async def fake_callable(*_args, **_kwargs):
        return {}

    return QueryTaskService(
        query_history_service=QueryHistoryService(dao=SimpleNamespace()),  # type: ignore[arg-type]
        datasource_manager_factory=lambda: SimpleNamespace(),
        fetch_http_query_task_result=fake_callable,
        stop_http_query_task=fake_callable,
        kill_mysql_query=fake_callable,
    )


def _task_response_data() -> dict:
    return {
        "task_id": "task_1",
        "task_type": "query",
        "datasource_id": "ds_a",
        "query_type": "sql",
        "status": "succeeded",
        "progress_text": "完成",
        "remote_tasks": [],
        "error_message": None,
        "request_payload": {"user_id": "u1"},
        "result_payload": {"results": []},
        "started_time": None,
        "finished_time": None,
        "create_time": None,
        "update_time": None,
    }


def test_get_query_task_for_user_returns_generic_task_shape(monkeypatch):
    """非 sql / sql_batch 任务走 executions + result 兜底返回分支。"""
    executions = [
        {
            "execution_id": "exec_1",
            "user_id": "u1",
            "member_datasource_id": "m1",
            "sql_status": "succeeded",
        }
    ]

    async def fake_get_task(_task_id):
        return {"task_id": "task_1"}

    async def fake_get_executions(_task_id):
        return executions

    monkeypatch.setattr(query_runtime, "get_query_task", fake_get_task)
    monkeypatch.setattr(query_runtime, "build_query_task_response_data", lambda _data: _task_response_data())
    monkeypatch.setattr(query_runtime, "get_query_executions", fake_get_executions)
    service = _task_service()

    response = asyncio.run(service.get_query_task_for_user("task_1", user_id="u1"))

    assert response["task_type"] == "query"
    assert response["result"] == {"results": []}
    assert response["executions"][0]["execution_id"] == "exec_1"
    assert response["executions"][0]["sql_status"] == "succeeded"
    assert "results" not in response


def test_build_execution_result_view_without_sql_status_skips_status():
    """执行记录没有 sql_status 时不写入 status 字段。"""
    view = QueryTaskService._build_execution_result_view({"execution_id": "exec_1", "query": "select 1"})

    assert view["execution_id"] == "exec_1"
    assert view["sql"] == "select 1"
    assert "status" not in view


def test_resolve_sql_task_execution_view_falls_back_by_index():
    """多执行视图且成员 ID 缺失时按位置取执行记录。"""
    views = [{"execution_id": "exec_1"}, {"execution_id": "exec_2"}]

    assert QueryTaskService.resolve_sql_task_execution_view({}, 1, views, {}) == {"execution_id": "exec_2"}
    assert QueryTaskService.resolve_sql_task_execution_view({}, 5, views, {}) is None


def test_merge_execution_view_field_keeps_existing_status():
    """已有 status 时不被执行视图覆盖。"""
    merged = {"status": "succeeded"}

    QueryTaskService._merge_execution_view_field(merged, "status", "failed")

    assert merged == {"status": "succeeded"}


def test_cancel_pending_member_result_marks_running_member_cancelled():
    """在途成员结果被标记为已人工取消。"""
    cancelled = QueryTaskService._cancel_pending_member_result({"status": "running", "data": [1]})

    assert cancelled["status"] == "cancelled"
    assert cancelled["error"] == "已人工取消"
    assert cancelled["data"] == [1]
    assert QueryTaskService._cancel_pending_member_result({"status": "succeeded"})["status"] == "succeeded"


def test_mark_pending_sql_batch_statements_cancelled_without_member_results():
    """语句无成员结果但仍在途时，直接标记语句本身已停止。"""
    statements = [{"status": "pending", "sql": "select 1"}, {"status": "succeeded", "sql": "select 2"}]

    result = QueryTaskService.mark_pending_sql_batch_statements_cancelled(statements)

    assert result[0]["status"] == "cancelled"
    assert result[0]["error"] == "已人工取消"
    assert result[1]["status"] == "succeeded"


def test_build_execution_map_by_member_skips_blank_member_id():
    """成员 ID 为空的执行记录不进入执行映射。"""
    execution_map = QueryTaskService.build_execution_map_by_member(
        [
            {"execution_id": "exec_0", "member_datasource_id": " "},
            {"execution_id": "exec_1", "member_datasource_id": "m1"},
        ]
    )

    assert list(execution_map) == ["m1"]
    assert execution_map["m1"]["execution_id"] == "exec_1"


def test_parse_history_time_end_of_day_expands_to_last_microsecond():
    """日期形式 + end_of_day 解析为当天最大时间。"""
    parsed = QueryHistoryService.parse_history_time("2024-01-02", end_of_day=True)

    assert parsed == datetime(2024, 1, 2, 23, 59, 59, 999999)
    assert QueryHistoryService.parse_history_time("2024-01-02") == datetime(2024, 1, 2)


def test_history_raw_status_maps_legacy_success_flag():
    """历史结果只有 success 旧字段时映射为 succeeded。"""
    execution = SimpleNamespace(sql_status="running")

    assert QueryHistoryService._history_raw_status({"success": True}, execution) == "succeeded"
    assert QueryHistoryService._history_raw_status({"success": False}, execution) == "failed"
    assert QueryHistoryService._history_raw_status({"status": "cancelled"}, execution) == "cancelled"
    assert QueryHistoryService._history_raw_status({}, execution) == "running"


def test_normalize_history_result_payload_returns_none_for_non_dict():
    """非字典结果直接返回 None，避免历史详情暴露脏数据。"""
    assert QueryHistoryService.normalize_history_result_payload(None, SimpleNamespace(sql_status=None), None) is None
    normalized = QueryHistoryService.normalize_history_result_payload(
        {"success": True, "columns": None, "data": None, "total_count": "bad"},
        SimpleNamespace(sql_status=None, result_row_count=None),
        None,
    )
    assert normalized is not None
    assert normalized["status"] == "succeeded"
    assert "success" not in normalized
    assert normalized["columns"] == []
    assert normalized["data"] == []
    assert normalized["total_count"] == 0


def test_group_cancel_results_by_task_skips_execution_without_task_id():
    """没有 task_id 的执行记录不进入批量停止聚合。"""
    rows = [(SimpleNamespace(task_id="  "), None)]

    assert QueryHistoryService.group_cancel_results_by_task(rows, [{"status": "cancelled"}]) == {}


def test_cancel_running_query_history_returns_empty_summary_without_rows():
    """没有在途执行时返回零值汇总，不触发任何停止回调。"""
    calls: list[str] = []

    class FakeDao:
        async def list_running_executions(self, **_kwargs):
            return []

    async def cancel_execution(*_args, **_kwargs):
        calls.append("cancel")
        return {}

    service = QueryHistoryService(dao=FakeDao())

    summary = asyncio.run(
        service.cancel_running_query_history(
            datasource_id="ds_a",
            user_id="u1",
            cancel_execution=cancel_execution,
            update_execution=cancel_execution,
            update_task=cancel_execution,
        )
    )

    assert summary == {"total": 0, "cancelled": 0, "failed": 0, "executions": []}
    assert calls == []


def test_update_cancel_task_statuses_skips_final_tasks():
    """已到达终态的任务不再回写停止状态。"""
    updates: list[tuple] = []

    class FakeDao:
        async def list_running_executions(self, **_kwargs):
            execution = SimpleNamespace(
                task_id="task_1",
                execution_id="exec_1",
                create_by_name="",
                update_by_name="",
            )
            task = SimpleNamespace(
                task_id="task_1",
                task_status="succeeded",
                create_by_name="",
                update_by_name="",
                request_payload="{}",
            )
            return [(execution, task)]

    async def cancel_execution(*_args, **_kwargs):
        return {"execution_id": "exec_1", "status": "cancelled"}

    async def update_execution(*_args, **_kwargs):
        return None

    async def update_task(*args, **kwargs):
        updates.append((args, kwargs))
        return None

    service = QueryHistoryService(dao=FakeDao())

    summary = asyncio.run(
        service.cancel_running_query_history(
            cancel_execution=cancel_execution,
            update_execution=update_execution,
            update_task=update_task,
        )
    )

    assert summary["cancelled"] == 1
    assert updates == []


def test_build_query_task_response_data_without_request_payload():
    """任务无 request_payload 时返回 None，不抛异常。"""
    response = query_runtime.build_query_task_response_data({"task_id": "task_1", "result_payload": '{"a": 1}'})

    assert response["task_id"] == "task_1"
    assert response["result_payload"] == {"a": 1}
    assert response["request_payload"] is None


def test_mark_sql_batch_remaining_cancelled_keeps_finished_statements(monkeypatch):
    """已完成语句不再改写，也不会触发任务结果回写。"""
    updates: list[tuple] = []

    async def fake_update_task(*args, **kwargs):
        updates.append((args, kwargs))

    monkeypatch.setattr(query_runtime, "update_query_task", fake_update_task)
    statements = [{"statement_index": 0, "status": "succeeded", "sql": "select 1"}]

    asyncio.run(query_runtime.mark_sql_batch_remaining_cancelled("task_1", statements))

    assert statements[0]["status"] == "succeeded"
    assert updates == []


def test_mark_sql_batch_precreation_failed_keeps_finished_statements(monkeypatch):
    """预创建失败只终态化在途语句，已完成语句保持不变。"""
    statements = [{"statement_index": 0, "status": "succeeded", "sql": "select 1"}]

    asyncio.run(query_runtime.mark_sql_batch_precreation_failed(statements, {}, 0, "创建失败"))

    assert statements[0] == {"statement_index": 0, "status": "succeeded", "sql": "select 1"}


def test_run_agent_sql_execution_without_task_returns_result(monkeypatch):
    """task_info 为空（任务创建失败）时只执行查询，不落库任务终态。"""
    finish_calls: list[dict] = []

    async def fake_setting(_key, _default=None):
        return 100

    async def fake_execute_member(**_kwargs):
        return {
            "status": "succeeded",
            "member_datasource_id": "m1",
            "sql": "select 1",
            "columns": ["id"],
            "data": [{"id": 1}],
            "total_count": 1,
        }

    class Manager:
        async def get_authorized_execution_datasources_async(self, *_args, **_kwargs):
            return [{"member_datasource_id": "m1", "member_name": "成员1"}]

    async def fake_finalize(task_info, final_result, persistent):
        finish_calls.append({"task_info": task_info})

    monkeypatch.setattr(query_runtime, "get_runtime_setting", fake_setting)
    monkeypatch.setattr(query_runtime, "execute_sql_for_member", fake_execute_member)
    monkeypatch.setattr(query_runtime, "_finalize_agent_sql_task", fake_finalize)

    result = asyncio.run(
        query_runtime._run_agent_sql_execution(
            ExecuteSqlRequest(sql="select 1", datasource_id="ds_a"),
            "select 1",
            datasource_id="ds_a",
            member_datasource_ids=None,
            user_id="u1",
            operator_name="用户一",
            task_info=None,
            execution_map={},
            ds_manager=Manager(),
        )
    )

    assert result["status"] == "succeeded"
    assert result["results"][0]["member_datasource_id"] == "m1"
    assert finish_calls == []


@pytest.mark.parametrize("statement_status", ["pending", "running"])
def test_mark_sql_batch_remaining_cancelled_marks_pending(monkeypatch, statement_status):
    """在途语句会改写为 cancelled 并回写执行记录。"""
    execution_updates: list[tuple] = []

    async def fake_update_execution(execution_id, **kwargs):
        execution_updates.append((execution_id, kwargs))

    async def fake_update_task(*_args, **_kwargs):
        return None

    monkeypatch.setattr(query_runtime, "update_query_execution", fake_update_execution)
    monkeypatch.setattr(query_runtime, "update_query_task", fake_update_task)
    statements = [{"statement_index": 0, "status": statement_status, "sql": "select 1"}]

    asyncio.run(query_runtime.mark_sql_batch_remaining_cancelled("task_1", statements, {0: {"m1": "exec_1"}}))

    assert statements[0]["status"] == "cancelled"
    assert execution_updates[0][0] == "exec_1"
    assert execution_updates[0][1]["sql_status"] == "cancelled"
    assert execution_updates[0][1]["error_message"] == "已人工取消"
