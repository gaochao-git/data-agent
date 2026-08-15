"""后台 Run 事件服务，提供可重放的 SSE 事件读取。"""

from __future__ import annotations

import re

from utils.database import get_session
from ...daos.agent_inbox_dao import AgentInboxDao, agent_inbox_dao
from .redis_event_bus import RedisEventBus, build_redis_event_bus


_EVENT_NAME_RE = re.compile(r"(?:^|\n)event:\s*([^\n]+)")


class AgentRunEventService:
    """追加运行事件，并按 seq 为 SSE 订阅者提供断线续传。"""

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

    async def append(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        event_id: str,
        payload: str,
    ) -> None:
        await self._bus.publish(
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            event_id=event_id,
            event_type=self._event_type(payload),
            payload=payload,
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
        while True:
            if is_disconnected and await is_disconnected():
                return
            events = await self._bus.read_run(run_id, cursor)
            if events:
                for event in events:
                    cursor = event["stream_id"]
                    yield event
                continue
            snapshot = await self._get_run_snapshot(run_id)
            if snapshot is None or snapshot["status"] in {
                "finished",
                "succeeded",
                "failed",
                "cancelled",
                "waiting_user",
            }:
                return

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
