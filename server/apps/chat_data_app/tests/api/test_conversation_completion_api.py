import asyncio
import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from apps.chat_data_app import routes
from apps.chat_data_app.agent import nodes
from apps.chat_data_app.services import semantic_builder_compiler, semantic_tools
from apps.chat_data_app.tests.api.conftest import FakeDatasourceManager
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
    yield None


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


class ToolCallingLLM:
    def __init__(self, tool_args):
        self.tool_args = tool_args
        self.calls = 0
        self.bound_tools = None

    def bind_tools(self, tools):
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
        {"name": "订单状态", "column": "status", "table": "orders", "type": "categorical"},
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
    }
    fake_llm = ToolCallingLLM(tool_args)

    async def fake_get_agent_id(_db_path, conversation_id, user_id=None):
        return "chat_data" if conversation_id.startswith("conv_") else None

    async def fake_get_or_create_conversation(_db_path, agent_id, conversation_id, user_id):
        return conversation_id

    handler_module = sys.modules[conversation_router._completion_handlers["chat_data"].__module__]
    monkeypatch.setattr(conversation_router.conversation, "get_agent_id", fake_get_agent_id)
    monkeypatch.setattr(handler_module.conversation, "get_or_create_conversation", fake_get_or_create_conversation)
    monkeypatch.setattr(handler_module, "get_checkpointer", lambda: fake_checkpointer())
    monkeypatch.setattr(handler_module, "_build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))
    monkeypatch.setattr(handler_module, "_build_query_skills_context", lambda **_kwargs: asyncio.sleep(0, result=""))
    monkeypatch.setattr(handler_module, "get_datasource_manager", lambda: manager)
    for module in (routes, semantic_builder_compiler, semantic_tools):
        monkeypatch.setattr(module, "get_datasource_manager", lambda: manager)
    for module_name in (
        "apps.chat_data_app.agent.nodes",
        "server.apps.chat_data_app.agent.nodes",
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            monkeypatch.setattr(module, "get_chat_data_llm", lambda **_kwargs: fake_llm)

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
