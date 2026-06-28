import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent import chat_data_graph, nodes, sql_assist_graph


def test_prompt_formatters():
    assert nodes.format_semantic_config_directory_info_for_llm("  ## 当前可见指标集目录  ") == "\n## 当前可见指标集目录\n"
    assert nodes.format_semantic_config_directory_info_for_llm("") == ""
    assert nodes.format_datasource_group_context_for_llm("  规则说明  ") == "\n## 数据源组智能问数说明\n规则说明\n"
    assert nodes.format_datasource_group_context_for_llm("") == ""
    assert "本轮未提供完整指标集" in nodes.build_semantic_layer_context(None)
    assert "业务数据查询或具体指标/维度询问先搜索当前数据源组中的真实指标集" in nodes.build_semantic_layer_context(None)
    assert "指标集目录或业务范围概览可直接基于“当前可见指标集目录”回答" in nodes.build_semantic_layer_context(None)
    assert "搜索后仍未找到、候选不唯一或口径不清时再澄清" in nodes.build_semantic_layer_context(None)
    assert "订单分析" in nodes.build_semantic_layer_context({"id": "sc_a", "name": "订单分析"})
    assert "semantic_config_id: sc_only" in nodes.build_semantic_layer_context({"id": "sc_only"})
    semantic_context = nodes.build_semantic_layer_context({"id": "sc_only"})
    assert "只有用户本轮显式指定业务知识技能时，才读取对应技能" in semantic_context
    assert "先读取 `custom-query-skill` 主说明" not in semantic_context
    assert "本轮未提供完整指标集" in nodes.build_semantic_layer_context({})
    assert "semantic_config_id" not in nodes.build_semantic_layer_context({"name": "订单分析"})
    assert "当前已选择指标集。" in nodes.build_semantic_layer_context({"id": "", "name": ""})


class FakeLLM:
    def __init__(self, content="完成", fail_once=False, response=None):
        self.content = content
        self.fail_once = fail_once
        self.response = response
        self.calls = 0
        self.bound_tools = None
        self.messages = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        self.messages = messages
        if self.fail_once and self.calls == 1:
            raise RuntimeError("模型不可用")
        if self.response is not None:
            return self.response
        return AIMessage(content=self.content)


@pytest.mark.asyncio
async def test_create_agent_node_uses_semantic_prompt_tools_and_token_usage(monkeypatch):
    fake_llm = FakeLLM("SQL 已生成")
    monkeypatch.setattr(nodes, "get_chat_data_llm", lambda **_kwargs: fake_llm)
    monkeypatch.setattr(nodes, "count_messages_tokens", lambda messages: 10)
    monkeypatch.setattr(nodes, "count_tools_tokens", lambda tools: 5)
    monkeypatch.setattr(nodes, "get_context_window", lambda model_id: 100)
    monkeypatch.setattr(nodes, "build_request_breakdown", lambda **kwargs: {"messages": len(kwargs["messages"]), "tools": len(kwargs["tools"])})
    monkeypatch.setattr(nodes, "build_token_usage_payload", lambda used, total: {"used": used, "total": total})
    monkeypatch.setattr("apps.chat_data_app.agent.tools.build_semantic_prompt", lambda semantic_config, mode: "语义配置内容")

    agent_node = nodes.create_agent_node(
        ["dsl2sql_tool"],
        semantic_config={"id": "sc_a"},
        model_id=None,
        available_skills_context="## 可用业务知识技能\n- custom-query-skill: 自定义问数技能",
    )
    result = await agent_node({
        "messages": [HumanMessage(content="查询销售额")],
        "datasource_group_context": "当前数据源组按执行对象数据源分库\n当前机构A",
        "semantic_config_directory_info": "## 当前可见指标集目录\n1. 名称：订单分析",
    })

    assert result["messages"][0].content == "SQL 已生成"
    assert result["messages"][0].additional_kwargs["timestamp"]
    assert result["token_usage"]["input_tokens"] == 15
    assert result["token_usage"]["context_window"] == 100
    assert fake_llm.bound_tools == ["dsl2sql_tool"]
    system_prompt = fake_llm.messages[0].content
    assert "语义配置内容" in system_prompt
    assert "当前已选择指标集" in system_prompt
    assert "semantic_config_id: sc_a" in system_prompt
    assert "当前机构A" in system_prompt
    assert "数据源组智能问数说明" in system_prompt
    assert "当前数据源组按执行对象数据源分库" in system_prompt
    assert "当前可见指标集目录" in system_prompt
    assert "1. 名称：订单分析" in system_prompt
    assert "custom-query-skill" in system_prompt
    assert "基本边界" in system_prompt
    assert "当前指标集是唯一可执行事实源" in system_prompt
    assert "数据源组智能问数说明是当前数据源组的主要业务上下文" in system_prompt
    assert "业务知识技能默认不加载" in system_prompt
    assert "只有用户本轮显式引用技能" in system_prompt
    assert "禁止再向用户确认是否加载" in system_prompt
    assert "必须先读取 `custom-query-skill` 主说明" not in system_prompt
    assert "必须先读取对应技能补充领域上下文" not in system_prompt
    assert "必须先读取对应技能补充业务判断" not in system_prompt
    assert "最终回答不要提及技能加载过程" in system_prompt


@pytest.mark.asyncio
async def test_create_agent_node_normalizes_repeated_tool_call_name(monkeypatch):
    repeated_tool_message = AIMessage(
        content="",
        tool_calls=[{
            "id": "call_select",
            "name": "select_semantic_config_toolselect_semantic_config_tool",
            "args": {"query": "查询入金金额"},
        }],
    )
    fake_tool = SimpleNamespace(name="select_semantic_config_tool")
    fake_llm = FakeLLM(response=repeated_tool_message)

    monkeypatch.setattr(nodes, "get_chat_data_llm", lambda **_kwargs: fake_llm)
    monkeypatch.setattr(nodes, "count_messages_tokens", lambda messages: 1)
    monkeypatch.setattr(nodes, "count_tools_tokens", lambda tools: 1)
    monkeypatch.setattr(nodes, "get_context_window", lambda model_id: 10)
    monkeypatch.setattr(nodes, "build_request_breakdown", lambda **_kwargs: {})
    monkeypatch.setattr(nodes, "build_token_usage_payload", lambda used, total: {"used": used, "total": total})

    result = await nodes.create_agent_node([fake_tool], semantic_config=None)({
        "messages": [HumanMessage(content="查询入金金额")],
    })

    assert result["messages"][0].tool_calls[0]["name"] == "select_semantic_config_tool"
    assert result["messages"][0].tool_calls[0]["args"] == {"query": "查询入金金额"}


def test_normalize_response_tool_call_names_does_not_guess_unknown_tool():
    unknown_message = AIMessage(
        content="",
        tool_calls=[{
            "id": "call_bad",
            "name": "unknown_toolunknown_tool",
            "args": {},
        }],
    )

    normalized = nodes.normalize_response_tool_call_names(
        unknown_message,
        [SimpleNamespace(name="select_semantic_config_tool")],
    )

    assert normalized.tool_calls[0]["name"] == "unknown_toolunknown_tool"

    repeated_message = AIMessage(
        content="",
        tool_calls=[{
            "id": "call_select",
            "name": "select_semantic_config_toolselect_semantic_config_toolselect_semantic_config_tool",
            "args": {},
        }],
    )
    normalized = nodes.normalize_response_tool_call_names(
        repeated_message,
        [SimpleNamespace(name="select_semantic_config_tool")],
    )
    assert normalized.tool_calls[0]["name"] == "select_semantic_config_tool"

    similar_message = AIMessage(
        content="",
        tool_calls=[{
            "id": "call_similar",
            "name": "select_semantic_config_tool_extra",
            "args": {},
        }],
    )
    normalized = nodes.normalize_response_tool_call_names(
        similar_message,
        [SimpleNamespace(name="select_semantic_config_tool")],
    )
    assert normalized.tool_calls[0]["name"] == "select_semantic_config_tool_extra"


@pytest.mark.asyncio
async def test_create_agent_node_falls_back_when_explicit_model_fails(monkeypatch):
    failed_llm = FakeLLM(fail_once=True)
    fallback_llm = FakeLLM("默认模型完成")
    calls = []

    def fake_get_chat_data_llm(model=None, **_kwargs):
        calls.append(model)
        return failed_llm if model == "bad_model" else fallback_llm

    monkeypatch.setattr(nodes, "get_chat_data_llm", fake_get_chat_data_llm)
    monkeypatch.setattr(nodes, "get_default_chat_data_model", lambda: "default_model")
    monkeypatch.setattr(nodes, "count_messages_tokens", lambda messages: 1)
    monkeypatch.setattr(nodes, "count_tools_tokens", lambda tools: 0)
    monkeypatch.setattr(nodes, "get_context_window", lambda model_id: 10)
    monkeypatch.setattr(nodes, "build_request_breakdown", lambda **_kwargs: {})
    monkeypatch.setattr(nodes, "build_token_usage_payload", lambda used, total: {"used": used, "total": total})
    monkeypatch.setattr("apps.chat_data_app.agent.tools.build_semantic_prompt", lambda semantic_config, mode: "语义配置内容")

    result = await nodes.create_agent_node([], semantic_config={"id": "sc_a"}, model_id="bad_model")({
        "messages": [HumanMessage(content="查询")],
        "mode": "query",
    })

    assert result["messages"][0].content == "默认模型完成"
    assert result["messages"][0].additional_kwargs["timestamp"]
    assert calls == ["bad_model", "default_model"]


@pytest.mark.asyncio
async def test_create_agent_node_explicit_model_fallback_without_tools(monkeypatch):
    failed_llm = FakeLLM(fail_once=True)
    fallback_llm = FakeLLM("默认模型无工具完成")
    calls = []

    def fake_get_chat_data_llm(model=None, **_kwargs):
        calls.append(model)
        return failed_llm if model == "bad_model" else fallback_llm

    monkeypatch.setattr(nodes, "get_chat_data_llm", fake_get_chat_data_llm)
    monkeypatch.setattr(nodes, "get_default_chat_data_model", lambda: "default_model")
    monkeypatch.setattr(nodes, "count_messages_tokens", lambda messages: 1)
    monkeypatch.setattr(nodes, "count_tools_tokens", lambda tools: 0)
    monkeypatch.setattr(nodes, "get_context_window", lambda model_id: 10)
    monkeypatch.setattr(nodes, "build_request_breakdown", lambda **_kwargs: {})
    monkeypatch.setattr(nodes, "build_token_usage_payload", lambda used, total: {"used": used, "total": total})

    result = await nodes.create_agent_node([], semantic_config=None, model_id="bad_model")({
        "messages": [HumanMessage(content="查询")],
    })

    assert result["messages"][0].content == "默认模型无工具完成"
    assert calls == ["bad_model", "default_model"]
    assert fallback_llm.bound_tools is None


@pytest.mark.asyncio
async def test_create_agent_node_explicit_model_fallback_with_tools(monkeypatch):
    failed_llm = FakeLLM(fail_once=True)
    fallback_llm = FakeLLM("默认模型工具完成")
    calls = []

    def fake_get_chat_data_llm(model=None, **_kwargs):
        calls.append(model)
        return failed_llm if model == "bad_model" else fallback_llm

    monkeypatch.setattr(nodes, "get_chat_data_llm", fake_get_chat_data_llm)
    monkeypatch.setattr(nodes, "get_default_chat_data_model", lambda: "default_model")
    monkeypatch.setattr(nodes, "count_messages_tokens", lambda messages: 1)
    monkeypatch.setattr(nodes, "count_tools_tokens", lambda tools: 1)
    monkeypatch.setattr(nodes, "get_context_window", lambda model_id: 10)
    monkeypatch.setattr(nodes, "build_request_breakdown", lambda **_kwargs: {})
    monkeypatch.setattr(nodes, "build_token_usage_payload", lambda used, total: {"used": used, "total": total})
    monkeypatch.setattr("apps.chat_data_app.agent.tools.build_semantic_prompt", lambda semantic_config, mode: "语义配置内容")

    result = await nodes.create_agent_node(["dsl2sql_tool"], semantic_config={"id": "sc_a"}, model_id="bad_model")({
        "messages": [HumanMessage(content="查询")],
    })

    assert result["messages"][0].content == "默认模型工具完成"
    assert calls == ["bad_model", "default_model"]
    assert fallback_llm.bound_tools == ["dsl2sql_tool"]


@pytest.mark.asyncio
async def test_create_agent_node_no_model_reraises_and_no_tools(monkeypatch):
    class AlwaysFailLLM(FakeLLM):
        async def ainvoke(self, messages):
            raise RuntimeError("model down")

    monkeypatch.setattr(nodes, "get_chat_data_llm", lambda model=None, **_kwargs: AlwaysFailLLM())
    monkeypatch.setattr(nodes, "count_messages_tokens", lambda messages: 1)
    monkeypatch.setattr(nodes, "count_tools_tokens", lambda tools: 0)
    monkeypatch.setattr(nodes, "get_context_window", lambda model_id: 10)
    monkeypatch.setattr(nodes, "build_request_breakdown", lambda **_kwargs: {})
    monkeypatch.setattr(nodes, "build_token_usage_payload", lambda used, total: {"used": used, "total": total})

    with pytest.raises(RuntimeError, match="model down"):
        await nodes.create_agent_node([], semantic_config=None, model_id=None)({
            "messages": [HumanMessage(content="查询")],
        })

    fake_llm = FakeLLM("无工具调用")
    monkeypatch.setattr(nodes, "get_chat_data_llm", lambda model=None, **_kwargs: fake_llm)
    result = await nodes.create_agent_node([], semantic_config=None, model_id=None)({
        "messages": [HumanMessage(content="查询")],
    })
    assert result["messages"][0].content == "无工具调用"
    assert fake_llm.bound_tools is None


def test_chat_data_graph_tool_error_and_compile(monkeypatch):
    errors = []
    monkeypatch.setattr(chat_data_graph.logger, "error", lambda message, **_kwargs: errors.append(message))

    assert chat_data_graph._format_tool_error(ValueError("")) == "ValueError('')"
    assert errors

    captured = {}

    class FakeStateGraph:
        def __init__(self, state_type):
            captured["state_type"] = state_type
            self.nodes = []
            self.edges = []

        def add_node(self, name, node):
            self.nodes.append((name, node))

        def add_edge(self, source, target):
            self.edges.append((source, target))

        def add_conditional_edges(self, source, condition):
            captured["conditional"] = (source, condition)

        def compile(self, checkpointer=None):
            return {"nodes": self.nodes, "edges": self.edges, "checkpointer": checkpointer}

    class FakeToolNode:
        def __init__(self, tools, handle_tool_errors=None):
            self.tools = tools
            self.handle_tool_errors = handle_tool_errors

    monkeypatch.setattr(chat_data_graph, "StateGraph", FakeStateGraph)
    monkeypatch.setattr(chat_data_graph, "ToolNode", FakeToolNode)
    monkeypatch.setattr(chat_data_graph, "START", "__start__")
    monkeypatch.setattr(chat_data_graph, "TOOLS", ["semantic_query_tool"])
    selection_tool_kwargs = {}

    def fake_create_semantic_config_selection_tools(model_id=None, semantic_config=None, user_id=None):
        selection_tool_kwargs.update({"model_id": model_id, "semantic_config": semantic_config, "user_id": user_id})
        return ["select_semantic_config_tool", "dsl2sql_tool"]

    monkeypatch.setattr(chat_data_graph, "create_semantic_config_selection_tools", fake_create_semantic_config_selection_tools)
    monkeypatch.setattr(
        chat_data_graph,
        "create_agent_node",
        lambda tools, semantic_config=None, model_id=None, enable_thinking=None, available_skills_context=None: (
            "agent",
            tools,
            semantic_config,
            model_id,
            enable_thinking,
            available_skills_context,
        ),
    )

    compiled = chat_data_graph.get_chat_data_graph(checkpointer="ckpt", semantic_config={"id": "sc_a"}, model_id="m1", user_id="u1")

    assert compiled["nodes"][0][0] == "agent"
    assert selection_tool_kwargs == {"model_id": "m1", "semantic_config": {"id": "sc_a"}, "user_id": "u1"}
    tool_names = [getattr(item, "name", item) for item in compiled["nodes"][0][1][1]]
    assert tool_names == ["skill_load", "select_semantic_config_tool", "dsl2sql_tool", "semantic_query_tool"]
    assert compiled["edges"] == [("__start__", "agent"), ("tools", "agent")]
    assert compiled["checkpointer"] == "ckpt"

    assert chat_data_graph._format_tool_error(RuntimeError("boom")) == "boom"


@pytest.mark.asyncio
async def test_sql_assist_node_and_graph(monkeypatch):
    fake_llm = FakeLLM("select 1")
    monkeypatch.setattr(sql_assist_graph, "get_chat_data_llm", lambda streaming=True: fake_llm)

    node = sql_assist_graph.create_sql_assist_node("Hive")
    result = await node({"messages": [HumanMessage(content="帮我写SQL")], "tables_ddl_info": "CREATE TABLE orders(id int)"})

    assert result["messages"][0].content == "select 1"
    system_prompt = fake_llm.messages[0].content
    assert "CREATE TABLE orders" in system_prompt
    assert "机构A" not in system_prompt
