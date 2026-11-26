"""后台子智能体任务的实时 SSE 事件流。

任务账本负责状态与最终结果；本模块只承载“过程快照”这类高频、可丢弃的
实时数据。事件写 Redis Stream：

- 与父 Run 解耦：父 Run 结束后，任务详情页仍能订阅；
- 可重放：前端断线带 ``Last-Event-ID`` 续传，刷新后从 0 重放可恢复当前过程；
- 不落 MySQL：过程正文/思考增量动辄很多条，避免撑大任务行；
- 终态后由订阅端读取任务账本判断收口，不依赖额外的 done 帧。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import uuid4

from apps.chat_data_app.sse_protocol import SSE_EVENT_NAMES, format_sse
from utils.logger import get_logger

from .redis_event_bus import RedisEventBus, build_redis_event_bus

logger = get_logger(__name__)

TASK_EVENT_READ_BLOCK_SECONDS = 1.0
TASK_EVENT_READ_COUNT = 200
TASK_EVENT_QUEUE_MAXSIZE = 10_000
DisconnectCheck = Callable[[], Awaitable[bool] | bool]
TerminalCheck = Callable[[], Awaitable[bool] | bool]

_END = object()


async def _resolve_check(check: DisconnectCheck | TerminalCheck | None) -> bool:
    if check is None:
        return False
    result = check()
    if inspect.isawaitable(result):
        result = await result
    return bool(result)


class SubagentTaskEventService:
    """任务过程事件的 Redis Stream 读写。"""

    def __init__(self, bus: RedisEventBus | None = None) -> None:
        self._bus = bus or build_redis_event_bus()

    async def claim_stream(self, task_id: str) -> str:
        """为任务事件流领取一个独立 owner token。

        复用 Run 事件流的 owner 脚本，但 token 使用 ``generation:uuid`` 形态；
        task_id 与 run_id 都是随机 UUID，实际不会碰撞。

        代际必须单调递增：任务转入等待审批或取消时会释放 owner key，但进程被强杀等异常
        路径可能残留旧 key（TTL 1 小时）。若这里写死 ``1:``，恢复执行时同代际
        claim 会被 owner 脚本拒绝，过程流会静默禁用、只剩列表轮询。
        """
        owner_token = f"{time.time_ns()}:{uuid4().hex}"
        await self._bus.claim_run_owner(task_id, owner_token)
        return owner_token

    async def release_stream(self, task_id: str, owner_token: str) -> None:
        await self._bus.release_run_owner(task_id, owner_token)

    async def latest_event_id(self, task_id: str) -> str | None:
        """读取任务事件流当前尾部游标；快照订阅从这里之后续传，避免全量重放。"""
        try:
            return await self._bus.latest_run_event_id(task_id)
        except Exception:
            logger.debug("读取后台子智能体事件流尾游标失败: task_id=%s", task_id, exc_info=True)
            return None

    async def publish_event(
        self,
        *,
        task_id: str,
        conversation_id: str,
        user_id: str,
        owner_token: str,
        event: dict[str, Any],
    ) -> bool:
        """发布一条子智能体过程事件；未知帧名直接丢弃，避免污染 SSE 协议。"""
        event_type = str(event.get("event_type") or "").strip()
        if event_type not in SSE_EVENT_NAMES:
            return False
        await self._bus.publish(
            run_id=task_id,
            conversation_id=conversation_id,
            user_id=user_id,
            event_id=f"{task_id}:{event_type}:{uuid4().hex}",
            event_type=event_type,
            payload=format_sse(event_type, event),
            owner_token=owner_token,
        )
        return True

    async def stream(
        self,
        *,
        task_id: str,
        last_event_id: str = "0-0",
        is_terminal: TerminalCheck,
        is_disconnected: DisconnectCheck | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        """按游标重放并持续读取任务事件；任务终态且事件读空后结束。"""
        cursor = last_event_id or "0-0"
        while True:
            if await _resolve_check(is_disconnected):
                return
            events = await self._read_batch(task_id, cursor)
            if events:
                for event in events:
                    cursor = event["stream_id"]
                    yield event
                continue
            if await _resolve_check(is_terminal):
                return
            if events is None:
                # 读取异常时退避，避免 Redis 抖动期间空转；读到空批次仍立即重试。
                await asyncio.sleep(0.5)

    async def _read_batch(self, task_id: str, cursor: str) -> list[dict[str, str]] | None:
        try:
            return await self._bus.read_run(
                task_id,
                cursor,
                block_ms=int(TASK_EVENT_READ_BLOCK_SECONDS * 1000),
                count=TASK_EVENT_READ_COUNT,
            )
        except Exception:
            logger.debug("读取后台子智能体实时事件失败，稍后重试: task_id=%s", task_id, exc_info=True)
            return None


class SubagentTaskStreamWriter:
    """把子图 writer 的同步调用排队，异步按序写入任务事件流。

    ``_emit_subagent_tool_event`` 在同步代码里调用 writer，不能直接 ``await``
    Redis；这里用一个后台泵保证顺序与背压，不阻塞子图 token 流。
    """

    def __init__(
        self,
        service: SubagentTaskEventService,
        *,
        task_id: str,
        conversation_id: str,
        user_id: str,
    ) -> None:
        self._service = service
        self._task_id = task_id
        self._conversation_id = conversation_id
        self._user_id = user_id
        self._queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue(maxsize=TASK_EVENT_QUEUE_MAXSIZE)
        self._owner_token: str | None = None
        self._pump_task: asyncio.Task | None = None
        self._disabled = False
        self._dropped = False

    async def start(self) -> None:
        try:
            self._owner_token = await self._service.claim_stream(self._task_id)
        except Exception:
            self._disabled = True
            logger.warning("后台子智能体实时事件流不可用，任务继续执行: task_id=%s", self._task_id, exc_info=True)
            return
        self._pump_task = asyncio.create_task(self._pump(), name=f"chatdata-subagent-events-{self._task_id}")

    def __call__(self, payload: Any) -> None:
        event = self._extract_event(payload)
        if event is None or self._disabled:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            if not self._dropped:
                self._dropped = True
                logger.warning("后台子智能体实时事件队列已满，后续预览事件将丢弃: task_id=%s", self._task_id)

    async def aclose(self) -> None:
        if self._pump_task is None:
            return
        try:
            await self._queue.put(_END)
            await self._pump_task
        finally:
            self._pump_task = None
            if self._owner_token is not None:
                try:
                    await self._service.release_stream(self._task_id, self._owner_token)
                except Exception:
                    logger.debug("释放后台子智能体事件流 owner 失败: task_id=%s", self._task_id, exc_info=True)
                self._owner_token = None

    async def _pump(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _END:
                return
            assert isinstance(item, dict)
            assert self._owner_token is not None
            try:
                await self._service.publish_event(
                    task_id=self._task_id,
                    conversation_id=self._conversation_id,
                    user_id=self._user_id,
                    owner_token=self._owner_token,
                    event=item,
                )
            except Exception:
                logger.warning("写入后台子智能体实时事件失败: task_id=%s", self._task_id, exc_info=True)

    @staticmethod
    def _extract_event(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        if payload.get("type") == "subagent_tool":
            event = payload.get("event")
            return event if isinstance(event, dict) else None
        return payload if isinstance(payload.get("event_type"), str) else None


subagent_task_event_service = SubagentTaskEventService()
