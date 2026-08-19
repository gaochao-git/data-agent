"""token_usage 事件跳过落库但仍被 ACK 的持久化消费者行为。"""

from contextlib import asynccontextmanager

import pytest

from apps.chat_data_app.services.agent_run import agent_run_event_persistence as persistence_module
from apps.chat_data_app.services.agent_run.agent_run_event_persistence import AgentRunEventPersistence


class _FakeBus:
    def __init__(self):
        self.acked: list[str] = []

    async def ack_persistence(self, stream_ids):
        self.acked.extend(stream_ids)


class _FakeDao:
    def __init__(self):
        self.appended: list[dict] = []

    async def append(self, _session, **kwargs):
        self.appended.append(kwargs)


def _row(stream_id: str, event_type: str) -> tuple[str, dict[str, str]]:
    return stream_id, {
        "run_id": "run-1",
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "sequence_no": "7",
        "stream_id": stream_id,
        "event_type": event_type,
        "payload": "{}",
    }


@pytest.mark.asyncio
async def test_token_usage_rows_are_skipped_but_acked(monkeypatch):
    bus = _FakeBus()
    dao = _FakeDao()
    monkeypatch.setattr(persistence_module, "agent_run_event_dao", dao)

    # 全部是 token_usage：不应打开数据库会话，也不应写任何行。
    def _fail_session():
        raise AssertionError("token_usage-only batch must not open a DB session")

    monkeypatch.setattr(persistence_module, "get_session", _fail_session)

    consumer = AgentRunEventPersistence(bus)
    await consumer._persist([_row("1-1", "token_usage"), _row("1-2", "token_usage")])

    assert dao.appended == []
    assert bus.acked == ["1-1", "1-2"]


@pytest.mark.asyncio
async def test_mixed_batch_persists_only_durable_rows_and_acks_all(monkeypatch):
    bus = _FakeBus()
    dao = _FakeDao()
    monkeypatch.setattr(persistence_module, "agent_run_event_dao", dao)

    @asynccontextmanager
    async def _fake_session():
        yield object()

    monkeypatch.setattr(persistence_module, "get_session", _fake_session)

    consumer = AgentRunEventPersistence(bus)
    await consumer._persist([
        _row("2-1", "message_delta"),
        _row("2-2", "token_usage"),
        _row("2-3", "message_end"),
    ])

    assert [item["event_id"] for item in dao.appended] == ["2-1", "2-3"]
    assert all(item["event_type"] != "token_usage" for item in dao.appended)
    assert bus.acked == ["2-1", "2-2", "2-3"]
