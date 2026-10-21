import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.agent.run import conversation_runtime
from apps.chat_data_app.agent.run.conversation_runtime import (
    TurnLifecycleHooks,
    TurnPersistenceHooks,
    TurnResourceOptions,
    stream_agent_turn,
)


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


async def _noop_async():
    return None


@pytest.mark.asyncio
async def test_stream_agent_turn_runs_persistence_and_resource_lifecycle_in_order():
    calls: list[str] = []
    adapter = _FakeStreamAdapter(["event: message\n\n"])

    async def hook(name: str):
        calls.append(name)

    async def finalize_resources(success: bool):
        calls.append(f"finalize:{success}")

    events = await _collect(
        stream_agent_turn(
            stream_adapter=adapter,
            format_sse=lambda event, _data: f"event: {event}\n\n",
            persistence=TurnPersistenceHooks(
                persist_user_message=lambda: hook("user"),
                persist_turn_history=lambda: hook("history"),
                persist_partial_history=lambda: hook("partial"),
            ),
            hooks=TurnLifecycleHooks(
                before_stream=lambda: _before_stream(calls),
                after_turn=lambda: hook("after"),
            ),
            resources=TurnResourceOptions(
                finalize_resources=finalize_resources,
                finalize_stream_kwargs={"persist_latest_ai_timestamp": True},
            ),
        )
    )

    assert events == ["event: message\n\n", "event: message_end\n\n"]
    assert adapter.finish_calls == [{"persist_latest_ai_timestamp": True}]
    assert set(calls[:2]) == {"user", "before"}
    assert calls[2:] == ["history", "finalize:True", "after"]


@pytest.mark.asyncio
async def test_stream_agent_turn_starts_model_before_slow_user_persistence_finishes():
    user_persistence_started = asyncio.Event()
    model_started = asyncio.Event()
    release_user_persistence = asyncio.Event()

    async def persist_user():
        user_persistence_started.set()
        await release_user_persistence.wait()

    class Adapter(_FakeStreamAdapter):
        async def stream_events(self):
            await user_persistence_started.wait()
            model_started.set()
            release_user_persistence.set()
            yield "event: message\n\n"

    events = await _collect(
        stream_agent_turn(
            stream_adapter=Adapter([]),
            format_sse=lambda event, _data: f"event: {event}\n\n",
            persistence=TurnPersistenceHooks(
                persist_user_message=persist_user,
                persist_turn_history=lambda: _noop_async(),
                persist_partial_history=lambda: _noop_async(),
            ),
            hooks=TurnLifecycleHooks(),
            resources=TurnResourceOptions(),
        )
    )

    assert model_started.is_set()
    assert events == ["event: message\n\n", "event: message_end\n\n"]


@pytest.mark.asyncio
async def test_stream_agent_turn_persists_disconnected_turn_without_end_events():
    calls: list[str] = []
    adapter = _FakeStreamAdapter([], disconnected=True)

    async def hook(name: str):
        calls.append(name)

    async def finalize_resources(success: bool):
        calls.append(f"finalize:{success}")

    events = await _collect(
        stream_agent_turn(
            stream_adapter=adapter,
            format_sse=lambda event, _data: f"event: {event}\n\n",
            persistence=TurnPersistenceHooks(
                persist_user_message=lambda: hook("user"),
                persist_turn_history=lambda: hook("history"),
                persist_partial_history=lambda: hook("partial"),
            ),
            hooks=TurnLifecycleHooks(),
            resources=TurnResourceOptions(
                finalize_resources=finalize_resources,
            ),
        )
    )

    assert events == []
    assert adapter.finish_calls == [{}]
    assert calls == ["user", "history", "finalize:True"]


@pytest.mark.asyncio
async def test_stream_agent_turn_notifies_disconnect_before_cleanup():
    calls: list[str] = []
    adapter = _FakeStreamAdapter([], disconnected=True)

    async def hook(name: str):
        calls.append(name)

    events = await _collect(
        stream_agent_turn(
            stream_adapter=adapter,
            format_sse=lambda event, _data: f"event: {event}\n\n",
            persistence=TurnPersistenceHooks(
                persist_user_message=lambda: hook("user"),
                persist_turn_history=lambda: hook("history"),
                persist_partial_history=lambda: hook("partial"),
            ),
            hooks=TurnLifecycleHooks(
                on_disconnect=lambda: hook("disconnect"),
            ),
            resources=TurnResourceOptions(),
        )
    )

    assert events == []
    assert calls == ["user", "history", "disconnect"]


@pytest.mark.asyncio
async def test_stream_agent_turn_persists_partial_history_and_cleans_resources_on_error():
    calls: list[str] = []
    adapter = _FakeStreamAdapter([], error=RuntimeError("stream failed"))

    async def hook(name: str):
        calls.append(name)

    async def finalize_resources(success: bool):
        calls.append(f"finalize:{success}")

    with pytest.raises(RuntimeError, match="stream failed"):
        await _collect(
            stream_agent_turn(
                stream_adapter=adapter,
                format_sse=lambda event, _data: f"event: {event}\n\n",
                persistence=TurnPersistenceHooks(
                    persist_user_message=lambda: hook("user"),
                    persist_turn_history=lambda: hook("history"),
                    persist_partial_history=lambda: hook("partial"),
                ),
                hooks=TurnLifecycleHooks(),
                resources=TurnResourceOptions(
                    finalize_resources=finalize_resources,
                ),
            )
        )

    assert calls == ["user", "partial", "finalize:False"]


@pytest.mark.asyncio
async def test_stream_agent_turn_emits_final_events_once():
    calls: list[str] = []
    adapter = _FakeStreamAdapter(["event: message\n\n"])

    async def hook(name: str):
        calls.append(name)

    events = await _collect(
        stream_agent_turn(
            stream_adapter=adapter,
            format_sse=lambda event, _data: f"event: {event}\n\n",
            persistence=TurnPersistenceHooks(
                persist_user_message=lambda: hook("user"),
                persist_turn_history=lambda: hook("history"),
                persist_partial_history=lambda: hook("partial"),
            ),
            hooks=TurnLifecycleHooks(
                after_stream=lambda: hook("after_stream"),
            ),
            resources=TurnResourceOptions(),
        )
    )

    assert events == [
        "event: message\n\n",
        "event: message_end\n\n",
    ]
    assert calls == ["user", "history", "after_stream"]


@pytest.mark.asyncio
async def test_stream_agent_turn_runs_after_finish_before_history():
    calls: list[str] = []
    adapter = _FakeStreamAdapter(["event: message\n\n"])

    async def hook(name: str):
        calls.append(name)

    events = await _collect(
        stream_agent_turn(
            stream_adapter=adapter,
            format_sse=lambda event, _data: f"event: {event}\n\n",
            persistence=TurnPersistenceHooks(
                persist_user_message=lambda: hook("user"),
                persist_turn_history=lambda: hook("history"),
                persist_partial_history=lambda: hook("partial"),
            ),
            hooks=TurnLifecycleHooks(
                after_finish=lambda: hook("checkpointed"),
            ),
            resources=TurnResourceOptions(),
        )
    )

    assert events[-1:] == ["event: message_end\n\n"]
    assert calls == ["user", "checkpointed", "history"]


async def _before_stream(calls: list[str]) -> bool:
    calls.append("before")
    return True


@pytest.mark.asyncio
async def test_stream_agent_turn_waits_for_user_persistence_on_cancel():
    """前端断开/取消时，已显示的用户消息必须先落库，下一轮才能看到上下文。"""
    persisted = asyncio.Event()

    class Adapter(_FakeStreamAdapter):
        async def stream_events(self):
            yield "event: message\n\n"
            await asyncio.Event().wait()

    async def persist_user():
        await asyncio.sleep(0.01)
        persisted.set()

    stream = stream_agent_turn(
        stream_adapter=Adapter([]),
        format_sse=lambda event, _data: f"event: {event}\n\n",
        persistence=TurnPersistenceHooks(
            persist_user_message=persist_user,
            persist_turn_history=_noop_async,
            persist_partial_history=_noop_async,
        ),
    )
    task = asyncio.create_task(_collect(stream))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert persisted.is_set()


@pytest.mark.asyncio
async def test_await_user_persistence_safely_has_timeout(monkeypatch):
    """取消路径等待落库必须有上限，DB 卡住不能拖死停止收口。"""
    from apps.chat_data_app.agent.run import conversation_runtime

    monkeypatch.setattr(conversation_runtime, "USER_PERSISTENCE_CANCEL_WAIT_SECONDS", 0.01)

    async def slow():
        await asyncio.sleep(5)

    task = asyncio.create_task(slow())
    started = asyncio.get_running_loop().time()
    await conversation_runtime._await_user_persistence_safely(task)
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 1, f"取消等待未受超时约束，实际耗时 {elapsed:.2f}s"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_await_user_persistence_safely_none_task_is_noop():
    from apps.chat_data_app.agent.run import conversation_runtime

    await conversation_runtime._await_user_persistence_safely(None)


# ============ 收口辅助函数分支覆盖 ============


@pytest.mark.asyncio
async def test_abort_user_persistence_cancels_pending_and_handles_none():
    """未完成任务被取消收敛；None 任务直接跳过。"""

    async def slow():
        await asyncio.sleep(5)

    task = asyncio.create_task(slow())
    await conversation_runtime._abort_user_persistence(task)  # type: ignore[arg-type]
    assert task.cancelled()
    await conversation_runtime._abort_user_persistence(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_stream_turn_wait_user_persistence_none_is_noop():
    await conversation_runtime._stream_turn_wait_user_persistence(None)


@pytest.mark.asyncio
async def test_maybe_invoke_interrupt_hook_none_is_noop():
    await conversation_runtime._maybe_invoke_interrupt_hook(None, {"q": 1})
