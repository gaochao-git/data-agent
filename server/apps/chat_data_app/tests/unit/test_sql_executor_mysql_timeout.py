"""MySQL 执行器超时路径回归测试：超时必须 KILL 服务端查询并废弃污染连接。"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from apps.chat_data_app.services.sql_run import executor as sql_executor


class _FakeCursor:
    def __init__(self, connection_id, hang_seconds):
        self._connection_id = connection_id
        self._hang_seconds = hang_seconds
        self.description = None
        self.executed_sqls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, sql, args=None):
        self.executed_sqls.append(sql)
        if "CONNECTION_ID" in sql:
            return
        await asyncio.sleep(self._hang_seconds)

    async def fetchone(self):
        if self._connection_id is None:
            return None
        return [self._connection_id]

    async def fetchall(self):
        return []


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.close_called = False

    def close(self):
        self.close_called = True

    def cursor(self):
        return self._cursor


class _AcquireContext:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc_info):
        return False


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _AcquireContext(self.conn)


def _build_executor(connection_id="12345", hang_seconds=30):
    cursor = _FakeCursor(connection_id, hang_seconds)
    conn = _FakeConnection(cursor)
    executor = sql_executor.MySQLExecutor(host="127.0.0.1", port=3306, database="db", user="u", password="p")
    pool = _FakePool(conn)

    async def fake_get_pool():
        return pool

    executor._get_pool = fake_get_pool
    return executor, conn, cursor


@pytest.mark.asyncio
async def test_mysql_timeout_kills_query_and_discards_connection(monkeypatch):
    """超时后必须调 KILL（用已取到的 connection_id）并 close 废弃连接，而不是把半读连接还回池。"""
    executor, conn, cursor = _build_executor()
    kill_calls = []

    async def fake_kill(connection_id):
        kill_calls.append(connection_id)
        return {"status": "succeeded", "error": None}

    executor.kill_query = fake_kill
    monkeypatch.setattr(sql_executor, "resolve_query_timeout", lambda *args, **kwargs: 0.05)

    result = await executor.execute("SELECT * FROM t", limit=10)

    assert result["status"] != "succeeded"
    assert "超时" in result["error"]
    assert kill_calls == ["12345"]
    assert conn.close_called is True


@pytest.mark.asyncio
async def test_mysql_timeout_returns_error_even_when_kill_fails(monkeypatch):
    """KILL 失败只告警，不影响超时错误返回，连接同样废弃。"""
    executor, conn, _cursor = _build_executor()

    async def fake_kill(connection_id):
        return {"status": "failed", "error": "KILL 失败"}

    executor.kill_query = fake_kill
    monkeypatch.setattr(sql_executor, "resolve_query_timeout", lambda *args, **kwargs: 0.05)

    result = await executor.execute("SELECT * FROM t", limit=10)

    assert result["status"] != "succeeded"
    assert "超时" in result["error"]
    assert conn.close_called is True


@pytest.mark.asyncio
async def test_mysql_timeout_without_connection_id_skips_kill_but_discards_connection(monkeypatch):
    """拿不到 connection_id 时跳过 KILL（无害），连接仍必须废弃。"""
    executor, conn, cursor = _build_executor(connection_id=None)
    kill_calls = []

    async def fake_kill(connection_id):
        kill_calls.append(connection_id)
        return {"status": "succeeded", "error": None}

    executor.kill_query = fake_kill
    monkeypatch.setattr(sql_executor, "resolve_query_timeout", lambda *args, **kwargs: 0.05)

    result = await executor.execute("SELECT * FROM t", limit=10)

    assert result["status"] != "succeeded"
    assert kill_calls == []
    assert conn.close_called is True


@pytest.mark.asyncio
async def test_mysql_success_path_does_not_close_connection(monkeypatch):
    """正常完成时不触发 KILL、不废弃连接。"""
    executor, conn, cursor = _build_executor(hang_seconds=0)
    cursor.description = [("a",)]
    kill_calls = []

    async def fake_kill(connection_id):
        kill_calls.append(connection_id)
        return {"status": "succeeded", "error": None}

    executor.kill_query = fake_kill

    async def fast_fetchall():
        return [[1]]

    cursor.fetchall = fast_fetchall

    async def fast_execute(sql, args=None):
        cursor.executed_sqls.append(sql)

    cursor.execute = fast_execute
    monkeypatch.setattr(sql_executor, "resolve_query_timeout", lambda *args, **kwargs: 1)

    result = await executor.execute("SELECT * FROM t", limit=10)

    assert result["status"] == "succeeded"
    assert result["db_connection_id"] == "12345"
    assert kill_calls == []
    assert conn.close_called is False
