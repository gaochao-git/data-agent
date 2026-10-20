"""智能体定时任务调度 Runtime。

应用内常驻轮询（仿 agent_run_runtime 的 dispatcher 模式），每个 tick 三步：
1. plan：对到期任务计算触发计划，写入触发实例（job_id+plan_time 唯一兜底），
   并推进 next_trigger_time；行锁串行化多节点推进。
2. dispatch：抢占 pending 触发实例（行锁 + 状态迁移），按个人并发闸门过滤后
   通过 agent_inbox_service.start_run 提交 Agent Run；会话冲突等可重试错误
   释放回 pending 并累计尝试次数，耗尽后置 skipped。
3. finalize：把 dispatched 触发实例随 Agent Run 终态收口为终态。

全局只写一个 MySQL 主库，行锁抢占天然跨机房互斥；主库 failover 由
utils.database.get_session 的切换机制承接，错过窗口按 misfire 语义快进，
不补跑窗口外的历史触发点。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from contextlib import suppress
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from apps.chat_data_app import AGENT_ID
from apps.chat_data_app.agent.run.agent_inbox_service import (
    AgentInboxService,
    AgentRunConflictError,
    AgentRunNotActiveError,
    agent_inbox_service,
)
from apps.chat_data_app.agent.run.agent_run_runtime import agent_run_runtime
from apps.chat_data_app.agent.run.completion_options import parse_completion_run_options
from apps.chat_data_app.daos.agent_schedule_dao import (
    RUN_STATUS_TO_TRIGGER_STATUS,
    SCHEDULE_RUN_STATUS_CLAIMED,
    SCHEDULE_RUN_STATUS_DISPATCHED,
    SCHEDULE_RUN_STATUS_FAILED,
    SCHEDULE_RUN_STATUS_PENDING,
    SCHEDULE_RUN_STATUS_SKIPPED,
    TRIGGER_TYPE_SCHEDULE,
    AgentScheduleDao,
    agent_schedule_dao,
)
from apps.chat_data_app.services.agent_schedule.trigger_time import SCHEDULE_TYPE_ONCE, compute_next_trigger
from apps.chat_data_app.services.conversation_service import conversation_dao as bound_conversation_dao
from apps.chat_data_app.services.runtime_config import read_bool_env, read_positive_int_env
from apps.chat_data_app.services.setting_service import get_runtime_setting
from utils.database import get_session
from utils.logger import get_logger
from utils.models import ChatDataAgentSchedule, ChatDataAgentScheduleRun

logger = get_logger(__name__)

# 轮询间隔 / 派发批量 / 单用户在途上限（CHAT_DATA_SCHEDULE_*）走运行时热值
# （get_runtime_setting：表覆盖 > env > 注册默认），调度器是常驻循环，每轮
# tick 前解析，改配置无需重启；构造参数仅保留给测试显式注入覆盖。
# 错过触发窗口：停机/主库切换期间过期的计划触发时间直接快进跳过，不补跑
SCHEDULE_MISFIRE_WINDOW_SECONDS = read_positive_int_env("CHAT_DATA_SCHEDULE_MISFIRE_WINDOW_SECONDS", 3600)
# 单个触发实例的最大派发尝试次数（会话占用等冲突每轮重试，耗尽置 skipped）
SCHEDULE_MAX_DISPATCH_ATTEMPTS = read_positive_int_env("CHAT_DATA_SCHEDULE_MAX_DISPATCH_ATTEMPTS", 10)
# claimed 超时回收：节点在抢占后、派发前崩溃（滚动发布/reload）会把实例悬挂在
# claimed，超过该时长由其他节点的调度循环释放回 pending 重试
SCHEDULE_CLAIM_TIMEOUT_SECONDS = read_positive_int_env("CHAT_DATA_SCHEDULE_CLAIM_TIMEOUT_SECONDS", 300)
# 单轮扫描的任务数与单任务单 tick 的触发计划推进上限，防异常表达式拖垮调度循环
_PLAN_JOB_BATCH = 50
_MAX_PLAN_FIRES_PER_TICK = 200
# 每次新建会话模式：触发实例在派发前没有会话，占位符由派发时新建的会话覆盖
_CONVERSATION_MODE_NEW_EACH_RUN = "new_each_run"
# 单任务单 tick 的推进迭代上限（含窗口外快进迭代）：长停机后逐点推进会在
# 事务 + 行锁内空转数十万次，超过上限直接跳变到 misfire 窗口起点的下一个触发点
_MAX_PLAN_STEPS = 500


def job_options(options_json: str | None) -> dict:
    """解析任务运行选项 JSON；损坏内容降级为空对象。"""
    try:
        value = json.loads(options_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _build_trigger_row(job: ChatDataAgentSchedule, plan_time: datetime) -> ChatDataAgentScheduleRun:
    return ChatDataAgentScheduleRun(
        job_id=job.job_id,
        user_id=job.user_id,
        conversation_id=job.conversation_id,
        plan_time=plan_time,
        trigger_status=SCHEDULE_RUN_STATUS_PENDING,
        trigger_type=TRIGGER_TYPE_SCHEDULE,
        delete_flag=0,
        create_time=plan_time,
        update_time=plan_time,
        create_by=job.user_id,
        create_by_name=job.create_by_name,
        update_by=job.user_id,
        update_by_name=job.create_by_name,
    )


class AgentScheduleRuntime:
    """多点多活下的智能体定时任务调度器；所有互斥依赖主库行锁。"""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        poll_seconds: float | None = None,
        dispatch_batch_size: int | None = None,
        user_max_concurrency: int | None = None,
        misfire_window_seconds: int | None = None,
        max_dispatch_attempts: int | None = None,
        claim_timeout_seconds: int | None = None,
        dao: AgentScheduleDao = agent_schedule_dao,
        inbox_service: AgentInboxService = agent_inbox_service,
        node_id: str | None = None,
    ) -> None:
        self.enabled = read_bool_env("CHAT_DATA_SCHEDULE_ENABLED", False) if enabled is None else enabled
        # 三项调度参数：None=走运行时热值（表覆盖 > env > 注册默认），测试注入显式值则固定
        self._poll_seconds = poll_seconds
        self._dispatch_batch_size = dispatch_batch_size
        self._user_max_concurrency = user_max_concurrency
        self._misfire_window = timedelta(seconds=misfire_window_seconds or SCHEDULE_MISFIRE_WINDOW_SECONDS)
        self._max_dispatch_attempts = max_dispatch_attempts or SCHEDULE_MAX_DISPATCH_ATTEMPTS
        self._claim_timeout = timedelta(seconds=claim_timeout_seconds or SCHEDULE_CLAIM_TIMEOUT_SECONDS)
        self._dao = dao
        self._inbox_service = inbox_service
        self._node_id = node_id or f"{socket.gethostname()}:{os.getpid()}"
        self._poll_task: asyncio.Task | None = None

    async def _resolve_poll_seconds(self) -> float:
        if self._poll_seconds is not None:
            return self._poll_seconds
        return await get_runtime_setting("CHAT_DATA_SCHEDULE_POLL_SECONDS")

    async def _resolve_dispatch_batch_size(self) -> int:
        if self._dispatch_batch_size is not None:
            return self._dispatch_batch_size
        return await get_runtime_setting("CHAT_DATA_SCHEDULE_DISPATCH_BATCH_SIZE")

    async def _resolve_user_max_concurrency(self) -> int:
        if self._user_max_concurrency is not None:
            return self._user_max_concurrency
        return await get_runtime_setting("CHAT_DATA_SCHEDULE_USER_MAX_CONCURRENCY")

    async def start(self) -> None:
        if not self.enabled:
            logger.info("智能体定时任务调度未启用（CHAT_DATA_SCHEDULE_ENABLED=false）")
            return
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(
                self._poll_loop(),
                name="chatdata-agent-schedule-dispatcher",
            )

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.tick()
                await asyncio.sleep(await self._resolve_poll_seconds())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("智能体定时任务调度 tick 失败", exc_info=True)
                await asyncio.sleep(1)

    async def tick(self, now: datetime | None = None) -> None:
        """单轮调度：终态收口 -> 悬挂回收 ->（未暂停时）计划推进 + 派发。

        全局暂停（后台一键暂停）只冻结新触发的计划与派发，已派发 Run 的
        状态收口与悬挂 claimed 回收继续执行，保证暂停前后状态口径一致。
        """
        await self._finalize_dispatched()
        await self._reap_stale_claims(now)
        if await self._is_paused():
            return
        await self._plan_triggers(now)
        await self._dispatch_pending(now)

    async def _is_paused(self) -> bool:
        # 属性访问必须留在事务内：get_session 提交后 ORM 属性过期，
        # 块外读取会触发惰性刷新 IO 并抛 MissingGreenlet。
        async with get_session() as session:
            control = await self._dao.get_control(session)
            return bool(control is not None and control.paused_flag)

    async def _reap_stale_claims(self, now: datetime | None = None) -> None:
        """回收悬挂的 claimed 触发实例。

        节点在抢占（claim 事务提交）后、派发完成前崩溃/重启（滚动发布、
        gunicorn reload 都会出现），实例会永久停在 claimed。超过
        CHAT_DATA_SCHEDULE_CLAIM_TIMEOUT_SECONDS 后由任意存活节点的调度
        循环释放回 pending 重试；attempt_count 在抢占时已累加，耗尽自动
        转 skipped，不会无限循环。时钟与 tick 注入时间同源，保证可测。
        """
        batch_size = await self._resolve_dispatch_batch_size()
        async with get_session() as session:
            current = now or await self._dao.now(session)
            before = current - self._claim_timeout
            stale_ids = await self._dao.list_stale_claimed_ids(session, before, batch_size * 3)
        for trigger_id in stale_ids:
            await self._release(trigger_id, SCHEDULE_RUN_STATUS_PENDING, "节点中断后超时回收，重新排队")

    # ---- 1. 触发计划推进 ----

    async def _plan_triggers(self, now: datetime | None = None) -> None:
        async with get_session() as session:
            current = now or await self._dao.now(session)
            horizon = current - self._misfire_window
            jobs = await self._dao.list_due_jobs(session, current, _PLAN_JOB_BATCH)
            for job in jobs:
                await self._plan_single_job(session, job, current, horizon)

    async def _plan_single_job(
        self, session: AsyncSession, job: ChatDataAgentSchedule, current: datetime, horizon: datetime
    ) -> None:
        """推进单个任务的触发计划：为窗口内每个到期时间点补触发实例。

        窗口外（停机/failover 期间过期）的触发点直接快进跳过，只推进游标。
        长停机场景迭代次数由 _MAX_PLAN_STEPS 封顶：超限时不再逐点推进，
        直接跳变到 misfire 窗口起点的下一个触发点，剩余窗口内触发点由
        后续 tick 从新游标继续补齐，避免事务 + 行锁内空转数十万次。
        """
        fires: list[datetime] = []
        cursor = job.next_trigger_time
        steps = 0
        while cursor is not None and cursor <= current and len(fires) < _MAX_PLAN_FIRES_PER_TICK:
            steps += 1
            if steps > _MAX_PLAN_STEPS:
                if job.schedule_type == SCHEDULE_TYPE_ONCE:
                    job.next_trigger_time = None
                else:
                    job.next_trigger_time = compute_next_trigger(
                        schedule_type=job.schedule_type,
                        cron_expr=job.cron_expr,
                        plan_time=None,
                        effective_end_time=job.effective_end_time,
                        after=current - self._misfire_window,
                    )
                break
            next_cursor = compute_next_trigger(
                schedule_type=job.schedule_type,
                cron_expr=job.cron_expr,
                plan_time=cursor if job.schedule_type == SCHEDULE_TYPE_ONCE else None,
                effective_end_time=job.effective_end_time,
                after=cursor,
            )
            if cursor >= horizon:
                fires.append(cursor)
            job.next_trigger_time = next_cursor
            job.last_trigger_time = cursor
            cursor = next_cursor
        for plan_time in fires:
            existing = await self._dao.get_trigger(session, job.job_id, plan_time)
            if existing is not None:
                continue
            # 唯一键 (job_id, plan_time) 是重复触发的最后防线；竞态漏网的插入
            # 会被约束拒绝并回滚本轮计划，下一轮重试后自然收敛。
            session.add(_build_trigger_row(job, plan_time))
        # 单次任务触发完后 next_trigger_time 置空即不再计划；
        # 保持 enabled_flag=1，让 pending 触发实例在派发冲突时仍可重试。

    # ---- 2. 派发 ----

    async def _dispatch_pending(self, now: datetime | None = None) -> None:
        batch_size = await self._resolve_dispatch_batch_size()
        async with get_session() as session:
            current = now or await self._dao.now(session)
            candidates = await self._dao.list_claimable_runs(session, current, batch_size * 3)
            claimed: list[ChatDataAgentScheduleRun] = []
            for run in candidates:
                if len(claimed) >= batch_size:
                    break
                run.trigger_status = SCHEDULE_RUN_STATUS_CLAIMED
                run.attempt_count = (run.attempt_count or 0) + 1
                run.owner_node = self._node_id
                run.claimed_time = current
                claimed.append(run)
            snapshots = [self._trigger_snapshot(run) for run in claimed]
        for snapshot in snapshots:
            await self._dispatch_single(snapshot)

    @staticmethod
    def _trigger_snapshot(run: ChatDataAgentScheduleRun) -> dict:
        return {
            "id": run.id,
            "job_id": run.job_id,
            "user_id": run.user_id,
            "conversation_id": run.conversation_id,
            "plan_time": run.plan_time,
        }

    async def _dispatch_single(self, snapshot: dict) -> dict:
        """派发单个触发实例；返回 {status, run_id} 供立即触发接口回显。"""
        result = {"status": SCHEDULE_RUN_STATUS_PENDING, "run_id": None}
        if not await self._user_has_slot(snapshot["user_id"]):
            await self._release(snapshot["id"], SCHEDULE_RUN_STATUS_PENDING, "")
            return result
        job = await self._load_job(snapshot["job_id"])
        if job is None:
            await self._release(snapshot["id"], SCHEDULE_RUN_STATUS_SKIPPED, "任务已删除或停用")
            result["status"] = SCHEDULE_RUN_STATUS_SKIPPED
            return result
        # 会话准备与派发同处一个 try：new_each_run 的建会话/绑数据源失败也要
        # 走统一的释放回 pending，否则异常会冒到 _dispatch_pending 的派发循环
        # （无 try），实例悬挂在 claimed 直到超时回收才重试。
        try:
            conversation_id = await self._resolve_conversation_id(job, snapshot)
            runtime_context = self._build_runtime_context(job)
            run_id, _adopted = await self._inbox_service.start_run(
                conversation_id,
                snapshot["user_id"],
                job["create_by_name"] or snapshot["user_id"],
                runtime_context,
            )
        except AgentRunConflictError:
            # 会话已有运行中任务：释放回 pending 下轮重试，尝试次数耗尽置 skipped
            await self._release(snapshot["id"], SCHEDULE_RUN_STATUS_PENDING, "")
            return result
        except AgentRunNotActiveError as exc:
            await self._release(snapshot["id"], SCHEDULE_RUN_STATUS_FAILED, str(exc))
            result["status"] = SCHEDULE_RUN_STATUS_FAILED
            return result
        except Exception:
            logger.error(
                "定时任务派发失败: job_id=%s trigger_id=%s",
                snapshot["job_id"],
                snapshot["id"],
                exc_info=True,
            )
            # 已知边界：new_each_run 在此异常路径下可能留下一个空会话
            # （create_thread 成功但 start_run 失败）。新会话无冲突场景，
            # 频率极低，不做回滚；残留空会话可由用户手动清理。
            await self._release(snapshot["id"], SCHEDULE_RUN_STATUS_PENDING, "")
            return result
        await self._mark_dispatched(snapshot["id"], run_id, conversation_id)
        result["status"] = SCHEDULE_RUN_STATUS_DISPATCHED
        result["run_id"] = run_id
        return result

    async def _resolve_conversation_id(self, job: dict, snapshot: dict) -> str:
        """确定本次执行落在哪个会话。

        fixed 模式以任务当前绑定会话为准（而非触发实例快照）：用户可能在实例
        排队期间把任务从 new_each_run 切回 fixed，快照里的空串会让 start_run
        报「会话不存在」，排队实例成批假失败。new_each_run 模式则新建独立会话，
        并按任务选项绑定数据源（与聊天链路的首次绑定同一入口），否则新会话
        无绑定，agent 查询时拿不到任何执行对象。
        """
        if job["conversation_mode"] != _CONVERSATION_MODE_NEW_EACH_RUN:
            return job["conversation_id"] or snapshot["conversation_id"]
        conversation_id = uuid4().hex
        await bound_conversation_dao.create_thread(
            user_id=snapshot["user_id"],
            thread_id=conversation_id,
            agent_id=AGENT_ID,
            title=f"【定时】{job['job_title']}",
            now=datetime.now(),
        )
        job_datasource_id = str(job_options(job["options_json"]).get("datasource_id") or "").strip()
        if job_datasource_id:
            await bound_conversation_dao.resolve_datasource_binding(
                conversation_id=conversation_id,
                user_id=snapshot["user_id"],
                requested_datasource_id=job_datasource_id,
            )
        return conversation_id

    async def _user_has_slot(self, user_id: str) -> bool:
        user_max_concurrency = await self._resolve_user_max_concurrency()
        async with get_session() as session:
            counts = await self._dao.count_dispatched_by_users(session, [user_id])
        return counts.get(user_id, 0) < user_max_concurrency

    async def _load_job(self, job_id: str) -> dict | None:
        """读取派发所需的任务字段快照；属性访问必须留在事务内（提交后 ORM 属性过期）。"""
        async with get_session() as session:
            job = await self._dao.get_job(session, job_id)
            if job is None:
                return None
            return {
                "job_id": job.job_id,
                "job_prompt": job.job_prompt,
                "options_json": job.options_json,
                "create_by_name": job.create_by_name,
                "conversation_mode": job.conversation_mode,
                "conversation_id": job.conversation_id,
                "job_title": job.job_title,
            }

    def _build_runtime_context(self, job: dict) -> dict:
        # 复用 chat 完成链路的 options 解析，保证运行参数字段口径一致。
        options = job_options(job.get("options_json"))
        run_options = parse_completion_run_options(options)
        return {
            "query": job["job_prompt"],
            "datasource_id": run_options.datasource_id,
            "member_datasource_ids": run_options.member_datasource_ids,
            "semantic_config_id": run_options.semantic_config_id,
            "model_id": run_options.model_id,
            "file_selections": run_options.file_selections,
            "quoted_snippets": run_options.quoted_snippets,
            "enable_thinking": run_options.enable_thinking,
            "thinking_effort": run_options.thinking_effort,
            "skill_names": run_options.skill_names,
            "operator_name": job["create_by_name"] or "",
            "options": options,
            "file_ids": [],
        }

    async def _mark_dispatched(self, trigger_id: int, run_id: str, conversation_id: str | None = None) -> None:
        async with get_session() as session:
            run = await self._dao.get_trigger_for_update(session, trigger_id)
            if run is None or run.trigger_status != SCHEDULE_RUN_STATUS_CLAIMED:
                return
            run.trigger_status = SCHEDULE_RUN_STATUS_DISPATCHED
            run.run_id = run_id
            if conversation_id:
                # 每次新建会话模式：触发实例回填本次实际执行的会话
                run.conversation_id = conversation_id
            run.dispatched_time = await self._dao.now(session)
        # 直启本进程执行加速；失败无碍，agent_run_runtime 的轮询会在数秒内接管
        with suppress(Exception):
            agent_run_runtime.submit(
                run_id=run_id,
                conversation_id="",
                user_id="",
                operator_name="",
            )

    async def _release(self, trigger_id: int, status: str, error_message: str) -> None:
        """把 claimed 触发实例迁移到目标状态；重试次数耗尽时改判 skipped。"""
        async with get_session() as session:
            run = await self._dao.get_trigger_for_update(session, trigger_id)
            if run is None or run.trigger_status != SCHEDULE_RUN_STATUS_CLAIMED:
                return
            final_status = status
            if status == SCHEDULE_RUN_STATUS_PENDING and run.attempt_count >= self._max_dispatch_attempts:
                final_status = SCHEDULE_RUN_STATUS_SKIPPED
            run.trigger_status = final_status
            if final_status != SCHEDULE_RUN_STATUS_PENDING:
                run.error_message = (error_message or "")[:1000]
                run.finished_time = await self._dao.now(session)

    # ---- 3. 终态收口 ----

    async def dispatch_trigger(self, trigger_id: int) -> dict:
        """立即触发：抢占指定触发实例并尝试同步派发，供手动执行接口调用。

        与轮询派发走完全同一条链路（个人并发闸门 / 冲突释放重试 / 执行历史）。
        会话正忙等可重试错误会释放回 pending，由调度 tick 自动续派，此时
        返回 queued；全局暂停只冻结计划触发，不拦截用户主动的立即执行。
        """
        async with get_session() as session:
            run = await self._dao.get_trigger_for_update(session, trigger_id)
            if run is None or run.trigger_status != SCHEDULE_RUN_STATUS_PENDING:
                return {"status": SCHEDULE_RUN_STATUS_PENDING, "run_id": None}
            run.trigger_status = SCHEDULE_RUN_STATUS_CLAIMED
            run.attempt_count = (run.attempt_count or 0) + 1
            run.owner_node = self._node_id
            run.claimed_time = await self._dao.now(session)
            snapshot = self._trigger_snapshot(run)
        return await self._dispatch_single(snapshot)

    async def _finalize_dispatched(self) -> None:
        batch_size = await self._resolve_dispatch_batch_size()
        async with get_session() as session:
            dispatched = await self._dao.list_dispatched_runs(session, batch_size * 3)
            run_statuses = await self._dao.list_run_statuses(session, [run.run_id for run in dispatched if run.run_id])
            finished = [
                (run.id, RUN_STATUS_TO_TRIGGER_STATUS[run_statuses[run.run_id]])
                for run in dispatched
                if run_statuses.get(run.run_id) in RUN_STATUS_TO_TRIGGER_STATUS
            ]
            for trigger_id, trigger_status in finished:
                row = await self._dao.get_trigger_for_update(session, trigger_id)
                if row is None or row.trigger_status != SCHEDULE_RUN_STATUS_DISPATCHED:
                    continue
                row.trigger_status = trigger_status
                row.finished_time = await self._dao.now(session)


agent_schedule_runtime = AgentScheduleRuntime()
