import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent import data_agent, sql_assist_agent
from apps.chat_data_app.agent.agent_runtime import format_current_time_for_prompt, normalize_response_tool_call_names
from apps.chat_data_app.agent.middleware import context_compression, context_usage
from apps.chat_data_app.agent.middleware.context_compression import build_context_compression_update, compress_messages_for_state
from apps.chat_data_app.agent.middleware.tool_call_normalizer import create_tool_call_name_normalizer_middleware
from apps.chat_data_app.agent.middleware import tool_error
from apps.chat_data_app.agent.prompts import unified
from apps.chat_data_app.services.context_budget import (
    ContextBudget,
    build_runtime_context_summary_message,
    count_messages_tokens,
)


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
    assert unified.format_semantic_config_directory_info_for_llm("  ## 当前可见指标集目录  ") == "\n## 当前可见指标集目录\n"
    assert unified.format_semantic_config_directory_info_for_llm("") == ""
    assert unified.format_datasource_group_context_for_llm("  规则说明  ") == "\n## 数据源组智能问数说明\n规则说明\n"
    assert unified.format_datasource_group_context_for_llm("") == ""
    assert format_current_time_for_prompt(datetime(2026, 6, 24, 10, 0, 0)) == "2026-06-24 10:00:00（周三）"
    assert "本轮未提供完整指标集" in unified.build_semantic_layer_context(None)
    assert "业务数据查询或具体指标/维度询问先搜索当前数据源组中的真实指标集" in unified.build_semantic_layer_context(None)
    assert "指标集目录或业务范围概览可直接基于“当前可见指标集目录”回答" in unified.build_semantic_layer_context(None)
    assert "未选中完整指标集前，不要推荐具体查数样例" in unified.build_semantic_layer_context(None)
    assert "搜索后仍未找到、候选不唯一或口径不清时再澄清" in unified.build_semantic_layer_context(None)

    monkeypatch.setattr("apps.chat_data_app.agent.prompts.unified.build_semantic_prompt", lambda semantic_config, mode: "语义配置内容")
    semantic_context = unified.build_semantic_layer_context({"id": "sc_a", "name": "订单分析"})
    assert "订单分析" in semantic_context
    assert "semantic_config_id: sc_a" in semantic_context
    assert "只有用户本轮显式指定业务知识技能时，才读取对应技能" in semantic_context
    assert "语义配置内容" in semantic_context
    assert "本轮未提供完整指标集" in unified.build_semantic_layer_context({})
    assert "semantic_config_id" not in unified.build_semantic_layer_context({"name": "订单分析"})
    assert "当前已选择指标集。" in unified.build_semantic_layer_context({"id": "", "name": ""})


def test_build_unified_system_prompt_uses_shared_prompt_context(monkeypatch):
    from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions

    monkeypatch.setattr("apps.chat_data_app.agent.prompts.unified.build_semantic_prompt", lambda semantic_config, mode: "语义配置内容")

    system_prompt = unified.build_unified_system_prompt(
        AgentRuntimeOptions(workspace_backend="local", bash_enabled=False),
        semantic_config={"id": "sc_a"},
        available_skills_xml="## 可用业务知识技能\n- custom-query-skill: 自定义问数技能",
        datasource_group_context="当前数据源组按执行对象数据源分库\n当前机构A",
        semantic_config_directory_info="## 当前可见指标集目录\n1. 名称：订单分析",
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
    # Bash 关闭时，提示词不应暴露该工具。
    assert "`sandbox_run`" not in system_prompt


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


@pytest.mark.asyncio
async def test_context_compression_compresses_completed_old_rounds_and_logs_tokens(monkeypatch):
    logs = []

    monkeypatch.setattr("apps.chat_data_app.agent.middleware.context_compression.logger.info", lambda *args, **kwargs: logs.append(args))
    messages = []
    for index in range(6):
        messages.extend([
            HumanMessage(content=f"历史问题{index}" + " 查询订单销售额 " * 300),
            AIMessage(content=f"历史回答{index}" + " 销售额同比环比维度指标口径 " * 300),
        ])
    messages.append(HumanMessage(content="当前问题"))

    compressed, summary, report = await compress_messages_for_state(
        messages,
        context_window=100,
        message_budget_tokens=10,
        config={"enabled": True, "trigger_ratio": 0.10},
        summarizer=lambda _messages: _fake_summary("订单历史摘要"),
    )

    assert report["triggered"] is True
    assert report["estimated_tokens_after"] < report["estimated_tokens_before"]
    assert summary == "订单历史摘要"
    assert compressed[0].content.startswith("当前问题")
    assert compressed[-1].content == "当前问题"
    assert len(logs) == 1
    assert "上下文压缩已执行" in logs[0][0]
    assert "before_tokens=%s after_tokens=%s" in logs[0][0]


@pytest.mark.asyncio
async def test_context_compression_update_replaces_persisted_messages():
    messages = []
    for index in range(6):
        messages.extend([
            HumanMessage(content=f"历史问题{index}" + " 查询订单销售额 " * 300),
            AIMessage(content=f"历史回答{index}" + " 销售额同比环比维度指标口径 " * 300),
        ])
    messages.append(HumanMessage(content="当前问题"))

    updates, summary, report = await build_context_compression_update(
        messages,
        context_window=100,
        message_budget_tokens=10,
        config={"enabled": True, "trigger_ratio": 0.10},
        summarizer=lambda _messages: _fake_summary("订单历史摘要"),
    )

    assert report["triggered"] is True
    assert isinstance(updates[0], RemoveMessage)
    assert updates[0].id == REMOVE_ALL_MESSAGES
    assert summary == "订单历史摘要"
    assert updates[1].content == "当前问题"
    assert updates[-1].content == "当前问题"
    replaced = add_messages(messages, updates)
    assert replaced == updates[1:]


@pytest.mark.asyncio
async def test_context_compression_merges_existing_summary_when_threshold_exceeded():
    messages = [
        SystemMessage(
            content="[历史对话摘要]\n1. 用户: 历史问题",
            additional_kwargs={"context_compression": True, "compressed_message_count": 8},
        ),
        HumanMessage(content="保留问题"),
        AIMessage(content="保留回答"),
        HumanMessage(content="当前问题"),
    ]

    updates, summary, report = await build_context_compression_update(
        messages,
        context_window=100,
        message_budget_tokens=1,
        config={"enabled": True, "trigger_ratio": 0.10},
        summarizer=lambda _messages: _fake_summary("订单历史摘要"),
    )

    assert updates is not None
    assert summary == "订单历史摘要"
    assert report["triggered"] is True
    assert updates[1].content == "当前问题"


@pytest.mark.asyncio
async def test_context_compression_migrates_legacy_summary_without_triggering_compression():
    messages = [
        SystemMessage(
            content="[历史对话摘要]\n已确认订单指标集",
            additional_kwargs={"context_compression": True},
        ),
        HumanMessage(content="当前问题"),
    ]

    updates, summary, report = await build_context_compression_update(
        messages,
        context_window=10000,
        message_budget_tokens=9000,
        config={"enabled": True, "trigger_ratio": 0.70},
    )

    assert report["triggered"] is False
    assert report["legacy_summary_migrated"] is True
    assert summary == "已确认订单指标集"
    assert updates is not None
    assert isinstance(updates[0], RemoveMessage)
    assert [message.content for message in updates[1:]] == ["当前问题"]


@pytest.mark.asyncio
async def test_context_compression_counts_runtime_summary_wrapper_in_trigger_budget():
    messages = [
        HumanMessage(content="历史问题"),
        AIMessage(content="历史回答"),
        HumanMessage(content="当前问题"),
    ]
    previous_summary = "历史摘要"
    wrapped_summary = build_runtime_context_summary_message(previous_summary)
    assert wrapped_summary is not None
    raw_summary_tokens = count_messages_tokens([HumanMessage(content=previous_summary)])
    wrapped_summary_tokens = count_messages_tokens([wrapped_summary])
    assert wrapped_summary_tokens > raw_summary_tokens

    _, summary, report = await compress_messages_for_state(
        messages,
        context_window=100,
        message_budget_tokens=count_messages_tokens(messages) + raw_summary_tokens,
        config={"enabled": True, "trigger_ratio": 0.10},
        previous_summary=previous_summary,
        summarizer=lambda _messages: _fake_summary("新的完整摘要"),
    )

    assert report["triggered"] is True
    assert summary == "新的完整摘要"


@pytest.mark.asyncio
async def test_context_compression_summarizes_all_completed_content_without_truncation():
    tool_call = {
        "id": "call-1",
        "name": "semantic_query_tool",
        "args": {"dsl": {"filter": "条件" * 3000}},
    }
    complete_tool_result = "完整工具结果 " * 10000
    messages = [
        HumanMessage(content="历史问题"),
        AIMessage(content="", tool_calls=[tool_call]),
        ToolMessage(content=complete_tool_result, tool_call_id="call-1", name="semantic_query_tool"),
        AIMessage(content="历史结论"),
        HumanMessage(content="当前问题"),
    ]
    captured = {}

    async def summarize(compressible_messages):
        captured["messages"] = compressible_messages
        captured["prompt"] = context_compression._build_summary_prompt(compressible_messages)
        return "完整历史摘要"

    compressed, summary, report = await compress_messages_for_state(
        messages,
        context_window=100,
        message_budget_tokens=1,
        config={"enabled": True, "trigger_ratio": 0.10},
        summarizer=summarize,
    )

    assert report["triggered"] is True
    assert report["compressed_messages"] == 4
    assert summary == "完整历史摘要"
    assert compressed == [messages[-1]]
    assert captured["messages"] == messages[:4]
    assert tool_call["args"]["dsl"]["filter"] in captured["prompt"]
    assert complete_tool_result.rstrip() in captured["prompt"]


async def _fake_summary(text: str) -> str:
    return text


@pytest.mark.asyncio
async def test_chat_data_context_usage_middleware_keeps_tool_messages(monkeypatch):
    from apps.chat_data_app.agent.middleware import context_usage

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
    monkeypatch.setattr("apps.chat_data_app.agent.prompts.unified.build_semantic_prompt", lambda semantic_config, mode: "语义配置内容")

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
    assert sent_messages[1].content == original_tool_content
    assert sent_messages[4].content == recent_tool_content
    assert sent_messages[5].content == "当前问题"
    assert response.model_response.result[0].content == "完成"
    token_usage = response.command.update["token_usage"]
    assert "context_editing" not in token_usage


@pytest.mark.asyncio
async def test_chat_data_context_usage_middleware_merges_persisted_compression_summary(monkeypatch):
    from apps.chat_data_app.agent.middleware import context_usage

    messages = []
    messages.append(SystemMessage(
        content="[历史对话摘要]\n1. 用户: 历史问题",
        additional_kwargs={"context_compression": True, "compressed_message_count": 10},
    ))
    messages.append(HumanMessage(content="当前问题"))
    monkeypatch.setattr(
        context_usage.ToolResultBudget,
        "configure",
        lambda self, system_prompt, tools, model_id=None: ContextBudget(
            context_window=1000,
            max_output_tokens=200,
            trigger_ratio=0.70,
            input_budget_tokens=700,
        ),
    )
    monkeypatch.setattr(context_usage.ToolResultBudget, "remaining_for_messages", lambda self: 100)
    monkeypatch.setattr(context_usage, "_emit_context_usage", lambda _usage: None)

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
    assert captured["request"].system_message.content == "智能问数系统提示词"
    assert sent_messages[0].additional_kwargs["context_summary"] is True
    assert "历史问题" in sent_messages[0].content
    assert sent_messages[-1].content == "当前问题"
    assert all(message.type != "system" for message in sent_messages)
    token_usage = response.command.update["token_usage"]
    assert token_usage["context_compression"]["triggered"] is False


@pytest.mark.asyncio
async def test_tool_call_name_normalizer_middleware_normalizes_tool_call_name():
    middleware = create_tool_call_name_normalizer_middleware([SimpleNamespace(name="select_semantic_config_tool")])

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

    response = await middleware.awrap_model_call(SimpleNamespace(), handler)

    assert response.result[0].tool_calls[0]["name"] == "select_semantic_config_tool"
    assert response.result[0].tool_calls[0]["args"] == {"query": "查询入金金额"}


@pytest.mark.asyncio
async def test_chat_data_context_usage_middleware_keeps_tool_call_name_to_standard_normalizer(monkeypatch):
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

    assert response.model_response.result[0].tool_calls[0]["name"] == "select_semantic_config_toolselect_semantic_config_tool"
    assert response.model_response.result[0].tool_calls[0]["args"] == {"query": "查询入金金额"}


@pytest.mark.asyncio
async def test_chat_data_context_usage_middleware_raises_when_model_fails(monkeypatch):
    monkeypatch.setattr(context_usage, "_emit_context_usage", lambda _usage: None)
    middleware = context_usage.create_chat_data_context_usage_middleware([], model_id="bad_model")

    async def handler(_request):
        raise RuntimeError("模型不可用")

    request = ModelRequest(
        model=FakeChatModel("bad_model"),
        messages=[HumanMessage(content="查询")],
        tools=[],
        system_message=SystemMessage(content="智能问数系统提示词"),
        state={},
    )

    with pytest.raises(RuntimeError, match="模型不可用"):
        await middleware.awrap_model_call(request, handler)


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
        return ["select_semantic_config_tool"]

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(name="agent")

    model_kwargs = {}

    def fake_create_streaming_chat_model(**kwargs):
        model_kwargs.update(kwargs)
        return FakeChatModel()

    monkeypatch.setattr(data_agent, "create_streaming_chat_model", fake_create_streaming_chat_model)
    monkeypatch.setattr(data_agent, "create_semantic_selection_tools_bundle", fake_create_semantic_selection_tools_bundle)
    monkeypatch.setattr(data_agent, "semantic_query_tool", "semantic_query_tool")
    monkeypatch.setattr(data_agent, "calculator_tool", "calculator_tool")
    monkeypatch.setattr(data_agent, "create_agent", fake_create_agent)

    from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions

    agent = data_agent.create_data_agent(
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
    assert selection_tool_kwargs["datasource_group_context"] is None
    assert model_kwargs["model_id"] == "m1"
    assert model_kwargs["stream_usage"] is True
    assert captured["checkpointer"] == "ckpt"
    assert captured["system_prompt"] == "智能问数系统提示词"
    assert captured["state_schema"] is data_agent.ChatDataAgentState
    assert captured["name"] == "chat_data_agent"
    assert [getattr(tool, "name", tool) for tool in captured["tools"]] == [
        "skill_load",
        "file_read",
        "file_edit",
        "list_dir",
        "present_artifacts",
        "get_current_time",
        "select_semantic_config_tool",
        "dsl2sql_tool",
        "semantic_query_tool",
        "calculator_tool",
    ]
    assert len(captured["middleware"]) == 5
    assert captured["middleware"][0].name == "ContextCompressionMiddleware"
    assert captured["middleware"][1].name == "ContextUsageMiddleware"
    assert captured["middleware"][2].name == "ResponseTimestampMiddleware"
    assert captured["middleware"][3].name == "ToolCallNameNormalizerMiddleware"
    assert captured["middleware"][4].name == "ToolErrorMiddleware"
    assert data_agent.create_data_agent(AgentRuntimeOptions(), model_id="m1").name == "agent"
    assert "智能问数与数据分析助手" in captured["system_prompt"]


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
