import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from langchain_core.messages import ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.agent.middleware.steering import (
    SteeringMessageTracker,
    create_steering_middleware,
)
from apps.chat_data_app.services.agent_run import agent_inbox_service as inbox_module
from apps.chat_data_app.services.agent_run.agent_inbox_service import (
    AgentInboxService,
    AgentRunConflictError,
    AgentRunNotActiveError,
)
from utils.models import Base, ChatDataAgentInbox, UserThread


@pytest.fixture()
def inbox_service(monkeypatch):
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

    monkeypatch.setattr(inbox_module, "get_session", get_test_session)

    async def create_thread(conversation_id="conv-1", user_id="user-1"):
        async with get_test_session() as session:
            session.add(UserThread(
                user_id=user_id,
                thread_id=conversation_id,
                agent_id="chat_data",
                delete_flag=0,
                create_time=datetime.now(),
                create_by=user_id,
                create_by_name=user_id,
                update_by=user_id,
                update_by_name=user_id,
            ))

    yield AgentInboxService(), create_thread, get_test_session
    asyncio.run(engine.dispose())


@pytest.mark.asyncio
async def test_agent_inbox_run_and_message_lifecycle(inbox_service):
    service, create_thread, get_session = inbox_service
    await create_thread()

    run_id = await service.start_run("conv-1", "user-1", "用户一")
    with pytest.raises(AgentRunConflictError):
        await service.start_run("conv-1", "user-1", "用户一")

    first_id = await service.enqueue_message("conv-1", "user-1", "用户一", "第一条")
    second_id = await service.enqueue_message("conv-1", "user-1", "用户一", "第二条")
    claimed = await service.claim_pending_messages(run_id)

    assert [message.content for message in claimed.messages] == ["第一条", "第二条"]
    assert [message.id for message in claimed.messages] == [first_id, second_id]

    await service.mark_consumed(run_id, [first_id, second_id])
    final_claim = await service.claim_pending_or_finish_run("conv-1", "user-1", run_id, "用户一")
    assert final_claim.messages == []

    async with get_session() as session:
        rows = list((await session.execute(
            select(ChatDataAgentInbox).order_by(ChatDataAgentInbox.id)
        )).scalars())
    assert [row.status for row in rows] == ["finished", "consumed", "consumed"]


@pytest.mark.asyncio
async def test_agent_inbox_final_check_claims_message_before_finishing(inbox_service):
    service, create_thread, get_session = inbox_service
    await create_thread()
    run_id = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "结束边界消息")

    claimed = await service.claim_pending_or_finish_run("conv-1", "user-1", run_id, "用户一")

    assert [message.id for message in claimed.messages] == [message_id]
    async with get_session() as session:
        run = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == run_id,
            ChatDataAgentInbox.record_type == "run",
        ))).scalar_one()
    assert run.status == "active"


@pytest.mark.asyncio
async def test_agent_inbox_reclaims_expired_message_lease(inbox_service):
    service, create_thread, get_session = inbox_service
    await create_thread()
    run_id = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "重试消息")
    await service.claim_pending_messages(run_id)

    async with get_session() as session:
        row = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.message_id == message_id,
        ))).scalar_one()
        row.lease_expire_time = datetime.now() - timedelta(seconds=1)

    reclaimed = await service.claim_pending_messages(run_id)
    assert [message.id for message in reclaimed.messages] == [message_id]


@pytest.mark.asyncio
async def test_failed_run_requeues_claimed_messages_for_next_run(inbox_service):
    service, create_thread, get_session = inbox_service
    await create_thread()
    run_id = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "失败后重试")
    await service.claim_pending_messages(run_id)
    await service.finish_run(run_id, "failed", "user-1", "用户一")

    next_run_id = await service.start_run("conv-1", "user-1", "用户一")
    claimed = await service.claim_pending_messages(next_run_id)
    assert [message.id for message in claimed.messages] == [message_id]


@pytest.mark.asyncio
async def test_expired_run_requeues_claimed_messages_when_new_run_starts(inbox_service):
    service, create_thread, get_session = inbox_service
    await create_thread()
    run_id = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "租约回收")
    await service.claim_pending_messages(run_id)
    async with get_session() as session:
        run = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == run_id,
            ChatDataAgentInbox.record_type == "run",
        ))).scalar_one()
        run.lease_expire_time = datetime.now() - timedelta(seconds=1)

    next_run_id = await service.start_run("conv-1", "user-1", "用户一")
    claimed = await service.claim_pending_messages(next_run_id)
    assert [message.id for message in claimed.messages] == [message_id]


@pytest.mark.asyncio
async def test_agent_inbox_rejects_missing_or_unowned_conversation(inbox_service):
    service, create_thread, _get_session = inbox_service
    await create_thread()

    with pytest.raises(AgentRunNotActiveError):
        await service.start_run("missing", "user-1", "用户一")
    with pytest.raises(AgentRunNotActiveError):
        await service.enqueue_message("conv-1", "other-user", "其他用户", "越权消息")


@pytest.mark.asyncio
async def test_steering_middleware_injects_messages_and_tracks_ids(monkeypatch):
    class FakeInboxService:
        async def claim_pending_messages(self, run_id):
            assert run_id == "run-1"
            return inbox_module.ClaimedAgentMessages(
                run_id,
                [inbox_module.HumanMessage(content="追加要求", id="message-1")],
            )

    tracker = SteeringMessageTracker()
    stream_events = []
    monkeypatch.setattr(
        "apps.chat_data_app.agent.middleware.steering.get_stream_writer",
        lambda: stream_events.append,
    )
    middleware = create_steering_middleware(
        run_id="run-1",
        inbox_service=FakeInboxService(),
        tracker=tracker,
    )

    update = await middleware.abefore_model({
        "messages": [inbox_module.HumanMessage(content="原问题"), ToolMessage(content="工具结果", tool_call_id="call-1")],
    }, None)

    assert [message.content for message in update["messages"]] == ["追加要求"]
    assert tracker.drain() == ["message-1"]
    assert stream_events == [{"type": "steer_consumed", "message_ids": ["message-1"]}]


@pytest.mark.asyncio
async def test_steering_middleware_does_not_consume_before_plain_model_call():
    claim_calls = 0

    class FakeInboxService:
        async def claim_pending_messages(self, _run_id):
            nonlocal claim_calls
            claim_calls += 1
            return inbox_module.ClaimedAgentMessages("run-1", [])

    middleware = create_steering_middleware(
        run_id="run-1",
        inbox_service=FakeInboxService(),
        tracker=SteeringMessageTracker(),
    )

    update = await middleware.abefore_model({
        "messages": [inbox_module.HumanMessage(content="普通对话")],
    }, None)

    assert update is None
    assert claim_calls == 0
