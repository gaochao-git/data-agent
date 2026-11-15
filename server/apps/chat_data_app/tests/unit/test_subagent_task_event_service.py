"""后台子智能体实时事件流：Redis 重放与同步 writer 顺序。"""

import pytest

from apps.chat_data_app.agent.run.subagent_task_event_service import (
    SubagentTaskEventService,
    SubagentTaskStreamWriter,
)


class _ReplayBus:
    def __init__(self, events=None):
        self.events = list(events or [])
        self.read_calls = 0

    async def read_run(self, task_id, cursor, *, block_ms, count):
        self.read_calls += 1
        if self.read_calls == 1:
            return self.events
        return []


class _WriterService:
    def __init__(self, *, claim_error=None):
        self.claim_error = claim_error
        self.claimed: list[str] = []
        self.released: list[tuple[str, str]] = []
        self.published: list[dict] = []

    async def claim_stream(self, task_id):
        if self.claim_error:
            raise self.claim_error
        self.claimed.append(task_id)
        return "1:owner"

    async def release_stream(self, task_id, owner_token):
        self.released.append((task_id, owner_token))

    async def publish_event(self, **kwargs):
        self.published.append(kwargs)


@pytest.mark.asyncio
async def test_task_event_stream_replays_then_stops_on_terminal():
    payload = 'event: subagent_message_delta\ndata: {"delta":"hi"}\n\n'
    bus = _ReplayBus([{"stream_id": "1-0", "payload": payload}])
    service = SubagentTaskEventService(bus=bus)

    events = []
    async for event in service.stream(task_id="t1", is_terminal=lambda: True):
        events.append(event)

    assert [item["stream_id"] for item in events] == ["1-0"]
    assert events[0]["payload"] == payload
    assert bus.read_calls == 2


@pytest.mark.asyncio
async def test_task_stream_writer_flushes_events_in_call_order():
    service = _WriterService()
    writer = SubagentTaskStreamWriter(
        service,
        task_id="t1",
        conversation_id="c1",
        user_id="u1",
    )
    await writer.start()
    writer({"type": "subagent_tool", "event": {"event_type": "tool_started", "tool_call_id": "c1"}})
    writer({"event_type": "subagent_message_delta", "message_id": "m1", "delta": "你好"})
    await writer.aclose()

    assert [item["event"]["event_type"] for item in service.published] == [
        "tool_started",
        "subagent_message_delta",
    ]
    assert service.claimed == ["t1"]
    assert service.released == [("t1", "1:owner")]


@pytest.mark.asyncio
async def test_task_stream_writer_disables_without_claim():
    service = _WriterService(claim_error=RuntimeError("redis down"))
    writer = SubagentTaskStreamWriter(
        service,
        task_id="t1",
        conversation_id="c1",
        user_id="u1",
    )
    await writer.start()
    writer({"event_type": "subagent_message_delta", "delta": "不应写入"})
    await writer.aclose()

    assert service.published == []
    assert service.claimed == []
    assert service.released == []
