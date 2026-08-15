"""进程内有界并发的 ChatData Agent Run Runtime。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from uuid import uuid4

from utils.logger import get_logger
from .agent_inbox_service import AgentInboxService, agent_inbox_service
from .agent_run_event_service import AgentRunEventService, agent_run_event_service
from .agent_run_event_persistence import AgentRunEventPersistence
from .redis_event_bus import RedisEventBus

logger = get_logger(__name__)


def _max_concurrency() -> int:
    raw = os.getenv("CHAT_DATA_AGENT_MAX_CONCURRENCY", "4")
    try:
        return max(int(raw), 1)
    except ValueError:
        raise ValueError("CHAT_DATA_AGENT_MAX_CONCURRENCY 必须是正整数") from None


class AgentRunRuntime:
    """单进程并发执行多个 Run；每个 Run 的事件先持久化再供 SSE 读取。"""

    def __init__(
        self,
        *,
        max_concurrency: int | None = None,
        inbox_service: AgentInboxService = agent_inbox_service,
        event_service: AgentRunEventService = agent_run_event_service,
        event_bus: RedisEventBus | None = None,
        event_persistence: AgentRunEventPersistence | None = None,
    ) -> None:
        self._max_concurrency = max_concurrency or _max_concurrency()
        self._inbox_service = inbox_service
        self._event_service = event_service
        self._event_bus = event_bus or event_service.bus
        self._event_persistence = event_persistence or AgentRunEventPersistence(self._event_bus)
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._tasks: dict[str, asyncio.Task] = {}
        self._poll_task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._event_bus.start()
        await self._event_persistence.start()
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_pending_runs(), name="chatdata-agent-run-dispatcher")

    def submit(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        stream_factory: Callable[[], AsyncIterator[str]] | None = None,
    ) -> None:
        if run_id in self._tasks:
            return
        task = asyncio.create_task(
            self._execute(
                run_id=run_id,
                conversation_id=conversation_id,
                user_id=user_id,
                operator_name=operator_name,
                stream_factory=stream_factory,
            ),
            name=f"chatdata-agent-run-{run_id}",
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(run_id, None))

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        await self._event_persistence.stop()
        await self._event_bus.close()

    async def _poll_pending_runs(self) -> None:
        while True:
            try:
                if len(self._tasks) < self._max_concurrency:
                    pending = await self._list_pending_runs()
                    for run_id in pending:
                        if run_id in self._tasks:
                            continue
                        self.submit(
                            run_id=run_id,
                            conversation_id="",
                            user_id="",
                            operator_name="",
                        )
                await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("ChatData Agent Run 调度失败", exc_info=True)
                await asyncio.sleep(1)

    async def _list_pending_runs(self) -> list[str]:
        from utils.database import get_session
        from ...daos.agent_inbox_dao import agent_inbox_dao

        async with get_session() as session:
            rows = await agent_inbox_dao.list_pending_runs(session, self._max_concurrency)
            return [row.run_id for row in rows]

    @staticmethod
    def _build_stream_factory(prepared: dict) -> Callable[[], AsyncIterator[str]]:
        context = prepared["runtime_context"]

        def factory() -> AsyncIterator[str]:
            from ...handlers import _generate_chat_data_stream

            return _generate_chat_data_stream(
                thread_id=prepared["conversation_id"],
                user_query=str(context.get("query") or ""),
                datasource_id=context.get("datasource_id"),
                user_id=prepared["user_id"],
                username=prepared.get("operator_name") or prepared["user_id"],
                member_datasource_ids=context.get("member_datasource_ids") or [],
                semantic_config_id=context.get("semantic_config_id"),
                request=None,
                model_id=context.get("model_id"),
                semantic_config_selection=context.get("semantic_config_selection"),
                enable_thinking=context.get("enable_thinking"),
                skill_names=context.get("skill_names") or [],
                options=context.get("options") or {},
                file_ids=context.get("file_ids"),
                run_id=prepared["run_id"],
                adopted_messages=prepared.get("messages") or [],
                resume_answer=prepared.get("resume_answer"),
                resume_turn_id=prepared.get("resume_turn_id"),
            )

        return factory

    async def _execute(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        stream_factory: Callable[[], AsyncIterator[str]] | None,
    ) -> None:
        async with self._semaphore:
            prepared = await self._inbox_service.prepare_pending_run(run_id)
            if prepared is None:
                return
            conversation_id = prepared["conversation_id"]
            user_id = prepared["user_id"]
            operator_name = prepared.get("operator_name") or operator_name or user_id
            if stream_factory is None:
                stream_factory = self._build_stream_factory(prepared)
            try:
                async for payload in stream_factory():
                    await self._event_service.append(
                        run_id=run_id,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        # A Run may resume after waiting_user; event IDs must
                        # remain unique across all execution segments.
                        event_id=f"{run_id}:{uuid4().hex}",
                        payload=payload,
                    )
            except asyncio.CancelledError:
                await self._inbox_service.finish_run(run_id, "cancelled", user_id, operator_name)
                raise
            except Exception:
                logger.error("ChatData Agent 后台运行失败: run_id=%s", run_id, exc_info=True)
                await self._inbox_service.finish_run(run_id, "failed", user_id, operator_name)


agent_run_runtime = AgentRunRuntime()
