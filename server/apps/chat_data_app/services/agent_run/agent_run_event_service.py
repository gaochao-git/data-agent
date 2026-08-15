"""后台 Run 事件服务，提供可重放的 SSE 事件读取。"""

from __future__ import annotations

import asyncio
import re

from utils.database import get_session
from utils.logger import get_logger
from ...daos.agent_inbox_dao import AgentInboxDao, agent_inbox_dao
from ...daos.agent_run_event_dao import AgentRunEventDao, agent_run_event_dao
from .redis_event_bus import RedisEventBus, build_redis_event_bus


_EVENT_NAME_RE = re.compile(r"(?:^|\n)event:\s*([^\n]+)")
logger = get_logger(__name__)


class AgentRunEventService:
    """追加运行事件，并按 seq 为 SSE 订阅者提供断线续传。"""

    def __init__(
        self,
        inbox_dao: AgentInboxDao = agent_inbox_dao,
        event_dao: AgentRunEventDao = agent_run_event_dao,
        bus: RedisEventBus | None = None,
    ) -> None:
        self._inbox_dao = inbox_dao
        self._event_dao = event_dao
        self._bus = bus or build_redis_event_bus()

    @property
    def bus(self) -> RedisEventBus:
        return self._bus

    async def append(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        event_id: str,
        payload: str,
        owner_token: str,
    ) -> None:
        # Event publishing is the token hot path. Do not open a MySQL
        # transaction or lock the Run row for every visible event. Sequence
        # recovery is the only exceptional database read, needed after Redis
        # stream expiration or Redis data loss.
        initial_sequence = 0
        if not await self._bus.sequence_exists(run_id):
            async with get_session() as session:
                initial_sequence = await self._event_dao.get_max_sequence(session, run_id=run_id)
        await self._bus.publish(
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            event_id=event_id,
            event_type=self._event_type(payload),
            payload=payload,
            initial_sequence=initial_sequence,
        )

    async def stream(
        self,
        *,
        run_id: str,
        last_event_id: str = "0-0",
        is_disconnected=None,
    ):
        """订阅 Redis Stream；断开后结束订阅，不影响后台 Run。"""
        cursor = last_event_id or "0-0"
        after_seq = await self._resolve_sequence(run_id, cursor)
        redis_unavailable = False
        while True:
            if is_disconnected and await is_disconnected():
                return
            try:
                events = await self._bus.read_run(run_id, cursor)
                stream_exists = True
                if redis_unavailable:
                    logger.info("Redis Run 事件读取已恢复: run_id=%s", run_id)
                    redis_unavailable = False
            except Exception:
                if not redis_unavailable:
                    logger.warning("Redis Run 事件读取失败，切换 MySQL 补偿: run_id=%s", run_id, exc_info=True)
                redis_unavailable = True
                events = []
                stream_exists = False
            if events:
                for event in events:
                    cursor = event["stream_id"]
                    after_seq = max(after_seq, int(event.get("sequence_no") or 0))
                    yield event
                    if event.get("event_type") == "done":
                        return
                continue
            # A stream can still exist after its oldest entries were trimmed.
            # Always consult durable history on an empty Redis read so a stale
            # Last-Event-ID is recoverable in both cases.
            try:
                durable_events = await self._read_durable_events(run_id, after_seq)
            except Exception:
                logger.warning("读取 Run 事件持久化补偿失败，保持订阅等待重试: run_id=%s", run_id, exc_info=True)
                await asyncio.sleep(0.5)
                continue
            if durable_events:
                for event in durable_events:
                    cursor = event["stream_id"]
                    after_seq = int(event["sequence_no"])
                    yield event
                    if event.get("event_type") == "done":
                        return
                continue
            if not stream_exists:
                await asyncio.sleep(0.5)
            else:
                try:
                    stream_exists = await self._bus.run_stream_exists(run_id)
                except Exception:
                    stream_exists = False
                if not stream_exists:
                    await asyncio.sleep(0.5)
            try:
                snapshot = await self._get_run_snapshot(run_id)
            except Exception:
                logger.warning("读取 Run 状态失败，保持 SSE 订阅等待重试: run_id=%s", run_id, exc_info=True)
                await asyncio.sleep(0.5)
                continue
            if snapshot is None or snapshot["status"] in {
                "finished",
                "succeeded",
                "failed",
                "cancelled",
                "waiting_user",
            }:
                return

    async def _resolve_sequence(self, run_id: str, cursor: str) -> int:
        if cursor == "0-0":
            return 0
        try:
            async with get_session() as session:
                return await self._event_dao.get_sequence_by_event_id(
                    session,
                    run_id=run_id,
                    event_id=cursor,
                ) or 0
        except Exception:
            # Redis remains the primary live source. If MySQL is temporarily
            # unavailable, use the Redis cursor and let durable replay retry
            # on the next empty read instead of breaking the SSE connection.
            logger.warning("解析 Run 事件游标失败，回退 Redis 游标: run_id=%s", run_id, exc_info=True)
            return 0

    async def _read_durable_events(self, run_id: str, after_seq: int) -> list[dict[str, str]]:
        async with get_session() as session:
            rows = await self._event_dao.list_after(
                session,
                run_id=run_id,
                after_seq=after_seq,
            )
            return [
                {
                    "stream_id": row.event_id,
                    "run_id": row.run_id,
                    "conversation_id": row.conversation_id,
                    "user_id": row.user_id,
                    "event_id": row.event_id,
                    "event_type": row.event_type,
                    "sequence_no": str(row.sequence_no),
                    "payload": row.payload,
                }
                for row in rows
            ]

    async def _get_run_snapshot(self, run_id: str) -> dict | None:
        async with get_session() as session:
            row = await self._inbox_dao.get_run_by_id(session, run_id)
            if row is None:
                return None
            return {"status": row.status, "conversation_id": row.conversation_id, "user_id": row.user_id}

    @staticmethod
    def _event_type(payload: str) -> str:
        match = _EVENT_NAME_RE.search(payload or "")
        return match.group(1).strip() if match else "message"


agent_run_event_service = AgentRunEventService()
