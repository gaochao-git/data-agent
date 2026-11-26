"""子智能体会话快照与终态写入的回归测试（SQLite 内存库）。

任务终态与会话快照必须保持可恢复的一致性：终态先落库会让紧随其后的
追问锚到旧轮次；快照写失败又不能把已经完成的任务回滚成失败。这里用
真实 DAO 与事务验证两条边界。
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from apps.chat_data_app.agent.run import subagent_event_service as event_service_module
from apps.chat_data_app.agent.run import subagent_task_service as service_module
from apps.chat_data_app.agent.run.agent_inbox_service import AgentRunConflictError, agent_inbox_service
from apps.chat_data_app.agent.run.subagent_task_service import subagent_task_service
from utils.models import Base, UserThread

CONV = "conv-bg-test"
USER = "u1"


class _FakeEventService:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def append(self, **kwargs):
        self.events.append(kwargs)


def _settings(max_active: int = 5, max_subagents_per_conversation: int = 20):
    values = {
        "CHAT_DATA_SUBAGENT_TASK_MAX_ACTIVE": max_active,
        "CHAT_DATA_SUBAGENT_MAX_PER_CONVERSATION": max_subagents_per_conversation,
        "CHAT_DATA_SUBAGENT_TASK_LEASE_SECONDS": 30,
        "CHAT_DATA_SUBAGENT_TASK_TIMEOUT_SECONDS": 60,
    }

    async def _get(key: str) -> int:
        return values.get(key, 60)

    return _get


@pytest.fixture()
def harness(monkeypatch, tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'subagent-session.db'}",
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


async def _create_task(harness):
    return await harness.service.create_task(
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        subagent_type="explore",
        description="深度分析近 30 天入金趋势",
        label="入金趋势",
        parent_tool_call_id="call-parent",
        parent_owner_token="parent-owner",
        operator_name="用户一",
    )


async def _start(harness, record):
    started = await harness.service.start_task(record.task_id, record.owner_token or "")
    assert started is not None
    return started


@pytest.mark.asyncio
async def test_finish_task_persists_session_history(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)
    history = [
        {"type": "human", "content": "盘点近 30 天入金趋势"},
        {"type": "ai", "content": "结论：整体平稳"},
    ]
    finished = await harness.service.finish_task(
        record.task_id,
        record.owner_token or "",
        status="completed",
        report="结论：整体平稳",
        history=history,
    )

    assert finished is not None
    assert finished.root_task_id == record.task_id
    assert finished.turn_index == 1
    assert (
        await harness.service.load_session_messages(
            record.task_id,
            conversation_id=CONV,
            user_id=USER,
        )
        == history
    )
    assert (
        await harness.service.load_session_messages(
            record.task_id,
            conversation_id=CONV,
            user_id=USER,
            expected_turn_index=2,
        )
        == []
    )


@pytest.mark.asyncio
async def test_load_session_history_degrades_when_table_unavailable(harness, monkeypatch):
    """会话表未迁移时不能阻断追问；读取失败按空历史处理。"""

    async def broken_get(*_args, **_kwargs):
        raise RuntimeError("table chat_data_subagent_session does not exist")

    monkeypatch.setattr(service_module.subagent_session_dao, "get", broken_get)

    assert (
        await harness.service.load_session_messages(
            "session-missing-table",
            conversation_id=CONV,
            user_id=USER,
        )
        == []
    )


@pytest.mark.asyncio
async def test_finish_task_commits_terminal_when_session_snapshot_fails(harness, monkeypatch):
    """快照写失败只回滚保存点，任务仍按终态提交，追问走任务书+报告兜底。"""
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)

    async def broken_save(*_args, **_kwargs):
        raise RuntimeError("table chat_data_subagent_session does not exist")

    monkeypatch.setattr(harness.service, "_save_session_history", broken_save)

    finished = await harness.service.finish_task(
        record.task_id,
        record.owner_token or "",
        status="completed",
        report="结论：任务仍正常完成",
        history=[{"type": "human", "content": "盘点近 30 天入金趋势"}],
    )

    assert finished is not None and finished.status == "completed"
    persisted = await harness.service.get_task(record.task_id)
    assert persisted is not None and persisted.status == "completed"
    assert (
        await harness.service.load_session_messages(
            record.task_id,
            conversation_id=CONV,
            user_id=USER,
        )
        == []
    )
