"""agent_run_runtime 剩余分支：SSE 事件名、并发配置、租约丢失收口与设置读取兜底。"""

import asyncio

import pytest

from apps.chat_data_app.agent.run import agent_run_runtime as rt
from apps.chat_data_app.agent.run.agent_inbox_service import AgentRunLeaseLostError
from apps.chat_data_app.agent.run.agent_run_runtime import AgentRunRuntime, _RunExecutionState


class _Bus:
    async def start(self):
        return None

    async def close(self):
        return None


class _Events:
    def __init__(self, *, append_error=None, release_error=None):
        self.bus = _Bus()
        self.append_error = append_error
        self.release_error = release_error
        self.released = []

    async def claim_owner(self, *_args, **_kwargs):
        return None

    async def release_owner(self, run_id, _token):
        if self.release_error is not None:
            raise self.release_error
        self.released.append(run_id)

    async def append(self, **_kwargs):
        if self.append_error is not None:
            raise self.append_error
        return "stream-1"


class _Inbox:
    def __init__(self, *, prepared=None, finish_error=None):
        self.prepared = prepared
        self.finish_error = finish_error
        self.finished = []
        self.terminals = []

    async def prepare_pending_run(self, _run_id):
        return self.prepared

    async def heartbeat_run(self, _run_id, _token):
        return "active"

    async def finish_run(self, run_id, status, *_args):
        if self.finish_error is not None:
            raise self.finish_error
        self.finished.append((run_id, status))
        return status

    async def record_terminal_event(self, run_id, stream_id):
        self.terminals.append((run_id, stream_id))


def _runtime(inbox=None, events=None) -> AgentRunRuntime:
    return AgentRunRuntime(
        max_concurrency=1,
        inbox_service=inbox or _Inbox(),  # type: ignore[arg-type]
        event_service=events or _Events(),  # type: ignore[arg-type]
        event_bus=_Bus(),  # type: ignore[arg-type]
    )


def _state(**overrides) -> _RunExecutionState:
    values = {
        "run_id": "run-1",
        "conversation_id": "conv-1",
        "user_id": "u1",
        "operator_name": "用户一",
    }
    values.update(overrides)
    return _RunExecutionState(**values)  # type: ignore[arg-type]


def test_sse_event_type_returns_none_without_event_line():
    assert rt._sse_event_type("data: hello\n\n") is None
    assert rt._sse_event_type("event: message\ndata: x\n") == "message"


def test_max_concurrency_rejects_invalid_env(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_AGENT_MAX_CONCURRENCY", "many")
    with pytest.raises(ValueError, match="必须是正整数"):
        rt._max_concurrency()


def test_claim_pending_run_returns_none_when_nothing_to_claim():
    runtime = _runtime(inbox=_Inbox(prepared=None))
    assert asyncio.run(runtime._claim_pending_run(_state())) is None


def test_finalize_cancelled_and_failed_ignore_lost_lease():
    inbox = _Inbox(finish_error=AgentRunLeaseLostError("lost"))
    runtime = _runtime(inbox=inbox)
    prepared = {"owner_token": "token-1"}
    state = _state()

    asyncio.run(runtime._finalize_cancelled_run(state, prepared, asyncio.CancelledError("agent_run_cancelled")))
    asyncio.run(runtime._finalize_failed_run(state, prepared))

    assert state.terminal_ready is False
    assert state.terminal_payload is None


def test_terminal_event_publish_ignores_lost_lease():
    events = _Events(append_error=AgentRunLeaseLostError("lost"))
    runtime = _runtime(events=events)
    state = _state(terminal_payload="done", terminal_ready=True)

    asyncio.run(runtime._publish_terminal_event(state, {"owner_token": "token-1"}))


def test_release_ownership_swallows_redis_errors():
    events = _Events(release_error=RuntimeError("redis down"))
    runtime = _runtime(events=events)

    asyncio.run(runtime._release_ownership(_state(), {"owner_token": "token-1"}))
    assert events.released == []


def test_execute_rebuilds_semaphore_from_runtime_setting(monkeypatch):
    async def configured_capacity(_key, _default=None):
        return 3

    monkeypatch.setattr(rt, "get_runtime_setting", configured_capacity)
    runtime = _runtime(inbox=_Inbox(prepared=None))

    asyncio.run(runtime._execute(run_id="run-1", conversation_id="conv-1", user_id="u1", operator_name="用户一"))

    assert runtime._semaphore_capacity == 3
    assert runtime._semaphore._value == 3
    assert runtime._tasks == {}


def test_execute_keeps_capacity_when_setting_read_fails(monkeypatch):
    async def failing_setting(_key, _default=None):
        raise RuntimeError("setting table down")

    monkeypatch.setattr(rt, "get_runtime_setting", failing_setting)
    runtime = _runtime(inbox=_Inbox(prepared=None))

    asyncio.run(runtime._execute(run_id="run-1", conversation_id="conv-1", user_id="u1", operator_name="用户一"))

    assert runtime._semaphore_capacity == 1
