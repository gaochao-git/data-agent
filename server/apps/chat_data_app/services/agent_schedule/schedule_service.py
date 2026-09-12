"""智能体定时任务管理：创建、查询、更新、删除、执行历史与后台全局管理。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from utils.database import get_session
from utils.logger import get_logger
from utils.models import ChatDataAgentSchedule, ChatDataAgentScheduleRun
from apps.chat_data_app.daos.agent_schedule_dao import (
    SCHEDULE_RUN_STATUS_PENDING,
    TRIGGER_TYPE_MANUAL,
    agent_schedule_dao,
)
from apps.chat_data_app.services.runtime_config import read_positive_int_env
from .trigger_time import (
    SCHEDULE_TYPES,
    SCHEDULE_TYPE_CRON,
    SCHEDULE_TYPE_ONCE,
    AgentScheduleValidationError,
    compute_next_trigger,
    normalize_prompt,
    normalize_title,
    validate_cron_expr,
)

logger = get_logger(__name__)

# 会话绑定方式：fixed-绑定指定会话；new_each_run-每次执行新建会话
CONVERSATION_MODE_FIXED = "fixed"
CONVERSATION_MODE_NEW_EACH_RUN = "new_each_run"
CONVERSATION_MODES = (CONVERSATION_MODE_FIXED, CONVERSATION_MODE_NEW_EACH_RUN)

# 单个用户可创建的定时任务上限，防止任务表被无界增长
MAX_JOBS_PER_USER = read_positive_int_env("CHAT_DATA_SCHEDULE_MAX_JOBS_PER_USER", 20)


class AgentScheduleNotFoundError(LookupError):
    """任务不存在或不属于当前用户。"""


class AgentScheduleConversationInvalidError(AgentScheduleValidationError):
    """绑定的会话不存在或不属于当前用户。"""


class AgentScheduleLimitExceededError(RuntimeError):
    """超出单用户任务数量上限。"""


def _validate_options(options: dict[str, Any] | None) -> str | None:
    """校验并序列化运行选项；服务端不信任前端原样 JSON。"""
    if options is None:
        return None
    if not isinstance(options, dict):
        raise AgentScheduleValidationError("运行选项必须是对象")
    try:
        return json.dumps(options, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise AgentScheduleValidationError("运行选项包含不可序列化的内容") from exc


def _resolve_schedule_fields(
    *,
    schedule_type: str | None,
    cron_expr: str | None,
    plan_time: datetime | None,
    effective_end_time: datetime | None,
    now: datetime,
) -> tuple[str, str, datetime | None, datetime | None, datetime | None]:
    """校验调度字段并计算 next_trigger_time，返回五元组。"""
    resolved_type = str(schedule_type or SCHEDULE_TYPE_CRON).strip()
    if resolved_type not in SCHEDULE_TYPES:
        raise AgentScheduleValidationError(f"调度类型必须是 {' 或 '.join(SCHEDULE_TYPES)}")

    resolved_end = effective_end_time
    if resolved_end is not None and resolved_end <= now:
        raise AgentScheduleValidationError("有效期结束时间必须晚于当前时间")

    if resolved_type == SCHEDULE_TYPE_ONCE:
        if plan_time is None:
            raise AgentScheduleValidationError("单次任务必须提供执行时间")
        if plan_time <= now:
            raise AgentScheduleValidationError("单次任务的执行时间必须晚于当前时间")
        resolved_cron = ""
        next_trigger = plan_time
    else:
        resolved_cron = validate_cron_expr(cron_expr or "")
        plan_time = None
        next_trigger = compute_next_trigger(
            schedule_type=resolved_type,
            cron_expr=resolved_cron,
            plan_time=None,
            effective_end_time=resolved_end,
            after=now,
        )
        if next_trigger is None:
            raise AgentScheduleValidationError("cron 表达式在有效期内没有可触发的时间点")

    if resolved_type == SCHEDULE_TYPE_ONCE and resolved_end is not None and plan_time > resolved_end:
        raise AgentScheduleValidationError("单次任务的执行时间不能晚于有效期结束时间")
    return resolved_type, resolved_cron, plan_time, resolved_end, next_trigger


class AgentScheduleService:
    """定时任务定义的管理入口；触发与派发由 agent_schedule_runtime 负责。"""

    def __init__(self, dao=agent_schedule_dao):
        self._dao = dao

    async def create_job(
        self,
        *,
        title: str,
        prompt: str,
        conversation_id: str = "",
        conversation_mode: str | None = None,
        schedule_type: str | None = None,
        cron_expr: str | None,
        plan_time: datetime | None,
        effective_end_time: datetime | None,
        datasource_id: str | None,
        options: dict[str, Any] | None,
        user_id: str,
        username: str,
    ) -> dict:
        resolved_title = normalize_title(title)
        resolved_prompt = normalize_prompt(prompt)
        resolved_mode = str(conversation_mode or CONVERSATION_MODE_FIXED).strip()
        if resolved_mode not in CONVERSATION_MODES:
            raise AgentScheduleValidationError(f"会话绑定方式必须是 {' 或 '.join(CONVERSATION_MODES)}")
        conversation_key = str(conversation_id or "").strip()
        if resolved_mode == CONVERSATION_MODE_FIXED and not conversation_key:
            raise AgentScheduleValidationError("指定会话模式必须选择一个会话")
        if resolved_mode == CONVERSATION_MODE_NEW_EACH_RUN:
            # 每次执行新建会话：不绑定具体会话，conversation_id 置空
            conversation_key = ""
        options_json = _validate_options(options)
        if datasource_id:
            merged = json.loads(options_json) if options_json else {}
            merged["datasource_id"] = str(datasource_id).strip()
            options_json = json.dumps(merged, ensure_ascii=False)

        async with get_session() as session:
            now = await self._dao.now(session)
            resolved = _resolve_schedule_fields(
                schedule_type=schedule_type,
                cron_expr=cron_expr,
                plan_time=plan_time,
                effective_end_time=effective_end_time,
                now=now,
            )
            if resolved_mode == CONVERSATION_MODE_FIXED and not await self._dao.conversation_exists(
                session, conversation_key, user_id
            ):
                raise AgentScheduleConversationInvalidError("会话不存在或不属于当前用户")
            # count 与 insert 非同一原子操作，并发创建存在小幅竞态（可能超出上限 1~2 个）；
            # 无用户级行可锁，引入分布式锁不值当，超限后果轻微（可被后台清理），接受该竞态
            if await self._dao.count_jobs_by_user(session, user_id) >= MAX_JOBS_PER_USER:
                raise AgentScheduleLimitExceededError(f"最多创建 {MAX_JOBS_PER_USER} 个定时任务")
            job = ChatDataAgentSchedule(
                job_id=uuid4().hex,
                user_id=user_id,
                conversation_id=conversation_key,
                conversation_mode=resolved_mode,
                job_title=resolved_title,
                job_prompt=resolved_prompt,
                schedule_type=resolved[0],
                cron_expr=resolved[1],
                plan_time=resolved[2],
                effective_end_time=resolved[3],
                enabled_flag=1,
                next_trigger_time=resolved[4],
                options_json=options_json,
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=user_id,
                create_by_name=username,
                update_by=user_id,
                update_by_name=username,
            )
            session.add(job)
            return job.to_dict()

    async def list_jobs(self, *, user_id: str) -> list[dict]:
        async with get_session() as session:
            jobs = await self._dao.list_jobs_by_user(session, user_id)
            titles = await self._dao.list_conversation_titles(session, [job.conversation_id for job in jobs])
            items = []
            for job in jobs:
                item = job.to_dict()
                item["conversation_title"] = titles.get(job.conversation_id)
                items.append(item)
            return items

    async def update_job(
        self,
        *,
        job_id: str,
        title: str | None,
        prompt: str | None,
        conversation_mode: str | None = None,
        conversation_id: str | None = None,
        schedule_type: str | None = None,
        cron_expr: str | None,
        plan_time: datetime | None,
        effective_end_time: datetime | None,
        enabled_flag: int | None,
        datasource_id: str | None,
        options: dict[str, Any] | None,
        user_id: str,
        username: str,
    ) -> dict:
        async with get_session() as session:
            now = await self._dao.now(session)
            job = await self._dao.get_job_for_update(session, job_id)
            if job is None or job.user_id != user_id:
                raise AgentScheduleNotFoundError("定时任务不存在")
            resolved_mode = str(conversation_mode or job.conversation_mode).strip()
            if resolved_mode not in CONVERSATION_MODES:
                raise AgentScheduleValidationError(f"会话绑定方式必须是 {' 或 '.join(CONVERSATION_MODES)}")
            if resolved_mode == CONVERSATION_MODE_FIXED:
                # 指定会话模式：优先取新传入的会话，否则沿用任务已绑定的会话
                conversation_key = str(conversation_id or "").strip() or job.conversation_id
                if not conversation_key:
                    raise AgentScheduleValidationError("指定会话模式必须选择一个会话")
                if conversation_key != job.conversation_id and not await self._dao.conversation_exists(
                    session, conversation_key, user_id
                ):
                    raise AgentScheduleConversationInvalidError("会话不存在或不属于当前用户")
            else:
                conversation_key = ""
            resolved_type = str(schedule_type or job.schedule_type).strip()
            resolved_cron = cron_expr if cron_expr is not None else job.cron_expr
            resolved_plan = plan_time if plan_time is not None else job.plan_time
            if resolved_type == SCHEDULE_TYPE_ONCE and plan_time is None and job.schedule_type != SCHEDULE_TYPE_ONCE:
                resolved_plan = None
            resolved_end = effective_end_time if effective_end_time is not None else job.effective_end_time
            resolved = _resolve_schedule_fields(
                schedule_type=resolved_type,
                cron_expr=resolved_cron,
                plan_time=resolved_plan,
                effective_end_time=resolved_end,
                now=now,
            )
            if title is not None:
                job.job_title = normalize_title(title)
            if prompt is not None:
                job.job_prompt = normalize_prompt(prompt)
            if options is not None or datasource_id is not None:
                merged = dict(options) if isinstance(options, dict) else self._load_options(job)
                if datasource_id:
                    merged["datasource_id"] = str(datasource_id).strip()
                job.options_json = json.dumps(merged, ensure_ascii=False)
            mode_changed = resolved_mode != job.conversation_mode or conversation_key != job.conversation_id
            if mode_changed:
                job.conversation_mode = resolved_mode
                job.conversation_id = conversation_key
            schedule_changed = (resolved[0], resolved[1], resolved[2], resolved[3]) != (
                job.schedule_type,
                job.cron_expr,
                job.plan_time,
                job.effective_end_time,
            )
            if schedule_changed:
                # 调度字段变更一律以服务端时钟重新计算下一次触发时间
                job.schedule_type = resolved[0]
                job.cron_expr = resolved[1]
                job.plan_time = resolved[2]
                job.effective_end_time = resolved[3]
                job.next_trigger_time = resolved[4]
            if enabled_flag is not None and (1 if enabled_flag else 0) != job.enabled_flag:
                job.enabled_flag = 1 if enabled_flag else 0
                if enabled_flag:
                    # 重新启用：仅在调度字段未变时按当前时钟重新武装，避免覆盖上面的重算结果
                    if not schedule_changed:
                        job.next_trigger_time = compute_next_trigger(
                            schedule_type=job.schedule_type,
                            cron_expr=job.cron_expr,
                            plan_time=job.plan_time,
                            effective_end_time=job.effective_end_time,
                            after=now,
                        )
                else:
                    job.next_trigger_time = None
                    # 停用后 pending/claimed 实例永不会被派发，主动清退防僵尸
                    await self._dao.cancel_unfinished_triggers(
                        session, job.job_id, now, "任务已停用，未执行的触发已取消",
                    )
            job.update_time = now
            job.update_by = user_id
            job.update_by_name = username
            return job.to_dict()

    async def delete_job(self, *, job_id: str, user_id: str, username: str) -> dict:
        async with get_session() as session:
            now = await self._dao.now(session)
            job = await self._dao.get_job_for_update(session, job_id)
            if job is None or job.user_id != user_id:
                raise AgentScheduleNotFoundError("定时任务不存在")
            job.delete_flag = 1
            job.enabled_flag = 0
            job.next_trigger_time = None
            await self._dao.cancel_unfinished_triggers(
                session, job.job_id, now, "任务已删除，未执行的触发已取消",
            )
            job.update_time = now
            job.update_by = user_id
            job.update_by_name = username
            return {"job_id": job.job_id}

    async def list_job_runs(self, *, job_id: str, user_id: str, limit: int = 50) -> list[dict]:
        async with get_session() as session:
            job = await self._dao.get_job(session, job_id)
            if job is None or job.user_id != user_id:
                raise AgentScheduleNotFoundError("定时任务不存在")
            runs = await self._dao.list_runs_by_job(session, job_id, limit)
            run_statuses = await self._dao.list_run_statuses(
                session,
                [run.run_id for run in runs if run.run_id],
            )
            return [run.to_dict(run_status=run_statuses.get(run.run_id)) for run in runs]

    async def trigger_now(
        self,
        *,
        job_id: str,
        user_id: str,
        username: str,
    ) -> dict:
        """立即执行（仅任务归属用户）：创建 manual 触发实例，派发由 runtime 完成。

        复用触发流水线使个人并发闸门、冲突重试与执行历史天然生效；同一时刻
        重复触发会被 (job_id, plan_time) 唯一键挡下，返回校验错误。
        """
        async with get_session() as session:
            now = await self._dao.now(session)
            job = await self._dao.get_job_for_update(session, job_id)
            if job is None or job.user_id != user_id:
                raise AgentScheduleNotFoundError("定时任务不存在")
            if not job.enabled_flag:
                raise AgentScheduleValidationError("任务已停用，请先启用后再执行")
            if await self._dao.get_trigger(session, job.job_id, now):
                raise AgentScheduleValidationError("刚刚已触发过，请稍后再试")
            trigger = ChatDataAgentScheduleRun(
                job_id=job.job_id,
                user_id=job.user_id,
                conversation_id=job.conversation_id,
                plan_time=now,
                trigger_status=SCHEDULE_RUN_STATUS_PENDING,
                trigger_type=TRIGGER_TYPE_MANUAL,
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=user_id,
                create_by_name=username,
                update_by=user_id,
                update_by_name=username,
            )
            session.add(trigger)
            await session.flush()
            return {
                "trigger_id": trigger.id,
                "job_id": job.job_id,
                "plan_time": now.isoformat(),
            }

    # ---- 后台管理（admin）：不做归属校验，入口由可信代理 service token 保护 ----

    async def get_admin_overview(self) -> dict:
        """全局概览：暂停状态、任务总数、执行统计。"""
        async with get_session() as session:
            control = await self._dao.get_control(session)
            total_jobs, enabled_jobs = await self._dao.count_all_jobs(session)
            status_counts = await self._dao.count_runs_by_status(session)
            runs_last_24h = await self._dao.count_runs_since(session, await self._dao.now(session) - timedelta(hours=24))
            return {
                "paused_flag": int(bool(control.paused_flag)) if control else 0,
                "pause_reason": control.pause_reason if control else "",
                "control_update_time": control.to_dict()["update_time"] if control else None,
                "total_jobs": total_jobs,
                "enabled_jobs": enabled_jobs,
                "total_runs": sum(status_counts.values()),
                "succeeded_runs": status_counts.get("succeeded", 0),
                "failed_runs": status_counts.get("failed", 0),
                "skipped_runs": status_counts.get("skipped", 0),
                "running_runs": status_counts.get("dispatched", 0) + status_counts.get("claimed", 0) + status_counts.get("pending", 0),
                "runs_last_24h": runs_last_24h,
            }

    async def set_paused(self, *, paused: bool, reason: str, user_id: str, username: str) -> dict:
        """全局一键暂停/恢复：只冻结新触发的计划与派发，不改任务本身的启停状态。

        暂停期间计划游标不推进；恢复时把已到期 cron 任务的 next_trigger_time
        快进到当前时刻的下一个触发点，暂停期间落进 misfire 窗口的触发点不补跑
        （与"跳过暂停期间"的语义一致，否则暂停 1 小时的每 5 分钟任务会一次性
        补跑 12 次）。单次任务不快进：其计划时刻是用户显式指定的。
        """
        async with get_session() as session:
            now = await self._dao.now(session)
            control = await self._dao.get_control_for_update(session)
            control.paused_flag = 1 if paused else 0
            control.pause_reason = str(reason or "").strip()[:255]
            control.update_time = now
            control.update_by = user_id
            control.update_by_name = username
            if not paused:
                due_jobs = (await session.execute(
                    select(ChatDataAgentSchedule)
                    .where(
                        ChatDataAgentSchedule.enabled_flag == 1,
                        ChatDataAgentSchedule.delete_flag == 0,
                        ChatDataAgentSchedule.schedule_type == SCHEDULE_TYPE_CRON,
                        ChatDataAgentSchedule.next_trigger_time.is_not(None),
                        ChatDataAgentSchedule.next_trigger_time <= now,
                    )
                    .with_for_update()
                )).scalars().all()
                for job in due_jobs:
                    job.next_trigger_time = compute_next_trigger(
                        schedule_type=SCHEDULE_TYPE_CRON,
                        cron_expr=job.cron_expr,
                        plan_time=None,
                        effective_end_time=job.effective_end_time,
                        after=now,
                    )
            return control.to_dict()

    async def list_all_jobs(self, *, keyword: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        """全量任务分页列表，附执行统计与最近一次执行状态。"""
        async with get_session() as session:
            rows = await self._dao.list_jobs_with_stats(session, limit, offset, (keyword or "").strip() or None)
            latest_runs = await self._dao.latest_runs_by_jobs(session, [job.job_id for job, _stats in rows])
            titles = await self._dao.list_conversation_titles(session, [job.conversation_id for job, _stats in rows])
            items = []
            for job, stats in rows:
                item = job.to_dict()
                item.update(stats)
                item["conversation_title"] = titles.get(job.conversation_id)
                # 聚合列 last_plan_time 是裸 datetime，必须显式转 ISO 才能进 JSON 响应
                last_plan_time = item.get("last_plan_time")
                item["last_plan_time"] = last_plan_time.isoformat() if last_plan_time else None
                last_run = latest_runs.get(job.job_id)
                if last_run is not None:
                    last_time = last_run.finished_time or last_run.dispatched_time or last_run.plan_time
                    item["last_run_status"] = last_run.trigger_status
                    item["last_run_time"] = last_time.isoformat() if last_time else None
                else:
                    item["last_run_status"] = None
                    item["last_run_time"] = None
                items.append(item)
            return {"jobs": items}

    async def admin_set_job_enabled(self, *, job_id: str, enabled_flag: int, user_id: str, username: str) -> dict:
        """后台强制启停任意任务；重新启用时按服务端时钟重新武装。"""
        async with get_session() as session:
            now = await self._dao.now(session)
            job = await self._dao.get_job_for_update(session, job_id)
            if job is None:
                raise AgentScheduleNotFoundError("定时任务不存在")
            job.enabled_flag = 1 if enabled_flag else 0
            if enabled_flag:
                next_trigger = compute_next_trigger(
                    schedule_type=job.schedule_type,
                    cron_expr=job.cron_expr,
                    plan_time=job.plan_time,
                    effective_end_time=job.effective_end_time,
                    after=now,
                )
                if next_trigger is None:
                    raise AgentScheduleValidationError(
                        "该任务当前没有可触发的时间点（单次任务可能已触发，请修改执行时间）"
                    )
                job.next_trigger_time = next_trigger
            else:
                # 与用户侧 update_job 停用分支同口径：清退未终态触发实例防僵尸
                job.next_trigger_time = None
                await self._dao.cancel_unfinished_triggers(
                    session, job.job_id, now, "任务已停用，未执行的触发已取消",
                )
            job.update_time = now
            job.update_by = user_id
            job.update_by_name = username
            return job.to_dict()

    async def admin_list_job_runs(self, *, job_id: str, limit: int = 50) -> list[dict]:
        """查看任意任务的执行历史（后台入口，无归属校验）。"""
        async with get_session() as session:
            job = await self._dao.get_job(session, job_id)
            if job is None:
                raise AgentScheduleNotFoundError("定时任务不存在")
            runs = await self._dao.list_runs_by_job(session, job_id, limit)
            run_statuses = await self._dao.list_run_statuses(
                session,
                [run.run_id for run in runs if run.run_id],
            )
            return [run.to_dict(run_status=run_statuses.get(run.run_id)) for run in runs]

    @staticmethod
    def _load_options(job: ChatDataAgentSchedule) -> dict[str, Any]:
        try:
            value = json.loads(job.options_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


agent_schedule_service = AgentScheduleService()
