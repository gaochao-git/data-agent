import pytest

from apps.chat_data_app.services.agent_run.agent_run_event_service import AgentRunEventService


class _Bus:
    async def read_run(self, _run_id, _cursor):
        return [{
            "stream_id": "1740000000000-0",
            "event_type": "done",
            "sequence_no": "2",
            "payload": "event: done\\ndata: [DONE]\\n\\n",
        }]


@pytest.mark.asyncio
async def test_append_does_not_open_mysql_transaction_when_sequence_exists(monkeypatch):
    class HotPathBus:
        def __init__(self):
            self.published = []

        async def sequence_exists(self, _run_id):
            return True

        async def publish(self, **kwargs):
            self.published.append(kwargs)

    bus = HotPathBus()
    service = AgentRunEventService(bus=bus)

    def unexpected_database_access():
        raise AssertionError("正常事件发布不应访问 MySQL")

    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.agent_run_event_service.get_session",
        unexpected_database_access,
    )

    await service.append(
        run_id="run-1",
        conversation_id="conversation-1",
        user_id="user-1",
        event_id="event-1",
        payload="event: message\ndata: {}\n\n",
        owner_token="owner-1",
    )

    assert bus.published[0]["initial_sequence"] == 0
    assert bus.published[0]["event_type"] == "message"


@pytest.mark.asyncio
async def test_stream_stops_after_terminal_done_event():
    service = AgentRunEventService(bus=_Bus())
    events = [event async for event in service.stream(run_id="run-1")]

    assert len(events) == 1
    assert events[0]["event_type"] == "done"


@pytest.mark.asyncio
async def test_stream_keeps_waiting_when_durable_fallback_is_temporarily_unavailable(monkeypatch):
    class EmptyBus:
        async def read_run(self, _run_id, _cursor):
            return []

        async def run_stream_exists(self, _run_id):
            return True

    service = AgentRunEventService(bus=EmptyBus())
    attempts = 0

    async def broken_durable(_run_id, _after_seq):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database unavailable")
        return [{
            "stream_id": "1740000000001-0",
            "event_type": "done",
            "sequence_no": "1",
            "payload": "event: done\\ndata: [DONE]\\n\\n",
        }]

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(service, "_read_durable_events", broken_durable)
    monkeypatch.setattr(service, "_get_run_snapshot", lambda _run_id: {"status": "active"})
    monkeypatch.setattr("apps.chat_data_app.services.agent_run.agent_run_event_service.asyncio.sleep", no_sleep)

    events = [event async for event in service.stream(run_id="run-1")]

    assert attempts == 2
    assert events[0]["event_type"] == "done"
