"""子智能体账本测试共享的独立 SQLite 环境。"""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from apps.chat_data_app.agent.middleware.subagents import (
    DecideArgs,
    DispatchArgs,
    ManageArgs,
    SendArgs,
    build_task_tool,
)
from apps.chat_data_app.agent.run import subagent_event_service as event_service_module
from apps.chat_data_app.agent.run import subagent_task_service as service_module
from apps.chat_data_app.agent.run.agent_inbox_service import AgentRunConflictError, agent_inbox_service
from apps.chat_data_app.agent.run.subagent_task_service import subagent_task_service
from utils.models import Base, UserThread

CONV = "conv-bg-test"
USER = "u1"


def build_subagent_tool(specs, run_controls=None):
    """构建单工具入口；测试始终通过真实分组 schema 调用。"""
    return build_task_tool(specs, run_controls=run_controls)


async def invoke_subagent_tool(tool, operation: str, *, runtime, **kwargs):
    """按真实嵌套 schema 校验参数后调用 subagent 工具。"""
    schema = {
        "dispatch": DispatchArgs,
        "send": SendArgs,
        "manage": ManageArgs,
        "decide": DecideArgs,
    }[operation]
    params = schema(**kwargs)
    return await tool.coroutine(runtime=runtime, **{operation: params})


class _FakeEventService:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def append(self, **kwargs):
        self.events.append(kwargs)


def _settings(
    max_active: int = 20,
    max_subagents_per_conversation: int = 5,
    timeout_seconds: int = 60,
):
    values = {
        "CHAT_DATA_SUBAGENT_TASK_MAX_ACTIVE": max_active,
        "CHAT_DATA_SUBAGENT_MAX_PER_CONVERSATION": max_subagents_per_conversation,
        "CHAT_DATA_SUBAGENT_TASK_LEASE_SECONDS": 30,
        "CHAT_DATA_SUBAGENT_TASK_TIMEOUT_SECONDS": timeout_seconds,
    }

    async def _get(key: str) -> int:
        return values.get(key, 60)

    return _get


@contextmanager
def create_subagent_harness(monkeypatch, tmp_path):
    # 后台执行与测试轮询会并发开 Session。内存库固定复用同一连接，会在
    # 语句未结束时被另一协程提交（SQL statements in progress）；改用独立
    # 文件库 + NullPool，让每个 Session 持有自己的连接，由 SQLite 正常排队。
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'subagent-background.db'}",
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
    subagent_task_service.register_recovery_runner(None)

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
    subagent_task_service.register_recovery_runner(None)
    asyncio.run(engine.dispose())
