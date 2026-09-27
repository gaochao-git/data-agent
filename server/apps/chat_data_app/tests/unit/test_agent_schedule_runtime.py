"""智能体定时任务调度 runtime 的单测（SQLite 内存库，全部注入假时钟）。"""

import asyncio
import types
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.agent.run import agent_inbox_service as inbox_module
from apps.chat_data_app.agent.run import agent_schedule_runtime as runtime_module
from apps.chat_data_app.agent.run.agent_inbox_service import (
    AgentInboxService,
    AgentRunConflictError,
    AgentRunNotActiveError,
)
from apps.chat_data_app.agent.run.agent_schedule_runtime import AgentScheduleRuntime
from apps.chat_data_app.daos.agent_schedule_dao import AgentScheduleDao
from utils.models import (
    Base,
    ChatDataAgentInbox,
    ChatDataAgentSchedule,
    ChatDataAgentScheduleControl,
    ChatDataAgentScheduleRun,
    UserThread,
)

USER_ID = "user-1"
CONVERSATION_ID = "conv-1"


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

    monkeypatch.setattr(runtime_module, "get_session", get_test_session)
    monkeypatch.setattr(inbox_module, "get_session", get_test_session)

    submitted_runs: list[str] = []

    def fake_submit(*, run_id, conversation_id, user_id, operator_name):
        submitted_runs.append(run_id)

    monkeypatch.setattr(runtime_module.agent_run_runtime, "submit", fake_submit)

    started: list[tuple[str, str, dict]] = []

    async def fake_start_run(conversation_id, user_id, operator_name, runtime_context, **kwargs):
        started.append((conversation_id, user_id, runtime_context))
        return f"run-{len(started)}", []

    inbox_service = AgentInboxService()
    inbox_service.start_run = fake_start_run

    def make_runtime(**overrides):
        params = {
            "enabled": True,
            "dispatch_batch_size": 10,
            "user_max_concurrency": 2,
            "misfire_window_seconds": 3600,
            "max_dispatch_attempts": 10,
            "dao": AgentScheduleDao(),
            "inbox_service": inbox_service,
            "node_id": "node-test",
        }
        params.update(overrides)
        runtime = AgentScheduleRuntime(**params)
        # 派发上下文构建依赖 handlers 装配链，测试中替换为轻量桩
        monkeypatch.setattr(runtime, "_build_runtime_context", lambda job: {"query": job["job_prompt"]})
        return runtime

    async def seed_job(
        *,
        job_id="job-1",
        schedule_type="cron",
        cron_expr="* * * * *",
        plan_time=None,
        next_trigger_time=None,
        enabled_flag=1,
        effective_end_time=None,
        user_id=USER_ID,
    ):
        async with get_test_session() as session:
            session.add(UserThread(
                user_id=user_id,
                thread_id=CONVERSATION_ID,
                agent_id="chat_data",
                delete_flag=0,
                create_time=datetime.now(),
                create_by=user_id,
                create_by_name=user_id,
                update_by=user_id,
                update_by_name=user_id,
            ))
            session.add(ChatDataAgentSchedule(
                job_id=job_id,
                user_id=user_id,
                conversation_id=CONVERSATION_ID,
                job_title="定时任务",
                job_prompt="汇总昨日数据",
                schedule_type=schedule_type,
                cron_expr=cron_expr or "",
                plan_time=plan_time,
                effective_end_time=effective_end_time,
                enabled_flag=enabled_flag,
                next_trigger_time=next_trigger_time,
                delete_flag=0,
                create_time=datetime.now(),
                create_by=user_id,
                create_by_name="用户一",
                update_by=user_id,
                update_by_name="用户一",
            ))

    yield types.SimpleNamespace(
        make_runtime=make_runtime,
        seed_job=seed_job,
        started=started,
        submitted=submitted_runs,
        session_factory=get_test_session,
    )
    asyncio.run(engine.dispose())


@pytest.mark.asyncio
async def test_tick_plans_cron_trigger_within_misfire_window(harness):
    await harness.seed_job(
        cron_expr="0 9 * * *",
        next_trigger_time=datetime(2026, 9, 11, 9, 0),
    )
    runtime = harness.make_runtime()
    now = datetime(2026, 9, 12, 9, 30)

    await runtime.tick(now=now)

    async with harness.session_factory() as session:
        triggers = list((await session.execute(
            select(ChatDataAgentScheduleRun).order_by(ChatDataAgentScheduleRun.plan_time)
        )).scalars())
        job = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
    # 昨天 09:00 在 misfire 窗口外被快进，只补今天 09:00 一个触发点
    assert [trigger.plan_time for trigger in triggers] == [datetime(2026, 9, 12, 9, 0)]
    assert all(trigger.trigger_status == "dispatched" for trigger in triggers)
    assert job.next_trigger_time == datetime(2026, 9, 13, 9, 0)
    assert job.last_trigger_time == datetime(2026, 9, 12, 9, 0)
    # 派发链路：以任务归属用户在其绑定会话内发起 Run
    assert harness.started == [(CONVERSATION_ID, USER_ID, {"query": "汇总昨日数据"})]
    assert len(harness.submitted) == 1


@pytest.mark.asyncio
async def test_tick_is_idempotent_for_already_planned_triggers(harness):
    await harness.seed_job(next_trigger_time=datetime(2026, 9, 12, 8, 0))
    runtime = harness.make_runtime()
    now = datetime(2026, 9, 12, 9, 0)

    await runtime.tick(now=now)
    await runtime.tick(now=now)

    async with harness.session_factory() as session:
        triggers = list((await session.execute(select(ChatDataAgentScheduleRun))).scalars())
    # 08:00-09:00 共 61 个分钟级触发点；第二轮 tick 游标已推进到未来，不会重复产生
    assert len(triggers) == 61


@pytest.mark.asyncio
async def test_once_job_fires_then_disables(harness):
    plan = datetime(2026, 9, 12, 8, 58)
    await harness.seed_job(schedule_type="once", cron_expr=None, plan_time=plan, next_trigger_time=plan)
    runtime = harness.make_runtime()
    now = datetime(2026, 9, 12, 9, 0)

    await runtime.tick(now=now)
    await runtime.tick(now=now)

    async with harness.session_factory() as session:
        job = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
        triggers = list((await session.execute(select(ChatDataAgentScheduleRun))).scalars())
    # next_trigger_time 置空即不再计划；enabled_flag 保持 1 以便 pending 触发可重试
    assert job.enabled_flag == 1
    assert job.next_trigger_time is None
    assert len(triggers) == 1
    assert triggers[0].plan_time == plan


@pytest.mark.asyncio
async def test_dispatch_conflict_releases_and_eventually_skips(harness):
    plan = datetime(2026, 9, 12, 8, 59)
    await harness.seed_job(schedule_type="once", cron_expr=None, plan_time=plan, next_trigger_time=plan)
    runtime = harness.make_runtime(max_dispatch_attempts=2)
    now = datetime(2026, 9, 12, 9, 0)

    async def conflict_start_run(*args, **kwargs):
        raise AgentRunConflictError("当前会话已有运行中的任务")

    runtime._inbox_service.start_run = conflict_start_run

    await runtime.tick(now=now)
    async with harness.session_factory() as session:
        trigger = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    # 第一次冲突释放回 pending，等待下轮重试
    assert trigger.trigger_status == "pending"
    assert trigger.attempt_count == 1

    await runtime.tick(now=now)
    async with harness.session_factory() as session:
        trigger = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    # 尝试次数耗尽改判 skipped，任务不被无限堆积
    assert trigger.trigger_status == "skipped"
    assert trigger.attempt_count == 2


@pytest.mark.asyncio
async def test_dispatch_missing_conversation_marks_failed(harness):
    plan = datetime(2026, 9, 12, 8, 59)
    await harness.seed_job(schedule_type="once", cron_expr=None, plan_time=plan, next_trigger_time=plan)
    runtime = harness.make_runtime()

    async def not_active_start_run(*args, **kwargs):
        raise AgentRunNotActiveError("会话不存在")

    runtime._inbox_service.start_run = not_active_start_run

    await runtime.tick(now=datetime(2026, 9, 12, 9, 0))
    async with harness.session_factory() as session:
        trigger = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    assert trigger.trigger_status == "failed"
    assert "会话不存在" in trigger.error_message


@pytest.mark.asyncio
async def test_dispatch_respects_user_concurrency_gate(harness):
    now = datetime(2026, 9, 12, 9, 0)
    await harness.seed_job(next_trigger_time=datetime(2026, 9, 12, 8, 59))
    runtime = harness.make_runtime(user_max_concurrency=1)

    # 预置一个在途触发，占满该用户的并发额度
    async with harness.session_factory() as session:
        session.add(ChatDataAgentScheduleRun(
            job_id="job-1",
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            plan_time=now - timedelta(minutes=10),
            trigger_status="dispatched",
            run_id="run-inflight",
            delete_flag=0,
            create_time=now,
        ))

    await runtime.tick(now=now)

    async with harness.session_factory() as session:
        triggers = list((await session.execute(
            select(ChatDataAgentScheduleRun).order_by(ChatDataAgentScheduleRun.id)
        )).scalars())
    # 计划阶段产生 08:59/09:00 两个新触发，均因额度占满被释放回 pending
    pending = [trigger for trigger in triggers if trigger.trigger_status == "pending"]
    assert len(pending) == 2
    assert all(trigger.attempt_count == 1 for trigger in pending)
    # 未派发新 Run，无新增提交
    assert len(harness.started) == 0


@pytest.mark.asyncio
async def test_finalize_closes_dispatched_trigger_with_run_terminal_state(harness):
    now = datetime(2026, 9, 12, 9, 0)
    # 任务未到期：tick 只跑 finalize，隔离验证状态收口逻辑
    await harness.seed_job(next_trigger_time=now + timedelta(minutes=10))
    runtime = harness.make_runtime()

    async with harness.session_factory() as session:
        session.add(ChatDataAgentScheduleRun(
            job_id="job-1",
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            plan_time=now - timedelta(minutes=1),
            trigger_status="dispatched",
            run_id="run-1",
            delete_flag=0,
            create_time=now,
        ))
        session.add(ChatDataAgentInbox(
            conversation_id=CONVERSATION_ID,
            user_id=USER_ID,
            run_id="run-1",
            message_id="run-1",
            record_type="run",
            record_content="{}",
            record_status="finished",
            delete_flag=0,
            create_time=now,
        ))

    await runtime.tick(now=now)

    async with harness.session_factory() as session:
        trigger = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    assert trigger.trigger_status == "succeeded"
    assert trigger.finished_time is not None


@pytest.mark.asyncio
async def test_finalize_marks_failed_run(harness):
    now = datetime(2026, 9, 12, 9, 0)
    await harness.seed_job(next_trigger_time=now + timedelta(minutes=10))
    runtime = harness.make_runtime()

    async with harness.session_factory() as session:
        session.add(ChatDataAgentScheduleRun(
            job_id="job-1",
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            plan_time=now - timedelta(minutes=1),
            trigger_status="dispatched",
            run_id="run-1",
            delete_flag=0,
            create_time=now,
        ))
        session.add(ChatDataAgentInbox(
            conversation_id=CONVERSATION_ID,
            user_id=USER_ID,
            run_id="run-1",
            message_id="run-1",
            record_type="run",
            record_content="{}",
            record_status="failed",
            delete_flag=0,
            create_time=now,
        ))

    await runtime.tick(now=now)

    async with harness.session_factory() as session:
        trigger = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    assert trigger.trigger_status == "failed"


@pytest.mark.asyncio
async def test_paused_control_freezes_plan_and_dispatch(harness):
    await harness.seed_job(next_trigger_time=datetime(2026, 9, 12, 8, 59))
    runtime = harness.make_runtime()
    now = datetime(2026, 9, 12, 9, 0)

    # 预置全局暂停；在途 dispatched 触发仍应被收口
    async with harness.session_factory() as session:
        session.add(ChatDataAgentScheduleControl(
            id=1, paused_flag=1, delete_flag=0,
        ))
        session.add(ChatDataAgentScheduleRun(
            job_id="job-1",
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            plan_time=now - timedelta(minutes=5),
            trigger_status="dispatched",
            run_id="run-1",
            delete_flag=0,
            create_time=now,
        ))
        session.add(ChatDataAgentInbox(
            conversation_id=CONVERSATION_ID,
            user_id=USER_ID,
            run_id="run-1",
            message_id="run-1",
            record_type="run",
            record_content="{}",
            record_status="finished",
            delete_flag=0,
            create_time=now,
        ))

    await runtime.tick(now=now)

    async with harness.session_factory() as session:
        triggers = list((await session.execute(select(ChatDataAgentScheduleRun))).scalars())
        job = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
    # 暂停期间不产生新触发实例、不派发，但在途触发照常收口
    assert [trigger.trigger_status for trigger in triggers] == ["succeeded"]
    assert job.next_trigger_time == datetime(2026, 9, 12, 8, 59)
    assert len(harness.started) == 0


@pytest.mark.asyncio
async def test_dispatch_fixed_mode_uses_job_current_conversation(harness):
    """模式切换防假失败：fixed 派发以任务当前会话为准，不信任触发实例快照。

    场景：new_each_run 期间计划出的 pending 实例 conversation_id 为空串，
    用户切回 fixed 后实例派发若用快照会 start_run 报「会话不存在」成批假失败。
    """
    plan = datetime(2026, 9, 12, 8, 59)
    await harness.seed_job(schedule_type="once", cron_expr=None, plan_time=plan, next_trigger_time=plan)

    async with harness.session_factory() as session:
        # 触发实例携带过期空串（new_each_run 时期计划出的）
        session.add(ChatDataAgentScheduleRun(
            job_id="job-1", user_id=USER_ID, conversation_id="",
            plan_time=plan, trigger_status="pending", delete_flag=0, create_time=plan,
        ))

    runtime = harness.make_runtime()
    await runtime.tick(now=datetime(2026, 9, 12, 9, 0))

    async with harness.session_factory() as session:
        trigger = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    # 任务当前是 fixed 且绑定 conv-1：派发用任务会话，而非快照空串
    assert trigger.trigger_status == "dispatched"
    assert harness.started[0][0] == CONVERSATION_ID


@pytest.mark.asyncio
async def test_dispatch_new_each_run_creates_fresh_conversation(harness):
    plan = datetime(2026, 9, 12, 8, 59)
    await harness.seed_job(schedule_type="once", cron_expr=None, plan_time=plan, next_trigger_time=plan)
    runtime = harness.make_runtime()

    # 任务切到 new_each_run 模式
    async with harness.session_factory() as session:
        job = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
        job.conversation_mode = "new_each_run"
        job.conversation_id = ""

    created_threads = []
    original_create = runtime_module.bound_conversation_dao.create_thread

    async def fake_create_thread(**kwargs):
        created_threads.append(kwargs)

    runtime_module.bound_conversation_dao.create_thread = fake_create_thread
    try:
        await runtime.tick(now=datetime(2026, 9, 12, 9, 0))
    finally:
        runtime_module.bound_conversation_dao.create_thread = original_create

    # 单次任务触发一次，即只新建一个会话，标题带任务名
    assert len(created_threads) == 1
    assert all(item["title"] == "【定时】定时任务" for item in created_threads)
    assert all(item["user_id"] == USER_ID for item in created_threads)
    # Run 在新建会话里执行，触发实例回填实际会话 ID
    assert harness.started[0][0] == created_threads[0]["thread_id"]
    assert harness.started[0][0] != CONVERSATION_ID
    async with harness.session_factory() as session:
        rows = list((await session.execute(select(ChatDataAgentScheduleRun))).scalars())
    assert {row.conversation_id for row in rows} == {item["thread_id"] for item in created_threads}


@pytest.mark.asyncio
async def test_plan_long_downtime_fast_forwards_without_unbounded_loop(harness):
    # 每分钟任务停机一年（misfire 窗口 1h）：旧实现逐点空转 52 万次且持行锁；
    # 新实现迭代 500 次后直接跳变到窗口起点的下一个触发点，窗口内触发点由
    # 后续 tick 从新游标继续补齐
    await harness.seed_job(cron_expr="* * * * *", next_trigger_time=datetime(2025, 9, 12, 9, 0))
    runtime = harness.make_runtime()
    now = datetime(2026, 9, 12, 9, 0)

    await runtime.tick(now=now)

    async with harness.session_factory() as session:
        job = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
        triggers = list((await session.execute(select(ChatDataAgentScheduleRun))).scalars())
    # 本轮不补跑（游标还在窗口外），快进到 08:01 等下一轮继续
    assert triggers == []
    assert job.next_trigger_time == datetime(2026, 9, 12, 8, 1)

    await runtime.tick(now=now)
    async with harness.session_factory() as session:
        triggers = list((await session.execute(select(ChatDataAgentScheduleRun))).scalars())
    # 第二轮把窗口内 08:01..09:00 共 60 个触发点补齐
    assert len(triggers) == 60


@pytest.mark.asyncio
async def test_stale_claim_reaped_back_to_pending(harness):
    now = datetime(2026, 9, 12, 9, 0)
    await harness.seed_job(next_trigger_time=now + timedelta(minutes=10))
    runtime = harness.make_runtime(claim_timeout_seconds=300)

    async with harness.session_factory() as session:
        # 悬挂 claimed（节点抢占后崩溃）+ 一条 attempt 耗尽的悬挂
        session.add(ChatDataAgentScheduleRun(
            job_id="job-1", user_id=USER_ID, conversation_id=CONVERSATION_ID,
            plan_time=now - timedelta(minutes=10),
            trigger_status="claimed", claimed_time=now - timedelta(minutes=10),
            attempt_count=1, delete_flag=0, create_time=now,
        ))
        stale_id = 2
        session.add(ChatDataAgentScheduleRun(
            id=stale_id, job_id="job-1", user_id=USER_ID, conversation_id=CONVERSATION_ID,
            plan_time=now - timedelta(minutes=9),
            trigger_status="claimed", claimed_time=now - timedelta(minutes=9),
            attempt_count=10, delete_flag=0, create_time=now,
        ))

    await runtime.tick(now=now)

    async with harness.session_factory() as session:
        rows = list((await session.execute(
            select(ChatDataAgentScheduleRun).order_by(ChatDataAgentScheduleRun.id)
        )).scalars())
    # 悬挂未耗尽：释放回 pending 后同一轮 tick 的派发阶段会立即重试
    # （plan_time 已到期）→ dispatched，attempt 累加为 2；已耗尽：直接 skipped
    assert rows[0].trigger_status == "dispatched"
    assert rows[0].attempt_count == 2
    assert rows[0].run_id != ""
    assert rows[1].trigger_status == "skipped"
    assert "重新排队" in rows[1].error_message


@pytest.mark.asyncio
async def test_fresh_claim_not_reaped(harness):
    now = datetime(2026, 9, 12, 9, 0)
    await harness.seed_job(next_trigger_time=now + timedelta(minutes=10))
    runtime = harness.make_runtime(claim_timeout_seconds=300)

    async with harness.session_factory() as session:
        session.add(ChatDataAgentScheduleRun(
            job_id="job-1", user_id=USER_ID, conversation_id=CONVERSATION_ID,
            plan_time=now - timedelta(seconds=5),
            trigger_status="claimed", claimed_time=now - timedelta(seconds=5),
            attempt_count=1, delete_flag=0, create_time=now,
        ))

    await runtime.tick(now=now)

    async with harness.session_factory() as session:
        row = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    assert row.trigger_status == "claimed"


@pytest.mark.asyncio
async def test_dispatch_trigger_now_runs_immediately(harness):
    plan = datetime(2026, 9, 12, 8, 59)
    await harness.seed_job(schedule_type="once", cron_expr=None, plan_time=plan, next_trigger_time=plan)
    runtime = harness.make_runtime()

    async with harness.session_factory() as session:
        trigger = ChatDataAgentScheduleRun(
            job_id="job-1",
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            plan_time=datetime.now(),
            trigger_status="pending",
            trigger_type="manual",
            delete_flag=0,
            create_time=datetime.now(),
        )
        session.add(trigger)
        await session.flush()
        trigger_id = trigger.id

    result = await runtime.dispatch_trigger(trigger_id)
    assert result["status"] == "dispatched"
    assert result["run_id"] == "run-1"
    assert len(harness.started) == 1

    async with harness.session_factory() as session:
        row = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    assert row.trigger_status == "dispatched"
    assert row.run_id == "run-1"

    # 非 pending 状态不可重复抢占
    result = await runtime.dispatch_trigger(trigger_id)
    assert result["status"] == "pending"
    assert len(harness.started) == 1


@pytest.mark.asyncio
async def test_disabled_runtime_start_is_noop():
    runtime = AgentScheduleRuntime(enabled=False)
    await runtime.start()
    assert runtime._poll_task is None
    await runtime.stop()


@pytest.mark.asyncio
async def test_fixed_mode_dispatch_prefers_job_conversation_over_stale_snapshot(harness):
    # 实例在 new_each_run 模式下排队（会话快照为空），任务随后切回 fixed 并绑定
    # conv-1：派发必须以任务当前会话为准，否则 start_run 报「会话不存在」成批假失败
    now = datetime(2026, 9, 12, 9, 0)
    await harness.seed_job(next_trigger_time=now + timedelta(minutes=10))
    runtime = harness.make_runtime()

    async with harness.session_factory() as session:
        session.add(ChatDataAgentScheduleRun(
            job_id="job-1", user_id=USER_ID, conversation_id="",
            plan_time=now - timedelta(minutes=1),
            trigger_status="pending", delete_flag=0, create_time=now,
        ))

    await runtime.tick(now=now)

    assert harness.started[0][0] == CONVERSATION_ID
    async with harness.session_factory() as session:
        row = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    assert row.trigger_status == "dispatched"
    assert row.conversation_id == CONVERSATION_ID


@pytest.mark.asyncio
async def test_new_each_run_binds_job_datasource_to_fresh_conversation(harness):
    # 每次新建会话模式下新会话没有数据源绑定，agent 查询拿不到执行对象：
    # 派发时必须按任务选项把数据源写进新会话
    now = datetime(2026, 9, 12, 9, 0)
    await harness.seed_job(next_trigger_time=now + timedelta(minutes=10))
    runtime = harness.make_runtime()

    async with harness.session_factory() as session:
        job = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
        job.conversation_mode = "new_each_run"
        job.conversation_id = ""
        job.options_json = '{"datasource_id": "ds-1"}'
        session.add(ChatDataAgentScheduleRun(
            job_id="job-1", user_id=USER_ID, conversation_id="",
            plan_time=now - timedelta(minutes=1),
            trigger_status="pending", delete_flag=0, create_time=now,
        ))

    created: list[dict] = []
    bound: list[dict] = []
    original_create = runtime_module.bound_conversation_dao.create_thread
    original_bind = runtime_module.bound_conversation_dao.resolve_datasource_binding

    async def fake_create_thread(**kwargs):
        created.append(kwargs)

    async def fake_bind(**kwargs):
        bound.append(kwargs)

    runtime_module.bound_conversation_dao.create_thread = fake_create_thread
    runtime_module.bound_conversation_dao.resolve_datasource_binding = fake_bind
    try:
        await runtime.tick(now=now)
    finally:
        runtime_module.bound_conversation_dao.create_thread = original_create
        runtime_module.bound_conversation_dao.resolve_datasource_binding = original_bind

    assert len(created) == 1
    new_conversation_id = created[0]["thread_id"]
    assert new_conversation_id != CONVERSATION_ID
    assert bound == [{
        "conversation_id": new_conversation_id,
        "user_id": USER_ID,
        "requested_datasource_id": "ds-1",
    }]
    assert harness.started[0][0] == new_conversation_id
    async with harness.session_factory() as session:
        row = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    assert row.conversation_id == new_conversation_id


@pytest.mark.asyncio
async def test_conversation_provision_failure_releases_trigger_to_pending(harness):
    # 建会话失败必须走统一的释放回 pending：异常若冒到派发循环，实例会悬挂在
    # claimed 直到超时回收才重试
    now = datetime(2026, 9, 12, 9, 0)
    await harness.seed_job(next_trigger_time=now + timedelta(minutes=10))
    runtime = harness.make_runtime()

    async with harness.session_factory() as session:
        job = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
        job.conversation_mode = "new_each_run"
        job.conversation_id = ""
        session.add(ChatDataAgentScheduleRun(
            job_id="job-1", user_id=USER_ID, conversation_id="",
            plan_time=now - timedelta(minutes=1),
            trigger_status="pending", delete_flag=0, create_time=now,
        ))

    original_create = runtime_module.bound_conversation_dao.create_thread

    async def failing_create_thread(**kwargs):
        raise RuntimeError("建会话失败")

    runtime_module.bound_conversation_dao.create_thread = failing_create_thread
    try:
        await runtime.tick(now=now)
    finally:
        runtime_module.bound_conversation_dao.create_thread = original_create

    assert harness.started == []
    async with harness.session_factory() as session:
        row = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    assert row.trigger_status == "pending"
    assert row.attempt_count == 1
    assert row.run_id == ""


# ---- 分支覆盖补测：启停摆动、派发兜底、终态收口竞态 ----
# 说明：_MAX_PLAN_STEPS 快进分支里的 ONCE 子分支（runtime.py:219-220）不可达——
# ONCE 的 compute_next_trigger 在 after==cursor 时必然返回 None，循环首轮即退出，
# 步数永远到不了上限，属纵深防御代码，不造用例硬凑。

async def _add_trigger(session_factory, *, plan_time, job_id="job-1", trigger_status="pending", **overrides):
    """插入一条触发实例并返回自增 ID（默认字段与 _build_trigger_row 同口径）。"""
    created = datetime(2026, 9, 12, 8, 0)
    async with session_factory() as session:
        row = ChatDataAgentScheduleRun(
            job_id=job_id,
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            plan_time=plan_time,
            trigger_status=trigger_status,
            trigger_type="schedule",
            delete_flag=0,
            create_time=created,
            update_time=created,
            **overrides,
        )
        session.add(row)
        await session.flush()
        return row.id


def test_job_options_degrades_on_broken_json():
    """options_json 损坏或非对象一律降级为空对象，不抛给调度循环。"""
    assert runtime_module.job_options("{不是 JSON") == {}
    assert runtime_module.job_options(None) == {}
    assert runtime_module.job_options('["列表不是对象"]') == {}
    assert runtime_module.job_options('{"model_id": "m1"}') == {"model_id": "m1"}


def test_build_runtime_context_reuses_completion_options_parsing():
    """派发上下文复用 chat 完成链路的 options 解析口径（数据源/模型/skill 归一化）。"""
    runtime = AgentScheduleRuntime(
        enabled=True,
        dao=AgentScheduleDao(),
        inbox_service=AgentInboxService(),
        node_id="node-test",
    )
    context = runtime._build_runtime_context({
        "job_prompt": "汇总昨日数据",
        "options_json": '{"model_id": "m1", "datasource_id": "ds-1", "skill_names": "officecli"}',
        "create_by_name": "用户一",
    })
    assert context["query"] == "汇总昨日数据"
    assert context["model_id"] == "m1"
    assert context["datasource_id"] == "ds-1"
    # 单字符串形态的 skill_names 也要归一化成列表
    assert context["skill_names"] == ["officecli"]
    assert context["operator_name"] == "用户一"


@pytest.mark.asyncio
async def test_start_and_stop_manage_poll_task(harness, monkeypatch):
    """启停：enabled 时建轮询任务、重复 start 不重复建、stop 取消并清空引用。"""
    real_sleep = asyncio.sleep

    async def fast_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr(runtime_module.asyncio, "sleep", fast_sleep)
    runtime = harness.make_runtime()

    assert runtime._poll_task is None
    await runtime.start()
    task = runtime._poll_task
    assert task is not None

    # 已在运行：第二次 start 不重复建任务
    await runtime.start()
    assert runtime._poll_task is task

    await runtime.stop()
    assert runtime._poll_task is None
    assert task.done()


@pytest.mark.asyncio
async def test_poll_loop_logs_tick_failure_and_keeps_running(harness, monkeypatch):
    """轮询循环：单轮 tick 抛异常只记日志并短暂退避，循环本身不退出。"""
    real_sleep = asyncio.sleep
    ticked = asyncio.Event()
    calls = {"count": 0}

    async def fast_sleep(_delay):
        await real_sleep(0)

    async def flaky_tick(now=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("tick boom")
        ticked.set()

    monkeypatch.setattr(runtime_module.asyncio, "sleep", fast_sleep)
    runtime = harness.make_runtime()
    monkeypatch.setattr(runtime, "tick", flaky_tick)

    await runtime.start()
    try:
        await asyncio.wait_for(ticked.wait(), timeout=5)
    finally:
        await runtime.stop()
    assert calls["count"] >= 2
    assert runtime._poll_task is None


@pytest.mark.asyncio
async def test_plan_keeps_existing_trigger_untouched(harness):
    """计划推进跳过已存在的触发实例：不重复插入、不改动既有行。"""
    plan = datetime(2026, 9, 12, 8, 59)
    await harness.seed_job(next_trigger_time=plan)
    kept_id = await _add_trigger(
        harness.session_factory, plan_time=plan, trigger_status="skipped", error_message="保留我",
    )
    runtime = harness.make_runtime()

    await runtime.tick(now=datetime(2026, 9, 12, 9, 0))

    async with harness.session_factory() as session:
        rows = list((await session.execute(
            select(ChatDataAgentScheduleRun).order_by(ChatDataAgentScheduleRun.plan_time)
        )).scalars())
    assert [row.plan_time for row in rows] == [plan, datetime(2026, 9, 12, 9, 0)]
    kept = next(row for row in rows if row.id == kept_id)
    assert kept.trigger_status == "skipped"
    assert kept.error_message == "保留我"


@pytest.mark.asyncio
async def test_dispatch_skips_trigger_when_job_soft_deleted(harness):
    """任务在实例排队期间被删除：立即触发路径把实例判 skipped，而不是崩在派发循环。"""
    await harness.seed_job(next_trigger_time=None)
    trigger_id = await _add_trigger(harness.session_factory, plan_time=datetime(2026, 9, 12, 8, 59))
    async with harness.session_factory() as session:
        job = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
        job.delete_flag = 1
        job.enabled_flag = 0
    runtime = harness.make_runtime()

    result = await runtime.dispatch_trigger(trigger_id)

    assert result["status"] == "skipped"
    async with harness.session_factory() as session:
        row = (await session.execute(
            select(ChatDataAgentScheduleRun).where(ChatDataAgentScheduleRun.id == trigger_id)
        )).scalar_one()
    assert row.trigger_status == "skipped"
    assert "已删除或停用" in row.error_message
    assert harness.started == []


@pytest.mark.asyncio
async def test_mark_dispatched_ignores_trigger_not_claimed(harness):
    """竞态兜底：实例已被取消/收口（非 claimed）时，派发回填必须整条跳过。"""
    trigger_id = await _add_trigger(harness.session_factory, plan_time=datetime(2026, 9, 12, 9, 0))
    runtime = harness.make_runtime()

    await runtime._mark_dispatched(trigger_id, "run-x")

    async with harness.session_factory() as session:
        row = (await session.execute(
            select(ChatDataAgentScheduleRun).where(ChatDataAgentScheduleRun.id == trigger_id)
        )).scalar_one()
    assert row.trigger_status == "pending"
    assert row.run_id == ""
    assert harness.submitted == []


@pytest.mark.asyncio
async def test_mark_dispatched_backfills_actual_conversation(harness):
    """new_each_run：派发成功后在实例上回填本次实际执行的会话，供历史追溯。"""
    trigger_id = await _add_trigger(
        harness.session_factory, plan_time=datetime(2026, 9, 12, 9, 0), trigger_status="claimed",
    )
    runtime = harness.make_runtime()

    await runtime._mark_dispatched(trigger_id, "run-x", "conv-new")

    async with harness.session_factory() as session:
        row = (await session.execute(
            select(ChatDataAgentScheduleRun).where(ChatDataAgentScheduleRun.id == trigger_id)
        )).scalar_one()
    assert row.trigger_status == "dispatched"
    assert row.run_id == "run-x"
    assert row.conversation_id == "conv-new"
    assert row.dispatched_time is not None
    assert harness.submitted == ["run-x"]


@pytest.mark.asyncio
async def test_release_ignores_trigger_not_claimed(harness):
    """竞态兜底：非 claimed 实例不再迁移状态，避免覆盖别人刚写入的终态。"""
    trigger_id = await _add_trigger(harness.session_factory, plan_time=datetime(2026, 9, 12, 9, 0))
    runtime = harness.make_runtime()

    await runtime._release(trigger_id, "pending", "")

    async with harness.session_factory() as session:
        row = (await session.execute(
            select(ChatDataAgentScheduleRun).where(ChatDataAgentScheduleRun.id == trigger_id)
        )).scalar_one()
    assert row.trigger_status == "pending"
    assert row.error_message == ""
    assert row.finished_time is None


@pytest.mark.asyncio
async def test_finalize_skips_trigger_flipped_before_update(harness):
    """终态收口竞态：扫描到 dispatched 快照后实例已被改判，更新前复查必须拦住。"""
    trigger_id = await _add_trigger(
        harness.session_factory, plan_time=datetime(2026, 9, 12, 9, 0),
        trigger_status="cancelled", run_id="run-1",
    )
    runtime = harness.make_runtime()

    async def stale_dispatched(_session, _limit):
        # 模拟「扫描快照落后于磁盘状态」：快照说 dispatched，实际行已被取消
        return [types.SimpleNamespace(id=trigger_id, run_id="run-1")]

    async def fake_run_statuses(_session, _run_ids):
        return {"run-1": "finished"}

    runtime._dao.list_dispatched_runs = stale_dispatched
    runtime._dao.list_run_statuses = fake_run_statuses

    await runtime._finalize_dispatched()

    async with harness.session_factory() as session:
        row = (await session.execute(
            select(ChatDataAgentScheduleRun).where(ChatDataAgentScheduleRun.id == trigger_id)
        )).scalar_one()
    assert row.trigger_status == "cancelled"
    assert row.finished_time is None


@pytest.mark.asyncio
async def test_mark_dispatched_keeps_snapshot_conversation_without_override(harness):
    """fixed 模式：不传实际会话（None）时保留触发实例快照里的会话，不被清空。"""
    trigger_id = await _add_trigger(
        harness.session_factory, plan_time=datetime(2026, 9, 12, 9, 0), trigger_status="claimed",
    )
    runtime = harness.make_runtime()

    await runtime._mark_dispatched(trigger_id, "run-y")

    async with harness.session_factory() as session:
        row = (await session.execute(
            select(ChatDataAgentScheduleRun).where(ChatDataAgentScheduleRun.id == trigger_id)
        )).scalar_one()
    assert row.trigger_status == "dispatched"
    assert row.run_id == "run-y"
    assert row.conversation_id == CONVERSATION_ID


@pytest.mark.asyncio
async def test_schedule_settings_resolve_hot_values_from_env(monkeypatch):
    """构造参数缺省时三项调度参数走运行时热值（表>env>默认；单测桩下降级到 env）。"""
    monkeypatch.setenv("CHAT_DATA_SCHEDULE_POLL_SECONDS", "7")
    monkeypatch.setenv("CHAT_DATA_SCHEDULE_DISPATCH_BATCH_SIZE", "3")
    monkeypatch.setenv("CHAT_DATA_SCHEDULE_USER_MAX_CONCURRENCY", "4")
    runtime = AgentScheduleRuntime(
        enabled=True,
        dao=AgentScheduleDao(),
        inbox_service=AgentInboxService(),
        node_id="node-test",
    )
    assert await runtime._resolve_poll_seconds() == 7
    assert await runtime._resolve_dispatch_batch_size() == 3
    assert await runtime._resolve_user_max_concurrency() == 4


@pytest.mark.asyncio
async def test_schedule_settings_explicit_injection_overrides_hot_values():
    """测试注入的构造参数固定取值，不经运行时热值解析。"""
    runtime = AgentScheduleRuntime(
        enabled=True,
        poll_seconds=99,
        dispatch_batch_size=11,
        user_max_concurrency=5,
        dao=AgentScheduleDao(),
        inbox_service=AgentInboxService(),
        node_id="node-test",
    )
    assert await runtime._resolve_poll_seconds() == 99
    assert await runtime._resolve_dispatch_batch_size() == 11
    assert await runtime._resolve_user_max_concurrency() == 5
