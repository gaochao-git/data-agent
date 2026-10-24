"""SQL executor 剩余分支：远端字段解析、MySQL 超时兜底、HTTP 任务 ID 与工厂校验。"""

import asyncio
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.services.sql_run import executor as sql_executor


def test_extract_remote_code_returns_none_for_null_code():
    assert sql_executor._extract_remote_code({"code": None}) is None
    assert sql_executor._extract_remote_code({"code": "20000"}) == 20000


def test_normalize_execution_time_skips_non_dict_and_bad_values():
    assert sql_executor._normalize_http_execution_time_ms({"cost": "abc"}, None) is None
    assert sql_executor._normalize_http_execution_time_ms({"cost": "12.9"}, {}) == 12
    assert sql_executor._normalize_http_execution_time_ms({"cost": True}, {}) is None


def test_parse_start_time_from_source_variants():
    assert sql_executor._parse_start_time_from_source(None) is None
    moment = datetime(2026, 1, 2, 3, 4, 5)
    assert sql_executor._parse_start_time_from_source({"startTime": moment}) == moment
    assert sql_executor._parse_start_time_from_source({"startTime": "2026-01-02T03:04:05"}) == moment
    assert sql_executor._parse_start_time_from_source({"startTime": "2026-01-02 03:04"}) == datetime(2026, 1, 2, 3, 4)
    assert sql_executor._parse_start_time_from_source({"startTime": "not-a-time"}) is None


def test_mysql_execute_returns_timeout_when_pool_acquire_times_out(monkeypatch):
    executor = sql_executor.MySQLExecutor(host="127.0.0.1", port=3306, database="db", user="u", password="p")

    async def timing_out_pool():
        raise TimeoutError()

    executor._get_pool = timing_out_pool

    result = asyncio.run(executor.execute("select 1 from t", limit=10, timeout=1))
    assert result["status"] == "failed"
    assert "查询超时" in result["error"]


def test_kill_timed_out_query_swallows_kill_exceptions():
    executor = sql_executor.MySQLExecutor(host="127.0.0.1", port=3306, database="db", user="u", password="p")

    async def raising_kill(_connection_id):
        raise RuntimeError("kill down")

    executor.kill_query = raising_kill

    asyncio.run(executor._kill_timed_out_query("12345"))
    asyncio.run(executor._kill_timed_out_query(""))


def test_http_execute_returns_error_when_submit_yields_no_task_id(monkeypatch):
    executor = sql_executor.HiveHttpExecutor(namespace="ns_a")

    async def submit_without_task(*_args, **_kwargs):
        return None, None

    monkeypatch.setattr(executor, "_submit_http_query", submit_without_task)
    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")

    result = asyncio.run(executor.execute("select 1"))
    assert result["status"] == "failed"
    assert "任务 ID" in result["error"]


def test_get_executor_rejects_unsupported_source_type(monkeypatch):
    monkeypatch.setattr(sql_executor, "resolve_execution_source_config", lambda *_args, **_kwargs: {})

    with pytest.raises(ValueError, match="不支持的数据源类型"):
        sql_executor.get_executor({"source_type": "oracle", "source_config": {}})
