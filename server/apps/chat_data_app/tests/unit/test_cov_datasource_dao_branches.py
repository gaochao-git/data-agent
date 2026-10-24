"""datasource_dao 缺失分支：空结果兜底、更新字段与索引读取。"""

import json
from types import SimpleNamespace

import pytest

from apps.chat_data_app.daos.datasource_dao import DataSourceDao


class _Result:
    def __init__(self, first):
        self._first = first

    def scalars(self):
        return SimpleNamespace(first=lambda: self._first)


class _Session:
    def __init__(self, first=None):
        self._first = first
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self._first)


class _Scope:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_exc):
        return False


def _dao_with_session(monkeypatch, first=None):
    dao = DataSourceDao()
    session = _Session(first)
    monkeypatch.setattr(dao, "_session_scope", lambda: _Scope(session))
    return dao, session


@pytest.mark.asyncio
async def test_get_datasource_variants_return_none_for_missing_rows(monkeypatch):
    dao = DataSourceDao()

    async def _none(*_args):
        return None

    monkeypatch.setattr(dao, "_get_group_row_by_id_async", _none)
    monkeypatch.setattr(dao, "_get_group_row_by_name_async", _none)

    assert await dao.get_datasource_async("ds-1") is None
    assert await dao.get_datasource_meta_async("ds-1") is None
    assert await dao.get_datasource_by_group_name_async("组") is None
    assert await dao.get_datasource_meta_by_group_name_async("组") is None


@pytest.mark.asyncio
async def test_get_meta_by_group_name_maps_existing_row(monkeypatch):
    dao = DataSourceDao()

    async def _row(_name):
        return {"group_id": "g1", "group_name": "一组"}

    monkeypatch.setattr(dao, "_get_group_row_by_name_async", _row)
    meta = await dao.get_datasource_meta_by_group_name_async("一组")
    assert meta["group_id"] == "g1"


def test_normalize_recommended_questions_stops_at_limit():
    questions = [f"问题{i}" for i in range(55)]
    normalized = DataSourceDao()._normalize_recommended_questions(json.dumps(questions))
    assert len(normalized) == 50


@pytest.mark.asyncio
async def test_update_group_writes_optional_fields(monkeypatch):
    dao, session = _dao_with_session(monkeypatch)

    await dao.update_datasource_group_async(
        "g1",
        query_context="业务口径",
        recommended_questions=["问题1"],
        source_type="mysql",
    )

    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_read_table_indexes_requires_ids(monkeypatch):
    dao, _session = _dao_with_session(monkeypatch)

    assert await dao.get_datasource_table_indexes_async("", "t1") is None
    assert await dao.get_datasource_table_indexes_async("g1", "") is None


@pytest.mark.asyncio
async def test_update_table_indexes_returns_none_when_row_missing(monkeypatch):
    dao, session = _dao_with_session(monkeypatch, first=None)

    assert await dao.update_datasource_table_indexes_async("g1", "t1", []) is None
    assert len(session.statements) == 1
