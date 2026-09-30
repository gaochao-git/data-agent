import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.chat_data_app.routers import conversations as conversation_router
from auth import AuthenticationError
from utils.error_codes import ErrorCode


def payload(response):
    return json.loads(response.body.decode("utf-8"))


async def _async_value(value):
    return value


class FakeRequest:
    def __init__(self, path="/api/chatdata/conversations"):
        self.url = SimpleNamespace(path=path)


@pytest.fixture()
def router_state(monkeypatch):
    original_configs = dict(conversation_router._agent_configs)
    original_completion = dict(conversation_router._completion_handlers)

    user = SimpleNamespace(user_id="u1", username="用户一")
    monkeypatch.setattr(conversation_router, "get_forwarded_user", lambda _request: user)
    conversation_router._agent_configs.clear()
    conversation_router._completion_handlers.clear()
    conversation_router._agent_configs.update(
        {
            "agent_a": {
                "get_graph": lambda _checkpointer: None,
                "get_checkpointer": lambda: None,
            },
            "agent_nograph": {
                "get_graph": None,
                "get_checkpointer": None,
            },
        }
    )

    yield user

    conversation_router._agent_configs.clear()
    conversation_router._agent_configs.update(original_configs)
    conversation_router._completion_handlers.clear()
    conversation_router._completion_handlers.update(original_completion)


def test_registration_and_auth_helpers(router_state, monkeypatch):
    conversation_router.register_completion_handler("agent_b", lambda **_kwargs: None)
    assert "agent_b" in conversation_router._completion_handlers
    assert conversation_router.get_agent_config("agent_a")["get_graph"] is not None

    monkeypatch.setattr(
        conversation_router, "get_forwarded_user", lambda _request: SimpleNamespace(username="name-only")
    )
    assert conversation_router._get_user_id(FakeRequest()) == "name-only"
    assert conversation_router._get_current_user_identity(FakeRequest()) == ("name-only", "name-only")

    def raise_auth(_request):
        raise AuthenticationError("bad token", status_code=401)

    monkeypatch.setattr(conversation_router, "get_forwarded_user", raise_auth)
    with pytest.raises(AuthenticationError):
        conversation_router._get_current_user(FakeRequest("/private"))


@pytest.mark.asyncio
async def test_get_agent_config_by_conversation_branches(router_state, monkeypatch):
    original_configs = dict(conversation_router._agent_configs)
    conversation_router._agent_configs.clear()
    assert await conversation_router.get_agent_config_by_conversation("conv") is None
    conversation_router._agent_configs.update(original_configs)

    async def get_no_agent(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_no_agent)
    assert await conversation_router.get_agent_config_by_conversation("conv") is None

    async def get_agent(*_args, **_kwargs):
        return "agent_a"

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_agent)
    assert (await conversation_router.get_agent_config_by_conversation("conv"))["get_graph"] is not None


@pytest.mark.asyncio
async def test_conversation_crud_routes_success_and_errors(router_state, monkeypatch):
    request = FakeRequest()

    async def fake_create(agent_id, user_id, datasource_id=None):
        return {"id": "conv_new", "agent_id": agent_id, "user_id": user_id}

    monkeypatch.setattr(conversation_router.conversation, "create", fake_create)
    created = payload(
        await conversation_router.create_conversation(
            conversation_router.CreateConversationRequest(agent_id="agent_a"), request
        )
    )
    assert created["data"]["id"] == "conv_new"
    assert (
        payload(
            await conversation_router.create_conversation(
                conversation_router.CreateConversationRequest(agent_id="missing"), request
            )
        )["code"]
        == ErrorCode.CHATDATA_UNKNOWN_AGENT
    )

    async def fake_list(agent_id, user_id, limit, offset):
        assert agent_id == "agent_a"
        assert user_id == "u1"
        assert offset == 1
        return [{"id": "c1"}, {"id": "c2"}]

    async def fake_list_by_agent_ids(agent_ids, user_id, limit, offset):
        assert agent_ids in (["agent_a", "agent_nograph"], ["agent_nograph"])
        if agent_ids == ["agent_nograph"]:
            return [{"id": "c2"}]
        return [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]

    async def fake_list_all(configs, user_id, page_size):
        return {"conversations": [{"id": "all"}], "page_size": page_size, "agent_count": len(configs)}

    monkeypatch.setattr(conversation_router.conversation, "list_conversations", fake_list)
    monkeypatch.setattr(conversation_router.conversation, "list_by_agent_ids", fake_list_by_agent_ids)
    monkeypatch.setattr(conversation_router.conversation, "list_all", fake_list_all)
    listed_one = payload(
        await conversation_router.list_conversations(request, agent_id="agent_a", agent_ids=None, page=2, page_size=1)
    )
    assert listed_one["data"]["has_more"] is True
    listed_many = payload(
        await conversation_router.list_conversations(
            request, agent_id=None, agent_ids="agent_a, agent_nograph", page=1, page_size=2
        )
    )
    assert listed_many["data"]["has_more"] is True
    listed_all = payload(
        await conversation_router.list_conversations(request, agent_id=None, agent_ids=None, page=1, page_size=20)
    )
    assert listed_all["data"]["agent_count"] == 2
    assert (
        payload(
            await conversation_router.list_conversations(
                request, agent_id=None, agent_ids="missing", page=1, page_size=20
            )
        )["code"]
        == ErrorCode.CHATDATA_UNKNOWN_AGENT
    )
    assert (
        payload(
            await conversation_router.list_conversations(
                request, agent_id="missing", agent_ids=None, page=1, page_size=20
            )
        )["code"]
        == ErrorCode.CHATDATA_UNKNOWN_AGENT
    )

    async def fake_list_grouped(agent_id, user_id, page_size, *, group_key=None, page=1, cursor=None):
        assert (agent_id, user_id, page_size, group_key, page, cursor) == (
            "agent_a",
            "u1",
            2,
            "datasource:ds_a",
            2,
            "cursor-a",
        )
        return {"groups": [{"key": group_key, "conversations": [], "has_more": False}]}

    monkeypatch.setattr(conversation_router.conversation, "list_grouped", fake_list_grouped)
    grouped = payload(
        await conversation_router.list_conversations(
            request,
            agent_id="agent_a",
            agent_ids=None,
            page=2,
            page_size=2,
            view="grouped",
            group_key="datasource:ds_a",
            cursor="cursor-a",
        )
    )
    assert grouped["data"]["groups"][0]["key"] == "datasource:ds_a"
    assert (
        payload(
            await conversation_router.list_conversations(
                request,
                agent_id=None,
                agent_ids=None,
                page=1,
                page_size=2,
                view="grouped",
                group_key=None,
            )
        )["code"]
        == ErrorCode.CHATDATA_BAD_REQUEST
    )

    async def get_config(*_args, **_kwargs):
        return conversation_router._agent_configs["agent_a"]

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_config)

    async def get_detail(*_args, **_kwargs):
        return {"id": "conv", "messages": []}

    monkeypatch.setattr(conversation_router.conversation, "get_detail", get_detail)
    assert payload(await conversation_router.get_conversation_detail("conv", request))["data"]["id"] == "conv"

    async def no_detail(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_router.conversation, "get_detail", no_detail)
    assert (
        payload(await conversation_router.get_conversation_detail("conv", request))["code"]
        == ErrorCode.CHATDATA_NOT_FOUND
    )
    assert (
        payload(await conversation_router.get_conversation_detail("", request))["code"]
        == ErrorCode.CHATDATA_BAD_REQUEST
    )

    async def update_title_ok(*_args, **_kwargs):
        return True

    monkeypatch.setattr(conversation_router.conversation, "update_title", update_title_ok)
    updated = payload(
        await conversation_router.update_conversation_title(
            "conv", conversation_router.UpdateTitleRequest(title="  这是一个很长很长很长很长的标题  "), request
        )
    )
    assert updated["data"]["title"] == "这是一个很长很长很长很长的标题"[:20]
    assert (
        payload(
            await conversation_router.update_conversation_title(
                "conv", conversation_router.UpdateTitleRequest(title="   "), request
            )
        )["code"]
        == ErrorCode.CHATDATA_REQUIRED_FIELD
    )

    async def update_title_missing(*_args, **_kwargs):
        return False

    monkeypatch.setattr(conversation_router.conversation, "update_title", update_title_missing)
    assert (
        payload(
            await conversation_router.update_conversation_title(
                "conv", conversation_router.UpdateTitleRequest(title="标题"), request
            )
        )["code"]
        == ErrorCode.CHATDATA_NOT_FOUND
    )

    async def generate_title_ok(*_args, **_kwargs):
        return "自动标题"

    monkeypatch.setattr(conversation_router.conversation, "generate_title", generate_title_ok)
    assert (
        payload(await conversation_router.generate_conversation_title("conv", request))["data"]["title"] == "自动标题"
    )

    async def generate_title_missing(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(conversation_router.conversation, "generate_title", generate_title_missing)
    assert (
        payload(await conversation_router.generate_conversation_title("conv", request))["code"]
        == ErrorCode.CHATDATA_NOT_FOUND
    )

    async def favorite_on(*_args, **_kwargs):
        return True

    monkeypatch.setattr(conversation_router.conversation, "toggle_favorite", favorite_on)
    assert (
        payload(await conversation_router.toggle_conversation_favorite("conv", request))["data"]["is_favorited"] is True
    )

    async def favorite_off(*_args, **_kwargs):
        return False

    monkeypatch.setattr(conversation_router.conversation, "toggle_favorite", favorite_off)
    assert payload(await conversation_router.toggle_conversation_favorite("conv", request))["msg"] == "取消收藏成功"

    async def favorite_missing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_router.conversation, "toggle_favorite", favorite_missing)
    assert (
        payload(await conversation_router.toggle_conversation_favorite("conv", request))["code"]
        == ErrorCode.CHATDATA_NOT_FOUND
    )

    async def no_config(*_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_completion_route_validates_ownership_and_injects_supported_kwargs(router_state, monkeypatch):
    request = FakeRequest()

    async def fake_get_agent_id(conversation_id, user_id=None):
        if conversation_id == "owned":
            return "agent_a" if user_id == "u1" else None
        if conversation_id == "other-owned":
            return "other" if user_id == "u1" else None
        if conversation_id == "unowned":
            return None if user_id == "u1" else "agent_a"
        return None

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", fake_get_agent_id)

    async def get_datasource_id(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_router.conversation, "get_datasource_id", get_datasource_id)
    calls = []

    async def handler(conversation_id, query, options, file_ids=None, user_id=None, username=None, request=None):
        calls.append(
            {
                "conversation_id": conversation_id,
                "query": query,
                "options": options,
                "file_ids": file_ids,
                "user_id": user_id,
                "username": username,
                "request": request,
            }
        )
        return {"streamed": True}

    conversation_router._completion_handlers["agent_a"] = handler
    response = await conversation_router.completion(
        "owned",
        conversation_router.CompletionRequest(agent_id="agent_a", query="hello", options={"x": 1}, file_ids=["f1"]),
        request,
    )
    assert response == {"streamed": True}
    assert calls[-1]["file_ids"] == ["f1"]
    assert calls[-1]["username"] == "用户一"
    assert calls[-1]["request"] is request

    assert (
        payload(
            await conversation_router.completion(
                "", conversation_router.CompletionRequest(agent_id="agent_a", query="hello"), request
            )
        )["code"]
        == ErrorCode.CHATDATA_BAD_REQUEST
    )
    assert (
        payload(
            await conversation_router.completion(
                "owned", conversation_router.CompletionRequest(agent_id="agent_a", query=""), request
            )
        )["code"]
        == ErrorCode.CHATDATA_REQUIRED_FIELD
    )
    assert (
        payload(
            await conversation_router.completion(
                "other-owned", conversation_router.CompletionRequest(agent_id="agent_a", query="hello"), request
            )
        )["code"]
        == ErrorCode.CHATDATA_NOT_FOUND
    )
    assert (
        payload(
            await conversation_router.completion(
                "unowned", conversation_router.CompletionRequest(agent_id="agent_a", query="hello"), request
            )
        )["code"]
        == ErrorCode.CHATDATA_NOT_FOUND
    )
    assert (
        payload(
            await conversation_router.completion(
                "new", conversation_router.CompletionRequest(agent_id="missing", query="hello"), request
            )
        )["code"]
        == ErrorCode.CHATDATA_UNKNOWN_AGENT
    )

    # 队列执行入参透传：空 query 只有携带 queued_message_id 才放行
    queue_calls = []

    async def queue_handler(
        conversation_id,
        query,
        options,
        file_ids=None,
        queued_message_id=None,
        preserve_queued_messages=False,
        user_id=None,
        username=None,
        request=None,
    ):
        queue_calls.append(
            {
                "conversation_id": conversation_id,
                "query": query,
                "queued_message_id": queued_message_id,
                "preserve_queued_messages": preserve_queued_messages,
                "user_id": user_id,
            }
        )
        return {"streamed": True}

    conversation_router._completion_handlers["agent_a"] = queue_handler
    response = await conversation_router.completion(
        "owned",
        conversation_router.CompletionRequest(
            agent_id="agent_a",
            query="",
            queued_message_id="msg-1",
            preserve_queued_messages=True,
        ),
        request,
    )
    assert response == {"streamed": True}
    assert queue_calls[-1] == {
        "conversation_id": "owned",
        "query": "",
        "queued_message_id": "msg-1",
        "preserve_queued_messages": True,
        "user_id": "u1",
    }


@pytest.mark.asyncio
async def test_completion_route_binds_datasource_and_rejects_mismatch(router_state, monkeypatch):
    request = FakeRequest()
    created = []
    handler_options = []

    async def no_existing_conversation(*_args, **_kwargs):
        return None

    async def create_conversation(*args, **_kwargs):
        created.append(args)
        return "conv"

    async def get_datasource_id(*_args, **_kwargs):
        return "ds_a"

    async def handler(**kwargs):
        handler_options.append(kwargs["options"])
        return {"streamed": True}

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", no_existing_conversation)
    monkeypatch.setattr(conversation_router.conversation, "get_or_create_conversation", create_conversation)
    monkeypatch.setattr(conversation_router.conversation, "get_datasource_id", get_datasource_id)
    conversation_router._completion_handlers["agent_a"] = handler

    response = await conversation_router.completion(
        "conv",
        conversation_router.CompletionRequest(agent_id="agent_a", query="hello", options={"datasource_id": "ds_a"}),
        request,
    )
    assert response == {"streamed": True}
    assert created == [("agent_a", "conv", "u1")]
    assert handler_options[-1]["datasource_id"] == "ds_a"

    restored = await conversation_router.completion(
        "conv",
        conversation_router.CompletionRequest(agent_id="agent_a", query="continue"),
        request,
    )
    assert restored == {"streamed": True}
    assert handler_options[-1]["datasource_id"] == "ds_a"

    rejected = payload(
        await conversation_router.completion(
            "conv",
            conversation_router.CompletionRequest(agent_id="agent_a", query="hello", options={"datasource_id": "ds_b"}),
            request,
        )
    )
    assert rejected["code"] == ErrorCode.CHATDATA_BAD_REQUEST


@pytest.mark.asyncio
async def test_get_conversation_context_returns_business_datasource(router_state, monkeypatch):
    request = FakeRequest()

    async def get_config(*_args, **_kwargs):
        return conversation_router._agent_configs["agent_a"]

    async def get_datasource_id(*_args, **_kwargs):
        return "ds_a"

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_config)
    monkeypatch.setattr(conversation_router.conversation, "get_datasource_id", get_datasource_id)

    result = payload(await conversation_router.get_conversation_context("conv", request))
    assert result["data"] == {"conversation_id": "conv", "datasource_id": "ds_a"}


@pytest.mark.asyncio
async def test_routes_convert_authentication_and_unexpected_errors(router_state, monkeypatch):
    request = FakeRequest()

    def raise_auth(_request):
        raise AuthenticationError("login required", status_code=401)

    monkeypatch.setattr(conversation_router, "get_forwarded_user", raise_auth)
    with pytest.raises(HTTPException) as create_error:
        await conversation_router.create_conversation(
            conversation_router.CreateConversationRequest(agent_id="agent_a"), request
        )
    assert create_error.value.status_code == 401
    with pytest.raises(HTTPException) as list_error:
        await conversation_router.list_conversations(request)
    assert list_error.value.status_code == 401

    user = SimpleNamespace(user_id="u1", username="用户一")
    monkeypatch.setattr(conversation_router, "get_forwarded_user", lambda _request: user)

    async def get_config(*_args, **_kwargs):
        return conversation_router._agent_configs["agent_a"]

    async def detail_raises(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_config)
    monkeypatch.setattr(conversation_router.conversation, "get_detail", detail_raises)
    assert (
        payload(await conversation_router.get_conversation_detail("conv", request))["code"]
        == ErrorCode.CHATDATA_CONVERSATION_DETAIL_FAILED
    )


@pytest.mark.asyncio
async def test_router_additional_error_branches(router_state, monkeypatch):
    request = FakeRequest()

    async def create_raises(*_args, **_kwargs):
        raise RuntimeError("create down")

    monkeypatch.setattr(conversation_router.conversation, "create", create_raises)
    assert (
        payload(
            await conversation_router.create_conversation(
                conversation_router.CreateConversationRequest(agent_id="agent_a"), request
            )
        )["code"]
        == ErrorCode.CHATDATA_CONVERSATION_CREATE_FAILED
    )

    async def list_raises(*_args, **_kwargs):
        raise RuntimeError("list down")

    monkeypatch.setattr(conversation_router.conversation, "list_all", list_raises)
    assert (
        payload(await conversation_router.list_conversations(request))["code"]
        == ErrorCode.CHATDATA_CONVERSATION_LIST_FAILED
    )

    async def list_internal_raises(*_args, **_kwargs):
        raise RuntimeError(
            "(pymysql.err.OperationalError) "
            "(2003, \"Can't connect to MySQL server on '62.234.152.217'\") "
            "(Background on this error at: https://sqlalche.me/e/20/e3q8)"
        )

    monkeypatch.setattr(conversation_router.conversation, "list_all", list_internal_raises)
    sanitized = payload(
        await conversation_router.list_conversations(request, agent_id=None, agent_ids=None, page=1, page_size=20)
    )
    assert sanitized["code"] == ErrorCode.CHATDATA_CONVERSATION_LIST_FAILED
    assert sanitized["msg"] == "获取对话列表失败，请稍后重试"
    assert "62.234.152.217" not in sanitized["msg"]

    async def get_config(*_args, **_kwargs):
        return conversation_router._agent_configs["agent_a"]

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_config)

    async def title_raises(*_args, **_kwargs):
        raise RuntimeError("title down")

    monkeypatch.setattr(conversation_router.conversation, "update_title", title_raises)
    assert (
        payload(
            await conversation_router.update_conversation_title(
                "conv", conversation_router.UpdateTitleRequest(title="标题"), request
            )
        )["code"]
        == ErrorCode.CHATDATA_CONVERSATION_TITLE_UPDATE_FAILED
    )
    assert (
        payload(
            await conversation_router.update_conversation_title(
                "", conversation_router.UpdateTitleRequest(title="标题"), request
            )
        )["code"]
        == ErrorCode.CHATDATA_BAD_REQUEST
    )

    async def generate_raises(*_args, **_kwargs):
        raise RuntimeError("generate down")

    monkeypatch.setattr(conversation_router.conversation, "generate_title", generate_raises)
    assert (
        payload(await conversation_router.generate_conversation_title("conv", request))["code"]
        == ErrorCode.CHATDATA_CONVERSATION_TITLE_GENERATE_FAILED
    )
    assert (
        payload(await conversation_router.generate_conversation_title("", request))["code"]
        == ErrorCode.CHATDATA_BAD_REQUEST
    )

    async def favorite_raises(*_args, **_kwargs):
        raise RuntimeError("favorite down")

    monkeypatch.setattr(conversation_router.conversation, "toggle_favorite", favorite_raises)
    assert (
        payload(await conversation_router.toggle_conversation_favorite("conv", request))["code"]
        == ErrorCode.CHATDATA_CONVERSATION_FAVORITE_FAILED
    )
    assert (
        payload(await conversation_router.toggle_conversation_favorite("", request))["code"]
        == ErrorCode.CHATDATA_BAD_REQUEST
    )

    async def get_agent(*_args, **_kwargs):
        return "agent_a"

    conversation_router._completion_handlers["agent_a"] = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError("sync boom")
    )
    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_agent)
    assert (
        payload(
            await conversation_router.completion(
                "conv", conversation_router.CompletionRequest(agent_id="agent_a", query="hello"), request
            )
        )["code"]
        == ErrorCode.CHATDATA_CONVERSATION_COMPLETION_FAILED
    )


@pytest.mark.asyncio
async def test_router_conversation_routes_return_not_found_when_config_missing(router_state, monkeypatch):
    request = FakeRequest()

    async def missing_config(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", missing_config)

    assert (
        payload(await conversation_router.get_conversation_detail("conv", request))["code"]
        == ErrorCode.CHATDATA_NOT_FOUND
    )
    assert (
        payload(
            await conversation_router.update_conversation_title(
                "conv",
                conversation_router.UpdateTitleRequest(title="标题"),
                request,
            )
        )["code"]
        == ErrorCode.CHATDATA_NOT_FOUND
    )
    assert (
        payload(await conversation_router.generate_conversation_title("conv", request))["code"]
        == ErrorCode.CHATDATA_NOT_FOUND
    )
    assert (
        payload(await conversation_router.toggle_conversation_favorite("conv", request))["code"]
        == ErrorCode.CHATDATA_NOT_FOUND
    )


# ============ steer 队列接口分支 ============


@pytest.fixture()
def steer_router_state(router_state, monkeypatch):
    """steer 接口测试环境：会话归属校验通过，inbox service 可观察。"""
    calls = {}

    async def get_agent_id(_conversation_id, _user_id=None):
        return "chat_data"

    async def enqueue(conversation_id, user_id, operator_name, content, queue_type="next-turn", meta=None):
        calls["enqueue"] = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "operator_name": operator_name,
            "content": content,
            "queue_type": queue_type,
            "meta": meta,
        }
        return "message-1"

    async def update(conversation_id, user_id, operator_name, message_id, *, content=None, queue_type=None):
        calls["update"] = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "message_id": message_id,
            "content": content,
            "queue_type": queue_type,
        }
        return {"message_id": message_id, "content": content or "原内容", "queue_type": queue_type or "next-step"}

    async def cancel(conversation_id, user_id, operator_name, message_id):
        calls["cancel"] = {"conversation_id": conversation_id, "message_id": message_id}
        return None

    async def list_pending(conversation_id, user_id):
        calls["list_pending"] = {"conversation_id": conversation_id, "user_id": user_id}
        return {
            "has_active_run": True,
            "messages": [
                {
                    "message_id": "message-1",
                    "content": "排队一",
                    "queue_type": "next-turn",
                    "status": "pending",
                    "create_time": "2025-01-01 00:00:00",
                },
            ],
        }

    async def cancel_run(*, conversation_id, user_id, operator_name, run_id):
        calls["cancel_run"] = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "operator_name": operator_name,
            "run_id": run_id,
        }
        return True

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_agent_id)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "enqueue_message", enqueue)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "update_pending_message", update)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "cancel_pending_message", cancel)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "list_message_states", list_pending)
    monkeypatch.setattr(conversation_router.agent_run_runtime, "cancel_run", cancel_run)
    return calls


@pytest.mark.asyncio
async def test_steer_enqueue_default_and_explicit_queue_type(steer_router_state):
    calls = steer_router_state
    request = conversation_router.SteerRequest(query=" 默认下轮 ")
    response = await conversation_router.steer("conv-1", request, FakeRequest())
    assert payload(response)["code"] == ErrorCode.SUCCESS
    assert calls["enqueue"]["content"] == "默认下轮"
    assert calls["enqueue"]["queue_type"] == "next-turn"
    assert calls["enqueue"]["meta"] is None

    request = conversation_router.SteerRequest(query="立即插入", queue_type="next-step")
    response = await conversation_router.steer("conv-1", request, FakeRequest())
    assert calls["enqueue"]["queue_type"] == "next-step"


@pytest.mark.asyncio
async def test_steer_enqueue_normalizes_meta(steer_router_state):
    """随行元数据归一：引用走 parse_file_selections（strip/去重/斜杠容错），ID 去重；全空不落 meta。"""
    calls = steer_router_state
    request = conversation_router.SteerRequest(
        query="带引用",
        file_selections=["uploads/a.pdf", "/workspace/semantic/a__1.yaml/", "uploads/a.pdf", " "],
        file_ids=[" file-1 ", "file-2", "file-1", ""],
    )
    response = await conversation_router.steer("conv-1", request, FakeRequest())
    assert payload(response)["code"] == ErrorCode.SUCCESS
    assert calls["enqueue"]["meta"] == {
        "file_selections": ["uploads/a.pdf", "workspace/semantic/a__1.yaml"],
        "file_ids": ["file-1", "file-2"],
    }

    # 两类引用都为空（或全是空白）时不落 meta，与纯文本消息同形态
    request = conversation_router.SteerRequest(query="纯文本", file_selections=[""], file_ids=[])
    await conversation_router.steer("conv-1", request, FakeRequest())
    assert calls["enqueue"]["meta"] is None


def test_normalize_steer_meta_keeps_unvalidated_paths():
    """入口只做格式归一：穿越路径原样保留进 meta，安全性由消费端
    _validate_workspace_file_selections（WORKSPACE_PATH_POLICY）统一拦截——存引用、晚解析。"""
    request = conversation_router.SteerRequest(query="x", file_selections=["../../etc/passwd", "uploads/a.pdf"])
    meta = conversation_router._normalize_steer_meta(request)
    assert meta == {"file_selections": ["../../etc/passwd", "uploads/a.pdf"]}


def test_normalize_steer_meta_caps_item_count():
    """数量护栏：meta 列 TEXT 64KB，单类引用超上限直接截断（防异常超大 payload）。"""
    cap = conversation_router._STEER_META_MAX_ITEMS
    request = conversation_router.SteerRequest(
        query="x",
        file_selections=[f"uploads/f{i}.pdf" for i in range(cap + 10)],
        file_ids=[f"file-{i}" for i in range(cap + 10)],
    )
    meta = conversation_router._normalize_steer_meta(request)
    assert len(meta["file_selections"]) == cap
    assert len(meta["file_ids"]) == cap
    assert meta["file_ids"][0] == "file-0"  # 截断保留先入队的


@pytest.mark.asyncio
async def test_steer_enqueue_rejects_blank_content(steer_router_state):
    response = await conversation_router.steer("conv-1", conversation_router.SteerRequest(query="   "), FakeRequest())
    assert payload(response)["code"] == ErrorCode.CHATDATA_REQUIRED_FIELD


@pytest.mark.asyncio
async def test_cancel_active_run_success(steer_router_state):
    calls = steer_router_state
    response = await conversation_router.cancel_active_run("conv-1", FakeRequest(), "run-1")
    assert payload(response)["data"] == {"cancelled": True}
    assert calls["cancel_run"] == {
        "conversation_id": "conv-1",
        "user_id": "u1",
        "operator_name": "用户一",
        "run_id": "run-1",
    }


@pytest.mark.asyncio
async def test_update_steer_message_success(steer_router_state):
    calls = steer_router_state
    request = conversation_router.SteerMessageUpdateRequest(content="改内容", queue_type="next-turn")
    response = await conversation_router.update_steer_message("conv-1", "message-1", request, FakeRequest())
    assert payload(response)["code"] == ErrorCode.SUCCESS
    assert calls["update"]["message_id"] == "message-1"
    assert calls["update"]["content"] == "改内容"
    assert calls["update"]["queue_type"] == "next-turn"


@pytest.mark.asyncio
async def test_update_steer_message_requires_field(steer_router_state):
    response = await conversation_router.update_steer_message(
        "conv-1",
        "message-1",
        conversation_router.SteerMessageUpdateRequest(),
        FakeRequest(),
    )
    assert payload(response)["code"] == ErrorCode.CHATDATA_REQUIRED_FIELD


@pytest.mark.asyncio
async def test_update_steer_message_not_found_and_not_pending(steer_router_state, monkeypatch):
    async def not_found(*_args, **_kwargs):
        raise conversation_router.SteerMessageNotFoundError("队列消息不存在")

    monkeypatch.setattr(conversation_router.agent_inbox_service, "update_pending_message", not_found)
    response = await conversation_router.update_steer_message(
        "conv-1",
        "message-1",
        conversation_router.SteerMessageUpdateRequest(content="x"),
        FakeRequest(),
    )
    assert payload(response)["code"] == ErrorCode.CHATDATA_STEER_MESSAGE_NOT_FOUND

    async def not_pending(*_args, **_kwargs):
        raise conversation_router.SteerMessageNotPendingError("消息已进入注入流程，无法修改")

    monkeypatch.setattr(conversation_router.agent_inbox_service, "update_pending_message", not_pending)
    response = await conversation_router.update_steer_message(
        "conv-1",
        "message-1",
        conversation_router.SteerMessageUpdateRequest(content="x"),
        FakeRequest(),
    )
    assert payload(response)["code"] == ErrorCode.CHATDATA_STEER_MESSAGE_NOT_PENDING


@pytest.mark.asyncio
async def test_cancel_steer_message_success_and_not_found(steer_router_state, monkeypatch):
    calls = steer_router_state
    response = await conversation_router.cancel_steer_message("conv-1", "message-1", FakeRequest())
    assert payload(response)["code"] == ErrorCode.SUCCESS
    assert calls["cancel"]["message_id"] == "message-1"

    async def not_found(*_args, **_kwargs):
        raise conversation_router.SteerMessageNotFoundError("队列消息不存在")

    monkeypatch.setattr(conversation_router.agent_inbox_service, "cancel_pending_message", not_found)
    response = await conversation_router.cancel_steer_message("conv-1", "missing", FakeRequest())
    assert payload(response)["code"] == ErrorCode.CHATDATA_STEER_MESSAGE_NOT_FOUND


@pytest.mark.asyncio
async def test_list_steer_messages_restores_queue(steer_router_state):
    calls = steer_router_state
    response = await conversation_router.list_steer_messages("conv-1", FakeRequest())
    body = payload(response)
    assert body["code"] == ErrorCode.SUCCESS
    assert calls["list_pending"] == {"conversation_id": "conv-1", "user_id": "u1"}
    assert body["data"]["has_active_run"] is True
    assert [item["content"] for item in body["data"]["messages"]] == ["排队一"]


@pytest.mark.asyncio
async def test_pending_question_is_scoped_to_current_user(router_state, monkeypatch):
    calls = {}

    async def pending(conversation_id, user_id):
        calls["pending"] = (conversation_id, user_id)
        return {"question_id": "q1", "questions": []}

    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_pending_question", pending)
    response = await conversation_router.get_pending_question("conv-1", FakeRequest())

    assert payload(response)["data"]["question_id"] == "q1"
    assert calls["pending"] == ("conv-1", "u1")


@pytest.mark.asyncio
async def test_answer_question_rejects_duplicate_resume(router_state, monkeypatch):
    async def get_agent_id(_conversation_id, _user_id=None):
        return "chat_data"

    async def pending(_conversation_id, _user_id):
        return {"run_id": "run-1", "question_id": "q1", "turn_id": "turn-1", "questions": []}

    async def duplicate(*_args, **_kwargs):
        raise conversation_router.AgentQuestionNotWaitingError("问题已回答或运行已恢复")

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_agent_id)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_pending_question", pending)
    monkeypatch.setattr(
        conversation_router.agent_run_event_service, "wait_for_done", lambda _run_id: _async_value(True)
    )
    monkeypatch.setattr(
        conversation_router.agent_run_event_service, "latest_event_id", lambda _run_id: _async_value("old-tail-0")
    )
    monkeypatch.setattr(conversation_router.agent_inbox_service, "claim_question_answer", duplicate)

    response = await conversation_router.answer_question(
        "conv-1",
        "q1",
        conversation_router.QuestionAnswerRequest(answers=[]),
        FakeRequest(),
    )
    assert payload(response)["code"] == ErrorCode.CHATDATA_QUESTION_NOT_WAITING
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_answer_question_does_not_claim_before_existing_run_done(router_state, monkeypatch):
    calls = {"claim": 0}

    async def get_agent_id(_conversation_id, _user_id=None):
        return "chat_data"

    async def pending(_conversation_id, _user_id):
        return {"run_id": "run-1", "question_id": "q1", "turn_id": "turn-1", "questions": []}

    async def claim(*_args, **_kwargs):
        calls["claim"] += 1
        return "run-1", {"query": "查询订单"}

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_agent_id)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_pending_question", pending)
    monkeypatch.setattr(
        conversation_router.agent_run_event_service, "wait_for_done", lambda _run_id: _async_value(False)
    )
    monkeypatch.setattr(conversation_router.agent_inbox_service, "claim_question_answer", claim)

    response = await conversation_router.answer_question(
        "conv-1",
        "q1",
        conversation_router.QuestionAnswerRequest(answers=[]),
        FakeRequest(),
    )

    assert response.status_code == 409
    assert payload(response)["code"] == ErrorCode.CHATDATA_QUESTION_NOT_WAITING
    assert calls["claim"] == 0


@pytest.mark.asyncio
async def test_resume_question_leaves_claim_for_runtime_when_stream_creation_fails(router_state, monkeypatch):
    calls = {}

    async def get_agent_id(_conversation_id, _user_id=None):
        return "chat_data"

    async def pending(_conversation_id, _user_id):
        return {"run_id": "run-1", "question_id": "q1", "turn_id": "turn-1", "questions": []}

    async def claim(*_args, **_kwargs):
        return "run-1", {"query": "查询订单"}

    async def restore(run_id, user_id, username):
        calls["restore"] = (run_id, user_id, username)

    def fail_resume(**_kwargs):
        raise RuntimeError("stream creation failed")

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_agent_id)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_pending_question", pending)
    monkeypatch.setattr(
        conversation_router.agent_run_event_service, "wait_for_done", lambda _run_id: _async_value(True)
    )
    monkeypatch.setattr(
        conversation_router.agent_run_event_service, "latest_event_id", lambda _run_id: _async_value("old-tail-0")
    )
    monkeypatch.setattr(conversation_router.agent_inbox_service, "claim_question_answer", claim)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "restore_question_answer", restore)
    monkeypatch.setattr("apps.chat_data_app.agent_stream.resume_chat_data_question_handler", fail_resume)

    with pytest.raises(RuntimeError, match="stream creation failed"):
        await conversation_router._resume_question("conv-1", "q1", {"answers": []}, FakeRequest())

    # The HTTP request has no owner token. Runtime owns rollback/recovery after
    # it claims the pending resume, so the router must not mutate the Run here.
    assert calls == {}


@pytest.mark.asyncio
async def test_resume_question_subscribes_after_existing_question_events(router_state, monkeypatch):
    calls = {}

    async def get_agent_id(_conversation_id, _user_id=None):
        return "chat_data"

    async def pending(_conversation_id, _user_id):
        return {"run_id": "run-1", "question_id": "q1", "turn_id": "turn-1", "questions": []}

    async def claim(*_args, **_kwargs):
        return "run-1", {"query": "查询订单"}

    async def resume(**kwargs):
        calls["resume"] = kwargs
        return "stream"

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_agent_id)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_pending_question", pending)
    monkeypatch.setattr(
        conversation_router.agent_run_event_service, "wait_for_done", lambda _run_id: _async_value(True)
    )
    monkeypatch.setattr(
        conversation_router.agent_run_event_service, "latest_event_id", lambda _run_id: _async_value("old-tail-0")
    )
    monkeypatch.setattr(conversation_router.agent_inbox_service, "claim_question_answer", claim)
    monkeypatch.setattr("apps.chat_data_app.agent_stream.resume_chat_data_question_handler", resume)

    result = await conversation_router._resume_question(
        "conv-1",
        "q1",
        {"answers": []},
        FakeRequest(),
    )

    assert result == "stream"
    assert calls["resume"]["run_id"] == "run-1"
    assert calls["resume"]["last_event_id"] == "old-tail-0"


# ============ 入口长度防线（防止超长输入进 checkpoint 膨胀） ============

from pydantic import ValidationError


def test_steer_request_rejects_overlong_query():
    with pytest.raises(ValidationError):
        conversation_router.SteerRequest(query="x" * 4001)
    # 边界值合法
    request = conversation_router.SteerRequest(query="x" * 4000)
    assert len(request.query) == 4000


def test_steer_message_update_request_rejects_overlong_content():
    with pytest.raises(ValidationError):
        conversation_router.SteerMessageUpdateRequest(content="x" * 4001)
    assert conversation_router.SteerMessageUpdateRequest(content="x" * 4000).content == "x" * 4000
    # 可选字段缺省不受影响
    assert conversation_router.SteerMessageUpdateRequest().content is None
