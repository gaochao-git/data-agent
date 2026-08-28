"""agent_run_runtime.py 覆盖率补充测试。

聚焦既有 test_agent_run_runtime.py 未触达的运行时生命周期分支：
start/stop 收口、submit 去重与已完成任务替换、_forget_task 身份校验、
cancel_run 走本地取消、终态发布失败与 owner 释放失败的兜底告警、
以及后台调度轮询的三种出口（正常/异常重试/取消）。
不访问真实 Redis、MySQL 与模型。
"""

import asyncio
from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.run.agent_inbox_service import (
    AgentRunCancellationResult,
    AgentRunReapResult,
)
from apps.chat_data_app.agent.run.agent_run_runtime import AgentRunRuntime

_REAL_SLEEP = asyncio.sleep


def _patch_sleep_to_yield(monkeypatch) -> None:
    """把 asyncio.sleep 换成让出一次事件循环，避免测试真等秒级。"""

    async def no_sleep(*_args, **_kwargs):
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", no_sleep)


class _EventBus:
    def __init__(self):
        self.started = 0
        self.closed = 0

    async def start(self):
        self.started += 1

    async def close(self):
        self.closed += 1


class _Events:
    def __init__(self, *, append_error: Exception | None = None, release_error: Exception | None = None):
        self.appended: list[dict] = []
        self.claimed: list[str] = []
        self.released: list[str] = []
        self.append_error = append_error
        self.release_error = release_error

    async def claim_owner(self, run_id, _owner_token):
        self.claimed.append(run_id)

    async def release_owner(self, run_id, _owner_token):
        if self.release_error is not None:
            raise self.release_error
        self.released.append(run_id)

    async def append(self, **kwargs):
        if self.append_error is not None:
            raise self.append_error
        self.appended.append(kwargs)
        return "stream-1"


class _Inbox:
    def __init__(self, *, cancel_result=None, reap_results=(), terminal_error=None):
        self.cancel_result = cancel_result or AgentRunCancellationResult(
            cancelled=True, status="cancelled", terminal_owner_token="tok-9"
        )
        self.reap_results = list(reap_results)
        self.terminal_error = terminal_error
        self.recorded: list[tuple] = []

    async def cancel_active_run(self, _conversation_id, _user_id, _operator_name, _run_id):
        return self.cancel_result

    async def reap_expired_runs(self, limit=20):
        return self.reap_results

    async def record_terminal_event(self, run_id, stream_id):
        if self.terminal_error is not None:
            raise self.terminal_error
        self.recorded.append((run_id, stream_id))

    async def heartbeat_run(self, _run_id, _owner_token):
        return "active"


def _runtime(inbox=None, events=None, bus=None) -> AgentRunRuntime:
    return AgentRunRuntime(
        inbox_service=inbox or _Inbox(),
        event_service=events or _Events(),
        event_bus=bus or _EventBus(),
    )


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_launches_dispatcher_once_and_stop_is_idempotent():
    bus = _EventBus()
    runtime = _runtime(bus=bus)

    async def idle_poll():
        await asyncio.sleep(30)

    monkey_task = asyncio.create_task(idle_poll())
    runtime._poll_task = monkey_task  # 预置一个在跑的轮询任务，验证不会重复起
    await runtime.start()
    assert runtime._poll_task is monkey_task
    assert bus.started == 1

    monkey_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await monkey_task

    await runtime.stop()
    assert runtime._poll_task is None
    assert bus.closed == 1
    await runtime.stop()  # 二次 stop 不应抛错
    assert bus.closed == 2


@pytest.mark.asyncio
async def test_start_creates_dispatcher_when_absent():
    runtime = _runtime()
    assert runtime._poll_task is None
    await runtime.start()
    assert runtime._poll_task is not None
    await runtime.stop()


@pytest.mark.asyncio
async def test_stop_cancels_owned_tasks_without_touching_db_lease():
    runtime = _runtime()
    started = asyncio.Event()

    async def long_run(**_kwargs):
        started.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(long_run())
    runtime._tasks["run-a"] = task
    runtime._owned_runs.add("run-a")
    await started.wait()

    await runtime.stop()

    assert task.cancelled() or task.done()
    assert runtime._tasks == {}
    assert runtime._owned_runs == set()


# ---------------------------------------------------------------------------
# submit / _forget_task / cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_skips_running_run_and_replaces_finished_one():
    runtime = _runtime()
    running = asyncio.create_task(asyncio.sleep(30))
    runtime._tasks["run-live"] = running

    runtime.submit(run_id="run-live", conversation_id="c", user_id="u", operator_name="n")
    assert runtime._tasks["run-live"] is running  # 未结束的同一个 run 不重复起

    finished = asyncio.create_task(_REAL_SLEEP(0))
    await finished
    runtime._tasks["run-done"] = finished
    monkey_executed = []

    async def fake_execute(**kwargs):
        monkey_executed.append(kwargs["run_id"])
        return None

    runtime._execute = fake_execute
    runtime.submit(run_id="run-done", conversation_id="c", user_id="u", operator_name="n")
    await _REAL_SLEEP(0)
    await _REAL_SLEEP(0)
    assert monkey_executed == ["run-done"]
    # 执行完成后 done_callback 自动回收条目，不留下已完成任务
    assert "run-done" not in runtime._tasks
    running.cancel()
    await runtime.stop()


@pytest.mark.asyncio
async def test_forget_task_only_removes_matching_task():
    runtime = _runtime()
    current = asyncio.create_task(asyncio.sleep(0))
    stale = asyncio.create_task(asyncio.sleep(0))
    runtime._tasks["run-x"] = current

    runtime._forget_task("run-x", stale)
    assert runtime._tasks["run-x"] is current  # 身份不符不得误删
    runtime._forget_task("run-x", current)
    assert "run-x" not in runtime._tasks
    await asyncio.gather(current, stale, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_local_requires_owned_run():
    runtime = _runtime()

    async def noop():
        await _REAL_SLEEP(30)

    task = asyncio.create_task(noop())
    await _REAL_SLEEP(0)  # 让任务真正起跑
    runtime._tasks["run-q"] = task
    # 尚未拿到 owner/租约：不拦腰取消
    assert runtime.cancel_local("run-q") is False
    assert not task.cancelled()

    runtime._owned_runs.add("run-q")
    assert runtime.cancel_local("run-q") is True
    assert runtime.cancel_local("run-missing") is False
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_run_returns_false_when_nothing_cancelled():
    inbox = _Inbox(cancel_result=AgentRunCancellationResult(
        cancelled=False, status="active", terminal_owner_token=None
    ))
    runtime = _runtime(inbox=inbox)
    ok = await runtime.cancel_run(
        run_id="run-1", conversation_id="c", user_id="u", operator_name="n",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_cancel_run_publishes_terminal_without_local_cancel():
    inbox = _Inbox()
    events = _Events()
    runtime = _runtime(inbox=inbox, events=events)
    cancelled_locally = []
    runtime.cancel_local = lambda run_id: cancelled_locally.append(run_id) or False

    ok = await runtime.cancel_run(
        run_id="run-1", conversation_id="c", user_id="u", operator_name="n",
    )
    assert ok is True
    assert cancelled_locally == []  # 已 MySQL 收口，不该再走本地取消
    assert events.appended and events.appended[0]["owner_token"] == "tok-9"
    assert inbox.recorded == [("run-1", "stream-1")]
    assert events.released == ["run-1"]


@pytest.mark.asyncio
async def test_cancel_run_falls_back_to_local_cancel_without_terminal_token():
    inbox = _Inbox(cancel_result=AgentRunCancellationResult(
        cancelled=True, status="cancelling", terminal_owner_token=None
    ))
    runtime = _runtime(inbox=inbox)
    seen = []
    runtime.cancel_local = lambda run_id: seen.append(run_id) or True

    assert await runtime.cancel_run(
        run_id="run-2", conversation_id="c", user_id="u", operator_name="n",
    ) is True
    assert seen == ["run-2"]


# ---------------------------------------------------------------------------
# 终态发布兜底
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_closed_terminal_swallows_append_failure():
    events = _Events(append_error=RuntimeError("redis down"))
    inbox = _Inbox()
    runtime = _runtime(inbox=inbox, events=events)

    # 发布失败只告警，不抛出：SSE 侧会用 MySQL 终态补发
    await runtime._publish_closed_terminal(
        run_id="run-3", conversation_id="c", user_id="u", status="cancelled", owner_token="tok",
    )
    assert events.claimed == ["run-3"]
    assert inbox.recorded == []
    assert events.released == ["run-3"]  # 失败也要释放租约


@pytest.mark.asyncio
async def test_publish_closed_terminal_swallows_owner_release_failure():
    events = _Events(release_error=RuntimeError("release failed"))
    runtime = _runtime(events=events)
    await runtime._publish_closed_terminal(
        run_id="run-4", conversation_id="c", user_id="u", status="cancelled", owner_token="tok",
    )
    assert events.released == []  # 释放失败被吞掉，不影响主流程


@pytest.mark.asyncio
async def test_publish_closed_terminal_records_terminal_event_failure():
    inbox = _Inbox(terminal_error=RuntimeError("db write failed"))
    events = _Events()
    runtime = _runtime(inbox=inbox, events=events)
    await runtime._publish_closed_terminal(
        run_id="run-5", conversation_id="c", user_id="u", status="timeout", owner_token="tok",
    )
    assert events.appended  # append 成功，record 失败被兜底


@pytest.mark.asyncio
async def test_reap_expired_runs_publishes_each_terminal():
    inbox = _Inbox(reap_results=[
        AgentRunReapResult("run-a", "conv-a", "user-a", "timeout", "tok-a"),
        AgentRunReapResult("run-b", "conv-b", "user-b", "timeout", "tok-b"),
    ])
    events = _Events()
    runtime = _runtime(inbox=inbox, events=events)
    await runtime._reap_expired_runs()
    assert events.claimed == ["run-a", "run-b"]


# ---------------------------------------------------------------------------
# 后台调度轮询
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_pending_runs_submits_then_exits_on_cancel(monkeypatch):
    runtime = _runtime()
    submitted: list[str] = []
    calls = {"reap": 0}

    async def fake_reap():
        calls["reap"] += 1
        if calls["reap"] >= 2:
            raise asyncio.CancelledError
        return None

    async def fake_list(_limit):
        return ["run-new", "run-known"]

    runtime._reap_expired_runs = fake_reap
    runtime._list_runnable_runs = fake_list
    runtime._tasks["run-known"] = asyncio.create_task(asyncio.sleep(0))
    monkeypatch.setattr(
        runtime, "submit",
        lambda **kwargs: submitted.append(kwargs["run_id"]),
    )
    _patch_sleep_to_yield(monkeypatch)

    with pytest.raises(asyncio.CancelledError):
        await runtime._poll_pending_runs()

    assert submitted == ["run-new"]  # 已在 _tasks 里的 run 不重复提交
    assert calls["reap"] >= 2
    await runtime.stop()


@pytest.mark.asyncio
async def test_poll_pending_runs_survives_unexpected_failure(monkeypatch):
    runtime = _runtime()
    attempts = {"count": 0}

    async def flaky_reap():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("redis unavailable")
        raise asyncio.CancelledError

    async def fake_list(_limit):
        return []

    runtime._reap_expired_runs = flaky_reap
    runtime._list_runnable_runs = fake_list
    _patch_sleep_to_yield(monkeypatch)

    with pytest.raises(asyncio.CancelledError):
        await runtime._poll_pending_runs()
    assert attempts["count"] == 2  # 异常后继续下一轮而不是退出


@pytest.mark.asyncio
async def test_list_runnable_runs_queries_dao_with_session(monkeypatch):
    class _Session:
        pass

    class _Ctx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *_exc):
            return False

    captured = {}

    async def fake_list_runnable(session, now, limit):
        captured.update({"session": session, "now": now, "limit": limit})
        return [SimpleNamespace(run_id="run-x"), SimpleNamespace(run_id="run-y")]

    import utils.database as database_module
    from apps.chat_data_app.daos import agent_inbox_dao as dao_module

    monkeypatch.setattr(database_module, "get_session", lambda: _Ctx())
    monkeypatch.setattr(dao_module.agent_inbox_dao, "list_runnable_runs", fake_list_runnable)

    runtime = _runtime()
    assert await runtime._list_runnable_runs(5) == ["run-x", "run-y"]
    assert captured["limit"] == 5
    assert isinstance(captured["session"], _Session)
