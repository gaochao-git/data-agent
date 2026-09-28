"""智能体定时任务管理：创建、查询、更新、删除、执行历史与后台全局管理。"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
from apps.chat_data_app.services.setting_service import get_runtime_setting
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

@dataclass(frozen=True)
class ScheduleFieldsUpdate:
    """定时任务调度字段的更新意图（全部可缺省，缺省表示沿用任务现值）。

    这五个字段只在「调度配置真的变了」的场景下成套出现，成组之后 ``update_job`` 的
    形参数量才能落在 AGENTS.md §3 的阈值内。
    """

    schedule_type: str | None = None
    cron_expr: str | None = None
    plan_time: datetime | None = None
    effective_end_time: datetime | None = None
    clear_effective_end_time: bool = False


@dataclass(frozen=True)
class UpdateOperator:
    """写操作的审计对象：用户 ID 与其显示名（两者总是一起写入替换审计列）。"""

    user_id: str = ""
    username: str = ""


logger = get_logger(__name__)

# 会话绑定方式：fixed-绑定指定会话；new_each_run-每次执行新建会话
CONVERSATION_MODE_FIXED = "fixed"
CONVERSATION_MODE_NEW_EACH_RUN = "new_each_run"
CONVERSATION_MODES = (CONVERSATION_MODE_FIXED, CONVERSATION_MODE_NEW_EACH_RUN)

# 单个用户可创建的定时任务上限（CHAT_DATA_SCHEDULE_MAX_JOBS_PER_USER）走运行时
# 热值（get_runtime_setting：表覆盖 > env > 注册默认），改配置无需重启。


class AgentScheduleNotFoundError(LookupError):
    """任务不存在或不属于当前用户。"""


class AgentScheduleConversationInvalidError(AgentScheduleValidationError):
    """绑定的会话不存在或不属于当前用户。"""


class AgentScheduleLimitExceededError(RuntimeError):
    """超出单用户任务数量上限。"""


def _validate_options(options: dict[str, Any] | None) -> str | None:
    """校验并序列化运行选项；服务端不信任前端原样 JSON。

    创建路径不存在既有配置，``null`` 与「未设置」等价，直接剔除，避免
    ``options_json`` 里留下无意义的空键。更新路径的 ``null`` 另有含义
    （清除该键），见 :meth:`AgentScheduleService._merge_options_patch`。
    """
    if options is None:
        return None
    if not isinstance(options, dict):
        raise AgentScheduleValidationError("运行选项必须是对象")
    cleaned = {key: value for key, value in options.items() if value is not None}
    try:
        return json.dumps(cleaned, ensure_ascii=False)
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
            max_jobs_per_user = await get_runtime_setting("CHAT_DATA_SCHEDULE_MAX_JOBS_PER_USER")
            if await self._dao.count_jobs_by_user(session, user_id) >= max_jobs_per_user:
                raise AgentScheduleLimitExceededError(f"最多创建 {max_jobs_per_user} 个定时任务")
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

    async def list_jobs(self, *, user_id: str, limit: int = 50, offset: int = 0) -> dict:
        """当前用户的任务分页列表，返回 ``{"jobs": [...], "total": n}``。"""
        async with get_session() as session:
            jobs = await self._dao.list_jobs_by_user(session, user_id, limit, offset)
            total = await self._dao.count_jobs_by_user(session, user_id)
            titles = await self._dao.list_conversation_titles(session, [job.conversation_id for job in jobs])
            items = []
            for job in jobs:
                item = job.to_dict()
                item["conversation_title"] = titles.get(job.conversation_id)
                items.append(item)
            return {"jobs": items, "total": total}

    async def update_job(
        self,
        *,
        job_id: str,
        title: str | None,
        prompt: str | None,
        conversation_mode: str | None = None,
        conversation_id: str | None = None,
        schedule: ScheduleFieldsUpdate | None = None,
        enabled_flag: int | None,
        datasource_id: str | None,
        options: dict[str, Any] | None,
        operator: UpdateOperator,
    ) -> dict:
        """更新任务；未传的字段保持不变。

        运行选项按补丁语义合并：只提交要改的键，值为 ``null`` 表示清除该键
        （回退系统默认）；未提交的键（如 ``semantic_config_id`` / ``skill_names``）
        原样保留，避免编辑标题时顺带清空其它运行配置。调度字段仅在真的变化时
        才重新校验并重算 ``next_trigger_time``。
        """
        schedule_fields = schedule or ScheduleFieldsUpdate()
        async with get_session() as session:
            now = await self._dao.now(session)
            job = await self._dao.get_job_for_update(session, job_id)
            if job is None or job.user_id != operator.user_id:
                raise AgentScheduleNotFoundError("定时任务不存在")
            resolved_mode, conversation_key = await self._resolve_conversation_binding(
                session, job, conversation_mode, conversation_id, operator.user_id
            )
            resolved, schedule_changed = self._resolve_schedule_update(
                job,
                schedule_type=schedule_fields.schedule_type,
                cron_expr=schedule_fields.cron_expr,
                plan_time=schedule_fields.plan_time,
                effective_end_time=schedule_fields.effective_end_time,
                clear_effective_end_time=schedule_fields.clear_effective_end_time,
                now=now,
            )
            if title is not None:
                job.job_title = normalize_title(title)
            if prompt is not None:
                job.job_prompt = normalize_prompt(prompt)
            self._apply_options_patch(
                job,
                options=options,
                datasource_id=datasource_id,
            )
            self._apply_conversation_binding(job, resolved_mode, conversation_key)
            self._apply_schedule_fields(job, resolved)
            await self._apply_enabled_transition(session, job, enabled_flag, now, schedule_changed)
            job.update_time = now
            job.update_by = operator.user_id
            job.update_by_name = operator.username
            return job.to_dict()

    # ---- update_job 的分段实现：只为把单函数复杂度压进阈值，语义与未拆分时完全一致 ----

    async def _resolve_conversation_binding(
        self,
        session,
        job: ChatDataAgentSchedule,
        conversation_mode: str | None,
        conversation_id: str | None,
        user_id: str,
    ) -> tuple[str, str]:
        """校验会话绑定方式，返回 (绑定方式, 最终 conversation_id)。"""
        resolved_mode = str(conversation_mode or job.conversation_mode).strip()
        if resolved_mode not in CONVERSATION_MODES:
            raise AgentScheduleValidationError(f"会话绑定方式必须是 {' 或 '.join(CONVERSATION_MODES)}")
        if resolved_mode != CONVERSATION_MODE_FIXED:
            # 每次执行新建会话：不绑定具体会话，conversation_id 置空
            return resolved_mode, ""
        # 指定会话模式：优先取新传入的会话，否则沿用任务已绑定的会话
        conversation_key = str(conversation_id or "").strip() or job.conversation_id
        if not conversation_key:
            raise AgentScheduleValidationError("指定会话模式必须选择一个会话")
        if conversation_key != job.conversation_id and not await self._dao.conversation_exists(
            session, conversation_key, user_id
        ):
            raise AgentScheduleConversationInvalidError("会话不存在或不属于当前用户")
        return resolved_mode, conversation_key

    def _resolve_schedule_update(
        self,
        job: ChatDataAgentSchedule,
        *,
        schedule_type: str | None,
        cron_expr: str | None,
        plan_time: datetime | None,
        effective_end_time: datetime | None,
        clear_effective_end_time: bool,
        now: datetime,
    ) -> tuple[tuple[str, str, datetime | None, datetime | None, datetime | None] | None, bool]:
        """解析调度字段变更，返回 (resolved 五元组, 是否变更)；未变更时 resolved 为 ``None``。

        仅当调度字段真的变了才校验并重算 ``next_trigger_time``：否则执行时间已过期的
        单次任务会被「必须晚于当前时间」永久拦住，连改标题都报错。
        """
        candidate_type = str(schedule_type or job.schedule_type).strip()
        if candidate_type not in SCHEDULE_TYPES:
            raise AgentScheduleValidationError(f"调度类型必须是 {' 或 '.join(SCHEDULE_TYPES)}")
        candidate_cron = cron_expr if cron_expr is not None else job.cron_expr
        candidate_plan = plan_time if plan_time is not None else job.plan_time
        if clear_effective_end_time:
            # 显式清除有效期（None 无法区分「未提供」与「清空」，由路由层判定）
            candidate_end = None
        else:
            candidate_end = effective_end_time if effective_end_time is not None else job.effective_end_time
        if (candidate_type, candidate_cron, candidate_plan, candidate_end) == (
            job.schedule_type,
            job.cron_expr,
            job.plan_time,
            job.effective_end_time,
        ):
            return None, False
        resolved = _resolve_schedule_fields(
            schedule_type=candidate_type,
            cron_expr=candidate_cron,
            plan_time=candidate_plan,
            effective_end_time=candidate_end,
            now=now,
        )
        return resolved, True

    def _apply_options_patch(
        self,
        job: ChatDataAgentSchedule,
        *,
        options: dict[str, Any] | None,
        datasource_id: str | None,
    ) -> None:
        """按补丁语义收敛运行选项：未提交的键原样保留、``null`` 表示清除该键。

        ``options`` 是补丁而非整份配置，编辑表单掌管的键之外的配置
        （semantic_config_id / skill_names 等）不由前端掌管，必须保留原值。
        """
        if options is None and datasource_id is None:
            return
        merged = self._merge_options_patch(
            self._load_options(job),
            options if isinstance(options, dict) else {},
        )
        if datasource_id:
            merged["datasource_id"] = str(datasource_id).strip()
        job.options_json = json.dumps(merged, ensure_ascii=False)

    @staticmethod
    def _apply_conversation_binding(job: ChatDataAgentSchedule, resolved_mode: str, conversation_key: str) -> None:
        if resolved_mode != job.conversation_mode or conversation_key != job.conversation_id:
            job.conversation_mode = resolved_mode
            job.conversation_id = conversation_key

    @staticmethod
    def _apply_schedule_fields(
        job: ChatDataAgentSchedule,
        resolved: tuple[str, str, datetime | None, datetime | None, datetime | None] | None,
    ) -> None:
        """调度字段变更一律以服务端时钟重新计算下一次触发时间；未变更（``None``）时不动。"""
        if resolved is None:
            return
        job.schedule_type = resolved[0]
        job.cron_expr = resolved[1]
        job.plan_time = resolved[2]
        job.effective_end_time = resolved[3]
        job.next_trigger_time = resolved[4]

    async def _apply_enabled_transition(
        self,
        session,
        job: ChatDataAgentSchedule,
        enabled_flag: int | None,
        now: datetime,
        schedule_changed: bool,
    ) -> None:
        """启停切换：启用时重新武装游标，停用时清退未终态触发实例防僵尸。"""
        if enabled_flag is None or (1 if enabled_flag else 0) == job.enabled_flag:
            return
        job.enabled_flag = 1 if enabled_flag else 0
        if not enabled_flag:
            job.next_trigger_time = None
            # 停用后 pending/claimed 实例永不会被派发，主动清退防僵尸
            await self._dao.cancel_unfinished_triggers(
                session, job.job_id, now, "任务已停用，未执行的触发已取消",
            )
            return
        if schedule_changed:
            # 调度字段已重算，不要再覆盖上面的结果
            return
        job.next_trigger_time = compute_next_trigger(
            schedule_type=job.schedule_type,
            cron_expr=job.cron_expr,
            plan_time=job.plan_time,
            effective_end_time=job.effective_end_time,
            after=now,
        )

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

    async def list_job_runs(self, *, job_id: str, user_id: str, limit: int = 50, offset: int = 0) -> dict:
        """任务执行历史分页，返回 ``{"runs": [...], "total": n}``。"""
        async with get_session() as session:
            job = await self._dao.get_job(session, job_id)
            if job is None or job.user_id != user_id:
                raise AgentScheduleNotFoundError("定时任务不存在")
            runs = await self._dao.list_runs_by_job(session, job_id, limit, offset)
            total = await self._dao.count_runs_by_job(session, job_id)
            run_statuses = await self._dao.list_run_statuses(
                session,
                [run.run_id for run in runs if run.run_id],
            )
            return {
                "runs": [run.to_dict(run_status=run_statuses.get(run.run_id)) for run in runs],
                "total": total,
            }

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
                "cancelled_runs": status_counts.get("cancelled", 0),
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

    @staticmethod
    def _build_job_list_item(job, stats, titles, latest_runs) -> dict:
        """组装单条任务列表项，含会话标题与最近一次执行状态（裸 datetime 转 ISO）。"""
        item = job.to_dict()
        item.update(stats)
        item["conversation_title"] = titles.get(job.conversation_id)
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
        return item

    async def list_all_jobs(
        self,
        *,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
        enabled: bool | None = None,
        expired: bool | None = None,
    ) -> dict:
        """全量任务分页列表，附执行统计与最近一次执行状态。"""
        async with get_session() as session:
            normalized_keyword = (keyword or "").strip() or None
            enabled_flag = None if enabled is None else (1 if enabled else 0)
            now = await self._dao.now(session) if expired is not None else None
            rows = await self._dao.list_jobs_with_stats(
                session, limit, offset, normalized_keyword,
                enabled_flag=enabled_flag, expired=expired, now=now,
            )
            total = await self._dao.count_jobs_filtered(
                session, normalized_keyword,
                enabled_flag=enabled_flag, expired=expired, now=now,
            )
            latest_runs = await self._dao.latest_runs_by_jobs(session, [job.job_id for job, _stats in rows])
            titles = await self._dao.list_conversation_titles(session, [job.conversation_id for job, _stats in rows])
            items = [self._build_job_list_item(job, stats, titles, latest_runs) for job, stats in rows]
            return {"jobs": items, "total": total}

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

    async def admin_list_job_runs(self, *, job_id: str, limit: int = 50, offset: int = 0) -> dict:
        """查看任意任务的执行历史分页（后台入口，无归属校验）。"""
        async with get_session() as session:
            job = await self._dao.get_job(session, job_id)
            if job is None:
                raise AgentScheduleNotFoundError("定时任务不存在")
            runs = await self._dao.list_runs_by_job(session, job_id, limit, offset)
            total = await self._dao.count_runs_by_job(session, job_id)
            run_statuses = await self._dao.list_run_statuses(
                session,
                [run.run_id for run in runs if run.run_id],
            )
            return {
                "runs": [run.to_dict(run_status=run_statuses.get(run.run_id)) for run in runs],
                "total": total,
            }

    @staticmethod
    def _load_options(job: ChatDataAgentSchedule) -> dict[str, Any]:
        try:
            value = json.loads(job.options_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _merge_options_patch(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        """把运行选项补丁合并到既有配置：``None`` 值表示清除该键。"""
        merged = dict(current)
        for key, value in patch.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        return merged


agent_schedule_service = AgentScheduleService()
