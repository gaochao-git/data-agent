import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from langchain_core.messages import ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.agent.middleware.steering import SteeringMessageTracker, create_steering_middleware
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

    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    with pytest.raises(AgentRunConflictError):
        await service.start_run("conv-1", "user-1", "用户一")

    first_id = await service.enqueue_message("conv-1", "user-1", "用户一", "第一条", queue_type="next-step")
    second_id = await service.enqueue_message("conv-1", "user-1", "用户一", "第二条", queue_type="next-step")
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
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
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
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "重试消息", queue_type="next-step")
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
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "失败后重试", queue_type="next-step")
    await service.claim_pending_messages(run_id)
    await service.finish_run(run_id, "failed", "user-1", "用户一")

    next_run_id, adopted = await service.start_run("conv-1", "user-1", "用户一")
    assert [message.id for message in adopted] == [message_id]
    claimed = await service.claim_pending_messages(next_run_id)
    assert claimed.messages == []


@pytest.mark.asyncio
async def test_expired_run_requeues_claimed_messages_when_new_run_starts(inbox_service):
    """模拟 worker 崩溃未收口：run lease 过期后，新 worker 接管同一消息。"""
    service, create_thread, get_session = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "租约回收", queue_type="next-step")
    await service.claim_pending_messages(run_id)
    async with get_session() as session:
        run = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == run_id,
            ChatDataAgentInbox.record_type == "run",
        ))).scalar_one()
        run.lease_expire_time = datetime.now() - timedelta(seconds=1)

    next_run_id, adopted = await service.start_run("conv-1", "user-1", "用户一")
    assert [message.id for message in adopted] == [message_id]
    claimed = await service.claim_pending_messages(next_run_id)
    assert claimed.messages == []


@pytest.mark.asyncio
async def test_agent_inbox_rejects_missing_or_unowned_conversation(inbox_service):
    service, create_thread, _get_session = inbox_service
    await create_thread()

    with pytest.raises(AgentRunNotActiveError):
        await service.start_run("missing", "user-1", "用户一")
    with pytest.raises(AgentRunNotActiveError):
        await service.enqueue_message("conv-1", "other-user", "其他用户", "越权消息")


@pytest.mark.asyncio
async def test_steering_middleware_persists_and_admits_next_step(monkeypatch):
    call_log = {}

    class FakeInboxService:
        async def claim_pending_messages(self, run_id, *, queue_type=None):
            call_log["claim"] = (run_id, queue_type)
            return inbox_module.ClaimedAgentMessages(
                run_id,
                [inbox_module.HumanMessage(content="追加要求", id="message-1")],
            )

    async def admit(messages):
        call_log["admitted"] = [str(message.id) for message in messages]

    stream_events = []
    monkeypatch.setattr(
        "apps.chat_data_app.agent.middleware.steering.get_stream_writer",
        lambda: stream_events.append,
    )
    middleware = create_steering_middleware(
        run_id="run-1",
        inbox_service=FakeInboxService(),
        admit_messages=admit,
        tracker=SteeringMessageTracker(),
    )

    update = await middleware.abefore_model({
        "messages": [inbox_module.HumanMessage(content="原问题"), ToolMessage(content="工具结果", tool_call_id="call-1")],
    }, None)

    assert [message.content for message in update["messages"]] == ["追加要求"]
    assert stream_events == [{"type": "inbox_admitted", "message_ids": ["message-1"]}]
    assert call_log == {
        "claim": ("run-1", "next-step"),
        "admitted": ["message-1"],
    }


@pytest.mark.asyncio
async def test_steering_middleware_injects_before_plain_model_call(monkeypatch):
    """放宽注入条件后，纯对话（HumanMessage 结尾）也会在模型调用前领取并注入。"""
    claim_calls = 0

    class FakeInboxService:
        async def claim_pending_messages(self, run_id, *, queue_type=None):
            nonlocal claim_calls
            claim_calls += 1
            assert run_id == "run-1"
            return inbox_module.ClaimedAgentMessages(
                run_id,
                [inbox_module.HumanMessage(content="追加要求", id="message-2")],
            )

    async def admit(_messages):
        return None

    stream_events = []
    monkeypatch.setattr(
        "apps.chat_data_app.agent.middleware.steering.get_stream_writer",
        lambda: stream_events.append,
    )
    middleware = create_steering_middleware(
        run_id="run-1",
        inbox_service=FakeInboxService(),
        admit_messages=admit,
        tracker=SteeringMessageTracker(),
    )

    update = await middleware.abefore_model({
        "messages": [inbox_module.HumanMessage(content="普通对话")],
    }, None)

    assert [message.content for message in update["messages"]] == ["追加要求"]
    assert stream_events == [{"type": "inbox_admitted", "message_ids": ["message-2"]}]
    assert claim_calls == 1


@pytest.mark.asyncio
async def test_steering_middleware_does_not_claim_without_messages():
    claim_calls = 0

    class FakeInboxService:
        async def claim_pending_messages(self, _run_id, *, queue_type=None):
            nonlocal claim_calls
            claim_calls += 1
            return inbox_module.ClaimedAgentMessages("run-1", [])

    middleware = create_steering_middleware(
        run_id="run-1",
        inbox_service=FakeInboxService(),
        admit_messages=lambda _messages: asyncio.sleep(0),
        tracker=SteeringMessageTracker(),
    )

    update = await middleware.abefore_model({"messages": []}, None)

    assert update is None
    assert claim_calls == 0


@pytest.mark.asyncio
async def test_steering_middleware_does_not_ack_when_admission_persistence_fails(monkeypatch):
    class FakeInboxService:
        async def claim_pending_messages(self, _run_id, *, queue_type=None):
            assert queue_type == "next-step"
            return inbox_module.ClaimedAgentMessages(
                "run-1",
                [inbox_module.HumanMessage(content="崩溃边界消息", id="message-crash")],
            )

    async def fail_admission(_messages):
        raise RuntimeError("event store unavailable")

    stream_events = []
    tracker = SteeringMessageTracker()
    monkeypatch.setattr(
        "apps.chat_data_app.agent.middleware.steering.get_stream_writer",
        lambda: stream_events.append,
    )
    middleware = create_steering_middleware(
        run_id="run-1",
        inbox_service=FakeInboxService(),
        admit_messages=fail_admission,
        tracker=tracker,
    )

    with pytest.raises(RuntimeError, match="event store unavailable"):
        await middleware.abefore_model({"messages": [inbox_module.HumanMessage(content="原问题")]}, None)

    assert stream_events == []
    assert tracker.drain() == []


@pytest.mark.asyncio
async def test_agent_inbox_queue_type_separates_next_step_from_next_turn(inbox_service):
    service, create_thread, _get_session = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")

    steer_id = await service.enqueue_message("conv-1", "user-1", "用户一", "立即插入", queue_type="next-step")
    next_id = await service.enqueue_message("conv-1", "user-1", "用户一", "下轮再说", queue_type="next-turn")

    steer_claimed = await service.claim_pending_messages(run_id, queue_type="next-step")
    assert [message.id for message in steer_claimed.messages] == [steer_id]

    turn_claimed = await service.claim_pending_or_finish_run("conv-1", "user-1", run_id, "用户一")
    assert [message.id for message in turn_claimed.messages] == [next_id]


@pytest.mark.asyncio
async def test_agent_inbox_enqueue_rejects_unknown_queue_type(inbox_service):
    service, create_thread, _get_session = inbox_service
    await create_thread()
    await service.start_run("conv-1", "user-1", "用户一")

    with pytest.raises(ValueError):
        await service.enqueue_message("conv-1", "user-1", "用户一", "非法类型", queue_type="bad")


@pytest.mark.asyncio
async def test_agent_inbox_update_pending_message_content_and_type(inbox_service):
    service, create_thread, get_session = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "原始内容")

    updated = await service.update_pending_message(
        "conv-1", "user-1", "用户一", message_id,
        content="修改后内容", queue_type="next-turn",
    )

    assert updated == {"message_id": message_id, "content": "修改后内容", "queue_type": "next-turn"}
    async with get_session() as session:
        row = (await session.execute(
            select(ChatDataAgentInbox).where(ChatDataAgentInbox.run_id == run_id, ChatDataAgentInbox.record_type == "message")
        )).scalar_one()
    assert row.content == "修改后内容"
    assert row.queue_type == "next-turn"


@pytest.mark.asyncio
async def test_agent_inbox_update_rejects_missing_and_claimed_messages(inbox_service):
    service, create_thread, _get_session = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "将被领取", queue_type="next-step")

    with pytest.raises(inbox_module.SteerMessageNotFoundError):
        await service.update_pending_message("conv-1", "user-1", "用户一", "missing-id", content="x")

    await service.claim_pending_messages(run_id, queue_type="next-step")
    with pytest.raises(inbox_module.SteerMessageNotPendingError):
        await service.update_pending_message("conv-1", "user-1", "用户一", message_id, content="x")
    with pytest.raises(inbox_module.SteerMessageNotPendingError):
        await service.cancel_pending_message("conv-1", "user-1", "用户一", message_id)


@pytest.mark.asyncio
async def test_agent_inbox_cancel_pending_message(inbox_service):
    service, create_thread, get_session = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "排队中")

    await service.cancel_pending_message("conv-1", "user-1", "用户一", message_id)

    async with get_session() as session:
        row = (await session.execute(
            select(ChatDataAgentInbox).where(ChatDataAgentInbox.run_id == run_id, ChatDataAgentInbox.record_type == "message")
        )).scalar_one()
    assert row.delete_flag == 1

    claimed = await service.claim_pending_messages(run_id, queue_type="next-step")
    assert claimed.messages == []


@pytest.mark.asyncio
async def test_agent_inbox_update_requires_active_run_and_ownership(inbox_service):
    service, create_thread, _get_session = inbox_service
    await create_thread()

    with pytest.raises(AgentRunNotActiveError):
        await service.update_pending_message("conv-1", "user-1", "用户一", "any-id", content="x")
    with pytest.raises(AgentRunNotActiveError):
        await service.cancel_pending_message("conv-1", "user-1", "用户一", "any-id")

    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "越权消息")

    with pytest.raises(AgentRunNotActiveError):
        await service.update_pending_message("conv-1", "other-user", "其他用户", message_id, content="x")


@pytest.mark.asyncio
async def test_agent_inbox_turn_end_claims_all_next_step_and_one_next_turn(inbox_service):
    service, create_thread, _get_session = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    next_id = await service.enqueue_message("conv-1", "user-1", "用户一", "下一轮任务")
    next_id_2 = await service.enqueue_message("conv-1", "user-1", "用户一", "再下一轮任务")
    steer_id = await service.enqueue_message("conv-1", "user-1", "用户一", "来不及插入的立即任务", queue_type="next-step")

    claimed = await service.claim_pending_or_finish_run("conv-1", "user-1", run_id, "用户一")

    assert [message.id for message in claimed.messages] == [steer_id, next_id]
    await service.mark_consumed(run_id, [steer_id, next_id])
    next_claimed = await service.claim_pending_or_finish_run("conv-1", "user-1", run_id, "用户一")
    assert [message.id for message in next_claimed.messages] == [next_id_2]


@pytest.mark.asyncio
async def test_agent_inbox_lists_claimed_message_for_refresh_restore(inbox_service):
    service, create_thread, _get_session = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    await service.enqueue_message("conv-1", "user-1", "用户一", "排队一")
    claimed_id = await service.enqueue_message("conv-1", "user-1", "用户一", "排队二", queue_type="next-step")

    queue = await service.list_message_states("conv-1", "user-1")

    assert queue["has_active_run"] is True
    assert [item["content"] for item in queue["messages"]] == ["排队一", "排队二"]
    assert [item["queue_type"] for item in queue["messages"]] == ["next-turn", "next-step"]
    assert all(item["message_id"] for item in queue["messages"])

    await service.claim_pending_messages(run_id, queue_type="next-step")
    queue = await service.list_message_states("conv-1", "user-1")
    state = next(item for item in queue["messages"] if item["message_id"] == claimed_id)
    assert state["status"] == "claimed"


@pytest.mark.asyncio
async def test_agent_inbox_list_message_states_rejects_unowned(inbox_service):
    service, create_thread, _get_session = inbox_service
    await create_thread()

    with pytest.raises(AgentRunNotActiveError):
        await service.list_message_states("conv-1", "other-user")
