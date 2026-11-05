import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.agent.run.agent_inbox_service import AgentRunLeaseLostError
from apps.chat_data_app.agent.run.conversation_event_service import ConversationEventService
from apps.chat_data_app.daos.conversation_event_dao import ConversationEventDao
from utils.models import Base, ChatDataAgentInbox, ChatDataConversationEvent, UserThread


@pytest.fixture()
def conversation_event_service():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

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

    async def create_thread(conversation_id: str, user_id: str):
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

    service = ConversationEventService(ConversationEventDao(session_context=lambda: get_test_session()))
    yield service, create_thread, get_test_session
    asyncio.run(engine.dispose())


@pytest.mark.asyncio
async def test_event_history_is_ordered_and_idempotent_across_turns(conversation_event_service):
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-1", "user-1")

    first_user = HumanMessage(content="查询订单", id="turn-1", additional_kwargs={"timestamp": "2026-07-24 10:00:00"})
    first_turn_messages = [
        first_user,
        AIMessage(
            content="", id="assistant-1", tool_calls=[{"id": "tool-1", "name": "data_analyzing_tool", "args": {}}]
        ),
        ToolMessage(content="订单查询结果", id="tool-result-1", tool_call_id="tool-1", name="data_analyzing_tool"),
        AIMessage(content="订单已查询完成", id="assistant-2"),
    ]
    first_reasoning = [
        {"id": "reasoning-1", "content": "先查询订单数据", "tool_call_count": 0, "reasoning_turn_id": "turn-1"},
        {"id": "reasoning-2", "content": "整理查询结果", "tool_call_count": 1, "reasoning_turn_id": "turn-1"},
    ]

    await service.persist_user_message(
        conversation_id="conv-1",
        user_id="user-1",
        operator_name="用户一",
        message=first_user,
    )
    await service.persist_turn(
        conversation_id="conv-1",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-1",
        messages=first_turn_messages,
        reasoning_segments=first_reasoning,
        token_usage={"used": 80, "total": 100, "breakdown": [{"content": "large"}]},
    )
    # 重复持久化同一轮不能生成重复历史事件。
    await service.persist_turn(
        conversation_id="conv-1",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-1",
        messages=first_turn_messages,
        reasoning_segments=first_reasoning,
    )

    second_user = HumanMessage(content="再给出结论", id="turn-2")
    await service.persist_user_message(
        conversation_id="conv-1",
        user_id="user-1",
        operator_name="用户一",
        message=second_user,
    )
    await service.persist_turn(
        conversation_id="conv-1",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-2",
        messages=[second_user, AIMessage(content="最终结论", id="assistant-3")],
        reasoning_segments=[],
        token_usage={"used": 35, "total": 100, "llm_calls": [{"input_tokens": 35}]},
    )

    history = await service.load_history_with_recovery(conversation_id="conv-1", user_id="user-1")

    sequence_numbers = [event["sequence_no"] for event in history["history_events"]]
    assert sequence_numbers == sorted(sequence_numbers)
    assert len(sequence_numbers) == len(set(sequence_numbers))
    assert [event["event_type"] for event in history["history_events"]] == [
        "user_message",
        "reasoning",
        "assistant_message",
        "tool_result",
        "reasoning",
        "assistant_message",
        "user_message",
        "assistant_message",
    ]
    assert [message["content"] for message in history["messages"]] == [
        "查询订单",
        "",
        "订单查询结果",
        "订单已查询完成",
        "再给出结论",
        "最终结论",
    ]
    assert history["token_usage"] == {
        "used": 35,
        "total": 100,
        "llm_calls": [{"input_tokens": 35}],
    }
    assert all(event["event_type"] != "token_usage" for event in history["history_events"])
    assert await service.get_first_user_message(conversation_id="conv-1", user_id="user-1") == "查询订单"


@pytest.mark.asyncio
async def test_persist_user_message_touches_thread_update_time_but_turn_does_not(conversation_event_service):
    """用户提问刷新会话行 update_time（侧栏「最近更新」排序依据），助手轮次不刷新。"""
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-touch", "user-1")

    async def read_thread():
        async with get_session() as session:
            row = (await session.scalars(select(UserThread).where(UserThread.thread_id == "conv-touch"))).one()
            return row.update_time, row.update_by

    initial_time, _ = await read_thread()
    assert initial_time is not None  # server_default=func.now()，创建时已有值

    user = HumanMessage(content="第一问", id="turn-touch-1")
    await service.persist_user_message(
        conversation_id="conv-touch",
        user_id="user-1",
        operator_name="用户一",
        message=user,
    )
    touched_time, touched_by = await read_thread()
    assert touched_time is not None
    assert touched_time > initial_time
    assert touched_by == "user-1"

    before_turn = touched_time
    await service.persist_turn(
        conversation_id="conv-touch",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-touch-1",
        messages=[user, AIMessage(content="回答", id="assistant-touch-1")],
        reasoning_segments=[],
    )
    turn_time, turn_by = await read_thread()
    assert turn_time == before_turn
    assert turn_by == "user-1"

    # 再次提问继续推进 update_time（严格递增，用睡眠保证时间戳不同）。
    await asyncio.sleep(0.01)
    second = HumanMessage(content="第二问", id="turn-touch-2")
    await service.persist_user_message(
        conversation_id="conv-touch",
        user_id="user-1",
        operator_name="用户一",
        message=second,
    )
    again_time, _ = await read_thread()
    assert again_time > before_turn


@pytest.mark.asyncio
async def test_token_usage_is_single_upserted_row_per_conversation(conversation_event_service):
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-usage", "user-1")

    for turn_index, used in enumerate((80, 35, 60), start=1):
        turn_id = f"turn-{turn_index}"
        user = HumanMessage(content=f"问题 {turn_index}", id=turn_id)
        await service.persist_user_message(
            conversation_id="conv-usage",
            user_id="user-1",
            operator_name="用户一",
            message=user,
        )
        await service.persist_turn(
            conversation_id="conv-usage",
            user_id="user-1",
            operator_name="用户一",
            turn_id=turn_id,
            messages=[user, AIMessage(content=f"回答 {turn_index}", id=f"assistant-{turn_index}")],
            reasoning_segments=[],
            token_usage={"used": used, "total": 100},
        )

    # 多轮之后表中只应有一条 token_usage 行（event_key 固定 upsert），且为最新一轮的值。
    from sqlalchemy import select

    from utils.models import ChatDataConversationEvent

    dao = service._dao
    async with dao._session_context() as session:
        rows = list(
            (
                await session.scalars(
                    select(ChatDataConversationEvent).where(
                        ChatDataConversationEvent.conversation_id == "conv-usage",
                        ChatDataConversationEvent.event_type == "token_usage",
                    )
                )
            ).all()
        )
    assert len(rows) == 1
    assert rows[0].event_key == "token_usage:latest"
    assert rows[0].turn_id == "turn-3"

    history = await service.load_history_with_recovery(conversation_id="conv-usage", user_id="user-1")
    assert history["token_usage"] == {"used": 60, "total": 100}


@pytest.mark.asyncio
async def test_event_history_is_scoped_to_conversation_owner(conversation_event_service):
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-owner", "user-1")
    message = HumanMessage(content="只属于用户一", id="turn-owner")

    await service.persist_user_message(
        conversation_id="conv-owner",
        user_id="user-1",
        operator_name="用户一",
        message=message,
    )

    assert (await service.load_history_with_recovery(conversation_id="conv-owner", user_id="user-1"))["messages"][0][
        "content"
    ] == "只属于用户一"
    assert (await service.load_history_with_recovery(conversation_id="conv-owner", user_id="user-2"))[
        "history_events"
    ] == []


@pytest.mark.asyncio
async def test_context_compression_event_is_anchored_and_idempotent(conversation_event_service):
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-compression", "user-1")
    user_message = HumanMessage(content="继续分析", id="turn-1")
    tool_message = ToolMessage(
        content="工具结果",
        id="tool-1",
        tool_call_id="call-1",
        name="data_analyzing_tool",
    )
    messages = [
        user_message,
        AIMessage(
            content="", id="assistant-1", tool_calls=[{"id": "call-1", "name": "data_analyzing_tool", "args": {}}]
        ),
        tool_message,
        AIMessage(content="已完成", id="assistant-2"),
    ]
    compression_event = {
        "id": "context-compression-1",
        "turn_id": "turn-1",
        "anchor_message_id": "tool-1",
        "timestamp": "2026-07-29 10:00:00",
        "estimated_tokens_before": 70_000,
        "estimated_tokens_after": 21_000,
        "estimated_cleared_tokens": 49_000,
        "compressed_messages": 12,
    }

    await service.persist_turn(
        conversation_id="conv-compression",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-1",
        messages=messages,
        reasoning_segments=[],
        context_compression_events=[compression_event],
    )
    await service.persist_turn(
        conversation_id="conv-compression",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-1",
        messages=messages,
        reasoning_segments=[],
        context_compression_events=[compression_event],
    )

    history = await service.load_history_with_recovery(conversation_id="conv-compression", user_id="user-1")
    assert [event["event_type"] for event in history["history_events"]] == [
        "user_message",
        "assistant_message",
        "tool_result",
        "context_compression",
        "assistant_message",
    ]
    assert history["history_events"][3]["payload"] == compression_event


@pytest.mark.asyncio
async def test_repeated_turn_snapshot_updates_existing_message(conversation_event_service):
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-snapshot", "user-1")
    user_message = HumanMessage(content="持续输出", id="turn-snapshot")

    await service.persist_user_message(
        conversation_id="conv-snapshot",
        user_id="user-1",
        operator_name="用户一",
        message=user_message,
    )
    await service.persist_turn(
        conversation_id="conv-snapshot",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-snapshot",
        messages=[user_message, AIMessage(content="第一阶段", id="assistant-snapshot")],
        reasoning_segments=[],
    )
    await service.persist_turn(
        conversation_id="conv-snapshot",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-snapshot",
        messages=[user_message, AIMessage(content="第一阶段的完整回答", id="assistant-snapshot")],
        reasoning_segments=[],
    )

    history = await service.load_history_with_recovery(conversation_id="conv-snapshot", user_id="user-1")

    assert [event["event_type"] for event in history["history_events"]] == [
        "user_message",
        "assistant_message",
    ]
    assert history["messages"][-1]["content"] == "第一阶段的完整回答"


async def _create_run(get_session, conversation_id: str, run_id: str, owner_token: str) -> None:
    async with get_session() as session:
        now = datetime.now()
        session.add(
            ChatDataAgentInbox(
                conversation_id=conversation_id,
                user_id="user-1",
                run_id=run_id,
                owner_token=owner_token,
                message_id=run_id,
                record_type="run",
                record_content=json.dumps({"turn_id": "turn-checkpoint"}, ensure_ascii=False),
                record_status="active",
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by="user-1",
                create_by_name="用户一",
                update_by="user-1",
                update_by_name="用户一",
            )
        )


async def _update_run_content(get_session, run_id: str, content: dict, *, status: str = "active") -> None:
    async with get_session() as session:
        run = await session.scalar(select(ChatDataAgentInbox).where(ChatDataAgentInbox.run_id == run_id))
        run.record_content = json.dumps(content, ensure_ascii=False)
        run.record_status = status


@pytest.mark.asyncio
async def test_turn_checkpoint_is_committed_with_history(conversation_event_service):
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-checkpoint", "user-1")
    await _create_run(get_session, "conv-checkpoint", "run-checkpoint", "owner-1")
    user = HumanMessage(content="检查恢复", id="turn-checkpoint")

    checkpoint = await service.persist_turn_with_checkpoint(
        conversation_id="conv-checkpoint",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-checkpoint",
        messages=[user, AIMessage(content="已保存", id="assistant-checkpoint")],
        reasoning_segments=[],
        run_id="run-checkpoint",
        owner_token="owner-1",
        stream_id="1787629922140-7",
    )

    assert checkpoint == {
        "run_id": "run-checkpoint",
        "turn_id": "turn-checkpoint",
        "history_sequence_no": 2,
        "stream_id": "1787629922140-7",
    }
    async with get_session() as session:
        rows = list((await session.scalars(select(ChatDataConversationEvent))).all())
        run = await session.scalar(select(ChatDataAgentInbox).where(ChatDataAgentInbox.run_id == "run-checkpoint"))
    assert len(rows) == 2
    assert json.loads(run.record_content)["history_checkpoint"] == checkpoint


@pytest.mark.asyncio
async def test_checkpoint_failure_rolls_back_history(conversation_event_service):
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-checkpoint-rollback", "user-1")
    await _create_run(get_session, "conv-checkpoint-rollback", "run-rollback", "owner-1")
    user = HumanMessage(content="不要丢失", id="turn-rollback")

    with pytest.raises(AgentRunLeaseLostError):
        await service.persist_turn_with_checkpoint(
            conversation_id="conv-checkpoint-rollback",
            user_id="user-1",
            operator_name="用户一",
            turn_id="turn-rollback",
            messages=[user, AIMessage(content="不应提交", id="assistant-rollback")],
            reasoning_segments=[],
            run_id="run-rollback",
            owner_token="old-owner",
            stream_id="1787629922140-7",
        )

    async with get_session() as session:
        rows = list((await session.scalars(select(ChatDataConversationEvent))).all())
        run = await session.scalar(select(ChatDataAgentInbox).where(ChatDataAgentInbox.run_id == "run-rollback"))
    assert rows == []
    assert "history_checkpoint" not in json.loads(run.record_content)


@pytest.mark.asyncio
async def test_checkpoint_is_not_written_without_redis_cursor(conversation_event_service):
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-no-cursor", "user-1")
    await _create_run(get_session, "conv-no-cursor", "run-no-cursor", "owner-1")
    user = HumanMessage(content="只写历史", id="turn-no-cursor")

    assert (
        await service.persist_turn_with_checkpoint(
            conversation_id="conv-no-cursor",
            user_id="user-1",
            operator_name="用户一",
            turn_id="turn-no-cursor",
            messages=[user, AIMessage(content="历史已写", id="assistant-no-cursor")],
            reasoning_segments=[],
            run_id="run-no-cursor",
            owner_token="owner-1",
            stream_id=None,
        )
        is None
    )
    async with get_session() as session:
        run = await session.scalar(select(ChatDataAgentInbox).where(ChatDataAgentInbox.run_id == "run-no-cursor"))
    assert "history_checkpoint" not in json.loads(run.record_content)


@pytest.mark.asyncio
async def test_checkpoint_requires_published_redis_cursor_and_worker_owner(conversation_event_service):
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-invalid-cursor", "user-1")
    await _create_run(get_session, "conv-invalid-cursor", "run-invalid-cursor", "owner-1")
    user = HumanMessage(content="非法游标", id="turn-invalid-cursor")

    assert (
        await service.persist_turn_with_checkpoint(
            conversation_id="conv-invalid-cursor",
            user_id="user-1",
            operator_name="用户一",
            turn_id="turn-invalid-cursor",
            messages=[user, AIMessage(content="历史仍可保存", id="assistant-invalid-cursor")],
            reasoning_segments=[],
            run_id="run-invalid-cursor",
            owner_token="owner-1",
            stream_id="0-0",
        )
        is None
    )

    async with get_session() as session:
        rows = list((await session.scalars(select(ChatDataConversationEvent))).all())
        run = await session.scalar(select(ChatDataAgentInbox).where(ChatDataAgentInbox.run_id == "run-invalid-cursor"))
    assert len(rows) == 2
    assert "history_checkpoint" not in json.loads(run.record_content)

    with pytest.raises(AgentRunLeaseLostError):
        await service.persist_turn_with_checkpoint(
            conversation_id="conv-invalid-cursor",
            user_id="user-1",
            operator_name="用户一",
            turn_id="turn-invalid-cursor",
            messages=[user, AIMessage(content="旧 Worker 不得写入", id="assistant-old-owner")],
            reasoning_segments=[],
            run_id="run-invalid-cursor",
            owner_token="old-owner",
            stream_id=None,
        )


@pytest.mark.asyncio
async def test_history_recovery_returns_only_valid_checkpoint_for_active_run(conversation_event_service):
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-recovery", "user-1")
    await _create_run(get_session, "conv-recovery", "run-recovery", "owner-1")
    await _update_run_content(
        get_session,
        "run-recovery",
        {
            "turn_id": "turn-recovery",
            "stream_started_wall_ms": 1787629922000,
            "history_checkpoint": {
                "run_id": "run-recovery",
                "turn_id": "turn-recovery",
                "history_sequence_no": 2,
                "stream_id": "1787629922140-7",
            },
        },
    )

    history = await service.load_history_with_recovery(
        conversation_id="conv-recovery",
        user_id="user-1",
    )

    assert history["active_run"]["run_id"] == "run-recovery"
    assert history["active_run"]["stream_started_wall_ms"] == 1787629922000
    assert history["active_run"]["history_checkpoint"]["stream_id"] == "1787629922140-7"

    await _update_run_content(
        get_session,
        "run-recovery",
        {
            "turn_id": "turn-recovery",
            "history_checkpoint": {
                "run_id": "another-run",
                "stream_id": "not-a-stream-id",
            },
        },
        status="finished",
    )
    finished_history = await service.load_history_with_recovery(
        conversation_id="conv-recovery",
        user_id="user-1",
    )
    assert finished_history["active_run"] is None


@pytest.mark.asyncio
async def test_history_recovery_hides_malformed_checkpoint_on_active_run(conversation_event_service):
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-malformed-recovery", "user-1")
    await _create_run(get_session, "conv-malformed-recovery", "run-malformed-recovery", "owner-1")
    await _update_run_content(
        get_session,
        "run-malformed-recovery",
        {
            "turn_id": "turn-malformed-recovery",
            "history_checkpoint": {
                "run_id": "run-malformed-recovery",
                "history_sequence_no": 3,
                "stream_id": "not-a-redis-id",
            },
        },
    )

    history = await service.load_history_with_recovery(
        conversation_id="conv-malformed-recovery",
        user_id="user-1",
    )

    assert history["active_run"]["run_id"] == "run-malformed-recovery"
    assert history["active_run"]["history_checkpoint"] is None


@pytest.mark.asyncio
async def test_checkpoint_rejects_run_that_finished_before_snapshot(conversation_event_service):
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-finished-checkpoint", "user-1")
    await _create_run(get_session, "conv-finished-checkpoint", "run-finished-checkpoint", "owner-1")
    await _update_run_content(
        get_session,
        "run-finished-checkpoint",
        {"turn_id": "turn-finished-checkpoint"},
        status="finished",
    )

    with pytest.raises(AgentRunLeaseLostError):
        await service.persist_turn_with_checkpoint(
            conversation_id="conv-finished-checkpoint",
            user_id="user-1",
            operator_name="用户一",
            turn_id="turn-finished-checkpoint",
            messages=[
                HumanMessage(content="迟到的写入", id="turn-finished-checkpoint"),
                AIMessage(content="不能追加", id="assistant-finished-checkpoint"),
            ],
            reasoning_segments=[],
            run_id="run-finished-checkpoint",
            owner_token="owner-1",
            stream_id="1787629922140-7",
        )

    async with get_session() as session:
        rows = list((await session.scalars(select(ChatDataConversationEvent))).all())
    assert rows == []


@pytest.mark.asyncio
async def test_checkpoint_does_not_move_backwards(conversation_event_service):
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-cursor-order", "user-1")
    await _create_run(get_session, "conv-cursor-order", "run-cursor-order", "owner-1")
    first_user = HumanMessage(content="第一步", id="turn-cursor-order")

    first = await service.persist_turn_with_checkpoint(
        conversation_id="conv-cursor-order",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-cursor-order",
        messages=[first_user, AIMessage(content="第一版", id="assistant-cursor-1")],
        reasoning_segments=[],
        run_id="run-cursor-order",
        owner_token="owner-1",
        stream_id="1787629922140-7",
    )
    second = await service.persist_turn_with_checkpoint(
        conversation_id="conv-cursor-order",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-cursor-order",
        messages=[first_user, AIMessage(content="第二版", id="assistant-cursor-2")],
        reasoning_segments=[],
        run_id="run-cursor-order",
        owner_token="owner-1",
        stream_id="1787629922139-9",
    )

    assert second == first
    async with get_session() as session:
        run = await session.scalar(select(ChatDataAgentInbox).where(ChatDataAgentInbox.run_id == "run-cursor-order"))
    assert json.loads(run.record_content)["history_checkpoint"] == first


@pytest.mark.asyncio
async def test_event_append_retries_connection_errors_and_propagates_final_failure(monkeypatch):
    class RetryDao:
        def __init__(self, failures):
            self.failures = failures
            self.calls = 0

        async def append_events(self, **_kwargs):
            self.calls += 1
            if self.calls <= self.failures:
                raise OperationalError(None, None, RuntimeError(2006, "server gone away"))
            return []

    async def skip_retry_delay(_delay):
        return None

    monkeypatch.setattr("apps.chat_data_app.agent.run.conversation_event_service.asyncio.sleep", skip_retry_delay)
    message = HumanMessage(content="重试写入", id="turn-retry")
    retry_dao = RetryDao(failures=2)
    await ConversationEventService(retry_dao).persist_user_message(
        conversation_id="conv-retry",
        user_id="user-1",
        operator_name="用户一",
        message=message,
    )
    assert retry_dao.calls == 3

    failed_dao = RetryDao(failures=3)
    with pytest.raises(OperationalError):
        await ConversationEventService(failed_dao).persist_user_message(
            conversation_id="conv-failed",
            user_id="user-1",
            operator_name="用户一",
            message=message,
        )
    assert failed_dao.calls == 3


@pytest.mark.asyncio
async def test_token_usage_failure_does_not_clear_visible_history():
    class EventRow:
        payload = '{"id":"turn-1","role":"user","content":"保留消息","timestamp":""}'
        sequence_no = 1
        turn_id = "turn-1"
        event_type = "user_message"

    @asynccontextmanager
    async def _fake_session():
        yield object()

    class TokenFailureDao:
        def session_context(self):
            return _fake_session()

        async def list_visible_event_window_in_session(self, _session, **_kwargs):
            return [EventRow()], False

        async def get_latest_token_usage_payload_in_session(self, _session, **_kwargs):
            raise RuntimeError("token usage unavailable")

        async def get_active_run_snapshot(self, _session, _conversation_id, _user_id):
            return None

    dao = TokenFailureDao()
    history = await ConversationEventService(dao, dao).load_history_with_recovery(
        conversation_id="conv-1",
        user_id="user-1",
    )

    assert history["messages"][0]["content"] == "保留消息"
    assert history["token_usage"] is None
    assert history["active_run"] is None


def test_serialized_messages_carry_execution_timing_only_when_stamped():
    """AI/工具消息盖了起止时间才写入 payload；耗时由展示端相减得出。"""
    from apps.chat_data_app.agent.run.conversation_event_service import (
        _serialize_ai_message,
        _serialize_tool_message,
    )

    stamped_ai = _serialize_ai_message(
        AIMessage(
            content="分析完成",
            id="msg-ai-1",
            additional_kwargs={"started_at_ms": 1000, "ended_at_ms": 3500},
        )
    )
    assert stamped_ai["started_at_ms"] == 1000
    assert stamped_ai["ended_at_ms"] == 3500

    plain_ai = _serialize_ai_message(AIMessage(content="无耗时", id="msg-ai-2"))
    assert "started_at_ms" not in plain_ai
    assert "ended_at_ms" not in plain_ai

    stamped_tool = _serialize_tool_message(
        ToolMessage(
            content="工具结果",
            id="msg-tool-1",
            tool_call_id="call-1",
            additional_kwargs={"started_at_ms": 4000, "ended_at_ms": 4200},
        )
    )
    assert stamped_tool["started_at_ms"] == 4000
    assert stamped_tool["ended_at_ms"] == 4200

    invalid_tool = _serialize_tool_message(
        ToolMessage(
            content="非法时间戳",
            id="msg-tool-2",
            tool_call_id="call-2",
            additional_kwargs={"started_at_ms": "bad", "ended_at_ms": None},
        )
    )
    assert "started_at_ms" not in invalid_tool
    assert "ended_at_ms" not in invalid_tool


def test_human_message_payload_strips_blocks_and_carries_vision_attachments():
    """保险丝：blocks 里的图片降级为占位文本；vision_attachments 标记随行。"""
    from apps.chat_data_app.agent.run.conversation_event_service import (
        _serialize_human_message,
    )

    blocked = _serialize_human_message(
        HumanMessage(
            id="turn-img",
            content=[
                {"type": "text", "text": "看这张图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBBB"}},
            ],
            additional_kwargs={"vision_attachments": ["/mnt/user-data/uploads/a.png"]},
        )
    )
    assert blocked["content"] == "看这张图\n[图片内容已省略，共 2 张]"
    assert blocked["vision_attachments"] == ["/mnt/user-data/uploads/a.png"]
    # payload 全文不出现 base64
    assert "base64" not in str(blocked)

    pure_image = _serialize_human_message(
        HumanMessage(
            content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,CCCC"}}],
        )
    )
    assert pure_image["content"] == "[图片内容已省略，共 1 张]"

    plain = _serialize_human_message(HumanMessage(content="普通文本消息"))
    assert plain["content"] == "普通文本消息"
    assert "vision_attachments" not in plain


def test_tool_message_payload_carries_subagent_trail_snapshot():
    """task 工具的子智能体过程快照随 tool_result 事件持久化（历史回放还原用）。"""
    from apps.chat_data_app.agent.run.conversation_event_service import (
        _serialize_tool_message,
    )

    trail = [
        {
            "kind": "message",
            "id": "m1",
            "content": "开始计算",
            "truncated": False,
            "thinking": "需要精确计算",
            "thinkingTruncated": False,
        },
        {
            "kind": "tool",
            "id": "inner-1",
            "tool_name": "calculator_tool",
            "args": {"expression": "1 + 1"},
            "result": "2",
            "status": "completed",
        },
    ]
    payload = _serialize_tool_message(
        ToolMessage(
            content="1 + 1 = 2",
            tool_call_id="call-task-1",
            name="task",
            additional_kwargs={"subagent_trail": trail},
        )
    )
    assert payload["subagent_trail"] == trail
    assert payload["tool_name"] == "task"

    # 无快照（普通工具 / 空快照）不出现该字段
    plain = _serialize_tool_message(ToolMessage(content="结果", tool_call_id="call-2", name="calculator_tool"))
    assert "subagent_trail" not in plain
    empty = _serialize_tool_message(
        ToolMessage(
            content="结论",
            tool_call_id="call-3",
            name="task",
            additional_kwargs={"subagent_trail": []},
        )
    )
    assert "subagent_trail" not in empty


def test_tool_message_payload_carries_subagent_context_usage():
    """task 工具的上下文用量快照随 tool_result 事件持久化（历史回放还原徽标用）。"""
    from apps.chat_data_app.agent.run.conversation_event_service import (
        _serialize_tool_message,
    )

    usage = {"used_tokens": 4321, "threshold_tokens": 70000, "percent": 6}
    payload = _serialize_tool_message(
        ToolMessage(
            content="盘点结论",
            tool_call_id="call-task-1",
            name="task",
            additional_kwargs={"subagent_context_usage": usage},
        )
    )
    assert payload["subagent_context_usage"] == usage

    # 无快照 / 非法形态不出现该字段
    plain = _serialize_tool_message(ToolMessage(content="结果", tool_call_id="call-2", name="calculator_tool"))
    assert "subagent_context_usage" not in plain
    invalid = _serialize_tool_message(
        ToolMessage(
            content="结论",
            tool_call_id="call-3",
            name="task",
            additional_kwargs={"subagent_context_usage": "not-a-dict"},
        )
    )
    assert "subagent_context_usage" not in invalid


async def _persist_qa_turn(
    service,
    *,
    conversation_id: str,
    turn_id: str,
    question: str,
    answer: str,
    extra_answers: tuple[str, ...] = (),
    user_id: str = "user-1",
) -> None:
    """写入一轮「提问 + 回答」，默认产生两条事件；extra_answers 用于拉长单轮。"""
    user = HumanMessage(content=question, id=turn_id)
    await service.persist_user_message(
        conversation_id=conversation_id,
        user_id=user_id,
        operator_name="用户一",
        message=user,
    )
    replies = [AIMessage(content=answer, id=f"assistant-{turn_id}")]
    replies.extend(
        AIMessage(content=text, id=f"assistant-{turn_id}-{index}") for index, text in enumerate(extra_answers, start=1)
    )
    await service.persist_turn(
        conversation_id=conversation_id,
        user_id=user_id,
        operator_name="用户一",
        turn_id=turn_id,
        messages=[user, *replies],
        reasoning_segments=[],
    )


@pytest.mark.asyncio
async def test_history_window_pages_backwards_until_exhausted(conversation_event_service):
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-page", "user-1")
    for index in range(1, 4):
        await _persist_qa_turn(
            service,
            conversation_id="conv-page",
            turn_id=f"turn-{index}",
            question=f"问题{index}",
            answer=f"回答{index}",
        )

    first_page = await service.load_history_with_recovery(
        conversation_id="conv-page",
        user_id="user-1",
        limit=2,
    )
    assert first_page["has_more"] is True
    assert [event["payload"]["content"] for event in first_page["history_events"]] == ["问题3", "回答3"]
    assert first_page["earliest_sequence_no"] == first_page["history_events"][0]["sequence_no"]

    second_page = await service.load_history_with_recovery(
        conversation_id="conv-page",
        user_id="user-1",
        limit=2,
        before_sequence=first_page["earliest_sequence_no"],
    )
    assert second_page["has_more"] is True
    assert [event["payload"]["content"] for event in second_page["history_events"]] == ["问题2", "回答2"]

    last_page = await service.load_history_with_recovery(
        conversation_id="conv-page",
        user_id="user-1",
        limit=2,
        before_sequence=second_page["earliest_sequence_no"],
    )
    assert last_page["has_more"] is False
    assert [event["payload"]["content"] for event in last_page["history_events"]] == ["问题1", "回答1"]

    merged = last_page["history_events"] + second_page["history_events"] + first_page["history_events"]
    assert [event["event_type"] for event in merged] == ["user_message", "assistant_message"] * 3
    assert [event["sequence_no"] for event in merged] == sorted(event["sequence_no"] for event in merged)

    # 游标落在最早事件之前（例如用户在翻页途中被清空历史）：空页且判定为没有更早
    empty_page = await service.load_history_with_recovery(
        conversation_id="conv-page",
        user_id="user-1",
        limit=2,
        before_sequence=1,
    )
    assert empty_page["history_events"] == []
    assert empty_page["has_more"] is False
    assert empty_page["earliest_sequence_no"] is None


@pytest.mark.asyncio
async def test_history_window_extends_leading_turn_boundary(conversation_event_service):
    """窗口截断处落在轮次中间时，必须补齐该轮更早的事件。"""
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-boundary", "user-1")
    for index in range(1, 4):
        await _persist_qa_turn(
            service,
            conversation_id="conv-boundary",
            turn_id=f"turn-{index}",
            question=f"问题{index}",
            answer=f"回答{index}",
        )

    # 尾部 3 条正好从 turn-2 的回答开始，属于切断轮次的情形
    page = await service.load_history_with_recovery(
        conversation_id="conv-boundary",
        user_id="user-1",
        limit=3,
    )
    assert [event["payload"]["content"] for event in page["history_events"]] == [
        "问题2",
        "回答2",
        "问题3",
        "回答3",
    ]
    assert page["has_more"] is True

    older = await service.load_history_with_recovery(
        conversation_id="conv-boundary",
        user_id="user-1",
        limit=3,
        before_sequence=page["earliest_sequence_no"],
    )
    assert [event["payload"]["content"] for event in older["history_events"]] == ["问题1", "回答1"]
    assert older["has_more"] is False


@pytest.mark.asyncio
async def test_history_window_finishes_single_turn_longer_than_limit(conversation_event_service):
    """单轮事件数超过 limit 时，补齐轮次后应判定为没有更早，避免多翻一次空页。"""
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-long-turn", "user-1")
    await _persist_qa_turn(
        service,
        conversation_id="conv-long-turn",
        turn_id="turn-1",
        question="长轮次问题",
        answer="回答1",
        extra_answers=("回答2", "回答3", "回答4"),
    )

    page = await service.load_history_with_recovery(
        conversation_id="conv-long-turn",
        user_id="user-1",
        limit=2,
    )
    assert [event["payload"]["content"] for event in page["history_events"]] == [
        "长轮次问题",
        "回答1",
        "回答2",
        "回答3",
        "回答4",
    ]
    assert page["has_more"] is False


@pytest.mark.asyncio
async def test_history_turn_window_returns_complete_turns_only(conversation_event_service):
    """按轮次分页：每页完整返回 turn_id 的所有事件，不按事件条数截断。"""
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-turn-page", "user-1")
    for index in range(1, 5):
        await _persist_qa_turn(
            service,
            conversation_id="conv-turn-page",
            turn_id=f"turn-{index}",
            question=f"问题{index}",
            answer=f"回答{index}",
        )

    first = await service.load_history_with_recovery(
        conversation_id="conv-turn-page",
        user_id="user-1",
        limit_turns=2,
    )
    assert [event["payload"]["content"] for event in first["history_events"]] == ["问题3", "回答3", "问题4", "回答4"]
    assert {event["turn_id"] for event in first["history_events"]} == {"turn-3", "turn-4"}
    assert first["has_more_turns"] is True
    assert first["earliest_turn_sequence"] == 6

    older = await service.load_history_with_recovery(
        conversation_id="conv-turn-page",
        user_id="user-1",
        limit_turns=2,
        before_turn_sequence=first["earliest_turn_sequence"],
    )
    assert [event["payload"]["content"] for event in older["history_events"]] == ["问题1", "回答1", "问题2", "回答2"]
    assert older["has_more_turns"] is False


@pytest.mark.asyncio
async def test_history_turn_window_keeps_long_turn_complete(conversation_event_service):
    """单轮事件数超过普通眼页大小也必须整轮返回，不能切断。"""
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-turn-long", "user-1")
    await _persist_qa_turn(
        service,
        conversation_id="conv-turn-long",
        turn_id="turn-long",
        question="长轮次问题",
        answer="回答1",
        extra_answers=("回答2", "回答3", "回答4"),
    )

    page = await service.load_history_with_recovery(
        conversation_id="conv-turn-long",
        user_id="user-1",
        limit_turns=5,
    )
    assert [event["payload"]["content"] for event in page["history_events"]] == [
        "长轮次问题",
        "回答1",
        "回答2",
        "回答3",
        "回答4",
    ]
    assert page["has_more_turns"] is False


@pytest.mark.asyncio
async def test_history_window_returns_full_history_when_shorter_than_limit(conversation_event_service):
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-short", "user-1")
    await _persist_qa_turn(
        service,
        conversation_id="conv-short",
        turn_id="turn-1",
        question="唯一问题",
        answer="唯一回答",
    )

    page = await service.load_history_with_recovery(
        conversation_id="conv-short",
        user_id="user-1",
        limit=50,
    )
    assert [event["payload"]["content"] for event in page["history_events"]] == ["唯一问题", "唯一回答"]
    assert page["has_more"] is False

    # 未指定 limit 时保持旧行为：全量 + has_more 恒为 False
    full = await service.load_history_with_recovery(conversation_id="conv-short", user_id="user-1")
    assert len(full["history_events"]) == 2
    assert full["has_more"] is False


@pytest.mark.asyncio
async def test_history_window_stays_contiguous_across_interleaved_turns(conversation_event_service):
    """并发 run 让同一轮次的事件在序号上不连续时，分页不得漏掉夹在中间的事件。

    真实库里 turn-x 的提问会先落库（上一条 run 还没收尾），中间插进其它轮次的问答，
    turn-x 的回答最后才以更大的序号追加。旧实现把「尾部 limit 条」与「最早一条所属轮次的
    更早事件」两段并集返回，夹在两段之间的 turn-b / turn-c 事件既不在本页、又落在下一页
    游标之下，会被永久跳过（首屏因此出现「没有提问的回答」）。
    """
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-interleaved", "user-1")
    await _persist_qa_turn(
        service,
        conversation_id="conv-interleaved",
        turn_id="turn-a",
        question="问题A",
        answer="回答A",
    )
    await _persist_qa_turn(
        service,
        conversation_id="conv-interleaved",
        turn_id="turn-b",
        question="问题B",
        answer="回答B",
    )
    # turn-x 的提问先落库，对应的 run 还没结束
    await service.persist_user_message(
        conversation_id="conv-interleaved",
        user_id="user-1",
        operator_name="用户一",
        message=HumanMessage(content="问题X", id="turn-x"),
    )
    await _persist_qa_turn(
        service,
        conversation_id="conv-interleaved",
        turn_id="turn-c",
        question="问题C",
        answer="回答C",
    )
    # turn-x 的回答等 run 收尾才追加，序号落在 turn-c 之后
    await _persist_qa_turn(
        service,
        conversation_id="conv-interleaved",
        turn_id="turn-x",
        question="问题X",
        answer="回答X1",
        extra_answers=("回答X2",),
    )

    baseline = await service.load_history_with_recovery(conversation_id="conv-interleaved", user_id="user-1")
    expected_sequences = [event["sequence_no"] for event in baseline["history_events"]]
    assert len(expected_sequences) == 9

    pages = []
    cursor = None
    for _ in range(10):
        page = await service.load_history_with_recovery(
            conversation_id="conv-interleaved",
            user_id="user-1",
            limit=2,
            before_sequence=cursor,
        )
        pages.append(page)
        if not page["has_more"]:
            break
        cursor = page["earliest_sequence_no"]
    else:
        pytest.fail("分页未能在 10 页内取尽历史")

    assert len(pages) == 3
    for page in pages:
        sequences = [event["sequence_no"] for event in page["history_events"]]
        # 每页都是连续序号区间，不会在两段之间留下空洞
        assert sequences == list(range(sequences[0], sequences[-1] + 1))
        # 每页第一行都是提问（轮次起点）
        assert page["history_events"][0]["event_type"] == "user_message"

    merged = [event for page in reversed(pages) for event in page["history_events"]]
    assert [event["sequence_no"] for event in merged] == expected_sequences
    assert [event["payload"]["content"] for event in merged] == [
        "问题A",
        "回答A",
        "问题B",
        "回答B",
        "问题X",
        "问题C",
        "回答C",
        "回答X1",
        "回答X2",
    ]


@pytest.mark.asyncio
async def test_history_window_closure_keeps_answer_with_its_question(conversation_event_service):
    """窗口起点必须收敛到轮次闭包起点，不能只取「尾部最早一条所属轮次的起点」。

    否则会出现「回答在本页、提问在上一页」的孤儿：turn-x 的回答被追加在 turn-b 之后，
    而尾部窗口最早一条属于 turn-b，起点停在 turn-b 的提问上，turn-x 的回答就被单拎到首屏。
    真实库 3/67 个会话如此（最长会话 503 条事件，提问 seq 221、回答 seq 346，首屏从 345 开窗）。
    """
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-orphan", "user-1")
    await _persist_qa_turn(
        service,
        conversation_id="conv-orphan",
        turn_id="turn-a",
        question="问题A",
        answer="回答A",
    )
    # turn-x 的提问先落库，对应的 run 还没结束
    await service.persist_user_message(
        conversation_id="conv-orphan",
        user_id="user-1",
        operator_name="用户一",
        message=HumanMessage(content="问题X", id="turn-x"),
    )
    await _persist_qa_turn(
        service,
        conversation_id="conv-orphan",
        turn_id="turn-b",
        question="问题B",
        answer="回答B",
    )
    # turn-x 的回答等 run 收尾才追加，序号落在 turn-b 之后
    await _persist_qa_turn(
        service,
        conversation_id="conv-orphan",
        turn_id="turn-x",
        question="问题X",
        answer="回答X1",
        extra_answers=("回答X2",),
    )

    # 尾部 3 条从 turn-b 的回答开始；仅按 turn-b 提问收窗会把 turn-x 的回答孤立在首屏
    page = await service.load_history_with_recovery(
        conversation_id="conv-orphan",
        user_id="user-1",
        limit=3,
    )
    assert [event["payload"]["content"] for event in page["history_events"]] == [
        "问题X",
        "问题B",
        "回答B",
        "回答X1",
        "回答X2",
    ]
    assert page["history_events"][0]["event_type"] == "user_message"
    assert page["has_more"] is True

    older = await service.load_history_with_recovery(
        conversation_id="conv-orphan",
        user_id="user-1",
        limit=3,
        before_sequence=page["earliest_sequence_no"],
    )
    assert [event["payload"]["content"] for event in older["history_events"]] == ["问题A", "回答A"]
    assert older["has_more"] is False


class _StubScalarSession:
    """只回应 session.scalar 的桩：按序吐出预设值，用尽后返回 None。"""

    def __init__(self, values):
        self._values = list(values)
        self.calls = 0

    async def scalar(self, _stmt):
        self.calls += 1
        return self._values.pop(0) if self._values else None


@pytest.mark.asyncio
async def test_window_cutoff_closure_stops_on_monotone_and_empty_results():
    """兜底：没有更早轮次可并入（或没有轮次标记）时起点保持不变，不会把窗口反向撑大。"""
    dao = ConversationEventDao()

    # 无轮次标记 / 没有事件位于起点之上：查询返回 None，起点原样返回
    session = _StubScalarSession([None])
    assert await dao._expand_cutoff_to_turn_closure(session, conditions=[], cutoff=42) == 42
    assert session.calls == 1

    # 收敛过程：42 → 30（并入更早轮次）→ 40 不小于当前起点，立即停止，不再回弹
    session = _StubScalarSession([30, 40])
    assert await dao._expand_cutoff_to_turn_closure(session, conditions=[], cutoff=42) == 30
    assert session.calls == 2

    # 异常数据下起点一直下降：迭代次数被上限截断，不会无限查库
    session = _StubScalarSession(range(41, 25, -1))
    assert await dao._expand_cutoff_to_turn_closure(session, conditions=[], cutoff=42) == 26
    assert session.calls == ConversationEventDao._TURN_CLOSURE_MAX_ROUNDS


@pytest.mark.asyncio
async def test_persist_user_message_insert_only_does_not_overwrite_existing(conversation_event_service):
    """提前落库只能补缺失行：运行期已写入的完整内容不能被截断版覆盖。"""
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-insert-only", "user-1")

    full = HumanMessage(content="完整用户消息（带附件占位）", id="turn-full", additional_kwargs={"timestamp": "t1"})
    await service.persist_user_message(
        conversation_id="conv-insert-only", user_id="user-1", operator_name="用户一", message=full
    )

    truncated = HumanMessage(content="截断版", id="turn-full", additional_kwargs={"timestamp": "t2"})
    await service.persist_user_message(
        conversation_id="conv-insert-only",
        user_id="user-1",
        operator_name="用户一",
        message=truncated,
        insert_only=True,
    )

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(ChatDataConversationEvent).where(
                        ChatDataConversationEvent.conversation_id == "conv-insert-only",
                        ChatDataConversationEvent.event_type == "user_message",
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1
    assert "完整用户消息" in rows[0].payload
    assert "截断版" not in rows[0].payload

    # insert_only 在没有既有行时仍要正常插入
    fresh = HumanMessage(content="新消息", id="turn-new", additional_kwargs={"timestamp": "t3"})
    await service.persist_user_message(
        conversation_id="conv-insert-only",
        user_id="user-1",
        operator_name="用户一",
        message=fresh,
        insert_only=True,
    )
    async with get_session() as session:
        count = len(
            list(
                (
                    await session.execute(
                        select(ChatDataConversationEvent).where(
                            ChatDataConversationEvent.conversation_id == "conv-insert-only",
                            ChatDataConversationEvent.event_type == "user_message",
                        )
                    )
                )
                .scalars()
                .all()
            )
        )
    assert count == 2


# ============ 纯函数与异常分支覆盖（防回归 + 覆盖率余量） ============


def test_compare_stream_ids_malformed_falls_back():
    """非法 stream_id 走字符串兜底键，不抛异常。"""
    from apps.chat_data_app.agent.run import conversation_event_service as ces

    assert ces._compare_stream_ids("1-2", "abc") == 1
    assert ces._compare_stream_ids("abc", "1-2") == -1
    assert ces._compare_stream_ids("abc", "abc") == 0
    assert ces._compare_stream_ids("5-x", "5-y") == -1  # ValueError 分支：按原串比较


@pytest.mark.asyncio
async def test_persist_user_message_skips_missing_id(conversation_event_service):
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-no-id", "user-1")
    await service.persist_user_message(
        conversation_id="conv-no-id",
        user_id="user-1",
        operator_name="用户一",
        message=HumanMessage(content="无 ID 消息"),
    )
    async with _get_session() as session:
        count = len(
            list(
                (
                    await session.execute(
                        select(ChatDataConversationEvent).where(
                            ChatDataConversationEvent.conversation_id == "conv-no-id"
                        )
                    )
                ).scalars()
            )
        )
    assert count == 0


@pytest.mark.asyncio
async def test_persist_turn_skips_empty_messages(conversation_event_service):
    service, _create_thread, _get_session = conversation_event_service
    # 空 messages → events 为空，直接返回，不触库
    await service.persist_turn(
        conversation_id="conv-empty-turn",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-1",
        messages=[],
        reasoning_segments=[],
    )


@pytest.mark.asyncio
async def test_append_events_raises_on_non_connection_error(conversation_event_service):
    service, _create_thread, _get_session = conversation_event_service

    async def boom(**_kwargs):
        raise RuntimeError("logic broken")

    service._dao.append_events = boom
    with pytest.raises(RuntimeError):
        await service._append_events(
            conversation_id="conv-x",
            user_id="user-1",
            operator_name="用户一",
            events=[{"event_key": "user:t1", "turn_id": "t1", "event_type": "user_message", "payload": "{}"}],
        )


@pytest.mark.asyncio
async def test_append_events_retries_on_connection_error(monkeypatch, conversation_event_service):
    from apps.chat_data_app.agent.run import conversation_event_service as ces

    monkeypatch.setattr(ces, "EVENT_APPEND_RETRY_DELAY_SECONDS", 0)
    service, _create_thread, _get_session = conversation_event_service
    calls = []

    async def flaky(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise OperationalError("stmt", {}, Exception(2013, "lost connection"))
        return None

    service._dao.append_events = flaky
    await service._append_events(
        conversation_id="conv-x",
        user_id="user-1",
        operator_name="用户一",
        events=[{"event_key": "user:t1", "turn_id": "t1", "event_type": "user_message", "payload": "{}"}],
    )
    assert len(calls) == 2


def _run(run_id: str, checkpoint: dict | None) -> object:
    import json as _json

    content: dict = {}
    if checkpoint is not None:
        content["history_checkpoint"] = checkpoint
    return SimpleNamespace(run_id=run_id, record_content=_json.dumps(content, ensure_ascii=False))


@pytest.mark.asyncio
async def test_update_history_checkpoint_lease_lost_on_owner_change(conversation_event_service):
    service, _create_thread, _get_session = conversation_event_service
    run = _run("run-old", {"run_id": "run-other", "stream_id": "1-0"})
    with pytest.raises(AgentRunLeaseLostError):
        await service._update_history_checkpoint(
            run_id="run-old",
            turn_id="t1",
            history_sequence_no=3,
            stream_id="2-0",
            run=run,
        )


@pytest.mark.asyncio
async def test_update_history_checkpoint_keeps_newer_stream_id(conversation_event_service):
    service, _create_thread, _get_session = conversation_event_service
    run = _run("run-1", {"run_id": "run-1", "stream_id": "9-0"})
    checkpoint = await service._update_history_checkpoint(
        run_id="run-1",
        turn_id="t1",
        history_sequence_no=3,
        stream_id="5-0",  # 比 previous 小：水位不回退
        run=run,
    )
    assert checkpoint is not None and checkpoint["stream_id"] == "9-0"


@pytest.mark.asyncio
async def test_get_first_user_message_db_error_returns_none(conversation_event_service):
    service, _create_thread, _get_session = conversation_event_service

    async def boom(**_kwargs):
        raise RuntimeError("db down")

    service._dao.get_first_user_message_payload = boom
    assert await service.get_first_user_message(conversation_id="conv-x", user_id="user-1") is None


def test_turn_message_range_stops_at_next_human():
    from apps.chat_data_app.agent.run.conversation_event_service import _turn_message_range

    messages = [
        HumanMessage(content="q1", id="t1"),
        AIMessage(content="a1"),
        ToolMessage(content="r", tool_call_id="c1"),
        HumanMessage(content="q2", id="t2"),
    ]
    assert _turn_message_range(messages, "t1") == (0, 3)
    assert _turn_message_range(messages, "t2") == (3, 4)
    assert _turn_message_range(messages, "missing") is None


def test_partition_compression_events_unanchored():
    from apps.chat_data_app.agent.run.conversation_event_service import _partition_compression_events

    anchored = {"id": "e1", "anchor_message_id": "m1"}
    unanchored = {"id": "e2", "anchor_message_id": ""}
    by_anchor, unanchored_events = _partition_compression_events([anchored, unanchored])
    assert by_anchor == {"m1": [anchored]}
    assert unanchored_events == [unanchored]


def test_build_turn_events_flushes_unanchored_and_leftover_anchors():
    from apps.chat_data_app.agent.run.conversation_event_service import _build_turn_events

    turn_id = "t1"
    ai = AIMessage(content="a", id="ai-1")
    events = _build_turn_events(
        turn_id=turn_id,
        messages=[HumanMessage(content="q", id=turn_id), ai],
        reasoning_segments=[],
        context_compression_events=[
            {"id": "e-unanchored", "turn_id": turn_id},  # 未锚定：随提问后立刻落
            {"id": "e-ai", "turn_id": turn_id, "anchor_message_id": "ai-1"},  # 锚定到 AI
            {"id": "e-orphan", "turn_id": turn_id, "anchor_message_id": "ghost"},  # 锚点缺失：轮末冲刷
        ],
    )
    keys = [event["event_key"] for event in events]
    assert "context_compression:e-unanchored" in keys
    assert "context_compression:e-ai" in keys
    assert "context_compression:e-orphan" in keys


def test_build_turn_events_skips_human_message_without_id():
    from apps.chat_data_app.agent.run.conversation_event_service import _build_turn_events

    turn_id = "t1"
    events = _build_turn_events(
        turn_id=turn_id,
        messages=[HumanMessage(content="q", id=turn_id), HumanMessage(content="无 ID")],
        reasoning_segments=[],
    )
    user_keys = [event["event_key"] for event in events if event["event_type"] == "user_message"]
    assert user_keys == [f"user:{turn_id}"]


def test_normalize_reasoning_segments_filters_invalid():
    from apps.chat_data_app.agent.run.conversation_event_service import _normalize_reasoning_segments

    normalized = _normalize_reasoning_segments(
        [
            "not-a-dict",  # 非 dict
            {"content": "其它轮次", "reasoning_turn_id": "t-other"},  # turn 不匹配
            {"content": "   "},  # 空内容
            {"content": "有效", "tool_call_count": None},  # 缺 id：自动生成
        ],
        "t1",
    )
    assert len(normalized) == 1
    assert normalized[0]["id"] == "reasoning-t1-3"
    assert normalized[0]["tool_call_count"] == 0


def test_normalize_context_compression_events_filters_invalid():
    from apps.chat_data_app.agent.run.conversation_event_service import _normalize_context_compression_events

    valid = {"id": "e1", "turn_id": "t1", "estimated_tokens_before": "12"}
    normalized = _normalize_context_compression_events(
        [
            "not-a-dict",
            {"turn_id": "t1"},  # 无 id
            valid,
            {"id": "e1", "turn_id": "t1"},  # 重复 id
            {"id": "e2", "turn_id": "t-other"},  # 其它轮次
        ],
        "t1",
    )
    assert normalized == [
        {
            "id": "e1",
            "turn_id": "t1",
            "anchor_message_id": "",
            "timestamp": "",
            "estimated_tokens_before": 12,
            "estimated_tokens_after": 0,
            "estimated_cleared_tokens": 0,
            "compressed_messages": 0,
        }
    ]


def test_non_negative_int():
    from apps.chat_data_app.agent.run.conversation_event_service import _non_negative_int

    assert _non_negative_int("3") == 3
    assert _non_negative_int(None) == 0
    assert _non_negative_int(-5) == 0
    assert _non_negative_int("abc") == 0
    assert _non_negative_int(object()) == 0


def test_execution_timing_skips_invalid_values():
    from apps.chat_data_app.agent.run.conversation_event_service import _execution_timing

    message = SimpleNamespace(
        additional_kwargs={
            "started_at_ms": True,  # bool 显式跳过
            "ended_at_ms": None,
        }
    )
    assert _execution_timing(message) == {}
    message = SimpleNamespace(additional_kwargs={"started_at_ms": "bad", "ended_at_ms": -1, "extra": 1})
    assert _execution_timing(message) == {}
    message = SimpleNamespace(additional_kwargs={"started_at_ms": 100, "ended_at_ms": "200"})
    assert _execution_timing(message) == {"started_at_ms": 100, "ended_at_ms": 200}


def test_sanitize_user_message_content_mixed_blocks():
    from apps.chat_data_app.agent.run.conversation_event_service import _sanitize_user_message_content

    assert _sanitize_user_message_content("纯文本") == "纯文本"
    mixed = [
        123,  # 非 dict 块
        {"type": "image_url", "image_url": {"url": "data:..."}},
        {"type": "text", "text": "你好"},
    ]
    assert _sanitize_user_message_content(mixed) == "你好\n[图片内容已省略，共 1 张]"
    assert _sanitize_user_message_content([{"type": "image_url"}]) == "[图片内容已省略，共 1 张]"
    assert _sanitize_user_message_content([{"type": "other"}]) == "[非文本内容已省略]"


def test_serialize_ai_message_full_payload():
    from apps.chat_data_app.agent.run.conversation_event_service import _serialize_ai_message

    # langchain 会校验 tool_calls 元素形状，这里直接用 SimpleNamespace 构造非法混合列表
    message = SimpleNamespace(
        content="回答",
        id="ai-1",
        additional_kwargs={
            "timestamp": "t1",
            "generation_stats": {"first_token_ms": 120},
            "reasoning_content": "思考中",
        },
        tool_calls=[{"id": "c1", "name": "file_delete", "args": {"path": "a.txt"}}, "bad-item"],
        response_metadata=None,
    )
    payload = _serialize_ai_message(message)
    assert payload["generation_stats"] == {"first_token_ms": 120}
    assert payload["reasoning_content"] == "思考中"
    assert [call["name"] for call in payload["tool_calls"]] == ["file_delete"]


def test_extract_reasoning_content_sources():
    from apps.chat_data_app.agent.run.conversation_event_service import _extract_reasoning_content

    assert _extract_reasoning_content(SimpleNamespace(additional_kwargs=None, response_metadata=None)) == ""
    assert (
        _extract_reasoning_content(
            SimpleNamespace(additional_kwargs={}, response_metadata={"reasoning_text": "元数据思考"})
        )
        == "元数据思考"
    )
    assert _extract_reasoning_content(SimpleNamespace(additional_kwargs={"reasoning": ""}, response_metadata={})) == ""


def test_extract_text_content_blocks_and_attachment_lines():
    from apps.chat_data_app.agent.run.conversation_event_service import _extract_text_content

    assert _extract_text_content(123) == ""
    assert _extract_text_content("  ") == ""
    blocks = [
        {"type": "text", "text": "第一行"},
        {"type": "image_url"},
        {"type": "text", "text": "[attachment] 占位"},
        {"type": "text", "text": "第二行"},
    ]
    assert _extract_text_content(blocks) == "第一行\n第二行"
    assert _extract_text_content([{"type": "text", "text": "[attachment] x"}]) == ""


def test_build_turn_events_human_message_without_id_direct():
    """无 ID 的 HumanMessage 直接进入构造器时跳过（不落事件）。"""
    from apps.chat_data_app.agent.run.conversation_event_service import (
        _build_turn_events_human_message,
    )

    events: list[dict] = []
    _build_turn_events_human_message(HumanMessage(content="无 ID"), events, {}, [], "t1")
    assert events == []


def test_build_turn_events_includes_tool_message():
    from apps.chat_data_app.agent.run.conversation_event_service import _build_turn_events

    turn_id = "t1"
    events = _build_turn_events(
        turn_id=turn_id,
        messages=[
            HumanMessage(content="q", id=turn_id),
            AIMessage(content="a", id="ai-1", tool_calls=[]),
            ToolMessage(content="结果", tool_call_id="c1", name="file_delete", id="tool-1"),
        ],
        reasoning_segments=[],
    )
    keys = [event["event_key"] for event in events]
    assert "tool:tool-1" in keys or any(event["event_type"] == "tool_result" for event in events)


def test_extract_text_content_skips_empty_text_blocks():
    from apps.chat_data_app.agent.run.conversation_event_service import _extract_text_content

    blocks = [
        {"type": "text", "text": "   "},
        {"type": "image_url"},
        {"type": "text", "text": "正文"},
    ]
    assert _extract_text_content(blocks) == "正文"


def test_serialize_ai_message_resolves_proxy_target_label(monkeypatch):
    from apps.chat_data_app.agent.run import conversation_event_service as event_service

    monkeypatch.setattr(
        event_service,
        "get_mcp_tool_display_name",
        lambda name: "磁盘用量排查" if name == "mcp_fault_ssh_disk" else "",
    )
    message = SimpleNamespace(
        content="",
        id="ai-proxy",
        additional_kwargs={},
        tool_calls=[
            {
                "id": "c1",
                "name": "mcp_tool_call",
                "args": {"name": "mcp_fault_ssh_disk", "input": {}},
            }
        ],
        response_metadata=None,
    )
    payload = event_service._serialize_ai_message(message)
    assert payload["tool_calls"][0]["display_name"] == "磁盘用量排查"
