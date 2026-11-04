"""SQL 查询链路修复：缓存授权域、缓存命中耗时口径、启动遗留任务对账。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from apps.chat_data_app.services.sql_run import cache as query_cache
from apps.chat_data_app.services.sql_run import runtime as query_runtime


def test_http_cache_identity_isolated_by_user_and_org():
    target = {
        "datasource_id": "ds_a",
        "member_datasource_id": "m1",
        "query_executor_type": "http",
        "source_type": "hive",
        "org_code": "org_a",
    }
    user_a = query_cache.build_sql_result_cache_identity(target, "select 1", user_id="user_a")
    user_b = query_cache.build_sql_result_cache_identity(target, "select 1", user_id="user_b")
    same_user = query_cache.build_sql_result_cache_identity(target, "select 1", user_id="user_a")

    assert user_a["cache_key"] != user_b["cache_key"]
    assert user_a["cache_key"] == same_user["cache_key"]


def test_direct_cache_identity_remains_shared_across_users():
    target = {
        "datasource_id": "ds_a",
        "member_datasource_id": "m1",
        "query_executor_type": "direct",
        "source_type": "hive",
    }
    user_a = query_cache.build_sql_result_cache_identity(target, "select 1", user_id="user_a")
    user_b = query_cache.build_sql_result_cache_identity(target, "select 1", user_id="user_b")

    assert user_a["cache_key"] == user_b["cache_key"]


def test_cache_hit_keeps_original_execution_time(monkeypatch):
    item = SimpleNamespace(
        create_time=datetime.now() - timedelta(seconds=3),
        expire_time=datetime.now() + timedelta(seconds=57),
    )
    payload = {
        "status": "succeeded",
        "columns": ["id"],
        "data": [{"id": 1}],
        "total_count": 1,
        "execution_time_ms": 812,
    }

    async def fake_read(_cache_key, *, now):
        return item, dict(payload)

    monkeypatch.setattr(query_cache.sql_result_cache_dao, "read_and_touch_cache", fake_read)

    result = asyncio.run(query_cache.get_sql_result_cache({"cache_key": "k1"}))

    assert result is not None
    assert result["cache_hit"] is True
    assert result["execution_time_ms"] == 812
    assert isinstance(result["cache_read_ms"], int)


def test_startup_reconciliation_only_interrupts_stale_heartbeat_tasks(monkeypatch):
    calls = {}

    async def fake_list(*, stale_before):
        calls["stale_before"] = stale_before
        return ["task-1", "task-2"]

    async def fake_interrupt(task_ids, *, error_message, progress_text):
        calls["interrupt"] = (task_ids, error_message, progress_text)
        return 2

    async def fake_executions(*, error_message, task_ids):
        calls["executions"] = (error_message, task_ids)
        return 3

    monkeypatch.setattr(query_runtime.query_task_dao, "list_stale_unfinished_tasks", fake_list)
    monkeypatch.setattr(query_runtime.query_task_dao, "interrupt_tasks", fake_interrupt)
    monkeypatch.setattr(query_runtime.query_execution_dao, "interrupt_unfinished_executions", fake_executions)

    interrupted = asyncio.run(query_runtime.interrupt_stale_query_tasks())

    assert interrupted == 2
    # 只清理心跳早于阈值的任务：不能按“早于进程启动时间”误杀其他 worker 的在途任务
    assert calls["stale_before"] < datetime.now()
    assert calls["interrupt"][0] == ["task-1", "task-2"]
    assert "服务重启" in calls["interrupt"][1]
    assert calls["executions"][1] == ["task-1", "task-2"]


def test_agent_sql_execution_passes_query_task_id(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return {"status": "succeeded", "columns": [], "data": [], "total_count": 0}

    class FakeDatasourceManager:
        async def get_authorized_execution_datasources_async(self, *args, **kwargs):
            return [{"member_datasource_id": "m1"}]

    async def fake_finalize(*args, **kwargs):
        return None

    monkeypatch.setattr(query_runtime, "execute_sql_for_member", fake_execute)
    monkeypatch.setattr(query_runtime, "get_runtime_setting", lambda _key: asyncio.sleep(0, result=100))
    monkeypatch.setattr(query_runtime, "build_persistent_query_task_result_for_request", fake_finalize)
    monkeypatch.setattr(query_runtime, "build_query_result_payload", lambda **kwargs: kwargs)
    monkeypatch.setattr(query_runtime, "_finalize_agent_sql_task", fake_finalize)

    asyncio.run(
        query_runtime._run_agent_sql_execution(
            None,
            "select 1",
            datasource_id="ds_a",
            member_datasource_ids=None,
            user_id="u1",
            operator_name="用户1",
            task_info={"task_id": "task-agent"},
            execution_map={},
            ds_manager=FakeDatasourceManager(),
        )
    )

    assert captured["query_task_id"] == "task-agent"


def test_attach_error_source_marks_which_layer_failed():
    from apps.chat_data_app.services.sql_run.runtime import _attach_error_source

    http_failure = _attach_error_source({"status": "failed", "error": "TExecuteStatementResp(...)"}, True)
    assert http_failure["error_source"] == "remote_http"

    direct_failure = _attach_error_source({"status": "failed", "error": "(1146, ...)"}, False)
    assert direct_failure["error_source"] == "datasource"

    cancelled = _attach_error_source({"status": "cancelled", "error": "已人工取消"}, True)
    assert cancelled["error_source"] == "service"

    # 执行器已标注的错误来源不被覆盖
    marked = _attach_error_source({"status": "failed", "error": "SQL 语法错误", "error_source": "service"}, True)
    assert marked["error_source"] == "service"

    succeeded = _attach_error_source({"status": "succeeded", "data": []}, True)
    assert "error_source" not in succeeded
