import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from auth import AuthenticationError
from utils.error_codes import ErrorCode
from apps.chat_data_app.routers import conversations as conversation_router


def payload(response):
    return json.loads(response.body.decode("utf-8"))


class FakeRequest:
    def __init__(self, path="/api/chatdata/conversations"):
        self.url = SimpleNamespace(path=path)


@pytest.fixture()
def router_state(monkeypatch):
    original_configs = dict(conversation_router._agent_configs)
    original_completion = dict(conversation_router._completion_handlers)
    original_resume = dict(conversation_router._resume_handlers)
    original_availability = dict(conversation_router._agent_availability_checkers)
    original_db_path = conversation_router._default_db_path

    user = SimpleNamespace(user_id="u1", username="用户一")
    monkeypatch.setattr(conversation_router, "authenticate_request", lambda _request: user)
    conversation_router._agent_configs.clear()
    conversation_router._completion_handlers.clear()
    conversation_router._resume_handlers.clear()
    conversation_router._agent_availability_checkers.clear()
    conversation_router._default_db_path = "db"
    conversation_router._agent_configs.update({
        "agent_a": {
            "db_path": "db",
            "get_graph": lambda _checkpointer: None,
            "get_checkpointer": lambda: None,
            "file_base_dir": "/tmp/files",
            "allowed_extensions": [".txt"],
        },
        "agent_nograph": {
            "db_path": "db",
            "get_graph": None,
            "get_checkpointer": None,
            "file_base_dir": None,
            "allowed_extensions": None,
        },
    })

    yield user

    conversation_router._agent_configs.clear()
    conversation_router._agent_configs.update(original_configs)
    conversation_router._completion_handlers.clear()
    conversation_router._completion_handlers.update(original_completion)
    conversation_router._resume_handlers.clear()
    conversation_router._resume_handlers.update(original_resume)
    conversation_router._agent_availability_checkers.clear()
    conversation_router._agent_availability_checkers.update(original_availability)
    conversation_router._default_db_path = original_db_path


def test_registration_and_auth_helpers(router_state, monkeypatch):
    conversation_router.register_completion_handler("agent_b", lambda **_kwargs: None)
    conversation_router.register_resume_handler("agent_b", lambda **_kwargs: None)
    conversation_router.register_agent_availability_checker("agent_b", lambda: False)
    assert "agent_b" in conversation_router._completion_handlers
    assert "agent_b" in conversation_router._resume_handlers
    assert conversation_router.get_agent_availability("agent_b").enabled is False
    assert conversation_router.get_agent_config("agent_a")["db_path"] == "db"

    monkeypatch.setattr(conversation_router, "authenticate_request", lambda _request: SimpleNamespace(username="name-only"))
    assert conversation_router._get_user_id(FakeRequest()) == "name-only"
    assert conversation_router._get_current_user_identity(FakeRequest()) == ("name-only", "name-only")

    def raise_auth(_request):
        raise AuthenticationError("bad token", status_code=401)

    monkeypatch.setattr(conversation_router, "authenticate_request", raise_auth)
    with pytest.raises(AuthenticationError):
        conversation_router._get_current_user(FakeRequest("/private"))


@pytest.mark.asyncio
async def test_get_agent_config_by_conversation_branches(router_state, monkeypatch):
    conversation_router._default_db_path = None
    assert await conversation_router.get_agent_config_by_conversation("conv") is None

    conversation_router._default_db_path = "db"
    async def get_no_agent(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_no_agent)
    assert await conversation_router.get_agent_config_by_conversation("conv") is None

    async def get_agent(*_args, **_kwargs):
        return "agent_a"

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_agent)
    assert (await conversation_router.get_agent_config_by_conversation("conv"))["db_path"] == "db"


@pytest.mark.asyncio
async def test_generic_resume_stream_completed_interrupt_and_error():
    class Checkpointer:
        async def __aenter__(self):
            return "checkpointer"

        async def __aexit__(self, *_args):
            return False

    class Graph:
        def __init__(self, state, events=()):
            self.state = state
            self.events = list(events)

        async def astream(self, *_args, **_kwargs):
            for event in self.events:
                yield event

        async def aget_state(self, _config):
            return self.state

    async def collect(state, events=()):
        return [
            chunk
            async for chunk in conversation_router._generic_resume_stream(
                "conv",
                "approve",
                {"ok": True},
                lambda _cp: Graph(state, events),
                lambda: Checkpointer(),
            )
        ]

    completed = await collect(
        SimpleNamespace(next=(), tasks=[], values={"messages": [HumanMessage(content="q"), AIMessage(content="done", id="ai1", additional_kwargs={"k": "v"})]}),
        [("custom", {"event": "progress", "data": {"step": 1}}), ("messages", {"ignored": True})],
    )
    assert completed[0].startswith("event: progress")
    assert "event: completed" in completed[-1]
    assert "done" in completed[-1]

    generic_done = await collect(SimpleNamespace(next=(), tasks=[], values={"messages": [HumanMessage(content="q")]}))
    assert "操作成功" in generic_done[-1]

    interrupted = await collect(SimpleNamespace(
        next=("confirm",),
        tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={"sql": "select 1"})])],
        values={"messages": []},
    ))
    assert "event: interrupt" in interrupted[-1]
    assert "select 1" in interrupted[-1]

    missing_state = await collect(SimpleNamespace(next=(), tasks=[], values={}))
    assert "未获取到最终状态" in missing_state[-1]

    def bad_checkpointer():
        raise RuntimeError("checkpoint down")

    errored = [
        chunk
        async for chunk in conversation_router._generic_resume_stream(
            "conv", "approve", None, lambda _cp: None, bad_checkpointer
        )
    ]
    assert "恢复对话失败: checkpoint down" in errored[-1]


@pytest.mark.asyncio
async def test_conversation_crud_routes_success_and_errors(router_state, monkeypatch):
    request = FakeRequest()

    async def fake_create(db_path, agent_id, user_id):
        return {"id": "conv_new", "agent_id": agent_id, "user_id": user_id, "db_path": db_path}

    monkeypatch.setattr(conversation_router.conversation, "create", fake_create)
    created = payload(await conversation_router.create_conversation(conversation_router.CreateConversationRequest(agent_id="agent_a"), request))
    assert created["data"]["id"] == "conv_new"
    assert payload(await conversation_router.create_conversation(conversation_router.CreateConversationRequest(agent_id="missing"), request))["code"] == ErrorCode.CHATDATA_UNKNOWN_AGENT

    conversation_router.register_agent_availability_checker(
        "agent_a",
        lambda: conversation_router.AgentAvailabilityResult(
            enabled=False,
            message="功能暂未开放",
            code=ErrorCode.CHATDATA_DATA_ANALYZE_DISABLED,
        ),
    )
    assert payload(await conversation_router.create_conversation(conversation_router.CreateConversationRequest(agent_id="agent_a"), request))["code"] == ErrorCode.CHATDATA_DATA_ANALYZE_DISABLED
    conversation_router._agent_availability_checkers.pop("agent_a", None)

    async def fake_list(_db_path, agent_id, user_id, limit, offset):
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

    monkeypatch.setattr(conversation_router.conversation, "list", fake_list)
    monkeypatch.setattr(conversation_router.conversation, "list_by_agent_ids", fake_list_by_agent_ids)
    monkeypatch.setattr(conversation_router.conversation, "list_all", fake_list_all)
    listed_one = payload(await conversation_router.list_conversations(request, agent_id="agent_a", agent_ids=None, page=2, page_size=1))
    assert listed_one["data"]["has_more"] is True
    listed_many = payload(await conversation_router.list_conversations(request, agent_id=None, agent_ids="agent_a, agent_nograph", page=1, page_size=2))
    assert listed_many["data"]["has_more"] is True
    listed_all = payload(await conversation_router.list_conversations(request, agent_id=None, agent_ids=None, page=1, page_size=20))
    assert listed_all["data"]["agent_count"] == 2
    assert payload(await conversation_router.list_conversations(request, agent_id=None, agent_ids="missing", page=1, page_size=20))["code"] == ErrorCode.CHATDATA_UNKNOWN_AGENT
    assert payload(await conversation_router.list_conversations(request, agent_id="missing", agent_ids=None, page=1, page_size=20))["code"] == ErrorCode.CHATDATA_UNKNOWN_AGENT

    conversation_router.register_agent_availability_checker("agent_a", lambda: {"enabled": False, "message": "禁用", "code": ErrorCode.CHATDATA_DATA_ANALYZE_DISABLED})
    hidden_one = payload(await conversation_router.list_conversations(request, agent_id="agent_a", agent_ids=None, page=1, page_size=20))
    hidden_many = payload(await conversation_router.list_conversations(request, agent_id=None, agent_ids="agent_a,agent_nograph", page=1, page_size=20))
    hidden_all = payload(await conversation_router.list_conversations(request, agent_id=None, agent_ids=None, page=1, page_size=20))
    assert hidden_one["data"]["conversations"] == []
    assert hidden_many["data"]["conversations"] == [{"id": "c2"}]
    assert hidden_all["data"]["agent_count"] == 1
    conversation_router._agent_availability_checkers.pop("agent_a", None)

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
    assert payload(await conversation_router.get_conversation_detail("conv", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload(await conversation_router.get_conversation_detail("", request))["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    async def delete_ok(*_args, **_kwargs):
        return True

    monkeypatch.setattr(conversation_router.conversation, "delete", delete_ok)
    assert payload(await conversation_router.delete_conversation("conv", request))["data"]["id"] == "conv"

    async def delete_missing(*_args, **_kwargs):
        return False

    monkeypatch.setattr(conversation_router.conversation, "delete", delete_missing)
    assert payload(await conversation_router.delete_conversation("conv", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND

    async def update_title_ok(*_args, **_kwargs):
        return True

    monkeypatch.setattr(conversation_router.conversation, "update_title", update_title_ok)
    updated = payload(await conversation_router.update_conversation_title("conv", conversation_router.UpdateTitleRequest(title="  这是一个很长很长很长很长的标题  "), request))
    assert updated["data"]["title"] == "这是一个很长很长很长很长的标题"[:20]
    assert payload(await conversation_router.update_conversation_title("conv", conversation_router.UpdateTitleRequest(title="   "), request))["code"] == ErrorCode.CHATDATA_REQUIRED_FIELD

    async def update_title_missing(*_args, **_kwargs):
        return False

    monkeypatch.setattr(conversation_router.conversation, "update_title", update_title_missing)
    assert payload(await conversation_router.update_conversation_title("conv", conversation_router.UpdateTitleRequest(title="标题"), request))["code"] == ErrorCode.CHATDATA_NOT_FOUND

    async def generate_title_ok(*_args, **_kwargs):
        return "自动标题"

    monkeypatch.setattr(conversation_router.conversation, "generate_title", generate_title_ok)
    assert payload(await conversation_router.generate_conversation_title("conv", request))["data"]["title"] == "自动标题"

    async def generate_title_missing(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(conversation_router.conversation, "generate_title", generate_title_missing)
    assert payload(await conversation_router.generate_conversation_title("conv", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND

    async def favorite_on(*_args, **_kwargs):
        return True

    monkeypatch.setattr(conversation_router.conversation, "toggle_favorite", favorite_on)
    assert payload(await conversation_router.toggle_conversation_favorite("conv", request))["data"]["is_favorited"] is True

    async def favorite_off(*_args, **_kwargs):
        return False

    monkeypatch.setattr(conversation_router.conversation, "toggle_favorite", favorite_off)
    assert payload(await conversation_router.toggle_conversation_favorite("conv", request))["msg"] == "取消收藏成功"

    async def favorite_missing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_router.conversation, "toggle_favorite", favorite_missing)
    assert payload(await conversation_router.toggle_conversation_favorite("conv", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND

    async def no_config(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", no_config)
    assert payload(await conversation_router.delete_conversation("conv", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND


@pytest.mark.asyncio
async def test_completion_route_validates_ownership_and_injects_supported_kwargs(router_state, monkeypatch):
    request = FakeRequest()

    async def fake_get_agent_id(_db_path, conversation_id, user_id=None):
        if conversation_id == "owned":
            return "agent_a" if user_id == "u1" else None
        if conversation_id == "other-owned":
            return "other" if user_id == "u1" else None
        if conversation_id == "unowned":
            return None if user_id == "u1" else "agent_a"
        return None

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", fake_get_agent_id)
    calls = []

    async def handler(conversation_id, query, options, file_ids=None, user_id=None, username=None, request=None):
        calls.append({
            "conversation_id": conversation_id,
            "query": query,
            "options": options,
            "file_ids": file_ids,
            "user_id": user_id,
            "username": username,
            "request": request,
        })
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

    conversation_router.register_agent_availability_checker("agent_a", lambda: False)
    disabled_response = payload(await conversation_router.completion(
        "owned",
        conversation_router.CompletionRequest(agent_id="agent_a", query="hello"),
        request,
    ))
    assert disabled_response["code"] == ErrorCode.CHATDATA_DATA_ANALYZE_DISABLED
    assert calls[-1]["query"] == "hello"
    conversation_router._agent_availability_checkers.pop("agent_a", None)

    assert payload(await conversation_router.completion("", conversation_router.CompletionRequest(agent_id="agent_a", query="hello"), request))["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert payload(await conversation_router.completion("owned", conversation_router.CompletionRequest(agent_id="agent_a", query=""), request))["code"] == ErrorCode.CHATDATA_REQUIRED_FIELD
    assert payload(await conversation_router.completion("other-owned", conversation_router.CompletionRequest(agent_id="agent_a", query="hello"), request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload(await conversation_router.completion("unowned", conversation_router.CompletionRequest(agent_id="agent_a", query="hello"), request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload(await conversation_router.completion("new", conversation_router.CompletionRequest(agent_id="missing", query="hello"), request))["code"] == ErrorCode.CHATDATA_UNKNOWN_AGENT


@pytest.mark.asyncio
async def test_completion_route_binds_datasource_and_rejects_mismatch(router_state, monkeypatch):
    request = FakeRequest()

    async def no_existing_conversation(*_args, **_kwargs):
        return None

    async def get_checkpoint_datasource_id(*_args, **_kwargs):
        return "ds_a"

    async def handler(**_kwargs):
        return {"streamed": True}

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", no_existing_conversation)
    monkeypatch.setattr(conversation_router.conversation, "get_checkpoint_datasource_id", get_checkpoint_datasource_id)
    conversation_router._completion_handlers["agent_a"] = handler

    response = await conversation_router.completion(
        "conv",
        conversation_router.CompletionRequest(agent_id="agent_a", query="hello", options={"datasource_id": "ds_a"}),
        request,
    )
    assert response == {"streamed": True}

    rejected = payload(await conversation_router.completion(
        "conv",
        conversation_router.CompletionRequest(agent_id="agent_a", query="hello", options={"datasource_id": "ds_b"}),
        request,
    ))
    assert rejected["code"] == ErrorCode.CHATDATA_BAD_REQUEST


@pytest.mark.asyncio
async def test_get_conversation_context_returns_checkpoint_datasource(router_state, monkeypatch):
    request = FakeRequest()

    async def get_config(*_args, **_kwargs):
        return conversation_router._agent_configs["agent_a"]

    async def get_checkpoint_datasource_id(*_args, **_kwargs):
        return "ds_a"

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_config)
    monkeypatch.setattr(conversation_router.conversation, "get_checkpoint_datasource_id", get_checkpoint_datasource_id)

    result = payload(await conversation_router.get_conversation_context("conv", request))
    assert result["data"] == {"conversation_id": "conv", "datasource_id": "ds_a"}


@pytest.mark.asyncio
async def test_resume_route_custom_and_generic(router_state, monkeypatch):
    request = FakeRequest()

    async def get_config(*_args, **_kwargs):
        return conversation_router._agent_configs["agent_a"]

    async def get_agent(*_args, **_kwargs):
        return "agent_a"

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_config)
    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_agent)

    async def custom_handler(conversation_id, action, data):
        return {"conversation_id": conversation_id, "action": action, "data": data}

    conversation_router._resume_handlers["agent_a"] = custom_handler
    custom = await conversation_router.resume_conversation("conv", conversation_router.ResumeRequest(action="edit", data={"sql": "select 1"}), request)
    assert custom["action"] == "edit"

    conversation_router.register_agent_availability_checker("agent_a", lambda: {"enabled": False, "message": "禁用", "code": ErrorCode.CHATDATA_DATA_ANALYZE_DISABLED})
    disabled_resume = payload(await conversation_router.resume_conversation("conv", conversation_router.ResumeRequest(), request))
    assert disabled_resume["code"] == ErrorCode.CHATDATA_DATA_ANALYZE_DISABLED
    conversation_router._agent_availability_checkers.pop("agent_a", None)

    conversation_router._resume_handlers.clear()

    class Checkpointer:
        async def __aenter__(self):
            return "cp"

        async def __aexit__(self, *_args):
            return False

    class Graph:
        async def astream(self, *_args, **_kwargs):
            if False:
                yield None

        async def aget_state(self, _config):
            return SimpleNamespace(next=(), tasks=[], values={"messages": [AIMessage(content="done")]})

    conversation_router._agent_configs["agent_a"]["get_graph"] = lambda _cp: Graph()
    conversation_router._agent_configs["agent_a"]["get_checkpointer"] = lambda: Checkpointer()
    response = await conversation_router.resume_conversation("conv", conversation_router.ResumeRequest(), request)
    assert isinstance(response, StreamingResponse)
    chunks = [chunk async for chunk in response.body_iterator]
    assert "event: completed" in chunks[-1]

    async def get_no_graph_config(*_args, **_kwargs):
        return conversation_router._agent_configs["agent_nograph"]

    async def get_no_graph_agent(*_args, **_kwargs):
        return "agent_nograph"

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_no_graph_config)
    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_no_graph_agent)
    assert payload(await conversation_router.resume_conversation("conv", conversation_router.ResumeRequest(), request))["code"] == ErrorCode.CHATDATA_UNKNOWN_AGENT

    async def get_no_agent(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_no_agent)
    assert payload(await conversation_router.resume_conversation("conv", conversation_router.ResumeRequest(), request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload(await conversation_router.resume_conversation("", conversation_router.ResumeRequest(), request))["code"] == ErrorCode.CHATDATA_BAD_REQUEST


@pytest.mark.asyncio
async def test_file_and_artifact_routes(router_state, monkeypatch):
    request = FakeRequest()
    async def get_config(*_args, **_kwargs):
        return conversation_router._agent_configs["agent_a"]

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_config)

    async def fake_save_file(**kwargs):
        assert kwargs["base_dir"] == "/tmp/files"
        assert kwargs["allowed_extensions"] == [".txt"]
        return {"file_id": "f1", "file_name": "a.txt"}

    async def fake_read_file(**kwargs):
        assert kwargs["preview"] is True
        return {"download": kwargs["file_id"]}

    monkeypatch.setattr(conversation_router.file_manager, "save_file", fake_save_file)
    monkeypatch.setattr(conversation_router.file_manager, "read_file", fake_read_file)
    monkeypatch.setattr(conversation_router.file_manager, "list_files", lambda base_dir, conversation_id: [{"file_id": f"{conversation_id}:f1", "base_dir": base_dir}])

    assert payload(await conversation_router.upload_file("conv", request, file=SimpleNamespace(filename="a.txt")))["data"]["file_id"] == "f1"
    assert await conversation_router.download_file("conv", "f1", request, preview=True) == {"download": "f1"}
    assert payload(await conversation_router.list_files("conv", request))["data"][0]["file_id"] == "conv:f1"

    no_files = dict(conversation_router._agent_configs["agent_a"], file_base_dir="")
    async def get_no_files_config(*_args, **_kwargs):
        return no_files

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_no_files_config)
    assert payload(await conversation_router.upload_file("conv", request, file=SimpleNamespace(filename="a.txt")))["code"] == ErrorCode.CHATDATA_FILE_STORAGE_NOT_CONFIGURED
    assert payload(await conversation_router.download_file("conv", "f1", request))["code"] == ErrorCode.CHATDATA_FILE_STORAGE_NOT_CONFIGURED
    assert payload(await conversation_router.list_files("conv", request))["code"] == ErrorCode.CHATDATA_FILE_STORAGE_NOT_CONFIGURED

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_config)

    async def bad_save_file(**_kwargs):
        raise ValueError("bad extension")

    async def missing_read_file(**_kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(conversation_router.file_manager, "save_file", bad_save_file)
    monkeypatch.setattr(conversation_router.file_manager, "read_file", missing_read_file)
    assert payload(await conversation_router.upload_file("conv", request, file=SimpleNamespace(filename="a.exe")))["code"] == ErrorCode.CHATDATA_REQUIRED_FIELD
    assert payload(await conversation_router.download_file("conv", "missing", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND

    assert payload(await conversation_router.upload_file("", request, file=SimpleNamespace(filename="a.txt")))["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert payload(await conversation_router.download_file("", "f1", request))["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert payload(await conversation_router.list_files("", request))["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    import utils.artifact_store as artifact_store

    monkeypatch.setattr(artifact_store, "load_artifact_meta_by_id", lambda artifact_id: {
        "conversation_id": "conv",
        "data": {"artifact_id": artifact_id},
    })
    assert payload(await conversation_router.get_artifact("artifact_a", request))["data"]["artifact_id"] == "artifact_a"

    monkeypatch.setattr(artifact_store, "load_artifact_meta_by_id", lambda _artifact_id: None)
    assert payload(await conversation_router.get_artifact("missing", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload(await conversation_router.get_artifact("", request))["code"] == ErrorCode.CHATDATA_BAD_REQUEST


@pytest.mark.asyncio
async def test_routes_convert_authentication_and_unexpected_errors(router_state, monkeypatch):
    request = FakeRequest()

    def raise_auth(_request):
        raise AuthenticationError("login required", status_code=401)

    monkeypatch.setattr(conversation_router, "authenticate_request", raise_auth)
    assert payload(await conversation_router.create_conversation(conversation_router.CreateConversationRequest(agent_id="agent_a"), request))["code"] == ErrorCode.CHATDATA_AUTHENTICATION_FAILED
    assert payload(await conversation_router.list_conversations(request))["code"] == ErrorCode.CHATDATA_AUTHENTICATION_FAILED

    user = SimpleNamespace(user_id="u1", username="用户一")
    monkeypatch.setattr(conversation_router, "authenticate_request", lambda _request: user)
    async def get_config(*_args, **_kwargs):
        return conversation_router._agent_configs["agent_a"]

    async def detail_raises(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_config)
    monkeypatch.setattr(conversation_router.conversation, "get_detail", detail_raises)
    assert payload(await conversation_router.get_conversation_detail("conv", request))["code"] == ErrorCode.CHATDATA_CONVERSATION_DETAIL_FAILED


@pytest.mark.asyncio
async def test_router_additional_error_branches(router_state, monkeypatch):
    request = FakeRequest()

    async def create_raises(*_args, **_kwargs):
        raise RuntimeError("create down")

    monkeypatch.setattr(conversation_router.conversation, "create", create_raises)
    assert payload(await conversation_router.create_conversation(conversation_router.CreateConversationRequest(agent_id="agent_a"), request))["code"] == ErrorCode.CHATDATA_CONVERSATION_CREATE_FAILED

    async def list_raises(*_args, **_kwargs):
        raise RuntimeError("list down")

    monkeypatch.setattr(conversation_router.conversation, "list_all", list_raises)
    assert payload(await conversation_router.list_conversations(request))["code"] == ErrorCode.CHATDATA_CONVERSATION_LIST_FAILED

    async def list_internal_raises(*_args, **_kwargs):
        raise RuntimeError(
            "(pymysql.err.OperationalError) "
            "(2003, \"Can't connect to MySQL server on '62.234.152.217'\") "
            "(Background on this error at: https://sqlalche.me/e/20/e3q8)"
        )

    monkeypatch.setattr(conversation_router.conversation, "list_all", list_internal_raises)
    sanitized = payload(await conversation_router.list_conversations(request, agent_id=None, agent_ids=None, page=1, page_size=20))
    assert sanitized["code"] == ErrorCode.CHATDATA_CONVERSATION_LIST_FAILED
    assert sanitized["msg"] == "后端服务异常，请稍后重试"
    assert "62.234.152.217" not in sanitized["msg"]

    async def get_config(*_args, **_kwargs):
        return conversation_router._agent_configs["agent_a"]

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_config)

    async def delete_raises(*_args, **_kwargs):
        raise RuntimeError("delete down")

    monkeypatch.setattr(conversation_router.conversation, "delete", delete_raises)
    assert payload(await conversation_router.delete_conversation("conv", request))["code"] == ErrorCode.CHATDATA_CONVERSATION_DELETE_FAILED
    assert payload(await conversation_router.delete_conversation("", request))["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    async def title_raises(*_args, **_kwargs):
        raise RuntimeError("title down")

    monkeypatch.setattr(conversation_router.conversation, "update_title", title_raises)
    assert payload(await conversation_router.update_conversation_title("conv", conversation_router.UpdateTitleRequest(title="标题"), request))["code"] == ErrorCode.CHATDATA_CONVERSATION_TITLE_UPDATE_FAILED
    assert payload(await conversation_router.update_conversation_title("", conversation_router.UpdateTitleRequest(title="标题"), request))["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    async def generate_raises(*_args, **_kwargs):
        raise RuntimeError("generate down")

    monkeypatch.setattr(conversation_router.conversation, "generate_title", generate_raises)
    assert payload(await conversation_router.generate_conversation_title("conv", request))["code"] == ErrorCode.CHATDATA_CONVERSATION_TITLE_GENERATE_FAILED
    assert payload(await conversation_router.generate_conversation_title("", request))["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    async def favorite_raises(*_args, **_kwargs):
        raise RuntimeError("favorite down")

    monkeypatch.setattr(conversation_router.conversation, "toggle_favorite", favorite_raises)
    assert payload(await conversation_router.toggle_conversation_favorite("conv", request))["code"] == ErrorCode.CHATDATA_CONVERSATION_FAVORITE_FAILED
    assert payload(await conversation_router.toggle_conversation_favorite("", request))["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    async def get_agent(*_args, **_kwargs):
        return "agent_a"

    conversation_router._completion_handlers["agent_a"] = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("sync boom"))
    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_agent)
    assert payload(await conversation_router.completion("conv", conversation_router.CompletionRequest(agent_id="agent_a", query="hello"), request))["code"] == ErrorCode.CHATDATA_CONVERSATION_COMPLETION_FAILED

    async def get_agent_for_resume(*_args, **_kwargs):
        return "agent_a"

    async def resume_raises(*_args, **_kwargs):
        raise RuntimeError("resume down")

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", get_agent_for_resume)
    conversation_router._resume_handlers["agent_a"] = resume_raises
    assert payload(await conversation_router.resume_conversation("conv", conversation_router.ResumeRequest(), request))["code"] == ErrorCode.CHATDATA_CONVERSATION_RESUME_FAILED


@pytest.mark.asyncio
async def test_router_file_artifact_and_auth_edge_branches(router_state, monkeypatch):
    request = FakeRequest()

    async def missing_config(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", missing_config)
    assert payload(await conversation_router.upload_file("conv", request, file=SimpleNamespace(filename="a.txt")))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload(await conversation_router.download_file("conv", "f1", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload(await conversation_router.list_files("conv", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND

    async def get_config(*_args, **_kwargs):
        return conversation_router._agent_configs["agent_a"]

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", get_config)

    async def save_raises(**_kwargs):
        raise RuntimeError("save down")

    async def read_value_error(**_kwargs):
        raise ValueError("bad file id")

    async def read_raises(**_kwargs):
        raise RuntimeError("read down")

    monkeypatch.setattr(conversation_router.file_manager, "save_file", save_raises)
    assert payload(await conversation_router.upload_file("conv", request, file=SimpleNamespace(filename="a.txt")))["code"] == ErrorCode.CHATDATA_UPLOAD_FAILED

    monkeypatch.setattr(conversation_router.file_manager, "read_file", read_value_error)
    assert payload(await conversation_router.download_file("conv", "bad", request))["code"] == ErrorCode.CHATDATA_REQUIRED_FIELD
    monkeypatch.setattr(conversation_router.file_manager, "read_file", read_raises)
    assert payload(await conversation_router.download_file("conv", "bad", request))["code"] == ErrorCode.CHATDATA_SERVICE_ERROR

    monkeypatch.setattr(conversation_router.file_manager, "list_files", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("list files down")))
    assert payload(await conversation_router.list_files("conv", request))["code"] == ErrorCode.CHATDATA_FILE_LIST_FAILED

    import utils.artifact_store as artifact_store

    monkeypatch.setattr(artifact_store, "load_artifact_meta_by_id", lambda _artifact_id: {"conversation_id": "", "data": {"ok": True}})
    assert payload(await conversation_router.get_artifact("a1", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    monkeypatch.setattr(artifact_store, "load_artifact_meta_by_id", lambda _artifact_id: {"conversation_id": "conv", "data": None})
    assert payload(await conversation_router.get_artifact("a1", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", missing_config)
    monkeypatch.setattr(artifact_store, "load_artifact_meta_by_id", lambda _artifact_id: {"conversation_id": "conv", "data": {"ok": True}})
    assert payload(await conversation_router.get_artifact("a1", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND

    def auth_fails(_request):
        raise AuthenticationError("login required", status_code=401)

    monkeypatch.setattr(conversation_router, "authenticate_request", auth_fails)
    assert payload(await conversation_router.get_artifact("a1", request))["code"] == ErrorCode.CHATDATA_AUTHENTICATION_FAILED


@pytest.mark.asyncio
async def test_router_conversation_routes_return_not_found_when_config_missing(router_state, monkeypatch):
    request = FakeRequest()

    async def missing_config(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", missing_config)

    assert payload(await conversation_router.get_conversation_detail("conv", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload(await conversation_router.delete_conversation("conv", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload(await conversation_router.update_conversation_title(
        "conv",
        conversation_router.UpdateTitleRequest(title="标题"),
        request,
    ))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload(await conversation_router.generate_conversation_title("conv", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload(await conversation_router.toggle_conversation_favorite("conv", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND


@pytest.mark.asyncio
async def test_generic_resume_stream_additional_interrupt_shapes():
    class Checkpointer:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    class Graph:
        def __init__(self, state, events):
            self.state = state
            self.events = events

        async def astream(self, *_args, **_kwargs):
            for event in self.events:
                yield event

        async def aget_state(self, _config):
            return self.state

    async def collect(state, events):
        return [
            chunk
            async for chunk in conversation_router._generic_resume_stream(
                "conv",
                "approve",
                None,
                lambda _cp: Graph(state, events),
                lambda: Checkpointer(),
            )
        ]

    events = [("custom", {"data": {"no_event": True}}), ("custom", "skip"), ("messages", {"ignored": True}), ("too", "long", "skip")]
    no_task_interrupt = await collect(SimpleNamespace(next=("confirm",), tasks=[], values={"messages": []}), events)
    assert "event: message" in no_task_interrupt[0]
    assert '"no_event": true' in no_task_interrupt[0]
    assert "event: interrupt" in no_task_interrupt[-1]
    assert '"interrupt_data": null' in no_task_interrupt[-1]

    non_interrupt_task = await collect(
        SimpleNamespace(next=("confirm",), tasks=[SimpleNamespace(interrupts=[])], values={"messages": []}),
        [],
    )
    assert '"interrupt_data": null' in non_interrupt_task[-1]
