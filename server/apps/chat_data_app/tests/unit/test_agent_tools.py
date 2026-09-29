import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.agent.tools import calculator_tool
from apps.chat_data_app.agent.tools import data_analyzing as agent_tools
from apps.chat_data_app.agent.tools.current_time import get_current_time_payload, get_current_time_tool
from apps.chat_data_app.schema import SemanticQueryInput
from apps.chat_data_app.services.sql_run import runtime as query_runtime


def test_current_time_tool_returns_detail_only_when_requested():
    current_time = datetime(2026, 7, 24, 10, 30, 0, tzinfo=timezone.utc)
    assert get_current_time_payload(current_time, detail=False) == {
        "current_time": "2026-07-24 18:30:00",
    }

    payload = get_current_time_payload(current_time, detail=True)

    assert payload == {
        "current_time": "2026-07-24 18:30:00",
        "timezone": "Asia/Shanghai",
        "utc_offset": "+08:00",
        "weekday": "周五",
        "timestamp": 1784889000,
    }
    compact_payload = json.loads(asyncio.run(get_current_time_tool.ainvoke({"detail": False})))
    assert list(compact_payload) == ["current_time"]
    assert isinstance(compact_payload["current_time"], str)
    detailed_payload = json.loads(asyncio.run(get_current_time_tool.ainvoke({"detail": True})))
    assert {"current_time", "timezone", "utc_offset", "weekday", "timestamp"} == set(detailed_payload)
    assert isinstance(detailed_payload["timestamp"], int)

    with pytest.raises(ValidationError):
        asyncio.run(get_current_time_tool.ainvoke({}))


def test_calculator_tool_calculates_decimal_expression_and_rounds():
    result = asyncio.run(
        calculator_tool.ainvoke(
            {
                "calculations": [
                    {"name": "分转元", "expression": "123456789 / 100", "decimal_places": 2},
                    {"name": "占比", "expression": "25 / 200 * 100", "decimal_places": 1},
                ],
            }
        )
    )

    payload = json.loads(result)
    assert payload == {
        "success": True,
        "results": [
            {"expression": "123456789 / 100", "name": "分转元", "success": True, "result": "1234567.89"},
            {"expression": "25 / 200 * 100", "name": "占比", "success": True, "result": "12.5"},
        ],
    }


def test_calculator_tool_rejects_unsafe_expression():
    result = asyncio.run(
        calculator_tool.ainvoke(
            {
                "calculations": [
                    {"name": "危险表达式", "expression": "__import__('os').system('echo bad')"},
                    {"name": "正常表达式", "expression": "10 + 1"},
                ],
            }
        )
    )

    payload = json.loads(result)
    assert payload == {
        "success": False,
        "results": [
            {
                "expression": "__import__('os').system('echo bad')",
                "name": "危险表达式",
                "success": False,
                "error": "expression 只能包含数字、括号和 + - * /",
            },
            {"expression": "10 + 1", "name": "正常表达式", "success": True, "result": "11"},
        ],
    }


def test_calculator_tool_covers_empty_division_and_unary_branches():
    result = asyncio.run(
        calculator_tool.ainvoke(
            {
                "calculations": [
                    {"expression": "   "},
                    {"name": "除零", "expression": "1 / 0"},
                    {"name": "一元运算", "expression": "-1 + +2", "decimal_places": 0},
                ],
            }
        )
    )

    payload = json.loads(result)

    assert payload["success"] is False
    assert payload["results"][0] == {"expression": "", "success": False, "error": "expression 不能为空"}
    assert payload["results"][1]["error"] == "除数不能为 0"
    assert payload["results"][2]["result"] == "1"


class _FakeDatasourceManager:
    async def get_datasource_async(self, datasource_id: str):
        if datasource_id != "ds_group":
            return None
        return {
            "id": datasource_id,
            "source_type": "mysql",
        }

    async def get_execution_datasources_async(self, datasource_id: str, member_datasource_ids: list[str] | None = None):
        member_ids = member_datasource_ids or ["member_a"]
        all_targets = {
            "member_a": {
                "member_datasource_id": "member_a",
                "member_name": "杭州库",
                "source_type": "mysql",
                "source_config": {"host": "127.0.0.1", "port": 3306, "database": "db_a", "user": "u", "password": "p"},
            },
            "member_b": {
                "member_datasource_id": "member_b",
                "member_name": "宁波库",
                "source_type": "mysql",
                "source_config": {"host": "127.0.0.2", "port": 3306, "database": "db_b", "user": "u", "password": "p"},
            },
        }
        return [all_targets[item] for item in member_ids]

    async def get_authorized_execution_datasources_async(
        self,
        datasource_id: str,
        member_datasource_ids: list[str] | None = None,
        user_id: str | None = None,
        username: str | None = None,
    ):
        return await self.get_execution_datasources_async(datasource_id, member_datasource_ids)


async def _fake_execute_sql_on_target(
    sql: str, target_datasource: dict, user_id: str | None = None, operator_name: str | None = None
) -> dict:
    member_id = target_datasource.get("member_datasource_id")
    return {
        "success": True,
        "columns": ["member_id", "sql_text"],
        "data": [{"member_id": member_id, "sql_text": sql}],
        "total_count": 1,
        "execution_time_ms": 15,
    }


def _fake_runtime_execute_sql_for_member(handler):
    async def fake_execute_sql_for_member(
        *,
        execution_target: dict,
        sql: str,
        limit: int = 100,
        timeout: int | None = None,
        user_id: str | None = None,
        username: str | None = None,
        query_task_id: str | None = None,
        execution_id: str | None = None,
        skip_query_cache: bool = False,
        datasource_id: str | None = None,
        max_rows: int | None = None,
    ) -> dict:
        member_id = execution_target.get("member_datasource_id")
        member_name = execution_target.get("member_name")
        try:
            try:
                base_result = await handler(
                    sql, execution_target, user_id=user_id, operator_name=username, execution_id=execution_id
                )
            except TypeError:
                base_result = await handler(sql, execution_target, user_id=user_id, operator_name=username)
        except Exception as exc:
            base_result = {
                "success": False,
                "columns": [],
                "data": [],
                "total_count": 0,
                "error": f"执行失败: {str(exc) or type(exc).__name__}",
            }
        return {
            "success": bool(base_result.get("success")),
            "sql": base_result.get("sql") or sql,
            "columns": base_result.get("columns", []),
            "data": base_result.get("data", []),
            "total_count": base_result.get("total_count", 0),
            "execution_time_ms": base_result.get("execution_time_ms"),
            "error": base_result.get("error"),
            "member_datasource_id": base_result.get("member_datasource_id") or member_id,
            "member_name": base_result.get("member_name") or member_name,
        }

    return fake_execute_sql_for_member


def test_execute_sql_real_returns_multi_member_results(monkeypatch):
    fake_manager = _FakeDatasourceManager()
    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: fake_manager)
    monkeypatch.setattr(
        query_runtime,
        "create_task_for_execute_request",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"task_id": "task_1"}),
    )
    monkeypatch.setattr(
        query_runtime, "create_query_executions_for_task", lambda *_args, **_kwargs: asyncio.sleep(0, result={})
    )
    monkeypatch.setattr(query_runtime, "update_query_task", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(
        query_runtime, "execute_sql_for_member", _fake_runtime_execute_sql_for_member(_fake_execute_sql_on_target)
    )

    result = asyncio.run(
        agent_tools._execute_sql_real(
            sql="select 1",
            datasource_id="ds_group",
            member_datasource_ids=["member_a", "member_b"],
        )
    )

    assert result["status"] == "succeeded"
    assert result["sql"] == "select 1"
    assert len(result["results"]) == 2
    assert result["results"][0]["member_name"] == "杭州库"
    assert result["results"][1]["member_name"] == "宁波库"
    assert result["results"][1]["data"][0]["member_id"] == "member_b"


async def _fake_execute_sql_on_target_with_failure(
    sql: str, target_datasource: dict, user_id: str | None = None, operator_name: str | None = None
) -> dict:
    member_id = target_datasource.get("member_datasource_id")
    if member_id == "member_a":
        return {
            "success": False,
            "columns": [],
            "data": [],
            "total_count": 0,
            "error": "杭州库执行失败",
        }
    return {
        "success": True,
        "columns": ["member_id", "sql_text"],
        "data": [{"member_id": member_id, "sql_text": sql}],
        "total_count": 1,
        "execution_time_ms": 18,
    }


def test_execute_sql_real_uses_first_successful_member_as_primary(monkeypatch):
    fake_manager = _FakeDatasourceManager()
    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: fake_manager)
    monkeypatch.setattr(
        query_runtime,
        "create_task_for_execute_request",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"task_id": "task_1"}),
    )
    monkeypatch.setattr(
        query_runtime, "create_query_executions_for_task", lambda *_args, **_kwargs: asyncio.sleep(0, result={})
    )
    monkeypatch.setattr(query_runtime, "update_query_task", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(
        query_runtime,
        "execute_sql_for_member",
        _fake_runtime_execute_sql_for_member(_fake_execute_sql_on_target_with_failure),
    )

    result = asyncio.run(
        agent_tools._execute_sql_real(
            sql="select 1",
            datasource_id="ds_group",
            member_datasource_ids=["member_a", "member_b"],
        )
    )

    assert result["status"] == "succeeded"
    assert result["results"][1]["member_name"] == "宁波库"
    assert result["results"][1]["columns"] == ["member_id", "sql_text"]
    assert result["results"][1]["data"][0]["member_id"] == "member_b"


async def _fake_execute_sql_on_target_with_delay(
    sql: str, target_datasource: dict, user_id: str | None = None, operator_name: str | None = None
) -> dict:
    await asyncio.sleep(0.2)
    member_id = target_datasource.get("member_datasource_id")
    return {
        "success": True,
        "columns": ["member_id"],
        "data": [{"member_id": member_id}],
        "total_count": 1,
        "execution_time_ms": 200,
    }


def test_execute_sql_real_runs_members_concurrently(monkeypatch):
    fake_manager = _FakeDatasourceManager()
    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: fake_manager)
    monkeypatch.setattr(
        query_runtime,
        "create_task_for_execute_request",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"task_id": "task_1"}),
    )
    monkeypatch.setattr(
        query_runtime, "create_query_executions_for_task", lambda *_args, **_kwargs: asyncio.sleep(0, result={})
    )
    monkeypatch.setattr(query_runtime, "update_query_task", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(
        query_runtime,
        "execute_sql_for_member",
        _fake_runtime_execute_sql_for_member(_fake_execute_sql_on_target_with_delay),
    )

    start = time.perf_counter()
    result = asyncio.run(
        agent_tools._execute_sql_real(
            sql="select 1",
            datasource_id="ds_group",
            member_datasource_ids=["member_a", "member_b"],
        )
    )
    elapsed = time.perf_counter() - start

    assert result["status"] == "succeeded"
    assert len(result["results"]) == 2
    assert result["results"][0]["member_datasource_id"] == "member_a"
    assert result["results"][1]["member_datasource_id"] == "member_b"
    assert elapsed < 0.35


async def _fake_execute_sql_on_target_with_exception(
    sql: str, target_datasource: dict, user_id: str | None = None, operator_name: str | None = None
) -> dict:
    member_id = target_datasource.get("member_datasource_id")
    if member_id == "member_a":
        raise RuntimeError("boom")
    return {
        "success": True,
        "columns": ["member_id", "sql_text"],
        "data": [{"member_id": member_id, "sql_text": sql}],
        "total_count": 1,
        "execution_time_ms": 18,
    }


def test_execute_sql_real_keeps_other_members_when_one_member_raises(monkeypatch):
    fake_manager = _FakeDatasourceManager()
    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: fake_manager)
    monkeypatch.setattr(
        query_runtime,
        "create_task_for_execute_request",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"task_id": "task_1"}),
    )
    monkeypatch.setattr(
        query_runtime, "create_query_executions_for_task", lambda *_args, **_kwargs: asyncio.sleep(0, result={})
    )
    monkeypatch.setattr(query_runtime, "update_query_task", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(
        query_runtime,
        "execute_sql_for_member",
        _fake_runtime_execute_sql_for_member(_fake_execute_sql_on_target_with_exception),
    )

    result = asyncio.run(
        agent_tools._execute_sql_real(
            sql="select 1",
            datasource_id="ds_group",
            member_datasource_ids=["member_a", "member_b"],
        )
    )

    assert result["status"] == "succeeded"
    assert result["results"][1]["member_name"] == "宁波库"
    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["error"] == "执行失败: boom"
    assert result["results"][1]["status"] == "succeeded"


def test_execute_sql_real_returns_errors_for_missing_datasource(monkeypatch):
    class FakeManager:
        async def get_datasource_async(self, datasource_id):
            return None

    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: FakeManager())

    missing_id = asyncio.run(agent_tools._execute_sql_real("select 1", None))
    missing_datasource = asyncio.run(agent_tools._execute_sql_real("select 1", "missing"))

    assert missing_id["error"] == "未指定数据源ID，无法执行查询"
    assert missing_datasource["error"] == "数据源不存在: missing"


def test_execute_sql_real_updates_task_success_and_failure(monkeypatch):
    class FakeManager(_FakeDatasourceManager):
        pass

    updates = []

    async def fake_create_task_for_execute_request(body, query_type=None, operator_name=None):
        return {"task_id": "task_1"}

    async def fake_create_query_executions_for_task(task_id, body, operator_name=None):
        return {"member_a": "exec_a"}

    def fake_get_execution_id_by_member(execution_map, target):
        return execution_map.get(target.get("member_datasource_id"))

    async def fake_update_query_task(task_id, **kwargs):
        updates.append({"task_id": task_id, **kwargs})
        return {"task_id": task_id}

    async def fake_execute_sql_for_member_success(
        sql, target_datasource, user_id=None, operator_name=None, execution_id=None
    ):
        return {
            "success": True,
            "sql": sql,
            "columns": ["id"],
            "data": [{"id": 1}],
            "total_count": 1,
            "member_datasource_id": target_datasource["member_datasource_id"],
        }

    async def fake_execute_sql_for_member_error(
        sql, target_datasource, user_id=None, operator_name=None, execution_id=None
    ):
        raise ValueError("不支持的数据源类型")

    monkeypatch.setattr(query_runtime, "get_datasource_manager", lambda: FakeManager())
    monkeypatch.setattr(query_runtime, "create_task_for_execute_request", fake_create_task_for_execute_request)
    monkeypatch.setattr(query_runtime, "create_query_executions_for_task", fake_create_query_executions_for_task)
    monkeypatch.setattr(query_runtime, "get_execution_id_by_member", fake_get_execution_id_by_member)
    monkeypatch.setattr(query_runtime, "update_query_task", fake_update_query_task)
    monkeypatch.setattr(
        query_runtime,
        "execute_sql_for_member",
        _fake_runtime_execute_sql_for_member(fake_execute_sql_for_member_success),
    )
    success = asyncio.run(
        agent_tools._execute_sql_real("select 1", "ds_group", ["member_a"], user_id="user_a", operator_name="测试用户")
    )

    async def fake_runtime_execute_error(**_kwargs):
        raise ValueError("不支持的数据源类型")

    monkeypatch.setattr(query_runtime, "execute_sql_for_member", fake_runtime_execute_error)
    failed = asyncio.run(agent_tools._execute_sql_real("select 1", "ds_group", ["member_a"]))

    assert success["status"] == "succeeded"
    assert updates[0]["status"] == "running"
    assert updates[1]["status"] == "succeeded"
    assert failed["status"] == "failed"
    assert failed["error"] == "不支持的数据源类型"
    assert updates[-1]["status"] == "failed"


def test_semantic_query_input_rejects_invalid_json_dsl_string():
    base_payload = {
        "semantic_config_id": "cfg_a",
        "member_datasource_ids": ["m1"],
    }

    assert SemanticQueryInput.model_validate(
        {
            **base_payload,
            "dsl": '{"metrics": ["订单数"]}',
        }
    ).dsl == {"metrics": ["订单数"]}

    with pytest.raises(ValidationError, match="dsl 必须是对象或有效的 JSON 对象字符串"):
        SemanticQueryInput.model_validate({**base_payload, "dsl": "not-json"})
    with pytest.raises(ValidationError, match="dsl 必须是对象或有效的 JSON 对象字符串"):
        SemanticQueryInput.model_validate({**base_payload, "dsl": "[]"})
