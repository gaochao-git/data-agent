"""后台 Run 事件服务，提供可重放的 SSE 事件读取。"""

from __future__ import annotations

import asyncio
import re

from utils.database import get_session
from utils.logger import get_logger
from ...daos.agent_inbox_dao import AgentInboxDao, agent_inbox_dao
from ...daos.agent_run_event_dao import AgentRunEventDao, agent_run_event_dao
from .agent_inbox_service import AgentRunLeaseLostError
from .redis_event_bus import RedisEventBus, RedisRunOwnerMismatchError, build_redis_event_bus


_EVENT_NAME_RE = re.compile(r"(?:^|\n)event:\s*([^\n]+)")
logger = get_logger(__name__)
QUESTION_DONE_WAIT_SECONDS = 10.0
QUESTION_DONE_DURABLE_POLL_SECONDS = 0.5
TERMINAL_PERSISTENCE_GRACE_SECONDS = 10.0


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
        # A Run is published by one owner in order. Avoid an EXISTS round trip
        # for every token while still recovering the sequence on the first
        # event handled by a new worker process.
        self._sequence_initialized_runs: set[str] = set()

    @property
    def bus(self) -> RedisEventBus:
        return self._bus

    async def claim_owner(self, run_id: str, owner_token: str) -> None:
        try:
            await self._bus.claim_run_owner(run_id, owner_token)
        except RedisRunOwnerMismatchError as exc:
            raise AgentRunLeaseLostError(f"Run 所有权已变更: {run_id}") from exc
        # A new execution segment may start after Redis stream expiry or data
        # loss. Re-check the durable maximum before its first published event.
        self._sequence_initialized_runs.discard(run_id)

    async def release_owner(self, run_id: str, owner_token: str) -> None:
        await self._bus.release_run_owner(run_id, owner_token)

    async def renew_owner(self, run_id: str, owner_token: str) -> None:
        try:
            await self._bus.renew_run_owner(run_id, owner_token)
        except RedisRunOwnerMismatchError as exc:
            raise AgentRunLeaseLostError(f"Run 所有权已变更: {run_id}") from exc

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
        event_type = self._event_type(payload)
        initial_sequence = 0
        needs_sequence_check = run_id not in self._sequence_initialized_runs
        if needs_sequence_check and not await self._bus.sequence_exists(run_id):
            async with get_session() as session:
                initial_sequence = await self._event_dao.get_max_sequence(session, run_id=run_id)
        try:
            await self._bus.publish(
                run_id=run_id,
                conversation_id=conversation_id,
                user_id=user_id,
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                owner_token=owner_token,
                initial_sequence=initial_sequence,
            )
        except RedisRunOwnerMismatchError as exc:
            if needs_sequence_check:
                self._sequence_initialized_runs.discard(run_id)
            raise AgentRunLeaseLostError(f"Run 所有权已变更: {run_id}") from exc
        except Exception:
            if needs_sequence_check:
                self._sequence_initialized_runs.discard(run_id)
            raise
        self._sequence_initialized_runs.add(run_id)
        if event_type == "done":
            self._sequence_initialized_runs.discard(run_id)

    async def latest_event_id(self, run_id: str) -> str:
        """Get a resume cursor before a checkpoint run is resumed.

        A waiting-user Run has already emitted its terminal SSE event. The
        answer endpoint must start after that event, otherwise the old
        question card and ``done`` event are replayed as if they were part of
        the resumed answer.
        """
        try:
            event_id = await self._bus.latest_run_event_id(run_id)
            if event_id:
                return event_id
        except Exception:
            logger.warning("读取 Redis Run 尾游标失败，回退持久化事件: run_id=%s", run_id, exc_info=True)

        try:
            async with get_session() as session:
                event_id = await self._event_dao.get_latest_event_id(session, run_id=run_id)
        except Exception:
            logger.error("读取 Run 恢复游标失败: run_id=%s", run_id, exc_info=True)
            raise
        return event_id or "0-0"

    async def wait_for_done(self, run_id: str, timeout: float = QUESTION_DONE_WAIT_SECONDS) -> bool:
        """Wait briefly for the interrupt segment's terminal SSE event."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout, 0.0)
        next_durable_check = 0.0
        while True:
            try:
                event = await self._bus.latest_run_event(run_id)
            except Exception:
                logger.warning("等待问题 Run 终止事件时 Redis 不可用，检查持久化事件: run_id=%s", run_id, exc_info=True)
                event = None
            if event and event.get("event_type") == "done":
                return True
            now = loop.time()
            if now >= next_durable_check:
                try:
                    async with get_session() as session:
                        latest_event_type = await self._event_dao.get_latest_event_type(session, run_id=run_id)
                    if latest_event_type == "done":
                        return True
                except Exception:
                    logger.warning("等待问题 Run 终止事件时持久化查询失败: run_id=%s", run_id, exc_info=True)
                next_durable_check = now + QUESTION_DONE_DURABLE_POLL_SECONDS
            if now >= deadline:
                return False
            await asyncio.sleep(0.05)

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
        terminal_detected_at: float | None = None
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
                now = asyncio.get_running_loop().time()
                if terminal_detected_at is None:
                    terminal_detected_at = now
                if now - terminal_detected_at >= TERMINAL_PERSISTENCE_GRACE_SECONDS:
                    return
                await asyncio.sleep(0.2)
                continue
            terminal_detected_at = None

    async def _resolve_sequence(self, run_id: str, cursor: str) -> int:
        if cursor == "0-0":
            return 0
        try:
            sequence_no = await self._bus.run_event_sequence(run_id, cursor)
            if sequence_no is not None:
                return sequence_no
        except Exception:
            logger.warning("从 Redis 解析 Run 事件游标失败，回退持久化事件: run_id=%s", run_id, exc_info=True)
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

    @classmethod
    def is_terminal_payload(cls, payload: str) -> bool:
        return cls._event_type(payload) == "done"


agent_run_event_service = AgentRunEventService()
