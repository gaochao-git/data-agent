"""后台 Run 事件服务，提供可重放的 SSE 事件读取。"""

from __future__ import annotations

import asyncio
import json
import re

from utils.database import get_session
from utils.logger import get_logger
from ...daos.agent_inbox_dao import AgentInboxDao, agent_inbox_dao
from .agent_inbox_service import AgentRunLeaseLostError
from .redis_event_bus import RedisEventBus, RedisRunOwnerMismatchError, build_redis_event_bus


_EVENT_NAME_RE = re.compile(r"(?:^|\n)event:\s*([^\n]+)")
logger = get_logger(__name__)
QUESTION_DONE_WAIT_SECONDS = 10.0
QUESTION_DONE_DURABLE_POLL_SECONDS = 0.5
TERMINAL_PERSISTENCE_GRACE_SECONDS = 10.0


class AgentRunEventService:
    """追加运行事件，并为 SSE 订阅者提供断线续传。"""

    def __init__(
        self,
        inbox_dao: AgentInboxDao = agent_inbox_dao,
        bus: RedisEventBus | None = None,
    ) -> None:
        self._inbox_dao = inbox_dao
        self._bus = bus or build_redis_event_bus()

    @property
    def bus(self) -> RedisEventBus:
        return self._bus

    async def claim_owner(self, run_id: str, owner_token: str) -> None:
        try:
            await self._bus.claim_run_owner(run_id, owner_token)
        except RedisRunOwnerMismatchError as exc:
            raise AgentRunLeaseLostError(f"Run 所有权已变更: {run_id}") from exc

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
    ) -> str:
        """发布事件到 Redis 实时 Stream，返回其 stream_id。

        Run 事件不落 MySQL。Redis 丢失后序列从 1 重新开始也无碍：
        sequence_no 只在单个 Stream 生命周期内排序，没有跨段消费方。
        """
        event_type = self._event_type(payload)
        try:
            return await self._bus.publish(
                run_id=run_id,
                conversation_id=conversation_id,
                user_id=user_id,
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                owner_token=owner_token,
            )
        except RedisRunOwnerMismatchError as exc:
            raise AgentRunLeaseLostError(f"Run 所有权已变更: {run_id}") from exc

    async def latest_event_id(self, run_id: str) -> str:
        """Get a resume cursor before a checkpoint run is resumed.

        A waiting-user Run has already emitted its terminal SSE event. The
        answer endpoint must start after that event, otherwise the old
        question card and ``done`` event are replayed as if they were part of
        the resumed answer. Redis holds the live tail; the inbox row's
        ``terminal_event`` (written after the done publish succeeded) is the
        durable fallback when the Redis stream has expired.
        """
        try:
            event_id = await self._bus.latest_run_event_id(run_id)
            if event_id:
                return event_id
        except Exception:
            logger.warning("读取 Redis Run 尾游标失败，回退 inbox 终止游标: run_id=%s", run_id, exc_info=True)

        terminal_event = await self._get_terminal_event(run_id)
        return str((terminal_event or {}).get("stream_id") or "") or "0-0"

    async def wait_for_done(self, run_id: str, timeout: float = QUESTION_DONE_WAIT_SECONDS) -> bool:
        """Wait briefly for the interrupt segment's terminal SSE event."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout, 0.0)
        next_durable_check = 0.0
        while True:
            try:
                event = await self._bus.latest_run_event(run_id)
            except Exception:
                logger.warning("等待问题 Run 终止事件时 Redis 不可用，检查 inbox 终止记录: run_id=%s", run_id, exc_info=True)
                event = None
            if event and event.get("event_type") == "done":
                return True
            now = loop.time()
            if now >= next_durable_check:
                try:
                    terminal_event = await self._get_terminal_event(run_id)
                    if terminal_event is not None:
                        return True
                except Exception:
                    logger.warning("等待问题 Run 终止事件时 inbox 查询失败: run_id=%s", run_id, exc_info=True)
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
        """订阅 Redis Stream；断开后结束订阅，不影响后台 Run。

        Redis Stream 是唯一的可重放事件源。流已过期（超过 TTL）时不补偿
        中间过程：订阅在 Run 终态后结束，刷新页面走会话历史回放最终结果。
        """
        cursor = last_event_id or "0-0"
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
                    logger.warning("Redis Run 事件读取失败，等待恢复: run_id=%s", run_id, exc_info=True)
                redis_unavailable = True
                events = []
                stream_exists = False
            if events:
                terminal_detected_at = None
                for event in events:
                    cursor = event["stream_id"]
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
                # inbox 终态先于 done 事件发布提交；宽限期内继续读流，
                # 等待终止事件到达后再结束订阅。
                now = asyncio.get_running_loop().time()
                if terminal_detected_at is None:
                    terminal_detected_at = now
                if now - terminal_detected_at >= TERMINAL_PERSISTENCE_GRACE_SECONDS:
                    return
                await asyncio.sleep(0.2)
                continue
            terminal_detected_at = None

    async def _get_terminal_event(self, run_id: str) -> dict | None:
        """读取 inbox Run 行记录的终止事件游标（done 发布成功后写入）。"""
        async with get_session() as session:
            row = await self._inbox_dao.get_run_by_id(session, run_id)
        if row is None or not row.content:
            return None
        try:
            content = json.loads(row.content)
        except (TypeError, ValueError):
            return None
        terminal_event = content.get("terminal_event")
        return terminal_event if isinstance(terminal_event, dict) else None

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
