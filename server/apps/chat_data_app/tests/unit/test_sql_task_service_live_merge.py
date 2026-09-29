"""SQL 任务轮询链路回归测试：单远端异常隔离 + 本地终态仲裁。"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.services.sql_run.task_service import QueryTaskService


def _build_service(fetch_impl):
    return QueryTaskService(
        query_history_service=SimpleNamespace(),
        datasource_manager_factory=lambda: None,
        fetch_http_query_task_result=fetch_impl,
        stop_http_query_task=None,
        kill_mysql_query=None,
    )


def _remote_tasks():
    return [
        {"remote_task_id": "t-bad", "source_type": "hive", "member_datasource_id": "m1", "member_name": "M1"},
        {"remote_task_id": "t-ok", "source_type": "hive", "member_datasource_id": "m2", "member_name": "M2"},
    ]


def _succeeded_remote_result():
    return {
        "status": "succeeded",
        "success": True,
        "columns": ["a"],
        "data": [[1]],
        "total_count": 1,
        "error": None,
    }


@pytest.mark.asyncio
async def test_fetch_remote_member_results_isolates_single_remote_failure():
    """单个远端状态接口抛异常只降级该成员，其余成员正常返回，整体不炸。"""
    seen_ids = []

    async def fake_fetch(remote_task_id, source_type="", user_id=None, username=None):
        seen_ids.append(remote_task_id)
        if remote_task_id == "t-bad":
            raise RuntimeError("远端接口 502")
        return _succeeded_remote_result()

    service = _build_service(fake_fetch)
    response_data = {"request_payload": {"user_id": "u1", "operator_name": "op"}}
    executions = [{"member_datasource_id": "m1", "sql_status": "running"}]

    results = await service.fetch_remote_member_results(response_data, _remote_tasks(), executions)

    assert [r["status"] for r in results] == ["running", "succeeded"]
    assert "远端状态查询失败" in results[0]["error"]
    assert results[1]["total_count"] == 1


@pytest.mark.asyncio
async def test_degraded_remote_result_keeps_local_failed_status():
    """远端异常降级时，本地已终态 failed 的成员沿用 failed + 本地错误信息。"""
    degraded = QueryTaskService._degraded_remote_result(
        RuntimeError("boom"),
        {"sql_status": "failed", "error_message": "SQL 语法错误"},
    )
    assert degraded["status"] == "failed"
    assert degraded["error"] == "SQL 语法错误"

    fallback = QueryTaskService._degraded_remote_result(RuntimeError("boom"), None)
    assert fallback["status"] == "running"


@pytest.mark.asyncio
async def test_degraded_remote_result_normalizes_local_only_status():
    """降级结果只输出契约内状态：本地专属的 kill_failed 归一为 failed。"""
    degraded = QueryTaskService._degraded_remote_result(
        RuntimeError("boom"),
        {"sql_status": "kill_failed", "error_message": "停止执行失败"},
    )
    assert degraded["status"] == "failed"
    assert degraded["error"] == "停止执行失败"

    for raw_status, expected in (("pending", "pending"), ("running", "running"), ("cancelled", "cancelled")):
        assert (
            QueryTaskService._degraded_remote_result(RuntimeError("boom"), {"sql_status": raw_status})["status"]
            == expected
        )


@pytest.mark.asyncio
async def test_degraded_remote_result_of_cancelled_member_keeps_kill_failed():
    """取消语义不受降级归一影响：本地 kill_failed 成员仍按取消分支输出 kill_failed。"""

    async def fake_fetch(remote_task_id, source_type="", user_id=None, username=None):
        raise RuntimeError("远端接口 502")

    service = _build_service(fake_fetch)
    response_data = {"request_payload": {"user_id": "u1", "operator_name": "op"}}
    executions = [{"member_datasource_id": "m1", "sql_status": "kill_failed", "error_message": "停止执行失败"}]

    results = await service.fetch_remote_member_results(
        response_data,
        [{"remote_task_id": "t-1", "source_type": "hive", "member_datasource_id": "m1", "member_name": "M1"}],
        executions,
    )

    assert results[0]["status"] == "kill_failed"
    assert results[0]["error"] == "停止执行失败"


@pytest.mark.asyncio
async def test_live_merge_local_failed_member_not_resurrected():
    """本地已判 failed 的成员，远端返回 succeeded 也不能把状态覆盖回成功。"""

    async def fake_fetch(remote_task_id, source_type="", user_id=None, username=None):
        return _succeeded_remote_result()

    service = _build_service(fake_fetch)
    response_data = {"request_payload": {"user_id": "u1", "operator_name": "op"}, "remote_tasks": ["t-ok"]}
    local_execution = {"member_datasource_id": "m2", "sql_status": "failed", "error_message": "行数超限"}

    merged = service.build_remote_member_result(
        response_data,
        {"remote_task_id": "t-ok", "source_type": "hive", "member_datasource_id": "m2", "member_name": "M2"},
        _succeeded_remote_result(),
        local_execution,
        None,
    )

    assert merged["status"] == "failed"
    assert merged["error"] == "行数超限"


@pytest.mark.asyncio
async def test_batch_remote_member_result_local_failed_not_resurrected():
    """sql_batch 路径同样以本地 failed 终态仲裁。"""

    async def fake_fetch(remote_task_id, source_type="", user_id=None, username=None):
        return _succeeded_remote_result()

    service = _build_service(fake_fetch)
    member_result = service._build_remote_member_result(
        {"remote_task_id": "t-ok", "member_datasource_id": "m2", "member_name": "M2"},
        {"sql": "SELECT 1"},
        _succeeded_remote_result(),
        {"remote_task_id": "t-ok", "sql_status": "failed", "error_message": "执行失败"},
    )

    assert member_result["status"] == "failed"
    assert member_result["error"] == "执行失败"


@pytest.mark.asyncio
async def test_live_merge_remote_status_wins_when_local_not_terminal():
    """本地非终态（如 running）时仍以远端实时状态为准，行为不回退。"""

    async def fake_fetch(remote_task_id, source_type="", user_id=None, username=None):
        return _succeeded_remote_result()

    service = _build_service(fake_fetch)
    response_data = {"request_payload": {"user_id": "u1", "operator_name": "op"}, "remote_tasks": ["t-ok"]}

    merged = service.build_remote_member_result(
        response_data,
        {"remote_task_id": "t-ok", "source_type": "hive", "member_datasource_id": "m2", "member_name": "M2"},
        _succeeded_remote_result(),
        {"member_datasource_id": "m2", "sql_status": "running"},
        None,
    )

    assert merged["status"] == "succeeded"
    assert merged["error"] is None
