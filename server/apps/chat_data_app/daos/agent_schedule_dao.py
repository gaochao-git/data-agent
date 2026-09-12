"""智能体定时任务数据访问：任务定义、触发实例与全局控制的事务内读写。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from utils.logger import get_logger
from utils.models import (
    ChatDataAgentInbox,
    ChatDataAgentSchedule,
    ChatDataAgentScheduleControl,
    ChatDataAgentScheduleRun,
    UserThread,
)
from .agent_inbox_dao import agent_inbox_dao

logger = get_logger(__name__)

# 触发实例状态（chat_data_agent_schedule_run.trigger_status）
SCHEDULE_RUN_STATUS_PENDING = "pending"
SCHEDULE_RUN_STATUS_CLAIMED = "claimed"
SCHEDULE_RUN_STATUS_DISPATCHED = "dispatched"
SCHEDULE_RUN_STATUS_SUCCEEDED = "succeeded"
SCHEDULE_RUN_STATUS_FAILED = "failed"
SCHEDULE_RUN_STATUS_CANCELLED = "cancelled"
SCHEDULE_RUN_STATUS_SKIPPED = "skipped"

# 触发来源（chat_data_agent_schedule_run.trigger_type）
TRIGGER_TYPE_SCHEDULE = "schedule"
TRIGGER_TYPE_MANUAL = "manual"

# Agent Run 终态 -> 触发实例终态映射；waiting_user 等非终态不在映射内
RUN_STATUS_TO_TRIGGER_STATUS = {
    "finished": SCHEDULE_RUN_STATUS_SUCCEEDED,
    "failed": SCHEDULE_RUN_STATUS_FAILED,
    "cancelled": SCHEDULE_RUN_STATUS_CANCELLED,
}


class AgentScheduleDao:
    """只负责事务内的定时任务与触发实例读写。"""

    async def now(self, session: AsyncSession) -> datetime:
        """租约与时钟统一使用数据库 now()，口径与 Agent 收件箱一致。"""
        return await agent_inbox_dao.now(session)

    # ---- 任务定义 ----

    async def list_due_jobs(self, session: AsyncSession, now: datetime, limit: int) -> list[ChatDataAgentSchedule]:
        """扫描到期任务并加行锁，串行化多节点的触发计划推进。"""
        result = await session.execute(
            select(ChatDataAgentSchedule)
            .where(
                ChatDataAgentSchedule.enabled_flag == 1,
                ChatDataAgentSchedule.delete_flag == 0,
                ChatDataAgentSchedule.next_trigger_time.is_not(None),
                ChatDataAgentSchedule.next_trigger_time <= now,
            )
            .order_by(ChatDataAgentSchedule.next_trigger_time)
            .limit(max(limit, 1))
            .with_for_update()
        )
        return list(result.scalars().all())

    async def count_jobs_by_user(self, session: AsyncSession, user_id: str) -> int:
        result = await session.execute(
            select(func.count())
            .select_from(ChatDataAgentSchedule)
            .where(ChatDataAgentSchedule.user_id == user_id, ChatDataAgentSchedule.delete_flag == 0)
        )
        return int(result.scalar() or 0)

    async def get_job(self, session: AsyncSession, job_id: str) -> ChatDataAgentSchedule | None:
        result = await session.execute(
            select(ChatDataAgentSchedule).where(
                ChatDataAgentSchedule.job_id == job_id,
                ChatDataAgentSchedule.delete_flag == 0,
            )
        )
        return result.scalar_one_or_none()

    async def get_job_for_update(self, session: AsyncSession, job_id: str) -> ChatDataAgentSchedule | None:
        result = await session.execute(
            select(ChatDataAgentSchedule).where(
                ChatDataAgentSchedule.job_id == job_id,
                ChatDataAgentSchedule.delete_flag == 0,
            ).with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_jobs_by_user(self, session: AsyncSession, user_id: str) -> list[ChatDataAgentSchedule]:
        result = await session.execute(
            select(ChatDataAgentSchedule)
            .where(ChatDataAgentSchedule.user_id == user_id, ChatDataAgentSchedule.delete_flag == 0)
            .order_by(ChatDataAgentSchedule.id.desc())
        )
        return list(result.scalars().all())

    async def conversation_exists(self, session: AsyncSession, conversation_id: str, user_id: str) -> bool:
        result = await session.execute(
            select(UserThread.id).where(
                UserThread.thread_id == conversation_id,
                UserThread.user_id == user_id,
                UserThread.delete_flag == 0,
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def list_conversation_titles(self, session: AsyncSession, conversation_ids: list[str]) -> dict[str, str]:
        """按会话 ID 批量取标题，供任务列表展示绑定会话（实时跟随改名）。"""
        if not conversation_ids:
            return {}
        result = await session.execute(
            select(UserThread.thread_id, UserThread.thread_title).where(
                UserThread.thread_id.in_(conversation_ids),
                UserThread.delete_flag == 0,
            )
        )
        return {row[0]: row[1] for row in result.all() if row[0]}

    # ---- 触发实例 ----

    async def get_trigger(
        self,
        session: AsyncSession,
        job_id: str,
        plan_time: datetime,
    ) -> ChatDataAgentScheduleRun | None:
        result = await session.execute(
            select(ChatDataAgentScheduleRun).where(
                ChatDataAgentScheduleRun.job_id == job_id,
                ChatDataAgentScheduleRun.plan_time == plan_time,
                ChatDataAgentScheduleRun.delete_flag == 0,
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def list_claimable_runs(
        self,
        session: AsyncSession,
        now: datetime,
        limit: int,
    ) -> list[ChatDataAgentScheduleRun]:
        """扫出可派发的触发实例并加行锁，行锁保证多节点只有一个抢占者。"""
        result = await session.execute(
            select(ChatDataAgentScheduleRun)
            .join(
                ChatDataAgentSchedule,
                (ChatDataAgentSchedule.job_id == ChatDataAgentScheduleRun.job_id)
                & (ChatDataAgentSchedule.enabled_flag == 1)
                & (ChatDataAgentSchedule.delete_flag == 0),
            )
            .where(
                ChatDataAgentScheduleRun.trigger_status == SCHEDULE_RUN_STATUS_PENDING,
                ChatDataAgentScheduleRun.plan_time <= now,
                ChatDataAgentScheduleRun.delete_flag == 0,
            )
            .order_by(ChatDataAgentScheduleRun.plan_time)
            .limit(max(limit, 1))
            .with_for_update()
        )
        return list(result.scalars().all())

    async def list_stale_claimed_ids(
        self,
        session: AsyncSession,
        before: datetime,
        limit: int,
    ) -> list[int]:
        """扫出抢占超时（claimed_time 早于 before）的触发实例 ID，供悬挂回收。"""
        result = await session.execute(
            select(ChatDataAgentScheduleRun.id)
            .where(
                ChatDataAgentScheduleRun.trigger_status == SCHEDULE_RUN_STATUS_CLAIMED,
                ChatDataAgentScheduleRun.claimed_time.is_not(None),
                ChatDataAgentScheduleRun.claimed_time < before,
                ChatDataAgentScheduleRun.delete_flag == 0,
            )
            .order_by(ChatDataAgentScheduleRun.claimed_time)
            .limit(max(limit, 1))
        )
        return [row[0] for row in result.all()]

    async def cancel_unfinished_triggers(
        self,
        session: AsyncSession,
        job_id: str,
        now: datetime,
        reason: str,
    ) -> int:
        """把任务未终态（pending/claimed）的触发实例批量置为 cancelled。

        任务停用/删除后其 pending 实例永不会被派发（claim 查询 join 了
        enabled_flag=1），不主动清退会变成僵尸并被概览的执行中统计计入。
        """
        result = await session.execute(
            update(ChatDataAgentScheduleRun)
            .where(
                ChatDataAgentScheduleRun.job_id == job_id,
                ChatDataAgentScheduleRun.trigger_status.in_(
                    (SCHEDULE_RUN_STATUS_PENDING, SCHEDULE_RUN_STATUS_CLAIMED)
                ),
                ChatDataAgentScheduleRun.delete_flag == 0,
            )
            .values(
                trigger_status=SCHEDULE_RUN_STATUS_CANCELLED,
                error_message=reason[:1000],
                finished_time=now,
                update_time=now,
            )
        )
        return result.rowcount or 0

    async def count_dispatched_by_users(self, session: AsyncSession, user_ids: list[str]) -> dict[str, int]:
        """统计用户在途（已派发未收口）的定时执行数，用于个人并发闸门。"""
        if not user_ids:
            return {}
        result = await session.execute(
            select(ChatDataAgentScheduleRun.user_id, func.count())
            .where(
                ChatDataAgentScheduleRun.user_id.in_(user_ids),
                ChatDataAgentScheduleRun.trigger_status == SCHEDULE_RUN_STATUS_DISPATCHED,
                ChatDataAgentScheduleRun.delete_flag == 0,
            )
            .group_by(ChatDataAgentScheduleRun.user_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def get_trigger_for_update(self, session: AsyncSession, trigger_id: int) -> ChatDataAgentScheduleRun | None:
        result = await session.execute(
            select(ChatDataAgentScheduleRun)
            .where(ChatDataAgentScheduleRun.id == trigger_id, ChatDataAgentScheduleRun.delete_flag == 0)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_dispatched_runs(self, session: AsyncSession, limit: int) -> list[ChatDataAgentScheduleRun]:
        result = await session.execute(
            select(ChatDataAgentScheduleRun)
            .where(
                ChatDataAgentScheduleRun.trigger_status == SCHEDULE_RUN_STATUS_DISPATCHED,
                ChatDataAgentScheduleRun.run_id != "",
                ChatDataAgentScheduleRun.delete_flag == 0,
            )
            .order_by(ChatDataAgentScheduleRun.id)
            .limit(max(limit, 1))
        )
        return list(result.scalars().all())

    async def list_run_statuses(self, session: AsyncSession, run_ids: list[str]) -> dict[str, str]:
        """批量读取 Agent Run 行状态，用于把触发实例收口到终态。"""
        if not run_ids:
            return {}
        result = await session.execute(
            select(ChatDataAgentInbox.run_id, ChatDataAgentInbox.status).where(
                ChatDataAgentInbox.run_id.in_(run_ids),
                ChatDataAgentInbox.record_type == "run",
                ChatDataAgentInbox.delete_flag == 0,
            )
        )
        return {row[0]: row[1] for row in result.all()}

    async def list_runs_by_job(
        self,
        session: AsyncSession,
        job_id: str,
        limit: int,
    ) -> list[ChatDataAgentScheduleRun]:
        result = await session.execute(
            select(ChatDataAgentScheduleRun)
            .where(
                ChatDataAgentScheduleRun.job_id == job_id,
                ChatDataAgentScheduleRun.delete_flag == 0,
            )
            .order_by(ChatDataAgentScheduleRun.plan_time.desc(), ChatDataAgentScheduleRun.id.desc())
            .limit(max(limit, 1))
        )
        return list(result.scalars().all())

    # ---- 全局控制（后台一键暂停） ----

    async def get_control(self, session: AsyncSession) -> ChatDataAgentScheduleControl:
        """读取固定单行的全局控制记录；缺失时惰性创建，避免依赖建表后的手工 INSERT。

        按仓库标注用法，时间字段在写入点用 DB 时钟显式赋值：新 INSERT 行的
        server_default 列在 MySQL 下 flush 后是过期态，回读会触发同步惰性加载
        并抛 MissingGreenlet，不能依赖 server_default 拿值。
        """
        result = await session.execute(
            select(ChatDataAgentScheduleControl).where(ChatDataAgentScheduleControl.id == 1)
        )
        control = result.scalar_one_or_none()
        if control is None:
            now = await self.now(session)
            control = ChatDataAgentScheduleControl(
                id=1,
                paused_flag=0,
                delete_flag=0,
                create_time=now,
                update_time=now,
            )
            session.add(control)
            await session.flush()
        return control

    async def get_control_for_update(self, session: AsyncSession) -> ChatDataAgentScheduleControl:
        """带行锁读取全局控制记录，供暂停/恢复切换使用。"""
        result = await session.execute(
            select(ChatDataAgentScheduleControl)
            .where(ChatDataAgentScheduleControl.id == 1)
            .with_for_update()
        )
        control = result.scalar_one_or_none()
        if control is None:
            now = await self.now(session)
            control = ChatDataAgentScheduleControl(
                id=1,
                paused_flag=0,
                delete_flag=0,
                create_time=now,
                update_time=now,
            )
            session.add(control)
            await session.flush()
        return control

    # ---- 后台管理统计 ----

    async def count_all_jobs(self, session: AsyncSession) -> tuple[int, int]:
        """返回 (任务总数, 启用数)。"""
        result = await session.execute(
            select(
                func.count(),
                func.sum(case((ChatDataAgentSchedule.enabled_flag == 1, 1), else_=0)),
            )
            .select_from(ChatDataAgentSchedule)
            .where(ChatDataAgentSchedule.delete_flag == 0)
        )
        row = result.one()
        return int(row[0] or 0), int(row[1] or 0)

    async def count_runs_by_status(self, session: AsyncSession) -> dict[str, int]:
        result = await session.execute(
            select(ChatDataAgentScheduleRun.trigger_status, func.count())
            .where(ChatDataAgentScheduleRun.delete_flag == 0)
            .group_by(ChatDataAgentScheduleRun.trigger_status)
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def count_runs_since(self, session: AsyncSession, since: datetime) -> int:
        result = await session.execute(
            select(func.count())
            .select_from(ChatDataAgentScheduleRun)
            .where(
                ChatDataAgentScheduleRun.delete_flag == 0,
                ChatDataAgentScheduleRun.create_time >= since,
            )
        )
        return int(result.scalar() or 0)

    async def count_jobs_filtered(self, session: AsyncSession, keyword: str | None) -> int:
        """按关键词过滤后的任务总数（与 list_jobs_with_stats 同一口径，供分页 total）。"""
        conditions = [ChatDataAgentSchedule.delete_flag == 0]
        if keyword:
            like = f"%{keyword}%"
            conditions.append(
                ChatDataAgentSchedule.job_title.like(like) | ChatDataAgentSchedule.user_id.like(like)
            )
        result = await session.execute(
            select(func.count())
            .select_from(ChatDataAgentSchedule)
            .where(*conditions)
        )
        return int(result.scalar() or 0)

    async def list_jobs_with_stats(
        self,
        session: AsyncSession,
        limit: int,
        offset: int,
        keyword: str | None,
    ) -> list[tuple[ChatDataAgentSchedule, dict]]:
        """全量任务分页列表，附每个任务的执行统计（次数/成功/失败/最近执行）。"""
        stats = (
            select(
                ChatDataAgentScheduleRun.job_id.label("job_id"),
                func.count().label("total_count"),
                func.sum(case((ChatDataAgentScheduleRun.trigger_status == SCHEDULE_RUN_STATUS_SUCCEEDED, 1), else_=0)).label("succeeded_count"),
                func.sum(case((ChatDataAgentScheduleRun.trigger_status == SCHEDULE_RUN_STATUS_FAILED, 1), else_=0)).label("failed_count"),
                func.sum(case((ChatDataAgentScheduleRun.trigger_status == SCHEDULE_RUN_STATUS_SKIPPED, 1), else_=0)).label("skipped_count"),
                func.max(ChatDataAgentScheduleRun.plan_time).label("last_plan_time"),
            )
            .where(ChatDataAgentScheduleRun.delete_flag == 0)
            .group_by(ChatDataAgentScheduleRun.job_id)
            .subquery()
        )
        conditions = [ChatDataAgentSchedule.delete_flag == 0]
        if keyword:
            like = f"%{keyword}%"
            conditions.append(
                ChatDataAgentSchedule.job_title.like(like) | ChatDataAgentSchedule.user_id.like(like)
            )
        result = await session.execute(
            select(
                ChatDataAgentSchedule,
                stats.c.total_count,
                stats.c.succeeded_count,
                stats.c.failed_count,
                stats.c.skipped_count,
                stats.c.last_plan_time,
            )
            .outerjoin(stats, stats.c.job_id == ChatDataAgentSchedule.job_id)
            .where(*conditions)
            .order_by(ChatDataAgentSchedule.id.desc())
            .offset(max(offset, 0))
            .limit(max(limit, 1))
        )
        rows: list[tuple[ChatDataAgentSchedule, dict]] = []
        for job, total_count, succeeded_count, failed_count, skipped_count, last_plan_time in result.all():
            rows.append((job, {
                "total_count": int(total_count or 0),
                "succeeded_count": int(succeeded_count or 0),
                "failed_count": int(failed_count or 0),
                "skipped_count": int(skipped_count or 0),
                "last_plan_time": last_plan_time,
            }))
        return rows

    async def latest_runs_by_jobs(
        self,
        session: AsyncSession,
        job_ids: list[str],
    ) -> dict[str, ChatDataAgentScheduleRun]:
        """取每个任务的最近一条触发实例，用于列表展示最近执行状态。

        两步查询（GROUP BY 取 MAX(id) 再按主键取行）：跑了一年的高频任务
        单任务就有数十万触发实例，整段读进内存去重会拖垮后台列表接口，
        且 MAX(id) 聚合可走 idx_schedule_run_job 前缀，兼容 MySQL 5.7。
        """
        if not job_ids:
            return {}
        max_id_rows = (await session.execute(
            select(ChatDataAgentScheduleRun.job_id, func.max(ChatDataAgentScheduleRun.id))
            .where(
                ChatDataAgentScheduleRun.job_id.in_(job_ids),
                ChatDataAgentScheduleRun.delete_flag == 0,
            )
            .group_by(ChatDataAgentScheduleRun.job_id)
        )).all()
        latest_ids = [row[1] for row in max_id_rows]
        if not latest_ids:
            return {}
        rows = (await session.execute(
            select(ChatDataAgentScheduleRun).where(ChatDataAgentScheduleRun.id.in_(latest_ids))
        )).scalars().all()
        return {run.job_id: run for run in rows}


agent_schedule_dao = AgentScheduleDao()
