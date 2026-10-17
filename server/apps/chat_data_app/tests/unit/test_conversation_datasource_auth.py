"""会话绑定数据源组的授权门禁（2026-10-17 补）。

语义模型快照与本体目录会物化进用户工作区（元数据暴露面），绑定入口必须复用
数据源机构授权口径：建会话与每轮 completion 均 fail-close。
"""

import json
from types import SimpleNamespace

import pytest

from apps.chat_data_app.routers import conversations as conversation_router
from utils.error_codes import ErrorCode


def payload(response):
    return json.loads(response.body.decode("utf-8"))


class FakeRequest:
    def __init__(self, path="/api/chatdata/conversations"):
        self.url = SimpleNamespace(path=path)
        self.headers = {}
        self.is_disconnected = False


class FakeDatasourceManager:
    def __init__(self, allowed):
        self.allowed = allowed
        self.calls = []

    async def can_access_datasource_async(self, datasource_id, user_id=None, username=None):
        self.calls.append((datasource_id, user_id, username))
        return self.allowed


@pytest.fixture()
def router_state(monkeypatch):
    original_configs = dict(conversation_router._agent_configs)
    user = SimpleNamespace(user_id="u1", username="用户一")
    monkeypatch.setattr(conversation_router, "get_forwarded_user", lambda _request: user)
    conversation_router._agent_configs.clear()
    conversation_router._agent_configs.update(
        {conversation_router.AGENT_ID: {"get_graph": lambda _checkpointer: None, "get_checkpointer": lambda: None}}
    )
    yield user
    conversation_router._agent_configs.clear()
    conversation_router._agent_configs.update(original_configs)


@pytest.mark.asyncio
async def test_create_conversation_rejects_unauthorized_datasource(router_state, monkeypatch):
    manager = FakeDatasourceManager(allowed=False)
    monkeypatch.setattr(conversation_router, "get_datasource_manager", lambda: manager)
    created = []

    async def fake_create(*args, **kwargs):
        created.append(args)
        return {"id": "conv_1"}

    monkeypatch.setattr(conversation_router.conversation, "create", fake_create)

    response = await conversation_router.create_conversation(
        conversation_router.CreateConversationRequest(agent_id=conversation_router.AGENT_ID, datasource_id="ds_x"),
        FakeRequest(),
    )
    body = payload(response)
    assert body["code"] == ErrorCode.CHATDATA_PERMISSION_DENIED
    assert body["status"] == "error"
    assert created == []
    assert manager.calls == [("ds_x", "u1", "用户一")]


@pytest.mark.asyncio
async def test_create_conversation_allows_authorized_datasource(router_state, monkeypatch):
    manager = FakeDatasourceManager(allowed=True)
    monkeypatch.setattr(conversation_router, "get_datasource_manager", lambda: manager)
    created = []

    async def fake_create(agent_id, user_id, datasource_id=None):
        created.append((agent_id, user_id, datasource_id))
        return {"id": "conv_1"}

    async def fake_snapshot(*_args, **_kwargs):
        return None

    async def fake_materialize(*_args, **_kwargs):
        return {"failed_ids": []}

    monkeypatch.setattr(conversation_router.conversation, "create", fake_create)
    monkeypatch.setattr(conversation_router, "build_runtime_skill_snapshot_async", fake_snapshot)
    monkeypatch.setattr(conversation_router, "materialize_conversation_semantic_models", fake_materialize)

    response = await conversation_router.create_conversation(
        conversation_router.CreateConversationRequest(agent_id=conversation_router.AGENT_ID, datasource_id="ds_x"),
        FakeRequest(),
    )
    assert payload(response)["data"]["id"] == "conv_1"
    assert created == [(conversation_router.AGENT_ID, "u1", "ds_x")]


@pytest.mark.asyncio
async def test_empty_datasource_skips_authorization(monkeypatch):
    manager = FakeDatasourceManager(allowed=False)
    monkeypatch.setattr(conversation_router, "get_datasource_manager", lambda: manager)

    assert await conversation_router._ensure_datasource_group_access("", "u1", "用户一") is None
    assert manager.calls == []


@pytest.mark.asyncio
async def test_completion_binding_checks_bound_datasource(monkeypatch):
    manager = FakeDatasourceManager(allowed=False)
    monkeypatch.setattr(conversation_router, "get_datasource_manager", lambda: manager)

    async def fake_get_bound(conversation_id, user_id):
        return "ds_bound"

    monkeypatch.setattr(conversation_router.conversation, "get_datasource_id", fake_get_bound)
    options = {"datasource_id": ""}
    response = await conversation_router._apply_completion_datasource_binding("conv_1", "u1", "用户一", options)
    assert response is not None
    assert payload(response)["code"] == ErrorCode.CHATDATA_PERMISSION_DENIED
    assert options["datasource_id"] == ""


@pytest.mark.asyncio
async def test_completion_binding_fills_authorized_datasource(monkeypatch):
    manager = FakeDatasourceManager(allowed=True)
    monkeypatch.setattr(conversation_router, "get_datasource_manager", lambda: manager)

    async def fake_get_bound(conversation_id, user_id):
        return "ds_bound"

    monkeypatch.setattr(conversation_router.conversation, "get_datasource_id", fake_get_bound)
    options = {}
    assert await conversation_router._apply_completion_datasource_binding("conv_1", "u1", "用户一", options) is None
    assert options["datasource_id"] == "ds_bound"
