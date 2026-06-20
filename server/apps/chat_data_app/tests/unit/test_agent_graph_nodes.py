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

from apps.chat_data_app.agent import chat_data_graph, config, nodes, sql_assist_graph


def test_mode_config_and_prompt_formatters():
    generate_config = config.get_mode_config(has_semantic=False, mode="generate")
    query_config = config.get_mode_config(has_semantic=True, mode="query")

    assert generate_config.uses_tools is False
    assert query_config.uses_tools is True
    auto_query_config = config.get_mode_config(has_semantic=False, mode="query")
    assert auto_query_config.uses_tools is True
    assert auto_query_config.base_prompt == query_config.base_prompt
    assert config.filter_tools_by_config(["tool"], generate_config) == []
    assert config.filter_tools_by_config(["tool"], query_config) == ["tool"]
    assert config.filter_tools_by_config(["tool"], auto_query_config) == ["tool"]
    with pytest.raises(ValueError, match="不支持的模式配置"):
        config.get_mode_config(has_semantic=False, mode="missing")

    result_text = nodes.format_query_results_for_llm([
        {"id": "result_1", "sql": "select " + "x" * 120, "columns": ["订单ID", "销售额"], "row_count": 10},
    ])
    assert "result_id: `result_1`" in result_text
    assert "..." in result_text
    assert "列: 订单ID, 销售额" in result_text
    assert nodes.format_query_results_for_llm([]) == ""
    assert nodes.format_execution_target_info_for_llm("  机构A  ") == "\n机构A\n"
    assert nodes.format_execution_target_info_for_llm("") == ""
    assert nodes.build_semantic_layer_context({"id": "sc_a"}, "generate") == ""
    assert "当前未选择指标集" in nodes.build_semantic_layer_context(None, "query")
    assert "必须先调用 `skill_load` 加载路由知识" in nodes.build_semantic_layer_context(None, "query")
    assert "没有明显同域技能时直接调用 `select_semantic_config_tool`" in nodes.build_semantic_layer_context(None, "query")
    assert "订单分析" in nodes.build_semantic_layer_context({"id": "sc_a", "name": "订单分析"}, "query")
    assert "semantic_config_id: sc_only" in nodes.build_semantic_layer_context({"id": "sc_only"}, "query")
    assert "必须先调用 `skill_load` 加载主说明" in nodes.build_semantic_layer_context({"id": "sc_only"}, "query")
    assert "当前未选择指标集" in nodes.build_semantic_layer_context({}, "query")
    assert "semantic_config_id" not in nodes.build_semantic_layer_context({"name": "订单分析"}, "query")
    assert "当前已选择指标集。" in nodes.build_semantic_layer_context({"id": "", "name": ""}, "query")


class FakeLLM:
    def __init__(self, content="完成", fail_once=False):
        self.content = content
        self.fail_once = fail_once
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
        available_skills_context="## 可用业务知识技能\n- business-query: 业务问数知识技能",
    )
    result = await agent_node({
        "messages": [HumanMessage(content="查询销售额")],
        "tables_ddl_info": "CREATE TABLE orders(id int)",
        "query_results": [{"id": "r1", "sql": "select 1", "columns": ["c1"], "row_count": 1}],
        "mode": "query",
        "execution_target_info": "当前机构A",
    })

    assert result["messages"][0].content == "SQL 已生成"
    assert result["token_usage"]["input_tokens"] == 15
    assert result["token_usage"]["context_window"] == 100
    assert fake_llm.bound_tools == ["dsl2sql_tool"]
    system_prompt = fake_llm.messages[0].content
    assert "语义配置内容" in system_prompt
    assert "当前已选择指标集" in system_prompt
    assert "semantic_config_id: sc_a" in system_prompt
    assert "当前机构A" in system_prompt
    assert "可用的查询结果" in system_prompt
    assert "business-query" in system_prompt
    assert "先调用 `skill_load` 读取该技能主说明" in system_prompt
    assert "再调用 `select_semantic_config_tool`" in system_prompt
    assert "必须在生成 DSL 前调用 `skill_load`" in system_prompt
    assert "没有明显匹配时不要硬加载" in system_prompt
    assert "四层职责边界" in system_prompt
    assert "Workflow 技能只用于看板、图表、智能分析等复杂产物任务" in system_prompt
    assert "必须先调用 `skill_load` 加载主说明" in system_prompt
    assert "最终回答不要提及技能加载过程" in system_prompt


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
    monkeypatch.setattr(nodes, "filter_tools_by_config", lambda tools, config: [])
    monkeypatch.setattr(nodes, "count_messages_tokens", lambda messages: 1)
    monkeypatch.setattr(nodes, "count_tools_tokens", lambda tools: 0)
    monkeypatch.setattr(nodes, "get_context_window", lambda model_id: 10)
    monkeypatch.setattr(nodes, "build_request_breakdown", lambda **_kwargs: {})
    monkeypatch.setattr(nodes, "build_token_usage_payload", lambda used, total: {"used": used, "total": total})

    result = await nodes.create_agent_node(["dsl2sql_tool"], semantic_config=None, model_id="bad_model")({
        "messages": [HumanMessage(content="查询")],
        "mode": "query",
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
        "mode": "query",
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
            "mode": "query",
        })

    fake_llm = FakeLLM("无工具调用")
    monkeypatch.setattr(nodes, "get_chat_data_llm", lambda model=None, **_kwargs: fake_llm)
    result = await nodes.create_agent_node([], semantic_config=None, model_id=None)({
        "messages": [HumanMessage(content="查询")],
        "mode": "query",
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
    monkeypatch.setattr(chat_data_graph, "create_semantic_config_selection_tools", lambda model_id=None, semantic_config=None: ["select_semantic_config_tool", "dsl2sql_tool"])
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

    compiled = chat_data_graph.get_chat_data_graph(checkpointer="ckpt", semantic_config={"id": "sc_a"}, model_id="m1")

    assert compiled["nodes"][0][0] == "agent"
    tool_names = [getattr(item, "name", item) for item in compiled["nodes"][0][1][1]]
    assert tool_names == ["skill_load", "select_semantic_config_tool", "dsl2sql_tool", "semantic_query_tool"]
    assert compiled["edges"] == [("__start__", "agent"), ("tools", "agent")]
    assert compiled["checkpointer"] == "ckpt"

    assert chat_data_graph._format_tool_error(RuntimeError("boom")) == "boom"


@pytest.mark.asyncio
async def test_sql_assist_node_and_graph(monkeypatch):
    fake_llm = FakeLLM("select 1")
    monkeypatch.setattr(sql_assist_graph, "get_chat_data_llm", lambda streaming=True: fake_llm)

    node = sql_assist_graph.create_sql_assist_node("Hive", "机构A")
    result = await node({"messages": [HumanMessage(content="帮我写SQL")], "tables_ddl_info": "CREATE TABLE orders(id int)"})

    assert result["messages"][0].content == "select 1"
    system_prompt = fake_llm.messages[0].content
    assert "CREATE TABLE orders" in system_prompt
    assert "机构A" in system_prompt
