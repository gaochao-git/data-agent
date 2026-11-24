"""子智能体心跳 watcher 的本地租约护栏测试。"""

import asyncio
from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.contracts.subagent_task import TASK_STATUS_CANCELLING
from apps.chat_data_app.agent.middleware import subagent_async, subagents
from apps.chat_data_app.agent.middleware.subagent_async import _watch_background_task


class _HeartbeatService:
    def __init__(self, *, outcome: str | None = "running", delay: float = 0.0, error: BaseException | None = None):
        self.outcome = outcome
        self.delay = delay
        self.error = error
        self.calls = 0

    async def heartbeat_task(self, task_id: str, owner_token: str, progress: dict) -> str | None:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.outcome


def _cancellable_task(reasons: list[str]) -> asyncio.Task:
    async def run() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError as exc:
            reasons.append(str(exc.args[0]) if exc.args else "")
            raise

    return asyncio.create_task(run())


async def _assert_cancelled(task: asyncio.Task, reasons: list[str], reason: str) -> None:
    done, pending = await asyncio.wait({task}, timeout=0.5)
    assert not pending
    assert done == {task}
    assert task.cancelled()
    assert reasons == [reason]


@pytest.mark.asyncio
async def test_watch_background_task_cancels_after_local_lease_deadline(monkeypatch):
    monkeypatch.setattr(subagent_async, "TASK_HEARTBEAT_SECONDS", 0.01)
    reasons: list[str] = []
    running = _cancellable_task(reasons)
    service = _HeartbeatService(error=RuntimeError("db down"))
    watcher = asyncio.create_task(
        _watch_background_task(
            "task-1",
            "owner-1",
            {},
            running,
            service,
            lease_seconds=0.08,
            lease_started_at=asyncio.get_running_loop().time(),
        )
    )

    await _assert_cancelled(running, reasons, "subagent_lease_lost")
    assert service.calls >= 1

    watcher.cancel()
    await asyncio.gather(watcher, return_exceptions=True)


@pytest.mark.asyncio
async def test_watch_background_task_times_out_hung_heartbeat(monkeypatch):
    monkeypatch.setattr(subagent_async, "TASK_HEARTBEAT_SECONDS", 0.01)
    reasons: list[str] = []
    running = _cancellable_task(reasons)
    service = _HeartbeatService(delay=10.0)
    watcher = asyncio.create_task(
        _watch_background_task(
            "task-2",
            "owner-2",
            {},
            running,
            service,
            lease_seconds=0.08,
            lease_started_at=asyncio.get_running_loop().time(),
        )
    )

    await _assert_cancelled(running, reasons, "subagent_lease_lost")
    assert service.calls >= 1

    watcher.cancel()
    await asyncio.gather(watcher, return_exceptions=True)


@pytest.mark.asyncio
async def test_watch_background_task_still_stops_on_remote_cancelling(monkeypatch):
    monkeypatch.setattr(subagent_async, "TASK_HEARTBEAT_SECONDS", 0.01)
    reasons: list[str] = []
    running = _cancellable_task(reasons)
    service = _HeartbeatService(outcome=TASK_STATUS_CANCELLING)
    watcher = asyncio.create_task(
        _watch_background_task(
            "task-3",
            "owner-3",
            {},
            running,
            service,
            lease_seconds=30,
            lease_started_at=asyncio.get_running_loop().time(),
        )
    )

    await _assert_cancelled(running, reasons, "subagent_task_cancelled")

    watcher.cancel()
    await asyncio.gather(watcher, return_exceptions=True)


@pytest.mark.asyncio
async def test_watch_background_task_bounds_heartbeat_timeout_by_remaining_lease(monkeypatch):
    monkeypatch.setattr(subagent_async, "TASK_HEARTBEAT_SECONDS", 0.2)
    timeouts: list[float] = []

    async def heartbeat_once(_task_id, _owner_token, _progress, _service, *, timeout: float):
        timeouts.append(timeout)
        return False, None

    monkeypatch.setattr(subagent_async, "_heartbeat_once", heartbeat_once)
    reasons: list[str] = []
    running = _cancellable_task(reasons)
    lease_started_at = asyncio.get_running_loop().time() - 0.75
    watcher = asyncio.create_task(
        _watch_background_task(
            "task-4",
            "owner-4",
            {},
            running,
            _HeartbeatService(),
            lease_seconds=1.0,
            lease_started_at=lease_started_at,
        )
    )

    await _assert_cancelled(running, reasons, "subagent_lease_lost")
    assert timeouts and timeouts[0] < 0.2

    watcher.cancel()
    await asyncio.gather(watcher, return_exceptions=True)


@pytest.mark.asyncio
async def test_background_task_finalizes_when_cancelled_before_attempt(monkeypatch):
    finished: dict = {}

    class _Service:
        async def get_task(self, _task_id):
            return SimpleNamespace(status="running", error="", meta={})

        async def finish_task(self, *_args, **kwargs):
            finished.update(kwargs)

        async def dispatch_pending_notifications(self, *, limit=1):
            return 0

    service = _Service()
    monkeypatch.setattr(subagents, "get_subagent_task_service", lambda: service)

    async def lease_seconds():
        return 30.0

    async def load_history(*_args, **_kwargs):
        await asyncio.sleep(10)

    async def cancel_watch(_task_id, _owner_token, _progress, running_task, _service, **_kwargs):
        running_task.cancel("subagent_lease_lost")
        await asyncio.sleep(0)

    monkeypatch.setattr(subagents, "_read_task_lease_seconds", lease_seconds)
    monkeypatch.setattr(subagents, "_load_background_history", load_history)
    monkeypatch.setattr(subagents, "_watch_background_task", cancel_watch)
    record = SimpleNamespace(
        task_id="task-startup",
        owner_token="owner-startup",
        description="启动阶段取消核对",
        meta={},
    )
    runner = asyncio.create_task(
        subagents._run_background_task(
            record,
            SimpleNamespace(state={}, tool_call_id="call-startup"),
            [],
            {},
            recovered=True,
        )
    )

    with pytest.raises(asyncio.CancelledError):
        await runner

    assert runner.cancelled()
    assert finished["status"] == "interrupted"
    assert "执行节点中断" in finished["report"]


@pytest.mark.asyncio
async def test_stop_watcher_is_bounded_when_watcher_ignores_cancel(monkeypatch):
    monkeypatch.setattr(subagents, "TASK_WATCHER_SHUTDOWN_TIMEOUT_SECONDS", 0.05)
    release = asyncio.Event()

    async def stubborn() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await release.wait()

    watcher = asyncio.create_task(stubborn())
    await asyncio.sleep(0)

    started = asyncio.get_running_loop().time()
    await subagents._stop_watcher(watcher)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.5
    assert not watcher.done()
    release.set()
    await watcher
