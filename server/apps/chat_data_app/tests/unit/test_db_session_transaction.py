"""数据库会话入口：事务内复用同一会话，事务外提交，禁止子任务并发共享会话。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import utils.database as database
from apps.chat_data_app.daos import dao_session


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _patch_fake_session(monkeypatch) -> FakeSession:
    session = FakeSession()

    def factory():
        @asynccontextmanager
        async def session_cm():
            yield session

        return session_cm()

    monkeypatch.setattr(database, "get_session_factory", lambda: factory)
    monkeypatch.setattr(database, "_endpoint_selector", SimpleNamespace(current=lambda: None))
    monkeypatch.setattr(dao_session, "get_session", database.get_session)
    return session


def test_transaction_scope_reuses_one_session_and_commits_once(monkeypatch):
    session = _patch_fake_session(monkeypatch)

    async def scenario():
        async with dao_session.transaction_scope() as tx:
            assert database.get_active_session() is tx
            async with database.get_session() as nested:
                assert nested is tx
            async with dao_session.session_scope() as nested_dao:
                assert nested_dao is tx

    asyncio.run(scenario())
    assert session.commits == 1
    assert database.get_active_session() is None


def test_get_session_outside_transaction_commits(monkeypatch):
    session = _patch_fake_session(monkeypatch)

    async def scenario():
        async with database.get_session() as opened:
            assert opened is session

    asyncio.run(scenario())
    assert session.commits == 1


def test_transaction_rejects_concurrent_child_task_session_use(monkeypatch):
    _patch_fake_session(monkeypatch)

    async def scenario():
        async with dao_session.transaction_scope():

            async def child():
                async with database.get_session():
                    return None

            await asyncio.create_task(child())

    with pytest.raises(RuntimeError, match="事务内禁止并发使用同一数据库会话"):
        asyncio.run(scenario())


def test_transaction_rolls_back_and_clears_context_on_error(monkeypatch):
    session = _patch_fake_session(monkeypatch)

    async def scenario():
        async with database.get_session():
            assert database.get_active_session() is session
            raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        asyncio.run(scenario())

    assert session.rollbacks == 1
    assert session.commits == 0
    assert database.get_active_session() is None
