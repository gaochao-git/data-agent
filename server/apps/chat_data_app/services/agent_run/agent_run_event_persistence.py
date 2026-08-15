"""Durable MySQL consumer for Redis Run events."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from utils.database import get_session
from utils.logger import get_logger
from ...daos.agent_run_event_dao import agent_run_event_dao
from .redis_event_bus import RedisEventBus

logger = get_logger(__name__)


class AgentRunEventPersistence:
    """Persist Redis events asynchronously without slowing Agent token output."""

    def __init__(self, bus: RedisEventBus) -> None:
        self._bus = bus
        self._task: asyncio.Task | None = None
        self._consumer = f"{__name__}:{uuid4().hex}"

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="chatdata-run-event-persistence")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                rows = await self._bus.reclaim_persistence_batch(consumer=self._consumer)
                if not rows:
                    rows = await self._bus.read_persistence_batch(consumer=self._consumer)
                if rows:
                    await self._persist(rows)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("持久化 Agent Run 事件失败", exc_info=True)
                await asyncio.sleep(1)

    async def _persist(self, rows: list[tuple[str, dict[str, str]]]) -> None:
        async with get_session() as session:
            for _stream_id, event in rows:
                await agent_run_event_dao.append(
                    session,
                    run_id=event["run_id"],
                    conversation_id=event["conversation_id"],
                    user_id=event["user_id"],
                    sequence_no=int(event["sequence_no"]),
                    event_id=event["event_id"],
                    event_type=event["event_type"],
                    payload=event["payload"],
                    now=None,
                )
        await self._bus.ack_persistence([stream_id for stream_id, _event in rows])
