"""conversation_dao.py 剩余分支：运行状态聚合、条件拼装与分组首屏。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from apps.chat_data_app.daos.conversation_dao import ConversationDao


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


def _dao_with_rows(rows):
    @asynccontextmanager
    async def context():
        yield _Session(rows)

    return ConversationDao(session_context=context)


class _Session:
    def __init__(self, result):
        self._result = result

    async def execute(self, *_args, **_kwargs):
        return self._result


def test_list_active_run_statuses_returns_empty_for_missing_conversation_ids():
    """空会话列表直接返回空字典，不发起查询。"""
    dao = ConversationDao(session_context=lambda: None)

    assert asyncio.run(dao.list_active_run_statuses([], "u1")) == {}


def test_list_active_run_statuses_keeps_highest_priority_status():
    """同一会话多条运行记录时保留优先级更高的状态。"""
    rows = [
        ("conv_a", "pending"),
        ("conv_a", "active"),
        ("conv_a", "waiting_user"),
        ("conv_b", "active"),
        ("conv_b", "pending"),
        ("conv_c", "active"),
        ("conv_c", "active"),
    ]
    dao = _dao_with_rows(_RowsResult(rows))

    statuses = asyncio.run(dao.list_active_run_statuses(["conv_a", "conv_b", "conv_c"], "u1"))

    assert statuses == {"conv_a": "waiting_user", "conv_b": "active", "conv_c": "active"}


def test_list_active_run_statuses_without_rows_returns_empty_dict():
    """查询没有命中记录时返回空字典。"""
    dao = _dao_with_rows(_RowsResult([]))

    assert asyncio.run(dao.list_active_run_statuses(["conv_a"], None)) == {}


def test_list_conditions_omits_user_filter_when_user_absent():
    """user_id 为空时只按 agent 与删除标记过滤。"""
    conditions = ConversationDao._list_conditions("agent_a", None)

    assert len(conditions) == 2
    assert len(ConversationDao._list_conditions("agent_a", "u1")) == 3


def test_list_thread_group_first_pages_skips_unclassified_without_blank_datasource():
    """分组清单没有空数据源时不追加 unclassified 分组。"""

    class RecordingDao(ConversationDao):
        def __init__(self):
            super().__init__(session_context=lambda: None)
            self.groups: list[tuple[str, str | None]] = []

        async def list_threads_in_group(self, *, group_type, datasource_id=None, **_kwargs):
            self.groups.append((group_type, datasource_id))
            return []

    @asynccontextmanager
    async def context_factory():
        yield _Session(_ScalarsResult(["ds_a"]))

    dao = RecordingDao()
    dao._session_context = context_factory

    rows = asyncio.run(dao.list_thread_group_first_pages(agent_id="agent_a", user_id="u1", limit_per_group=2))

    assert rows == []
    assert ("unclassified", None) not in dao.groups
    assert dao.groups == [("pinned", None), ("datasource", "ds_a")]


def test_list_thread_group_first_pages_includes_unclassified_for_blank_datasource():
    """存在空数据源分组时追加 unclassified 分组。"""

    class RecordingDao(ConversationDao):
        def __init__(self):
            super().__init__(session_context=lambda: None)
            self.groups: list[tuple[str, str | None]] = []

        async def list_threads_in_group(self, *, group_type, datasource_id=None, **_kwargs):
            self.groups.append((group_type, datasource_id))
            return []

    @asynccontextmanager
    async def context_factory():
        yield _Session(_ScalarsResult([""]))

    dao = RecordingDao()
    dao._session_context = context_factory

    asyncio.run(dao.list_thread_group_first_pages(agent_id="agent_a", user_id=None, limit_per_group=2))

    assert dao.groups == [("pinned", None), ("unclassified", None)]
