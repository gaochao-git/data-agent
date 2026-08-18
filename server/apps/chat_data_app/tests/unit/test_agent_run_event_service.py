import pytest

from apps.chat_data_app.services.agent_run.agent_inbox_service import AgentRunLeaseLostError
from apps.chat_data_app.services.agent_run.agent_run_event_service import AgentRunEventService
from apps.chat_data_app.services.agent_run.redis_event_bus import RedisRunOwnerMismatchError


class _Bus:
    async def latest_run_event(self, _run_id):
        return {"stream_id": "1740000000000-0", "event_type": "done"}

    async def run_event_sequence(self, _run_id, _cursor):
        return 2

    async def latest_run_event_id(self, _run_id):
        return "1740000000000-0"

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
async def test_append_checks_redis_sequence_only_once_per_run(monkeypatch):
    class Bus:
        def __init__(self):
            self.sequence_checks = 0
            self.published = []

        async def sequence_exists(self, _run_id):
            self.sequence_checks += 1
            return True

        async def publish(self, **kwargs):
            self.published.append(kwargs)

    bus = Bus()
    service = AgentRunEventService(bus=bus)

    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.agent_run_event_service.get_session",
        lambda: (_ for _ in ()).throw(AssertionError("序号存在时不应访问 MySQL")),
    )

    for event_id in ("event-1", "event-2"):
        await service.append(
            run_id="run-1",
            conversation_id="conversation-1",
            user_id="user-1",
            event_id=event_id,
            payload="event: message\ndata: {}\n\n",
            owner_token="owner-1",
        )

    assert bus.sequence_checks == 1
    assert len(bus.published) == 2


@pytest.mark.asyncio
async def test_claiming_new_owner_rechecks_sequence_state():
    class Bus:
        async def claim_run_owner(self, _run_id, _owner_token):
            return None

    service = AgentRunEventService(bus=Bus())
    service._sequence_initialized_runs.add("run-1")

    await service.claim_owner("run-1", "2:new-owner")

    assert "run-1" not in service._sequence_initialized_runs


@pytest.mark.asyncio
async def test_claim_owner_maps_redis_fencing_to_run_lease_loss():
    class Bus:
        async def claim_run_owner(self, _run_id, _owner_token):
            raise RedisRunOwnerMismatchError("run-1")

    service = AgentRunEventService(bus=Bus())

    with pytest.raises(AgentRunLeaseLostError):
        await service.claim_owner("run-1", "1:stale")


@pytest.mark.asyncio
async def test_stream_stops_after_terminal_done_event():
    service = AgentRunEventService(bus=_Bus())
    events = [event async for event in service.stream(run_id="run-1")]

    assert len(events) == 1
    assert events[0]["event_type"] == "done"


@pytest.mark.asyncio
async def test_stream_after_resume_cursor_does_not_replay_old_question():
    class ResumeBus:
        async def run_event_sequence(self, _run_id, cursor):
            assert cursor == "old-tail-0"
            return 2

        async def read_run(self, _run_id, cursor):
            assert cursor == "old-tail-0"
            return [
                {
                    "stream_id": "new-message-0",
                    "event_type": "message",
                    "sequence_no": "3",
                    "payload": "event: message\ndata: {\"content\": \"继续\"}\n\n",
                },
                {
                    "stream_id": "new-done-0",
                    "event_type": "done",
                    "sequence_no": "4",
                    "payload": "event: done\ndata: [DONE]\n\n",
                },
            ]

    service = AgentRunEventService(bus=ResumeBus())
    events = [event async for event in service.stream(run_id="run-1", last_event_id="old-tail-0")]

    assert [event["event_type"] for event in events] == ["message", "done"]
    assert all("question_requested" not in event["payload"] for event in events)
@pytest.mark.asyncio
async def test_latest_event_id_prefers_redis_tail(monkeypatch):
    class Bus:
        async def latest_run_event_id(self, _run_id):
            return "1740000000002-0"

    service = AgentRunEventService(bus=Bus())

    def unexpected_database_access():
        raise AssertionError("Redis 可用时不应读取 MySQL 恢复游标")

    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.agent_run_event_service.get_session",
        unexpected_database_access,
    )

    assert await service.latest_event_id("run-1") == "1740000000002-0"


@pytest.mark.asyncio
async def test_wait_for_done_waits_until_terminal_event(monkeypatch):
    class Bus:
        def __init__(self):
            self.calls = 0

        async def latest_run_event(self, _run_id):
            self.calls += 1
            if self.calls == 1:
                return {"event_type": "question_requested"}
            return {"event_type": "done"}

    class EventDao:
        async def get_latest_event_type(self, _session, *, run_id):
            assert run_id == "run-1"
            return None

    service = AgentRunEventService(bus=Bus(), event_dao=EventDao())
    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.agent_run_event_service.get_session",
        lambda: Session(),
    )
    assert await service.wait_for_done("run-1", timeout=0.2) is True


@pytest.mark.asyncio
async def test_wait_for_done_falls_back_to_durable_terminal_event(monkeypatch):
    class Bus:
        async def latest_run_event(self, _run_id):
            return None

    class EventDao:
        async def get_latest_event_type(self, _session, *, run_id):
            assert run_id == "run-1"
            return "done"

    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.agent_run_event_service.get_session",
        lambda: Session(),
    )
    service = AgentRunEventService(bus=Bus(), event_dao=EventDao())

    assert await service.wait_for_done("run-1", timeout=0) is True


@pytest.mark.asyncio
async def test_latest_event_id_falls_back_to_mysql_when_redis_unavailable(monkeypatch):
    class Bus:
        async def latest_run_event_id(self, _run_id):
            raise ConnectionError("redis unavailable")

    class EventDao:
        async def get_latest_event_id(self, _session, *, run_id):
            assert run_id == "run-1"
            return "1740000000003-0"

    service = AgentRunEventService(bus=Bus(), event_dao=EventDao())

    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.agent_run_event_service.get_session",
        lambda: Session(),
    )

    assert await service.latest_event_id("run-1") == "1740000000003-0"


@pytest.mark.asyncio
async def test_resolve_sequence_prefers_redis_before_async_mysql_persistence(monkeypatch):
    class Bus:
        async def run_event_sequence(self, _run_id, event_id):
            assert event_id == "redis-tail-0"
            return 7

    service = AgentRunEventService(bus=Bus())

    def unexpected_database_access():
        raise AssertionError("Redis 中存在游标时不应依赖异步 MySQL 持久化")

    monkeypatch.setattr(
        "apps.chat_data_app.services.agent_run.agent_run_event_service.get_session",
        unexpected_database_access,
    )

    assert await service._resolve_sequence("run-1", "redis-tail-0") == 7


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
