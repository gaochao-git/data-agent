"""后台子智能体持久任务账本测试（SQLite 内存库）。

覆盖任务生命周期（登记/启动/心跳/收口/取消/失联回收）、完成通知只领取
自身消息、以及 ``task(background=true)`` 的工具级集成。任务真值在
``chat_data_agent_inbox``，因此测试必须走真实 DAO 与事务，不能再用旧的
进程内注册表断言。
"""

import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError
from sqlalchemy import select

from apps.chat_data_app.agent.middleware import subagent_history as subagent_history_module
from apps.chat_data_app.agent.middleware import subagents as subagents_module
from apps.chat_data_app.agent.middleware.subagent_async import _format_task_report
from apps.chat_data_app.agent.run import subagent_task_service as service_module
from apps.chat_data_app.agent.run.agent_inbox_service import AgentRunLeaseLostError, agent_inbox_service
from apps.chat_data_app.agent.run.subagent_task_service import (
    SubagentTaskCapacityError,
    SubagentTaskRecord,
    notification_message_id,
)
from apps.chat_data_app.tests.unit.subagent_test_support import (
    CONV,
    USER,
    _settings,
    build_subagent_tool,
    invoke_subagent_tool,
)
from utils.models import ChatDataAgentInbox


async def _create_task(harness, *, parent_run_id="run-parent", capacity_owner="parent-owner"):
    return await harness.service.create_task(
        conversation_id=CONV,
        user_id=USER,
        parent_run_id=parent_run_id,
        subagent_type="explore",
        description="深度分析近 30 天入金趋势",
        label="入金趋势",
        parent_tool_call_id="call-parent",
        parent_owner_token=capacity_owner,
        operator_name="用户一",
    )


async def _start(harness, record):
    started = await harness.service.start_task(record.task_id, record.owner_token or "")
    assert started is not None
    return started


async def _expire_task(harness, task_id: str) -> None:
    async with harness.get_session() as session:
        row = (
            await session.execute(select(ChatDataAgentInbox).where(ChatDataAgentInbox.message_id == task_id))
        ).scalar_one()
        row.lease_expire_time = datetime.now() - timedelta(seconds=1)


async def _save_recovery_runtime(harness, record) -> None:
    saved = await harness.service.save_task_recovery_runtime(
        record.task_id,
        record.owner_token or "",
        state={"conversation_id": CONV, "user_id": USER, "workspace": {"workspace_root": "/tmp/workspace"}},
        tool_call_id="call-parent",
    )
    assert saved is not None


async def _wait_terminal(harness, task_id: str, timeout: float = 6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = await harness.service.get_task(task_id)
        if record is not None and record.is_terminal:
            return record
        await asyncio.sleep(0.02)
    raise AssertionError(f"后台任务未在 {timeout}s 内进入终态: {task_id}")


def _spec(name="explore"):
    return subagents_module.SubagentSpec(
        name=name,
        description="深度分析子智能体",
        system_prompt="你是分析子智能体",
        tools=[],
        model=object(),
    )


def _fake_runtime(conversation_id=CONV, tool_call_id="call_bg_1"):
    return SimpleNamespace(
        state={
            "conversation_id": conversation_id,
            "user_id": USER,
            "operator_name": "用户一",
            "run_id": "run-parent",
        },
        tool_call_id=tool_call_id,
    )


def _runnable(report: str, *, delay_after_yield: float = 0.0):
    class _Runnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content=report)]}}
            if delay_after_yield:
                await asyncio.sleep(delay_after_yield)

    return _Runnable()


def _task_id_from_text(text: str) -> str:
    match = re.search(r"task_id=([0-9a-f]+)", text)
    assert match, text
    return match.group(1)


@pytest.mark.asyncio
async def test_create_task_persists_snapshot_and_publishes_compact_sse(harness):
    await harness.create_conversation()
    async with harness.get_session() as session:
        session.add(
            ChatDataAgentInbox(
                conversation_id=CONV,
                user_id=USER,
                run_id="run-parent",
                owner_token="parent-owner",
                message_id="run-parent",
                record_type="run",
                record_status="active",
                record_content=json.dumps({}),
                record_meta="{}",
                delete_flag=0,
                create_time=datetime.now(),
                update_time=datetime.now(),
            )
        )

    record = await _create_task(harness, capacity_owner="parent-owner")

    assert record.status == "pending"
    assert record.subagent_type == "explore"
    assert record.label == "入金趋势"
    assert record.progress == {}
    assert record.agent_id == record.task_id
    assert record.agent_session_id == record.task_id
    assert record.root_task_id == record.task_id
    assert record.parent_agent_id == ""
    assert record.root_agent_id == record.task_id
    assert record.depth == 1
    payload = record.as_dict(include_report=False)
    assert payload["task_id"] == record.task_id
    assert payload["agent_id"] == record.task_id
    assert payload["agent_session_id"] == record.task_id
    assert payload["parent_agent_id"] is None
    assert payload["root_agent_id"] == record.task_id
    assert payload["depth"] == 1
    assert payload["execution_mode"] == "background"
    assert payload["compaction_count"] == 0
    assert "report" not in payload
    assert "result_meta" not in payload

    assert len(harness.events.events) == 1
    frame = harness.events.events[0]["payload"]
    assert frame.startswith("event: subagent_task_updated\n")
    assert record.task_id in frame
    assert '"report"' not in frame


@pytest.mark.asyncio
async def test_write_scopes_are_normalized_in_task_ledger(harness):
    await harness.create_conversation()
    record = await harness.service.create_task(
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        subagent_type="general",
        description="分别生成 Word 和 PPT",
        label="交付物",
        parent_tool_call_id="call-scope",
        parent_owner_token="parent-owner",
        operator_name="用户一",
    )

    updated = await harness.service.set_task_write_scopes(
        record.task_id,
        record.owner_token or "",
        [" ./outputs/word/ ", "outputs\\ppt", "outputs/word", "a/../b", "workspace"],
    )

    assert updated is not None
    assert updated.write_scopes == ("outputs/word", "outputs/ppt", "workspace:*")
    payload = updated.as_dict(include_report=False)
    assert payload["write_scopes"] == ["outputs/word", "outputs/ppt", "workspace:*"]

    assert await harness.service.set_task_write_scopes(record.task_id, "wrong-owner", ["outputs/x"]) is None
    started = await harness.service.start_task(record.task_id, record.owner_token or "")
    assert started is not None
    assert await harness.service.set_task_write_scopes(record.task_id, record.owner_token or "", ["outputs/y"]) is None


@pytest.mark.asyncio
async def test_start_heartbeat_and_finish_round_trip(harness):
    await harness.create_conversation()
    record = await _create_task(harness)

    started = await _start(harness, record)
    assert started.status == "running"
    assert started.started_at is not None
    assert started.progress.get("phase") == "starting"

    status = await harness.service.heartbeat_task(
        record.task_id,
        record.owner_token or "",
        {"phase": "running", "tool_calls": 2, "last_tool": "file_read"},
    )
    assert status == "running"
    heartbeat = await harness.service.get_task(record.task_id, conversation_id=CONV, user_id=USER)
    assert heartbeat is not None
    assert heartbeat.progress["last_tool"] == "file_read"
    assert heartbeat.progress["tool_calls"] == 2
    assert heartbeat.progress.get("updated_at")

    finished = await harness.service.finish_task(
        record.task_id,
        record.owner_token or "",
        status="completed",
        report="盘点结论：一切正常",
        result_meta={"tool_status": "success", "subagent_trail": [{"type": "message", "content": "过程"}]},
    )
    assert finished is not None
    assert finished.status == "completed"
    assert finished.report == "盘点结论：一切正常"
    assert finished.result_meta["tool_status"] == "success"
    assert finished.result_meta["subagent_trail"][0]["content"] == "过程"
    assert finished.finished_at is not None
    assert finished.owner_token is None
    # 收口后进度必须是终态：不能残留运行期的 phase=running / running_tools
    assert finished.progress["phase"] == "completed"
    assert finished.progress["running_tools"] == 0
    assert finished.as_dict()["progress"]["phase"] == "completed"


def test_terminal_report_returns_full_text_when_not_offloaded():
    """未落盘的终态报告一次返回全文，不再分页，也不再展示陈旧运行进度。"""
    report = "".join(f"第{i:05d}行\n" for i in range(1500))
    record = SimpleNamespace(
        task_id="task-long",
        agent_id="agent-stable",
        subagent_type="explore",
        label="盘点语义模型",
        status="completed",
        is_terminal=True,
        report=report,
        error="",
        progress={"phase": "running", "tool_calls": 0, "last_tool": "dir_list"},
    )

    result = _format_task_report(record)
    assert report in result
    assert "agent_id=agent-stable task_id=task-long" in result
    assert "分页" not in result
    assert "阶段=running" not in result


def test_terminal_report_with_path_returns_file_path_and_preview_only():
    """已落盘报告返回 result_path + 短预览，完整正文交给 file_read。"""
    report = "完整报告正文" * 3_000
    record = SimpleNamespace(
        task_id="task-long",
        subagent_type="explore",
        label="盘点语义模型",
        status="completed",
        is_terminal=True,
        report=report,
        report_path="/mnt/user-data/workspace/.system/tool-results/report-long.md",
        error="",
        progress={},
    )

    result = _format_task_report(record)
    assert "result_path=/mnt/user-data/workspace/.system/tool-results/report-long.md" in result
    assert "file_read" in result
    assert "预览：" in result
    assert report not in result
    assert "分页" not in result


@pytest.mark.asyncio
async def test_background_offload_keeps_full_report_and_stores_result_path(monkeypatch):
    """后台报告超长时账本仍存全文，另记隐藏文件路径供主智能体 file_read。"""
    report = "完整报告" * 10_000

    async def fake_build(*_args, **_kwargs):
        return SimpleNamespace(update={"messages": [ToolMessage(content=report, tool_call_id="call-1")]})

    async def fake_offload(content: str, _runtime):
        return content, {
            "result_path": "/mnt/user-data/workspace/.system/tool-results/report.md",
            "result_size_chars": len(content),
        }

    monkeypatch.setattr(subagents_module, "_build_task_tool_impl", fake_build)
    monkeypatch.setattr(subagents_module, "_offload_subagent_report", fake_offload)
    record = SimpleNamespace(task_id="task-offload", subagent_type="explore", description="盘点", timeout_seconds=60)

    status, stored_report, error, result_meta = await subagents_module._run_background_attempt(
        record, SimpleNamespace(state={}), [], {}, {}, {}
    )

    assert status == "completed"
    assert error == ""
    assert stored_report == report
    assert result_meta["report_path"].endswith("report.md")
    assert result_meta["report_size_chars"] == len(report)


@pytest.mark.asyncio
async def test_background_attempt_uses_dedicated_task_stream_writer(monkeypatch):
    """后台子图过程走任务自己的 SSE writer，而不是父 Run 的 custom 流。"""
    captured: dict = {}

    async def fake_build(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(update={"messages": [ToolMessage(content="盘点完成", tool_call_id="call-1")]})

    def fake_writer(_payload):
        return None

    monkeypatch.setattr(subagents_module, "_build_task_tool_impl", fake_build)
    record = SimpleNamespace(task_id="task-stream", subagent_type="explore", description="盘点", timeout_seconds=60)

    await subagents_module._run_background_attempt(
        record,
        SimpleNamespace(state={}),
        [],
        {},
        {},
        {},
        stream_writer=fake_writer,
    )

    assert captured["stream_events"] is True
    assert captured["stream_writer"] is fake_writer


@pytest.mark.asyncio
async def test_finish_task_keeps_large_result_snapshot_out_of_text_meta(harness):
    """过程快照超过 TEXT 容量时改存 record_content，不能再撑爆 record_meta。"""
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)
    big_trail = [{"type": "tool", "content": "盘点明细 " + "x" * 120_000}]

    finished = await harness.service.finish_task(
        record.task_id,
        record.owner_token or "",
        status="completed",
        report="盘点结论：8 个文件",
        result_meta={"tool_status": "success", "subagent_trail": big_trail},
    )

    assert finished is not None
    assert finished.report == "盘点结论：8 个文件"
    assert finished.result_meta["subagent_trail"] == big_trail
    async with harness.get_session() as session:
        row = (
            await session.execute(select(ChatDataAgentInbox).where(ChatDataAgentInbox.message_id == record.task_id))
        ).scalar_one()
    assert len(row.record_meta or "") < 4096
    assert "subagent_trail" not in (row.record_meta or "")
    envelope = json.loads(row.record_content)
    assert envelope["report"] == "盘点结论：8 个文件"
    assert envelope["result_meta"]["subagent_trail"] == big_trail

    # 列表只需要状态标签：不加载报告与过程快照。
    listed = await harness.service.list_tasks(CONV, USER)
    assert listed and listed[0].report == ""
    detail = await harness.service.get_task(record.task_id, conversation_id=CONV, user_id=USER)
    assert detail is not None
    assert detail.report == "盘点结论：8 个文件"
    assert detail.result_meta["subagent_trail"] == big_trail


@pytest.mark.asyncio
async def test_cancel_pending_and_running_tasks(harness, monkeypatch):
    await harness.create_conversation()
    calls: list[dict] = []

    async def start_run(conversation_id, user_id, operator_name, runtime_context, **kwargs):
        calls.append(runtime_context)
        return "run-notification"

    monkeypatch.setattr(agent_inbox_service, "start_run", start_run)
    pending = await _create_task(harness)

    cancelled = await harness.service.cancel_task(CONV, USER, pending.task_id, "用户一")
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.report == "任务在开始执行前已取消。"
    assert cancelled.owner_token is None
    # 排队中任务取消即终态：立刻通知并唤醒主智能体。
    assert len(calls) == 1
    assert calls[0]["subagent_notification_task_id"] == pending.task_id
    assert calls[0]["subagent_notification_status"] == "cancelled"
    async with harness.get_session() as session:
        notification = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.message_id == notification_message_id(pending.task_id)
                )
            )
        ).scalar_one()
    assert "不需要汇报完成结论" in notification.record_content
    assert "任务在开始执行前已取消" in notification.record_content

    running = await _create_task(harness)
    await _start(harness, running)
    requested = await harness.service.cancel_task(CONV, USER, running.task_id, "用户一")
    assert requested is not None
    assert requested.status == "cancelling"
    # 运行中的任务要等执行体收口，收口前不能谎报已停止。
    assert len(calls) == 1

    settled = await harness.service.finish_task(
        running.task_id,
        running.owner_token or "",
        status="completed",
        report="迟到的完成结果",
    )
    assert settled is not None
    assert settled.status == "cancelled"
    # 取消请求优先于迟到的完成结果，但已产出的报告仍保留供用户查看。
    assert settled.report == "迟到的完成结果"
    # 取消终态不再被抑制：通知可派发并唤醒主智能体。
    assert await harness.service.dispatch_pending_notifications(limit=10) == 1
    assert len(calls) == 2
    assert calls[1]["subagent_notification_task_id"] == running.task_id
    assert calls[1]["subagent_notification_status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancelled_background_runner_dispatches_wakeup_notification():
    """执行体被取消收口后立即派发通知；跨节点路径由调度轮询兜底。"""
    calls: list[tuple[str, object]] = []

    class _Service:
        async def get_task(self, _task_id):
            return SimpleNamespace(status="cancelling", error="")

        async def finish_task(self, *_args, **kwargs):
            calls.append(("finish", kwargs.get("status")))

        async def dispatch_pending_notifications(self, *, limit=10):
            calls.append(("dispatch", limit))
            return 1

    record = SimpleNamespace(task_id="task-cancel", owner_token="owner-1", description="取消通知核对")
    await subagents_module._finish_cancelled_background_task(_Service(), record, {}, history=[])

    assert calls[0] == ("finish", "cancelled")
    assert calls[1] == ("dispatch", 1)


@pytest.mark.asyncio
async def test_subagent_tool_dispatches_management_actions(harness, monkeypatch):
    await harness.create_conversation()
    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None, **kwargs: object())
    record = await _create_task(harness)
    await _start(harness, record)
    await harness.service.finish_task(
        record.task_id,
        record.owner_token or "",
        status="completed",
        report="完整盘点结论",
    )
    tool = build_subagent_tool([_spec()])
    runtime = _fake_runtime()

    listed = await invoke_subagent_tool(tool, "manage", action="list", runtime=runtime)
    assert record.task_id in listed

    checked = await invoke_subagent_tool(tool, "manage", action="check", runtime=runtime, task_id=record.task_id)
    assert "完整盘点结论" in checked

    waited = await invoke_subagent_tool(tool, "manage", action="wait", runtime=runtime, task_ids=[record.task_id])
    assert "完整盘点结论" in waited

    cancelled = await invoke_subagent_tool(tool, "manage", action="cancel", runtime=runtime, task_id=record.task_id)
    assert "无需取消" in cancelled

    for kwargs in (
        {"action": "check"},
        {"action": "cancel"},
        {"action": "wait"},
        {"action": "list", "task_id": record.task_id},
    ):
        with pytest.raises(ValidationError):
            await invoke_subagent_tool(tool, "manage", runtime=runtime, **kwargs)


@pytest.mark.asyncio
async def test_capacity_guard_serializes_active_tasks(harness, monkeypatch):
    monkeypatch.setattr(service_module, "get_runtime_setting", _settings(max_active=1))
    await harness.create_conversation()
    await _create_task(harness)

    with pytest.raises(SubagentTaskCapacityError, match="已达上限"):
        await _create_task(harness)


@pytest.mark.asyncio
async def test_subagent_capacity_counts_distinct_agents_and_allows_follow_up(harness, monkeypatch):
    """主对话子智能体总数有新开上限；追问复用原子智能体不占新额度。"""
    monkeypatch.setattr(
        service_module,
        "get_runtime_setting",
        _settings(max_active=20, max_subagents_per_conversation=2),
    )
    await harness.create_conversation()
    first = await _create_task(harness)
    await harness.service.finish_task(
        first.task_id,
        first.owner_token or "",
        status="completed",
        report="第一份结论",
    )
    second = await harness.service.create_task(
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        subagent_type="explore",
        description="另一个独立盘点任务",
        label="独立任务",
        parent_tool_call_id="call-second",
        parent_owner_token="parent-owner",
        operator_name="用户一",
    )
    await harness.service.finish_task(
        second.task_id,
        second.owner_token or "",
        status="completed",
        report="第二份结论",
    )

    with pytest.raises(SubagentTaskCapacityError, match="达到上限"):
        await _create_task(harness)
    async with harness.get_session() as session:
        assert await harness.service._dao.count_subagents(session, CONV, USER) == 2

    follow_up = await harness.service.create_task(
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        subagent_type="explore",
        description="在第二份结论上补充渠道拆分",
        label=None,
        parent_tool_call_id="call-follow-up",
        parent_owner_token="parent-owner",
        operator_name="用户一",
        parent_task_id=second.task_id,
        agent_session_id=second.agent_session_id,
    )
    assert follow_up.agent_session_id == second.agent_session_id
    assert follow_up.turn_index == 2
    async with harness.get_session() as session:
        assert await harness.service._dao.count_subagents(session, CONV, USER) == 2


@pytest.mark.asyncio
async def test_completion_notification_claims_only_its_own_message(harness, monkeypatch):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)
    await harness.service.finish_task(
        record.task_id,
        record.owner_token or "",
        status="completed",
        report="完整盘点结论",
    )

    calls: list[dict] = []

    async def start_run(conversation_id, user_id, operator_name, runtime_context, **kwargs):
        calls.append(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "operator_name": operator_name,
                "runtime_context": runtime_context,
                "kwargs": kwargs,
            }
        )
        return "run-notification"

    monkeypatch.setattr(agent_inbox_service, "start_run", start_run)

    assert await harness.service.dispatch_pending_notifications(limit=1) == 1
    assert len(calls) == 1
    assert calls[0]["conversation_id"] == CONV
    assert calls[0]["user_id"] == USER
    assert calls[0]["kwargs"]["adopt_pending_messages"] is False
    assert calls[0]["kwargs"]["selected_message_id"] == notification_message_id(record.task_id)
    # 报告正文在通知消息里，Run 只领取这一条；query 留空避免重复拼同一段报告。
    assert calls[0]["runtime_context"]["query"] == ""
    assert calls[0]["runtime_context"]["subagent_notification_task_id"] == record.task_id
    assert calls[0]["runtime_context"]["disable_user_interaction"] is True

    async with harness.get_session() as session:
        message = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.message_id == notification_message_id(record.task_id)
                )
            )
        ).scalar_one()
    assert message.record_type == "message"
    assert message.record_status == "pending"
    assert "完整盘点结论" in message.record_content
    assert json.loads(message.record_meta)["hide_from_ui"] is True

    notified = await harness.service.get_task(record.task_id)
    assert notified is not None
    assert notified.notified_at is not None


@pytest.mark.asyncio
async def test_mark_viewed_cancels_pending_notification(harness, monkeypatch):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)
    record = await harness.service.finish_task(
        record.task_id,
        record.owner_token or "",
        status="completed",
        report="已读报告",
    )

    async def start_run(*_args, **_kwargs):
        return "run-notification"

    monkeypatch.setattr(agent_inbox_service, "start_run", start_run)
    await harness.service.dispatch_pending_notifications(limit=1)

    await harness.service.mark_viewed(record)
    async with harness.get_session() as session:
        message = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.message_id == notification_message_id(record.task_id)
                )
            )
        ).scalar_one()
    assert message.record_status == "cancelled"
    assert message.delete_flag == 1


@pytest.mark.asyncio
async def test_background_tool_spawns_persisted_task_and_notifies(harness, monkeypatch):
    await harness.create_conversation()
    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _runnable("盘点结论：近 30 天平稳"),
    )
    calls: list[dict] = []

    async def start_run(conversation_id, user_id, operator_name, runtime_context, **kwargs):
        calls.append(kwargs)
        return "run-notification"

    monkeypatch.setattr(agent_inbox_service, "start_run", start_run)

    tool = build_subagent_tool([_spec()])
    result = await invoke_subagent_tool(
        tool,
        "dispatch",
        description="盘点近 30 天入金趋势",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_bg_1"),
        background=True,
        label="入金趋势",
    )

    assert isinstance(result, str)
    assert "后台启动" in result
    task_id = _task_id_from_text(result)
    record = await _wait_terminal(harness, task_id)
    assert record.status == "completed"
    assert "盘点结论：近 30 天平稳" in record.report
    assert record.result_meta.get("tool_status") == "success"
    deadline = time.monotonic() + 2.0
    while not calls and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert calls and calls[0]["adopt_pending_messages"] is False
    assert calls[0]["selected_message_id"] == notification_message_id(task_id)


@pytest.mark.asyncio
async def test_background_task_compiles_without_parent_run_fence(harness, monkeypatch):
    """后台任务不得挂父 Run 的执行围栏：父 Run 收口后任务仍需继续调工具。"""
    await harness.create_conversation()
    compiled_with: list[object] = []
    parent_controls = SimpleNamespace(run_id="run-parent", owner_token="parent-owner")

    def fake_compile(spec, run_controls=None, **kwargs):
        compiled_with.append(run_controls)
        return _runnable("盘点结论")

    monkeypatch.setattr(subagents_module, "_compile_subagent", fake_compile)
    tool = build_subagent_tool([_spec()], run_controls=parent_controls)
    result = await invoke_subagent_tool(
        tool,
        "dispatch",
        description="盘点语义模型目录",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_bg_fence"),
        background=True,
    )

    assert "后台启动" in result
    record = await _wait_terminal(harness, _task_id_from_text(result))
    assert record.status == "completed"
    assert compiled_with == [parent_controls, None]


@pytest.mark.asyncio
async def test_background_tool_unknown_type_does_not_write_task(harness):
    await harness.create_conversation()
    tool = build_subagent_tool([_spec()])
    with pytest.raises(ValueError) as exc_info:
        await invoke_subagent_tool(
            tool, "dispatch", description="x", subagent_type="不存在", runtime=_fake_runtime(), background=True
        )
    assert "类型不存在" in str(exc_info.value)
    records = await harness.service.list_tasks(CONV, USER)
    assert records == []


@pytest.mark.asyncio
async def test_background_timeout_keeps_partial_report(harness, monkeypatch):
    await harness.create_conversation()
    monkeypatch.setattr(service_module, "get_runtime_setting", _settings(timeout_seconds=1))
    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _runnable("中断前的中间结论", delay_after_yield=5),
    )
    tool = build_subagent_tool([_spec()])
    result = await invoke_subagent_tool(
        tool,
        "dispatch",
        description="很慢的任务",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_timeout"),
        background=True,
    )
    task_id = _task_id_from_text(result)
    local_task = harness.service._local_tasks.get(task_id)
    assert local_task is not None
    await local_task
    record = await harness.service.get_task(task_id)
    assert record is not None
    assert record.status == "timeout"
    assert record.timeout_seconds == 1
    assert "执行超时" in record.report
    assert "中断前的中间结论" in record.report
    assert record.result_meta.get("tool_status") == "error"
    assert record.result_meta.get("subagent_trail")


def test_start_schema_rejects_model_supplied_timeout():
    """超时只由全局配置控制，模型硬塞 timeout_seconds 必须被参数校验拒绝。"""
    with pytest.raises(ValueError, match="timeout_seconds"):
        subagents_module.DispatchArgs(
            description="快速任务",
            subagent_type="explore",
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_sync_task_path_still_returns_tool_message(monkeypatch):
    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _runnable("同步结论"),
    )
    tool = build_subagent_tool([_spec()])
    result = await invoke_subagent_tool(
        tool,
        "dispatch",
        background=False,
        description="同步任务",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_sync"),
    )
    assert not isinstance(result, str)
    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.content == "同步结论"


def test_system_prompt_documents_background_and_subagent_tools():
    middleware = subagents_module.SubagentMiddleware([_spec()])
    assert "background=true" in middleware.system_prompt
    assert 'subagent(manage={action:"list"})' in middleware.system_prompt
    assert "list → agent_id → task_id" in middleware.system_prompt
    # 精确动作语法只写在工具描述里，系统提示不重复一遍。
    description = middleware.tools[0].description
    for keyword in ("dispatch", "send", "manage", "decide", "list/check/wait/cancel"):
        assert keyword in description
    assert "async_task" not in middleware.system_prompt
    assert "list_async_tasks" not in middleware.system_prompt
    assert "check_async_task" not in middleware.system_prompt


@pytest.mark.asyncio
async def test_follow_up_reuses_session_history(harness, monkeypatch):
    await harness.create_conversation()
    captured_states: list[dict] = []

    class _Runnable:
        async def astream(self, state, _config, *, stream_mode):
            captured_states.append(dict(state))
            yield "updates", {"model": {"messages": [AIMessage(content=f"第 {len(captured_states)} 轮结论")]}}

    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _Runnable(),
    )
    tool = build_subagent_tool([_spec()])
    follow_tool = build_subagent_tool([_spec()])
    first = await invoke_subagent_tool(
        tool,
        "dispatch",
        description="盘点近 30 天入金趋势",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_bg_first"),
        background=True,
        label="入金趋势",
    )
    first_record = await _wait_terminal(harness, _task_id_from_text(first))
    assert first_record.status == "completed"
    assert captured_states[0]["messages"][0].content == "盘点近 30 天入金趋势"

    follow = await invoke_subagent_tool(
        follow_tool,
        "send",
        task_id=first_record.task_id,
        message="再按渠道拆一下，结论附上渠道 Top3",
        runtime=_fake_runtime(tool_call_id="call_bg_follow"),
    )
    assert "第 2 轮追问" in follow
    second_record = await _wait_terminal(harness, _task_id_from_text(follow))
    assert second_record.status == "completed"
    assert second_record.parent_task_id == first_record.task_id
    assert second_record.root_task_id == first_record.task_id
    assert second_record.agent_id == first_record.agent_id
    assert second_record.agent_session_id == first_record.agent_session_id
    assert second_record.parent_agent_id == ""
    assert second_record.root_agent_id == first_record.agent_id
    assert second_record.depth == 1
    assert second_record.turn_index == 2

    second_messages = captured_states[1]["messages"]
    assert [message.type for message in second_messages] == ["human", "ai", "human"]
    assert second_messages[0].content == "盘点近 30 天入金趋势"
    assert second_messages[1].content == "第 1 轮结论"
    assert second_messages[2].content == "再按渠道拆一下，结论附上渠道 Top3"
    session = await harness.service.load_session_messages(
        first_record.task_id,
        conversation_id=CONV,
        user_id=USER,
    )
    assert [item["type"] for item in session] == ["human", "ai", "human", "ai"]
    assert session[-1]["content"] == "第 2 轮结论"


@pytest.mark.asyncio
async def test_follow_up_inherits_write_scopes_when_omitted(harness, monkeypatch):
    await harness.create_conversation()

    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _runnable("交付物已生成"),
    )
    start_tool = build_subagent_tool([_spec("general")])
    follow_tool = build_subagent_tool([_spec("general")])
    first = await invoke_subagent_tool(
        start_tool,
        "dispatch",
        description="生成 Word 报告",
        subagent_type="general",
        write_scopes=["outputs/word"],
        runtime=_fake_runtime(tool_call_id="call_scope_first"),
    )
    first_record = await _wait_terminal(harness, _task_id_from_text(first))
    assert first_record.write_scopes == ("outputs/word",)

    follow = await invoke_subagent_tool(
        follow_tool,
        "send",
        task_id=first_record.task_id,
        message="补充目录页",
        runtime=_fake_runtime(tool_call_id="call_scope_follow"),
    )
    second_record = await _wait_terminal(harness, _task_id_from_text(follow))

    assert second_record.write_scopes == ("outputs/word",)

    override = await invoke_subagent_tool(
        follow_tool,
        "send",
        task_id=second_record.task_id,
        message="目录页另存到附件目录",
        write_scopes=["outputs/word/annex"],
        runtime=_fake_runtime(tool_call_id="call_scope_override"),
    )
    third_record = await _wait_terminal(harness, _task_id_from_text(override))

    assert third_record.write_scopes == ("outputs/word/annex",)


@pytest.mark.asyncio
async def test_follow_up_without_write_scopes_skips_ledger_rewrite(harness, monkeypatch):
    """未声明范围的任务链不额外写账本，避免无意义的并发事务。"""
    await harness.create_conversation()
    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _runnable("盘点完成"),
    )
    calls: list[tuple] = []
    original = harness.service.set_task_write_scopes

    async def _spy(*args):
        calls.append(args)
        return await original(*args)

    monkeypatch.setattr(harness.service, "set_task_write_scopes", _spy)
    tool = build_subagent_tool([_spec()])
    follow_tool = build_subagent_tool([_spec()])
    first = await invoke_subagent_tool(
        tool,
        "dispatch",
        description="盘点近 30 天入金趋势",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_scope_none_first"),
    )
    first_record = await _wait_terminal(harness, _task_id_from_text(first))
    follow = await invoke_subagent_tool(
        follow_tool,
        "send",
        task_id=first_record.task_id,
        message="补充渠道拆分",
        runtime=_fake_runtime(tool_call_id="call_scope_none_follow"),
    )
    second_record = await _wait_terminal(harness, _task_id_from_text(follow))

    assert second_record.write_scopes == ()
    assert calls == []


@pytest.mark.asyncio
async def test_follow_up_can_switch_from_background_to_sync(harness, monkeypatch):
    """后台首轮完成后，当前轮可用同步追问直接拿结论；历史与会话身份保持不变。"""
    await harness.create_conversation()
    captured_states: list[dict] = []

    class _Runnable:
        async def astream(self, state, _config, *, stream_mode):
            captured_states.append(dict(state))
            yield "updates", {"model": {"messages": [AIMessage(content=f"第 {len(captured_states)} 轮结论")]}}

    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _Runnable(),
    )
    tool = build_subagent_tool([_spec()])
    follow_tool = build_subagent_tool([_spec()])
    first = await invoke_subagent_tool(
        tool,
        "dispatch",
        description="后台盘点近 30 天入金趋势",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_bg_to_sync_1"),
        background=True,
    )
    first_record = await _wait_terminal(harness, _task_id_from_text(first))
    await harness.service.mark_viewed(first_record)

    follow = await invoke_subagent_tool(
        follow_tool,
        "send",
        task_id=first_record.task_id,
        message="同步补充渠道 Top3",
        runtime=_fake_runtime(tool_call_id="call_bg_to_sync_2"),
        background=False,
    )

    assert follow.update["messages"][0].content == "第 2 轮结论"
    records = await harness.service.list_tasks(CONV, USER, limit=10)
    second_record = next(item for item in records if item.parent_task_id == first_record.task_id)
    assert second_record.status == "completed"
    assert second_record.execution_mode == "sync"
    assert second_record.notify_on_finish is False
    assert second_record.agent_session_id == first_record.agent_session_id
    assert second_record.turn_index == 2
    assert [message.content for message in captured_states[1]["messages"]] == [
        "后台盘点近 30 天入金趋势",
        "第 1 轮结论",
        "同步补充渠道 Top3",
    ]
    async with harness.get_session() as session:
        notification = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.message_id == notification_message_id(second_record.task_id),
                    ChatDataAgentInbox.conversation_id == CONV,
                    ChatDataAgentInbox.user_id == USER,
                )
            )
        ).scalar_one_or_none()
    assert notification is None


@pytest.mark.asyncio
async def test_sync_follow_up_failure_preserves_prior_history(harness, monkeypatch):
    """A failed sync follow-up must not discard the previous session snapshot."""
    await harness.create_conversation()
    fail_follow_up = False

    class _Runnable:
        async def astream(self, state, _config, *, stream_mode):
            if fail_follow_up:
                raise RuntimeError("sync follow-up failure")
            yield "updates", {"model": {"messages": [AIMessage(content="first answer")]}}

    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _Runnable(),
    )
    tool = build_subagent_tool([_spec()])
    follow_tool = build_subagent_tool([_spec()])
    first = await invoke_subagent_tool(
        tool,
        "dispatch",
        description="first task",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_sync_history_1"),
        background=True,
    )
    first_record = await _wait_terminal(harness, _task_id_from_text(first))

    fail_follow_up = True
    follow = await invoke_subagent_tool(
        follow_tool,
        "send",
        task_id=first_record.task_id,
        message="follow-up task",
        runtime=_fake_runtime(tool_call_id="call_sync_history_2"),
        background=False,
    )

    assert follow.update["messages"][0].status == "error"
    records = await harness.service.list_tasks(CONV, USER, limit=10)
    second_record = next(item for item in records if item.parent_task_id == first_record.task_id)
    assert second_record.status == "failed"
    session = await harness.service.load_session_messages(
        first_record.task_id,
        conversation_id=CONV,
        user_id=USER,
    )
    assert [item["type"] for item in session] == ["human", "ai", "human"]
    assert [item["content"] for item in session] == ["first task", "first answer", "follow-up task"]


@pytest.mark.asyncio
async def test_follow_up_can_switch_from_sync_to_background(harness, monkeypatch):
    """同步首轮完成后，仍可异步追问；后台轮次沿用同一子会话与历史。"""
    await harness.create_conversation()
    captured_states: list[dict] = []

    class _Runnable:
        async def astream(self, state, _config, *, stream_mode):
            captured_states.append(dict(state))
            yield "updates", {"model": {"messages": [AIMessage(content=f"第 {len(captured_states)} 轮结论")]}}

    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _Runnable(),
    )
    tool = build_subagent_tool([_spec()])
    follow_tool = build_subagent_tool([_spec()])
    first = await invoke_subagent_tool(
        tool,
        "dispatch",
        description="同步盘点近 30 天入金趋势",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_sync_to_bg_1"),
        background=False,
    )
    assert first.update["messages"][0].content == "第 1 轮结论"
    records = await harness.service.list_tasks(CONV, USER, limit=10)
    first_record = next(item for item in records if item.execution_mode == "sync")

    follow = await invoke_subagent_tool(
        follow_tool,
        "send",
        task_id=first_record.task_id,
        message="后台补充渠道 Top3",
        runtime=_fake_runtime(tool_call_id="call_sync_to_bg_2"),
        background=True,
    )
    second_record = await _wait_terminal(harness, _task_id_from_text(follow))

    assert second_record.status == "completed"
    assert second_record.execution_mode == "background"
    assert second_record.parent_task_id == first_record.task_id
    assert second_record.agent_session_id == first_record.agent_session_id
    assert second_record.turn_index == 2
    assert [message.content for message in captured_states[1]["messages"]] == [
        "同步盘点近 30 天入金趋势",
        "第 1 轮结论",
        "后台补充渠道 Top3",
    ]


def test_task_progress_drops_compressing_flag_in_terminal_state():
    """异常中断可能收不到 completed；终态读取时不能残留「正在压缩」。"""
    record = SubagentTaskRecord(
        task_id="task-terminal",
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        owner_token=None,
        status="completed",
        report="",
        result_meta={},
        meta={"progress": {"phase": "running", "compressing": True, "compaction_count": 1}},
        created_at=datetime.now(),
        updated_at=None,
        started_at=None,
        finished_at=None,
        notified_at=None,
        lease_expire_time=None,
    )

    assert "compressing" not in record.progress
    assert record.progress["phase"] == "completed"
    assert record.progress["compaction_count"] == 1


def test_task_identity_falls_back_for_legacy_meta():
    """旧任务行没有 agent_* 字段时按 root_task_id 推导，历史数据无需回填。"""
    record = SubagentTaskRecord(
        task_id="turn-2",
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        owner_token=None,
        status="completed",
        report="",
        result_meta={},
        meta={
            "parent_task_id": "turn-1",
            "root_task_id": "session-1",
            "turn_index": 2,
        },
        created_at=datetime.now(),
        updated_at=None,
        started_at=None,
        finished_at=None,
        notified_at=None,
        lease_expire_time=None,
    )

    assert record.root_task_id == "session-1"
    assert record.agent_session_id == "session-1"
    assert record.agent_id == "session-1"
    assert record.parent_agent_id == ""
    assert record.root_agent_id == "session-1"
    assert record.depth == 1


def test_task_record_compaction_count_from_result_meta():
    """列表摘要也要带压缩次数，前端无需拉完整 result_meta 即可按会话求和。"""
    record = SubagentTaskRecord(
        task_id="turn-1",
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        owner_token=None,
        status="completed",
        report="",
        result_meta={"subagent_compactions": [{"id": "compact-1"}, {"id": "compact-2"}]},
        meta={},
        created_at=datetime.now(),
        updated_at=None,
        started_at=None,
        finished_at=None,
        notified_at=None,
        lease_expire_time=None,
    )

    assert record.compaction_count == 2
    assert record.as_dict(include_report=False)["compaction_count"] == 2


def test_task_record_compaction_count_from_running_progress():
    """运行中还没有 result_meta，压缩次数随心跳透出，任务卡不会一直显示 0。"""
    record = SubagentTaskRecord(
        task_id="turn-1",
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        owner_token=None,
        status="running",
        report="",
        result_meta={},
        meta={"progress": {"phase": "running", "compaction_count": 3}},
        created_at=datetime.now(),
        updated_at=None,
        started_at=None,
        finished_at=None,
        notified_at=None,
        lease_expire_time=None,
    )

    assert record.compaction_count == 3
    assert record.as_dict(include_report=False)["compaction_count"] == 3


def test_task_record_compaction_count_uses_larger_of_progress_and_result():
    """最后一次心跳滞后于结果快照时，取较大值避免把次数算少。"""
    record = SubagentTaskRecord(
        task_id="turn-1",
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        owner_token=None,
        status="completed",
        report="",
        result_meta={"subagent_compactions": [{"id": "c1"}, {"id": "c2"}]},
        meta={"progress": {"phase": "running", "compaction_count": 1}},
        created_at=datetime.now(),
        updated_at=None,
        started_at=None,
        finished_at=None,
        notified_at=None,
        lease_expire_time=None,
    )

    assert record.compaction_count == 2


@pytest.mark.asyncio
async def test_sync_task_is_tracked_without_finish_notification(harness, monkeypatch):
    """同步执行也进入统一任务账本；终态可供前端展示，但不生成唤醒通知。"""
    await harness.create_conversation()
    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _runnable("同步任务结论"),
    )
    tool = build_subagent_tool([_spec()])

    result = await invoke_subagent_tool(
        tool,
        "dispatch",
        background=False,
        description="当前轮同步核对",
        subagent_type="explore",
        label="同步核对",
        runtime=_fake_runtime(tool_call_id="call_sync_tracked"),
    )

    assert result.update["messages"][0].content == "同步任务结论"
    records = await harness.service.list_tasks(CONV, USER, limit=10)
    assert len(records) == 1
    listed = records[0]
    assert listed.execution_mode == "sync"
    record = await harness.service.get_task(listed.task_id, conversation_id=CONV, user_id=USER)
    assert record is not None
    assert record.parent_tool_call_id == "call_sync_tracked"
    assert record.notify_on_finish is False
    assert record.status == "completed"
    assert record.report == "同步任务结论"
    assert record.notified_at is not None
    assert record.result_meta.get("tool_status") == "success"
    assert record.result_meta.get("subagent_trail")
    assert await harness.service.dispatch_pending_notifications(limit=10) == 0
    async with harness.get_session() as session:
        notification = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.message_id == notification_message_id(record.task_id),
                    ChatDataAgentInbox.conversation_id == CONV,
                    ChatDataAgentInbox.user_id == USER,
                )
            )
        ).scalar_one_or_none()
    assert notification is None


@pytest.mark.asyncio
async def test_sync_task_persists_write_scopes(harness, monkeypatch):
    """同步任务和后台任务共用同一份写范围账本。"""
    await harness.create_conversation()
    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _runnable("Word 已生成"),
    )
    tool = build_subagent_tool([_spec("general")])

    result = await invoke_subagent_tool(
        tool,
        "dispatch",
        background=False,
        description="生成 Word 报告",
        subagent_type="general",
        label="Word 报告",
        write_scopes=[" ./outputs/word/ ", "outputs\\word"],
        runtime=_fake_runtime(tool_call_id="call_sync_scope"),
    )

    assert result.update["messages"][0].content == "Word 已生成"
    records = await harness.service.list_tasks(CONV, USER, limit=10)
    assert len(records) == 1
    record = await harness.service.get_task(records[0].task_id, conversation_id=CONV, user_id=USER)
    assert record is not None
    assert record.execution_mode == "sync"
    assert record.write_scopes == ("outputs/word",)


@pytest.mark.asyncio
async def test_sync_task_cancellation_becomes_tool_error(monkeypatch):
    class _Writer:
        async def start(self):
            return None

        async def aclose(self):
            return None

    finished: dict = {}

    class _Service:
        def create_event_writer(self, _started):
            return _Writer()

        async def finish_task(self, *_args, **kwargs):
            finished.update(kwargs)

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError("subagent_task_cancelled")

    monkeypatch.setattr(subagents_module, "_build_task_tool_impl", cancelled)
    started = SimpleNamespace(
        task_id="task-1",
        owner_token="owner-1",
        subagent_type="explore",
        description="task",
        agent_session_id="session-1",
    )

    result = await subagents_module._run_registered_sync_task(
        _Service(),
        started,
        [],
        {},
        "explore",
        "task",
        _fake_runtime(tool_call_id="call-cancel"),
    )

    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    assert message.tool_call_id == "call-cancel"
    assert finished["status"] == "cancelled"


@pytest.mark.asyncio
async def test_parent_cancellation_still_propagates_from_sync_task(monkeypatch):
    class _Writer:
        async def start(self):
            return None

        async def aclose(self):
            return None

    class _Service:
        def create_event_writer(self, _started):
            return _Writer()

        async def finish_task(self, *_args, **_kwargs):
            return None

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(subagents_module, "_build_task_tool_impl", cancelled)
    started = SimpleNamespace(
        task_id="task-1",
        owner_token="owner-1",
        subagent_type="explore",
        description="task",
        agent_session_id="session-1",
    )

    with pytest.raises(asyncio.CancelledError):
        await subagents_module._run_registered_sync_task(
            _Service(),
            started,
            [],
            {},
            "explore",
            "task",
            _fake_runtime(tool_call_id="call-parent-cancel"),
        )


@pytest.mark.asyncio
async def test_sync_task_start_failure_does_not_leave_pending_row(harness, monkeypatch):
    """账本启动失败时降级执行，但已登记任务必须立即收口而不是残留 pending。"""
    await harness.create_conversation()
    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _runnable("降级同步结论"),
    )

    async def fail_start(_task_id: str, _owner_token: str):
        raise RuntimeError("模拟启动失败")

    monkeypatch.setattr(harness.service, "start_task", fail_start)
    tool = build_subagent_tool([_spec()])

    result = await invoke_subagent_tool(
        tool,
        "dispatch",
        background=False,
        description="启动失败降级核对",
        subagent_type="explore",
        label="降级核对",
        runtime=_fake_runtime(tool_call_id="call_sync_start_failure"),
    )

    assert result.update["messages"][0].content == "降级同步结论"
    records = await harness.service.list_tasks(CONV, USER, limit=10)
    assert len(records) == 1
    record = records[0]
    assert record.execution_mode == "sync"
    assert record.status == "cancelled"
    assert record.notify_on_finish is False
    assert record.notified_at is not None
    assert await harness.service.dispatch_pending_notifications(limit=10) == 0


@pytest.mark.asyncio
async def test_create_task_rejects_second_active_turn_in_same_session(harness):
    """前置查询与建任务之间存在窗口，建任务事务内必须再挡一次并行追问。"""
    await harness.create_conversation()
    first = await _create_task(harness)
    await _start(harness, first)
    await harness.service.finish_task(
        first.task_id,
        first.owner_token or "",
        status="completed",
        report="第一轮结论",
    )
    second = await harness.service.create_task(
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        subagent_type="explore",
        description="第二轮",
        label=None,
        parent_tool_call_id="call-follow-1",
        parent_owner_token="parent-owner",
        operator_name="用户一",
        parent_task_id=first.task_id,
        root_task_id=first.task_id,
    )

    with pytest.raises(SubagentTaskCapacityError, match="已有进行中的追问任务"):
        await harness.service.create_task(
            conversation_id=CONV,
            user_id=USER,
            parent_run_id="run-parent",
            subagent_type="explore",
            description="重复的第三轮",
            label=None,
            parent_tool_call_id="call-follow-2",
            parent_owner_token="parent-owner",
            operator_name="用户一",
            parent_task_id=first.task_id,
            root_task_id=first.task_id,
        )

    assert second.turn_index == 2


@pytest.mark.asyncio
async def test_follow_up_from_old_turn_anchors_to_latest_session_turn(harness, monkeypatch):
    """从旧任务卡追问也要线性续写：parent 指向最新一轮，轮次不重复。"""
    await harness.create_conversation()
    responses = iter(["第 1 轮", "第 2 轮", "第 3 轮", "第 4 轮"])

    class _Runnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content=next(responses))]}}

    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _Runnable(),
    )
    tool = build_subagent_tool([_spec()])
    follow_tool = build_subagent_tool([_spec()])
    first = await invoke_subagent_tool(
        tool,
        "dispatch",
        description="第一轮",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_turn_1"),
        background=True,
    )
    first_record = await _wait_terminal(harness, _task_id_from_text(first))

    second = await invoke_subagent_tool(
        follow_tool,
        "send",
        task_id=first_record.task_id,
        message="第二轮",
        runtime=_fake_runtime(tool_call_id="call_turn_2"),
    )
    second_record = await _wait_terminal(harness, _task_id_from_text(second))
    third = await invoke_subagent_tool(
        follow_tool,
        "send",
        task_id=second_record.task_id,
        message="第三轮",
        runtime=_fake_runtime(tool_call_id="call_turn_3"),
    )
    third_record = await _wait_terminal(harness, _task_id_from_text(third))

    stale = await invoke_subagent_tool(
        follow_tool,
        "send",
        task_id=first_record.task_id,
        message="从旧卡片继续",
        runtime=_fake_runtime(tool_call_id="call_turn_4"),
    )
    fourth_record = await _wait_terminal(harness, _task_id_from_text(stale))

    assert fourth_record.parent_task_id == third_record.task_id
    assert fourth_record.root_task_id == first_record.task_id
    assert fourth_record.turn_index == 4


@pytest.mark.asyncio
async def test_follow_up_ignores_stale_latest_task_when_snapshot_write_failed(harness, monkeypatch):
    """快照写失败时 latest_task_id 可能停在旧轮次，追问必须锚定用户指定的新任务。"""
    await harness.create_conversation()
    responses = iter(["第 1 轮结论", "第 2 轮结论", "兜底追问结论"])

    class _Runnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content=next(responses))]}}

    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _Runnable(),
    )
    tool = build_subagent_tool([_spec()])
    follow_tool = build_subagent_tool([_spec()])
    first = await invoke_subagent_tool(
        tool,
        "dispatch",
        description="第一轮",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_stale_1"),
        background=True,
    )
    first_record = await _wait_terminal(harness, _task_id_from_text(first))
    second = await invoke_subagent_tool(
        follow_tool,
        "send",
        task_id=first_record.task_id,
        message="第二轮",
        runtime=_fake_runtime(tool_call_id="call_stale_2"),
    )
    second_record = await _wait_terminal(harness, _task_id_from_text(second))

    async def stale_latest(*_args, **_kwargs):
        return first_record

    monkeypatch.setattr(harness.service, "get_latest_task_in_session", stale_latest)
    third = await invoke_subagent_tool(
        follow_tool,
        "send",
        task_id=second_record.task_id,
        message="在第二轮基础上继续",
        runtime=_fake_runtime(tool_call_id="call_stale_3"),
    )
    third_record = await _wait_terminal(harness, _task_id_from_text(third))

    assert third_record.parent_task_id == second_record.task_id
    assert third_record.turn_index == 3


@pytest.mark.asyncio
async def test_follow_up_falls_back_to_legacy_report_when_snapshot_missing(harness, monkeypatch):
    await harness.create_conversation()
    captured_states: list[dict] = []

    class _Runnable:
        async def astream(self, state, _config, *, stream_mode):
            captured_states.append(dict(state))
            yield "updates", {"model": {"messages": [AIMessage(content="追问结论")]}}

    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None, **kwargs: _Runnable(),
    )
    record = await _create_task(harness)
    await _start(harness, record)
    await harness.service.finish_task(
        record.task_id,
        record.owner_token or "",
        status="completed",
        report="旧任务结论",
    )
    tool = build_subagent_tool([_spec()])
    result = await invoke_subagent_tool(
        tool, "send", task_id=record.task_id, message="在旧结论基础上补充说明", runtime=_fake_runtime()
    )

    assert "第 2 轮追问" in result
    second = await _wait_terminal(harness, _task_id_from_text(result))
    assert second.status == "completed"
    messages = captured_states[0]["messages"]
    assert [message.content for message in messages] == [
        "深度分析近 30 天入金趋势",
        "旧任务结论",
        "在旧结论基础上补充说明",
    ]


def test_history_roundtrip_keeps_tool_calls_and_tool_results(monkeypatch):
    monkeypatch.setattr(subagent_history_module, "SUBAGENT_HISTORY_TOTAL_MAX_CHARS", 10_000)
    original = [
        HumanMessage(content="查一下入金", id="u1"),
        AIMessage(
            content="",
            id="a1",
            tool_calls=[{"name": "sql_query", "args": {"sql": "select 1"}, "id": "call-1", "type": "tool_call"}],
        ),
        ToolMessage(content="1 行结果", tool_call_id="call-1", status="success", id="t1"),
        AIMessage(content="结论：正常", id="a2"),
    ]

    payloads = subagents_module.serialize_subagent_messages(original)
    restored = subagents_module.deserialize_subagent_messages(payloads)

    assert [message.type for message in restored] == ["human", "ai", "tool", "ai"]
    assert restored[1].tool_calls[0]["id"] == "call-1"
    assert restored[2].tool_call_id == "call-1"
    assert restored[-1].content == "结论：正常"


def test_history_serialization_drops_incomplete_tool_call_group():
    """超时/取消停在工具中途时，快照不能留下没有 ToolMessage 的 tool_calls。"""
    original = [
        HumanMessage(content="查一下入金", id="u1"),
        AIMessage(
            content="正在查询",
            id="a1",
            tool_calls=[{"name": "sql_query", "args": {"sql": "select 1"}, "id": "call-1", "type": "tool_call"}],
        ),
    ]

    payloads = subagents_module.serialize_subagent_messages(original)
    restored = subagents_module.deserialize_subagent_messages(payloads)

    assert [message.type for message in restored] == ["human"]


def test_history_trimming_drops_orphans_and_notes_truncation(monkeypatch):
    monkeypatch.setattr(subagent_history_module, "SUBAGENT_HISTORY_TOTAL_MAX_CHARS", 60)
    messages = [
        HumanMessage(content="很早的任务书"),
        AIMessage(content="x" * 60),
        ToolMessage(content="y" * 60, tool_call_id="c1"),
        HumanMessage(content="最近的问题"),
        AIMessage(content="最近结论"),
    ]

    payloads = subagents_module.serialize_subagent_messages(messages)

    assert payloads[0] == {"type": "human", "content": subagent_history_module.SUBAGENT_HISTORY_TRUNCATED_NOTE}
    assert payloads[-1]["content"] == "最近结论"


def test_state_message_tracker_applies_full_reset_and_dedup():
    tracker = subagents_module._SubagentStreamState("call-1")
    tracker.prime_state_messages([HumanMessage(content="任务", id="u1")])
    tracker.track_state_messages({"model": {"messages": [AIMessage(content="第一版", id="a1")]}})
    tracker.track_state_messages({"model": {"messages": [AIMessage(content="修正版", id="a1")]}})
    assert [message.content for message in tracker.state_messages] == ["任务", "修正版"]

    from langchain_core.messages import RemoveMessage
    from langgraph.graph.message import REMOVE_ALL_MESSAGES

    tracker.track_state_messages(
        {
            "compact": {
                "messages": [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    SystemMessage(content="摘要"),
                    HumanMessage(content="最近问题", id="u2"),
                ]
            }
        }
    )
    assert [message.content for message in tracker.state_messages] == ["摘要", "最近问题"]


@pytest.mark.asyncio
async def test_approval_resume_rotates_owner(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)

    suspended = await harness.service.suspend_task(
        record.task_id,
        record.owner_token or "",
        status="waiting_approval",
        interrupt_payload={"kind": "approval", "approval_items": []},
    )
    assert suspended is not None
    assert suspended.status == "waiting_approval"
    assert suspended.owner_token is None
    assert suspended.meta["pending_interrupt"] == {"kind": "approval", "approval_items": []}

    resumed = await harness.service.resume_task(
        record.task_id,
        conversation_id=CONV,
        user_id=USER,
        resume_value={"kind": "approval", "answers": []},
    )
    assert resumed is not None
    resumed_record, resume_value = resumed
    assert resumed_record.status == "running"
    assert resumed_record.owner_token and resumed_record.owner_token != record.owner_token
    assert resume_value == {"kind": "approval", "answers": []}
    assert resumed_record.meta.get("pending_interrupt") is None
    # 旧父 Run owner 已失效，恢复后不能再拿它推送
    assert resumed_record.meta.get("parent_owner_token") == ""


@pytest.mark.asyncio
async def test_steer_queue_is_claimed_once_by_owner(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)

    queued = await harness.service.enqueue_steer_message(
        record.task_id,
        conversation_id=CONV,
        user_id=USER,
        message="补充：只看华东区",
        operator_name="用户一",
    )
    assert queued is not None
    assert await harness.service.claim_steer_messages(record.task_id, "wrong-owner") == []
    messages = await harness.service.claim_steer_messages(record.task_id, record.owner_token or "")
    assert messages == ["补充：只看华东区"]
    assert await harness.service.claim_steer_messages(record.task_id, record.owner_token or "") == []


@pytest.mark.asyncio
async def test_suspended_task_can_be_cancelled_without_local_runner(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)
    await harness.service.suspend_task(
        record.task_id,
        record.owner_token or "",
        status="waiting_approval",
        interrupt_payload={"kind": "approval", "approval_items": []},
    )

    cancelled = await harness.service.cancel_task(CONV, USER, record.task_id, "用户一")
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.meta.get("pending_interrupt") is None


def test_approval_resume_value_shapes_single_and_batch():
    single = subagents_module._build_approval_resume_value(
        {"kind": "approval", "approval_items": [{"call_id": "c1"}]}, "allow"
    )
    assert single == {"answers": [{"id": "approval", "selected": ["允许"]}]}

    batch = subagents_module._build_approval_resume_value(
        {"kind": "approval", "approval_items": [{"call_id": "c1"}, {"call_id": "c2"}]}, "deny"
    )
    assert batch == {
        "answers": [
            {"id": "c1", "selected": ["拒绝"]},
            {"id": "c2", "selected": ["拒绝"]},
        ]
    }


@pytest.mark.asyncio
async def test_task_push_failure_backs_off_between_heartbeats(monkeypatch):
    from apps.chat_data_app.agent.run.subagent_task_service import SubagentTaskService

    service = SubagentTaskService(dao=object(), inbox=object(), event_service=object())
    calls: list[tuple[str, str]] = []

    async def fail_append(task, run_id, owner_token):
        calls.append((run_id, owner_token))
        return "failed"

    async def active_target(task):
        return ("run-1", "token-1")

    monkeypatch.setattr(service, "_append_task_event", fail_append)
    monkeypatch.setattr(service, "_load_active_run_target", active_target)

    task = SimpleNamespace(
        task_id="task-1",
        parent_run_id="run-parent",
        meta={"parent_owner_token": "parent-token"},
    )
    await service.publish_task_update(task)
    await service.publish_task_update(task)

    # 第一次：父 Run 旧 token + 活跃 Run 各失败一次；第二次命中静默期不再重试
    assert calls == [("run-parent", "parent-token"), ("run-1", "token-1")]


@pytest.mark.asyncio
async def test_task_push_skips_stale_parent_token_after_owner_mismatch(monkeypatch):
    from apps.chat_data_app.agent.run.subagent_task_service import SubagentTaskService

    class Clock:
        def __init__(self, *values: float) -> None:
            self._values = iter(values)

        def monotonic(self) -> float:
            return next(self._values)

    service = SubagentTaskService(dao=object(), inbox=object(), event_service=object())
    calls: list[tuple[str, str]] = []

    async def lease_lost(task, run_id, owner_token):
        calls.append((run_id, owner_token))
        return "lease_lost"

    async def no_active_run(task):
        return None

    monkeypatch.setattr(service_module, "time", Clock(0.0, 31.0))
    monkeypatch.setattr(service, "_append_task_event", lease_lost)
    monkeypatch.setattr(service, "_load_active_run_target", no_active_run)

    task = SimpleNamespace(
        task_id="task-1",
        parent_run_id="run-parent",
        meta={"parent_owner_token": "parent-token"},
    )
    await service.publish_task_update(task)
    await service.publish_task_update(task)

    # 父 Run token 已失配：静默期过后也只回退查询活跃 Run，不再重试旧 token
    assert calls == [("run-parent", "parent-token")]


@pytest.mark.asyncio
async def test_task_push_failure_without_active_run_backs_off(monkeypatch):
    from apps.chat_data_app.agent.run.subagent_task_service import SubagentTaskService

    class Clock:
        def __init__(self, *values: float) -> None:
            self._values = iter(values)

        def monotonic(self) -> float:
            return next(self._values)

    service = SubagentTaskService(dao=object(), inbox=object(), event_service=object())
    calls: list[tuple[str, str]] = []

    async def fail_append(task, run_id, owner_token):
        calls.append((run_id, owner_token))
        return "failed"

    async def no_active_run(task):
        return None

    monkeypatch.setattr(service_module, "time", Clock(0.0, 1.0))
    monkeypatch.setattr(service, "_append_task_event", fail_append)
    monkeypatch.setattr(service, "_load_active_run_target", no_active_run)

    task = SimpleNamespace(
        task_id="task-1",
        parent_run_id="run-parent",
        meta={"parent_owner_token": "parent-token"},
    )
    await service.publish_task_update(task)
    await service.publish_task_update(task)

    # 没有活跃 Run 时也要进入静默期，避免每个心跳都重复失败
    assert calls == [("run-parent", "parent-token")]


@pytest.mark.asyncio
async def test_task_push_event_maps_owner_mismatch_to_lease_lost():
    from apps.chat_data_app.agent.run.subagent_task_service import SubagentTaskService

    class FailingEventService:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        async def append(self, **kwargs):
            raise self._exc

    task = SimpleNamespace(
        task_id="task-1",
        conversation_id="conv-1",
        user_id="user-1",
        as_dict=lambda **kwargs: {"task_id": "task-1"},
    )

    lease_lost_service = SubagentTaskService(
        dao=object(),
        inbox=object(),
        event_service=FailingEventService(AgentRunLeaseLostError("lost")),
    )
    assert await lease_lost_service._append_task_event(task, "run-1", "token-1") == "lease_lost"

    failed_service = SubagentTaskService(
        dao=object(),
        inbox=object(),
        event_service=FailingEventService(RuntimeError("redis down")),
    )
    assert await failed_service._append_task_event(task, "run-1", "token-1") == "failed"
