"""进程内有界并发的 ChatData Agent Run Runtime。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from utils.logger import get_logger
from .agent_inbox_service import AgentRunLeaseLostError, AgentInboxService, agent_inbox_service
from .agent_run_event_service import AgentRunEventService, agent_run_event_service, format_terminal_done
from .chat_data_stream_request import ChatDataStreamRequest
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


@dataclass
class _RunExecutionState:
    """单次 Run 执行过程中的可变状态，供 _execute 与其各阶段 helper 共享。"""

    run_id: str
    conversation_id: str
    user_id: str
    operator_name: str
    terminal_payload: str | None = None
    terminal_ready: bool = False
    next_run_id_to_submit: str | None = None
    # 心跳发现 cancelling 时认领已生效，但 helper 无法 return prepared；
    # 挂在 state 上带回，保证取消收口（finish_run/release_owner）仍能执行。
    cancelled_prepared: dict | None = None


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
        from apps.chat_data_app.daos.agent_inbox_dao import agent_inbox_dao

        async with get_session() as session:
            rows = await agent_inbox_dao.list_runnable_runs(session, datetime.now(), limit)
            return [row.run_id for row in rows]

    def _build_stream_factory(self, prepared: dict) -> Callable[[], AsyncIterator[str]]:
        context = prepared["runtime_context"]
        stream_generator = self._stream_generator
        if stream_generator is None:
            raise RuntimeError("AgentRunRuntime 未装配 stream_generator，请在启动时调用 set_stream_generator()")

        def factory() -> AsyncIterator[str]:
            # 流式执行入参统一走 ChatDataStreamRequest：字段列表/默认值单一来源，
            # 生成器签名收窄后无需再在这里罗列二十余个关键字参数。
            return stream_generator(
                ChatDataStreamRequest(
                    thread_id=prepared["conversation_id"],
                    user_query=str(context.get("query") or ""),
                    datasource_id=context.get("datasource_id"),
                    user_id=prepared["user_id"],
                    username=prepared.get("operator_name") or prepared["user_id"],
                    member_datasource_ids=context.get("member_datasource_ids") or [],
                    semantic_config_id=context.get("semantic_config_id"),
                    request=None,
                    model_id=context.get("model_id"),
                    file_selections=context.get("file_selections"),
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
            )

        return factory

    async def _claim_pending_run(self, state: _RunExecutionState) -> dict | None:
        """认领待执行 Run 并刷新执行身份；心跳显示 cancelling 时抛取消。"""
        prepared = await self._inbox_service.prepare_pending_run(state.run_id)
        if prepared is None:
            return None
        state.conversation_id = prepared["conversation_id"]
        state.user_id = prepared["user_id"]
        state.operator_name = prepared.get("operator_name") or state.operator_name or state.user_id
        try:
            await self._event_service.claim_owner(state.run_id, prepared["owner_token"])
        except Exception:
            # prepare 已把 Run 行置为 ACTIVE（租约 120s）；claim_owner 失败
            # （典型为 Redis 不可达）若不收口，行会作为无主 ACTIVE 僵尸挂到
            # 租约过期被 reaper 回收，期间 SSE 一直显示「进行中」假状态。
            # finish_run("failed") 断言 DB owner（prepare 已写入）并重排队消息，
            # 纯 MySQL；SSE 经 MySQL 终态兜底立刻拿到 done(failed)，用户可重发。
            try:
                await self._inbox_service.finish_run(
                    state.run_id,
                    "failed",
                    state.user_id,
                    state.operator_name,
                    prepared["owner_token"],
                )
            except Exception:
                # 收口失败（同样多为存储异常）只记日志，不掩盖原始 claim 异常；
                # 此时僵尸行退回租约 reaper 兜底回收
                logger.exception("Run claim 失败后收口也失败 run_id=%s", state.run_id)
            raise
        self._owned_runs.add(state.run_id)
        if await self._inbox_service.heartbeat_run(state.run_id, prepared["owner_token"]) == "cancelling":
            state.cancelled_prepared = prepared
            raise asyncio.CancelledError("agent_run_cancelled")
        return prepared

    async def _append_run_event(self, state: _RunExecutionState, prepared: dict, payload: str) -> str:
        # A Run may resume after waiting_user; event IDs must
        # remain unique across all execution segments.
        return await self._event_service.append(
            run_id=state.run_id,
            conversation_id=state.conversation_id,
            user_id=state.user_id,
            event_id=f"{state.run_id}:{uuid4().hex}",
            payload=payload,
            owner_token=prepared["owner_token"],
        )

    async def _decide_terminal_state(self, state: _RunExecutionState, prepared: dict, saw_question_requested: bool) -> str:
        """流结束后决定终态：waiting_user 直接复用，否则落库收口。"""
        if saw_question_requested:
            # waiting_user 已由 mark_waiting_for_user 落库，无需重复收口。
            return format_terminal_done("waiting_user")
        completion = await self._inbox_service.complete_run(
            state.run_id,
            state.user_id,
            state.operator_name,
            prepared["owner_token"],
        )
        if completion.status == "finished":
            state.next_run_id_to_submit = completion.next_run_id
        return format_terminal_done(completion.status, completion.next_run_id)

    async def _stream_and_finalize(self, state: _RunExecutionState, prepared: dict) -> str:
        """消费流并逐事件落库，结束后决定终态并返回结构化 done payload。"""
        event_cursor: dict[str, str | None] = {"stream_id": None}
        prepared["stream_event_id_provider"] = lambda: event_cursor["stream_id"]
        stream_factory = self._build_stream_factory(prepared)
        saw_question_requested = False
        async for payload in stream_factory():
            # 生成器只产出事实；question_requested 是 waiting_user 的标记事实。
            if _sse_event_type(payload) == "question_requested":
                saw_question_requested = True
            stream_id = await self._append_run_event(state, prepared, payload)
            event_cursor["stream_id"] = stream_id
        # 正常结束：唯一生命周期 owner 在此收口——先落库，再发布结构化 done。
        return await self._decide_terminal_state(state, prepared, saw_question_requested)

    async def _finalize_cancelled_run(self, state: _RunExecutionState, prepared: dict | None, exc: asyncio.CancelledError) -> None:
        if prepared is None or not (exc.args and exc.args[0] == "agent_run_cancelled"):
            return
        # 用户主动停止也是终态：落库 cancelled 后发布结构化 done。
        # 租约丢失的取消不收口也不发布——流已归新 Worker 所有。
        try:
            finished_status = await self._inbox_service.finish_run(
                state.run_id,
                "cancelled",
                state.user_id,
                state.operator_name,
                prepared["owner_token"],
            )
        except AgentRunLeaseLostError:
            logger.info("ChatData Agent Run 取消收口时租约已丢失: run_id=%s", state.run_id)
            return
        if finished_status:
            state.terminal_payload = format_terminal_done(finished_status)
            state.terminal_ready = True

    async def _finalize_failed_run(self, state: _RunExecutionState, prepared: dict) -> None:
        try:
            finished_status = await self._inbox_service.finish_run(
                state.run_id,
                "failed",
                state.user_id,
                state.operator_name,
                prepared["owner_token"],
            )
        except AgentRunLeaseLostError:
            logger.info("ChatData Agent Run 失败收口时租约已丢失: run_id=%s", state.run_id)
            return
        if finished_status:
            state.terminal_payload = format_terminal_done(finished_status)
            state.terminal_ready = True

    async def _publish_terminal_event(self, state: _RunExecutionState, prepared: dict) -> None:
        if state.terminal_payload is None or not state.terminal_ready:
            return
        try:
            terminal_stream_id = await self._event_service.append(
                run_id=state.run_id,
                conversation_id=state.conversation_id,
                user_id=state.user_id,
                event_id=f"{state.run_id}:terminal:{prepared['owner_token']}",
                payload=state.terminal_payload,
                owner_token=prepared["owner_token"],
            )
        except AgentRunLeaseLostError:
            logger.info("ChatData Agent Run 终止事件发布时租约已丢失: run_id=%s", state.run_id)
            return
        except Exception:
            logger.error("ChatData Agent Run 终止事件发布失败: run_id=%s", state.run_id, exc_info=True)
            return
        # done 已进 Redis Stream；把游标落到 inbox 行，
        # 供问答恢复在 Redis 失效时做 done 检测与游标兜底。
        try:
            await self._inbox_service.record_terminal_event(state.run_id, terminal_stream_id)
        except Exception:
            logger.warning(
                "ChatData Agent Run 终止游标落库失败（Redis 内 done 仍可用）: run_id=%s",
                state.run_id,
                exc_info=True,
            )

    async def _release_ownership(self, state: _RunExecutionState, prepared: dict) -> None:
        try:
            await self._event_service.release_owner(state.run_id, prepared["owner_token"])
        except Exception:
            logger.warning("ChatData Agent Run Redis owner 清理失败: run_id=%s", state.run_id, exc_info=True)

    def _submit_chained_run(self, state: _RunExecutionState) -> None:
        if not state.next_run_id_to_submit:
            return
        # MySQL 已创建 pending Run。旧 Run 的终态发布和 owner 清理
        # 完成后再提交，避免链式接续等待调度兜底轮询；
        # 新旧 Run 各有独立 Stream，正确性不依赖本直启（轮询可接管）。
        self.submit(
            run_id=state.next_run_id_to_submit,
            conversation_id=state.conversation_id,
            user_id=state.user_id,
            operator_name=state.operator_name,
        )

    async def _release_after_run(self, state: _RunExecutionState, prepared: dict | None) -> None:
        if prepared is None:
            self._owned_runs.discard(state.run_id)
            return
        await self._publish_terminal_event(state, prepared)
        await self._release_ownership(state, prepared)
        self._submit_chained_run(state)
        self._owned_runs.discard(state.run_id)

    async def _execute(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        operator_name: str,
    ) -> None:
        async with self._semaphore:
            state = _RunExecutionState(
                run_id=run_id,
                conversation_id=conversation_id,
                user_id=user_id,
                operator_name=operator_name,
            )
            prepared: dict | None = None
            try:
                prepared = await self._claim_pending_run(state)
                if prepared is None:
                    return
                state.terminal_payload = await self._stream_and_finalize(state, prepared)
                state.terminal_ready = True
            except asyncio.CancelledError as exc:
                prepared = state.cancelled_prepared or prepared
                await self._finalize_cancelled_run(state, prepared, exc)
                raise
            except Exception as exc:
                if isinstance(exc, AgentRunLeaseLostError):
                    logger.info("ChatData Agent Run 租约已丢失，停止旧执行: run_id=%s", run_id)
                    return
                logger.error("ChatData Agent 后台运行失败: run_id=%s", run_id, exc_info=True)
                if prepared is not None:
                    await self._finalize_failed_run(state, prepared)
            finally:
                await self._release_after_run(state, prepared)


agent_run_runtime = AgentRunRuntime()
