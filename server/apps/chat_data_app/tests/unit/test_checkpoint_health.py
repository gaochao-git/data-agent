"""checkpoint 健康探针：SQLite/MySQL 均返回布尔值，不抛异常。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from utils import checkpoint


def test_checkpoint_health_sqlite_probe(monkeypatch, tmp_path):
    class FakeConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _sql):
            return None

    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "sqlite")
    monkeypatch.setattr(checkpoint, "DB_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.setitem(__import__("sys").modules, "aiosqlite", SimpleNamespace(connect=lambda _p: FakeConn()))
    assert asyncio.run(checkpoint.probe_checkpoint_health()) is True


def test_checkpoint_health_mysql_without_selector_is_false(monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "mysql")
    monkeypatch.setattr(checkpoint, "_endpoint_selector", None)
    assert asyncio.run(checkpoint.probe_checkpoint_health()) is False


def test_checkpoint_health_returns_false_on_probe_error(monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "mysql")
    monkeypatch.setattr(
        checkpoint,
        "_endpoint_selector",
        SimpleNamespace(endpoints=[object()], current=lambda: SimpleNamespace(label=lambda: "h:p")),
    )
    monkeypatch.setattr(checkpoint, "_build_mysql_checkpointer_context_factory", lambda: lambda _e: None)

    async def broken_probe(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(checkpoint, "_probe_mysql_checkpointer_endpoint", broken_probe)
    assert asyncio.run(checkpoint.probe_checkpoint_health()) is False
