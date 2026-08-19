import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from langchain_core.messages import ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.agent.middleware.steering import SteeringMessageTracker, create_steering_middleware
from apps.chat_data_app.agent.middleware.tool_execution_fence import create_tool_execution_fence_middleware
from apps.chat_data_app.services.agent_run import agent_inbox_service as inbox_module
from apps.chat_data_app.services.agent_run.agent_inbox_service import (
    AgentInboxService,
    AgentQuestionNotWaitingError,
    AgentRunConflictError,
    AgentRunLeaseLostError,
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

    service = AgentInboxService()
    owner_tokens = {}
    original_start_run = service.start_run

    async def start_and_prepare(*args, **kwargs):
        result = await original_start_run(*args, **kwargs)
        prepared = await service.prepare_pending_run(result[0])
        owner_tokens[result[0]] = prepared["owner_token"]
        return result

    # These tests exercise the inbox lifecycle after a worker has claimed a
    # run. Runtime-specific pending behavior is covered separately.
    service.start_run = start_and_prepare
    yield service, create_thread, get_test_session, owner_tokens
    asyncio.run(engine.dispose())


@pytest.mark.asyncio
async def test_agent_inbox_run_and_message_lifecycle(inbox_service):
    service, create_thread, get_session, owners = inbox_service
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
    await service.complete_run(run_id, "user-1", "用户一", owners[run_id])

    async with get_session() as session:
        rows = list((await session.execute(
            select(ChatDataAgentInbox).order_by(ChatDataAgentInbox.id)
        )).scalars())
    assert [row.status for row in rows] == ["finished", "consumed", "consumed"]


@pytest.mark.asyncio
async def test_agent_question_wait_and_answer_is_atomic(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    runtime_context = {"query": "查询订单", "model_id": "model-a"}
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一", runtime_context)
    question = {
        "question_id": "question-1",
        "turn_id": "turn-1",
        "questions": [{
            "id": "scope",
            "question": "选择范围",
            "options": [{"label": "全部"}, {"label": "本月"}],
            "multi_select": False,
        }],
    }
    await service.mark_waiting_for_user(run_id, question, "user-1", "用户一", owners[run_id])
    assert await service.get_pending_question("conv-1", "user-1") == {"run_id": run_id, **question}

    answer = {"answers": [{"id": "scope", "selected": ["本月"], "custom": ""}]}
    claimed_run_id, restored_context = await service.claim_question_answer(
        "conv-1", "user-1", "用户一", "question-1", answer,
    )
    assert claimed_run_id == run_id
    assert restored_context == runtime_context
    assert await service.get_pending_question("conv-1", "user-1") is None
    with pytest.raises(AgentQuestionNotWaitingError):
        await service.claim_question_answer("conv-1", "user-1", "用户一", "question-1", answer)

    async with get_session() as session:
        run = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == run_id,
            ChatDataAgentInbox.record_type == "run",
        ))).scalar_one()
    assert run.status == "pending"
    assert run.lease_expire_time is None

    prepared = await service.prepare_pending_run(run_id)
    owners[run_id] = prepared["owner_token"]
    await service.finish_run(run_id, "failed", "user-1", "用户一", owners[run_id])
    assert await service.get_pending_question("conv-1", "user-1") == {"run_id": run_id, **question}

    await service.claim_question_answer("conv-1", "user-1", "用户一", "question-1", answer)
    prepared = await service.prepare_pending_run(run_id)
    owners[run_id] = prepared["owner_token"]
    await service.mark_question_resume_started(run_id, owners[run_id])
    await service.finish_run(run_id, "failed", "user-1", "用户一", owners[run_id])
    assert await service.get_pending_question("conv-1", "user-1") is None

    async with get_session() as session:
        run = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == run_id,
            ChatDataAgentInbox.record_type == "run",
        ))).scalar_one()
    assert run.status == "failed"


@pytest.mark.asyncio
async def test_agent_question_claim_can_be_restored_before_resume_starts(inbox_service):
    service, create_thread, _get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    question = {
        "question_id": "question-1",
        "questions": [{"id": "scope", "question": "选择范围"}],
    }
    await service.mark_waiting_for_user(run_id, question, "user-1", "用户一", owners[run_id])
    answer = {"answers": [{"id": "scope", "selected": [], "custom": "本月"}]}
    await service.claim_question_answer("conv-1", "user-1", "用户一", "question-1", answer)

    prepared = await service.prepare_pending_run(run_id)
    owners[run_id] = prepared["owner_token"]
    await service.restore_question_answer(run_id, "user-1", "用户一", owners[run_id])

    assert await service.get_pending_question("conv-1", "user-1") == {"run_id": run_id, **question}


@pytest.mark.asyncio
async def test_expired_unstarted_question_resume_returns_to_waiting(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    question = {
        "question_id": "question-1",
        "questions": [{"id": "scope", "question": "选择范围"}],
    }
    await service.mark_waiting_for_user(run_id, question, "user-1", "用户一", owners[run_id])
    answer = {"answers": [{"id": "scope", "selected": [], "custom": "本月"}]}
    await service.claim_question_answer("conv-1", "user-1", "用户一", "question-1", answer)

    async with get_session() as session:
        run = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == run_id,
            ChatDataAgentInbox.record_type == "run",
        ))).scalar_one()
        run.lease_expire_time = datetime.now() - timedelta(seconds=1)

    # Answer submission is durable as a pending resume. An expired pending
    # resume must be picked up by Runtime, not exposed as a new question.
    assert await service.get_pending_question("conv-1", "user-1") is None


@pytest.mark.asyncio
async def test_agent_question_validates_options_and_supports_cancel(inbox_service):
    service, create_thread, _get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    question = {
        "question_id": "question-1",
        "questions": [{
            "id": "scope",
            "question": "选择范围",
            "options": [{"label": "全部"}],
            "multi_select": False,
        }],
    }
    await service.mark_waiting_for_user(run_id, question, "user-1", "用户一", owners[run_id])
    with pytest.raises(ValueError, match="无效选项"):
        await service.claim_question_answer(
            "conv-1", "user-1", "用户一", "question-1",
            {"answers": [{"id": "scope", "selected": ["不存在"], "custom": ""}]},
        )
    with pytest.raises(ValueError, match="不能重复"):
        await service.claim_question_answer(
            "conv-1", "user-1", "用户一", "question-1",
            {"answers": [
                {"id": "scope", "selected": ["全部"], "custom": ""},
                {"id": "scope", "selected": ["全部"], "custom": ""},
            ]},
        )
    claimed_run_id, _ = await service.claim_question_answer(
        "conv-1", "user-1", "用户一", "question-1", {"cancelled": True, "answers": []},
    )
    assert claimed_run_id == run_id


@pytest.mark.asyncio
async def test_agent_inbox_completion_schedules_next_run(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "结束边界消息")

    next_run_id = await service.complete_run(run_id, "user-1", "用户一", owners[run_id])

    assert next_run_id
    async with get_session() as session:
        rows = list((await session.execute(
            select(ChatDataAgentInbox).order_by(ChatDataAgentInbox.id)
        )).scalars())
    assert rows[0].status == "finished"
    assert rows[1].run_id == next_run_id
    assert rows[1].status == "claimed"
    assert rows[1].message_id == message_id

    next_run = next(row for row in rows if row.record_type == "run" and row.run_id == next_run_id)
    assert service._load_run_content(next_run.content)["turn_id"] == message_id


@pytest.mark.asyncio
async def test_continuation_turn_id_tracks_last_admitted_user_message(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    await service.enqueue_message(
        "conv-1", "user-1", "用户一", "立即补充", queue_type="next-step",
    )
    last_message_id = await service.enqueue_message(
        "conv-1", "user-1", "用户一", "下一轮问题", queue_type="next-turn",
    )

    next_run_id = await service.complete_run(run_id, "user-1", "用户一", owners[run_id])

    async with get_session() as session:
        next_run = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == next_run_id,
            ChatDataAgentInbox.record_type == "run",
        ))).scalar_one()
    assert service._load_run_content(next_run.content)["turn_id"] == last_message_id


@pytest.mark.asyncio
async def test_agent_inbox_reclaims_expired_message_lease(inbox_service):
    service, create_thread, get_session, owners = inbox_service
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
    service, create_thread, get_session, owners = inbox_service
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
async def test_expired_run_is_reclaimed_with_same_run_and_messages(inbox_service):
    """Worker 崩溃后由新 Worker 接管同一 Run，不创建并发 Run。"""
    service, create_thread, get_session, owners = inbox_service
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

    with pytest.raises(AgentRunConflictError):
        await service.start_run("conv-1", "user-1", "用户一")

    prepared = await service.prepare_pending_run(run_id)
    assert prepared is not None
    assert prepared["run_id"] == run_id
    assert prepared["recovering"] is True
    assert [message.id for message in prepared["messages"]] == [message_id]
    assert prepared["owner_token"].startswith("2:")


@pytest.mark.asyncio
async def test_expired_run_accepts_steer_for_recovery_and_rejects_parallel_completion(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    async with get_session() as session:
        run = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == run_id,
            ChatDataAgentInbox.record_type == "run",
        ))).scalar_one()
        run.lease_expire_time = datetime.now() - timedelta(seconds=1)

    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "恢复后继续处理")
    assert message_id
    with pytest.raises(AgentRunConflictError):
        await service.start_run("conv-1", "user-1", "用户一")


@pytest.mark.asyncio
async def test_recovered_messages_are_reconciled_against_checkpoint(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    checkpointed_id = await service.enqueue_message(
        "conv-1", "user-1", "用户一", "已写入 checkpoint", queue_type="next-step",
    )
    pending_id = await service.enqueue_message(
        "conv-1", "user-1", "用户一", "尚未写入 checkpoint", queue_type="next-step",
    )
    await service.claim_pending_messages(run_id)

    await service.reconcile_recovered_messages(
        run_id,
        {checkpointed_id},
        owners[run_id],
    )

    async with get_session() as session:
        rows = list((await session.execute(
            select(ChatDataAgentInbox)
            .where(ChatDataAgentInbox.record_type == "message")
            .order_by(ChatDataAgentInbox.id)
        )).scalars())
    assert [(row.message_id, row.status) for row in rows] == [
        (checkpointed_id, "consumed"),
        (pending_id, "pending"),
    ]


@pytest.mark.asyncio
async def test_tool_execution_fence_rejects_stale_owner_before_handler():
    calls = {"handler": 0}

    class Inbox:
        async def assert_run_owner(self, _run_id, _owner_token):
            raise AgentRunLeaseLostError("owner changed")

    async def handler(_request):
        calls["handler"] += 1
        return ToolMessage(content="ok", tool_call_id="call-1")

    middleware = create_tool_execution_fence_middleware(
        run_id="run-1",
        owner_token="1:stale",
        inbox_service=Inbox(),
    )

    with pytest.raises(AgentRunLeaseLostError):
        await middleware.awrap_tool_call(object(), handler)
    assert calls["handler"] == 0


@pytest.mark.asyncio
async def test_cancel_active_run_waits_for_worker_and_preserves_queue(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "停止后继续")

    assert await service.cancel_active_run("conv-1", "user-1", "用户一", run_id) is True
    assert await service.heartbeat_run(run_id) == "cancelling"
    with pytest.raises(AgentRunConflictError):
        await service.start_run("conv-1", "user-1", "用户一")
    with pytest.raises(AgentRunNotActiveError):
        await service.enqueue_message("conv-1", "user-1", "用户一", "停止中不能继续排队")

    await service.finish_run(run_id, "cancelled", "user-1", "用户一")
    next_run_id, adopted = await service.start_run("conv-1", "user-1", "用户一")
    assert next_run_id != run_id
    assert [message.id for message in adopted] == [message_id]


@pytest.mark.asyncio
async def test_cancelling_run_cannot_claim_messages(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    step_id = await service.enqueue_message(
        "conv-1", "user-1", "用户一", "立即消息", queue_type="next-step",
    )
    turn_id = await service.enqueue_message("conv-1", "user-1", "用户一", "下轮消息")
    assert await service.cancel_active_run("conv-1", "user-1", "用户一", run_id) is True

    assert (await service.claim_pending_messages(run_id)).messages == []
    assert await service.complete_run(run_id, "user-1", "用户一", owners[run_id]) is None
    await service.finish_run(run_id, "cancelled", "user-1", "用户一")

    async with get_session() as session:
        rows = list((await session.execute(
            select(ChatDataAgentInbox).where(ChatDataAgentInbox.run_id == run_id)
            .order_by(ChatDataAgentInbox.id)
        )).scalars())
    assert rows[0].status == "cancelled"
    assert [(row.message_id, row.status) for row in rows[1:]] == [
        (step_id, "pending"),
        (turn_id, "pending"),
    ]


@pytest.mark.asyncio
async def test_stale_run_id_cannot_cancel_newer_run(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    old_run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    await service.finish_run(old_run_id, "cancelled", "user-1", "用户一")
    new_run_id, _ = await service.start_run("conv-1", "user-1", "用户一")

    assert await service.cancel_active_run("conv-1", "user-1", "用户一", old_run_id) is False

    async with get_session() as session:
        new_run = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == new_run_id,
            ChatDataAgentInbox.record_type == "run",
        ))).scalar_one()
    assert new_run.status == "active"


@pytest.mark.asyncio
async def test_agent_inbox_rejects_missing_or_unowned_conversation(inbox_service):
    service, create_thread, _get_session, owners = inbox_service
    await create_thread()

    with pytest.raises(AgentRunNotActiveError):
        await service.start_run("missing", "user-1", "用户一")
    with pytest.raises(AgentRunNotActiveError):
        await service.enqueue_message("conv-1", "other-user", "其他用户", "越权消息")


@pytest.mark.asyncio
async def test_steering_middleware_persists_and_admits_next_step(monkeypatch):
    call_log = {}

    class FakeInboxService:
        async def claim_pending_messages(self, run_id, *, queue_type=None, owner_token=None):
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
        owner_token="owner-1",
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
async def test_steering_middleware_does_not_inject_before_plain_model_call(monkeypatch):
    """普通模型轮次结束前不领取 next-step，避免打断当前助手输出。"""
    claim_calls = 0

    class FakeInboxService:
        async def claim_pending_messages(self, run_id, *, queue_type=None, owner_token=None):
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
        owner_token="owner-1",
        inbox_service=FakeInboxService(),
        admit_messages=admit,
        tracker=SteeringMessageTracker(),
    )

    update = await middleware.abefore_model({
        "messages": [inbox_module.HumanMessage(content="普通对话")],
    }, None)

    assert update is None
    assert stream_events == []
    assert claim_calls == 0


@pytest.mark.asyncio
async def test_steering_middleware_does_not_claim_without_messages():
    claim_calls = 0

    class FakeInboxService:
        async def claim_pending_messages(self, _run_id, *, queue_type=None, owner_token=None):
            nonlocal claim_calls
            claim_calls += 1
            return inbox_module.ClaimedAgentMessages("run-1", [])

    middleware = create_steering_middleware(
        run_id="run-1",
        owner_token="owner-1",
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
        async def claim_pending_messages(self, _run_id, *, queue_type=None, owner_token=None):
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
        owner_token="owner-1",
        inbox_service=FakeInboxService(),
        admit_messages=fail_admission,
        tracker=tracker,
    )

    with pytest.raises(RuntimeError, match="event store unavailable"):
        await middleware.abefore_model({"messages": [
            inbox_module.HumanMessage(content="原问题"),
            ToolMessage(content="工具结果", tool_call_id="call-1"),
        ]}, None)

    assert stream_events == []
    assert tracker.drain() == []


@pytest.mark.asyncio
async def test_agent_inbox_queue_type_separates_next_step_from_next_turn(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")

    steer_id = await service.enqueue_message("conv-1", "user-1", "用户一", "立即插入", queue_type="next-step")
    next_id = await service.enqueue_message("conv-1", "user-1", "用户一", "下轮再说", queue_type="next-turn")

    steer_claimed = await service.claim_pending_messages(run_id, queue_type="next-step")
    assert [message.id for message in steer_claimed.messages] == [steer_id]

    await service.mark_consumed(run_id, [steer_id])
    next_run_id = await service.complete_run(run_id, "user-1", "用户一", owners[run_id])
    assert next_run_id
    async with get_session() as session:
        row = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == next_run_id,
            ChatDataAgentInbox.message_id == next_id,
        ))).scalar_one()
    assert row.status == "claimed"


@pytest.mark.asyncio
async def test_agent_inbox_enqueue_rejects_unknown_queue_type(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    await service.start_run("conv-1", "user-1", "用户一")

    with pytest.raises(ValueError):
        await service.enqueue_message("conv-1", "user-1", "用户一", "非法类型", queue_type="bad")


@pytest.mark.asyncio
async def test_agent_inbox_update_pending_message_content_and_type(inbox_service):
    service, create_thread, get_session, owners = inbox_service
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
    service, create_thread, _get_session, owners = inbox_service
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
    service, create_thread, get_session, owners = inbox_service
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
async def test_agent_inbox_update_requires_existing_message_and_ownership(inbox_service):
    service, create_thread, _get_session, owners = inbox_service
    await create_thread()

    with pytest.raises(inbox_module.SteerMessageNotFoundError):
        await service.update_pending_message("conv-1", "user-1", "用户一", "any-id", content="x")
    with pytest.raises(inbox_module.SteerMessageNotFoundError):
        await service.cancel_pending_message("conv-1", "user-1", "用户一", "any-id")

    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "越权消息")

    with pytest.raises(AgentRunNotActiveError):
        await service.update_pending_message("conv-1", "other-user", "其他用户", message_id, content="x")


@pytest.mark.asyncio
async def test_agent_inbox_can_delete_pending_message_after_run_cancelled(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "停止后残留消息")

    await service.finish_run(run_id, "cancelled", "user-1", "用户一", owners[run_id])
    await service.cancel_pending_message("conv-1", "user-1", "用户一", message_id)

    async with get_session() as session:
        row = (await session.execute(
            select(ChatDataAgentInbox).where(ChatDataAgentInbox.message_id == message_id)
        )).scalar_one()
    assert row.status == "cancelled"
    assert row.delete_flag == 1


@pytest.mark.asyncio
async def test_agent_inbox_completion_claims_all_next_step_and_one_next_turn(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    next_id = await service.enqueue_message("conv-1", "user-1", "用户一", "下一轮任务")
    next_id_2 = await service.enqueue_message("conv-1", "user-1", "用户一", "再下一轮任务")
    steer_id = await service.enqueue_message("conv-1", "user-1", "用户一", "来不及插入的立即任务", queue_type="next-step")

    await service.claim_pending_messages(run_id, queue_type="next-step")
    await service.mark_consumed(run_id, [steer_id])
    next_run_id = await service.complete_run(run_id, "user-1", "用户一", owners[run_id])
    assert next_run_id
    async with get_session() as session:
        rows = list((await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == next_run_id,
            ChatDataAgentInbox.record_type == "message",
        ).order_by(ChatDataAgentInbox.id))).scalars())
    assert [(row.message_id, row.status) for row in rows] == [
        (next_id, "claimed"),
        (next_id_2, "pending"),
    ]


@pytest.mark.asyncio
async def test_agent_inbox_lists_claimed_message_for_refresh_restore(inbox_service):
    service, create_thread, _get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    await service.enqueue_message("conv-1", "user-1", "用户一", "排队一")
    claimed_id = await service.enqueue_message("conv-1", "user-1", "用户一", "排队二", queue_type="next-step")

    queue = await service.list_message_states("conv-1", "user-1")

    assert queue["has_active_run"] is True
    assert queue["run_id"] == run_id
    assert queue["run_status"] == "active"
    assert [item["content"] for item in queue["messages"]] == ["排队一", "排队二"]
    assert [item["queue_type"] for item in queue["messages"]] == ["next-turn", "next-step"]
    assert all(item["message_id"] for item in queue["messages"])

    await service.claim_pending_messages(run_id, queue_type="next-step")
    queue = await service.list_message_states("conv-1", "user-1")
    state = next(item for item in queue["messages"] if item["message_id"] == claimed_id)
    assert state["status"] == "claimed"

    await service.finish_run(run_id, "cancelled", "user-1", "用户一")
    queue = await service.list_message_states("conv-1", "user-1")
    assert queue["has_active_run"] is False
    assert queue["run_id"] == run_id
    assert queue["run_status"] == "cancelled"


@pytest.mark.asyncio
async def test_agent_inbox_list_message_states_rejects_unowned(inbox_service):
    service, create_thread, _get_session, owners = inbox_service
    await create_thread()

    with pytest.raises(AgentRunNotActiveError):
        await service.list_message_states("conv-1", "other-user")


@pytest.mark.asyncio
async def test_record_terminal_event_persists_cursor_without_owner(inbox_service):
    """done 发布后 owner 可能已被 mark_waiting_for_user 清空，落游标不做 owner 断言。"""
    import json

    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    question = {
        "question_id": "question-1",
        "turn_id": "turn-1",
        "questions": [{"id": "scope", "question": "选择范围", "options": [{"label": "全部"}], "multi_select": False}],
    }
    await service.mark_waiting_for_user(run_id, question, "user-1", "用户一", owners[run_id])

    await service.record_terminal_event(run_id, "1740000000000-0")

    async with get_session() as session:
        run = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == run_id,
            ChatDataAgentInbox.record_type == "run",
        ))).scalar_one()
    assert run.status == "waiting_user"
    content = json.loads(run.content)
    assert content["terminal_event"] == {"stream_id": "1740000000000-0", "event_type": "done"}
    # pending_question 不受影响，问答仍可恢复。
    assert content["pending_question"]["question_id"] == "question-1"


@pytest.mark.asyncio
async def test_record_terminal_event_supports_finished_run(inbox_service):
    import json

    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    await service.complete_run(run_id, "user-1", "用户一", owners[run_id])

    await service.record_terminal_event(run_id, "1740000000001-0")

    async with get_session() as session:
        run = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == run_id,
            ChatDataAgentInbox.record_type == "run",
        ))).scalar_one()
    assert run.status == "finished"
    assert json.loads(run.content)["terminal_event"]["stream_id"] == "1740000000001-0"


@pytest.mark.asyncio
async def test_prepare_pending_run_clears_stale_terminal_event(inbox_service):
    """新执行段领取后，上一段的终止游标必须失效，避免误判新段已收口。"""
    import json

    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    question = {
        "question_id": "question-1",
        "turn_id": "turn-1",
        "questions": [{"id": "scope", "question": "选择范围", "options": [{"label": "全部"}], "multi_select": False}],
    }
    await service.mark_waiting_for_user(run_id, question, "user-1", "用户一", owners[run_id])
    await service.record_terminal_event(run_id, "1740000000000-0")

    answer = {"answers": [{"id": "scope", "selected": ["全部"], "custom": ""}]}
    await service.claim_question_answer("conv-1", "user-1", "用户一", "question-1", answer)
    prepared = await service.prepare_pending_run(run_id)
    owners[run_id] = prepared["owner_token"]

    async with get_session() as session:
        run = (await session.execute(select(ChatDataAgentInbox).where(
            ChatDataAgentInbox.run_id == run_id,
            ChatDataAgentInbox.record_type == "run",
        ))).scalar_one()
    assert "terminal_event" not in json.loads(run.content)
