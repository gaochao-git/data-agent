import asyncio
import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

from utils.lang_util import conversation_router


class FakeMessageChunk:
    def __init__(
        self,
        *,
        content="",
        msg_type="ai",
        tool_calls=None,
        tool_call_chunks=None,
        tool_call_id="",
        name="",
    ):
        self.content = content
        self.type = msg_type
        self.tool_calls = tool_calls or []
        self.tool_call_chunks = tool_call_chunks or []
        self.tool_call_id = tool_call_id
        self.name = name

    def model_dump(self):
        data = {"content": self.content, "type": self.type}
        if self.tool_calls:
            data["tool_calls"] = self.tool_calls
        if self.tool_call_chunks:
            data["tool_call_chunks"] = self.tool_call_chunks
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        return data


class FakeGraph:
    def __init__(self, events):
        self.events = events
        self.initial_state = None

    async def astream(self, initial_state, config=None, stream_mode=None):
        self.initial_state = initial_state
        for event in self.events:
            yield event

    async def aget_state(self, _config):
        return SimpleNamespace(tasks=[], values={"token_usage": {"total_tokens": 7}})


@asynccontextmanager
async def fake_checkpointer():
    yield object()


def _patch_completion_dependencies(monkeypatch, graph):
    handler_module = sys.modules[conversation_router._completion_handlers["chat_data"].__module__]

    async def fake_get_agent_id(_db_path, conversation_id, user_id=None):
        return "chat_data" if conversation_id.startswith("conv_") else None

    async def fake_get_or_create_conversation(_db_path, agent_id, conversation_id, user_id):
        return conversation_id

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", fake_get_agent_id)
    monkeypatch.setattr(handler_module.conversation, "get_or_create_conversation", fake_get_or_create_conversation)
    monkeypatch.setattr(handler_module, "get_checkpointer", lambda: fake_checkpointer())
    monkeypatch.setattr(handler_module, "get_chat_data_graph", lambda **_kwargs: graph)
    monkeypatch.setattr(
        handler_module,
        "_build_execution_target_info_async",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=""),
    )
    monkeypatch.setattr(
        handler_module,
        "build_available_skills_prompt",
        lambda **_kwargs: asyncio.sleep(0, result="## 可用业务知识技能\n- business-query"),
    )


def _post_completion(client, auth_headers, conversation_id, query, options=None):
    return client.post(
        f"/api/chatdata/conversations/{conversation_id}/completion",
        json={
            "agent_id": "chat_data",
            "query": query,
            "options": options or {"mode": "query", "datasource_id": "ds_a"},
        },
        headers=auth_headers,
    )


def test_completion_api_streams_multi_config_query_plan_without_select_required(client, auth_headers, monkeypatch):
    payload = {
        "selected": False,
        "coverage": "partial",
        "query_plan": {
            "type": "multi_config_independent",
            "tasks": [
                {"task_id": "order", "semantic_config_id": "cfg_order", "question": "查询订单金额"},
                {"task_id": "user", "semantic_config_id": "cfg_user", "question": "查询用户数"},
            ],
            "semantic_configs": {
                "cfg_order": {"id": "cfg_order", "name": "订单分析"},
                "cfg_user": {"id": "cfg_user", "name": "用户分析"},
            },
            "final_action": "summarize",
        },
        "message": "该问题需要拆分为多个指标集子任务",
    }
    graph = FakeGraph([
        (
            FakeMessageChunk(
                tool_calls=[{"id": "call_select", "name": "select_semantic_config_tool"}],
            ),
            {"langgraph_node": "agent"},
        ),
        (
            FakeMessageChunk(
                tool_call_chunks=[
                    {
                        "id": "call_select",
                        "name": "select_semantic_config_tool",
                        "args": json.dumps({"query": "查询订单金额和用户数"}, ensure_ascii=False),
                    }
                ],
            ),
            {"langgraph_node": "agent"},
        ),
        (
            FakeMessageChunk(
                content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False),
                msg_type="tool",
                tool_call_id="call_select",
                name="select_semantic_config_tool",
            ),
            {"langgraph_node": "tools"},
        ),
        (
            FakeMessageChunk(content="已分别查询订单金额和用户数。"),
            {"langgraph_node": "agent"},
        ),
    ])
    _patch_completion_dependencies(monkeypatch, graph)

    response = _post_completion(client, auth_headers, "conv_query_plan_api", "查询订单金额和用户数")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    assert "event: semantic_config_select_required" not in text
    assert "event: tool" in text
    assert "multi_config_independent" in text
    assert "cfg_order" in text
    assert "cfg_user" in text
    assert "已分别查询订单金额和用户数。" in text
    assert "event: message_end" in text
    assert text.rstrip().endswith("data: [DONE]")

    assert graph.initial_state["mode"] == "query"
    assert graph.initial_state["datasource_id"] == "ds_a"
    assert graph.initial_state["messages"][0].additional_kwargs["timestamp"]


def test_completion_api_streams_single_selected_config_event(client, auth_headers, monkeypatch):
    payload = {
        "selected": True,
        "selected_config_id": "cfg_order",
        "selected_config_name": "订单分析",
        "coverage": "full",
        "confidence": "high",
        "reason": "命中订单金额",
        "matched_metrics": ["订单金额"],
        "matched_dimensions": ["机构"],
    }
    graph = FakeGraph([
        (
            FakeMessageChunk(tool_calls=[{"id": "call_select", "name": "select_semantic_config_tool"}]),
            {"langgraph_node": "agent"},
        ),
        (
            FakeMessageChunk(
                content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False),
                msg_type="tool",
                tool_call_id="call_select",
                name="select_semantic_config_tool",
            ),
            {"langgraph_node": "tools"},
        ),
    ])
    _patch_completion_dependencies(monkeypatch, graph)

    response = _post_completion(client, auth_headers, "conv_selected_api", "查询订单金额")

    assert response.status_code == 200
    text = response.text
    assert "event: semantic_config_selected" in text
    assert '"semantic_config_id": "cfg_order"' in text
    assert "event: semantic_config_select_required" not in text


def test_completion_api_passes_explicit_skill_names_to_query_state(client, auth_headers, monkeypatch):
    graph = FakeGraph([
        (
            FakeMessageChunk(content="完成"),
            {"langgraph_node": "agent"},
        ),
    ])
    _patch_completion_dependencies(monkeypatch, graph)

    response = _post_completion(
        client,
        auth_headers,
        "conv_skill_api",
        "查询订单金额",
        options={
            "mode": "query",
            "datasource_id": "ds_a",
            "skill_names": ["business-query", "business-query", "custom-query"],
        },
    )

    assert response.status_code == 200
    assert "完成" in response.text
    human_message = graph.initial_state["messages"][0]
    assert human_message.additional_kwargs["skill_names"] == ["business-query", "custom-query"]
