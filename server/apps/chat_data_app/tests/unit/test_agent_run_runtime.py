import time

import pytest

from apps.chat_data_app.agent.run.agent_inbox_service import (
    AgentRunCancellationResult,
    AgentRunCompletionResult,
    AgentRunReapResult,
)
from apps.chat_data_app.agent.run.agent_run_runtime import AgentRunRuntime


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


def _completion(status: str = "finished", next_run_id: str | None = None) -> AgentRunCompletionResult:
    return AgentRunCompletionResult(status, next_run_id)


class _ActiveInbox:
    async def heartbeat_run(self, _run_id, _owner_token):
        return "active"


@pytest.mark.asyncio
async def test_done_is_published_after_successful_generator_finalization(monkeypatch):
    """正常结束：先 complete_run 落库，再发布唯一的结构化 done（含 next_run_id）。"""
    order = []
    published_done = []

    class Inbox(_ActiveInbox):
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def complete_run(self, _run_id, _user_id, _operator_name, _owner_token):
            order.append("run_completed")
            return _completion(next_run_id="run-2")

        async def record_terminal_event(self, _run_id, stream_id):
            assert stream_id == "done-stream-0"
            order.append("terminal_recorded")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            order.append("owner_claimed")

        async def release_owner(self, _run_id, _owner_token):
            order.append("owner_released")

        async def append(self, **kwargs):
            if "event: done" in kwargs["payload"]:
                order.append("done_published")
                published_done.append(kwargs["payload"])
                return "done-stream-0"
            order.append("message_published")
            return "message-stream-0"

    async def stream():
        yield "event: message\ndata: {}\n\n"
        order.append("generator_finalized")

    runtime = AgentRunRuntime(
        inbox_service=Inbox(),
        event_service=Events(),
        event_bus=Events.bus,
    )

    def submit_next_run(**kwargs):
        assert kwargs == {
            "run_id": "run-2",
            "conversation_id": "conv-1",
            "user_id": "user-1",
            "operator_name": "用户一",
        }
        order.append("next_run_submitted")

    monkeypatch.setattr(runtime, "submit", submit_next_run)
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    # 终态顺序契约：执行结束 → 落库收口 → 发布 done → 游标落库 → 释放租约。
    assert order.index("generator_finalized") < order.index("run_completed")
    assert order.index("run_completed") < order.index("done_published")
    assert order.index("done_published") < order.index("terminal_recorded")
    assert order.index("terminal_recorded") < order.index("owner_released")
    assert order[-1] == "next_run_submitted"
    assert '"status": "finished"' in published_done[0]
    assert '"next_run_id": "run-2"' in published_done[0]


@pytest.mark.asyncio
async def test_non_finished_completion_does_not_submit_next_run(monkeypatch):
    """只有成功收口创建的 next_run 才能进入本进程 fast-path。"""
    submitted = []

    class Inbox(_ActiveInbox):
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def complete_run(self, *_args, **_kwargs):
            return _completion(status="waiting_user", next_run_id="should-not-run")

        async def record_terminal_event(self, _run_id, _stream_id):
            return None

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            return None

        async def release_owner(self, _run_id, _owner_token):
            return None

        async def append(self, **_kwargs):
            return "stream-0"

    async def stream():
        yield "event: message\ndata: {}\n\n"

    runtime = AgentRunRuntime(inbox_service=Inbox(), event_service=Events(), event_bus=Events.bus)
    monkeypatch.setattr(runtime, "submit", lambda **kwargs: submitted.append(kwargs))
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    assert submitted == []


@pytest.mark.asyncio
async def test_reaper_publishes_done_for_expired_run():
    """调度器回收过期 Run 后必须通知原 SSE 结束。"""
    order = []

    class Inbox:
        async def reap_expired_runs(self, limit):
            assert limit == 20
            return [AgentRunReapResult("run-1", "conv-1", "user-1", "failed", "2:reaper")]

        async def record_terminal_event(self, _run_id, _stream_id):
            order.append("terminal_recorded")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, owner_token):
            assert owner_token == "2:reaper"
            order.append("owner_claimed")

        async def append(self, **kwargs):
            assert kwargs["run_id"] == "run-1"
            assert '"status": "failed"' in kwargs["payload"]
            order.append("done_published")
            return "done-stream-0"

        async def release_owner(self, _run_id, _owner_token):
            order.append("owner_released")

    runtime = AgentRunRuntime(inbox_service=Inbox(), event_service=Events(), event_bus=Events.bus)
    await runtime._reap_expired_runs()

    assert order == ["owner_claimed", "done_published", "terminal_recorded", "owner_released"]


@pytest.mark.asyncio
async def test_completion_honors_concurrent_cancellation(monkeypatch):
    """流结束与停止并发时，以 MySQL 定稿的 cancelled 为准，不能误发 finished。"""
    published_done = []

    class Inbox(_ActiveInbox):
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def complete_run(self, *_args, **_kwargs):
            return _completion(status="cancelled")

        async def record_terminal_event(self, _run_id, _stream_id):
            return None

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            return None

        async def release_owner(self, _run_id, _owner_token):
            return None

        async def append(self, **kwargs):
            if "event: done" in kwargs["payload"]:
                published_done.append(kwargs["payload"])
            return "stream-0"

    async def stream():
        yield "event: message\ndata: {}\n\n"

    runtime = AgentRunRuntime(inbox_service=Inbox(), event_service=Events(), event_bus=Events.bus)
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    assert len(published_done) == 1
    assert '"status": "cancelled"' in published_done[0]
    assert '"status": "finished"' not in published_done[0]


@pytest.mark.asyncio
async def test_done_is_published_after_failed_run_is_closed(monkeypatch):
    """失败结束：finish_run("failed") 先落库，再发布 status=failed 的 done。"""
    order = []
    published_done = []

    class Inbox(_ActiveInbox):
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def finish_run(self, run_id, status, *_args, **_kwargs):
            assert status == "failed"
            order.append("run_failed")
            return "failed"

        async def record_terminal_event(self, _run_id, _stream_id):
            order.append("terminal_recorded")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            return None

        async def release_owner(self, _run_id, _owner_token):
            order.append("owner_released")

        async def append(self, **kwargs):
            if "event: done" in kwargs["payload"]:
                order.append("done_published")
                published_done.append(kwargs["payload"])
                return "done-stream-0"
            order.append("event_published")
            return "event-stream-0"

    async def stream():
        yield "event: error\ndata: {}\n\n"
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
    assert '"status": "failed"' in published_done[0]


@pytest.mark.asyncio
async def test_handler_stream_failure_exception_closes_run_as_failed(monkeypatch):
    """真实链路契约：Handler 生成器产出 error 事实后抛 AgentRunStreamFailedError，
    Runtime 必须走 finish_run("failed") 收口，绝不能落入 complete_run 误标 finished。"""
    from apps.chat_data_app.agent_stream import AgentRunStreamFailedError

    order = []
    published_done = []

    class Inbox(_ActiveInbox):
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def finish_run(self, _run_id, status, *_args, **_kwargs):
            assert status == "failed"
            order.append("run_failed")
            return "failed"

        async def complete_run(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("失败流不应触发 complete_run")

        async def record_terminal_event(self, _run_id, _stream_id):
            order.append("terminal_recorded")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            return None

        async def release_owner(self, _run_id, _owner_token):
            order.append("owner_released")

        async def append(self, **kwargs):
            if "event: done" in kwargs["payload"]:
                order.append("done_published")
                published_done.append(kwargs["payload"])
                return "done-stream-0"
            order.append("error_published")
            return "error-stream-0"

    async def stream():
        yield 'event: error\ndata: {"error": "智能问数执行失败，请稍后重试。"}\n\n'
        raise AgentRunStreamFailedError

    runtime = AgentRunRuntime(
        inbox_service=Inbox(),
        event_service=Events(),
        event_bus=Events.bus,
    )
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    assert order.index("error_published") < order.index("run_failed")
    assert order.index("run_failed") < order.index("done_published")
    assert order[-1] == "owner_released"
    assert '"status": "failed"' in published_done[0]


@pytest.mark.asyncio
async def test_waiting_user_publishes_done_without_complete_run(monkeypatch):
    """等待用户：mark_waiting_for_user 已落库，Runtime 只发布 done，不再收口。"""
    order = []
    published_done = []

    class Inbox(_ActiveInbox):
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def complete_run(self, *_args, **_kwargs):  # pragma: no cover - 不应被调用
            raise AssertionError("waiting_user 不应触发 complete_run")

        async def record_terminal_event(self, _run_id, _stream_id):
            order.append("terminal_recorded")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            return None

        async def release_owner(self, _run_id, _owner_token):
            order.append("owner_released")

        async def append(self, **kwargs):
            if "event: done" in kwargs["payload"]:
                order.append("done_published")
                published_done.append(kwargs["payload"])
                return "done-stream-0"
            order.append("question_published")
            return "question-stream-0"

    async def stream():
        yield 'event: question_requested\ndata: {"question_id": "q1"}\n\n'

    runtime = AgentRunRuntime(
        inbox_service=Inbox(),
        event_service=Events(),
        event_bus=Events.bus,
    )
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    assert "done_published" in order
    assert '"status": "waiting_user"' in published_done[0]
    assert order[-1] == "owner_released"


@pytest.mark.asyncio
async def test_cancelled_run_closes_and_publishes_cancelled_done(monkeypatch):
    """用户取消：finish_run("cancelled") 后发布 status=cancelled 的 done 进 Redis。"""
    import asyncio

    order = []
    published_done = []

    class Inbox(_ActiveInbox):
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def finish_run(self, _run_id, status, *_args, **_kwargs):
            assert status == "cancelled"
            order.append("run_cancelled")
            return "cancelled"

        async def record_terminal_event(self, _run_id, _stream_id):
            order.append("terminal_recorded")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            return None

        async def release_owner(self, _run_id, _owner_token):
            order.append("owner_released")

        async def append(self, **kwargs):
            if "event: done" in kwargs["payload"]:
                order.append("done_published")
                published_done.append(kwargs["payload"])
                return "done-stream-0"
            order.append("message_published")
            return "message-stream-0"

    async def stream():
        yield "event: message\ndata: {}\n\n"
        raise asyncio.CancelledError("agent_run_cancelled")

    runtime = AgentRunRuntime(
        inbox_service=Inbox(),
        event_service=Events(),
        event_bus=Events.bus,
    )
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    with pytest.raises(asyncio.CancelledError):
        await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    assert order.index("run_cancelled") < order.index("done_published")
    assert order.index("done_published") < order.index("terminal_recorded")
    assert order[-1] == "owner_released"
    assert '"status": "cancelled"' in published_done[0]


@pytest.mark.asyncio
async def test_cancelling_at_claim_still_closes_run_and_releases_owner(monkeypatch):
    """认领后心跳即读到 cancelling：prepared 虽未从 helper 返回，
    取消收口（finish_run + owner 释放）仍必须完整执行。"""
    import asyncio

    order = []
    published_done = []

    class Inbox:
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def heartbeat_run(self, _run_id, _owner_token):
            return "cancelling"

        async def finish_run(self, _run_id, status, *_args, **_kwargs):
            assert status == "cancelled"
            order.append("run_cancelled")
            return "cancelled"

        async def record_terminal_event(self, _run_id, _stream_id):
            order.append("terminal_recorded")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            order.append("owner_claimed")

        async def release_owner(self, _run_id, _owner_token):
            order.append("owner_released")

        async def append(self, **kwargs):
            if "event: done" in kwargs["payload"]:
                order.append("done_published")
                published_done.append(kwargs["payload"])
                return "done-stream-0"
            order.append("message_published")
            return "message-stream-0"

    async def stream():  # pragma: no cover - 认领期取消，流不应被消费
        raise AssertionError("认领期取消不应进入流消费")

    runtime = AgentRunRuntime(
        inbox_service=Inbox(),
        event_service=Events(),
        event_bus=Events.bus,
    )
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    with pytest.raises(asyncio.CancelledError):
        await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    assert order == [
        "owner_claimed",
        "run_cancelled",
        "done_published",
        "terminal_recorded",
        "owner_released",
    ]
    assert '"status": "cancelled"' in published_done[0]


@pytest.mark.asyncio
async def test_claim_owner_failure_finalizes_unexecuted_run(monkeypatch):
    """claim_owner 失败（典型为 Redis 不可达）：prepare 已把行置为 ACTIVE，
    必须立即 finish_run("failed") 收口，否则行成为无主僵尸、SSE 一直假进行中。"""
    order = []

    class Inbox(_ActiveInbox):
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def finish_run(self, run_id, status, user_id, operator_name, owner_token=None):
            assert (run_id, status, user_id, operator_name, owner_token) == (
                "run-1",
                "failed",
                "user-1",
                "用户一",
                "1:owner",
            )
            order.append("run_failed")
            return "failed"

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            raise TimeoutError("Timeout reading from redis")

        async def release_owner(self, _run_id, _owner_token):  # pragma: no cover
            raise AssertionError("未认领成功不应释放 owner")

        async def append(self, **_kwargs):  # pragma: no cover
            raise AssertionError("未执行不应发布事件")

    async def stream():  # pragma: no cover
        raise AssertionError("认领失败不应进入流消费")

    runtime = AgentRunRuntime(
        inbox_service=Inbox(),
        event_service=Events(),
        event_bus=Events.bus,
    )
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    # _execute 对普通异常记 ERROR 后吞掉；关键断言是失败收口已执行
    await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    assert order == ["run_failed"]


@pytest.mark.asyncio
async def test_claim_owner_failure_propagates_original_error_when_finish_also_fails(monkeypatch, caplog):
    """finish_run 收口自身也失败时只记日志，不得掩盖原始 claim 异常。"""
    import logging

    class Inbox(_ActiveInbox):
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def finish_run(self, *_args, **_kwargs):
            raise ConnectionError("mysql also down")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            raise TimeoutError("Timeout reading from redis")

        async def release_owner(self, _run_id, _owner_token):  # pragma: no cover
            raise AssertionError("未认领成功不应释放 owner")

        async def append(self, **_kwargs):  # pragma: no cover
            raise AssertionError("未执行不应发布事件")

    async def stream():  # pragma: no cover
        raise AssertionError("认领失败不应进入流消费")

    runtime = AgentRunRuntime(
        inbox_service=Inbox(),
        event_service=Events(),
        event_bus=Events.bus,
    )
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)
    # get_logger 默认 propagate=False，caplog 收不到记录；测试期临时放开
    from apps.chat_data_app.agent.run import agent_run_runtime as runtime_module

    monkeypatch.setattr(runtime_module.logger, "propagate", True)

    # _execute 对普通异常记 ERROR 后吞掉；断言顶层日志携带的异常是原始
    # TimeoutError（而非被 finish 的 ConnectionError 掩盖）
    with caplog.at_level(logging.ERROR):
        await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    error_records = [r for r in caplog.records if "后台运行失败" in r.getMessage()]
    assert error_records, "顶层失败日志缺失"
    assert all(rec.exc_info and rec.exc_info[0] is TimeoutError for rec in error_records)
    # 收口失败也应有独立日志（含 ConnectionError），两条异常都可查
    finish_logs = [r for r in caplog.records if "收口也失败" in r.getMessage()]
    assert finish_logs and all(rec.exc_info and rec.exc_info[0] is ConnectionError for rec in finish_logs)


@pytest.mark.asyncio
async def test_lease_lost_cancellation_publishes_nothing(monkeypatch):
    """租约丢失的取消不收口也不发布——流已归新 Worker 所有。"""
    import asyncio

    class Inbox(_ActiveInbox):
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def finish_run(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("租约丢失不应收口")

        async def record_terminal_event(self, _run_id, _stream_id):  # pragma: no cover
            raise AssertionError("租约丢失不应记录终态游标")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            return None

        async def release_owner(self, _run_id, _owner_token):
            pass

        async def append(self, **kwargs):  # pragma: no cover
            raise AssertionError("租约丢失不应发布任何事件")

    async def stream():
        raise asyncio.CancelledError("agent_run_lease_lost")
        yield  # pragma: no cover - 保持异步生成器类型

    runtime = AgentRunRuntime(
        inbox_service=Inbox(),
        event_service=Events(),
        event_bus=Events.bus,
    )
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    with pytest.raises(asyncio.CancelledError):
        await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")


@pytest.mark.asyncio
async def test_terminal_record_failure_does_not_break_owner_release(monkeypatch):
    """终止游标落库失败只降级（Redis 内 done 仍可用），不影响收尾。"""
    order = []
    published_done = []

    class Inbox(_ActiveInbox):
        async def prepare_pending_run(self, _run_id):
            return _prepared()

        async def complete_run(self, *_args, **_kwargs):
            return _completion()

        async def record_terminal_event(self, _run_id, _stream_id):
            raise RuntimeError("mysql unavailable")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            return None

        async def release_owner(self, _run_id, _owner_token):
            order.append("owner_released")

        async def append(self, **kwargs):
            if "event: done" in kwargs["payload"]:
                order.append("done_published")
                published_done.append(kwargs["payload"])
            return "stream-0"

    async def stream():
        yield "event: message\ndata: {}\n\n"

    runtime = AgentRunRuntime(
        inbox_service=Inbox(),
        event_service=Events(),
        event_bus=Events.bus,
    )
    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)

    await runtime._execute(run_id="run-1", conversation_id="", user_id="", operator_name="")

    assert order == ["done_published", "owner_released"]
    assert '"status": "finished"' in published_done[0]


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

    def fake_stream_generator(request):
        # 流式执行入参已收敛为 ChatDataStreamRequest（单一对象）
        captured.update(
            {
                "thread_id": request.thread_id,
                "run_id": request.run_id,
                "owner_token": request.owner_token,
            }
        )

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


def test_build_stream_factory_prefers_run_row_arrival_stamp():
    """Run 行带的请求到达墙钟原样透传（用户气泡对齐口径）。"""
    captured = {}

    def fake_stream_generator(request):
        captured["stream_started_wall_ms"] = request.stream_started_wall_ms

        async def stream():
            yield "event: done\ndata: [DONE]\n\n"

        return stream()

    runtime = AgentRunRuntime(
        inbox_service=object(),
        event_service=type("Events", (), {"bus": _EventBus()})(),
        event_bus=_EventBus(),
    )
    runtime.set_stream_generator(fake_stream_generator)
    prepared = _prepared()
    prepared["stream_started_wall_ms"] = 1700000000000
    stream = runtime._build_stream_factory(prepared)()
    assert captured["stream_started_wall_ms"] == 1700000000000
    stream.aclose()


def test_build_stream_factory_falls_back_to_dispatch_wall_ms_without_stamp():
    """Run 行无盖章（定时任务/continuation）时按派发时刻兜底，保持原行为。"""
    captured = {}

    def fake_stream_generator(request):
        captured["stream_started_wall_ms"] = request.stream_started_wall_ms

        async def stream():
            yield "event: done\ndata: [DONE]\n\n"

        return stream()

    runtime = AgentRunRuntime(
        inbox_service=object(),
        event_service=type("Events", (), {"bus": _EventBus()})(),
        event_bus=_EventBus(),
    )
    runtime.set_stream_generator(fake_stream_generator)
    before_ms = int(time.time() * 1000)
    stream = runtime._build_stream_factory(_prepared())()
    after_ms = int(time.time() * 1000)
    assert before_ms <= captured["stream_started_wall_ms"] <= after_ms
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
    runtime._owned_runs.add("run-1")
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


@pytest.mark.asyncio
async def test_cancel_pending_run_publishes_terminal_after_mysql_close():
    order = []

    class Inbox:
        async def cancel_active_run(self, *_args):
            order.append("mysql_closed")
            return AgentRunCancellationResult(
                cancelled=True,
                status="cancelled",
                terminal_owner_token="1:cancel",
            )

        async def record_terminal_event(self, _run_id, _stream_id):
            order.append("terminal_recorded")

    class Events:
        bus = _EventBus()

        async def claim_owner(self, _run_id, _owner_token):
            order.append("owner_claimed")

        async def append(self, **kwargs):
            order.append("done_published")
            assert kwargs["event_id"] == "run-1:terminal:1:cancel"
            assert '"status": "cancelled"' in kwargs["payload"]
            return "done-stream-0"

        async def release_owner(self, _run_id, _owner_token):
            order.append("owner_released")

    runtime = AgentRunRuntime(inbox_service=Inbox(), event_service=Events(), event_bus=Events.bus)

    cancelled = await runtime.cancel_run(
        run_id="run-1",
        conversation_id="conv-1",
        user_id="user-1",
        operator_name="用户一",
    )

    assert cancelled is True
    assert order == [
        "mysql_closed",
        "owner_claimed",
        "done_published",
        "terminal_recorded",
        "owner_released",
    ]


def test_build_stream_factory_passes_unattended_flag():
    """runtime_context 的无人值守标记透传到流请求：定时轮装配据此剔除
    ask_user_question；chat 轮无标记时保持 False。"""
    captured: dict = {}

    def fake_stream_generator(request):
        captured["request"] = request
        yield ""

    runtime = AgentRunRuntime(inbox_service=object(), event_service=object(), event_bus=object())
    runtime.set_stream_generator(fake_stream_generator)
    prepared = _prepared()
    prepared["runtime_context"] = {"query": "汇总", "disable_user_interaction": True}
    factory = runtime._build_stream_factory(prepared)
    iterator = factory()
    next(iter(iterator), None)
    assert captured["request"].disable_user_interaction is True

    prepared["runtime_context"] = {"query": "汇总"}
    factory = runtime._build_stream_factory(prepared)
    next(iter(factory()), None)
    assert captured["request"].disable_user_interaction is False
