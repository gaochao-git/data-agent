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
