"""子智能体会话关闭与单对话容量的真实账本测试。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from apps.chat_data_app.agent.contracts.subagent_task import (
    SESSION_STATUS_CLOSED,
    SubagentSessionActiveError,
    SubagentSessionClosedError,
    SubagentTaskCapacityError,
)
from apps.chat_data_app.agent.run import subagent_event_service as event_service_module
from apps.chat_data_app.agent.run import subagent_task_service as service_module
from apps.chat_data_app.agent.run.agent_inbox_service import AgentRunConflictError, agent_inbox_service
from apps.chat_data_app.agent.run.subagent_task_service import subagent_task_service
from apps.chat_data_app.daos.subagent_session_dao import subagent_session_dao
from utils.models import Base, ChatDataSubagentSession, UserThread

CONV = "conv-session-lifecycle"
CONV_2 = "conv-session-lifecycle-2"
USER = "u1"


class _FakeEventService:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def append(self, **kwargs):
        self.events.append(kwargs)


def _settings(*, max_active: int = 20, max_per: int = 5):
    values = {
        "CHAT_DATA_SUBAGENT_TASK_MAX_ACTIVE": max_active,
        "CHAT_DATA_SUBAGENT_MAX_PER_CONVERSATION": max_per,
        "CHAT_DATA_SUBAGENT_TASK_LEASE_SECONDS": 30,
        "CHAT_DATA_SUBAGENT_TASK_TIMEOUT_SECONDS": 60,
    }

    async def _get(key: str) -> int:
        return values.get(key, 60)

    return _get


@pytest.fixture()
def harness(monkeypatch, tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'subagent-lifecycle.db'}",
        poolclass=NullPool,
        connect_args={"timeout": 30},
    )

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
    monkeypatch.setattr(service_module, "get_runtime_setting", _settings())
    monkeypatch.setattr(subagent_task_service, "_event_service", _FakeEventService())

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
        create_conversation=create_conversation,
        service=subagent_task_service,
    )
    for task in list(subagent_task_service._local_tasks.values()):
        task.cancel()
    subagent_task_service._local_tasks.clear()
    asyncio.run(engine.dispose())


async def _create_task(harness, *, conversation_id: str = CONV):
    return await harness.service.create_task(
        conversation_id=conversation_id,
        user_id=USER,
        parent_run_id="run-parent",
        subagent_type="explore",
        description="整理一份结论",
        label="会话测试",
        parent_tool_call_id="call-parent",
        parent_owner_token="parent-owner",
        timeout_seconds=60,
        operator_name="用户一",
    )


async def _finish(harness, record, history=None):
    return await harness.service.finish_task(
        record.task_id,
        record.owner_token or "",
        status="completed",
        report="结论已完成",
        history=history or [{"type": "human", "content": "整理一份结论"}],
    )


@pytest.mark.asyncio
async def test_close_session_blocks_follow_up_but_preserves_history(harness):
    await harness.create_conversation()
    first = await _create_task(harness)
    await harness.service.start_task(first.task_id, first.owner_token or "")
    history = [
        {"type": "human", "content": "整理一份结论"},
        {"type": "ai", "content": "结论已完成"},
    ]
    finished = await _finish(harness, first, history)
    assert finished is not None

    state = await harness.service.close_session(
        first.task_id,
        conversation_id=CONV,
        user_id=USER,
        operator_name="用户一",
    )
    assert state is not None and state.status == SESSION_STATUS_CLOSED
    assert await harness.service.load_session_messages(first.task_id, conversation_id=CONV, user_id=USER) == history

    again = await harness.service.close_session(
        first.task_id,
        conversation_id=CONV,
        user_id=USER,
        operator_name="用户一",
    )
    assert again is not None and again.status == SESSION_STATUS_CLOSED

    with pytest.raises(SubagentSessionClosedError, match="已关闭"):
        await harness.service.create_task(
            conversation_id=CONV,
            user_id=USER,
            parent_run_id="run-parent",
            subagent_type="explore",
            description="继续补充",
            label=None,
            parent_tool_call_id="call-follow",
            parent_owner_token="parent-owner",
            timeout_seconds=60,
            operator_name="用户一",
            parent_task_id=finished.task_id,
            agent_session_id=finished.agent_session_id,
        )


@pytest.mark.asyncio
async def test_close_session_rejects_active_task(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await harness.service.start_task(record.task_id, record.owner_token or "")

    with pytest.raises(SubagentSessionActiveError, match="仍有进行中的任务"):
        await harness.service.close_session(
            record.task_id,
            conversation_id=CONV,
            user_id=USER,
            operator_name="用户一",
        )


@pytest.mark.asyncio
async def test_close_from_old_task_keeps_session_latest_pointer(harness):
    await harness.create_conversation()
    first = await _create_task(harness)
    await harness.service.start_task(first.task_id, first.owner_token or "")
    await _finish(harness, first)
    second = await harness.service.create_task(
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        subagent_type="explore",
        description="补充第二轮",
        label=None,
        parent_tool_call_id="call-follow",
        parent_owner_token="parent-owner",
        timeout_seconds=60,
        operator_name="用户一",
        parent_task_id=first.task_id,
        agent_session_id=first.agent_session_id,
    )
    await harness.service.start_task(second.task_id, second.owner_token or "")
    await _finish(harness, second)

    await harness.service.close_session(
        first.task_id,
        conversation_id=CONV,
        user_id=USER,
        operator_name="用户一",
    )
    latest = await harness.service.get_latest_task_in_session(
        first.agent_session_id,
        conversation_id=CONV,
        user_id=USER,
    )

    assert latest is not None and latest.task_id == second.task_id


@pytest.mark.asyncio
async def test_close_session_releases_conversation_subagent_quota(harness, monkeypatch):
    monkeypatch.setattr(service_module, "get_runtime_setting", _settings(max_active=20, max_per=1))
    await harness.create_conversation()
    first = await _create_task(harness)
    await harness.service.start_task(first.task_id, first.owner_token or "")
    await _finish(harness, first)

    with pytest.raises(SubagentTaskCapacityError, match="达到上限"):
        await _create_task(harness)

    await harness.service.close_session(
        first.task_id,
        conversation_id=CONV,
        user_id=USER,
        operator_name="用户一",
    )
    replacement = await _create_task(harness)

    assert replacement.turn_index == 1
    assert replacement.agent_session_id != first.agent_session_id


@pytest.mark.asyncio
async def test_capacity_limits_are_isolated_per_conversation(harness, monkeypatch):
    monkeypatch.setattr(service_module, "get_runtime_setting", _settings(max_active=1, max_per=1))
    await harness.create_conversation()
    await harness.create_conversation(CONV_2)
    first = await _create_task(harness)

    other_conversation = await _create_task(harness, conversation_id=CONV_2)

    assert other_conversation.task_id != first.task_id
    with pytest.raises(SubagentTaskCapacityError, match="已达上限"):
        await _create_task(harness)


@pytest.mark.asyncio
async def test_capacity_falls_back_when_session_schema_is_missing(harness, monkeypatch):
    """会话表未迁移时关闭额度不可用，但不能阻断原有子智能体派发。"""
    await harness.create_conversation()
    await _create_task(harness)
    monkeypatch.setattr(service_module, "get_runtime_setting", _settings(max_per=1))
    async with harness.engine.begin() as connection:
        await connection.run_sync(ChatDataSubagentSession.__table__.drop, checkfirst=True)

    with pytest.raises(SubagentTaskCapacityError, match="达到上限"):
        await _create_task(harness)


async def _finish_first_turn(harness):
    await harness.create_conversation()
    first = await _create_task(harness)
    await harness.service.start_task(first.task_id, first.owner_token or "")
    finished = await _finish(harness, first)
    assert finished is not None
    return finished


async def _create_follow_up(harness, finished):
    return await harness.service.create_task(
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        subagent_type="explore",
        description="继续补充",
        label=None,
        parent_tool_call_id="call-follow",
        parent_owner_token="parent-owner",
        timeout_seconds=60,
        operator_name="用户一",
        parent_task_id=finished.task_id,
        agent_session_id=finished.agent_session_id,
    )


@pytest.mark.asyncio
async def test_follow_up_fails_closed_on_session_state_db_error(harness, monkeypatch):
    finished = await _finish_first_turn(harness)

    async def broken_get(*_args, **_kwargs):
        raise RuntimeError("数据库连接中断")

    monkeypatch.setattr(subagent_session_dao, "get", broken_get)

    with pytest.raises(RuntimeError, match="数据库连接中断"):
        await _create_follow_up(harness, finished)


@pytest.mark.asyncio
async def test_follow_up_degrades_only_when_close_column_not_migrated(harness, monkeypatch):
    finished = await _finish_first_turn(harness)

    async def not_migrated(*_args, **_kwargs):
        raise OperationalError(
            "SELECT ...",
            {},
            Exception(1054, "Unknown column 'session_status' in 'field list'"),
        )

    monkeypatch.setattr(subagent_session_dao, "get", not_migrated)

    record = await _create_follow_up(harness, finished)
    assert record.turn_index == 2


@pytest.mark.asyncio
async def test_close_from_unsnapshotted_new_turn_anchors_latest_pointer(harness):
    finished = await _finish_first_turn(harness)
    second = await _create_follow_up(harness, finished)
    await harness.service.start_task(second.task_id, second.owner_token or "")
    # 模拟收口快照写失败：任务已终态，但会话快照仍停在第一轮。
    await harness.service.finish_task(
        second.task_id,
        second.owner_token or "",
        status="completed",
        report="第二轮完成",
    )

    await harness.service.close_session(
        second.task_id,
        conversation_id=CONV,
        user_id=USER,
        operator_name="用户一",
    )

    async with harness.get_session() as session:
        row = await subagent_session_dao.get(
            session,
            second.agent_session_id,
            conversation_id=CONV,
            user_id=USER,
        )
    assert row is not None
    assert row.turn_count == 2
    assert row.latest_task_id == second.task_id
