import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services.conversation_runtime import stream_agent_turn


class _FakeStreamAdapter:
    def __init__(self, events: list[str], *, disconnected: bool = False, error: Exception | None = None):
        self.events = events
        self.disconnected = disconnected
        self.error = error
        self.finish_calls: list[dict] = []

    async def stream_events(self):
        for event in self.events:
            yield event
        if self.error:
            raise self.error

    async def finish(self, **kwargs):
        self.finish_calls.append(kwargs)
        return {"timestamp": "2026-07-25 20:30:00"}


async def _collect(stream):
    return [item async for item in stream]


@pytest.mark.asyncio
async def test_stream_agent_turn_runs_persistence_and_resource_lifecycle_in_order():
    calls: list[str] = []
    adapter = _FakeStreamAdapter(["event: message\n\n"])

    async def hook(name: str):
        calls.append(name)

    async def finalize_resources(success: bool):
        calls.append(f"finalize:{success}")

    events = await _collect(stream_agent_turn(
        stream_adapter=adapter,
        format_sse=lambda event, _data: f"event: {event}\n\n",
        backfill_history=lambda: hook("backfill"),
        persist_user_message=lambda: hook("user"),
        before_stream=lambda: _before_stream(calls),
        persist_turn_history=lambda: hook("history"),
        persist_partial_history=lambda: hook("partial"),
        finalize_resources=finalize_resources,
        finalize_stream_kwargs={"persist_latest_ai_timestamp": True},
        after_turn=lambda: hook("after"),
    ))

    assert events == ["event: message\n\n", "event: message_end\n\n", "event: done\n\n"]
    assert adapter.finish_calls == [{"persist_latest_ai_timestamp": True}]
    assert calls == ["backfill", "user", "before", "finalize:True", "history", "after"]


@pytest.mark.asyncio
async def test_stream_agent_turn_persists_disconnected_turn_without_end_events():
    calls: list[str] = []
    adapter = _FakeStreamAdapter([], disconnected=True)

    async def hook(name: str):
        calls.append(name)

    async def finalize_resources(success: bool):
        calls.append(f"finalize:{success}")

    events = await _collect(stream_agent_turn(
        stream_adapter=adapter,
        format_sse=lambda event, _data: f"event: {event}\n\n",
        backfill_history=lambda: hook("backfill"),
        persist_user_message=lambda: hook("user"),
        persist_turn_history=lambda: hook("history"),
        persist_partial_history=lambda: hook("partial"),
        finalize_resources=finalize_resources,
    ))

    assert events == []
    assert adapter.finish_calls == [{}]
    assert calls == ["backfill", "user", "history", "finalize:True"]


@pytest.mark.asyncio
async def test_stream_agent_turn_persists_partial_history_and_cleans_resources_on_error():
    calls: list[str] = []
    adapter = _FakeStreamAdapter([], error=RuntimeError("stream failed"))

    async def hook(name: str):
        calls.append(name)

    async def finalize_resources(success: bool):
        calls.append(f"finalize:{success}")

    with pytest.raises(RuntimeError, match="stream failed"):
        await _collect(stream_agent_turn(
            stream_adapter=adapter,
            format_sse=lambda event, _data: f"event: {event}\n\n",
            backfill_history=lambda: hook("backfill"),
            persist_user_message=lambda: hook("user"),
            persist_turn_history=lambda: hook("history"),
            persist_partial_history=lambda: hook("partial"),
            finalize_resources=finalize_resources,
        ))

    assert calls == ["backfill", "user", "partial", "finalize:False"]


async def _before_stream(calls: list[str]) -> bool:
    calls.append("before")
    return True
