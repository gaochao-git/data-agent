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
from .agent_run_event_service import AgentRunEventService, agent_run_event_service, format_terminal_done
from .redis_event_bus import RedisEventBus

logger = get_logger(__name__)

RUN_DISPATCH_POLL_SECONDS = 2.0


def _sse_event_type(payload: str) -> str | None:
    """从 SSE payload 中解析事件名（如 "message" / "question_requested"）。"""
    for line in payload.splitlines():
        if line.startswith("event: "):
            return line[len("event: "):].strip()
    return None


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
        self._owned_runs: set[str] = set()
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

    def cancel_local(self, run_id: str) -> bool:
        """停止请求恰好落在执行者进程时，直接取消本地任务（毫秒级 fast path）。

        只触发取消，不代写状态：收口仍由执行段按取消原因走 finally 完成，
        与心跳读到 cancelling 后自取消是同一条收口路径。任务不在本进程时
        返回 False，由执行者心跳（2 秒周期）兜底。
        """
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        # 只中断已经完成 DB prepare + Redis owner 领取的执行。尚在排队或
        # prepare 中的任务继续走到状态检查，自行观察 cancelling/cancelled，
        # 避免 prepared/owner 尚未建立时被拦腰取消而无法发布终态。
        if run_id not in self._owned_runs:
            return False
        task.cancel("agent_run_cancelled")
        return True

    async def cancel_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        operator_name: str,
    ) -> bool:
        """取消 Run 的唯一编排入口：先提交 MySQL 状态，再停止执行并发布终态。"""
        result = await self._inbox_service.cancel_active_run(
            conversation_id,
            user_id,
            operator_name,
            run_id,
        )
        if not result.cancelled:
            return False
        if result.status and result.terminal_owner_token:
            await self._publish_closed_terminal(
                run_id=run_id,
                conversation_id=conversation_id,
                user_id=user_id,
                status=result.status,
                owner_token=result.terminal_owner_token,
            )
            return True
        self.cancel_local(run_id)
        return True

    async def _publish_closed_terminal(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        status: str,
        owner_token: str,
    ) -> None:
        """发布已经在 MySQL 收口的 Run；失败时 SSE 会用 Run 状态补发。"""
        owner_claimed = False
        try:
            await self._event_service.claim_owner(run_id, owner_token)
            owner_claimed = True
            stream_id = await self._event_service.append(
                run_id=run_id,
                conversation_id=conversation_id,
                user_id=user_id,
                event_id=f"{run_id}:terminal:{owner_token}",
                payload=format_terminal_done(status),
                owner_token=owner_token,
            )
            await self._inbox_service.record_terminal_event(run_id, stream_id)
        except Exception:
            logger.warning(
                "ChatData 已收口 Run 的 done 发布失败，将由 MySQL 终态兜底: run_id=%s",
                run_id,
                exc_info=True,
            )
        finally:
            if owner_claimed:
                try:
                    await self._event_service.release_owner(run_id, owner_token)
                except Exception:
                    logger.warning("ChatData Run 取消发布 owner 清理失败: run_id=%s", run_id, exc_info=True)

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
        self._owned_runs.clear()
        await self._event_bus.close()

    async def _poll_pending_runs(self) -> None:
        while True:
            try:
                await self._reap_expired_runs()
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
                await asyncio.sleep(RUN_DISPATCH_POLL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("ChatData Agent Run 调度失败", exc_info=True)
                await asyncio.sleep(1)

    async def _reap_expired_runs(self) -> None:
        """后台收口 Worker 崩溃留下的过期 Run，并结束其 SSE。"""
        results = await self._inbox_service.reap_expired_runs(limit=20)
        for result in results:
            await self._publish_closed_terminal(
                run_id=result.run_id,
                conversation_id=result.conversation_id,
                user_id=result.user_id,
                status=result.status,
                owner_token=result.owner_token,
            )

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
                thinking_effort=context.get("thinking_effort"),
                skill_names=context.get("skill_names") or [],
                options=context.get("options") or {},
                file_ids=context.get("file_ids"),
                run_id=prepared["run_id"],
                owner_token=prepared["owner_token"],
                stream_event_id_provider=prepared.get("stream_event_id_provider"),
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
            next_run_id_to_submit: str | None = None
            try:
                prepared = await self._inbox_service.prepare_pending_run(run_id)
                if prepared is None:
                    return
                conversation_id = prepared["conversation_id"]
                user_id = prepared["user_id"]
                operator_name = prepared.get("operator_name") or operator_name or user_id
                await self._event_service.claim_owner(run_id, prepared["owner_token"])
                self._owned_runs.add(run_id)
                if await self._inbox_service.heartbeat_run(run_id, prepared["owner_token"]) == "cancelling":
                    raise asyncio.CancelledError("agent_run_cancelled")
                event_cursor: dict[str, str | None] = {"stream_id": None}
                prepared["stream_event_id_provider"] = lambda: event_cursor["stream_id"]
                stream_factory = self._build_stream_factory(prepared)
                saw_question_requested = False
                async for payload in stream_factory():
                    # 生成器只产出事实；question_requested 是 waiting_user 的标记事实。
                    if _sse_event_type(payload) == "question_requested":
                        saw_question_requested = True
                    stream_id = await self._event_service.append(
                        run_id=run_id,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        # A Run may resume after waiting_user; event IDs must
                        # remain unique across all execution segments.
                        event_id=f"{run_id}:{uuid4().hex}",
                        payload=payload,
                        owner_token=prepared["owner_token"],
                    )
                    event_cursor["stream_id"] = stream_id
                # 正常结束：唯一生命周期 owner 在此收口——先落库，再发布结构化 done。
                if saw_question_requested:
                    # waiting_user 已由 mark_waiting_for_user 落库，无需重复收口。
                    terminal_payload = format_terminal_done("waiting_user")
                else:
                    completion = await self._inbox_service.complete_run(
                        run_id,
                        user_id,
                        operator_name,
                        prepared["owner_token"],
                    )
                    terminal_payload = format_terminal_done(completion.status, completion.next_run_id)
                    if completion.status == "finished":
                        next_run_id_to_submit = completion.next_run_id
                terminal_ready = True
            except asyncio.CancelledError as exc:
                if prepared is not None and exc.args and exc.args[0] == "agent_run_cancelled":
                    # 用户主动停止也是终态：落库 cancelled 后发布结构化 done。
                    # 租约丢失的取消不收口也不发布——流已归新 Worker 所有。
                    try:
                        finished_status = await self._inbox_service.finish_run(
                            run_id,
                            "cancelled",
                            user_id,
                            operator_name,
                            prepared["owner_token"],
                        )
                        if finished_status:
                            terminal_payload = format_terminal_done(finished_status)
                            terminal_ready = True
                    except AgentRunLeaseLostError:
                        logger.info("ChatData Agent Run 取消收口时租约已丢失: run_id=%s", run_id)
                raise
            except Exception as exc:
                if isinstance(exc, AgentRunLeaseLostError):
                    logger.info("ChatData Agent Run 租约已丢失，停止旧执行: run_id=%s", run_id)
                    return
                logger.error("ChatData Agent 后台运行失败: run_id=%s", run_id, exc_info=True)
                if prepared is not None:
                    try:
                        finished_status = await self._inbox_service.finish_run(
                            run_id,
                            "failed",
                            user_id,
                            operator_name,
                            prepared["owner_token"],
                        )
                        if finished_status:
                            terminal_payload = format_terminal_done(finished_status)
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
                                event_id=f"{run_id}:terminal:{prepared['owner_token']}",
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
                    if next_run_id_to_submit:
                        # MySQL 已创建 pending Run。旧 Run 的终态发布和 owner 清理
                        # 完成后再提交，避免链式接续等待调度兜底轮询；
                        # 新旧 Run 各有独立 Stream，正确性不依赖本直启（轮询可接管）。
                        self.submit(
                            run_id=next_run_id_to_submit,
                            conversation_id=conversation_id,
                            user_id=user_id,
                            operator_name=operator_name,
                        )
                self._owned_runs.discard(run_id)


agent_run_runtime = AgentRunRuntime()
