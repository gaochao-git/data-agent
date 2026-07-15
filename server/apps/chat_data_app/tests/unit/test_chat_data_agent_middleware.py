import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent import chat_data_agent, sql_assist_agent
from apps.chat_data_app.agent.agent_runtime import format_current_time_for_prompt, normalize_response_tool_call_names
from apps.chat_data_app.agent.middleware import context_usage
from apps.chat_data_app.agent.middleware import tool_error
from apps.chat_data_app.agent.prompts import chat_data_builder
from apps.chat_data_app.services.context_budget import ContextBudget


class FakeChatModel:
    def __init__(self, model_name: str = "fake-model"):
        self.model_name = model_name

    def bind_tools(self, _tools):
        return self


class FakeLLM:
    def __init__(self, content="完成"):
        self.content = content
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return AIMessage(content=self.content)


def test_prompt_formatters(monkeypatch):
    assert chat_data_builder.format_semantic_config_directory_info_for_llm("  ## 当前可见指标集目录  ") == "\n## 当前可见指标集目录\n"
    assert chat_data_builder.format_semantic_config_directory_info_for_llm("") == ""
    assert chat_data_builder.format_datasource_group_context_for_llm("  规则说明  ") == "\n## 数据源组智能问数说明\n规则说明\n"
    assert chat_data_builder.format_datasource_group_context_for_llm("") == ""
    assert format_current_time_for_prompt(datetime(2026, 6, 24, 10, 0, 0)) == "2026-06-24 10:00:00（周三）"
    assert "本轮未提供完整指标集" in chat_data_builder.build_semantic_layer_context(None)
    assert "业务数据查询或具体指标/维度询问先搜索当前数据源组中的真实指标集" in chat_data_builder.build_semantic_layer_context(None)
    assert "指标集目录或业务范围概览可直接基于“当前可见指标集目录”回答" in chat_data_builder.build_semantic_layer_context(None)
    assert "未选中完整指标集前，不要推荐具体查数样例" in chat_data_builder.build_semantic_layer_context(None)
    assert "搜索后仍未找到、候选不唯一或口径不清时再澄清" in chat_data_builder.build_semantic_layer_context(None)

    monkeypatch.setattr("apps.chat_data_app.agent.prompts.chat_data_builder.build_semantic_prompt", lambda semantic_config, mode: "语义配置内容")
    semantic_context = chat_data_builder.build_semantic_layer_context({"id": "sc_a", "name": "订单分析"})
    assert "订单分析" in semantic_context
    assert "semantic_config_id: sc_a" in semantic_context
    assert "只有用户本轮显式指定业务知识技能时，才读取对应技能" in semantic_context
    assert "语义配置内容" in semantic_context
    assert "本轮未提供完整指标集" in chat_data_builder.build_semantic_layer_context({})
    assert "semantic_config_id" not in chat_data_builder.build_semantic_layer_context({"name": "订单分析"})
    assert "当前已选择指标集。" in chat_data_builder.build_semantic_layer_context({"id": "", "name": ""})


def test_build_chat_data_system_prompt_uses_state_context(monkeypatch):
    monkeypatch.setattr("apps.chat_data_app.agent.prompts.chat_data_builder.build_semantic_prompt", lambda semantic_config, mode: "语义配置内容")

    system_prompt = chat_data_builder.build_chat_data_system_prompt(
        {
            "datasource_group_context": "当前数据源组按执行对象数据源分库\n当前机构A",
            "semantic_config_directory_info": "## 当前可见指标集目录\n1. 名称：订单分析",
        },
        semantic_config={"id": "sc_a"},
        available_skills_context="## 可用业务知识技能\n- custom-query-skill: 自定义问数技能",
    )

    assert "语义配置内容" in system_prompt
    assert "当前已选择指标集" in system_prompt
    assert "semantic_config_id: sc_a" in system_prompt
    assert "当前机构A" in system_prompt
    assert "数据源组智能问数说明" in system_prompt
    assert "当前可见指标集目录" in system_prompt
    assert "custom-query-skill" in system_prompt
    assert "当前指标集是唯一可执行事实源" in system_prompt
    assert "未选指标集时，可以推荐的问题应是下一步引导类" in system_prompt
    assert "最终回答不要提及技能加载过程" in system_prompt


def test_normalize_response_tool_call_names_does_not_guess_unknown_tool():
    unknown_message = AIMessage(
        content="",
        tool_calls=[{
            "id": "call_bad",
            "name": "unknown_toolunknown_tool",
            "args": {},
        }],
    )

    normalized = normalize_response_tool_call_names(
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
    normalized = normalize_response_tool_call_names(
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
    normalized = normalize_response_tool_call_names(
        similar_message,
        [SimpleNamespace(name="select_semantic_config_tool")],
    )
    assert normalized.tool_calls[0]["name"] == "select_semantic_config_tool_extra"


def test_log_context_editing_only_when_triggered(monkeypatch):
    logs = []

    monkeypatch.setattr(context_usage.logger, "info", lambda *args, **kwargs: logs.append(args))

    context_usage._log_context_editing_if_triggered({"triggered": False}, context_window=100, model_id="qwen-test")
    assert logs == []

    context_usage._log_context_editing_if_triggered(
        {
            "triggered": True,
            "estimated_tokens_before": 1000,
            "estimated_tokens_after": 300,
            "estimated_cleared_tokens": 700,
            "cleared_tool_results": 2,
            "history_trimmed": False,
        },
        context_window=128000,
        model_id="qwen-test",
    )

    assert len(logs) == 1
    assert "上下文编辑已触发" in logs[0][0]
    assert "qwen-test" in logs[0]


@pytest.mark.asyncio
async def test_chat_data_context_usage_middleware_edits_messages_and_updates_state(monkeypatch):
    original_tool_content = "订单 " * 5000
    recent_tool_content = "结果 " * 1000
    messages = [
        HumanMessage(content="历史问题"),
        ToolMessage(content=original_tool_content, tool_call_id="call_old", name="semantic_query_tool"),
        AIMessage(content="历史回答"),
        HumanMessage(content="历史问题2"),
        ToolMessage(content=recent_tool_content, tool_call_id="call_recent", name="semantic_query_tool"),
        HumanMessage(content="当前问题"),
    ]
    monkeypatch.setattr(context_usage, "get_context_edit_config", lambda: {"enabled": True, "trigger_ratio": 0.70})
    monkeypatch.setattr(
        context_usage.ToolResultBudget,
        "configure",
        lambda self, system_prompt, tools, model_id=None: ContextBudget(
            context_window=10000,
            max_output_tokens=2000,
            trigger_ratio=0.70,
            input_budget_tokens=7000,
        ),
    )
    monkeypatch.setattr(context_usage.ToolResultBudget, "remaining_for_messages", lambda self: 7000)
    monkeypatch.setattr(context_usage, "_emit_context_usage", lambda _usage: None)
    monkeypatch.setattr("apps.chat_data_app.agent.prompts.chat_data_builder.build_semantic_prompt", lambda semantic_config, mode: "语义配置内容")

    middleware = context_usage.create_chat_data_context_usage_middleware([], model_id="qwen-test")
    captured = {}

    async def handler(request):
        captured["request"] = request
        return ModelResponse(result=[AIMessage(content="完成")])

    request = ModelRequest(
        model=FakeChatModel(),
        messages=messages,
        tools=[],
        system_message=SystemMessage(content="智能问数系统提示词"),
        state={},
    )

    response = await middleware.awrap_model_call(request, handler)

    sent_messages = captured["request"].messages
    assert sent_messages[0].content == "历史问题"
    assert sent_messages[1].content.startswith("[工具结果已移出模型上下文]")
    assert sent_messages[4].content == recent_tool_content
    assert sent_messages[5].content == "当前问题"
    assert messages[1].content == original_tool_content
    assert response.model_response.result[0].content == "完成"
    assert response.model_response.result[0].additional_kwargs["timestamp"]
    token_usage = response.command.update["token_usage"]
    assert token_usage["context_editing"]["triggered"] is True
    assert token_usage["context_editing"]["cleared_tool_results"] == 1


@pytest.mark.asyncio
async def test_chat_data_context_usage_middleware_normalizes_tool_call_name(monkeypatch):
    monkeypatch.setattr(context_usage, "_emit_context_usage", lambda _usage: None)
    tool = SimpleNamespace(name="select_semantic_config_tool")
    middleware = context_usage.create_chat_data_context_usage_middleware([tool])

    async def handler(_request):
        return ModelResponse(result=[
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_select",
                    "name": "select_semantic_config_toolselect_semantic_config_tool",
                    "args": {"query": "查询入金金额"},
                }],
            )
        ])

    request = ModelRequest(
        model=FakeChatModel(),
        messages=[HumanMessage(content="查询入金金额")],
        tools=[tool],
        system_message=SystemMessage(content="智能问数系统提示词"),
        state={},
    )

    response = await middleware.awrap_model_call(request, handler)

    assert response.model_response.result[0].tool_calls[0]["name"] == "select_semantic_config_tool"
    assert response.model_response.result[0].tool_calls[0]["args"] == {"query": "查询入金金额"}


@pytest.mark.asyncio
async def test_chat_data_context_usage_middleware_falls_back_when_explicit_model_fails(monkeypatch):
    calls = []
    fallback_model = FakeChatModel("default_model")

    def fake_get_chat_data_llm(model=None, **_kwargs):
        calls.append(model)
        return fallback_model

    monkeypatch.setattr(context_usage, "get_chat_data_llm", fake_get_chat_data_llm)
    monkeypatch.setattr(context_usage, "get_default_chat_data_model", lambda: "default_model")
    monkeypatch.setattr(context_usage, "_emit_context_usage", lambda _usage: None)
    middleware = context_usage.create_chat_data_context_usage_middleware([], model_id="bad_model")
    call_count = 0

    async def handler(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("模型不可用")
        assert request.model is fallback_model
        return ModelResponse(result=[AIMessage(content="默认模型完成")])

    request = ModelRequest(
        model=FakeChatModel("bad_model"),
        messages=[HumanMessage(content="查询")],
        tools=[],
        system_message=SystemMessage(content="智能问数系统提示词"),
        state={},
    )

    response = await middleware.awrap_model_call(request, handler)

    assert response.model_response.result[0].content == "默认模型完成"
    assert calls == ["default_model"]


def test_tool_error_middleware_and_chat_data_agent_create_agent(monkeypatch):
    errors = []
    monkeypatch.setattr(tool_error.logger, "error", lambda *args, **_kwargs: errors.append(args))

    assert tool_error.format_tool_error(ValueError("")) == "ValueError('')"
    assert errors
    assert tool_error.format_tool_error(RuntimeError("boom")) == "boom"

    captured = {}
    selection_tool_kwargs = {}

    def fake_create_semantic_selection_tools_bundle(**kwargs):
        selection_tool_kwargs.update(kwargs)
        return ["select_semantic_config_tool", "dsl2sql_tool"]

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(name="agent")

    monkeypatch.setattr(chat_data_agent, "create_streaming_chat_model", lambda **_kwargs: FakeChatModel())
    monkeypatch.setattr(chat_data_agent, "create_semantic_selection_tools_bundle", fake_create_semantic_selection_tools_bundle)
    monkeypatch.setattr(chat_data_agent, "semantic_query_tool", "semantic_query_tool")
    monkeypatch.setattr(chat_data_agent, "calculator_tool", "calculator_tool")
    monkeypatch.setattr(chat_data_agent, "create_agent", fake_create_agent)

    agent = chat_data_agent.create_chat_data_agent(
        checkpointer="ckpt",
        semantic_config={"id": "sc_a"},
        datasource_id="ds_a",
        system_prompt="智能问数系统提示词",
        model_id="m1",
        user_id="u1",
    )

    assert agent.name == "agent"
    assert selection_tool_kwargs["model_id"] == "m1"
    assert selection_tool_kwargs["semantic_config"] == {"id": "sc_a"}
    assert selection_tool_kwargs["datasource_id"] == "ds_a"
    assert selection_tool_kwargs["user_id"] == "u1"
    assert selection_tool_kwargs["tool_result_budget"] is not None
    assert captured["checkpointer"] == "ckpt"
    assert captured["system_prompt"] == "智能问数系统提示词"
    assert captured["state_schema"] is chat_data_agent.ChatDataAgentState
    assert captured["name"] == "chat_data_agent"
    assert [getattr(tool, "name", tool) for tool in captured["tools"]] == [
        "skill_load",
        "select_semantic_config_tool",
        "dsl2sql_tool",
        "semantic_query_tool",
        "calculator_tool",
    ]
    assert len(captured["middleware"]) == 2
    assert captured["middleware"][0].name == "ChatDataContextUsageMiddleware"
    assert captured["middleware"][1].name == "ToolErrorMiddleware"
    assert chat_data_agent.get_chat_data_graph(model_id="m1").name == "agent"
    assert captured["system_prompt"] == "你是智能问数助手，请基于当前会话上下文和可用工具回答用户问题。"


@pytest.mark.asyncio
async def test_tool_error_middleware_returns_error_tool_message(monkeypatch):
    monkeypatch.setattr(tool_error.logger, "error", lambda *args, **_kwargs: None)
    middleware = tool_error.create_tool_error_middleware()

    async def handler(_request):
        raise RuntimeError("tool down")

    request = SimpleNamespace(tool_call={"id": "call_1", "name": "semantic_query_tool"})
    message = await middleware.awrap_tool_call(request, handler)

    assert message.content == "tool down"
    assert message.name == "semantic_query_tool"
    assert message.tool_call_id == "call_1"
    assert message.status == "error"


def test_sql_assist_agent_uses_create_agent(monkeypatch):
    captured = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(name="sql_agent")

    monkeypatch.setattr(sql_assist_agent, "get_chat_data_llm", lambda streaming=True: FakeChatModel())
    monkeypatch.setattr(sql_assist_agent, "create_agent", fake_create_agent)

    system_prompt = sql_assist_agent.build_sql_assist_system_prompt(
        sql_dialect="Hive",
        tables_ddl_info="CREATE TABLE orders(id int)",
    )
    assert "CREATE TABLE orders" in system_prompt
    assert "机构A" not in system_prompt

    agent = sql_assist_agent.create_sql_assist_agent(
        checkpointer="ckpt",
        sql_dialect="Hive",
        tables_ddl_info="CREATE TABLE orders(id int)",
    )

    assert agent.name == "sql_agent"
    assert captured["checkpointer"] == "ckpt"
    assert captured["tools"] == []
    assert captured["state_schema"] is sql_assist_agent.SqlAssistState
    assert captured["name"] == "sql_assist_agent"
    assert "CREATE TABLE orders" in captured["system_prompt"]
