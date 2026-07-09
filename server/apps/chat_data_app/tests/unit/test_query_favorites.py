from contextlib import asynccontextmanager
from datetime import datetime
import asyncio
import json
from types import SimpleNamespace

from starlette.requests import Request

from apps.chat_data_app.routers.query_favorites import (
    create_query_favorite,
    delete_query_favorite,
    get_query_favorites,
    update_query_favorite,
)
from apps.chat_data_app.schema import QueryFavoriteCreateRequest, QueryFavoriteUpdateRequest
from apps.chat_data_app.services import query_providers


class _FakeExecuteResult:
    def __init__(self, item=None, items=None):
        self._item = item
        self._items = items or []

    def scalar_one_or_none(self):
        return self._item

    def scalars(self):
        return self

    def all(self):
        return self._items


class _FakeFavorite:
    def __init__(self):
        self.id = 1
        self.datasource_id = "ds1"
        self.user_id = "u1"
        self.semantic_config_id = None
        self.query_type = "sql"
        self.favorite_title = "t1"
        self.query_text = "select 1"
        self.builder_config = None
        self.delete_flag = 0
        self.create_time = datetime.now()
        self.update_time = datetime.now()

    def to_dict(self):
        return {
            "id": self.id,
            "datasource_id": self.datasource_id,
            "user_id": self.user_id,
            "query_type": self.query_type,
            "favorite_title": self.favorite_title,
            "query_text": self.query_text,
        }


class _FakeDB:
    def __init__(self, item=None, items=None):
        self.item = item
        self.items = items or []
        self.last_stmt = None
        self.added_item = None

    async def execute(self, stmt):
        self.last_stmt = stmt
        if self.item is not None:
            return _FakeExecuteResult(item=self.item)
        return _FakeExecuteResult(items=self.items)

    def add(self, item):
        self.added_item = item

    async def flush(self):
        return None


def _make_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _payload(resp):
    return json.loads(resp.body.decode("utf-8"))


def _fake_current_user(user_id: str = "u-login"):
    return SimpleNamespace(user_id=user_id, username=user_id)


def test_create_query_favorite_uses_current_user_id(monkeypatch):
    from apps.chat_data_app.routers import query_favorites as query_favorites_routes

    fake_db = _FakeDB()

    @asynccontextmanager
    async def fake_session():
        yield fake_db

    monkeypatch.setattr(query_favorites_routes, "_get_current_user", lambda _req: _fake_current_user())
    monkeypatch.setattr(query_providers, "get_session", fake_session)

    payload = QueryFavoriteCreateRequest(
        datasource_id="ds1",
        query_type="sql",
        favorite_title="标题",
        query_text="select 1",
    )
    resp = asyncio.run(create_query_favorite(payload, _make_request()))
    assert _payload(resp)["status"] == "success"
    assert fake_db.added_item is not None
    assert fake_db.added_item.user_id == "u-login"


def test_get_query_favorites_filters_by_current_user(monkeypatch):
    from apps.chat_data_app.routers import query_favorites as query_favorites_routes

    fake_db = _FakeDB(items=[_FakeFavorite()])

    @asynccontextmanager
    async def fake_session():
        yield fake_db

    monkeypatch.setattr(query_favorites_routes, "_get_current_user", lambda _req: _fake_current_user())
    monkeypatch.setattr(query_providers, "get_session", fake_session)

    resp = asyncio.run(get_query_favorites(_make_request(), datasource_id="ds1", query_type="sql", limit=10))
    assert _payload(resp)["status"] == "success"
    sql_text = str(fake_db.last_stmt)
    assert "sql_query_favorite.user_id" in sql_text


def test_update_delete_query_favorite_filter_by_current_user(monkeypatch):
    from apps.chat_data_app.routers import query_favorites as query_favorites_routes

    item = _FakeFavorite()
    fake_db = _FakeDB(item=item)

    @asynccontextmanager
    async def fake_session():
        yield fake_db

    monkeypatch.setattr(query_favorites_routes, "_get_current_user", lambda _req: _fake_current_user())
    monkeypatch.setattr(query_providers, "get_session", fake_session)

    update_payload = QueryFavoriteUpdateRequest(favorite_title="new-title")
    update_resp = asyncio.run(update_query_favorite(1, update_payload, _make_request()))
    assert _payload(update_resp)["status"] == "success"
    assert item.favorite_title == "new-title"
    assert "sql_query_favorite.user_id" in str(fake_db.last_stmt)

    delete_resp = asyncio.run(delete_query_favorite(1, _make_request()))
    assert _payload(delete_resp)["status"] == "success"
    assert item.delete_flag == 1
    assert "sql_query_favorite.user_id" in str(fake_db.last_stmt)
