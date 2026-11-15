"""子智能体生命周期事件 outbox 投递测试。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.agent.run import subagent_event_service as event_service_module
from apps.chat_data_app.agent.run import subagent_task_service as service_module
from apps.chat_data_app.agent.run.agent_inbox_service import agent_inbox_service
from apps.chat_data_app.agent.run.subagent_event_service import (
    EVENT_PAUSED,
    EVENT_RESUMED,
    EVENT_TERMINAL,
    EVENT_USER_MESSAGE,
    EVENT_WAITING_APPROVAL,
    terminal_event_id,
)
from apps.chat_data_app.agent.run.subagent_task_service import subagent_task_service
from utils.models import Base, ChatDataAgentInbox, ChatDataSubagentEvent, UserThread

CONV = "conv-event-test"
USER = "u1"


class _FakeRunEventService:
    async def append(self, **_kwargs):
        return None


async def _settings(_key: str) -> int:
    return 60


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
    monkeypatch.setattr(event_service_module, "get_session", get_test_session)
    monkeypatch.setattr(service_module, "get_runtime_setting", _settings)
    monkeypatch.setattr(subagent_task_service, "_event_service", _FakeRunEventService())
    yield SimpleNamespace(engine=engine, get_session=get_test_session, service=subagent_task_service)
    asyncio.run(engine.dispose())


async def _create_conversation(harness) -> None:
    async with harness.get_session() as session:
        session.add(
            UserThread(
                user_id=USER,
                thread_id=CONV,
                agent_id="chat_data",
                delete_flag=0,
                create_time=datetime.now(),
                create_by=USER,
                create_by_name=USER,
                update_by=USER,
                update_by_name=USER,
            )
        )


async def _create_task(harness):
    return await harness.service.create_task(
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        subagent_type="explore",
        description="盘点近 30 天入金趋势",
        label="入金趋势",
        parent_tool_call_id="call-parent",
        parent_owner_token="parent-owner",
        timeout_seconds=60,
        operator_name="用户一",
    )


async def _start(harness, record):
    started = await harness.service.start_task(record.task_id, record.owner_token or "")
    assert started is not None
    return started


async def _event(harness, task_id: str, event_type: str) -> ChatDataSubagentEvent:
    async with harness.get_session() as session:
        row = (
            await session.execute(
                select(ChatDataSubagentEvent).where(
                    ChatDataSubagentEvent.task_id == task_id,
                    ChatDataSubagentEvent.event_type == event_type,
                )
            )
        ).scalar_one()
        return row


@pytest.mark.asyncio
async def test_terminal_event_wakes_idle_conversation_and_marks_task_consumed(harness, monkeypatch):
    await _create_conversation(harness)
    record = await _create_task(harness)
    await _start(harness, record)
    calls: list[dict] = []

    async def start_run(conversation_id, user_id, operator_name, runtime_context, **kwargs):
        calls.append(
            {"conversation_id": conversation_id, "user_id": user_id, "context": runtime_context, "kwargs": kwargs}
        )
        return "run-notification"

    monkeypatch.setattr(agent_inbox_service, "start_run", start_run)
    await harness.service.finish_task(
        record.task_id,
        record.owner_token or "",
        status="completed",
        report="完整盘点结论",
    )
    event = await _event(harness, record.task_id, EVENT_TERMINAL)
    message_id = event_service_module.event_message_id(event.event_id)

    assert event.delivery_status == "pending"
    assert event.event_id == terminal_event_id(record.task_id)
    assert len(message_id) <= 64
    assert await harness.service.dispatch_pending_notifications(limit=10) == 1
    assert calls[0]["kwargs"]["selected_message_id"] == message_id
    assert calls[0]["context"]["subagent_notification_event_type"] == EVENT_TERMINAL
    assert calls[0]["context"]["subagent_notification_status"] == "completed"

    delivered = await _event(harness, record.task_id, EVENT_TERMINAL)
    assert delivered.delivery_status == "delivered"
    assert delivered.delivered_time is not None
    async with harness.get_session() as session:
        message = (
            await session.execute(select(ChatDataAgentInbox).where(ChatDataAgentInbox.message_id == message_id))
        ).scalar_one()
    assert message.record_status == "pending"
    assert "完整盘点结论" in message.record_content
    notified = await harness.service.get_task(record.task_id)
    assert notified is not None and notified.notified_at is not None


@pytest.mark.asyncio
async def test_resume_cancels_stale_approval_message_and_emits_resumed(harness, monkeypatch):
    await _create_conversation(harness)
    record = await _create_task(harness)
    await _start(harness, record)

    async def start_run(*_args, **_kwargs):
        return "run-notification"

    monkeypatch.setattr(agent_inbox_service, "start_run", start_run)
    await harness.service.suspend_task(
        record.task_id,
        record.owner_token or "",
        status="waiting_approval",
        interrupt_payload={"kind": "approval", "approval_items": []},
    )
    approval = await _event(harness, record.task_id, EVENT_WAITING_APPROVAL)
    approval_message_id = event_service_module.event_message_id(approval.event_id)

    resumed = await harness.service.resume_task(
        record.task_id,
        conversation_id=CONV,
        user_id=USER,
        resume_value={"kind": "approval", "answers": []},
    )
    resumed_event = await _event(harness, record.task_id, EVENT_RESUMED)
    async with harness.get_session() as session:
        approval_message = (
            await session.execute(
                select(ChatDataAgentInbox).where(ChatDataAgentInbox.message_id == approval_message_id)
            )
        ).scalar_one()

    assert resumed is not None and resumed[0].status == "running"
    assert approval_message.record_status == "cancelled"
    assert approval_message.delete_flag == 1
    assert resumed_event.delivery_status == "delivered"


@pytest.mark.asyncio
async def test_mark_viewed_cancels_undelivered_terminal_event(harness):
    await _create_conversation(harness)
    record = await _create_task(harness)
    await _start(harness, record)
    finished = await harness.service.finish_task(
        record.task_id,
        record.owner_token or "",
        status="completed",
        report="模型已直接读取的结论",
    )

    assert finished is not None
    assert (await _event(harness, record.task_id, EVENT_TERMINAL)).delivery_status == "pending"
    await harness.service.mark_viewed(finished)
    assert (await _event(harness, record.task_id, EVENT_TERMINAL)).delivery_status == "cancelled"
    assert await harness.service.dispatch_pending_notifications(limit=10) == 0


@pytest.mark.asyncio
async def test_user_message_event_is_attached_without_waking_idle_session(harness, monkeypatch):
    await _create_conversation(harness)
    record = await _create_task(harness)
    await _start(harness, record)
    calls: list[dict] = []

    async def start_run(*_args, **kwargs):
        calls.append(kwargs)
        return "unexpected-run"

    monkeypatch.setattr(agent_inbox_service, "start_run", start_run)
    event_id = await harness.service.notify_user_message(record, "帮我补充华东区渠道")
    event = await _event(harness, record.task_id, EVENT_USER_MESSAGE)

    assert event_id == event.event_id
    assert event.delivery_status == "delivered"
    assert calls == []
    assert "帮我补充华东区渠道" in event.event_content


@pytest.mark.asyncio
async def test_paused_lifecycle_event_is_audited_even_without_active_run(harness):
    await _create_conversation(harness)
    record = await _create_task(harness)
    await _start(harness, record)
    await harness.service.suspend_task(
        record.task_id,
        record.owner_token or "",
        status="paused",
        interrupt_payload={"kind": "subagent_pause"},
    )

    event = await _event(harness, record.task_id, EVENT_PAUSED)
    assert event.wake_policy == "attach"
    assert event.delivery_status == "delivered"
