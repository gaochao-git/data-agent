"""写适配器：Hive 连接配置与执行。"""

from __future__ import annotations

import asyncio

import pytest

from apps.chat_data_app.services.write import HiveWriteAdapter, get_write_adapter


def test_get_write_adapter_rejects_unsupported_source():
    with pytest.raises(RuntimeError, match="不支持"):
        get_write_adapter({"source_type": "mysql"})


def test_hive_adapter_executes_statements(monkeypatch):
    calls = []

    class Cursor:
        def execute(self, sql):
            calls.append(sql)

    class Connection:
        def __init__(self):
            self.closed = False

        def cursor(self):
            return Cursor()

        def close(self):
            self.closed = True

    conn = Connection()
    captured = {}

    class FakeHive:
        @staticmethod
        def Connection(**kwargs):
            captured.update(kwargs)
            return conn

    import pyhive.hive

    monkeypatch.setattr(pyhive.hive, "Connection", FakeHive.Connection)
    asyncio.run(
        HiveWriteAdapter().execute_statements_async(
            {"source_config": {"host": "h", "port": 10001, "username": "u", "database": "db", "password": "p"}},
            ["DROP TABLE x", "CREATE TABLE x (id int)"],
        )
    )
    assert captured == {"host": "h", "port": 10001, "username": "u", "database": "db", "password": "p"}
    assert calls == ["DROP TABLE x", "CREATE TABLE x (id int)"]
    assert conn.closed is True
