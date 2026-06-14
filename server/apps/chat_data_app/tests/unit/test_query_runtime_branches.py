import asyncio
import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "test")

from apps.chat_data_app.schema import ExecuteSqlRequest
from apps.chat_data_app.services import query_runtime


def test_query_runtime_cache_identity_and_flags(monkeypatch):
    monkeypatch.setattr(query_runtime, "QUERY_CACHE_ENABLED", True)

    identity = query_runtime.build_sql_result_cache_identity(
        {
            "id": "ds_fallback",
            "member_datasource_id": "m1",
            "query_executor_type": "HTTP",
            "source_type": "Hive",
        },
        " select 1 ",
        datasource_id="ds_a",
    )
    fallback_identity = query_runtime.build_sql_result_cache_identity({"id": "ds_fallback"}, "select 1")

    assert query_runtime.is_http_execution_target({"query_executor_type": " HTTP "}) is True
    assert query_runtime.is_http_execution_target({"query_executor_type": "direct"}) is False
    assert query_runtime.is_http_remote_ref_cache(None) is False
    assert query_runtime.is_http_remote_ref_cache({"cache_type": "http_remote_ref", "remote_task_id": ""}) is False
    assert query_runtime.is_http_remote_ref_cache({"cache_type": "HTTP_REMOTE_REF", "remote_task_id": "rt1"}) is True
    assert query_runtime.normalize_sql_cache_text(" select 1 ") == "select 1"
    assert identity["datasource_id"] == "ds_a"
    assert identity["executor_type"] == "http"
    assert identity["source_type"] == "hive"
    assert identity["sql_text"] == " select 1 "
    assert fallback_identity["datasource_id"] == "ds_fallback"
    assert query_runtime.is_sql_result_cache_enabled(False) is True
    assert query_runtime.is_sql_result_cache_enabled(True) is False


def test_load_remote_tasks_and_query_task_response_branches():
    assert query_runtime.load_remote_tasks(None) == []
    assert query_runtime.load_remote_tasks("bad json") == []
    assert query_runtime.load_remote_tasks({"bad": True}) == []
    assert query_runtime.load_remote_tasks([
        "skip",
        {"member_datasource_id": "", "remote_task_id": "rt_skip"},
        {"member_datasource_id": "m1", "member_name": "成员1", "remote_task_id": "rt1", "source_type": ""},
    ]) == [{"member_datasource_id": "m1", "member_name": "成员1", "remote_task_id": "rt1", "source_type": "hive"}]

    response = query_runtime.build_query_task_response_data({
        "task_id": "task_a",
        "task_type": "sql",
        "datasource_id": "ds_a",
        "query_type": "sql",
        "status": "succeeded",
        "progress_text": "完成",
        "request_payload": "{bad",
        "result_payload": json.dumps({"success": True}),
        "remote_task_payload": json.dumps([{"member_datasource_id": "m1", "member_name": "成员1", "remote_task_id": "rt1"}]),
        "error_message": "",
        "started_time": "s",
        "finished_time": "f",
        "create_time": "c",
        "update_time": "u",
    })
    bad_result_response = query_runtime.build_query_task_response_data({
        "task_id": "task_b",
        "request_payload": json.dumps({"sql": "select 1"}),
        "result_payload": "{bad",
        "remote_task_payload": "bad",
    })

    assert response["request_payload"] is None
    assert response["result_payload"] == {"success": True}
    assert response["remote_tasks"][0]["source_type"] == "hive"
    assert bad_result_response["request_payload"] == {"sql": "select 1"}
    assert bad_result_response["result_payload"] is None
    assert bad_result_response["remote_tasks"] == []


def test_persistent_query_task_result_payload_filters_http_members():
    result_data = {
        "success": True,
        "sql": "select all",
        "columns": ["id"],
        "data": [{"id": 1}],
        "total_count": 2,
        "execution_time_ms": 30,
        "error": None,
        "results": [
            {"member_datasource_id": "http_1", "success": True, "columns": ["id"], "data": [{"id": 9}], "total_count": 1, "sql": "select http", "member_name": "HTTP"},
            {"member_datasource_id": "local_1", "success": False, "error": "本地失败", "columns": [], "data": [], "total_count": 0, "sql": "select local", "member_name": "本地"},
        ],
    }
    all_http = query_runtime.build_persistent_query_task_result_payload(result_data, {"http_1", "local_1"})
    only_local_failed = query_runtime.build_persistent_query_task_result_payload(result_data, {"http_1"})
    no_http = query_runtime.build_persistent_query_task_result_payload(result_data, set())

    assert all_http is None
    assert no_http is result_data
    assert only_local_failed["success"] is False
    assert only_local_failed["sql"] == "select local"
    assert only_local_failed["error"] == "本地失败"
    assert only_local_failed["results"][0]["member_datasource_id"] == "local_1"


def test_persistent_query_task_result_payload_local_success_and_error_fallback():
    result_data = {
        "error": "整体失败",
        "results": [
            {"member_datasource_id": "local_1", "success": False, "error": "", "columns": [], "data": [], "total_count": 0},
            {"member_datasource_id": "local_2", "success": True, "columns": ["id"], "data": [{"id": 2}], "total_count": 1, "execution_time_ms": 7, "member_name": "本地2", "cache_hit": True, "cache_age_seconds": 10, "cache_expires_in_seconds": 50},
        ],
    }
    failed_without_error = query_runtime.build_persistent_query_task_result_payload(
        {"error": "整体失败", "results": [{"member_datasource_id": "local_1", "success": False, "error": ""}]},
        {"http_1"},
    )
    success = query_runtime.build_persistent_query_task_result_payload(result_data, {"http_1"})

    assert failed_without_error["error"] == "整体失败"
    assert success["success"] is True
    assert success["columns"] == ["id"]
    assert success["cache_hit"] is True
    assert success["cache_age_seconds"] == 10


def test_runtime_task_operator_and_execution_id_helpers():
    assert query_runtime.get_task_operator_name({"request_payload": "{bad"}) == ""
    assert query_runtime.get_task_operator_name({"request_payload": json.dumps({"operator_name": "测试用户"})}) == "测试用户"
    assert query_runtime.get_execution_id_by_member({"m1": "ex1", "": "default"}, {"member_datasource_id": "m1"}) == "ex1"
    assert query_runtime.get_execution_id_by_member({"": "default"}, {"member_datasource_id": "missing"}) == "default"

    task = SimpleNamespace(
        task_id="task_a",
        task_type="sql",
        datasource_id="ds_a",
        query_type="agent",
        task_status="running",
        progress_text="执行中",
        request_payload="{}",
        result_payload="",
        remote_task_payload="[]",
        error_message="",
        started_time=datetime(2026, 6, 2, 10, 0, 0),
        finished_time=None,
        create_time=None,
        update_time=datetime(2026, 6, 2, 10, 1, 0),
        create_by="u1",
        update_by="u2",
        create_by_name="创建人",
        update_by_name="更新人",
    )
    data = query_runtime.build_query_task_runtime_data(task)
    assert data["started_time"] == "2026-06-02T10:00:00"
    assert data["create_time"] is None
    assert data["update_by_name"] == "更新人"


def test_prepare_sql_request_for_execution_branches(monkeypatch):
    normal = ExecuteSqlRequest(sql="select 1", datasource_id="ds_a")
    no_semantic_config = ExecuteSqlRequest(
        sql="select bad",
        datasource_id="ds_a",
        semantic_builder_config={"dimensions": []},
    )
    semantic_builder = ExecuteSqlRequest(
        sql="select bad",
        datasource_id="ds_a",
        semantic_config_id="cfg_a",
        semantic_builder_config={"dimensions": [{"semanticName": "城市"}]},
        builder_config={"tampered": True},
    )

    async def fake_compile_semantic_builder_sql(datasource_id, semantic_config_id, semantic_builder_config):
        assert datasource_id == "ds_a"
        assert semantic_config_id == "cfg_a"
        assert semantic_builder_config["dimensions"][0]["semanticName"] == "城市"
        return {"sql": "select city from orders"}

    monkeypatch.setattr(query_runtime, "compile_semantic_builder_sql", fake_compile_semantic_builder_sql)

    assert asyncio.run(query_runtime.prepare_sql_request_for_execution(normal)) is normal
    with pytest.raises(ValueError, match="缺少 semantic_config_id"):
        asyncio.run(query_runtime.prepare_sql_request_for_execution(no_semantic_config))

    prepared = asyncio.run(query_runtime.prepare_sql_request_for_execution(semantic_builder))
    assert prepared.sql == "select city from orders"
    assert prepared.builder_config is None
    assert prepared.semantic_builder_config == {"dimensions": [{"semanticName": "城市"}]}


def test_remote_ref_cache_result_branches(monkeypatch):
    missing_id = asyncio.run(query_runtime.fetch_http_remote_ref_cache_result({"source_type": "mysql"}))
    assert missing_id["_cache_ref_unavailable"] is True
    assert "缺少远端任务" in missing_id["error"]

    async def raise_fetch(*_args, **_kwargs):
        raise RuntimeError("remote failed")

    async def unavailable_fetch(*_args, **_kwargs):
        return {"success": False, "status": "failed", "error": "已过期", "columns": ["id"], "data": [{"id": 1}], "total_count": 1}

    async def success_fetch(*_args, **_kwargs):
        return {"success": True, "columns": ["id"], "data": [{"id": 1}], "total_count": 1, "execution_time_ms": 8}

    monkeypatch.setattr(query_runtime, "fetch_http_query_task_result", raise_fetch)
    raised = asyncio.run(query_runtime.fetch_http_remote_ref_cache_result({"remote_task_id": "rt1", "source_type": "hive"}))
    assert raised["_cache_ref_unavailable"] is True
    assert raised["error"] == "remote failed"

    monkeypatch.setattr(query_runtime, "fetch_http_query_task_result", unavailable_fetch)
    unavailable = asyncio.run(query_runtime.fetch_http_remote_ref_cache_result({"remote_task_id": "rt2"}))
    assert unavailable["_cache_ref_unavailable"] is True
    assert unavailable["status"] == "failed"
    assert unavailable["columns"] == ["id"]

    monkeypatch.setattr(query_runtime, "fetch_http_query_task_result", success_fetch)
    success = asyncio.run(query_runtime.fetch_http_remote_ref_cache_result({
        "remote_task_id": "rt3",
        "source_type": "mysql",
        "cache_age_seconds": 3,
        "cache_expires_in_seconds": 9,
    }))
    assert success["success"] is True
    assert success["remote_task_id"] == "rt3"
    assert success["cache_hit"] is True
    assert success["cache_age_seconds"] == 3


def test_build_persistent_query_task_result_for_request_branches(monkeypatch):
    request = ExecuteSqlRequest(sql="select 1", datasource_id="ds_a")
    result_data = {
        "success": True,
        "results": [
            {"member_datasource_id": "m1", "success": True, "columns": ["id"], "data": [{"id": 1}], "total_count": 1}
        ],
    }

    async def all_http_true(_request):
        return True

    async def all_http_raise(_request):
        raise RuntimeError("all http failed")

    async def member_ids_raise(_request):
        raise RuntimeError("member failed")

    async def member_ids_ok(_request):
        return {"http_1"}

    monkeypatch.setattr(query_runtime, "are_all_sql_execution_targets_http", all_http_true)
    assert asyncio.run(query_runtime.build_persistent_query_task_result_for_request(request, result_data)) is None

    monkeypatch.setattr(query_runtime, "are_all_sql_execution_targets_http", all_http_raise)
    monkeypatch.setattr(query_runtime, "get_http_execution_member_ids", member_ids_raise)
    fallback_all = asyncio.run(query_runtime.build_persistent_query_task_result_for_request(request, result_data))
    assert fallback_all == result_data

    monkeypatch.setattr(query_runtime, "get_http_execution_member_ids", member_ids_ok)
    filtered = asyncio.run(query_runtime.build_persistent_query_task_result_for_request(request, result_data))
    assert filtered["results"][0]["member_datasource_id"] == "m1"


def test_execution_target_queries_use_manager(monkeypatch):
    class Manager:
        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids):
            assert datasource_id == "ds_a"
            return [
                {"member_datasource_id": "m1", "query_executor_type": "http"},
                {"member_datasource_id": "m2", "query_executor_type": "direct"},
                {"member_datasource_id": "", "query_executor_type": "http"},
            ]

    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: Manager())
    request = ExecuteSqlRequest(sql="select 1", datasource_id="ds_a")

    assert asyncio.run(query_runtime.are_all_sql_execution_targets_http(request)) is False
    assert asyncio.run(query_runtime.get_http_execution_member_ids(request)) == {"m1", ""}


def test_execute_sql_for_member_cancelled_before_and_after_start(monkeypatch):
    target = {"member_datasource_id": "m1", "member_name": "成员1", "source_type": "mysql"}
    update_calls = []

    async def update_query_execution(execution_id, **kwargs):
        update_calls.append((execution_id, kwargs))

    monkeypatch.setattr(query_runtime, "update_query_execution", update_query_execution)
    monkeypatch.setattr(query_runtime, "get_executor", lambda _target: (_ for _ in ()).throw(AssertionError("executor should not run")))

    async def cancelled_status(_execution_id):
        return "cancelled"

    monkeypatch.setattr(query_runtime, "get_query_execution_status", cancelled_status)
    before_start = asyncio.run(query_runtime.execute_sql_for_member(
        execution_target=target,
        sql="select 1",
        limit=10,
        timeout=None,
        execution_id="ex_before",
    ))

    status_values = iter(["running", "cancelled"])

    async def cancelled_after_update(_execution_id):
        return next(status_values)

    monkeypatch.setattr(query_runtime, "get_query_execution_status", cancelled_after_update)
    after_start = asyncio.run(query_runtime.execute_sql_for_member(
        execution_target=target,
        sql="select 1",
        limit=10,
        timeout=None,
        execution_id="ex_after",
    ))

    assert before_start["error"] == "已人工取消"
    assert after_start["error"] == "已人工取消"
    assert update_calls == [("ex_after", {"sql_status": "running", "started_time": update_calls[0][1]["started_time"], "error_message": ""})]


def test_execute_sql_for_member_callbacks_skip_cache_and_post_execute_cancel(monkeypatch):
    target = {"member_datasource_id": "m1", "member_name": "成员1", "source_type": "mysql"}
    update_calls = []
    remote_tasks = []
    final_updates = []
    closed = []

    class CallbackExecutor:
        async def execute(
            self,
            sql,
            limit,
            timeout,
            user_id,
            username=None,
            remote_task_callback=None,
            db_connection_callback=None,
        ):
            await remote_task_callback({"remote_task_id": ""})
            await remote_task_callback({"remote_task_id": "remote_1", "source_type": "mysql"})
            await db_connection_callback({})
            await db_connection_callback({"db_connection_id": "conn_1"})
            return {
                "success": True,
                "columns": ["id"],
                "data": [{"id": 1}],
                "total_count": 1,
                "execution_time_ms": 5,
                "remote_task_id": "remote_1",
                "db_connection_id": "conn_1",
            }

        async def close(self):
            closed.append(True)

    async def update_query_execution(execution_id, **kwargs):
        update_calls.append((execution_id, kwargs))

    async def append_query_task_remote_task(task_id, remote_task):
        remote_tasks.append((task_id, remote_task))

    async def update_query_execution_from_result(execution_id, result, store_result_payload=True):
        final_updates.append((execution_id, result, store_result_payload))

    async def run_with_limit(_user_id, _execution_target, query_fn):
        return await query_fn()

    status_values = iter(["running", "running", "cancelled"])

    async def execution_status(_execution_id):
        return next(status_values)

    monkeypatch.setattr(query_runtime, "QUERY_CACHE_ENABLED", True)
    monkeypatch.setattr(query_runtime, "get_executor", lambda _target: CallbackExecutor())
    monkeypatch.setattr(query_runtime, "run_with_sql_user_datasource_limit", run_with_limit)
    monkeypatch.setattr(query_runtime, "get_query_execution_status", execution_status)
    monkeypatch.setattr(query_runtime, "update_query_execution", update_query_execution)
    monkeypatch.setattr(query_runtime, "append_query_task_remote_task", append_query_task_remote_task)
    monkeypatch.setattr(query_runtime, "update_query_execution_from_result", update_query_execution_from_result)

    result = asyncio.run(query_runtime.execute_sql_for_member(
        execution_target=target,
        sql="select 1",
        limit=10,
        timeout=None,
        user_id="user_1",
        username="测试用户",
        query_task_id="task_1",
        execution_id="ex_1",
        skip_query_cache=True,
        datasource_id="ds_a",
    ))

    assert result["success"] is False
    assert result["error"] == "已人工取消"
    assert result["sql"] == "select 1 LIMIT 10"
    assert remote_tasks == [("task_1", {
        "member_datasource_id": "m1",
        "member_name": "成员1",
        "remote_task_id": "remote_1",
        "source_type": "mysql",
    })]
    assert ("ex_1", {"remote_task_id": "remote_1"}) in update_calls
    assert ("ex_1", {"db_connection_id": "conn_1"}) in update_calls
    assert final_updates[0][0] == "ex_1"
    assert final_updates[0][1]["error"] == "已人工取消"
    assert final_updates[0][2] is True
    assert closed == [True]


def test_execute_sql_for_member_http_remote_ref_cache_hit(monkeypatch):
    target = {
        "member_datasource_id": "m_http",
        "member_name": "HTTP成员",
        "source_type": "hive",
        "query_executor_type": "http",
    }
    remote_tasks = []
    final_updates = []

    async def get_sql_result_cache(_cache_identity):
        return {
            "cache_type": "http_remote_ref",
            "remote_task_id": "remote_cached",
            "source_type": "hive",
            "cache_age_seconds": 3,
            "cache_expires_in_seconds": 9,
        }

    async def fetch_remote_ref(cached_result, user_id=None, username=None):
        assert user_id == "user_1"
        assert username == "测试用户"
        return {
            "success": True,
            "columns": ["id"],
            "data": [{"id": 7}],
            "total_count": 1,
            "execution_time_ms": 2,
            "remote_task_id": cached_result["remote_task_id"],
            "cache_hit": True,
            "cache_age_seconds": cached_result["cache_age_seconds"],
            "cache_expires_in_seconds": cached_result["cache_expires_in_seconds"],
        }

    async def append_query_task_remote_task(task_id, remote_task):
        remote_tasks.append((task_id, remote_task))

    async def update_query_execution_from_result(execution_id, result, store_result_payload=True):
        final_updates.append((execution_id, result, store_result_payload))

    async def running_status(_execution_id):
        return "running"

    async def update_query_execution(_execution_id, **_kwargs):
        return None

    monkeypatch.setattr(query_runtime, "QUERY_CACHE_ENABLED", True)
    monkeypatch.setattr(query_runtime, "get_sql_result_cache", get_sql_result_cache)
    monkeypatch.setattr(query_runtime, "fetch_http_remote_ref_cache_result", fetch_remote_ref)
    monkeypatch.setattr(query_runtime, "get_executor", lambda _target: (_ for _ in ()).throw(AssertionError("cache hit should not execute")))
    monkeypatch.setattr(query_runtime, "append_query_task_remote_task", append_query_task_remote_task)
    monkeypatch.setattr(query_runtime, "get_query_execution_status", running_status)
    monkeypatch.setattr(query_runtime, "update_query_execution", update_query_execution)
    monkeypatch.setattr(query_runtime, "update_query_execution_from_result", update_query_execution_from_result)

    result = asyncio.run(query_runtime.execute_sql_for_member(
        execution_target=target,
        sql="select 1",
        limit=10,
        timeout=None,
        user_id="user_1",
        username="测试用户",
        query_task_id="task_http",
        execution_id="ex_http",
        datasource_id="ds_a",
    ))

    assert result["success"] is True
    assert result["cache_hit"] is True
    assert result["remote_task_id"] == "remote_cached"
    assert remote_tasks == [("task_http", {
        "member_datasource_id": "m_http",
        "member_name": "HTTP成员",
        "remote_task_id": "remote_cached",
        "source_type": "hive",
    })]
    assert final_updates[0][2] is False


def test_execute_sql_for_member_cache_miss_and_ref_unavailable_paths(monkeypatch):
    execute_calls = []
    cache_writes = []
    closed = []

    class FakeExecutor:
        async def execute(
            self,
            sql,
            limit,
            timeout,
            user_id,
            username=None,
            remote_task_callback=None,
            db_connection_callback=None,
        ):
            execute_calls.append((sql, username))
            return {
                "success": True,
                "columns": ["id"],
                "data": [{"id": len(execute_calls)}],
                "total_count": 1,
                "execution_time_ms": 4,
                "remote_task_id": f"remote_{len(execute_calls)}",
                "source_type": "hive",
            }

        async def close(self):
            closed.append(True)

    async def run_with_limit(_user_id, _execution_target, query_fn):
        return await query_fn()

    async def running_status(_execution_id):
        return "running"

    async def update_query_execution(_execution_id, **_kwargs):
        return None

    async def update_query_execution_from_result(_execution_id, _result, store_result_payload=True):
        cache_writes.append(("final", store_result_payload))

    async def save_http_remote_ref_sql_result_cache(_cache_identity, _query_result, operator_id=None, operator_name=None):
        cache_writes.append(("http", operator_id, operator_name))

    async def save_sql_result_cache(_cache_identity, _query_result, operator_id=None, operator_name=None):
        cache_writes.append(("direct", operator_id, operator_name))

    monkeypatch.setattr(query_runtime, "QUERY_CACHE_ENABLED", True)
    monkeypatch.setattr(query_runtime, "get_executor", lambda _target: FakeExecutor())
    monkeypatch.setattr(query_runtime, "run_with_sql_user_datasource_limit", run_with_limit)
    monkeypatch.setattr(query_runtime, "get_query_execution_status", running_status)
    monkeypatch.setattr(query_runtime, "update_query_execution", update_query_execution)
    monkeypatch.setattr(query_runtime, "update_query_execution_from_result", update_query_execution_from_result)
    monkeypatch.setattr(query_runtime, "save_http_remote_ref_sql_result_cache", save_http_remote_ref_sql_result_cache)
    monkeypatch.setattr(query_runtime, "save_sql_result_cache", save_sql_result_cache)

    async def unavailable_ref(_cached_result, user_id=None, username=None):
        return {"_cache_ref_unavailable": True, "success": False, "status": "failed", "error": "远端结果已过期"}

    async def http_ref_cache(_cache_identity):
        return {"cache_type": "http_remote_ref", "remote_task_id": "remote_old", "source_type": "hive"}

    monkeypatch.setattr(query_runtime, "fetch_http_remote_ref_cache_result", unavailable_ref)
    monkeypatch.setattr(query_runtime, "get_sql_result_cache", http_ref_cache)
    http_result = asyncio.run(query_runtime.execute_sql_for_member(
        execution_target={"member_datasource_id": "m_http", "member_name": "HTTP成员", "source_type": "hive", "query_executor_type": "http"},
        sql="select 1",
        limit=10,
        timeout=None,
        user_id="user_1",
        username="测试用户",
        execution_id="ex_http",
        datasource_id="ds_a",
    ))

    direct_result = asyncio.run(query_runtime.execute_sql_for_member(
        execution_target={"member_datasource_id": "m_direct", "member_name": "直连成员", "source_type": "mysql", "query_executor_type": "direct"},
        sql="select 1",
        limit=10,
        timeout=None,
        user_id="user_1",
        username="测试用户",
        execution_id="ex_direct",
        datasource_id="ds_a",
    ))

    assert http_result["success"] is True
    assert direct_result["success"] is True
    assert execute_calls == [("select 1", "测试用户"), ("select 1", "测试用户")]
    assert ("http", "user_1", "测试用户") in cache_writes
    assert ("direct", "user_1", "测试用户") in cache_writes
    assert ("final", False) in cache_writes
    assert ("final", True) in cache_writes
    assert closed == [True, True]


def test_execute_sql_for_member_cache_read_and_write_errors_are_ignored(monkeypatch):
    target = {"member_datasource_id": "m1", "member_name": "成员1", "source_type": "mysql"}

    class FakeExecutor:
        async def execute(self, sql, limit, timeout, user_id):
            return {"success": True, "columns": ["id"], "data": [{"id": 1}], "total_count": 1}

        async def close(self):
            return None

    async def get_sql_result_cache(_cache_identity):
        raise RuntimeError("cache read failed")

    async def save_sql_result_cache(_cache_identity, _query_result, operator_id=None, operator_name=None):
        raise RuntimeError("cache write failed")

    async def run_with_limit(_user_id, _execution_target, query_fn):
        return await query_fn()

    monkeypatch.setattr(query_runtime, "QUERY_CACHE_ENABLED", True)
    monkeypatch.setattr(query_runtime, "get_sql_result_cache", get_sql_result_cache)
    monkeypatch.setattr(query_runtime, "save_sql_result_cache", save_sql_result_cache)
    monkeypatch.setattr(query_runtime, "get_executor", lambda _target: FakeExecutor())
    monkeypatch.setattr(query_runtime, "run_with_sql_user_datasource_limit", run_with_limit)

    result = asyncio.run(query_runtime.execute_sql_for_member(
        execution_target=target,
        sql="select 1",
        limit=10,
        timeout=None,
        user_id="user_1",
        username="测试用户",
        datasource_id="ds_a",
    ))

    assert result["success"] is True
    assert result["data"] == [{"id": 1}]


def test_sql_result_cache_save_short_circuits_and_http_delegation(monkeypatch):
    upserts = []

    async def upsert(cache_identity, payload, result_row_count, operator_id=None, operator_name=None):
        upserts.append((cache_identity, payload, result_row_count, operator_id, operator_name))

    monkeypatch.setattr(query_runtime, "_upsert_sql_result_cache_payload", upsert)

    asyncio.run(query_runtime.save_sql_result_cache(
        {"executor_type": "direct"},
        {"success": False, "columns": ["id"], "data": [{"id": 1}], "total_count": 1},
    ))
    asyncio.run(query_runtime.save_http_remote_ref_sql_result_cache(
        {"source_type": "hive"},
        {"success": True, "remote_task_id": ""},
    ))
    asyncio.run(query_runtime.save_http_remote_ref_sql_result_cache(
        {"source_type": "mysql"},
        {"success": False, "remote_task_id": "rt_skip"},
    ))

    assert upserts == []

    asyncio.run(query_runtime.save_sql_result_cache(
        {"executor_type": "http", "source_type": "hive"},
        {"success": True, "remote_task_id": "remote_1", "execution_time_ms": 3},
        operator_id="u1",
        operator_name="张三",
    ))
    asyncio.run(query_runtime.save_sql_result_cache(
        {"executor_type": "direct"},
        {"success": True, "columns": ["id"], "data": [{"id": 1}], "total_count": 1, "execution_time_ms": 5},
        operator_id="u2",
        operator_name="李四",
    ))

    assert upserts[0][1]["cache_type"] == "http_remote_ref"
    assert upserts[0][1]["remote_task_id"] == "remote_1"
    assert upserts[0][2] == 0
    assert upserts[0][3:] == ("u1", "张三")
    assert upserts[1][1]["data"] == [{"id": 1}]
    assert upserts[1][2] == 1
    assert upserts[1][3:] == ("u2", "李四")


def test_execute_sql_for_member_executor_exception_closes_and_standardizes_result(monkeypatch):
    closed = []
    final_updates = []

    class FailingExecutor:
        async def execute(self, sql, limit, timeout, user_id):
            raise RuntimeError("executor failed")

        async def close(self):
            closed.append(True)

    async def run_with_limit(_user_id, _execution_target, query_fn):
        return await query_fn()

    async def running_status(_execution_id):
        return "running"

    async def update_query_execution(_execution_id, **_kwargs):
        return None

    async def update_query_execution_from_result(execution_id, result, store_result_payload=True):
        final_updates.append((execution_id, result, store_result_payload))

    monkeypatch.setattr(query_runtime, "QUERY_CACHE_ENABLED", False)
    monkeypatch.setattr(query_runtime, "get_executor", lambda _target: FailingExecutor())
    monkeypatch.setattr(query_runtime, "run_with_sql_user_datasource_limit", run_with_limit)
    monkeypatch.setattr(query_runtime, "get_query_execution_status", running_status)
    monkeypatch.setattr(query_runtime, "update_query_execution", update_query_execution)
    monkeypatch.setattr(query_runtime, "update_query_execution_from_result", update_query_execution_from_result)

    result = asyncio.run(query_runtime.execute_sql_for_member(
        execution_target={"member_datasource_id": "m1", "member_name": "成员1", "source_type": "mysql"},
        sql="select 1",
        limit=10,
        timeout=None,
        user_id="user_1",
        execution_id="ex_1",
        datasource_id="ds_a",
    ))

    assert result["success"] is False
    assert result["error"] == "executor failed"
    assert result["columns"] == []
    assert result["data"] == []
    assert closed == [True]
    assert final_updates[0][0] == "ex_1"
    assert final_updates[0][1]["error"] == "executor failed"
    assert final_updates[0][2] is True
