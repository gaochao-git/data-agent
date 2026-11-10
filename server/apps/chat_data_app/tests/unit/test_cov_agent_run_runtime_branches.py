"""agent/run/agent_run_runtime.py 剩余分支：重复提交、调度空槽与收口兜底。"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from apps.chat_data_app.agent.run import agent_run_runtime as rt
from apps.chat_data_app.agent.run.agent_run_runtime import AgentRunRuntime, _RunExecutionState


class _Bus:
    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _Events:
    def __init__(self, *, claim_error: Exception | None = None) -> None:
        self.bus = _Bus()
        self.claim_error = claim_error
        self.released: list[str] = []
        self.appended: list[dict] = []

    async def claim_owner(self, *_args, **_kwargs) -> None:
        if self.claim_error is not None:
            raise self.claim_error

    async def release_owner(self, run_id: str, _token: str) -> None:
        self.released.append(run_id)

    async def append(self, **kwargs) -> str:
        self.appended.append(kwargs)
        return "stream-1"


class _Inbox:
    def __init__(self, *, finish_status: str | None = "cancelled") -> None:
        self.finish_status = finish_status
        self.finished: list[tuple] = []

    async def prepare_pending_run(self, _run_id: str):
        return None

    async def finish_run(self, run_id: str, status: str, *_args) -> str | None:
        self.finished.append((run_id, status))
        return self.finish_status

    async def record_terminal_event(self, _run_id: str, _stream_id: str) -> None:
        return None

    async def reap_expired_runs(self, **_kwargs):
        return []

    async def reap_expired_pending_runs(self, **_kwargs):
        return []


def _runtime(*, inbox=None, events=None, max_concurrency: int = 1, node_id: str | None = None) -> AgentRunRuntime:
    return AgentRunRuntime(
        max_concurrency=max_concurrency,
        inbox_service=inbox or _Inbox(),  # type: ignore[arg-type]
        event_service=events or _Events(),  # type: ignore[arg-type]
        event_bus=_Bus(),  # type: ignore[arg-type]
        node_id=node_id,
    )


def _state() -> _RunExecutionState:
    return _RunExecutionState(run_id="run-1", conversation_id="conv-1", user_id="u1", operator_name="用户一")


@pytest.fixture()
def stub_runtime_setting(monkeypatch):
    async def fake_setting(_key: str, _default: object = None) -> int:
        return 1

    monkeypatch.setattr(rt, "get_runtime_setting", fake_setting)


def test_submit_replaces_finished_task(stub_runtime_setting):
    """同名 Run 的旧任务已结束时重新提交。"""
    runtime = _runtime()

    async def scenario() -> asyncio.Future:
        finished: asyncio.Future = asyncio.get_running_loop().create_future()
        finished.set_result(None)
        runtime._tasks["run-1"] = finished  # type: ignore[assignment]
        runtime.submit(run_id="run-1", conversation_id="conv-1", user_id="u1", operator_name="用户一")
        new_task = runtime._tasks["run-1"]
        await asyncio.gather(new_task, return_exceptions=True)
        return new_task

    new_task = asyncio.run(scenario())

    assert new_task is not None
    assert new_task.done()


def test_submit_skips_running_task(stub_runtime_setting):
    """同名 Run 仍在执行时不重复提交。"""
    runtime = _runtime()

    async def scenario() -> asyncio.Task:
        pending = asyncio.ensure_future(asyncio.sleep(0.05))
        runtime._tasks["run-1"] = pending
        runtime.submit(run_id="run-1", conversation_id="conv-1", user_id="u1", operator_name="用户一")
        same = runtime._tasks["run-1"]
        await pending
        return same

    task = asyncio.run(scenario())

    assert task.done()


def test_publish_closed_terminal_skips_release_when_claim_fails():
    """认领 owner 失败时不执行 release，避免误释放他人租约。"""
    events = _Events(claim_error=RuntimeError("claim down"))
    runtime = _runtime(events=events)

    asyncio.run(
        runtime._publish_closed_terminal(
            run_id="run-1",
            conversation_id="conv-1",
            user_id="u1",
            status="succeeded",
            owner_token="token-1",
        )
    )

    assert events.released == []
    assert events.appended == []


def test_publish_closed_terminal_releases_owner_when_claimed():
    """认领成功并发布 done 后释放 owner。"""
    events = _Events()
    runtime = _runtime(events=events)

    asyncio.run(
        runtime._publish_closed_terminal(
            run_id="run-1",
            conversation_id="conv-1",
            user_id="u1",
            status="succeeded",
            owner_token="token-1",
        )
    )

    assert events.appended[0]["payload"]
    assert events.released == ["run-1"]


def test_executor_allowlist_skips_run_on_unlisted_node(monkeypatch):
    prepared: list[str] = []

    class Inbox(_Inbox):
        async def prepare_pending_run(self, run_id: str):
            prepared.append(run_id)
            return None

    async def setting(key: str, _default: object = None) -> object:
        if key == "CHAT_DATA_AGENT_EXECUTOR_NODES":
            return "bj12-b"
        return 1

    monkeypatch.setattr(rt, "get_runtime_setting", setting)
    runtime = _runtime(inbox=Inbox(), node_id="bj12-a")

    asyncio.run(runtime._execute(run_id="run-1", conversation_id="conv-1", user_id="u1", operator_name="用户一"))

    assert prepared == []


def test_executor_allowlist_allows_listed_node(monkeypatch):
    prepared: list[str] = []

    class Inbox(_Inbox):
        async def prepare_pending_run(self, run_id: str):
            prepared.append(run_id)
            return None

    async def setting(key: str, _default: object = None) -> object:
        if key == "CHAT_DATA_AGENT_EXECUTOR_NODES":
            return "bj12-a,bj12-b"
        return 1

    monkeypatch.setattr(rt, "get_runtime_setting", setting)
    runtime = _runtime(inbox=Inbox(), node_id="bj12-a")

    asyncio.run(runtime._execute(run_id="run-1", conversation_id="conv-1", user_id="u1", operator_name="用户一"))

    assert prepared == ["run-1"]


def test_executor_allowlist_rejects_missing_node_id(monkeypatch):
    prepared: list[str] = []

    class Inbox(_Inbox):
        async def prepare_pending_run(self, run_id: str):
            prepared.append(run_id)
            return None

    async def setting(key: str, _default: object = None) -> object:
        if key == "CHAT_DATA_AGENT_EXECUTOR_NODES":
            return "bj12-a"
        return 1

    monkeypatch.delenv("CHAT_DATA_NODE_ID", raising=False)
    monkeypatch.setattr(rt, "get_runtime_setting", setting)
    runtime = _runtime(inbox=Inbox(), node_id="")

    asyncio.run(runtime._execute(run_id="run-1", conversation_id="conv-1", user_id="u1", operator_name="用户一"))

    assert prepared == []


def test_executor_allowlist_empty_does_not_restrict(monkeypatch):
    prepared: list[str] = []

    class Inbox(_Inbox):
        async def prepare_pending_run(self, run_id: str):
            prepared.append(run_id)
            return None

    async def setting(key: str, _default: object = None) -> object:
        if key == "CHAT_DATA_AGENT_EXECUTOR_NODES":
            return ""
        return 1

    monkeypatch.setattr(rt, "get_runtime_setting", setting)
    runtime = _runtime(inbox=Inbox(), node_id="bj12-a")

    asyncio.run(runtime._execute(run_id="run-1", conversation_id="conv-1", user_id="u1", operator_name="用户一"))

    assert prepared == ["run-1"]


def test_poll_pending_runs_skips_disallowed_node(monkeypatch):
    reaped: list[str] = []
    listed: list[int] = []

    async def setting(key: str, _default: object = None) -> object:
        if key == "CHAT_DATA_AGENT_EXECUTOR_NODES":
            return "bj12-b"
        return 1

    async def fake_reap() -> None:
        reaped.append("reap")

    async def fake_list(limit: int) -> list[str]:
        listed.append(limit)
        return []

    monkeypatch.setattr(rt, "get_runtime_setting", setting)
    runtime = _runtime(node_id="bj12-a")
    monkeypatch.setattr(runtime, "_reap_expired_runs", fake_reap)
    monkeypatch.setattr(runtime, "_list_runnable_runs", fake_list)

    async def scenario() -> None:
        poller = asyncio.ensure_future(runtime._poll_pending_runs())
        await asyncio.sleep(0.02)
        poller.cancel()
        with suppress(asyncio.CancelledError):
            await poller

    asyncio.run(scenario())

    assert reaped == []
    assert listed == []


def test_poll_pending_runs_waits_when_no_capacity(monkeypatch):
    """并发槽位已满时轮询只等待，不再拉取可运行 Run。"""
    listed: list[int] = []

    async def fake_reap() -> None:
        return None

    async def fake_list(limit: int) -> list[str]:
        listed.append(limit)
        return []

    runtime = _runtime()
    monkeypatch.setattr(runtime, "_reap_expired_runs", fake_reap)
    monkeypatch.setattr(runtime, "_list_runnable_runs", fake_list)

    async def scenario() -> None:
        runtime._tasks["busy"] = asyncio.ensure_future(asyncio.sleep(10))
        poller = asyncio.ensure_future(runtime._poll_pending_runs())
        await asyncio.sleep(0.02)
        poller.cancel()
        with suppress(asyncio.CancelledError):
            await poller
        runtime._tasks["busy"].cancel()
        with suppress(asyncio.CancelledError):
            await runtime._tasks["busy"]

    asyncio.run(scenario())

    assert listed == []


@pytest.mark.parametrize(
    ("method_name", "status"),
    [("_finalize_cancelled_run", "cancelled"), ("_finalize_failed_run", "failed")],
)
def test_finalize_skips_terminal_payload_when_finish_returns_none(method_name, status):
    """finish_run 未返回终态时（租约已被新 Worker 接管）不设终态 payload。"""
    inbox = _Inbox(finish_status=None)
    runtime = _runtime(inbox=inbox)
    state = _state()
    prepared = {"owner_token": "token-1"}

    if method_name == "_finalize_cancelled_run":
        asyncio.run(runtime._finalize_cancelled_run(state, prepared, asyncio.CancelledError("agent_run_cancelled")))
    else:
        asyncio.run(runtime._finalize_failed_run(state, prepared))

    assert inbox.finished == [("run-1", status)]
    assert state.terminal_ready is False
    assert state.terminal_payload is None
