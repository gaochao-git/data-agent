import json

import pytest

from apps.chat_data_app.agent.run.agent_inbox_service import AgentRunLeaseLostError
from apps.chat_data_app.agent.run.agent_run_event_service import AgentRunEventService, format_terminal_done
from apps.chat_data_app.agent.run.redis_event_bus import RedisRunOwnerMismatchError


class _Bus:
    async def latest_run_event(self, _run_id):
        return {"stream_id": "1740000000000-0", "event_type": "done"}

    async def latest_run_event_id(self, _run_id):
        return "1740000000000-0"

    async def read_run(self, _run_id, _cursor):
        return [
            {
                "stream_id": "1740000000000-0",
                "event_type": "done",
                "sequence_no": "2",
                "payload": "event: done\\ndata: [DONE]\\n\\n",
            }
        ]


class _InboxDao:
    """返回携带 terminal_event 的 Run 行，模拟 done 发布后的 inbox 状态。"""

    def __init__(self, content: dict | None = None, status: str = "waiting_user"):
        self._content = content
        self._status = status

    async def get_run_by_id(self, _session, run_id):
        class Row:
            pass

        row = Row()
        row.record_status = self._status
        row.conversation_id = "conversation-1"
        row.user_id = "user-1"
        row.record_content = json.dumps(self._content, ensure_ascii=False) if self._content is not None else ""
        return row


class _Session:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_append_never_opens_mysql_transaction(monkeypatch):
    class HotPathBus:
        def __init__(self):
            self.published = []

        async def publish(self, **kwargs):
            self.published.append(kwargs)
            return "1740000000000-0"

    bus = HotPathBus()
    service = AgentRunEventService(bus=bus)

    def unexpected_database_access():
        raise AssertionError("事件发布不应访问 MySQL")

    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
        unexpected_database_access,
    )

    stream_id = await service.append(
        run_id="run-1",
        conversation_id="conversation-1",
        user_id="user-1",
        event_id="event-1",
        payload="event: message\ndata: {}\n\n",
        owner_token="owner-1",
    )

    assert stream_id == "1740000000000-0"
    assert bus.published[0]["event_type"] == "message"


@pytest.mark.asyncio
async def test_append_maps_redis_fencing_to_run_lease_loss():
    class FencedBus:
        async def publish(self, **_kwargs):
            raise RedisRunOwnerMismatchError("run-1")

    service = AgentRunEventService(bus=FencedBus())

    with pytest.raises(AgentRunLeaseLostError):
        await service.append(
            run_id="run-1",
            conversation_id="conversation-1",
            user_id="user-1",
            event_id="event-1",
            payload="event: message\ndata: {}\n\n",
            owner_token="1:stale",
        )


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
        async def read_run(self, _run_id, cursor):
            assert cursor == "old-tail-0"
            return [
                {
                    "stream_id": "new-message-0",
                    "event_type": "message",
                    "sequence_no": "3",
                    "payload": 'event: message\ndata: {"content": "继续"}\n\n',
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
        raise AssertionError("Redis 可用时不应读取 inbox 恢复游标")

    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
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

    service = AgentRunEventService(bus=Bus(), inbox_dao=_InboxDao(content={}))
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
        lambda: _Session(),
    )
    assert await service.wait_for_done("run-1", timeout=0.2) is True


@pytest.mark.asyncio
async def test_wait_for_done_falls_back_to_inbox_terminal_event(monkeypatch):
    class Bus:
        async def latest_run_event(self, _run_id):
            return None

    service = AgentRunEventService(
        bus=Bus(),
        inbox_dao=_InboxDao(content={"terminal_event": {"stream_id": "1740000000003-0", "event_type": "done"}}),
    )
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
        lambda: _Session(),
    )

    assert await service.wait_for_done("run-1", timeout=0) is True


@pytest.mark.asyncio
async def test_wait_for_done_times_out_without_terminal_evidence(monkeypatch):
    class Bus:
        async def latest_run_event(self, _run_id):
            return None

    service = AgentRunEventService(bus=Bus(), inbox_dao=_InboxDao(content={}))
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
        lambda: _Session(),
    )

    assert await service.wait_for_done("run-1", timeout=0) is False


@pytest.mark.asyncio
async def test_latest_event_id_falls_back_to_inbox_when_redis_unavailable(monkeypatch):
    class Bus:
        async def latest_run_event_id(self, _run_id):
            raise ConnectionError("redis unavailable")

    service = AgentRunEventService(
        bus=Bus(),
        inbox_dao=_InboxDao(content={"terminal_event": {"stream_id": "1740000000003-0", "event_type": "done"}}),
    )
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
        lambda: _Session(),
    )

    assert await service.latest_event_id("run-1") == "1740000000003-0"


@pytest.mark.asyncio
async def test_latest_event_id_defaults_to_zero_cursor_without_terminal_record(monkeypatch):
    class Bus:
        async def latest_run_event_id(self, _run_id):
            return None

    service = AgentRunEventService(bus=Bus(), inbox_dao=_InboxDao(content={}))
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
        lambda: _Session(),
    )

    assert await service.latest_event_id("run-1") == "0-0"


@pytest.mark.asyncio
async def test_stream_ends_after_terminal_grace_when_stream_expired(monkeypatch):
    """Redis Stream 过期且 Run 已终态：由 MySQL 权威终态补发结构化 done。"""

    class ExpiredBus:
        async def read_run(self, _run_id, _cursor):
            return []

        async def run_stream_exists(self, _run_id):
            return False

    service = AgentRunEventService(
        bus=ExpiredBus(),
        inbox_dao=_InboxDao(content={"next_run_id": "run-2"}, status="finished"),
    )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
        lambda: _Session(),
    )
    monkeypatch.setattr("apps.chat_data_app.agent.run.agent_run_event_service.asyncio.sleep", no_sleep)
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.TERMINAL_PERSISTENCE_GRACE_SECONDS",
        0.0,
    )

    events = [event async for event in service.stream(run_id="run-1")]

    assert len(events) == 1
    assert events[0]["event_type"] == "done"
    assert '"status": "finished"' in events[0]["payload"]
    assert '"next_run_id": "run-2"' in events[0]["payload"]


@pytest.mark.asyncio
async def test_stream_keeps_waiting_while_run_is_active(monkeypatch):
    """流暂时为空但 Run 仍在运行：保持订阅直到事件到达。"""

    class LateBus:
        def __init__(self):
            self.reads = 0

        async def read_run(self, _run_id, _cursor):
            self.reads += 1
            if self.reads < 3:
                return []
            return [
                {
                    "stream_id": "1740000000001-0",
                    "event_type": "done",
                    "sequence_no": "1",
                    "payload": "event: done\ndata: [DONE]\n\n",
                }
            ]

        async def run_stream_exists(self, _run_id):
            return True

    service = AgentRunEventService(bus=LateBus(), inbox_dao=_InboxDao(content={}, status="active"))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
        lambda: _Session(),
    )
    monkeypatch.setattr("apps.chat_data_app.agent.run.agent_run_event_service.asyncio.sleep", no_sleep)

    events = [event async for event in service.stream(run_id="run-1")]

    assert [event["event_type"] for event in events] == ["done"]


def test_format_terminal_done_maps_succeeded_and_normalizes_unknown():
    succeeded = format_terminal_done("succeeded", "run-9")
    assert '"status": "finished"' in succeeded
    assert '"next_run_id": "run-9"' in succeeded

    weird = format_terminal_done("weird")
    assert '"status": "failed"' in weird
    assert "next_run_id" not in weird

    blank = format_terminal_done("finished", "   ")
    assert '"status": "finished"' in blank
    assert "next_run_id" not in blank


@pytest.mark.asyncio
async def test_wait_for_done_skips_durable_check_between_polls(monkeypatch):
    class MessageBus:
        async def latest_run_event(self, _run_id):
            return {"event_type": "message"}

    async def no_sleep(_seconds):
        return None

    service = AgentRunEventService(bus=MessageBus(), inbox_dao=_InboxDao(content={}))
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
        lambda: _Session(),
    )
    monkeypatch.setattr("apps.chat_data_app.agent.run.agent_run_event_service.asyncio.sleep", no_sleep)
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.QUESTION_DONE_DURABLE_POLL_SECONDS",
        10.0,
    )

    assert await service.wait_for_done("run-1", timeout=0.05) is False


@pytest.mark.asyncio
async def test_read_run_events_logs_failure_once_and_recovery():
    class FlakyBus:
        def __init__(self):
            self.calls = 0

        async def read_run(self, _run_id, _cursor):
            self.calls += 1
            if self.calls in (1, 3):
                raise ConnectionError("redis down")
            return [
                {
                    "stream_id": "1740000000001-0",
                    "event_type": "message",
                    "sequence_no": "1",
                    "payload": "event: message\ndata: {}\n\n",
                }
            ]

    service = AgentRunEventService(bus=FlakyBus())

    first = await service._read_run_events("run-1", "0-0", was_unavailable=False)
    assert first == ([], False, True)

    second = await service._read_run_events("run-1", "0-0", was_unavailable=True)
    assert second[1] is True
    assert second[2] is False
    assert len(second[0]) == 1

    third = await service._read_run_events("run-1", "0-0", was_unavailable=True)
    assert third == ([], False, True)


@pytest.mark.asyncio
async def test_wait_between_polls_sleeps_when_stream_missing(monkeypatch):
    class NoStreamBus:
        async def run_stream_exists(self, _run_id):
            return False

    service = AgentRunEventService(bus=NoStreamBus())
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("apps.chat_data_app.agent.run.agent_run_event_service.asyncio.sleep", fake_sleep)

    await service._wait_between_polls("run-1", False)
    await service._wait_between_polls("run-1", True)

    assert sleeps == [0.5, 0.5]


@pytest.mark.asyncio
async def test_stream_returns_immediately_when_client_disconnected():
    service = AgentRunEventService(bus=_Bus())

    async def disconnected():
        return True

    events = [event async for event in service.stream(run_id="run-1", is_disconnected=disconnected)]

    assert events == []


@pytest.mark.asyncio
async def test_stream_continues_after_non_done_events_and_skips_failed_snapshot(monkeypatch):
    class MessageThenEmptyBus:
        def __init__(self):
            self.reads = 0

        async def read_run(self, _run_id, _cursor):
            self.reads += 1
            if self.reads == 1:
                return [
                    {
                        "stream_id": "1740000000001-0",
                        "event_type": "message",
                        "sequence_no": "1",
                        "payload": "event: message\ndata: {}\n\n",
                    }
                ]
            return []

    class BoomDao:
        async def get_run_by_id(self, _session, _run_id):
            raise RuntimeError("db down")

    service = AgentRunEventService(bus=MessageThenEmptyBus(), inbox_dao=BoomDao())
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
        lambda: _Session(),
    )
    disconnect_calls = 0

    async def disconnected():
        nonlocal disconnect_calls
        disconnect_calls += 1
        return disconnect_calls >= 3

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("apps.chat_data_app.agent.run.agent_run_event_service.asyncio.sleep", no_sleep)

    events = [event async for event in service.stream(run_id="run-1", is_disconnected=disconnected)]

    assert len(events) == 1
    assert events[0]["event_type"] == "message"


@pytest.mark.asyncio
async def test_stream_ends_silently_when_snapshot_missing_after_grace(monkeypatch):
    class ExpiredBus:
        async def read_run(self, _run_id, _cursor):
            return []

        async def run_stream_exists(self, _run_id):
            return False

    class MissingRowDao:
        async def get_run_by_id(self, _session, _run_id):
            return None

    service = AgentRunEventService(bus=ExpiredBus(), inbox_dao=MissingRowDao())

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("apps.chat_data_app.agent.run.agent_run_event_service.get_session", lambda: _Session())
    monkeypatch.setattr("apps.chat_data_app.agent.run.agent_run_event_service.asyncio.sleep", no_sleep)
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.TERMINAL_PERSISTENCE_GRACE_SECONDS",
        0.0,
    )

    events = [event async for event in service.stream(run_id="run-1")]

    assert events == []


@pytest.mark.asyncio
async def test_stream_waits_within_grace_before_fallback(monkeypatch):
    class ExpiredBus:
        async def read_run(self, _run_id, _cursor):
            return []

        async def run_stream_exists(self, _run_id):
            return False

    service = AgentRunEventService(bus=ExpiredBus(), inbox_dao=_InboxDao(content={}, status="finished"))
    disconnect_calls = 0

    async def disconnected():
        nonlocal disconnect_calls
        disconnect_calls += 1
        return disconnect_calls >= 3

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("apps.chat_data_app.agent.run.agent_run_event_service.get_session", lambda: _Session())
    monkeypatch.setattr("apps.chat_data_app.agent.run.agent_run_event_service.asyncio.sleep", no_sleep)
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.TERMINAL_PERSISTENCE_GRACE_SECONDS",
        10.0,
    )

    events = [event async for event in service.stream(run_id="run-1", is_disconnected=disconnected)]

    assert events == []


@pytest.mark.asyncio
async def test_get_run_snapshot_returns_none_for_missing_row(monkeypatch):
    class MissingRowDao:
        async def get_run_by_id(self, _session, _run_id):
            return None

    service = AgentRunEventService(bus=_Bus(), inbox_dao=MissingRowDao())
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
        lambda: _Session(),
    )

    assert await service._get_run_snapshot("run-1") is None


@pytest.mark.asyncio
async def test_stream_fast_fallback_when_redis_unavailable_and_terminal(monkeypatch):
    """Redis 读已不可用且 Run 已终态：单次短等即合成兜底 done，不等满 3s。"""

    class DownBus:
        def __init__(self):
            self.reads = 0

        async def read_run(self, _run_id, _cursor):
            self.reads += 1
            raise ConnectionError("redis down")

        async def run_stream_exists(self, _run_id):
            return False

    down = DownBus()
    service = AgentRunEventService(bus=down, inbox_dao=_InboxDao(content={}, status="failed"))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.get_session",
        lambda: _Session(),
    )
    monkeypatch.setattr("apps.chat_data_app.agent.run.agent_run_event_service.asyncio.sleep", no_sleep)
    # 故意把正常宽限调大：证明收窄来自 unavailable 感知而非默认值
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.TERMINAL_PERSISTENCE_GRACE_SECONDS",
        100.0,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.agent.run.agent_run_event_service.TERMINAL_UNAVAILABLE_GRACE_SECONDS",
        0.0,
    )

    events = [event async for event in service.stream(run_id="run-1")]

    assert down.reads == 1  # 不可用宽限期内不再重读，避免黑洞链路重付连接超时
    assert len(events) == 1
    assert events[0]["event_type"] == "done"
    assert '"status": "failed"' in events[0]["payload"]
