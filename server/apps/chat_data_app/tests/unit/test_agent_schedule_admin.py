"""智能体定时任务后台管理（概览/暂停/全量列表/强制启停）单测。

所有 service 返回 dict 一律追加 json.dumps 断言：business_response 底层是裸
json.dumps（无 datetime 编码器），裸 datetime 进响应即线上 500，SQLite+RETURNING
掩盖不住这类问题，必须在单测层拦住。
"""

import asyncio
import json
import types
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.services.agent_schedule import schedule_service as service_module
from apps.chat_data_app.services.agent_schedule.schedule_service import (
    AgentScheduleNotFoundError,
    AgentScheduleValidationError,
    AgentScheduleService,
)
from utils.models import Base, ChatDataAgentSchedule, ChatDataAgentScheduleControl, ChatDataAgentScheduleRun


@pytest.fixture()
def harness(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_test_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(service_module, "get_session", get_test_session)
    service = AgentScheduleService()

    async def seed_job(job_id="job-1", user_id="user-1", enabled_flag=1, **overrides):
        fields = dict(
            job_id=job_id,
            user_id=user_id,
            conversation_id="conv-1",
            job_title=f"任务-{job_id}",
            job_prompt="汇总昨日数据",
            schedule_type="cron",
            cron_expr="0 9 * * *",
            enabled_flag=enabled_flag,
            next_trigger_time=datetime.now() + timedelta(hours=1),
            delete_flag=0,
            create_time=datetime.now(),
            create_by=user_id,
            create_by_name="用户一",
            update_by=user_id,
            update_by_name="用户一",
        )
        fields.update(overrides)
        async with get_test_session() as session:
            session.add(ChatDataAgentSchedule(**fields))

    async def seed_run(job_id="job-1", trigger_status="succeeded", plan_time=None):
        async with get_test_session() as session:
            session.add(ChatDataAgentScheduleRun(
                job_id=job_id,
                user_id="user-1",
                conversation_id="conv-1",
                plan_time=plan_time or datetime.now() - timedelta(minutes=5),
                trigger_status=trigger_status,
                delete_flag=0,
                create_time=datetime.now(),
            ))

    yield types.SimpleNamespace(
        service=service,
        seed_job=seed_job,
        seed_run=seed_run,
        session_factory=get_test_session,
    )
    asyncio.run(engine.dispose())


@pytest.mark.asyncio
async def test_overview_counts_jobs_and_runs(harness):
    await harness.seed_job("job-1", enabled_flag=1)
    await harness.seed_job("job-2", enabled_flag=0)
    await harness.seed_run("job-1", trigger_status="succeeded")
    await harness.seed_run("job-1", trigger_status="failed")
    await harness.seed_run("job-2", trigger_status="pending")

    overview = await harness.service.get_admin_overview()

    assert overview["total_jobs"] == 2
    assert overview["enabled_jobs"] == 1
    assert overview["total_runs"] == 3
    assert overview["succeeded_runs"] == 1
    assert overview["failed_runs"] == 1
    assert overview["paused_flag"] == 0


@pytest.mark.asyncio
async def test_set_paused_roundtrip(harness):
    paused = await harness.service.set_paused(
        paused=True, reason="发布窗口", user_id="admin-1", username="管理员",
    )
    assert paused["paused_flag"] == 1
    assert paused["pause_reason"] == "发布窗口"

    overview = await harness.service.get_admin_overview()
    assert overview["paused_flag"] == 1

    resumed = await harness.service.set_paused(
        paused=False, reason="", user_id="admin-1", username="管理员",
    )
    assert resumed["paused_flag"] == 0
    json.dumps(resumed)


@pytest.mark.asyncio
async def test_control_row_lazily_created(harness):
    overview = await harness.service.get_admin_overview()
    assert overview["paused_flag"] == 0
    async with harness.session_factory() as session:
        control = (await session.execute(
            select(ChatDataAgentScheduleControl).where(ChatDataAgentScheduleControl.id == 1)
        )).scalar_one()
    assert control.paused_flag == 0


@pytest.mark.asyncio
async def test_list_all_jobs_with_stats(harness):
    await harness.seed_job("job-1")
    await harness.seed_job("job-2", user_id="user-2")
    await harness.seed_run("job-1", trigger_status="succeeded", plan_time=datetime(2026, 9, 12, 9, 0))
    await harness.seed_run("job-1", trigger_status="failed", plan_time=datetime(2026, 9, 12, 10, 0))

    data = await harness.service.list_all_jobs()
    assert len(data["jobs"]) == 2
    first = next(job for job in data["jobs"] if job["job_id"] == "job-1")
    assert first["create_by_name"] == "用户一"
    assert first["total_count"] == 2
    assert first["succeeded_count"] == 1
    assert first["failed_count"] == 1
    assert first["last_run_status"] == "failed"
    assert first["last_run_time"] == datetime(2026, 9, 12, 10, 0).isoformat()
    second = next(job for job in data["jobs"] if job["job_id"] == "job-2")
    assert second["total_count"] == 0
    assert second["last_run_status"] is None

    filtered = await harness.service.list_all_jobs(keyword="任务-job-2")
    assert [job["job_id"] for job in filtered["jobs"]] == ["job-2"]
    json.dumps(data)
    json.dumps(filtered)


@pytest.mark.asyncio
async def test_list_all_jobs_filters(harness):
    """启停/是否过期两个过滤维度。"""
    now = datetime.now()
    await harness.seed_job("job-on-active", enabled_flag=1)  # 开启，长期有效
    await harness.seed_job("job-off-expired", enabled_flag=0, effective_end_time=now - timedelta(days=1))
    await harness.seed_job("job-on-expired", enabled_flag=1, effective_end_time=now - timedelta(days=2))
    await harness.seed_job("job-on-future-end", enabled_flag=1, effective_end_time=now + timedelta(days=1))

    by_enabled = await harness.service.list_all_jobs(enabled=False)
    assert sorted(job["job_id"] for job in by_enabled["jobs"]) == ["job-off-expired"]
    assert by_enabled["total"] == 1

    expired = await harness.service.list_all_jobs(expired=True)
    assert sorted(job["job_id"] for job in expired["jobs"]) == ["job-off-expired", "job-on-expired"]

    active = await harness.service.list_all_jobs(expired=False)
    assert sorted(job["job_id"] for job in active["jobs"]) == ["job-on-active", "job-on-future-end"]

    # 组合过滤：开启 + 已过期 → 只剩 job-on-expired
    combined = await harness.service.list_all_jobs(enabled=True, expired=True)
    assert [job["job_id"] for job in combined["jobs"]] == ["job-on-expired"]


@pytest.mark.asyncio
async def test_admin_set_job_enabled(harness):
    await harness.seed_job("job-1", enabled_flag=0)

    enabled = await harness.service.admin_set_job_enabled(
        job_id="job-1", enabled_flag=1, user_id="admin-1", username="管理员",
    )
    assert enabled["enabled_flag"] == 1
    assert enabled["next_trigger_time"] is not None

    disabled = await harness.service.admin_set_job_enabled(
        job_id="job-1", enabled_flag=0, user_id="admin-1", username="管理员",
    )
    assert disabled["enabled_flag"] == 0
    assert disabled["next_trigger_time"] is None
    json.dumps(enabled)
    json.dumps(disabled)

    # 与用户侧停用同口径：未终态触发实例一并清退，防僵尸计入执行中统计
    async with harness.session_factory() as session:
        session.add(ChatDataAgentScheduleRun(
            job_id="job-1", user_id="user-1", conversation_id="conv-1",
            plan_time=datetime.now() + timedelta(minutes=5),
            trigger_status="pending", delete_flag=0, create_time=datetime.now(),
        ))
    await harness.service.admin_set_job_enabled(
        job_id="job-1", enabled_flag=0, user_id="admin-1", username="管理员",
    )
    async with harness.session_factory() as session:
        trigger = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    assert trigger.trigger_status == "cancelled"
    assert "停用" in trigger.error_message

    with pytest.raises(AgentScheduleNotFoundError):
        await harness.service.admin_set_job_enabled(
            job_id="missing", enabled_flag=1, user_id="admin-1", username="管理员",
        )


@pytest.mark.asyncio
async def test_admin_enable_once_job_without_plan_time_rejected(harness):
    await harness.seed_job(
        "job-once", enabled_flag=0, schedule_type="once", cron_expr="",
        plan_time=datetime.now() - timedelta(hours=1),
        next_trigger_time=None,
    )
    with pytest.raises(AgentScheduleValidationError):
        await harness.service.admin_set_job_enabled(
            job_id="job-once", enabled_flag=1, user_id="admin-1", username="管理员",
        )


@pytest.mark.asyncio
async def test_resume_fast_forwards_cron_cursor(harness):
    # 暂停期间游标不推进；恢复时快进到当前时刻的下一个触发点，
    # 暂停期间落进 misfire 窗口的触发点不补跑（否则暂停 1h 的 5 分钟任务恢复即补 12 次）
    now = datetime.now()
    await harness.seed_job("job-1", next_trigger_time=now - timedelta(minutes=30))

    await harness.service.set_paused(paused=True, reason="", user_id="admin-1", username="管理员")
    await harness.service.set_paused(paused=False, reason="", user_id="admin-1", username="管理员")

    async with harness.session_factory() as session:
        job = (await session.execute(
            select(ChatDataAgentSchedule).where(ChatDataAgentSchedule.job_id == "job-1")
        )).scalar_one()
    assert job.next_trigger_time is not None
    assert job.next_trigger_time > now


@pytest.mark.asyncio
async def test_admin_list_job_runs_without_ownership(harness):
    await harness.seed_job("job-1", user_id="user-1")
    await harness.seed_run("job-1", trigger_status="succeeded")
    result = await harness.service.admin_list_job_runs(job_id="job-1")
    runs = result["runs"]
    assert result["total"] == 1
    assert len(runs) == 1
    assert runs[0]["status"] == "succeeded"
    json.dumps(result)
    with pytest.raises(AgentScheduleNotFoundError):
        await harness.service.admin_list_job_runs(job_id="missing")
