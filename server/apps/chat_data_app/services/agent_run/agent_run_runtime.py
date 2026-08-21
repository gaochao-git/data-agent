"""进程内有界并发的 ChatData Agent Run Runtime。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import datetime
from uuid import uuid4

from utils.logger import get_logger
from .agent_inbox_service import AgentRunLeaseLostError, AgentInboxService, agent_inbox_service
from .agent_run_event_service import AgentRunEventService, agent_run_event_service
from .redis_event_bus import RedisEventBus

logger = get_logger(__name__)


def _max_concurrency() -> int:
    raw = os.getenv("CHAT_DATA_AGENT_MAX_CONCURRENCY", "4")
    try:
        return max(int(raw), 1)
    except ValueError:
        raise ValueError("CHAT_DATA_AGENT_MAX_CONCURRENCY 必须是正整数") from None


class AgentRunRuntime:
    """单进程并发执行多个 Run；事件经 Redis Stream 实时发布。"""

    def __init__(
        self,
        *,
        max_concurrency: int | None = None,
        inbox_service: AgentInboxService = agent_inbox_service,
        event_service: AgentRunEventService = agent_run_event_service,
        event_bus: RedisEventBus | None = None,
        stream_generator: Callable[..., AsyncIterator[str]] | None = None,
    ) -> None:
        self._max_concurrency = max_concurrency or _max_concurrency()
        self._inbox_service = inbox_service
        self._event_service = event_service
        self._event_bus = event_bus or event_service.bus
        self._stream_generator = stream_generator
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._tasks: dict[str, asyncio.Task] = {}
        self._poll_task: asyncio.Task | None = None

    def set_stream_generator(self, stream_generator: Callable[..., AsyncIterator[str]]) -> None:
        """由上层（handlers）在启动装配时注入 Run 流生成器，避免服务层反向 import。"""
        self._stream_generator = stream_generator

    async def start(self) -> None:
        await self._event_bus.start()
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_pending_runs(), name="chatdata-agent-run-dispatcher")

    def submit(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        operator_name: str,
    ) -> None:
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            return
        if existing is not None:
            self._tasks.pop(run_id, None)
        task = asyncio.create_task(
            self._execute(
                run_id=run_id,
                conversation_id=conversation_id,
                user_id=user_id,
                operator_name=operator_name,
            ),
            name=f"chatdata-agent-run-{run_id}",
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda completed: self._forget_task(run_id, completed))

    def _forget_task(self, run_id: str, completed: asyncio.Task) -> None:
        if self._tasks.get(run_id) is completed:
            self._tasks.pop(run_id, None)

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        tasks = list(self._tasks.values())
        for task in tasks:
            # Shutdown is not an explicit user cancellation. Leave the DB
            # lease so another process can recover the Run after restart.
            task.cancel("runtime_shutdown")
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        await self._event_bus.close()

    async def _poll_pending_runs(self) -> None:
        while True:
            try:
                available_slots = self._max_concurrency - len(self._tasks)
                if available_slots > 0:
                    runnable = await self._list_runnable_runs(available_slots)
                    for run_id in runnable:
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

    async def _list_runnable_runs(self, limit: int) -> list[str]:
        from utils.database import get_session
        from ...daos.agent_inbox_dao import agent_inbox_dao

        async with get_session() as session:
            rows = await agent_inbox_dao.list_runnable_runs(session, datetime.now(), limit)
            return [row.run_id for row in rows]

    def _build_stream_factory(self, prepared: dict) -> Callable[[], AsyncIterator[str]]:
        context = prepared["runtime_context"]
        stream_generator = self._stream_generator
        if stream_generator is None:
            raise RuntimeError("AgentRunRuntime 未装配 stream_generator，请在启动时调用 set_stream_generator()")

        def factory() -> AsyncIterator[str]:
            return stream_generator(
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
                owner_token=prepared["owner_token"],
                adopted_messages=prepared.get("messages") or [],
                resume_answer=prepared.get("resume_answer"),
                resume_started=bool(prepared.get("resume_started")),
                resume_turn_id=prepared.get("resume_turn_id"),
                turn_id=prepared.get("turn_id"),
                recovering=bool(prepared.get("recovering")),
            )

        return factory

    async def _execute(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        operator_name: str,
    ) -> None:
        async with self._semaphore:
            prepared = None
            terminal_payload: str | None = None
            terminal_ready = False
            try:
                prepared = await self._inbox_service.prepare_pending_run(run_id)
                if prepared is None:
                    return
                conversation_id = prepared["conversation_id"]
                user_id = prepared["user_id"]
                operator_name = prepared.get("operator_name") or operator_name or user_id
                await self._event_service.claim_owner(run_id, prepared["owner_token"])
                stream_factory = self._build_stream_factory(prepared)
                async for payload in stream_factory():
                    if self._event_service.is_terminal_payload(payload):
                        terminal_payload = payload
                        continue
                    await self._event_service.append(
                        run_id=run_id,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        # A Run may resume after waiting_user; event IDs must
                        # remain unique across all execution segments.
                        event_id=f"{run_id}:{uuid4().hex}",
                        payload=payload,
                        owner_token=prepared["owner_token"],
                    )
                terminal_ready = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if isinstance(exc, AgentRunLeaseLostError):
                    logger.info("ChatData Agent Run 租约已丢失，停止旧执行: run_id=%s", run_id)
                    return
                logger.error("ChatData Agent 后台运行失败: run_id=%s", run_id, exc_info=True)
                if prepared is not None:
                    try:
                        await self._inbox_service.finish_run(
                            run_id,
                            "failed",
                            user_id,
                            operator_name,
                            prepared["owner_token"],
                        )
                        terminal_ready = True
                    except AgentRunLeaseLostError:
                        logger.info("ChatData Agent Run 失败收口时租约已丢失: run_id=%s", run_id)
            finally:
                if prepared is not None:
                    if terminal_payload is not None and terminal_ready:
                        try:
                            terminal_stream_id = await self._event_service.append(
                                run_id=run_id,
                                conversation_id=conversation_id,
                                user_id=user_id,
                                event_id=f"{run_id}:{uuid4().hex}",
                                payload=terminal_payload,
                                owner_token=prepared["owner_token"],
                            )
                        except AgentRunLeaseLostError:
                            logger.info("ChatData Agent Run 终止事件发布时租约已丢失: run_id=%s", run_id)
                        except Exception:
                            logger.error("ChatData Agent Run 终止事件发布失败: run_id=%s", run_id, exc_info=True)
                        else:
                            # done 已进 Redis Stream；把游标落到 inbox 行，
                            # 供问答恢复在 Redis 失效时做 done 检测与游标兜底。
                            try:
                                await self._inbox_service.record_terminal_event(run_id, terminal_stream_id)
                            except Exception:
                                logger.warning(
                                    "ChatData Agent Run 终止游标落库失败（Redis 内 done 仍可用）: run_id=%s",
                                    run_id,
                                    exc_info=True,
                                )
                    try:
                        await self._event_service.release_owner(run_id, prepared["owner_token"])
                    except Exception:
                        logger.warning("ChatData Agent Run Redis owner 清理失败: run_id=%s", run_id, exc_info=True)


agent_run_runtime = AgentRunRuntime()
