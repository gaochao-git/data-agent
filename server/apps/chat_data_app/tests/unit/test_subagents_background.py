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
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.agent.middleware import subagents as subagents_module
from apps.chat_data_app.agent.middleware.subagent_async import _format_task_report
from apps.chat_data_app.agent.middleware.subagents import build_task_tool
from apps.chat_data_app.agent.run import subagent_task_service as service_module
from apps.chat_data_app.agent.run.agent_inbox_service import AgentRunConflictError, agent_inbox_service
from apps.chat_data_app.agent.run.subagent_task_service import (
    SubagentTaskCapacityError,
    notification_message_id,
    subagent_task_service,
)
from utils.models import Base, ChatDataAgentInbox, UserThread

CONV = "conv-bg-test"
USER = "u1"


class _FakeEventService:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def append(self, **kwargs):
        self.events.append(kwargs)


def _settings(max_active: int = 20):
    values = {
        "CHAT_DATA_SUBAGENT_TASK_MAX_ACTIVE": max_active,
        "CHAT_DATA_SUBAGENT_TASK_LEASE_SECONDS": 30,
        "CHAT_DATA_SUBAGENT_TASK_TIMEOUT_SECONDS": 60,
    }

    async def _get(key: str) -> int:
        return values.get(key, 60)

    return _get


@pytest.fixture()
def harness(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_test_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(service_module, "get_session", get_test_session)
    monkeypatch.setattr(service_module, "get_runtime_setting", _settings())
    fake_events = _FakeEventService()
    monkeypatch.setattr(subagent_task_service, "_event_service", fake_events)

    async def conflict_start_run(*_args, **_kwargs):
        raise AgentRunConflictError("会话占用")

    monkeypatch.setattr(agent_inbox_service, "start_run", conflict_start_run)

    async def create_conversation(conversation_id: str = CONV, user_id: str = USER) -> None:
        async with get_test_session() as session:
            session.add(
                UserThread(
                    user_id=user_id,
                    thread_id=conversation_id,
                    agent_id="chat_data",
                    delete_flag=0,
                    create_time=datetime.now(),
                    create_by=user_id,
                    create_by_name=user_id,
                    update_by=user_id,
                    update_by_name=user_id,
                )
            )

    yield SimpleNamespace(
        engine=engine,
        get_session=get_test_session,
        events=fake_events,
        create_conversation=create_conversation,
        service=subagent_task_service,
    )
    for task in list(subagent_task_service._local_tasks.values()):
        task.cancel()
    subagent_task_service._local_tasks.clear()
    asyncio.run(engine.dispose())


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
        timeout_seconds=60,
        operator_name="用户一",
    )


async def _start(harness, record):
    started = await harness.service.start_task(record.task_id, record.owner_token or "")
    assert started is not None
    return started


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
    payload = record.as_dict(include_report=False)
    assert payload["task_id"] == record.task_id
    assert "report" not in payload
    assert "result_meta" not in payload

    assert len(harness.events.events) == 1
    frame = harness.events.events[0]["payload"]
    assert frame.startswith("event: subagent_task_updated\n")
    assert record.task_id in frame
    assert '"report"' not in frame


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
async def test_cancel_pending_and_running_tasks(harness):
    await harness.create_conversation()
    pending = await _create_task(harness)

    cancelled = await harness.service.cancel_task(CONV, USER, pending.task_id, "用户一")
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.report == "任务在开始执行前已取消。"
    assert cancelled.owner_token is None

    running = await _create_task(harness)
    await _start(harness, running)
    requested = await harness.service.cancel_task(CONV, USER, running.task_id, "用户一")
    assert requested is not None
    assert requested.status == "cancelling"

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
    # 主动取消不再制造「任务已取消」自动通知 Run。
    assert await harness.service.dispatch_pending_notifications(limit=10) == 0


@pytest.mark.asyncio
async def test_task_tool_dispatches_management_actions(harness, monkeypatch):
    await harness.create_conversation()
    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: object())
    record = await _create_task(harness)
    await _start(harness, record)
    await harness.service.finish_task(
        record.task_id,
        record.owner_token or "",
        status="completed",
        report="完整盘点结论",
    )
    tool = build_task_tool([_spec()])
    runtime = _fake_runtime()

    listed = await tool.coroutine(action="list", runtime=runtime)
    assert record.task_id in listed

    checked = await tool.coroutine(action="check", runtime=runtime, task_id=record.task_id)
    assert "完整盘点结论" in checked

    waited = await tool.coroutine(action="wait", runtime=runtime, task_ids=[record.task_id])
    assert "完整盘点结论" in waited

    cancelled = await tool.coroutine(action="cancel", runtime=runtime, task_id=record.task_id)
    assert "无需取消" in cancelled

    assert "task_id" in await tool.coroutine(action="check", runtime=runtime)
    assert "task_ids" in await tool.coroutine(action="wait", runtime=runtime)


@pytest.mark.asyncio
async def test_capacity_guard_serializes_active_tasks(harness, monkeypatch):
    monkeypatch.setattr(service_module, "get_runtime_setting", _settings(max_active=1))
    await harness.create_conversation()
    await _create_task(harness)

    with pytest.raises(SubagentTaskCapacityError, match="已达上限"):
        await _create_task(harness)


@pytest.mark.asyncio
async def test_reap_expired_running_task_marks_lost(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)

    async with harness.get_session() as session:
        row = (
            await session.execute(select(ChatDataAgentInbox).where(ChatDataAgentInbox.message_id == record.task_id))
        ).scalar_one()
        row.lease_expire_time = datetime.now() - timedelta(seconds=1)

    assert await harness.service.reap_expired_tasks() == 1
    reaped = await harness.service.get_task(record.task_id)
    assert reaped is not None
    assert reaped.status == "lost"
    assert reaped.error == "执行节点租约过期"
    assert "可重新派发" in reaped.report
    assert reaped.owner_token is None


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
        lambda spec, run_controls=None: _runnable("盘点结论：近 30 天平稳"),
    )
    calls: list[dict] = []

    async def start_run(conversation_id, user_id, operator_name, runtime_context, **kwargs):
        calls.append(kwargs)
        return "run-notification"

    monkeypatch.setattr(agent_inbox_service, "start_run", start_run)

    tool = build_task_tool([_spec()])
    result = await tool.coroutine(
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

    def fake_compile(spec, run_controls=None):
        compiled_with.append(run_controls)
        return _runnable("盘点结论")

    monkeypatch.setattr(subagents_module, "_compile_subagent", fake_compile)
    tool = build_task_tool([_spec()], run_controls=parent_controls)
    result = await tool.coroutine(
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
    tool = build_task_tool([_spec()])
    result = await tool.coroutine(
        description="x",
        subagent_type="不存在",
        runtime=_fake_runtime(),
        background=True,
    )
    assert "类型不存在" in result
    records = await harness.service.list_tasks(CONV, USER)
    assert records == []


@pytest.mark.asyncio
async def test_background_timeout_keeps_partial_report(harness, monkeypatch):
    await harness.create_conversation()
    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None: _runnable("中断前的中间结论", delay_after_yield=5),
    )
    tool = build_task_tool([_spec()])
    result = await tool.coroutine(
        description="很慢的任务",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_timeout"),
        background=True,
        timeout_seconds=1,
    )
    task_id = _task_id_from_text(result)
    local_task = harness.service._local_tasks.get(task_id)
    assert local_task is not None
    await local_task
    record = await harness.service.get_task(task_id)
    assert record is not None
    assert record.status == "timeout"
    assert "执行超时" in record.report
    assert "中断前的中间结论" in record.report
    assert record.result_meta.get("tool_status") == "error"
    assert record.result_meta.get("subagent_trail")


@pytest.mark.asyncio
async def test_sync_task_path_still_returns_tool_message(monkeypatch):
    monkeypatch.setattr(
        subagents_module,
        "_compile_subagent",
        lambda spec, run_controls=None: _runnable("同步结论"),
    )
    tool = build_task_tool([_spec()])
    result = await tool.coroutine(
        description="同步任务",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_sync"),
    )
    assert not isinstance(result, str)
    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.content == "同步结论"


def test_system_prompt_documents_background_and_task_tools():
    middleware = subagents_module.SubagentMiddleware([_spec()])
    assert "background=true" in middleware.system_prompt
    assert 'task(action="list")' in middleware.system_prompt
    assert 'task(action="check"' in middleware.system_prompt
    assert 'task(action="wait"' in middleware.system_prompt
    assert 'task(action="cancel"' in middleware.system_prompt
    assert "async_task" not in middleware.system_prompt
    assert "list_async_tasks" not in middleware.system_prompt
    assert "check_async_task" not in middleware.system_prompt
