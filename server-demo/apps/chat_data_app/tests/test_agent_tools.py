import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent import tools as agent_tools


class _FakeDatasourceManager:
    def get_datasource(self, datasource_id: str):
        if datasource_id != "ds_group":
            return None
        return {
            "id": datasource_id,
            "source_type": "mysql",
        }

    def get_execution_datasources(self, datasource_id: str, member_datasource_ids: list[str] | None = None):
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


async def _fake_execute_sql_on_target(sql: str, target_datasource: dict) -> dict:
    member_id = target_datasource.get("member_datasource_id")
    return {
        "success": True,
        "columns": ["member_id", "sql_text"],
        "data": [{"member_id": member_id, "sql_text": sql}],
        "total_count": 1,
        "execution_time_ms": 15,
    }


def test_execute_sql_real_returns_multi_member_results(monkeypatch):
    fake_manager = _FakeDatasourceManager()
    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.get_datasource_manager",
        lambda: fake_manager,
    )
    monkeypatch.setattr(agent_tools, "_execute_sql_on_target", _fake_execute_sql_on_target)

    result = asyncio.run(agent_tools._execute_sql_real(
        sql="select 1",
        datasource_id="ds_group",
        member_datasource_ids=["member_a", "member_b"],
    ))

    assert result["success"] is True
    assert result["sql"] == "select 1"
    assert len(result["results"]) == 2
    assert result["results"][0]["member_name"] == "杭州库"
    assert result["results"][1]["member_name"] == "宁波库"
    assert result["results"][1]["data"][0]["member_id"] == "member_b"


async def _fake_execute_sql_on_target_with_failure(sql: str, target_datasource: dict) -> dict:
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
    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.get_datasource_manager",
        lambda: fake_manager,
    )
    monkeypatch.setattr(agent_tools, "_execute_sql_on_target", _fake_execute_sql_on_target_with_failure)

    result = asyncio.run(agent_tools._execute_sql_real(
        sql="select 1",
        datasource_id="ds_group",
        member_datasource_ids=["member_a", "member_b"],
    ))

    assert result["success"] is True
    assert result["member_name"] == "宁波库"
    assert result["columns"] == ["member_id", "sql_text"]
    assert result["data"][0]["member_id"] == "member_b"


async def _fake_execute_sql_on_target_with_delay(sql: str, target_datasource: dict) -> dict:
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
    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.get_datasource_manager",
        lambda: fake_manager,
    )
    monkeypatch.setattr(agent_tools, "_execute_sql_on_target", _fake_execute_sql_on_target_with_delay)

    start = time.perf_counter()
    result = asyncio.run(agent_tools._execute_sql_real(
        sql="select 1",
        datasource_id="ds_group",
        member_datasource_ids=["member_a", "member_b"],
    ))
    elapsed = time.perf_counter() - start

    assert result["success"] is True
    assert len(result["results"]) == 2
    assert result["results"][0]["member_datasource_id"] == "member_a"
    assert result["results"][1]["member_datasource_id"] == "member_b"
    assert elapsed < 0.35


async def _fake_execute_sql_on_target_with_exception(sql: str, target_datasource: dict) -> dict:
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
    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.get_datasource_manager",
        lambda: fake_manager,
    )
    monkeypatch.setattr(agent_tools, "_execute_sql_on_target", _fake_execute_sql_on_target_with_exception)

    result = asyncio.run(agent_tools._execute_sql_real(
        sql="select 1",
        datasource_id="ds_group",
        member_datasource_ids=["member_a", "member_b"],
    ))

    assert result["success"] is True
    assert result["member_name"] == "宁波库"
    assert result["results"][0]["success"] is False
    assert result["results"][0]["error"] == "执行失败: boom"
    assert result["results"][1]["success"] is True
