import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from langchain_core.messages import ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.agent.middleware.steering import SteeringMessageTracker, create_steering_middleware
from apps.chat_data_app.agent.middleware.tool_execution_fence import create_tool_execution_fence_middleware
from apps.chat_data_app.agent.run import agent_inbox_service as inbox_module
from apps.chat_data_app.agent.run.agent_inbox_service import (
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
    claimed = await service.claim_pending_messages(run_id, owner_token=owners[run_id])

    assert [message.content for message in claimed.messages] == ["第一条", "第二条"]
    assert [message.id for message in claimed.messages] == [first_id, second_id]

    await service.mark_consumed(run_id, [first_id, second_id], owners[run_id])
    await service.complete_run(run_id, "user-1", "用户一", owners[run_id])

    async with get_session() as session:
        rows = list((await session.execute(select(ChatDataAgentInbox).order_by(ChatDataAgentInbox.id))).scalars())
    assert [row.record_status for row in rows] == ["finished", "consumed", "consumed"]


@pytest.mark.asyncio
async def test_agent_question_wait_and_answer_is_atomic(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    runtime_context = {"query": "查询订单", "model_id": "model-a"}
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一", runtime_context)
    question = {
        "question_id": "question-1",
        "turn_id": "turn-1",
        "questions": [
            {
                "id": "scope",
                "question": "选择范围",
                "options": [{"label": "全部"}, {"label": "本月"}],
                "multi_select": False,
            }
        ],
    }
    await service.mark_waiting_for_user(run_id, question, "user-1", "用户一", owners[run_id])
    assert await service.get_pending_question("conv-1", "user-1") == {"run_id": run_id, **question}

    answer = {"answers": [{"id": "scope", "selected": ["本月"], "custom": ""}]}
    claimed_run_id, restored_context = await service.claim_question_answer(
        "conv-1",
        "user-1",
        "用户一",
        "question-1",
        answer,
    )
    assert claimed_run_id == run_id
    assert restored_context == runtime_context
    assert await service.get_pending_question("conv-1", "user-1") is None
    with pytest.raises(AgentQuestionNotWaitingError):
        await service.claim_question_answer("conv-1", "user-1", "用户一", "question-1", answer)

    async with get_session() as session:
        run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
    assert run.record_status == "pending"
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
        run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
    assert run.record_status == "failed"


@pytest.mark.asyncio
async def test_finish_run_reports_waiting_user_when_question_already_asked(inbox_service):
    """异常/取消发生在问题落库之后：回报权威状态 waiting_user，队列保持挂载。"""
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "排队消息", queue_type="next-step")
    claimed = await service.claim_pending_messages(run_id, owner_token=owners[run_id])
    assert [message.id for message in claimed.messages] == [message_id]

    question = {"question_id": "question-1", "questions": [{"id": "scope", "question": "选择范围"}]}
    await service.mark_waiting_for_user(run_id, question, "user-1", "用户一", owners[run_id])

    # 此刻 Runtime 才遭遇异常/取消：不得把 waiting_user 收口成 failed/cancelled，
    # 否则前端会按失败清理掉仍在等待回答的问题卡片。
    assert await service.finish_run(run_id, "failed", "user-1", "用户一", owners[run_id]) == "waiting_user"
    assert await service.finish_run(run_id, "cancelled", "user-1", "用户一", owners[run_id]) == "waiting_user"

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(ChatDataAgentInbox)
                    .where(ChatDataAgentInbox.run_id == run_id)
                    .order_by(ChatDataAgentInbox.id)
                )
            ).scalars()
        )
    assert rows[0].record_status == "waiting_user"
    assert [(row.message_id, row.record_status) for row in rows[1:]] == [(message_id, "claimed")]


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
        run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
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
        "questions": [
            {
                "id": "scope",
                "question": "选择范围",
                "options": [{"label": "全部"}],
                "multi_select": False,
            }
        ],
    }
    await service.mark_waiting_for_user(run_id, question, "user-1", "用户一", owners[run_id])
    with pytest.raises(ValueError, match="无效选项"):
        await service.claim_question_answer(
            "conv-1",
            "user-1",
            "用户一",
            "question-1",
            {"answers": [{"id": "scope", "selected": ["不存在"], "custom": ""}]},
        )
    with pytest.raises(ValueError, match="不能重复"):
        await service.claim_question_answer(
            "conv-1",
            "user-1",
            "用户一",
            "question-1",
            {
                "answers": [
                    {"id": "scope", "selected": ["全部"], "custom": ""},
                    {"id": "scope", "selected": ["全部"], "custom": ""},
                ]
            },
        )
    claimed_run_id, _ = await service.claim_question_answer(
        "conv-1",
        "user-1",
        "用户一",
        "question-1",
        {"cancelled": True, "answers": []},
    )
    assert claimed_run_id == run_id


@pytest.mark.asyncio
async def test_agent_inbox_completion_schedules_next_run(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "结束边界消息")

    completion = await service.complete_run(run_id, "user-1", "用户一", owners[run_id])
    next_run_id = completion.next_run_id

    assert next_run_id
    assert completion.status == "finished"
    async with get_session() as session:
        rows = list((await session.execute(select(ChatDataAgentInbox).order_by(ChatDataAgentInbox.id))).scalars())
    assert rows[0].record_status == "finished"
    assert service._load_run_content(rows[0].record_content)["next_run_id"] == next_run_id
    assert rows[1].run_id == next_run_id
    assert rows[1].record_status == "claimed"
    assert rows[1].message_id == message_id

    next_run = next(row for row in rows if row.record_type == "run" and row.run_id == next_run_id)
    assert service._load_run_content(next_run.record_content)["turn_id"] == message_id


@pytest.mark.asyncio
async def test_continuation_turn_id_tracks_last_admitted_user_message(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    await service.enqueue_message(
        "conv-1",
        "user-1",
        "用户一",
        "立即补充",
        queue_type="next-step",
    )
    last_message_id = await service.enqueue_message(
        "conv-1",
        "user-1",
        "用户一",
        "下一轮问题",
        queue_type="next-turn",
    )

    completion = await service.complete_run(run_id, "user-1", "用户一", owners[run_id])
    next_run_id = completion.next_run_id

    async with get_session() as session:
        next_run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == next_run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
    assert service._load_run_content(next_run.record_content)["turn_id"] == last_message_id


@pytest.mark.asyncio
async def test_agent_inbox_reclaims_expired_message_lease(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "重试消息", queue_type="next-step")
    await service.claim_pending_messages(run_id, owner_token=owners[run_id])

    async with get_session() as session:
        row = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.message_id == message_id,
                )
            )
        ).scalar_one()
        row.lease_expire_time = datetime.now() - timedelta(seconds=1)

    reclaimed = await service.claim_pending_messages(run_id, owner_token=owners[run_id])
    assert [message.id for message in reclaimed.messages] == [message_id]


@pytest.mark.asyncio
async def test_agent_inbox_heartbeat_renews_only_run_lease(inbox_service):
    """高频心跳只续 Run，不再锁定或改写已领取消息。"""
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "心跳消息")
    await service.claim_pending_messages(run_id, owner_token=owners[run_id])

    async with get_session() as session:
        before = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.message_id == message_id,
                )
            )
        ).scalar_one()
        message_lease = before.lease_expire_time
        run_before = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
        run_lease = run_before.lease_expire_time

    assert await service.heartbeat_run(run_id, owners[run_id]) == "active"

    async with get_session() as session:
        after_message = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.message_id == message_id,
                )
            )
        ).scalar_one()
        after_run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()

    assert after_message.lease_expire_time == message_lease
    assert after_run.lease_expire_time != run_lease


@pytest.mark.asyncio
async def test_failed_run_requeues_claimed_messages_for_next_run(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "失败后重试", queue_type="next-step")
    await service.claim_pending_messages(run_id, owner_token=owners[run_id])
    await service.finish_run(run_id, "failed", "user-1", "用户一", owners[run_id])

    next_run_id, adopted = await service.start_run("conv-1", "user-1", "用户一")
    assert [message.id for message in adopted] == [message_id]
    claimed = await service.claim_pending_messages(next_run_id, owner_token=owners[next_run_id])
    assert claimed.messages == []


@pytest.mark.asyncio
async def test_start_run_can_preserve_pending_messages_for_new_input(inbox_service):
    """停止后发送新输入时，旧队列必须留给当前 Run 完成后的 continuation。"""
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    old_run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    queued_id = await service.enqueue_message("conv-1", "user-1", "用户一", "旧队列")
    await service.cancel_active_run("conv-1", "user-1", "用户一", old_run_id)
    await service.finish_run(old_run_id, "cancelled", "user-1", "用户一", owners[old_run_id])

    new_run_id, adopted = await AgentInboxService.start_run(
        service,
        "conv-1",
        "user-1",
        "用户一",
        {"query": "新输入"},
        adopt_pending_messages=False,
    )
    assert new_run_id != old_run_id
    assert adopted == []
    async with get_session() as session:
        queued = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.message_id == queued_id,
                )
            )
        ).scalar_one()
    assert queued.record_status == "pending"
    assert queued.run_id == old_run_id


@pytest.mark.asyncio
async def test_start_run_selected_message_only_executes_one_pending_message(inbox_service):
    """停止后点击执行时，只迁移指定队列消息，其他消息继续等待。"""
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    old_run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    first_id = await service.enqueue_message("conv-1", "user-1", "用户一", "执行这一条")
    second_id = await service.enqueue_message("conv-1", "user-1", "用户一", "保留这一条")
    await service.cancel_active_run("conv-1", "user-1", "用户一", old_run_id)
    await service.finish_run(old_run_id, "cancelled", "user-1", "用户一", owners[old_run_id])

    new_run_id, adopted = await AgentInboxService.start_run(
        service,
        "conv-1",
        "user-1",
        "用户一",
        {"query": ""},
        adopt_pending_messages=False,
        selected_message_id=first_id,
    )
    assert [message.id for message in adopted] == [first_id]
    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(ChatDataAgentInbox)
                    .where(
                        ChatDataAgentInbox.message_id.in_([first_id, second_id]),
                    )
                    .order_by(ChatDataAgentInbox.id)
                )
            ).scalars()
        )
    assert [(row.message_id, row.run_id, row.record_status) for row in rows] == [
        (first_id, new_run_id, "claimed"),
        (second_id, old_run_id, "pending"),
    ]


@pytest.mark.asyncio
async def test_expired_run_stops_and_new_run_adopts_requeued_messages(inbox_service):
    """Worker 崩溃（租约过期）后任务停止：不自动续跑，旧 Run 收口为 failed，
    排队消息退回 pending 由用户下一次发起的新 Run 接管。"""
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "租约回收", queue_type="next-step")
    await service.claim_pending_messages(run_id, owner_token=owners[run_id])
    async with get_session() as session:
        run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
        run.lease_expire_time = datetime.now() - timedelta(seconds=1)

    # 调度器不接管过期 active Run
    assert await service.prepare_pending_run(run_id) is None

    # 用户下一次发起会话：旧 Run 就地收口，新 Run 创建并接管退回的消息
    new_run_id, adopted = await service.start_run("conv-1", "user-1", "用户一")
    assert new_run_id != run_id
    assert [message.id for message in adopted] == [message_id]
    async with get_session() as session:
        old_run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
    assert old_run.record_status == "failed"
    assert old_run.owner_token is None


@pytest.mark.asyncio
async def test_background_reaper_closes_expired_run_and_requeues_messages(inbox_service):
    """后台回收无需用户刷新：收口过期 Run 并返回终态发布信息。"""
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "后台回收", queue_type="next-step")
    await service.claim_pending_messages(run_id, owner_token=owners[run_id])

    async with get_session() as session:
        run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
        run.lease_expire_time = datetime.now() - timedelta(seconds=1)

    results = await service.reap_expired_runs()

    assert len(results) == 1
    assert results[0].run_id == run_id
    assert results[0].status == "failed"
    assert results[0].owner_token.startswith("2:")
    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(ChatDataAgentInbox)
                    .where(
                        ChatDataAgentInbox.run_id == run_id,
                    )
                    .order_by(ChatDataAgentInbox.id)
                )
            ).scalars()
        )
    assert rows[0].record_status == "failed"
    assert rows[0].owner_token is None
    assert rows[1].message_id == message_id
    assert rows[1].record_status == "pending"


@pytest.mark.asyncio
async def test_background_reaper_preserves_waiting_question(inbox_service):
    """已落库但尚未恢复的问题回收后仍保持 waiting_user。"""
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    question = {"question_id": "q1", "questions": [{"id": "scope", "question": "选择范围"}]}
    await service.mark_waiting_for_user(run_id, question, "user-1", "用户一", owners[run_id])

    # waiting_user 本身没有执行租约，不应被后台回收。
    assert await service.reap_expired_runs() == []

    async with get_session() as session:
        run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
    assert run.record_status == "waiting_user"


@pytest.mark.asyncio
async def test_expired_run_rejects_steer_and_allows_new_run(inbox_service):
    """租约过期的 Run 已死亡：追加消息就地收口该 Run 并拒绝入队，随后可发起新 Run。"""
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    async with get_session() as session:
        run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
        run.lease_expire_time = datetime.now() - timedelta(seconds=1)

    with pytest.raises(AgentRunNotActiveError):
        await service.enqueue_message("conv-1", "user-1", "用户一", "恢复后继续处理")
    new_run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    assert new_run_id != run_id


@pytest.mark.asyncio
async def test_recovered_messages_are_reconciled_against_checkpoint(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    checkpointed_id = await service.enqueue_message(
        "conv-1",
        "user-1",
        "用户一",
        "已写入 checkpoint",
        queue_type="next-step",
    )
    pending_id = await service.enqueue_message(
        "conv-1",
        "user-1",
        "用户一",
        "尚未写入 checkpoint",
        queue_type="next-step",
    )
    await service.claim_pending_messages(run_id, owner_token=owners[run_id])

    await service.reconcile_recovered_messages(
        run_id,
        {checkpointed_id},
        owners[run_id],
    )

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(ChatDataAgentInbox)
                    .where(ChatDataAgentInbox.record_type == "message")
                    .order_by(ChatDataAgentInbox.id)
                )
            ).scalars()
        )
    assert [(row.message_id, row.record_status) for row in rows] == [
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

    cancellation = await service.cancel_active_run("conv-1", "user-1", "用户一", run_id)
    assert cancellation.cancelled is True
    assert cancellation.status == "cancelling"
    assert await service.heartbeat_run(run_id, owners[run_id]) == "cancelling"
    with pytest.raises(AgentRunConflictError):
        await service.start_run("conv-1", "user-1", "用户一")
    with pytest.raises(AgentRunNotActiveError):
        await service.enqueue_message("conv-1", "user-1", "用户一", "停止中不能继续排队")

    await service.finish_run(run_id, "cancelled", "user-1", "用户一", owners[run_id])
    next_run_id, adopted = await service.start_run("conv-1", "user-1", "用户一")
    assert next_run_id != run_id
    assert [message.id for message in adopted] == [message_id]


@pytest.mark.asyncio
async def test_cancel_pending_run_closes_transaction_with_terminal_status(inbox_service):
    """Worker prepare 前停止：事务内直接收口，Run status 可供 SSE 补发终态。"""
    service, create_thread, get_session, _owners = inbox_service
    await create_thread()
    run_id, _ = await AgentInboxService.start_run(service, "conv-1", "user-1", "用户一")

    cancellation = await service.cancel_active_run("conv-1", "user-1", "用户一", run_id)

    assert cancellation.cancelled is True
    assert cancellation.status == "cancelled"
    assert cancellation.terminal_owner_token
    async with get_session() as session:
        run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
    assert run.record_status == "cancelled"


@pytest.mark.asyncio
async def test_cancelling_run_cannot_claim_messages(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    step_id = await service.enqueue_message(
        "conv-1",
        "user-1",
        "用户一",
        "立即消息",
        queue_type="next-step",
    )
    turn_id = await service.enqueue_message("conv-1", "user-1", "用户一", "下轮消息")
    cancellation = await service.cancel_active_run("conv-1", "user-1", "用户一", run_id)
    assert cancellation.cancelled is True
    assert cancellation.status == "cancelling"

    assert (await service.claim_pending_messages(run_id, owner_token=owners[run_id])).messages == []
    completion = await service.complete_run(run_id, "user-1", "用户一", owners[run_id])
    assert completion.status == "cancelled"

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(ChatDataAgentInbox)
                    .where(ChatDataAgentInbox.run_id == run_id)
                    .order_by(ChatDataAgentInbox.id)
                )
            ).scalars()
        )
    assert rows[0].record_status == "cancelled"
    assert [(row.message_id, row.record_status) for row in rows[1:]] == [
        (step_id, "pending"),
        (turn_id, "pending"),
    ]


@pytest.mark.asyncio
async def test_stale_run_id_cannot_cancel_newer_run(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    old_run_id, _ = await service.start_run("conv-1", "user-1", "用户一")
    await service.finish_run(old_run_id, "cancelled", "user-1", "用户一", owners[old_run_id])
    new_run_id, _ = await service.start_run("conv-1", "user-1", "用户一")

    cancellation = await service.cancel_active_run("conv-1", "user-1", "用户一", old_run_id)
    assert cancellation.cancelled is False

    async with get_session() as session:
        new_run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == new_run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
    assert new_run.record_status == "active"


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

    update = await middleware.abefore_model(
        {
            "messages": [
                inbox_module.HumanMessage(content="原问题"),
                ToolMessage(content="工具结果", tool_call_id="call-1"),
            ],
        },
        None,
    )

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

    update = await middleware.abefore_model(
        {
            "messages": [inbox_module.HumanMessage(content="普通对话")],
        },
        None,
    )

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
        await middleware.abefore_model(
            {
                "messages": [
                    inbox_module.HumanMessage(content="原问题"),
                    ToolMessage(content="工具结果", tool_call_id="call-1"),
                ]
            },
            None,
        )

    assert stream_events == []
    assert tracker.drain() == []


@pytest.mark.asyncio
async def test_agent_inbox_queue_type_separates_next_step_from_next_turn(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")

    steer_id = await service.enqueue_message("conv-1", "user-1", "用户一", "立即插入", queue_type="next-step")
    next_id = await service.enqueue_message("conv-1", "user-1", "用户一", "下轮再说", queue_type="next-turn")

    steer_claimed = await service.claim_pending_messages(run_id, queue_type="next-step", owner_token=owners[run_id])
    assert [message.id for message in steer_claimed.messages] == [steer_id]

    await service.mark_consumed(run_id, [steer_id], owners[run_id])
    completion = await service.complete_run(run_id, "user-1", "用户一", owners[run_id])
    next_run_id = completion.next_run_id
    assert next_run_id
    async with get_session() as session:
        row = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == next_run_id,
                    ChatDataAgentInbox.message_id == next_id,
                )
            )
        ).scalar_one()
    assert row.record_status == "claimed"


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
        "conv-1",
        "user-1",
        "用户一",
        message_id,
        content="修改后内容",
        queue_type="next-turn",
    )

    assert updated == {"message_id": message_id, "content": "修改后内容", "queue_type": "next-turn"}
    async with get_session() as session:
        row = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id, ChatDataAgentInbox.record_type == "message"
                )
            )
        ).scalar_one()
    assert row.record_content == "修改后内容"
    assert row.queue_type == "next-turn"


@pytest.mark.asyncio
async def test_agent_inbox_update_rejects_missing_and_claimed_messages(inbox_service):
    service, create_thread, _get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    message_id = await service.enqueue_message("conv-1", "user-1", "用户一", "将被领取", queue_type="next-step")

    with pytest.raises(inbox_module.SteerMessageNotFoundError):
        await service.update_pending_message("conv-1", "user-1", "用户一", "missing-id", content="x")

    await service.claim_pending_messages(run_id, queue_type="next-step", owner_token=owners[run_id])
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
        row = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id, ChatDataAgentInbox.record_type == "message"
                )
            )
        ).scalar_one()
    assert row.delete_flag == 1

    claimed = await service.claim_pending_messages(run_id, queue_type="next-step", owner_token=owners[run_id])
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
        row = (
            await session.execute(select(ChatDataAgentInbox).where(ChatDataAgentInbox.message_id == message_id))
        ).scalar_one()
    assert row.record_status == "cancelled"
    assert row.delete_flag == 1


@pytest.mark.asyncio
async def test_agent_inbox_completion_claims_all_next_step_and_one_next_turn(inbox_service):
    service, create_thread, get_session, owners = inbox_service
    await create_thread()
    run_id, _adopted = await service.start_run("conv-1", "user-1", "用户一")
    next_id = await service.enqueue_message("conv-1", "user-1", "用户一", "下一轮任务")
    next_id_2 = await service.enqueue_message("conv-1", "user-1", "用户一", "再下一轮任务")
    steer_id = await service.enqueue_message(
        "conv-1", "user-1", "用户一", "来不及插入的立即任务", queue_type="next-step"
    )

    await service.claim_pending_messages(run_id, queue_type="next-step", owner_token=owners[run_id])
    await service.mark_consumed(run_id, [steer_id], owners[run_id])
    completion = await service.complete_run(run_id, "user-1", "用户一", owners[run_id])
    next_run_id = completion.next_run_id
    assert next_run_id
    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(ChatDataAgentInbox)
                    .where(
                        ChatDataAgentInbox.run_id == next_run_id,
                        ChatDataAgentInbox.record_type == "message",
                    )
                    .order_by(ChatDataAgentInbox.id)
                )
            ).scalars()
        )
    assert [(row.message_id, row.record_status) for row in rows] == [
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

    await service.claim_pending_messages(run_id, queue_type="next-step", owner_token=owners[run_id])
    queue = await service.list_message_states("conv-1", "user-1")
    state = next(item for item in queue["messages"] if item["message_id"] == claimed_id)
    assert state["status"] == "claimed"

    await service.finish_run(run_id, "cancelled", "user-1", "用户一", owners[run_id])
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
        run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
    assert run.record_status == "waiting_user"
    content = json.loads(run.record_content)
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
        run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
    assert run.record_status == "finished"
    assert json.loads(run.record_content)["terminal_event"]["stream_id"] == "1740000000001-0"


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
        run = (
            await session.execute(
                select(ChatDataAgentInbox).where(
                    ChatDataAgentInbox.run_id == run_id,
                    ChatDataAgentInbox.record_type == "run",
                )
            )
        ).scalar_one()
    assert "terminal_event" not in json.loads(run.record_content)


@pytest.mark.asyncio
async def test_agent_inbox_meta_roundtrip_to_human_message(inbox_service):
    """排队消息随行元数据：入队落 meta 列，构建 HumanMessage 时还原进 additional_kwargs。"""
    service, create_thread, get_session, _owners = inbox_service
    await create_thread()
    run_id, _ = await service.start_run("conv-1", "user-1", "用户一")

    meta = {"file_selections": ["workspace/semantic/a__1.yaml"], "file_ids": ["file-1", "file-2"]}
    with_meta_id = await service.enqueue_message(
        "conv-1",
        "user-1",
        "用户一",
        "带引用",
        queue_type="next-step",
        meta=meta,
    )
    without_meta_id = await service.enqueue_message(
        "conv-1",
        "user-1",
        "用户一",
        "纯文本",
        queue_type="next-step",
    )

    async with get_session() as session:
        rows = {row.message_id: row for row in (await session.execute(select(ChatDataAgentInbox))).scalars()}
    assert rows[with_meta_id].record_meta and "file_selections" in rows[with_meta_id].record_meta
    assert rows[without_meta_id].record_meta is None

    claimed = await service.claim_pending_messages(run_id, owner_token=_owners[run_id])
    by_content = {message.content: message for message in claimed.messages}
    with_meta_msg = by_content["带引用"]
    assert with_meta_msg.additional_kwargs["file_selections"] == meta["file_selections"]
    assert with_meta_msg.additional_kwargs["file_ids"] == meta["file_ids"]
    assert with_meta_msg.additional_kwargs["timestamp"]
    without_meta_msg = by_content["纯文本"]
    assert "file_selections" not in without_meta_msg.additional_kwargs
    assert "file_ids" not in without_meta_msg.additional_kwargs


@pytest.mark.asyncio
async def test_agent_inbox_list_message_states_returns_meta(inbox_service):
    """刷新恢复接口透传随行元数据；无 meta 的项返回 None。"""
    service, create_thread, _get_session, _owners = inbox_service
    await create_thread()
    await service.start_run("conv-1", "user-1", "用户一")
    await service.enqueue_message(
        "conv-1",
        "user-1",
        "用户一",
        "带附件",
        queue_type="next-turn",
        meta={"file_ids": ["file-9"]},
    )
    await service.enqueue_message("conv-1", "user-1", "用户一", "纯文本")

    queue = await service.list_message_states("conv-1", "user-1")
    states = {item["content"]: item for item in queue["messages"]}
    assert states["带附件"]["meta"] == {"file_ids": ["file-9"]}
    assert states["纯文本"]["meta"] is None


def test_load_inbox_meta_tolerates_bad_data():
    """meta 列坏数据（非 JSON / 非 dict / 空）一律回退空 dict，不影响构建消息。"""
    from apps.chat_data_app.agent.run.agent_inbox_service import _load_inbox_meta

    assert _load_inbox_meta(None) == {}
    assert _load_inbox_meta("") == {}
    assert _load_inbox_meta("not-json") == {}
    assert _load_inbox_meta('["list"]') == {}
    assert _load_inbox_meta('{"file_ids": ["f1"]}') == {"file_ids": ["f1"]}
