"""Redis 故障贯穿演练：断点恢复矩阵「Redis 数据清空 / Redis 不可达」两行的整链契约。

与单层隔离测试（各自 monkeypatch 假件）互补，这里把真实组件组合起来——真实
AgentInboxService / AgentRunEventService / ConversationEventService / AgentRunRuntime
收口路径 + sqlite 内存库全量表——只把 Redis 换成可编程 FakeBus（模拟 Lua 原子语义：
owner 校验、event_id 去重、XADD 单调游标；以及两种故障形态），从「Run 进行中 Redis
故障」一路断言到矩阵声称的终态：

- **服务不可达**（``go_down``）：worker 侧 publish 连接失败 → 立即 ``finish_run("failed")``
  + 消息重排队（不等租约过期）；SSE 侧读失败 → ``redis_unavailable`` → MySQL 快照轮询 →
  0.5s 快速宽限 → 合成 done(failed)（``event_id`` 带 ``terminal:fallback``）；conversation_event
  账本原样保留；总线恢复后重发可正常执行。
- **数据清空、服务仍活**（``flush``）：owner key 丢失 → publish 抛 owner mismatch →
  worker 按「租约已丢」静默停止（防误伤潜在新执行段）；MySQL 行作为无主 ACTIVE 僵尸挂到
  租约过期，由 reaper 收口 failed + 重排队 + 补发真实 done；SSE 侧靠快照轮询等待并收到
  reaper 的真实 done 后结束；账本同样不丢。

矩阵措辞「进行中 run 因 owner key 丢失在下次 publish 时失败标记」与实现的差异由本测试
固化：清空场景的「标记失败」发生在 reaper 收口时（租约 120s + 调度 2s），不是 publish 时；
用户在此期间看到的是「进行中」。服务不可达场景才是 publish 时立即收口。
"""

from __future__ import annotations

import asyncio
import importlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import cast

import pytest
from langchain_core.messages import HumanMessage
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.agent.run.agent_inbox_service import AgentInboxService
from apps.chat_data_app.agent.run.agent_run_event_service import AgentRunEventService
from apps.chat_data_app.agent.run.agent_run_runtime import AgentRunRuntime
from apps.chat_data_app.agent.run.conversation_event_service import ConversationEventService
from apps.chat_data_app.agent.run.redis_event_bus import RedisEventBus, RedisRunOwnerMismatchError
from utils.models import Base, ChatDataAgentInbox, UserThread

inbox_module = importlib.import_module("apps.chat_data_app.agent.run.agent_inbox_service")
event_module = importlib.import_module("apps.chat_data_app.agent.run.agent_run_event_service")
ledger_module = importlib.import_module("apps.chat_data_app.agent.run.conversation_event_service")


def _sse(seq: int) -> str:
    return f'event: message\ndata: {{"seq": {seq}}}\n\n'


class _FlushableBus:
    """可编程 Redis 事件总线假件。

    healthy：内存 stream + owner 表模拟 Lua 原子语义（owner 校验、event_id 去重返回
    原 stream_id、游标单调且字典序可比）；``go_down``：一切命令抛连接错误；
    ``flush``：数据清空（owner/stream 全没）但服务可用，对齐生产 FLUSHALL 的可见行为。
    """

    def __init__(self) -> None:
        self._streams: dict[str, list[dict[str, str]]] = {}
        self._owners: dict[str, str] = {}
        self._published: dict[str, str] = {}
        self._seq = 0
        self.down = False

    def go_down(self) -> None:
        self.down = True

    def recover(self) -> None:
        self.down = False

    def flush(self) -> None:
        self._streams.clear()
        self._owners.clear()
        self._published.clear()

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _check_alive(self) -> None:
        if self.down:
            raise RedisConnectionError("drill: redis 不可达")

    async def claim_run_owner(self, run_id: str, owner_token: str) -> None:
        self._check_alive()
        self._owners[run_id] = owner_token

    async def release_run_owner(self, run_id: str, owner_token: str) -> None:
        if self.down:
            return
        if self._owners.get(run_id) == owner_token:
            self._owners.pop(run_id, None)

    async def renew_run_owner(self, run_id: str, owner_token: str) -> None:
        self._check_alive()
        if self._owners.get(run_id) != owner_token:
            raise RedisRunOwnerMismatchError("drill: owner 已丢失")

    async def publish(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        event_id: str,
        event_type: str,
        payload: str,
        owner_token: str,
    ) -> str:
        self._check_alive()
        if event_id in self._published:
            return self._published[event_id]
        if self._owners.get(run_id) != owner_token:
            raise RedisRunOwnerMismatchError("drill: owner key 不存在或已易主")
        self._seq += 1
        stream_id = f"{1700000000000 + self._seq}-{self._seq:08d}"
        event = {
            "stream_id": stream_id,
            "event_id": event_id,
            "event_type": event_type,
            "payload": payload,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "sequence_no": str(self._seq),
        }
        self._streams.setdefault(run_id, []).append(event)
        self._published[event_id] = stream_id
        return stream_id

    async def read_run(self, run_id: str, cursor: str, **_kwargs) -> list[dict[str, str]]:
        self._check_alive()
        return [dict(item) for item in self._streams.get(run_id, []) if item["stream_id"] > cursor]

    async def run_stream_exists(self, run_id: str) -> bool:
        self._check_alive()
        return run_id in self._streams

    async def latest_run_event_id(self, run_id: str) -> str | None:
        self._check_alive()
        events = self._streams.get(run_id, [])
        return events[-1]["stream_id"] if events else None

    async def latest_run_event(self, run_id: str) -> dict[str, str] | None:
        self._check_alive()
        events = self._streams.get(run_id, [])
        return dict(events[-1]) if events else None


class _Drill:
    """真实组件 + FakeBus 的演练环境（会话/Run/消息/账本均在 sqlite 内存库）。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        async def create_tables() -> None:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

        asyncio.run(create_tables())
        self.session_factory = async_sessionmaker(engine, expire_on_commit=False)

        @asynccontextmanager
        async def get_test_session():
            async with self.session_factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        for module in (inbox_module, event_module, ledger_module):
            monkeypatch.setattr(module, "get_session", get_test_session)

        self.bus = _FlushableBus()
        typed_bus = cast(RedisEventBus, self.bus)  # 测试替身：方法面与真实总线一致
        self.events = AgentRunEventService(bus=typed_bus)
        self.inbox = AgentInboxService()
        self.runtime = AgentRunRuntime(inbox_service=self.inbox, event_service=self.events, event_bus=typed_bus)
        monkeypatch.setattr(self.runtime, "submit", lambda **_kwargs: None)
        self.ledger = ConversationEventService()
        self._engine = engine

    async def create_thread(self) -> None:
        async with self.session_factory() as session:
            session.add(
                UserThread(
                    user_id="user-1",
                    thread_id="conv-1",
                    agent_id="chat_data",
                    delete_flag=0,
                    create_time=datetime.now(),
                    create_by="user-1",
                    create_by_name="user-1",
                    update_by="user-1",
                    update_by_name="user-1",
                )
            )
            await session.commit()

    async def start_run_with_message(self) -> str:
        run_id, _adopted = await self.inbox.start_run("conv-1", "user-1", "用户一")
        await self.inbox.enqueue_message("conv-1", "user-1", "用户一", "查询订单", queue_type="next-step")
        return run_id

    async def seed_ledger(self, turn_id: str) -> None:
        await self.ledger.persist_user_message(
            conversation_id="conv-1",
            user_id="user-1",
            operator_name="用户一",
            message=HumanMessage(content="查询订单", id=turn_id),
        )

    async def run_row(self, run_id: str) -> ChatDataAgentInbox | None:
        async with self.session_factory() as session:
            return (
                await session.execute(
                    select(ChatDataAgentInbox).where(
                        ChatDataAgentInbox.run_id == run_id,
                        ChatDataAgentInbox.record_type == "run",
                    )
                )
            ).scalar_one_or_none()

    async def message_rows(self) -> list[ChatDataAgentInbox]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(ChatDataAgentInbox).where(ChatDataAgentInbox.record_type == "message")
            )
            return list(result.scalars())

    async def expire_lease(self, run_id: str) -> None:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(ChatDataAgentInbox).where(
                        ChatDataAgentInbox.run_id == run_id,
                        ChatDataAgentInbox.record_type == "run",
                    )
                )
            ).scalar_one()
            row.lease_expire_time = datetime.now() - timedelta(seconds=1)
            await session.commit()

    async def dispose(self) -> None:
        await self._engine.dispose()


@pytest.fixture()
def drill(monkeypatch):
    environment = _Drill(monkeypatch)
    yield environment
    asyncio.run(environment.dispose())


async def _until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "drill: 等待条件超时"
        await asyncio.sleep(0.01)


async def _subscribe(events: AgentRunEventService, run_id: str, collected: list[dict[str, str]]) -> None:
    async for event in events.stream(run_id=run_id, last_event_id="0-0"):
        collected.append(event)


def _fault_stream(
    runtime: AgentRunRuntime,
    monkeypatch: pytest.MonkeyPatch,
    ready: asyncio.Event,
    action,
) -> None:
    """产出两段事件、中间注入故障的流工厂：第一段发布成功并等订阅侧确认后再触发故障。"""

    async def stream():
        yield _sse(1)
        await ready.wait()
        action()
        yield _sse(2)

    monkeypatch.setattr(runtime, "_build_stream_factory", lambda _prepared: stream)


async def _execute(runtime: AgentRunRuntime, run_id: str) -> None:
    await runtime._execute(run_id=run_id, conversation_id="conv-1", user_id="user-1", operator_name="用户一")


@pytest.mark.asyncio
async def test_drill_redis_unavailable_fails_fast_and_sse_gets_fallback_done(drill, monkeypatch):
    """服务不可达：worker 立即收口 failed + 重排队；SSE 拿合成 done；账本不丢；恢复可重跑。"""
    await drill.create_thread()
    await drill.seed_ledger("turn-down-1")
    run_id = await drill.start_run_with_message()

    collected: list[dict[str, str]] = []
    subscriber = asyncio.create_task(_subscribe(drill.events, run_id, collected))
    ready = asyncio.Event()
    _fault_stream(drill.runtime, monkeypatch, ready, drill.bus.go_down)
    worker = asyncio.create_task(_execute(drill.runtime, run_id))

    await _until(lambda: any(item["payload"].startswith("event: message") for item in collected))
    ready.set()
    await worker

    row = await drill.run_row(run_id)
    assert row is not None and row.record_status == "failed"
    assert [message.record_status for message in await drill.message_rows()] == ["pending"]

    await asyncio.wait_for(subscriber, timeout=10)
    done_events = [item for item in collected if item["event_type"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["event_id"] == f"{run_id}:terminal:fallback"
    assert '"status": "failed"' in done_events[0]["payload"]
    assert any(item["payload"].startswith("event: message") for item in collected)

    from utils.models import ChatDataConversationEvent

    async with drill.session_factory() as session:
        ledger = list((await session.execute(select(ChatDataConversationEvent))).scalars())
    assert [row_.event_key for row_ in ledger] == ["user:turn-down-1"], "已完成步骤在账本不丢"

    # 总线恢复后用户重发可正常执行（fast-fail 语义：断了标记失败、重发继续）
    drill.bus.recover()
    monkeypatch.setattr(
        drill.runtime,
        "_build_stream_factory",
        lambda _prepared: _healthy_stream(),
    )
    run2, _adopted = await drill.inbox.start_run("conv-1", "user-1", "用户一")
    await _execute(drill.runtime, run2)
    row2 = await drill.run_row(run2)
    assert row2 is not None and row2.record_status == "finished"


def _healthy_stream():
    async def stream():
        yield _sse(1)

    return stream


@pytest.mark.asyncio
async def test_drill_redis_flush_zombie_until_reaper_closes(drill, monkeypatch):
    """数据清空：worker 静默停止留无主 ACTIVE；reaper 租约过期收口 + 补发真实 done。"""
    await drill.create_thread()
    await drill.seed_ledger("turn-flush-1")
    run_id = await drill.start_run_with_message()

    collected: list[dict[str, str]] = []
    subscriber = asyncio.create_task(_subscribe(drill.events, run_id, collected))
    ready = asyncio.Event()
    _fault_stream(drill.runtime, monkeypatch, ready, drill.bus.flush)
    worker = asyncio.create_task(_execute(drill.runtime, run_id))

    await _until(lambda: any(item["payload"].startswith("event: message") for item in collected))
    ready.set()
    await worker

    # worker 静默停止：MySQL 行保持无主 ACTIVE（矩阵措辞与实现的差异点，见模块 docstring）
    zombie = await drill.run_row(run_id)
    assert zombie is not None and zombie.record_status == "active"

    await drill.expire_lease(run_id)
    await drill.runtime._reap_expired_runs()

    row = await drill.run_row(run_id)
    assert row is not None and row.record_status == "failed"
    assert [message.record_status for message in await drill.message_rows()] == ["pending"]

    await asyncio.wait_for(subscriber, timeout=10)
    done_events = [item for item in collected if item["event_type"] == "done"]
    assert len(done_events) == 1
    # 清空场景（服务仍活）SSE 收到的是 reaper 补发的真实 done，不是合成兜底
    assert done_events[0]["event_id"].startswith(f"{run_id}:terminal:")
    assert done_events[0]["event_id"] != f"{run_id}:terminal:fallback"
    assert '"status": "failed"' in done_events[0]["payload"]

    from utils.models import ChatDataConversationEvent

    async with drill.session_factory() as session:
        ledger = list((await session.execute(select(ChatDataConversationEvent))).scalars())
    assert [row_.event_key for row_ in ledger] == ["user:turn-flush-1"], "已完成步骤在账本不丢"
