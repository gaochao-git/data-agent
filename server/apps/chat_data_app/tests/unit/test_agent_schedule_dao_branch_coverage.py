"""定时任务 DAO 空入参短路分支的单测。

这两个方法在「无非空 id 列表」时直接返回空字典，**早于任何 DB 访问**，
因此用 ``session=None`` 断言短路行为本身：一旦有人把提前返回挪到查询之后，
用例会立刻抛 AttributeError 转红，起到哨兵作用。
"""

import pytest

from apps.chat_data_app.daos.agent_schedule_dao import AgentScheduleDao

pytestmark = pytest.mark.asyncio


async def test_count_dispatched_by_users_returns_empty_without_user_ids():
    """无用户 ID 时不查库（个人并发闸门对空集合直接放行）。"""
    assert await AgentScheduleDao().count_dispatched_by_users(None, []) == {}


async def test_latest_runs_by_jobs_returns_empty_without_job_ids():
    """无任务 ID 时不查库（后台列表对空集合直接返回）。"""
    assert await AgentScheduleDao().latest_runs_by_jobs(None, []) == {}
