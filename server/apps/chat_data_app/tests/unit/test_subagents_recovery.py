"""后台子智能体失联接管、检查点续跑与安全拒绝测试。"""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from sqlalchemy import select

from apps.chat_data_app.agent.middleware import subagents as subagents_module
from apps.chat_data_app.agent.run import subagent_task_service as service_module
from apps.chat_data_app.tests.unit.subagent_test_support import CONV, USER
from apps.chat_data_app.tests.unit.test_subagents_background import (
    _create_task,
    _expire_task,
    _runnable,
    _save_recovery_runtime,
    _spec,
    _start,
    _wait_terminal,
)
from utils.models import ChatDataAgentInbox


def _register_background_recovery(harness, record) -> None:
    specs = [_spec()]
    cache = subagents_module._BackgroundRunnableCache(specs)

    async def recover(recovered, runtime):
        # 与生产注册的 runner 完全一致：不额外传 resumed/recovered，
        # 由任务账本里的 recovery_count 判断本次是接管而非首次启动。
        await subagents_module._run_background_task(recovered, runtime, specs, cache.get())

    harness.service.register_recovery_runner(recover, task_id=record.task_id)


@pytest.mark.asyncio
async def test_expired_background_task_waits_for_recovery_runner(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    started = await _start(harness, record)
    await _expire_task(harness, record.task_id)

    assert await harness.service.reap_expired_tasks() == 0
    waiting = await harness.service.get_task(record.task_id)
    assert waiting is not None
    assert waiting.status == "running"
    assert waiting.owner_token == started.owner_token
    assert waiting.meta["recovery_wait_started_at"]


@pytest.mark.asyncio
async def test_expired_background_task_marks_lost_after_runner_grace(harness):
    """进程重启后不会有该任务的 runner，宽限期结束必须收口而非永久 running。"""
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)
    await _expire_task(harness, record.task_id)
    async with harness.get_session() as session:
        row = (
            await session.execute(select(ChatDataAgentInbox).where(ChatDataAgentInbox.message_id == record.task_id))
        ).scalar_one()
        meta = json.loads(row.record_meta)
        meta["recovery_wait_started_at"] = (
            datetime.now() - timedelta(seconds=service_module.TASK_RECOVERY_RUNNER_WAIT_SECONDS + 5)
        ).isoformat()
        row.record_meta = json.dumps(meta)

    assert await harness.service.reap_expired_tasks() == 1
    reaped = await harness.service.get_task(record.task_id)
    assert reaped is not None
    assert reaped.status == "lost"
    assert reaped.owner_token is None


@pytest.mark.asyncio
async def test_recovery_runner_is_bound_to_its_own_task(harness):
    """不同任务的恢复执行器不能互相覆盖，避免跨用户复用工具/工作区上下文。"""
    await harness.create_conversation()
    first = await _create_task(harness)
    second = await _create_task(harness)
    await _save_recovery_runtime(harness, first)
    await _save_recovery_runtime(harness, second)
    claimed: list[str] = []

    async def recover_first(record, _runtime):
        claimed.append(record.task_id)

    async def recover_second(record, _runtime):
        claimed.append(record.task_id)

    harness.service.register_recovery_runner(recover_first, task_id=first.task_id)
    harness.service.register_recovery_runner(recover_second, task_id=second.task_id)
    await _expire_task(harness, first.task_id)
    await _expire_task(harness, second.task_id)

    assert await harness.service.reap_expired_tasks() == 2
    await asyncio.sleep(0)
    assert sorted(claimed) == sorted([first.task_id, second.task_id])


@pytest.mark.asyncio
async def test_expired_sync_task_marks_lost_without_background_recovery(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await harness.service.configure_task_execution(
        record.task_id,
        record.owner_token or "",
        execution_mode="sync",
    )
    await _start(harness, record)
    await _expire_task(harness, record.task_id)

    assert await harness.service.reap_expired_tasks() == 1
    reaped = await harness.service.get_task(record.task_id)
    assert reaped is not None
    assert reaped.status == "lost"
    assert reaped.error == "执行节点租约过期"
    assert reaped.owner_token is None


@pytest.mark.asyncio
async def test_expired_pending_task_is_claimed_once_with_persisted_runtime(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _save_recovery_runtime(harness, record)
    claimed = []

    async def recover(recovered, runtime):
        claimed.append((recovered, runtime))

    harness.service.register_recovery_runner(recover)
    await _expire_task(harness, record.task_id)

    results = await asyncio.gather(harness.service.reap_expired_tasks(), harness.service.reap_expired_tasks())
    assert sorted(results) == [0, 1]
    await asyncio.sleep(0)
    assert len(claimed) == 1
    recovered, runtime = claimed[0]
    assert recovered.status == "running"
    assert recovered.owner_token != record.owner_token
    assert recovered.meta["execution_attempt"] == 2
    assert recovered.meta["recovery_count"] == 1
    assert recovered.meta["recovery_kind"] == "pending_start"
    assert runtime.state["conversation_id"] == CONV
    assert runtime.tool_call_id == "call-parent"


@pytest.mark.asyncio
async def test_expired_running_task_rotates_owner_for_checkpoint_recovery(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    started = await _start(harness, record)
    claimed = []

    async def recover(recovered, _runtime):
        claimed.append(recovered)

    harness.service.register_recovery_runner(recover)
    await _expire_task(harness, record.task_id)

    assert await harness.service.reap_expired_tasks() == 1
    await asyncio.sleep(0)
    assert len(claimed) == 1
    assert claimed[0].meta["recovery_kind"] == "checkpoint"
    assert claimed[0].owner_token != started.owner_token
    with pytest.raises(service_module.SubagentTaskLeaseLostError, match="其他 Worker 接管"):
        await harness.service.assert_task_owner(record.task_id, started.owner_token or "")
    await harness.service.assert_task_owner(record.task_id, claimed[0].owner_token or "")


@pytest.mark.asyncio
async def test_expired_claim_does_not_override_renewed_lease(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)
    await _expire_task(harness, record.task_id)
    expired = await harness.service.get_task(record.task_id)
    assert expired is not None and expired.lease_expire_time is not None
    await harness.service.heartbeat_task(record.task_id, record.owner_token or "", {"phase": "running"})

    async with harness.get_session() as session:
        updated = await harness.service._dao.update_expired(
            session,
            record.task_id,
            owner_token=expired.owner_token or "",
            record_status=expired.status,
            lease_expire_time=expired.lease_expire_time,
            now=datetime.now(),
            values={"record_status": "lost"},
        )
    assert updated == 0
    assert (await harness.service.get_task(record.task_id)).status == "running"


@pytest.mark.asyncio
async def test_expired_task_stops_recovering_at_attempt_limit(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)
    async with harness.get_session() as session:
        row = (
            await session.execute(select(ChatDataAgentInbox).where(ChatDataAgentInbox.message_id == record.task_id))
        ).scalar_one()
        meta = json.loads(row.record_meta)
        meta["recovery_count"] = service_module.MAX_TASK_RECOVERY_ATTEMPTS
        row.record_meta = json.dumps(meta)
    called = False

    async def recover(_record, _runtime):
        nonlocal called
        called = True

    harness.service.register_recovery_runner(recover)
    await _expire_task(harness, record.task_id)

    assert await harness.service.reap_expired_tasks() == 1
    assert not called
    assert (await harness.service.get_task(record.task_id)).status == "lost"


@pytest.mark.asyncio
async def test_pending_recovery_without_persisted_runtime_fails_closed(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    harness.service.register_recovery_runner(lambda _record, _runtime: asyncio.sleep(0))
    await _expire_task(harness, record.task_id)

    assert await harness.service.reap_expired_tasks() == 1
    reaped = await _wait_terminal(harness, record.task_id)
    assert reaped.status == "lost"
    assert "恢复上下文缺失" in reaped.report


@pytest.mark.asyncio
async def test_pending_recovery_runs_existing_background_pipeline(harness, monkeypatch):
    await harness.create_conversation()
    runnable = _runnable("pending 恢复结论")
    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda *_args, **_kwargs: runnable)
    record = await _create_task(harness)
    _register_background_recovery(harness, record)
    await _save_recovery_runtime(harness, record)
    await _expire_task(harness, record.task_id)

    assert await harness.service.reap_expired_tasks() == 1
    completed = await _wait_terminal(harness, record.task_id)
    assert completed.status == "completed"
    assert completed.report == "pending 恢复结论"
    assert completed.meta["recovery_kind"] == "pending_start"


@pytest.mark.asyncio
async def test_running_recovery_resumes_checkpoint_with_new_owner(harness, monkeypatch):
    await harness.create_conversation()
    inputs = []

    class _CheckpointRunnable:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "messages": [HumanMessage(content="原任务")],
                    "workspace": {"workspace_root": "/tmp/workspace"},
                },
                next=("model",),
                tasks=(),
            )

        async def astream(self, state, _config, *, stream_mode):
            inputs.append(state)
            yield "updates", {"model": {"messages": [AIMessage(content="检查点恢复结论")]}}

    runnable = _CheckpointRunnable()
    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda *_args, **_kwargs: runnable)
    record = await _create_task(harness)
    _register_background_recovery(harness, record)
    await _save_recovery_runtime(harness, record)
    started = await _start(harness, record)
    await _expire_task(harness, record.task_id)

    assert await harness.service.reap_expired_tasks() == 1
    completed = await _wait_terminal(harness, record.task_id)
    assert completed.status == "completed"
    assert completed.report == "检查点恢复结论"
    assert len(inputs) == 1
    assert isinstance(inputs[0], Command)
    assert inputs[0].update["subagent_owner_token"]
    assert inputs[0].update["subagent_owner_token"] != started.owner_token


@pytest.mark.asyncio
async def test_approval_resume_after_recovery_does_not_replay_checkpoint(harness, monkeypatch):
    """接管过的任务再次进入审批后，恢复必须使用审批 resume 值。"""
    await harness.create_conversation()
    inputs = []

    class _ApprovalRunnable:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={"messages": [HumanMessage(content="等待审批")]},
                next=("model",),
                tasks=(),
            )

        async def astream(self, state, _config, *, stream_mode):
            inputs.append(state)
            yield "updates", {"model": {"messages": [AIMessage(content="审批后继续完成")]}}

    runnable = _ApprovalRunnable()
    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda *_args, **_kwargs: runnable)
    record = await _create_task(harness)
    await _save_recovery_runtime(harness, record)
    started = await _start(harness, record)
    async with harness.get_session() as session:
        row = (
            await session.execute(select(ChatDataAgentInbox).where(ChatDataAgentInbox.message_id == record.task_id))
        ).scalar_one()
        meta = json.loads(row.record_meta)
        meta["recovery_count"] = 1
        meta["recovery_kind"] = "checkpoint"
        row.record_meta = json.dumps(meta)
    suspended = await harness.service.suspend_task(
        record.task_id,
        started.owner_token or "",
        status="waiting_approval",
        interrupt_payload={"kind": "approval"},
    )
    assert suspended is not None
    resumed = await harness.service.resume_task(
        record.task_id,
        conversation_id=CONV,
        user_id=USER,
        resume_value={"kind": "approval", "answers": []},
    )
    assert resumed is not None
    resumed_record, resume_value = resumed
    specs = [_spec()]
    compiled = subagents_module._BackgroundRunnableCache(specs).get()
    await subagents_module._run_background_task(
        resumed_record,
        SimpleNamespace(
            state={"conversation_id": CONV, "user_id": USER, "operator_name": "用户一", "run_id": "run-parent"},
            tool_call_id="call-parent",
        ),
        specs,
        compiled,
        resumed=True,
        resume=True,
        resume_value=resume_value,
    )

    completed = await _wait_terminal(harness, record.task_id)
    assert completed.status == "completed"
    assert getattr(inputs[0], "resume", None) == resume_value


@pytest.mark.asyncio
async def test_running_recovery_without_checkpoint_fails_closed(harness, monkeypatch):
    await harness.create_conversation()
    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda *_args, **_kwargs: _runnable("不会从头重放"))
    record = await _create_task(harness)
    _register_background_recovery(harness, record)
    await _save_recovery_runtime(harness, record)
    await _start(harness, record)
    await _expire_task(harness, record.task_id)

    assert await harness.service.reap_expired_tasks() == 1
    recovered = await _wait_terminal(harness, record.task_id)
    assert recovered.status == "lost"
    assert "检查点读取失败" in recovered.report
    assert "不会从头重放" not in recovered.report


@pytest.mark.asyncio
async def test_cancelling_task_is_not_automatically_recovered(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)
    cancelling = await harness.service.cancel_task(CONV, USER, record.task_id, "用户一")
    assert cancelling is not None and cancelling.status == "cancelling"
    called = False

    async def recover(_record, _runtime):
        nonlocal called
        called = True

    harness.service.register_recovery_runner(recover)
    await _expire_task(harness, record.task_id)

    assert await harness.service.reap_expired_tasks() == 1
    assert not called
    assert (await harness.service.get_task(record.task_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_checkpoint_recovery_rejects_uncertain_write_tool_side_effect(harness):
    class _Runnable:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": [HumanMessage(content="写文件")]}, next=("tools",), tasks=())

    runtime = SimpleNamespace(state={})
    with pytest.raises(subagents_module.SubagentRecoveryRejected, match="副作用尚未确认"):
        await subagents_module._prepare_crash_recovery_input(
            _Runnable(),
            {},
            runtime,
            task_id="task-1",
            owner_token="new-owner",
            subagent_type="general",
        )


@pytest.mark.asyncio
async def test_checkpoint_recovery_allows_read_only_tool_replay(harness):
    class _Runnable:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": [HumanMessage(content="只读查询")]}, next=("tools",), tasks=())

    runtime = SimpleNamespace(state={})
    command, messages = await subagents_module._prepare_crash_recovery_input(
        _Runnable(),
        {},
        runtime,
        task_id="task-1",
        owner_token="new-owner",
        subagent_type="explore",
    )
    assert command.update["subagent_owner_token"] == "new-owner"
    assert runtime.state["subagent_owner_token"] == "new-owner"
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_checkpoint_recovery_preserves_pending_approval_interrupt(harness):
    class _Runnable:
        async def aget_state(self, _config):
            task = SimpleNamespace(interrupts=(object(),))
            return SimpleNamespace(
                values={"messages": [HumanMessage(content="待审批")]}, next=("tools",), tasks=(task,)
            )

    command, _messages = await subagents_module._prepare_crash_recovery_input(
        _Runnable(),
        {},
        SimpleNamespace(state={}),
        task_id="task-1",
        owner_token="new-owner",
        subagent_type="general",
    )
    assert command.update["subagent_owner_token"] == "new-owner"
    assert command.resume is None
