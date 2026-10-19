"""模型注册表 Admin 接口契约测试（列表/新建/更新/启停/默认/删除/探测/连接测试）。

复用 tests/api/conftest.py 的 app/client/auth_headers；DB 用内存 SQLite 覆盖
model_registry_dao.default_get_session，路由层走真实 service 与鉴权链路。
覆盖 200 / 400（校验）/ 40067（业务冲突）/ 404（不存在）/ 422（严格布尔）。
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.daos import model_registry_dao
from apps.chat_data_app.services import llm_config, model_registry_service
from utils.models import Base, ChatDataModelRegistry

CIPHER = "dGVzdC1pdi12YWx1ZQ==:dGVzdC1jaXBoZXJ0ZXN0"
PAYLOAD = {
    "model_id": "qwen-test",
    "provider": "阿里云",
    "display_name": "测试模型",
    "base_url": "http://127.0.0.1:8000/v1",
    "api_key": CIPHER,
    "params": {"context_window": 32000, "temperature": 0.3},
    "enabled": True,
    "remark": "备注",
}


@pytest.fixture()
def registry_db(monkeypatch):
    monkeypatch.setenv("ENCRYPT_KEY", "00" * 16)
    monkeypatch.delenv("CHAT_DATA_MODELS_FILE", raising=False)
    llm_config.reset_registry_cache()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[ChatDataModelRegistry.__table__])
            )

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def fake_get_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(model_registry_dao, "default_get_session", fake_get_session)
    yield session_factory
    llm_config.reset_registry_cache()
    asyncio.run(engine.dispose())


def _create(client, auth_headers, **overrides):
    body = {**PAYLOAD, **overrides}
    return client.post("/api/chatdata/admin/models", headers=auth_headers, json=body)


def test_models_full_crud_flow(client, auth_headers, registry_db):
    # 空列表
    resp = client.get("/api/chatdata/admin/models", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["models"] == []

    # 新建
    resp = _create(client, auth_headers)
    assert resp.status_code == 200
    created = resp.json()["data"]
    assert created["model_key"] == "qwen-test"  # 注册键自动生成=请求名
    assert created["api_key_cipher"] == CIPHER  # 密文回填
    assert created["has_api_key"] is True

    # 业务唯一键：同供应商+同请求名 → 40067
    resp = _create(client, auth_headers)
    assert resp.json()["code"] == 40067

    # 同请求名不同供应商：允许并存
    resp = _create(client, auth_headers, provider="本地vLLM", base_url="http://127.0.0.1:9/v1")
    assert resp.status_code == 200
    assert resp.json()["data"]["model_key"] == "qwen-test#2"

    # 更新（按注册键定位）
    resp = client.put(
        "/api/chatdata/admin/models/qwen-test",
        headers=auth_headers,
        json={**PAYLOAD, "display_name": "改名"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["display_name"] == "改名"

    # 更新不存在的注册键 → 40487
    resp = client.put(
        "/api/chatdata/admin/models/ghost", headers=auth_headers, json={**PAYLOAD, "model_id": "g"}
    )
    assert resp.json()["code"] == 40487

    # 更新撞其它活跃行的 (provider, model_id) → 40067
    resp = client.put(
        "/api/chatdata/admin/models/qwen-test",
        headers=auth_headers,
        json={**PAYLOAD, "model_id": "qwen-test", "provider": "本地vLLM"},
    )
    assert resp.json()["code"] == 40067

    # 校验失败（明文 key）→ 40066
    resp = _create(client, auth_headers, model_id="plain", provider="p", api_key="plain-key")
    assert resp.json()["code"] == 40066


def test_models_enabled_default_delete_flow(client, auth_headers, registry_db):
    _create(client, auth_headers, model_id="a", provider="p1")
    _create(client, auth_headers, model_id="b", provider="p2")

    # 启停
    resp = client.patch(
        "/api/chatdata/admin/models/a/enabled", headers=auth_headers, json={"enabled": False}
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"model_key": "a", "enabled": False}

    # 严格布尔：字符串 "false" 被 Pydantic 正确解析为 False（旧 bool() 会当真值），
    # 非布尔语义的垃圾值直接 422 拒绝
    resp = client.patch(
        "/api/chatdata/admin/models/a/enabled", headers=auth_headers, json={"enabled": "false"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"model_key": "a", "enabled": False}
    resp = client.patch(
        "/api/chatdata/admin/models/a/enabled", headers=auth_headers, json={"enabled": "abc"}
    )
    assert resp.status_code == 422

    # 启停不存在的模型 → 40487
    resp = client.patch(
        "/api/chatdata/admin/models/ghost/enabled", headers=auth_headers, json={"enabled": True}
    )
    assert resp.json()["code"] == 40487

    # 设默认
    resp = client.patch("/api/chatdata/admin/models/b/default", headers=auth_headers)
    assert resp.status_code == 200
    rows = {m["model_id"]: m for m in client.get("/api/chatdata/admin/models", headers=auth_headers).json()["data"]["models"]}
    assert rows["b"]["is_default"] is True
    assert rows["a"]["is_default"] is False

    resp = client.patch("/api/chatdata/admin/models/ghost/default", headers=auth_headers)
    assert resp.json()["code"] == 40487

    # 删除（软删）+ 再删 404
    resp = client.delete("/api/chatdata/admin/models/a", headers=auth_headers)
    assert resp.status_code == 200
    remaining = client.get("/api/chatdata/admin/models", headers=auth_headers).json()["data"]["models"]
    assert [m["model_id"] for m in remaining] == ["b"]
    resp = client.delete("/api/chatdata/admin/models/a", headers=auth_headers)
    assert resp.json()["code"] == 40487


def test_models_discover_endpoint(client, auth_headers, registry_db, monkeypatch):
    class _FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"id": "b-model"}, {"id": "a-model"}, {"id": "a-model"}]}

    class _FakeClient:
        def __init__(self, **kwargs):
            _ = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            _ = url, headers
            return _FakeResponse()

    monkeypatch.setattr(model_registry_service, "decrypt_password", lambda value: "plain-key")
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)

    resp = client.post(
        "/api/chatdata/admin/models/discover",
        headers=auth_headers,
        json={"base_url": "http://127.0.0.1:8000/v1", "api_key": CIPHER},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["models"] == ["a-model", "b-model"]

    # 校验失败（非法协议）→ 40066
    resp = client.post(
        "/api/chatdata/admin/models/discover",
        headers=auth_headers,
        json={"base_url": "ftp://x", "api_key": CIPHER},
    )
    assert resp.json()["code"] == 40066


def test_models_test_connection_endpoint(client, auth_headers, registry_db, monkeypatch):
    class _FakeLLM:
        async def ainvoke(self, prompt):
            _ = prompt
            return type("Resp", (), {"content": "ok"})()

    monkeypatch.setattr(model_registry_service, "get_llm", lambda **kwargs: _FakeLLM())
    monkeypatch.setattr(model_registry_service, "decrypt_password", lambda value: "plain-key")

    resp = client.post("/api/chatdata/admin/models/test-connection", headers=auth_headers, json=PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["success"] is True
    assert data["sample"] == "ok"

    # 校验失败 → 40066
    resp = client.post(
        "/api/chatdata/admin/models/test-connection",
        headers=auth_headers,
        json={**PAYLOAD, "api_key": ""},
    )
    assert resp.json()["code"] == 40066


def test_models_internal_error_paths_return_structured_codes(client, auth_headers, registry_db, monkeypatch):
    """service 抛非受控异常：8 条路由的兜底 except 全部返回结构化错误码（非裸 500）。"""
    from apps.chat_data_app.routers import model_admin

    boom = RuntimeError("db exploded")

    async def raise_boom(*args, **kwargs):
        raise boom

    cases = [
        ("get", "/api/chatdata/admin/models", None, "list_models", 50098),
        ("post", "/api/chatdata/admin/models", PAYLOAD, "create_model", 50099),
        ("put", "/api/chatdata/admin/models/k", PAYLOAD, "update_model", 50099),
        ("patch", "/api/chatdata/admin/models/k/enabled", {"enabled": True}, "set_model_enabled", 50100),
        ("patch", "/api/chatdata/admin/models/k/default", None, "set_model_default", 50100),
        ("delete", "/api/chatdata/admin/models/k", None, "delete_model", 50100),
        ("post", "/api/chatdata/admin/models/discover", {"base_url": "http://x", "api_key": CIPHER}, "discover_models", 50101),
        ("post", "/api/chatdata/admin/models/test-connection", PAYLOAD, "test_model_connection", 50101),
    ]
    for method, url, body, func, expected_code in cases:
        monkeypatch.setattr(model_admin, func, raise_boom)  # 路由模块 from-import 直引，须打桩该命名空间
        resp = client.request(method, url, headers=auth_headers, json=body)
        assert resp.status_code == 200, (method, url)
        assert resp.json()["code"] == expected_code, (method, url, resp.json())
