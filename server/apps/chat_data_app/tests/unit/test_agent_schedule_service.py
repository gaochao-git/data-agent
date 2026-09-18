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
    data = await service.list_jobs(user_id="user-1")
    assert data == {"jobs": [], "total": 0}
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

    result = await service.list_job_runs(job_id=job["job_id"], user_id="user-1")
    runs = result["runs"]
    assert result["total"] == 1
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-1"
    assert runs[0]["status"] == "dispatched"
    assert runs[0]["run_status"] == "finished"
    json.dumps(result)
    with pytest.raises(AgentScheduleNotFoundError):
        await service.list_job_runs(job_id="missing", user_id="user-1")


@pytest.mark.asyncio
async def test_list_job_runs_pagination(harness):
    """执行历史按 limit/offset 分页，total 为去软删后的全量计数。"""
    service, create_conversation, create_payload, get_session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))
    now = datetime.now()
    async with get_session() as session:
        for i in range(3):
            session.add(ChatDataAgentScheduleRun(
                job_id=job["job_id"],
                user_id="user-1",
                conversation_id="conv-1",
                plan_time=now - timedelta(minutes=i + 1),
                trigger_status="succeeded",
                delete_flag=0,
                create_time=now,
            ))
        # 软删行不计入 total 也不出现在任何页
        session.add(ChatDataAgentScheduleRun(
            job_id=job["job_id"],
            user_id="user-1",
            conversation_id="conv-1",
            plan_time=now,
            trigger_status="cancelled",
            delete_flag=1,
            create_time=now,
        ))

    page1 = await service.list_job_runs(job_id=job["job_id"], user_id="user-1", limit=2, offset=0)
    page2 = await service.list_job_runs(job_id=job["job_id"], user_id="user-1", limit=2, offset=2)
    assert page1["total"] == 3
    assert len(page1["runs"]) == 2
    # 按计划时间倒序：第一页是最近的两次执行
    assert page1["runs"][0]["plan_time"] > page1["runs"][1]["plan_time"]
    assert page2["total"] == 3
    assert len(page2["runs"]) == 1


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


@pytest.mark.asyncio
async def test_update_job_treats_options_as_patch(harness):
    """编辑提交的 options 是补丁：未提交的键必须原样保留，null 表示清除该键。"""
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload(
        options={"model_id": "m1", "semantic_config_id": "sc-1", "skill_names": ["a"]},
    )))

    updated = await service.update_job(
        job_id=job["job_id"], title="改标题", prompt=None, schedule_type=None, cron_expr=None,
        plan_time=None, effective_end_time=None, enabled_flag=None, datasource_id=None,
        options={"model_id": "m2", "datasource_id": None},
        user_id="user-1", username="用户一",
    )

    # 表单掌管的键以补丁为准；未被表单掌管的键不能被顺手清空
    assert updated["options"]["model_id"] == "m2"
    assert updated["options"]["semantic_config_id"] == "sc-1"
    assert updated["options"]["skill_names"] == ["a"]
    # 补丁里的 null 清除该键（回退系统默认）
    assert "datasource_id" not in updated["options"]
    assert updated["datasource_id"] is None


@pytest.mark.asyncio
async def test_create_job_drops_null_options(harness):
    """创建路径没有既有配置，null 视为未设置：不应在 options_json 里留下空键。"""
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload(
        datasource_id=None,
        options={"model_id": None, "enable_thinking": False},
    )))
    assert job["options"] == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_update_expired_once_job_allows_title_edit(harness):
    """执行时间已过期的单次任务仍可改标题：调度未变就不重新校验。"""
    service, create_conversation, create_payload, get_session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload(
        schedule_type="once", cron_expr=None, plan_time=FUTURE,
    )))
    expired = datetime.now() - timedelta(hours=2)
    async with get_session() as session:
        row = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
        row.plan_time = expired
        row.next_trigger_time = None

    updated = await service.update_job(
        job_id=job["job_id"], title="改个标题", prompt=None, schedule_type=None, cron_expr=None,
        plan_time=None, effective_end_time=None, enabled_flag=None, datasource_id=None,
        options=None, user_id="user-1", username="用户一",
    )
    assert updated["title"] == "改个标题"
    assert datetime.fromisoformat(updated["plan_time"]) == expired
    assert updated["next_trigger_time"] is None


@pytest.mark.asyncio
async def test_update_job_clears_effective_end_time(harness):
    """显式清空有效期：effective_end_time 字段真的出现在请求里且为 null。"""
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload(
        effective_end_time=FUTURE + timedelta(days=1),
    )))
    assert job["effective_end_time"] is not None

    cleared = await service.update_job(
        job_id=job["job_id"], title=None, prompt=None, schedule_type=None, cron_expr=None,
        plan_time=None, effective_end_time=None, clear_effective_end_time=True,
        enabled_flag=None, datasource_id=None, options=None,
        user_id="user-1", username="用户一",
    )
    assert cleared["effective_end_time"] is None
    assert cleared["next_trigger_time"] is not None

    async with _session() as session:
        row = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
    assert row.effective_end_time is None


# ---- 分支覆盖补测：校验失败出口、列表标题、补丁边界 ----


def test_validate_options_normalizes_and_rejects_non_dict():
    """options 序列化口径：None 直接透传、null 值剔除、非对象拒绝。"""
    assert service_module._validate_options(None) is None
    assert json.loads(service_module._validate_options({"model_id": "m1", "datasource_id": None})) == {
        "model_id": "m1"
    }
    with pytest.raises(AgentScheduleValidationError):
        service_module._validate_options("不是对象")


@pytest.mark.asyncio
async def test_create_job_rejects_invalid_schedule_type_and_conversation_mode(harness):
    """调度类型与会话绑定方式的白名单校验都要给出可展示的错误。"""
    service, create_conversation, create_payload, _session = harness
    await create_conversation()

    with pytest.raises(AgentScheduleValidationError):
        await service.create_job(**_create_kwargs(create_payload(conversation_mode="bound")))
    with pytest.raises(AgentScheduleValidationError):
        await service.create_job(**_create_kwargs(create_payload(schedule_type="daily")))


@pytest.mark.asyncio
async def test_create_job_rejects_cron_without_fire_before_effective_end(harness):
    """有效期落在下一次命中之前：没有可触发时间点，创建即被拦下。"""
    service, create_conversation, create_payload, _session = harness
    await create_conversation()

    # 「每年 1 月 1 日」的下一次命中远在半年后，而有效期只到几分钟后
    with pytest.raises(AgentScheduleValidationError, match="没有可触发的时间点"):
        await service.create_job(**_create_kwargs(create_payload(
            cron_expr="0 0 1 1 *",
            effective_end_time=datetime.now() + timedelta(minutes=5),
        )))


@pytest.mark.asyncio
async def test_create_once_job_rejects_plan_after_effective_end(harness):
    """单次任务：执行时间晚于有效期结束时间必须被拒。"""
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    plan = datetime.now() + timedelta(hours=3)

    with pytest.raises(AgentScheduleValidationError, match="不能晚于有效期结束时间"):
        await service.create_job(**_create_kwargs(create_payload(
            schedule_type="once",
            cron_expr=None,
            plan_time=plan,
            effective_end_time=plan - timedelta(hours=1),
        )))


@pytest.mark.asyncio
async def test_list_jobs_attaches_live_conversation_titles(harness):
    """任务列表附上绑定会话的实时标题，供前端直接展示。"""
    service, create_conversation, create_payload, get_session = harness
    async with get_session() as session:
        session.add(UserThread(
            user_id="user-1",
            thread_id="conv-1",
            thread_title="昨日销售分析",
            agent_id="chat_data",
            delete_flag=0,
            create_time=datetime.now(),
            create_by="user-1",
            create_by_name="用户一",
            update_by="user-1",
            update_by_name="用户一",
        ))
    await service.create_job(**_create_kwargs(create_payload()))

    data = await service.list_jobs(user_id="user-1")

    assert data["total"] == 1
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["conversation_title"] == "昨日销售分析"


@pytest.mark.asyncio
async def test_list_jobs_pagination(harness):
    """任务列表按 limit/offset 分页，total 为去软删后的全量计数。"""
    service, create_conversation, create_payload, get_session = harness
    await create_conversation()
    created = [
        await service.create_job(**_create_kwargs(create_payload(title=f"任务{i}")))
        for i in range(3)
    ]
    # 软删一个：不应计入 total，也不出现在任何页
    await service.delete_job(job_id=created[0]["job_id"], user_id="user-1", username="用户一")

    page1 = await service.list_jobs(user_id="user-1", limit=1, offset=0)
    page2 = await service.list_jobs(user_id="user-1", limit=1, offset=1)
    assert page1["total"] == 2
    assert [job["title"] for job in page1["jobs"]] == ["任务2"]
    assert [job["title"] for job in page2["jobs"]] == ["任务1"]

    offset_beyond = await service.list_jobs(user_id="user-1", limit=10, offset=2)
    assert offset_beyond["total"] == 2
    assert offset_beyond["jobs"] == []


@pytest.mark.asyncio
async def test_update_job_rejects_invalid_mode_and_schedule_type(harness):
    """更新路径同样对会话绑定方式与调度类型做白名单校验。"""
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))

    with pytest.raises(AgentScheduleValidationError):
        await service.update_job(
            job_id=job["job_id"], title=None, prompt=None, conversation_mode="bound",
            cron_expr=None, plan_time=None, effective_end_time=None, enabled_flag=None,
            datasource_id=None, options=None, user_id="user-1", username="用户一",
        )
    with pytest.raises(AgentScheduleValidationError):
        await service.update_job(
            job_id=job["job_id"], title=None, prompt=None, schedule_type="daily",
            cron_expr=None, plan_time=None, effective_end_time=None, enabled_flag=None,
            datasource_id=None, options=None, user_id="user-1", username="用户一",
        )


@pytest.mark.asyncio
async def test_update_job_rejects_switching_to_foreign_conversation(harness):
    """切到不属于当前用户（或不存在）的会话必须被拒，避免越权绑定。"""
    service, create_conversation, create_payload, get_session = harness
    await create_conversation()
    async with get_session() as session:
        session.add(UserThread(
            user_id="user-2",
            thread_id="conv-of-user-2",
            agent_id="chat_data",
            delete_flag=0,
            create_time=datetime.now(),
            create_by="user-2",
            create_by_name="用户二",
            update_by="user-2",
            update_by_name="用户二",
        ))
    job = await service.create_job(**_create_kwargs(create_payload()))

    with pytest.raises(AgentScheduleConversationInvalidError):
        await service.update_job(
            job_id=job["job_id"], title=None, prompt=None, conversation_id="conv-of-user-2",
            cron_expr=None, plan_time=None, effective_end_time=None, enabled_flag=None,
            datasource_id=None, options=None, user_id="user-1", username="用户一",
        )


@pytest.mark.asyncio
async def test_update_job_accepts_datasource_id_without_options_patch(harness):
    """只传 datasource_id 时也要写进 options_json（表单数据源组字段）。"""
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))

    updated = await service.update_job(
        job_id=job["job_id"], title=None, prompt=None, cron_expr=None, plan_time=None,
        effective_end_time=None, enabled_flag=None, datasource_id="ds-2", options=None,
        user_id="user-1", username="用户一",
    )

    assert updated["datasource_id"] == "ds-2"
    # 表单未掌管的键仍保留（补丁语义）
    assert updated["options"]["model_id"] == "m1"


@pytest.mark.asyncio
async def test_update_job_degrades_on_corrupt_options_json(harness):
    """既有 options_json 损坏时按空配置继续，不因脏数据阻断编辑。"""
    service, create_conversation, create_payload, get_session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))
    async with get_session() as session:
        row = (await session.execute(select(ChatDataAgentSchedule))).scalar_one()
        row.options_json = "{坏掉的 JSON"

    updated = await service.update_job(
        job_id=job["job_id"], title=None, prompt=None, cron_expr=None, plan_time=None,
        effective_end_time=None, enabled_flag=None, datasource_id=None,
        options={"model_id": "m2"}, user_id="user-1", username="用户一",
    )

    assert updated["options"] == {"model_id": "m2"}


@pytest.mark.asyncio
async def test_update_job_reenable_keeps_recomputed_cursor(harness):
    """停用任务在「改调度 + 同时启用」时，游标用新调度的重算结果，不被重新武装覆盖。"""
    service, create_conversation, create_payload, _session = harness
    await create_conversation()
    job = await service.create_job(**_create_kwargs(create_payload()))

    await service.update_job(
        job_id=job["job_id"], title=None, prompt=None, cron_expr=None, plan_time=None,
        effective_end_time=None, enabled_flag=0, datasource_id=None, options=None,
        user_id="user-1", username="用户一",
    )
    reenabled = await service.update_job(
        job_id=job["job_id"], title=None, prompt=None, cron_expr="0 10 * * *", plan_time=None,
        effective_end_time=None, enabled_flag=1, datasource_id=None, options=None,
        user_id="user-1", username="用户一",
    )

    assert reenabled["enabled_flag"] == 1
    assert datetime.fromisoformat(reenabled["next_trigger_time"]).hour == 10
