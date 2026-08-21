import pytest

from apps.chat_data_app.services.agent_run.agent_run_runtime import AgentRunRuntime


class _EventBus:
    async def start(self):
        return None

    async def close(self):
        return None


def _prepared():
    return {
        "run_id": "run-1",
        "owner_token": "1:owner",
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "operator_name": "用户一",
        "runtime_context": {},
    }


@pytest.mark.asyncio
async def test_done_is_published_after_successful_generator_finalization(monkeypatch):
    order = []

    class Inbox:
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def record_terminal_event(self, _run_id, stream_id):
            assert stream_id == "done-stream-0"
            order.append("terminal_recorded")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            order.append("owner_claimed")

        async def release_owner(self, _run_id, _owner_token):
            order.append("owner_released")

        @staticmethod
        def is_terminal_payload(payload):
            return "event: done" in payload

        async def append(self, **kwargs):
            if "event: done" in kwargs["payload"]:
                order.append("done_published")
                return "done-stream-0"
            order.append("message_published")
            return "message-stream-0"

    async def stream():
        yield "event: message\ndata: {}\n\n"
        yield "event: done\ndata: [DONE]\n\n"
        order.append("generator_finalized")

    runtime = AgentRunRuntime(
        inbox_service=Inbox(),
        event_service=Events(),
        event_bus=Events.bus,
    )
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    assert order.index("generator_finalized") < order.index("done_published")
    # done 进入 Redis 后才把终止游标写入 inbox 行，供问答恢复兜底。
    assert order.index("done_published") < order.index("terminal_recorded")
    assert order[-1] == "owner_released"


@pytest.mark.asyncio
async def test_done_is_published_after_failed_run_is_closed(monkeypatch):
    order = []

    class Inbox:
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def finish_run(self, *_args):
            order.append("run_failed")

        async def record_terminal_event(self, _run_id, _stream_id):
            order.append("terminal_recorded")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            return None

        async def release_owner(self, _run_id, _owner_token):
            order.append("owner_released")

        @staticmethod
        def is_terminal_payload(payload):
            return "event: done" in payload

        async def append(self, **kwargs):
            if "event: done" in kwargs["payload"]:
                order.append("done_published")
                return "done-stream-0"
            order.append("event_published")
            return "event-stream-0"

    async def stream():
        yield "event: error\ndata: {}\n\n"
        yield "event: done\ndata: [DONE]\n\n"
        raise RuntimeError("agent failed")

    runtime = AgentRunRuntime(
        inbox_service=Inbox(),
        event_service=Events(),
        event_bus=Events.bus,
    )
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    assert order.index("run_failed") < order.index("done_published")
    assert order.index("done_published") < order.index("terminal_recorded")
    assert order[-1] == "owner_released"


@pytest.mark.asyncio
async def test_terminal_record_failure_does_not_break_owner_release(monkeypatch):
    """终止游标落库失败只降级（Redis 内 done 仍可用），不影响收尾。"""
    order = []

    class Inbox:
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def record_terminal_event(self, _run_id, _stream_id):
            raise RuntimeError("mysql unavailable")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            return None

        async def release_owner(self, _run_id, _owner_token):
            order.append("owner_released")

        @staticmethod
        def is_terminal_payload(payload):
            return "event: done" in payload

        async def append(self, **kwargs):
            if "event: done" in kwargs["payload"]:
                order.append("done_published")
            return "stream-0"

    async def stream():
        yield "event: done\ndata: [DONE]\n\n"

    runtime = AgentRunRuntime(
        inbox_service=Inbox(),
        event_service=Events(),
        event_bus=Events.bus,
    )
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    assert order == ["done_published", "owner_released"]


def test_build_stream_factory_requires_injected_stream_generator():
    runtime = AgentRunRuntime(
        inbox_service=object(),
        event_service=type("Events", (), {"bus": _EventBus()})(),
        event_bus=_EventBus(),
    )
    with pytest.raises(RuntimeError, match="stream_generator"):
        runtime._build_stream_factory(_prepared())


def test_set_stream_generator_is_used_by_stream_factory():
    captured = {}

    def fake_stream_generator(**kwargs):
        captured.update(kwargs)

        async def stream():
            yield "event: done\ndata: [DONE]\n\n"

        return stream()

    runtime = AgentRunRuntime(
        inbox_service=object(),
        event_service=type("Events", (), {"bus": _EventBus()})(),
        event_bus=_EventBus(),
    )
    runtime.set_stream_generator(fake_stream_generator)
    factory = runtime._build_stream_factory(_prepared())
    stream = factory()
    assert captured["run_id"] == "run-1"
    assert captured["owner_token"] == "1:owner"
    assert captured["thread_id"] == "conv-1"
    stream.aclose()

@pytest.mark.asyncio
async def test_cancel_local_cancels_in_process_task_with_cancel_reason():
    import asyncio

    runtime = AgentRunRuntime(
        inbox_service=object(),
        event_service=type("Events", (), {"bus": _EventBus()})(),
        event_bus=_EventBus(),
    )
    reason = {}

    async def running():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError as exc:
            reason["args"] = exc.args
            raise

    task = asyncio.create_task(running())
    runtime._tasks["run-1"] = task
    await asyncio.sleep(0)

    assert runtime.cancel_local("run-1") is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reason["args"] == ("agent_run_cancelled",)


@pytest.mark.asyncio
async def test_cancel_local_returns_false_when_task_absent_or_done():
    import asyncio

    runtime = AgentRunRuntime(
        inbox_service=object(),
        event_service=type("Events", (), {"bus": _EventBus()})(),
        event_bus=_EventBus(),
    )
    assert runtime.cancel_local("missing") is False

    async def finished():
        return None

    task = asyncio.create_task(finished())
    await task
    runtime._tasks["run-2"] = task
    assert runtime.cancel_local("run-2") is False
