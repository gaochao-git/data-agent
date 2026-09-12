"""智能体定时任务管理 service 的单测（SQLite 内存库）。"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.services.agent_schedule import schedule_service as service_module
from apps.chat_data_app.services.agent_schedule.schedule_service import (
    AgentScheduleConversationInvalidError,
    AgentScheduleLimitExceededError,
    AgentScheduleNotFoundError,
    AgentScheduleService,
    AgentScheduleValidationError,
)
from utils.models import Base, ChatDataAgentInbox, ChatDataAgentSchedule, ChatDataAgentScheduleRun, UserThread

FUTURE = datetime.now() + timedelta(hours=1)


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

    async def create_conversation(conversation_id="conv-1", user_id="user-1"):
        async with get_test_session() as session:
            session.add(UserThread(
                user_id=user_id,
                thread_id=conversation_id,
                agent_id="chat_data",
                delete_flag=0,
                create_time=datetime.now(),
                create_by=user_id,
                create_by_name=user_id,
                update_by=user_id,
                update_by_name=user_id,
            ))

    def create_payload(**overrides):
        payload = {
            "title": "每日销售报表",
            "prompt": "汇总昨日销售额",
            "conversation_id": "conv-1",
            "schedule_type": "cron",
            "cron_expr": "0 9 * * *",
            "plan_time": None,
            "effective_end_time": None,
            "datasource_id": "ds-1",
            "options": {"model_id": "m1"},
            "user_id": "user-1",
            "username": "用户一",
        }
        payload.update(overrides)
        return payload

    service = AgentScheduleService()
    yield service, create_conversation, create_payload, get_test_session
    asyncio.run(engine.dispose())


def _create_kwargs(payload):
    return {key: value for key, value in payload.items()}


@pytest.mark.asyncio
async def test_create_job_computes_next_trigger_and_merges_datasource(harness):
    service, create_conversation, create_payload, get_session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))
    json.dumps(job)
    assert job["schedule_type"] == "cron"
    assert job["next_trigger_time"] is not None
    assert job["datasource_id"] == "ds-1"

    async with get_session() as session:
        row = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
    assert row.cron_expr == "0 9 * * *"
    assert row.enabled_flag == 1
    assert row.next_trigger_time == datetime.fromisoformat(job["next_trigger_time"])


@pytest.mark.asyncio
async def test_create_once_job(harness):
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload(
        schedule_type="once", cron_expr=None, plan_time=FUTURE,
    )))
    assert job["schedule_type"] == "once"
    assert datetime.fromisoformat(job["next_trigger_time"]) == FUTURE


@pytest.mark.asyncio
async def test_create_job_validation_errors(harness):
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    with pytest.raises(AgentScheduleValidationError):
        await service.create_job(**_create_kwargs(create_payload(title="  ")))
    with pytest.raises(AgentScheduleValidationError):
        await service.create_job(**_create_kwargs(create_payload(cron_expr="bad expr")))
    with pytest.raises(AgentScheduleValidationError):
        await service.create_job(**_create_kwargs(create_payload(schedule_type="once", cron_expr=None)))
    with pytest.raises(AgentScheduleValidationError):
        await service.create_job(**_create_kwargs(create_payload(
            schedule_type="once", cron_expr=None, plan_time=datetime.now() - timedelta(minutes=1),
        )))
    with pytest.raises(AgentScheduleValidationError):
        await service.create_job(**_create_kwargs(create_payload(effective_end_time=datetime.now() - timedelta(days=1))))
    # 会话不存在 / 属于他人
    with pytest.raises(AgentScheduleConversationInvalidError):
        await service.create_job(**_create_kwargs(create_payload(conversation_id="missing")))
    with pytest.raises(AgentScheduleConversationInvalidError):
        await service.create_job(**_create_kwargs(create_payload(user_id="user-2")))
    # 运行选项不可序列化
    with pytest.raises(AgentScheduleValidationError):
        await service.create_job(**_create_kwargs(create_payload(options={"bad": {1, 2}})))


@pytest.mark.asyncio
async def test_create_job_respects_per_user_limit(monkeypatch, harness):
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    monkeypatch.setattr(service_module, "MAX_JOBS_PER_USER", 1)
    await service.create_job(**_create_kwargs(create_payload()))
    with pytest.raises(AgentScheduleLimitExceededError):
        await service.create_job(**_create_kwargs(create_payload(title="第二个")))


@pytest.mark.asyncio
async def test_update_job_rearms_next_trigger(harness):
    service, create_conversation, create_payload, get_session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload(cron_expr="0 9 * * *")))
    updated = await service.update_job(
        job_id=job["job_id"],
        title=None,
        prompt="改一下指令",
        schedule_type=None,
        cron_expr="30 10 * * *",
        plan_time=None,
        effective_end_time=None,
        enabled_flag=None,
        datasource_id=None,
        options=None,
        user_id="user-1",
        username="用户一",
    )
    assert updated["prompt"] == "改一下指令"
    json.dumps(updated)
    assert updated["cron_expr"] == "30 10 * * *"
    # 10:30 晚于当前时间，重新武装后应指向明天的 10:30 之前的下一个触发点
    assert updated["next_trigger_time"] is not None

    async with get_session() as session:
        row = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
    assert row.next_trigger_time == datetime.fromisoformat(updated["next_trigger_time"])


@pytest.mark.asyncio
async def test_update_job_toggle_enable_recomputes(harness):
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))
    disabled = await service.update_job(
        job_id=job["job_id"], title=None, prompt=None, schedule_type=None, cron_expr=None,
        plan_time=None, effective_end_time=None, enabled_flag=0, datasource_id=None,
        options=None, user_id="user-1", username="用户一",
    )
    assert disabled["enabled_flag"] == 0
    assert disabled["next_trigger_time"] is None
    enabled = await service.update_job(
        job_id=job["job_id"], title=None, prompt=None, schedule_type=None, cron_expr=None,
        plan_time=None, effective_end_time=None, enabled_flag=1, datasource_id=None,
        options=None, user_id="user-1", username="用户一",
    )
    assert enabled["enabled_flag"] == 1
    assert enabled["next_trigger_time"] is not None


@pytest.mark.asyncio
async def test_update_job_requires_owner(harness):
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))
    with pytest.raises(AgentScheduleNotFoundError):
        await service.update_job(
            job_id=job["job_id"], title=None, prompt=None, schedule_type=None, cron_expr=None,
            plan_time=None, effective_end_time=None, enabled_flag=0, datasource_id=None,
            options=None, user_id="user-other", username="别人",
        )


@pytest.mark.asyncio
async def test_delete_job_soft_deletes(harness):
    service, create_conversation, create_payload, get_session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))
    result = await service.delete_job(job_id=job["job_id"], user_id="user-1", username="用户一")
    assert result == {"job_id": job["job_id"]}
    assert await service.list_jobs(user_id="user-1") == []
    async with get_session() as session:
        row = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
    assert row.delete_flag == 1
    assert row.enabled_flag == 0
    with pytest.raises(AgentScheduleNotFoundError):
        await service.delete_job(job_id=job["job_id"], user_id="user-1", username="用户一")


@pytest.mark.asyncio
async def test_create_new_each_run_mode_without_conversation(harness):
    service, _create_conversation, create_payload, get_session = harness
    job = await service.create_job(**_create_kwargs(create_payload(
        conversation_id="", conversation_mode="new_each_run",
    )))
    assert job["conversation_mode"] == "new_each_run"
    assert job["conversation_id"] == ""

    # 指定会话模式缺会话仍需报错
    with pytest.raises(AgentScheduleValidationError):
        await service.create_job(**_create_kwargs(create_payload(conversation_id="")))


@pytest.mark.asyncio
async def test_update_switch_conversation_mode(harness):
    service, create_conversation, create_payload, get_session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))

    # fixed -> new_each_run：清空绑定会话
    switched = await service.update_job(
        job_id=job["job_id"], title=None, prompt=None, schedule_type=None, cron_expr=None,
        plan_time=None, effective_end_time=None, enabled_flag=None, datasource_id=None,
        options=None, user_id="user-1", username="用户一",
        conversation_mode="new_each_run",
    )
    assert switched["conversation_mode"] == "new_each_run"
    assert switched["conversation_id"] == ""

    # new_each_run -> fixed：不传会话应报错
    with pytest.raises(AgentScheduleValidationError):
        await service.update_job(
            job_id=job["job_id"], title=None, prompt=None, schedule_type=None, cron_expr=None,
            plan_time=None, effective_end_time=None, enabled_flag=None, datasource_id=None,
            options=None, user_id="user-1", username="用户一",
            conversation_mode="fixed",
        )

    # new_each_run -> fixed：指定存在的会话成功
    await create_conversation(conversation_id="conv-2")
    back = await service.update_job(
        job_id=job["job_id"], title=None, prompt=None, schedule_type=None, cron_expr=None,
        plan_time=None, effective_end_time=None, enabled_flag=None, datasource_id=None,
        options=None, user_id="user-1", username="用户一",
        conversation_mode="fixed", conversation_id="conv-2",
    )
    assert back["conversation_mode"] == "fixed"
    assert back["conversation_id"] == "conv-2"


@pytest.mark.asyncio
async def test_disable_job_cancels_pending_triggers(harness):
    service, create_conversation, create_payload, get_session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))
    now = datetime.now()

    async with get_session() as session:
        session.add(ChatDataAgentScheduleRun(
            job_id=job["job_id"], user_id="user-1", conversation_id="conv-1",
            plan_time=now + timedelta(minutes=5),
            trigger_status="pending", delete_flag=0, create_time=now,
        ))

    await service.update_job(
        job_id=job["job_id"], title=None, prompt=None, schedule_type=None, cron_expr=None,
        plan_time=None, effective_end_time=None, enabled_flag=0, datasource_id=None,
        options=None, user_id="user-1", username="用户一",
    )

    async with get_session() as session:
        trigger = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    # 停用后 pending 实例永不会被派发，必须清退成 cancelled 防僵尸
    assert trigger.trigger_status == "cancelled"
    assert "停用" in trigger.error_message


@pytest.mark.asyncio
async def test_list_job_runs_joins_agent_run_status(harness):
    service, create_conversation, create_payload, get_session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))
    now = datetime.now()

    async with get_session() as session:
        session.add(ChatDataAgentScheduleRun(
            job_id=job["job_id"],
            user_id="user-1",
            conversation_id="conv-1",
            plan_time=now - timedelta(minutes=5),
            trigger_status="dispatched",
            run_id="run-1",
            delete_flag=0,
            create_time=now,
        ))
        session.add(ChatDataAgentInbox(
            conversation_id="conv-1",
            user_id="user-1",
            run_id="run-1",
            message_id="run-1",
            record_type="run",
            content="{}",
            status="finished",
            delete_flag=0,
            create_time=now,
        ))

    runs = await service.list_job_runs(job_id=job["job_id"], user_id="user-1")
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-1"
    assert runs[0]["status"] == "dispatched"
    assert runs[0]["run_status"] == "finished"
    json.dumps(runs)
    with pytest.raises(AgentScheduleNotFoundError):
        await service.list_job_runs(job_id="missing", user_id="user-1")


@pytest.mark.asyncio
async def test_trigger_now_creates_manual_trigger(harness):
    service, create_conversation, create_payload, get_session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))

    created = await service.trigger_now(
        job_id=job["job_id"], user_id="user-1", username="用户一",
    )
    assert created["job_id"] == job["job_id"]
    json.dumps(created)

    async with get_session() as session:
        trigger = (await session.execute(select(ChatDataAgentScheduleRun))).scalar_one()
    assert trigger.trigger_type == "manual"
    assert trigger.trigger_status == "pending"
    assert trigger.create_by == "user-1"

    # 同一时刻重复触发被唯一键预检挡下
    async def same_now(_session):
        return datetime.fromisoformat(created["plan_time"])

    original_now = service._dao.now
    service._dao.now = same_now
    try:
        with pytest.raises(AgentScheduleValidationError):
            await service.trigger_now(job_id=job["job_id"], user_id="user-1", username="用户一")
    finally:
        service._dao.now = original_now


@pytest.mark.asyncio
async def test_trigger_now_requires_enabled_and_owner(harness):
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))
    with pytest.raises(AgentScheduleNotFoundError):
        await service.trigger_now(job_id=job["job_id"], user_id="user-other", username="别人")
    await service.update_job(
        job_id=job["job_id"], title=None, prompt=None, schedule_type=None, cron_expr=None,
        plan_time=None, effective_end_time=None, enabled_flag=0, datasource_id=None,
        options=None, user_id="user-1", username="用户一",
    )
    with pytest.raises(AgentScheduleValidationError):
        await service.trigger_now(job_id=job["job_id"], user_id="user-1", username="用户一")
