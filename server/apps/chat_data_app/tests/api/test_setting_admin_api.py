"""后台配置变量 Admin 接口契约测试（列表 / 覆盖白名单 / 重置单个与全部）。

复用 tests/api/conftest.py 的 app/client/auth_headers；DB 用内存 SQLite 覆盖
setting_service.transaction_scope，路由层走真实 service 与鉴权链路。
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.services import setting_service
from utils.models import Base

REGISTERED_KEY = "CHAT_DATA_MAX_UPLOAD_SIZE_MB"


@pytest.fixture()
def setting_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def test_transaction_scope():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(setting_service, "transaction_scope", test_transaction_scope)
    setting_service.clear_setting_cache()
    yield session_factory
    setting_service.clear_setting_cache()
    asyncio.run(engine.dispose())


def test_settings_full_crud_flow(client, auth_headers, setting_db):
    resp = client.get("/api/chatdata/admin/settings", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    keys = {item["setting_key"] for item in body["data"]["items"]}
    assert REGISTERED_KEY in keys

    # 覆盖注册 key（白名单内）
    resp = client.put(
        f"/api/chatdata/admin/settings/{REGISTERED_KEY}",
        headers=auth_headers,
        json={"setting_value": "20"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["action"] == "created"

    # 注册 key 非法值 → 写时校验拦截（CHATDATA_SETTING_INVALID）
    resp = client.put(
        f"/api/chatdata/admin/settings/{REGISTERED_KEY}",
        headers=auth_headers,
        json={"setting_value": "abc"},
    )
    assert resp.json()["code"] == 40061

    # 未注册 key 拒绝入表（白名单收口：手动新增已关死）
    for bad_key in ("APP_NOTICE_TEXT", "CHAT_DATA_API_KEY"):
        resp = client.put(
            f"/api/chatdata/admin/settings/{bad_key}",
            headers=auth_headers,
            json={"setting_value": "x"},
        )
        assert resp.json()["code"] == 40061
        assert "未注册" in resp.json()["msg"]

    # 重置 → 再重置报不存在 → 重新覆盖复用原行恢复
    resp = client.delete(f"/api/chatdata/admin/settings/{REGISTERED_KEY}", headers=auth_headers)
    assert resp.json()["data"]["setting_key"] == REGISTERED_KEY
    resp = client.delete(f"/api/chatdata/admin/settings/{REGISTERED_KEY}", headers=auth_headers)
    assert resp.json()["code"] == 40485
    resp = client.put(
        f"/api/chatdata/admin/settings/{REGISTERED_KEY}",
        headers=auth_headers,
        json={"setting_value": "20"},
    )
    assert resp.json()["data"]["action"] == "restored"

    # 列表可见恢复后的行（来源=表）
    resp = client.get("/api/chatdata/admin/settings?keyword=UPLOAD", headers=auth_headers)
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["source"] == "table"
    assert items[0]["setting_value"] == "20"


def test_settings_reset_all(client, auth_headers, setting_db):
    resp = client.put(
        f"/api/chatdata/admin/settings/{REGISTERED_KEY}",
        headers=auth_headers,
        json={"setting_value": "20"},
    )
    assert resp.json()["data"]["action"] == "created"

    resp = client.delete("/api/chatdata/admin/settings", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["reset_count"] == 1

    # 全部重置后列表回默认来源，且再重置为 0 个
    resp = client.get("/api/chatdata/admin/settings", headers=auth_headers)
    items = resp.json()["data"]["items"]
    assert all(item["source"] != "table" for item in items)
    assert all(item["setting_value"] is None for item in items)

    resp = client.delete("/api/chatdata/admin/settings", headers=auth_headers)
    assert resp.json()["data"]["reset_count"] == 0


def test_settings_write_requires_forwarded_user(client, auth_headers, setting_db):
    # GET 列表与其他 admin GET 一致不做用户头校验；写接口必须携带转发用户
    no_user = {k: v for k, v in auth_headers.items() if k != "X-User-Id"}
    resp = client.delete(f"/api/chatdata/admin/settings/{REGISTERED_KEY}", headers=no_user)
    assert resp.status_code == 401
    resp = client.delete("/api/chatdata/admin/settings", headers=no_user)
    assert resp.status_code == 401
