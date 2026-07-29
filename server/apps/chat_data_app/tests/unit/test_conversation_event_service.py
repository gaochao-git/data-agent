import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.daos.conversation_event_dao import ConversationEventDao
from apps.chat_data_app.services.conversation_event_service import ConversationEventService
from utils.models import Base, UserThread


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

    yield ConversationEventService(ConversationEventDao(session_context=lambda: get_test_session())), create_thread
    asyncio.run(engine.dispose())


@pytest.mark.asyncio
async def test_event_history_is_ordered_and_idempotent_across_turns(conversation_event_service):
    service, create_thread = conversation_event_service
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
    )

    history = await service.load_history(conversation_id="conv-1", user_id="user-1")

    assert [event["sequence_no"] for event in history["history_events"]] == list(range(1, 9))
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


@pytest.mark.asyncio
async def test_event_history_is_scoped_to_conversation_owner(conversation_event_service):
    service, create_thread = conversation_event_service
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
    service, create_thread = conversation_event_service
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
