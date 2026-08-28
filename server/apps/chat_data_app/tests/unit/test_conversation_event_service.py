import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime

import pytest
from sqlalchemy import select
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.daos.conversation_event_dao import ConversationEventDao
from apps.chat_data_app.agent.run.conversation_event_service import ConversationEventService
from apps.chat_data_app.agent.run.agent_inbox_service import AgentRunLeaseLostError
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
        AIMessage(content="", id="assistant-1", tool_calls=[{"id": "tool-1", "name": "semantic_query_tool", "args": {}}]),
        ToolMessage(content="订单查询结果", id="tool-result-1", tool_call_id="tool-1", name="semantic_query_tool"),
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

    history = await service.load_history(conversation_id="conv-1", user_id="user-1")

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
        rows = list((await session.scalars(
            select(ChatDataConversationEvent).where(
                ChatDataConversationEvent.conversation_id == "conv-usage",
                ChatDataConversationEvent.event_type == "token_usage",
            )
        )).all())
    assert len(rows) == 1
    assert rows[0].event_key == "token_usage:latest"
    assert rows[0].turn_id == "turn-3"

    history = await service.load_history(conversation_id="conv-usage", user_id="user-1")
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

    assert (await service.load_history(conversation_id="conv-owner", user_id="user-1"))["messages"][0]["content"] == "只属于用户一"
    assert (await service.load_history(conversation_id="conv-owner", user_id="user-2"))["history_events"] == []


@pytest.mark.asyncio
async def test_context_compression_event_is_anchored_and_idempotent(conversation_event_service):
    service, create_thread, _get_session = conversation_event_service
    await create_thread("conv-compression", "user-1")
    user_message = HumanMessage(content="继续分析", id="turn-1")
    tool_message = ToolMessage(
        content="工具结果",
        id="tool-1",
        tool_call_id="call-1",
        name="semantic_query_tool",
    )
    messages = [
        user_message,
        AIMessage(content="", id="assistant-1", tool_calls=[{"id": "call-1", "name": "semantic_query_tool", "args": {}}]),
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

    history = await service.load_history(conversation_id="conv-compression", user_id="user-1")
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

    history = await service.load_history(conversation_id="conv-snapshot", user_id="user-1")

    assert [event["event_type"] for event in history["history_events"]] == [
        "user_message",
        "assistant_message",
    ]
    assert history["messages"][-1]["content"] == "第一阶段的完整回答"


async def _create_run(get_session, conversation_id: str, run_id: str, owner_token: str) -> None:
    async with get_session() as session:
        now = datetime.now()
        session.add(ChatDataAgentInbox(
            conversation_id=conversation_id,
            user_id="user-1",
            run_id=run_id,
            owner_token=owner_token,
            message_id=run_id,
            record_type="run",
            content=json.dumps({"turn_id": "turn-checkpoint"}, ensure_ascii=False),
            status="active",
            delete_flag=0,
            create_time=now,
            update_time=now,
            create_by="user-1",
            create_by_name="用户一",
            update_by="user-1",
            update_by_name="用户一",
        ))


async def _update_run_content(get_session, run_id: str, content: dict, *, status: str = "active") -> None:
    async with get_session() as session:
        run = await session.scalar(select(ChatDataAgentInbox).where(ChatDataAgentInbox.run_id == run_id))
        run.content = json.dumps(content, ensure_ascii=False)
        run.status = status


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
    assert json.loads(run.content)["history_checkpoint"] == checkpoint


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
    assert "history_checkpoint" not in json.loads(run.content)


@pytest.mark.asyncio
async def test_checkpoint_is_not_written_without_redis_cursor(conversation_event_service):
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-no-cursor", "user-1")
    await _create_run(get_session, "conv-no-cursor", "run-no-cursor", "owner-1")
    user = HumanMessage(content="只写历史", id="turn-no-cursor")

    assert await service.persist_turn_with_checkpoint(
        conversation_id="conv-no-cursor",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-no-cursor",
        messages=[user, AIMessage(content="历史已写", id="assistant-no-cursor")],
        reasoning_segments=[],
        run_id="run-no-cursor",
        owner_token="owner-1",
        stream_id=None,
    ) is None
    async with get_session() as session:
        run = await session.scalar(select(ChatDataAgentInbox).where(ChatDataAgentInbox.run_id == "run-no-cursor"))
    assert "history_checkpoint" not in json.loads(run.content)


@pytest.mark.asyncio
async def test_checkpoint_requires_published_redis_cursor_and_worker_owner(conversation_event_service):
    service, create_thread, get_session = conversation_event_service
    await create_thread("conv-invalid-cursor", "user-1")
    await _create_run(get_session, "conv-invalid-cursor", "run-invalid-cursor", "owner-1")
    user = HumanMessage(content="非法游标", id="turn-invalid-cursor")

    assert await service.persist_turn_with_checkpoint(
        conversation_id="conv-invalid-cursor",
        user_id="user-1",
        operator_name="用户一",
        turn_id="turn-invalid-cursor",
        messages=[user, AIMessage(content="历史仍可保存", id="assistant-invalid-cursor")],
        reasoning_segments=[],
        run_id="run-invalid-cursor",
        owner_token="owner-1",
        stream_id="0-0",
    ) is None

    async with get_session() as session:
        rows = list((await session.scalars(select(ChatDataConversationEvent))).all())
        run = await session.scalar(select(ChatDataAgentInbox).where(ChatDataAgentInbox.run_id == "run-invalid-cursor"))
    assert len(rows) == 2
    assert "history_checkpoint" not in json.loads(run.content)

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
    await _update_run_content(get_session, "run-recovery", {
        "turn_id": "turn-recovery",
        "history_checkpoint": {
            "run_id": "run-recovery",
            "turn_id": "turn-recovery",
            "history_sequence_no": 2,
            "stream_id": "1787629922140-7",
        },
    })

    history = await service.load_history_with_recovery(
        conversation_id="conv-recovery",
        user_id="user-1",
    )

    assert history["active_run"]["run_id"] == "run-recovery"
    assert history["active_run"]["history_checkpoint"]["stream_id"] == "1787629922140-7"

    await _update_run_content(get_session, "run-recovery", {
        "turn_id": "turn-recovery",
        "history_checkpoint": {
            "run_id": "another-run",
            "stream_id": "not-a-stream-id",
        },
    }, status="finished")
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
    await _update_run_content(get_session, "run-malformed-recovery", {
        "turn_id": "turn-malformed-recovery",
        "history_checkpoint": {
            "run_id": "run-malformed-recovery",
            "history_sequence_no": 3,
            "stream_id": "not-a-redis-id",
        },
    })

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
    assert json.loads(run.content)["history_checkpoint"] == first


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

    class TokenFailureDao:
        async def list_visible_events(self, **_kwargs):
            return [EventRow()]

        async def get_latest_token_usage_payload(self, **_kwargs):
            raise RuntimeError("token usage unavailable")

    history = await ConversationEventService(TokenFailureDao()).load_history(
        conversation_id="conv-1",
        user_id="user-1",
    )

    assert history["messages"][0]["content"] == "保留消息"
    assert history["token_usage"] is None


def test_serialized_messages_carry_execution_timing_only_when_stamped():
    """AI/工具消息盖了起止时间才写入 payload；耗时由展示端相减得出。"""
    from apps.chat_data_app.agent.run.conversation_event_service import (
        _serialize_ai_message,
        _serialize_tool_message,
    )

    stamped_ai = _serialize_ai_message(AIMessage(
        content="分析完成",
        id="msg-ai-1",
        additional_kwargs={"started_at_ms": 1000, "ended_at_ms": 3500},
    ))
    assert stamped_ai["started_at_ms"] == 1000
    assert stamped_ai["ended_at_ms"] == 3500

    plain_ai = _serialize_ai_message(AIMessage(content="无耗时", id="msg-ai-2"))
    assert "started_at_ms" not in plain_ai
    assert "ended_at_ms" not in plain_ai

    stamped_tool = _serialize_tool_message(ToolMessage(
        content="工具结果",
        id="msg-tool-1",
        tool_call_id="call-1",
        additional_kwargs={"started_at_ms": 4000, "ended_at_ms": 4200},
    ))
    assert stamped_tool["started_at_ms"] == 4000
    assert stamped_tool["ended_at_ms"] == 4200

    invalid_tool = _serialize_tool_message(ToolMessage(
        content="非法时间戳",
        id="msg-tool-2",
        tool_call_id="call-2",
        additional_kwargs={"started_at_ms": "bad", "ended_at_ms": None},
    ))
    assert "started_at_ms" not in invalid_tool
    assert "ended_at_ms" not in invalid_tool


def test_human_message_payload_strips_blocks_and_carries_vision_attachments():
    """保险丝：blocks 里的图片降级为占位文本；vision_attachments 标记随行。"""
    from apps.chat_data_app.agent.run.conversation_event_service import (
        _serialize_human_message,
    )

    blocked = _serialize_human_message(HumanMessage(
        id="turn-img",
        content=[
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBBB"}},
        ],
        additional_kwargs={"vision_attachments": ["/mnt/user-data/uploads/a.png"]},
    ))
    assert blocked["content"] == "看这张图\n[图片内容已省略，共 2 张]"
    assert blocked["vision_attachments"] == ["/mnt/user-data/uploads/a.png"]
    # payload 全文不出现 base64
    assert "base64" not in str(blocked)

    pure_image = _serialize_human_message(HumanMessage(
        content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,CCCC"}}],
    ))
    assert pure_image["content"] == "[图片内容已省略，共 1 张]"

    plain = _serialize_human_message(HumanMessage(content="普通文本消息"))
    assert plain["content"] == "普通文本消息"
    assert "vision_attachments" not in plain
