import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from apps.chat_data_app.agent.tools import dsl2sql
from apps.chat_data_app.services import semantic_builder_compiler, semantic_conversation_context, semantic_tools
from apps.chat_data_app.tests.api.conftest import FakeDatasourceManager
from apps.chat_data_app.routers import conversations as conversation_router


@pytest.fixture(autouse=True)
def _stub_conversation_event_writes(monkeypatch):
    handler_module = sys.modules[conversation_router._completion_handlers["chat_data"].__module__]

    async def noop_event_write(**_kwargs):
        return None

    monkeypatch.setattr(handler_module.conversation_event_service, "persist_user_message", noop_event_write)
    monkeypatch.setattr(handler_module.conversation_event_service, "persist_turn", noop_event_write)


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
    yield InMemorySaver()


def _patch_completion_dependencies(monkeypatch, graph):
    handler_module = sys.modules[conversation_router._completion_handlers["chat_data"].__module__]

    async def fake_get_agent_id(_db_path, conversation_id, user_id=None):
        return "chat_data" if conversation_id.startswith("conv_") else None

    async def fake_get_or_create_conversation(_db_path, agent_id, conversation_id, user_id):
        return conversation_id

    async def fake_get_datasource_id(_conversation_id, _user_id):
        return None

    async def fake_resolve_datasource_binding(_conversation_id, _user_id, requested_datasource_id):
        return requested_datasource_id

    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", fake_get_agent_id)
    monkeypatch.setattr(conversation_router.conversation, "get_datasource_id", fake_get_datasource_id)
    monkeypatch.setattr(handler_module.conversation, "resolve_datasource_binding", fake_resolve_datasource_binding)
    monkeypatch.setattr(handler_module.conversation, "get_or_create_conversation", fake_get_or_create_conversation)
    monkeypatch.setattr(handler_module, "get_checkpointer", lambda: fake_checkpointer())
    monkeypatch.setattr(handler_module, "create_data_agent", lambda *_args, **_kwargs: graph)


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


def _tool_event_contents(stream_text: str) -> list[str]:
    """解析 SSE 工具事件中的原始工具结果。"""
    contents = []
    for event in stream_text.split("\n\n"):
        if not event.startswith("event: tool\n"):
            continue
        payload = json.loads(event.split("data: ", 1)[1])
        contents.append(str(payload.get("content") or ""))
    return contents


def test_completion_api_streams_multi_semantic_retrieval_tool_result(client, auth_headers, monkeypatch):
    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order", "cfg_user"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析", "metrics": [{"name": "订单金额"}]},
            "cfg_user": {"id": "cfg_user", "name": "用户分析", "metrics": [{"name": "用户数"}]},
        },
    }
    graph = FakeGraph([
        (
            FakeMessageChunk(
                tool_calls=[{"id": "call_select", "name": "search_dataset"}],
            ),
            {"langgraph_node": "model"},
        ),
        (
            FakeMessageChunk(
                tool_call_chunks=[
                    {
                        "id": "call_select",
                        "name": "search_dataset",
                        "args": json.dumps({"query": "查询订单金额和用户数"}, ensure_ascii=False),
                    }
                ],
            ),
            {"langgraph_node": "model"},
        ),
        (
            FakeMessageChunk(
                content="[search_dataset]\n" + json.dumps(payload, ensure_ascii=False),
                msg_type="tool",
                tool_call_id="call_select",
                name="search_dataset",
            ),
            {"langgraph_node": "tools"},
        ),
        (
            FakeMessageChunk(content="已分别查询订单金额和用户数。"),
            {"langgraph_node": "model"},
        ),
    ])
    _patch_completion_dependencies(monkeypatch, graph)

    response = _post_completion(client, auth_headers, "conv_retrieval_api", "查询订单金额和用户数")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    assert "event: semantic_config_select_required" not in text
    assert "event: tool" in text
    assert '"semantic_config_ids": ["cfg_order", "cfg_user"]' in _tool_event_contents(text)[-1]
    assert "query_plan" not in text
    assert "cfg_order" in text
    assert "cfg_user" in text
    assert "已分别查询订单金额和用户数。" in text
    assert "event: message_end" in text
    assert text.rstrip().endswith("data: [DONE]")

    assert "mode" not in graph.initial_state
    assert graph.initial_state["execution_context"]["datasource_id"] == "ds_a"
    assert graph.initial_state["messages"][0].additional_kwargs["timestamp"]


def test_completion_api_streams_single_semantic_retrieval_tool_result(client, auth_headers, monkeypatch):
    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析", "metrics": [{"name": "订单金额", "expression": "SUM(orders.amount)"}]},
        },
    }
    graph = FakeGraph([
        (
            FakeMessageChunk(tool_calls=[{"id": "call_select", "name": "search_dataset"}]),
            {"langgraph_node": "model"},
        ),
        (
            FakeMessageChunk(
                content="[search_dataset]\n" + json.dumps(payload, ensure_ascii=False),
                msg_type="tool",
                tool_call_id="call_select",
                name="search_dataset",
            ),
            {"langgraph_node": "tools"},
        ),
    ])
    _patch_completion_dependencies(monkeypatch, graph)

    response = _post_completion(client, auth_headers, "conv_selected_api", "查询订单金额")

    assert response.status_code == 200
    text = response.text
    assert "event: tool" in text
    assert '"semantic_config_ids": ["cfg_order"]' in _tool_event_contents(text)[-1]
    assert '"expression": "SUM(orders.amount)"' in _tool_event_contents(text)[-1]
    assert "event: semantic_config_selected" not in text
    assert "event: semantic_config_select_required" not in text


def test_completion_api_passes_explicit_skill_names_to_query_state(client, auth_headers, monkeypatch):
    graph = FakeGraph([
        (
            FakeMessageChunk(content="完成"),
            {"langgraph_node": "model"},
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
            "skill_names": ["custom-query-skill", "custom-query-skill", "risk-query-skill"],
        },
    )

    assert response.status_code == 200
    assert "完成" in response.text
    human_message = graph.initial_state["messages"][0]
    assert human_message.additional_kwargs["skill_names"] == ["custom-query-skill", "risk-query-skill"]


class ToolCallingLLM:
    def __init__(self, tool_args):
        self.tool_args = tool_args
        self.calls = 0
        self.bound_tools = None

    def bind_tools(self, tools, **_kwargs):
        self.bound_tools = tools
        return self

    async def ainvoke(self, _messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_dsl",
                    "name": "dsl2sql_tool",
                    "args": self.tool_args,
                }],
            )
        return AIMessage(content="已生成 SQL。")


def test_completion_api_runs_real_dsl2sql_tool_with_structured_having(client, auth_headers, monkeypatch):
    manager = FakeDatasourceManager()
    datasource = manager.datasources["ds_a"]
    datasource["ds_tables"].append({
        "table_name": "order_items",
        "description": "订单明细",
        "columns": [
            {"name": "order_id", "type": "int"},
            {"name": "amount", "type": "decimal"},
        ],
    })
    config = datasource["semantic_configs"][0]
    config["metrics"] = [
        {"name": "出金总额", "expression": "SUM(orders.out_amount)", "table": "orders"},
        {"name": "销售额", "expression": "SUM(order_items.amount)", "table": "order_items"},
    ]
    config["dimensions"] = [
        {"name": "订单状态", "column": "status", "table": "orders", "logical_type": "text"},
    ]
    config["relationships"] = [
        {
            "left_table": "orders",
            "right_table": "order_items",
            "join_type": "left",
            "join_conditions": [{"left_column": "order_id", "right_column": "order_id"}],
            "relationship_type": "one_to_many",
        }
    ]
    tool_args = {
        "semantic_config_id": "cfg_a",
        "dsl": {
            "metrics": ["出金总额", "销售额"],
            "dimensions": ["订单状态"],
            "filters": [],
            "having": [{
                "left": {"type": "metric", "name": "出金总额"},
                "operator": ">",
                "right": {
                    "type": "binary",
                    "operator": "*",
                    "left": {"type": "metric", "name": "销售额"},
                    "right": {"type": "literal", "value": 10},
                },
            }],
            "order_by": [],
        },
    }
    fake_llm = ToolCallingLLM(tool_args)

    async def fake_get_agent_id(_db_path, conversation_id, user_id=None):
        return "chat_data" if conversation_id.startswith("conv_") else None

    async def fake_get_or_create_conversation(_db_path, agent_id, conversation_id, user_id):
        return conversation_id

    async def fake_get_datasource_id(_conversation_id, _user_id):
        return None

    async def fake_resolve_datasource_binding(_conversation_id, _user_id, requested_datasource_id):
        return requested_datasource_id

    handler_module = sys.modules[conversation_router._completion_handlers["chat_data"].__module__]
    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", fake_get_agent_id)
    monkeypatch.setattr(conversation_router.conversation, "get_datasource_id", fake_get_datasource_id)
    monkeypatch.setattr(handler_module.conversation, "resolve_datasource_binding", fake_resolve_datasource_binding)
    monkeypatch.setattr(handler_module.conversation, "get_or_create_conversation", fake_get_or_create_conversation)
    monkeypatch.setattr(handler_module, "get_checkpointer", lambda: fake_checkpointer())
    for module in (semantic_builder_compiler, semantic_conversation_context, semantic_tools):
        monkeypatch.setattr(module, "get_datasource_manager", lambda: manager)

    async def fake_get_dashboard_spec_semantic_config(*_args, **_kwargs):
        return dict(config)

    monkeypatch.setattr(dsl2sql, "get_dashboard_spec_semantic_config", fake_get_dashboard_spec_semantic_config)
    from apps.chat_data_app.agent import data_agent

    monkeypatch.setattr(data_agent, "create_streaming_chat_model", lambda *_args, **_kwargs: fake_llm)

    response = _post_completion(
        client,
        auth_headers,
        "conv_real_dsl_tool_api",
        "按订单状态查询出金总额大于销售额十倍的情况",
        options={"mode": "query", "datasource_id": "ds_a", "semantic_config_id": "cfg_a"},
    )

    assert response.status_code == 200
    text = response.text
    assert "event: tool" in text
    assert "dsl2sql_tool" in text
    assert "LEFT JOIN order_items" in text
    assert "HAVING SUM(orders.out_amount) > (SUM(order_items.amount) * 10)" in text
    assert "已生成 SQL。" in text
    assert fake_llm.calls == 2
