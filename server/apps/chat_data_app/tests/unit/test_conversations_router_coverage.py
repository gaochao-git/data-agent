"""conversations 路由剩余错误分支与 SSE 重订阅的覆盖率补充测试。"""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.chat_data_app.routers import conversations as conversation_router
from auth import AuthenticationError
from utils.error_codes import ErrorCode


def payload(response):
    return json.loads(response.body.decode("utf-8"))


class FakeRequest:
    def __init__(self, path="/api/chatdata/conversations", headers=None, disconnected=False):
        self.url = SimpleNamespace(path=path)
        self.headers = headers or {}
        self.is_disconnected = disconnected


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
            conversation_router.AGENT_ID: {
                "get_graph": lambda _checkpointer: None,
                "get_checkpointer": lambda: None,
            },
        }
    )
    yield user

    conversation_router._agent_configs.clear()
    conversation_router._agent_configs.update(original_configs)
    conversation_router._completion_handlers.clear()
    conversation_router._completion_handlers.update(original_completion)


def identity_sync(request_obj=None):
    """同步替身，保持与真实 _get_current_user_identity 签名一致。"""
    return "u1", "用户一"


@pytest.mark.asyncio
async def test_create_conversation_snapshot_failure_is_non_blocking(router_state, monkeypatch):
    created = []

    async def fake_create(agent_id, user_id, datasource_id=None):
        created.append((agent_id, user_id))
        return {"id": "conv_1"}

    async def snapshot_boom(user_id, conversation_id):
        raise RuntimeError("snapshot boom")

    monkeypatch.setattr(conversation_router.conversation, "create", fake_create)
    monkeypatch.setattr(conversation_router, "build_runtime_skill_snapshot_async", snapshot_boom)

    response = await conversation_router.create_conversation(
        conversation_router.CreateConversationRequest(agent_id=conversation_router.AGENT_ID),
        FakeRequest(),
    )
    assert payload(response)["data"]["id"] == "conv_1"
    assert created == [(conversation_router.AGENT_ID, "u1")]

    async def create_boom(*_args, **_kwargs):
        raise RuntimeError("db boom")

    monkeypatch.setattr(conversation_router.conversation, "create", create_boom)
    response = await conversation_router.create_conversation(
        conversation_router.CreateConversationRequest(agent_id=conversation_router.AGENT_ID),
        FakeRequest(),
    )
    assert payload(response)["code"] == ErrorCode.CHATDATA_CONVERSATION_CREATE_FAILED

    response = await conversation_router.create_conversation(
        conversation_router.CreateConversationRequest(agent_id="ghost_agent"),
        FakeRequest(),
    )
    assert payload(response)["code"] == ErrorCode.CHATDATA_UNKNOWN_AGENT


@pytest.mark.asyncio
async def test_list_conversations_grouped_and_unknown_agent_branches(router_state, monkeypatch):
    monkeypatch.setattr(conversation_router, "_get_current_user_identity", identity_sync)

    response = await conversation_router.list_conversations(
        FakeRequest(),
        agent_id="ghost_agent",
        agent_ids=None,
        page=1,
        page_size=20,
        view="grouped",
        group_key=None,
        cursor=None,
    )
    assert payload(response)["code"] == ErrorCode.CHATDATA_UNKNOWN_AGENT

    response = await conversation_router.list_conversations(
        FakeRequest(),
        agent_id=None,
        agent_ids="a,b",
        page=1,
        page_size=20,
        view="grouped",
        group_key=None,
        cursor=None,
    )
    assert payload(response)["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    async def grouped_raises(*_args, **_kwargs):
        raise ValueError("非法分组")

    monkeypatch.setattr(conversation_router.conversation, "list_grouped", grouped_raises)
    response = await conversation_router.list_conversations(
        FakeRequest(),
        agent_id=conversation_router.AGENT_ID,
        agent_ids=None,
        page=1,
        page_size=20,
        view="grouped",
        group_key=None,
        cursor=None,
    )
    assert payload(response)["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    # 平铺列表传入未注册的合并 agent_ids → UNKNOWN_AGENT
    response = await conversation_router.list_conversations(
        FakeRequest(),
        agent_id=None,
        agent_ids="ghost_agent",
        page=1,
        page_size=20,
        view="flat",
        group_key=None,
        cursor=None,
    )
    body = payload(response)
    assert body["code"] == ErrorCode.CHATDATA_UNKNOWN_AGENT
    assert "ghost_agent" in body["msg"]


@pytest.mark.asyncio
async def test_token_usage_detail_route(router_state, monkeypatch):
    async def config_found(conversation_id, user_id=None):
        return {"get_checkpointer": None}

    async def detail_ok(*_args, **_kwargs):
        return {"used": 10, "breakdown": [{"content": "full"}]}

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", config_found)
    monkeypatch.setattr(conversation_router.conversation_event_service, "get_latest_token_usage_detail", detail_ok)
    response = await conversation_router.get_conversation_token_usage("conv_1", FakeRequest())
    assert payload(response)["data"] == {"used": 10, "breakdown": [{"content": "full"}]}

    async def config_missing(conversation_id, user_id=None):
        return None

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", config_missing)
    response = await conversation_router.get_conversation_token_usage("conv_x", FakeRequest())
    assert payload(response)["msg"] == "对话不存在"


@pytest.mark.asyncio
async def test_detail_title_favorite_error_paths(router_state, monkeypatch):
    async def config_found(conversation_id, user_id=None):
        return {"get_checkpointer": None}

    async def detail_ok(*_args, **_kwargs):
        return {
            "id": "conv_1",
            "messages": ["m"],
            "reasoning_segments": ["r"],
            "extra": True,
            "token_usage": {"used": 10, "breakdown": [{"content": "large"}]},
        }

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", config_found)
    monkeypatch.setattr(conversation_router.conversation, "get_detail", detail_ok)
    response = await conversation_router.get_conversation_detail("conv_1", FakeRequest(), view="timeline")
    data = payload(response)["data"]
    assert "messages" not in data and "reasoning_segments" not in data
    assert data["extra"] is True
    assert "content" not in data["token_usage"]["breakdown"][0]

    async def detail_missing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_router.conversation, "get_detail", detail_missing)
    response = await conversation_router.get_conversation_detail("conv_x", FakeRequest(), view="full")
    assert payload(response)["msg"] == "对话不存在"

    async def config_missing(conversation_id, user_id=None):
        return None

    async def title_ok(conversation_id, title, user_id):
        return True

    async def title_fail(conversation_id, title, user_id):
        return False

    monkeypatch.setattr(conversation_router, "_get_user_id", lambda request_obj: "u1")
    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", config_found)
    monkeypatch.setattr(conversation_router.conversation, "update_title", title_fail)
    response = await conversation_router.update_conversation_title(
        "conv_1",
        conversation_router.UpdateTitleRequest(title="新标题"),
        FakeRequest(),
    )
    assert payload(response)["msg"] == "对话不存在"

    monkeypatch.setattr(conversation_router.conversation, "update_title", title_ok)
    response = await conversation_router.update_conversation_title(
        "conv_1",
        conversation_router.UpdateTitleRequest(title="新标题"),
        FakeRequest(),
    )
    assert payload(response)["data"]["title"] == "新标题"

    async def update_boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(conversation_router.conversation, "update_title", update_boom)
    response = await conversation_router.update_conversation_title(
        "conv_1",
        conversation_router.UpdateTitleRequest(title="新标题"),
        FakeRequest(),
    )
    assert payload(response)["code"] == ErrorCode.CHATDATA_CONVERSATION_TITLE_UPDATE_FAILED

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", config_missing)
    response = await conversation_router.generate_conversation_title("conv_x", FakeRequest())
    assert payload(response)["msg"] == "对话不存在"

    async def gen_title_empty(*_args, **_kwargs):
        return ""

    async def gen_title_ok(*_args, **_kwargs):
        return "自动标题"

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", config_found)
    monkeypatch.setattr(conversation_router.conversation, "generate_title", gen_title_ok)
    response = await conversation_router.generate_conversation_title("conv_1", FakeRequest())
    assert payload(response)["data"]["title"] == "自动标题"

    monkeypatch.setattr(conversation_router.conversation, "generate_title", gen_title_empty)
    response = await conversation_router.generate_conversation_title("conv_1", FakeRequest())
    assert payload(response)["msg"] == "无法生成标题，对话不存在或无消息"


@pytest.mark.asyncio
async def test_toggle_favorite_all_branches(router_state, monkeypatch):
    monkeypatch.setattr(conversation_router, "_get_user_id", lambda request_obj: "u1")

    async def config_found(conversation_id, user_id=None):
        return {"get_checkpointer": None}

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", config_found)

    values = iter([None, True, False])

    async def toggle(conversation_id, user_id):
        return next(values)

    monkeypatch.setattr(conversation_router.conversation, "toggle_favorite", toggle)

    response = await conversation_router.toggle_conversation_favorite("conv_1", FakeRequest())
    assert payload(response)["msg"] == "对话不存在"

    response = await conversation_router.toggle_conversation_favorite("conv_1", FakeRequest())
    assert payload(response)["msg"] == "收藏成功"

    response = await conversation_router.toggle_conversation_favorite("conv_1", FakeRequest())
    assert payload(response)["msg"] == "取消收藏成功"

    async def toggle_boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(conversation_router.conversation, "toggle_favorite", toggle_boom)
    response = await conversation_router.toggle_conversation_favorite("conv_1", FakeRequest())
    assert payload(response)["code"] == ErrorCode.CHATDATA_CONVERSATION_FAVORITE_FAILED

    async def missing_config(conversation_id, user_id=None):
        return None

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", missing_config)
    response = await conversation_router.toggle_conversation_favorite("", FakeRequest())
    assert payload(response)["code"] == ErrorCode.CHATDATA_BAD_REQUEST


@pytest.mark.asyncio
async def test_steer_cancel_pending_question_error_paths(router_state, monkeypatch):
    request = conversation_router.SteerRequest(query="继续")

    async def existing_ok(conversation_id, agent_id, user_id):
        return conversation_router.AGENT_ID

    async def enqueue_ok(*_args, **_kwargs):
        return "message-1"

    async def enqueue_not_active(*_args, **_kwargs):
        raise conversation_router.AgentRunNotActiveError("会话不在运行中")

    async def enqueue_boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(conversation_router, "_get_current_user_identity", identity_sync)
    monkeypatch.setattr(conversation_router, "_get_existing_agent_for_completion", existing_ok)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "enqueue_message", enqueue_ok)
    response = await conversation_router.steer("conv_1", request, FakeRequest())
    assert payload(response)["data"]["message_id"] == "message-1"

    monkeypatch.setattr(conversation_router.agent_inbox_service, "enqueue_message", enqueue_not_active)
    response = await conversation_router.steer("conv_1", request, FakeRequest())
    assert payload(response)["code"] == ErrorCode.CHATDATA_AGENT_RUN_NOT_ACTIVE

    monkeypatch.setattr(conversation_router.agent_inbox_service, "enqueue_message", enqueue_boom)
    response = await conversation_router.steer("conv_1", request, FakeRequest())
    assert payload(response)["code"] == ErrorCode.CHATDATA_STEER_ENQUEUE_FAILED

    async def cancel_ok(**_kwargs):
        return True

    async def cancel_not_found(**_kwargs):
        raise LookupError("对话不存在")

    async def cancel_boom(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(conversation_router.agent_run_runtime, "cancel_run", cancel_ok)
    response = await conversation_router.cancel_active_run("conv_1", FakeRequest(), run_id="run_1")
    assert payload(response)["data"]["cancelled"] is True

    monkeypatch.setattr(conversation_router.agent_run_runtime, "cancel_run", cancel_boom)
    response = await conversation_router.cancel_active_run("conv_1", FakeRequest(), run_id="run_1")
    assert payload(response)["code"] == ErrorCode.CHATDATA_AGENT_RUN_CANCEL_FAILED

    async def pending_ok(conversation_id, user_id):
        return {"question": "Q?"}

    async def pending_not_active(conversation_id, user_id):
        raise conversation_router.AgentRunNotActiveError("nope")

    async def pending_boom(conversation_id, user_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_pending_question", pending_ok)
    response = await conversation_router.get_pending_question("conv_1", FakeRequest())
    assert payload(response)["data"] == {"question": "Q?"}

    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_pending_question", pending_not_active)
    response = await conversation_router.get_pending_question("conv_1", FakeRequest())
    assert payload(response)["code"] == ErrorCode.CHATDATA_NOT_FOUND

    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_pending_question", pending_boom)
    response = await conversation_router.get_pending_question("conv_1", FakeRequest())
    assert payload(response)["code"] == ErrorCode.CHATDATA_QUESTION_ANSWER_FAILED

    async def existing_bad(conversation_id, agent_id, user_id):
        return ""

    monkeypatch.setattr(conversation_router, "_get_existing_agent_for_completion", existing_bad)
    body = payload(await conversation_router.cancel_active_run("conv_1", FakeRequest(), run_id="run_1"))
    assert body["msg"] == "对话不存在"


@pytest.mark.asyncio
async def test_resume_question_full_flow_and_errors(router_state, monkeypatch):
    calls = {}

    async def existing_ok(conversation_id, agent_id, user_id):
        return conversation_router.AGENT_ID

    async def pending_ok(conversation_id, user_id):
        return {"turn_id": "turn_1", "run_id": "run_1"}

    async def pending_none(conversation_id, user_id):
        return None

    async def wait_done(run_id):
        calls["wait_for_done"] = run_id
        return True

    async def wait_not_done(run_id):
        return False

    async def latest_event_id(run_id):
        return "7-0"

    async def claim(*args):
        calls["claim"] = args
        return "run_2", {"resume": True}

    resume_calls = {}

    async def resume_handler(**kwargs):
        resume_calls.update(kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(conversation_router, "_get_current_user_identity", identity_sync)
    monkeypatch.setattr(conversation_router, "_get_existing_agent_for_completion", existing_ok)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_pending_question", pending_ok)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "claim_question_answer", claim)
    monkeypatch.setattr(conversation_router.agent_run_event_service, "wait_for_done", wait_done)
    monkeypatch.setattr(conversation_router.agent_run_event_service, "latest_event_id", latest_event_id)

    import apps.chat_data_app.agent_stream as handlers_mod

    monkeypatch.setattr(handlers_mod, "resume_chat_data_question_handler", resume_handler)

    result = await conversation_router._resume_question(
        "conv_1",
        "q_1",
        {"answers": []},
        FakeRequest(),
    )
    assert result.status_code == 200
    assert calls["wait_for_done"] == "run_1"
    assert resume_calls["turn_id"] == "turn_1"
    assert resume_calls["last_event_id"] == "7-0"
    assert resume_calls["runtime_context"] == {"resume": True}

    async def existing_bad(conversation_id, agent_id, user_id):
        return ""

    monkeypatch.setattr(conversation_router, "_get_existing_agent_for_completion", existing_bad)
    with pytest.raises(conversation_router.AgentQuestionNotFoundError):
        await conversation_router._resume_question("conv_1", "q_1", {}, FakeRequest())

    monkeypatch.setattr(conversation_router, "_get_existing_agent_for_completion", existing_ok)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_pending_question", pending_none)
    with pytest.raises(conversation_router.AgentQuestionNotFoundError):
        await conversation_router._resume_question("conv_1", "q_1", {}, FakeRequest())

    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_pending_question", pending_ok)
    monkeypatch.setattr(conversation_router.agent_run_event_service, "wait_for_done", wait_not_done)
    with pytest.raises(conversation_router.AgentQuestionNotWaitingError):
        await conversation_router._resume_question("conv_1", "q_1", {}, FakeRequest())


@pytest.mark.asyncio
async def test_answer_and_cancel_question_routes(router_state, monkeypatch):
    answer_request = conversation_router.QuestionAnswerRequest(
        answers=[conversation_router.QuestionAnswerItem(id="opt_1", selected=["a"])],
    )

    async def resume_ok(conversation_id, question_id, answer, req):
        assert answer["answers"][0]["id"] == "opt_1"
        return SimpleNamespace(status_code=200)

    async def resume_value_error(*_args, **_kwargs):
        raise ValueError("bad input")

    async def resume_not_found(*_args, **_kwargs):
        raise conversation_router.AgentQuestionNotFoundError("不存在")

    async def resume_not_waiting(*_args, **_kwargs):
        raise conversation_router.AgentQuestionNotWaitingError("收口中")

    def resume_auth_error(*_args, **_kwargs):
        raise AuthenticationError(401, "unauthorized")

    async def resume_boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(conversation_router, "_resume_question", resume_ok)
    response = await conversation_router.answer_question("conv_1", "q_1", answer_request, FakeRequest())
    assert response.status_code == 200

    monkeypatch.setattr(conversation_router, "_resume_question", resume_value_error)
    body = payload(await conversation_router.answer_question("conv_1", "q_1", answer_request, FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    monkeypatch.setattr(conversation_router, "_resume_question", resume_not_found)
    body = payload(await conversation_router.answer_question("conv_1", "q_1", answer_request, FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_QUESTION_NOT_FOUND

    monkeypatch.setattr(conversation_router, "_resume_question", resume_not_waiting)
    body = payload(await conversation_router.answer_question("conv_1", "q_1", answer_request, FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_QUESTION_NOT_WAITING

    monkeypatch.setattr(conversation_router, "_resume_question", resume_auth_error)
    with pytest.raises(HTTPException):
        await conversation_router.answer_question("conv_1", "q_1", answer_request, FakeRequest())

    monkeypatch.setattr(conversation_router, "_resume_question", resume_boom)
    body = payload(await conversation_router.answer_question("conv_1", "q_1", answer_request, FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_QUESTION_ANSWER_FAILED

    async def cancel_ok(conversation_id, question_id, answer, req):
        assert answer["cancelled"] is True
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(conversation_router, "_resume_question", cancel_ok)
    response = await conversation_router.cancel_question("conv_1", "q_1", FakeRequest())
    assert response.status_code == 200

    monkeypatch.setattr(conversation_router, "_resume_question", resume_not_found)
    body = payload(await conversation_router.cancel_question("conv_1", "q_1", FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_QUESTION_NOT_FOUND

    monkeypatch.setattr(conversation_router, "_resume_question", resume_not_waiting)
    body = payload(await conversation_router.cancel_question("conv_1", "q_1", FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_QUESTION_NOT_WAITING

    monkeypatch.setattr(conversation_router, "_resume_question", resume_boom)
    body = payload(await conversation_router.cancel_question("conv_1", "q_1", FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_QUESTION_ANSWER_FAILED


@pytest.mark.asyncio
async def test_list_steer_messages_and_stream_events(router_state, monkeypatch):
    async def queue_ok(conversation_id, user_id):
        return {"has_active_run": True, "messages": []}

    async def queue_not_active(conversation_id, user_id):
        raise conversation_router.AgentRunNotActiveError("no active run")

    async def queue_boom(conversation_id, user_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(conversation_router, "_get_current_user_identity", identity_sync)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "list_message_states", queue_ok)
    response = await conversation_router.list_steer_messages("conv_1", FakeRequest())
    assert payload(response)["data"]["has_active_run"] is True

    monkeypatch.setattr(conversation_router.agent_inbox_service, "list_message_states", queue_not_active)
    body = payload(await conversation_router.list_steer_messages("conv_1", FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_AGENT_RUN_NOT_ACTIVE

    monkeypatch.setattr(conversation_router.agent_inbox_service, "list_message_states", queue_boom)
    body = payload(await conversation_router.list_steer_messages("conv_1", FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_STEER_LIST_FAILED

    async def snapshot_none(conversation_id, user_id, run_id):
        return None

    async def snapshot_ok(conversation_id, user_id, run_id):
        return {"run_id": run_id}

    async def stream_events(*_args, **_kwargs):
        yield {"stream_id": "1-0", "payload": "data: x"}

    async def snapshot_boom(conversation_id, user_id, run_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_run_snapshot", snapshot_none)
    response = await conversation_router.stream_run_events("conv_1", "run_x", FakeRequest())
    assert payload(response)["msg"] == "运行不存在"

    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_run_snapshot", snapshot_ok)
    monkeypatch.setattr(conversation_router.agent_run_event_service, "stream", stream_events)
    response = await conversation_router.stream_run_events(
        "conv_1",
        "run_1",
        FakeRequest(headers={"Last-Event-ID": "3-0"}),
    )
    assert response.media_type == "text/event-stream"
    assert response.headers["x-chatdata-run-id"] == "run_1"
    chunks = [chunk async for chunk in response.body_iterator]
    assert any("id: 1-0" in chunk for chunk in chunks)

    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_run_snapshot", snapshot_boom)
    body = payload(await conversation_router.stream_run_events("conv_1", "run_1", FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_STEER_LIST_FAILED

    monkeypatch.setattr(conversation_router, "_get_current_user_identity", None)
    monkeypatch.setattr(
        conversation_router,
        "_get_current_user_identity",
        lambda *_a: (_ for _ in ()).throw(AuthenticationError(401, "unauthorized")),
    )
    with pytest.raises(HTTPException):
        await conversation_router.list_steer_messages("conv_1", FakeRequest())


@pytest.mark.asyncio
async def test_update_cancel_steer_message_error_matrix(router_state, monkeypatch):
    update_request = conversation_router.SteerMessageUpdateRequest(content="改后的消息")

    errors = {}
    inbox = conversation_router.agent_inbox_service

    async def update_ok(conversation_id, user_id, operator_name, message_id, *, content=None, queue_type=None):
        return {"message_id": message_id}

    async def update_err(conversation_id, user_id, operator_name, message_id, *, content=None, queue_type=None):
        err = errors.get("update")
        if err:
            raise err
        return {"message_id": message_id}

    async def cancel_err(conversation_id, user_id, operator_name, message_id):
        err = errors.get("cancel")
        if err:
            raise err
        return None

    monkeypatch.setattr(conversation_router, "_get_current_user_identity", identity_sync)
    monkeypatch.setattr(inbox, "update_pending_message", update_err)
    monkeypatch.setattr(inbox, "cancel_pending_message", cancel_err)

    error_codes = [
        (conversation_router.SteerMessageNotFoundError("missing"), ErrorCode.CHATDATA_STEER_MESSAGE_NOT_FOUND),
        (conversation_router.SteerMessageNotPendingError("not pending"), ErrorCode.CHATDATA_STEER_MESSAGE_NOT_PENDING),
        (conversation_router.AgentRunNotActiveError("inactive"), ErrorCode.CHATDATA_AGENT_RUN_NOT_ACTIVE),
    ]

    for exc, expected_code in error_codes:
        errors["update"] = exc
        body = payload(await conversation_router.update_steer_message("conv_1", "m_1", update_request, FakeRequest()))
        assert body["code"] == expected_code

        errors["cancel"] = exc
        body = payload(await conversation_router.cancel_steer_message("conv_1", "m_1", FakeRequest()))
        assert body["code"] == expected_code

    errors["update"] = RuntimeError("boom")
    body = payload(await conversation_router.update_steer_message("conv_1", "m_1", update_request, FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_STEER_UPDATE_FAILED

    errors["update"] = AuthenticationError(401, "unauthorized")
    with pytest.raises(HTTPException):
        await conversation_router.update_steer_message("conv_1", "m_1", update_request, FakeRequest())

    errors["cancel"] = RuntimeError("boom")
    body = payload(await conversation_router.cancel_steer_message("conv_1", "m_1", FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_STEER_CANCEL_FAILED

    errors.clear()
    monkeypatch.setattr(inbox, "update_pending_message", update_ok)
    response = await conversation_router.update_steer_message("conv_1", "m_1", update_request, FakeRequest())
    assert payload(response)["data"]["message_id"] == "m_1"

    empty_update = conversation_router.SteerMessageUpdateRequest()
    body = payload(await conversation_router.update_steer_message("conv_1", "m_1", empty_update, FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_REQUIRED_FIELD

    blank_update = conversation_router.SteerMessageUpdateRequest(content="   ")
    body = payload(await conversation_router.update_steer_message("conv_1", "m_1", blank_update, FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_REQUIRED_FIELD


@pytest.mark.asyncio
async def test_get_conversation_context_branches(router_state, monkeypatch):
    monkeypatch.setattr(conversation_router, "_get_user_id", lambda request_obj: "u1")

    body = payload(await conversation_router.get_conversation_context("", FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    async def config_missing(conversation_id, user_id=None):
        return None

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", config_missing)
    body = payload(await conversation_router.get_conversation_context("conv_1", FakeRequest()))
    assert body["msg"] == "对话不存在"

    async def config_ok(conversation_id, user_id=None):
        return {"get_checkpointer": None}

    async def datasource_ok(conversation_id, user_id):
        return "ds_a"

    async def datasource_boom(conversation_id, user_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(conversation_router, "get_agent_config_by_conversation", config_ok)
    monkeypatch.setattr(conversation_router.conversation, "get_datasource_id", datasource_ok)
    body = payload(await conversation_router.get_conversation_context("conv_1", FakeRequest()))
    assert body["data"]["datasource_id"] == "ds_a"

    monkeypatch.setattr(conversation_router.conversation, "get_datasource_id", datasource_boom)
    body = payload(await conversation_router.get_conversation_context("conv_1", FakeRequest()))
    assert body["code"] == ErrorCode.CHATDATA_CONVERSATION_DETAIL_FAILED

    from fastapi.responses import JSONResponse  # noqa: F401  占位确保 fastapi 可用

    monkeypatch.setattr(conversation_router, "_get_user_id", None)
    monkeypatch.setattr(
        conversation_router, "_get_user_id", lambda *_a: (_ for _ in ()).throw(AuthenticationError(401, "unauthorized"))
    )
    with pytest.raises(HTTPException):
        await conversation_router.get_conversation_context("conv_1", FakeRequest())
