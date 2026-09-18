import os
import sys
import json
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
from apps.chat_data_app.agent.agent_runtime import normalize_response_tool_call_names
from apps.chat_data_app.agent.middleware import context_compression, context_usage
from apps.chat_data_app.agent.middleware import message_integrity, tool_call_history_repair
from apps.chat_data_app.agent.middleware.context_compression import build_context_compression_update, compress_messages_for_state
from apps.chat_data_app.agent.middleware.tool_call_normalizer import create_tool_call_name_normalizer_middleware
from apps.chat_data_app.agent.prompts.context_compression import build_context_compression_summary_prompt
from apps.chat_data_app.agent.tool_results import build_tool_result_recovery, merge_tool_result_recovery_artifact
from apps.chat_data_app.agent import agent_runtime
from langchain.agents.middleware import ToolErrorMiddleware
from apps.chat_data_app.agent.context.context_budget import (
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
        [SimpleNamespace(name="file_read")],
    )

    assert normalized.tool_calls[0]["name"] == "unknown_toolunknown_tool"

    repeated_message = AIMessage(
        content="",
        tool_calls=[{
            "id": "call_select",
            "name": "file_readfile_readfile_read",
            "args": {},
        }],
    )
    normalized = normalize_response_tool_call_names(
        repeated_message,
        [SimpleNamespace(name="file_read")],
    )
    assert normalized.tool_calls[0]["name"] == "file_read"

    similar_message = AIMessage(
        content="",
        tool_calls=[{
            "id": "call_similar",
            "name": "file_read_extra",
            "args": {},
        }],
    )
    normalized = normalize_response_tool_call_names(
        similar_message,
        [SimpleNamespace(name="file_read")],
    )
    assert normalized.tool_calls[0]["name"] == "file_read_extra"


def test_normalize_response_tool_call_names_coerces_string_args():
    """内网 Qwen3.6 经 vLLM 返回的 args 是 JSON 字符串/双层编码，须在工具执行前规整为 dict。

    服务端 langchain 版本不校验 tool_calls args 类型（本仓库 fxcjg 的 1.5.4 构造时强校验 dict），
    故用 model_construct 绕过构造校验模拟服务端真实形态。
    """
    message = AIMessage.model_construct(
        content="",
        tool_calls=[
            {
                "id": "call_str",
                "name": "calculator_tool",
                "args": '{"calculations": [{"expression": "1 + 1"}]}',
            },
            {
                "id": "call_double",
                "name": "file_read",
                "args": json.dumps(json.dumps({"path": "/a"})),
            },
            {
                "id": "call_bad",
                "name": "file_read",
                "args": "raw-not-json",
            },
            {
                "id": "call_dict",
                "name": "file_read",
                "args": {"path": "/b"},
            },
        ],
    )

    normalized = normalize_response_tool_call_names(
        message,
        [SimpleNamespace(name="calculator_tool"), SimpleNamespace(name="file_read")],
    )

    calls = {call["id"]: call for call in normalized.tool_calls}
    assert calls["call_str"]["args"] == {"calculations": [{"expression": "1 + 1"}]}
    assert calls["call_double"]["args"] == {"path": "/a"}
    # 解析不了的原始文本保持原样，让工具侧自然报错，不做静默篡改
    assert calls["call_bad"]["args"] == "raw-not-json"
    assert calls["call_dict"]["args"] == {"path": "/b"}


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
        static_input_tokens=0,
        compression_trigger_tokens=10,
        hard_input_limit_tokens=90,
        compression_target_tokens=1,
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
        static_input_tokens=0,
        compression_trigger_tokens=10,
        hard_input_limit_tokens=90,
        compression_target_tokens=1,
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
async def test_context_compression_replaces_history_even_when_summary_does_not_reduce_tokens(monkeypatch):
    logs = []
    monkeypatch.setattr(
        "apps.chat_data_app.agent.middleware.context_compression.logger.info",
        lambda *args, **kwargs: logs.append(args),
    )
    messages = [
        HumanMessage(content="历史问题"),
        AIMessage(content="历史回答"),
        HumanMessage(content="当前问题"),
    ]

    compressed, summary, report = await compress_messages_for_state(
        messages,
        static_input_tokens=0,
        compression_trigger_tokens=1,
        hard_input_limit_tokens=12_000,
        compression_target_tokens=1,
        config={"enabled": True, "trigger_ratio": 0.10},
        summarizer=lambda _messages: _fake_summary("历史问题 历史回答"),
    )

    assert compressed == [messages[-1]]
    assert summary == "历史问题 历史回答"
    assert report["triggered"] is True
    assert report["estimated_cleared_tokens"] == 0
    assert "上下文压缩已执行" in logs[0][0]


@pytest.mark.asyncio
async def test_context_compression_compacts_earlier_tools_in_current_turn():
    first_tool_result = "第一份查询结果 " * 1200
    second_tool_result = "第二份查询结果 " * 1200
    messages = [
        HumanMessage(content="生成完整数据分析报告", id="turn-current"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call-1", "name": "file_read", "args": {}}],
            id="assistant-1",
        ),
        ToolMessage(content=first_tool_result, tool_call_id="call-1", name="file_read", id="tool-1"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call-2", "name": "bash_run", "args": {}}],
            id="assistant-2",
        ),
        ToolMessage(content=second_tool_result, tool_call_id="call-2", name="bash_run", id="tool-2"),
    ]
    captured = {}

    async def summarize(summary_messages):
        captured["messages"] = summary_messages
        return "已读取查询结果并完成脚本分析"

    compressed, summary, report = await compress_messages_for_state(
        messages,
        static_input_tokens=0,
        compression_trigger_tokens=10,
        hard_input_limit_tokens=10_000,
        compression_target_tokens=count_messages_tokens([messages[0], *messages[3:]]),
        config={"enabled": True, "trigger_ratio": 0.10},
        summarizer=summarize,
    )

    assert report["triggered"] is True
    assert summary == "已读取查询结果并完成脚本分析"
    assert compressed == [messages[0], messages[3], messages[4]]
    assert captured["messages"] == [messages[1], messages[2]]
    assert report["compressed_messages"] == 2


@pytest.mark.asyncio
async def test_context_compression_references_parallel_tool_results_without_breaking_pairs():
    recovery_a = build_tool_result_recovery(
        source_tool="file_read",
        result_path="/mnt/user-data/workspace/a.json",
        result_size_chars=5000,
        offset=0,
        limit=200,
    )
    recovery_b = build_tool_result_recovery(
        source_tool="data_analyzing_tool",
        result_path="/mnt/user-data/outputs/.tool-offload/result-b.json",
        result_size_chars=6000,
        result_id="result-b",
    )
    messages = [
        HumanMessage(content="并行核对两个结果", id="turn-parallel"),
        AIMessage(
            content="",
            id="assistant-parallel",
            tool_calls=[
                {"id": "call-a", "name": "file_read", "args": {}},
                {"id": "call-b", "name": "data_analyzing_tool", "args": {}},
            ],
        ),
        ToolMessage(
            content="甲" * 5000,
            id="tool-a",
            name="file_read",
            tool_call_id="call-a",
            artifact=merge_tool_result_recovery_artifact(None, recovery_a),
        ),
        ToolMessage(
            content="乙" * 6000,
            id="tool-b",
            name="data_analyzing_tool",
            tool_call_id="call-b",
            artifact=merge_tool_result_recovery_artifact(None, recovery_b),
        ),
    ]

    compressed, summary, report = await compress_messages_for_state(
        messages,
        static_input_tokens=0,
        compression_trigger_tokens=10,
        hard_input_limit_tokens=20_000,
        compression_target_tokens=1000,
        config={"enabled": True, "trigger_ratio": 0.10},
    )

    assert summary is None
    assert report["referenced_tool_results"] == 2
    assert report["target_reached"] is True
    assert compressed[1].tool_calls[0]["id"] == "call-a"
    assert compressed[1].tool_calls[1]["id"] == "call-b"
    assert [message.tool_call_id for message in compressed[2:]] == ["call-a", "call-b"]
    assert all("[tool_result_referenced]" in message.content for message in compressed[2:])

    referenced_again, referenced_count = context_compression._reference_tool_results_to_target(
        compressed,
        static_input_tokens=0,
        summary_text=None,
        target_tokens=1,
    )
    assert referenced_again == compressed
    assert referenced_count == 0


@pytest.mark.asyncio
async def test_hard_guard_drops_latest_unrecoverable_tool_batch_and_continues():
    current_user = HumanMessage(content="继续完成分析", id="turn-hard-guard")
    messages = [
        HumanMessage(content="旧问题" + "旧内容" * 500, id="turn-old"),
        AIMessage(content="旧回答" + "旧内容" * 500, id="assistant-old"),
        current_user,
        AIMessage(
            content="",
            id="assistant-tool",
            tool_calls=[{"id": "call-large", "name": "unknown_tool", "args": {}}],
        ),
        ToolMessage(
            content="不可恢复结果" * 2000,
            id="tool-large",
            name="unknown_tool",
            tool_call_id="call-large",
        ),
    ]

    compressed, summary, report = await compress_messages_for_state(
        messages,
        static_input_tokens=50,
        compression_trigger_tokens=100_000,
        hard_input_limit_tokens=500,
        compression_target_tokens=100,
        config={"enabled": False, "trigger_ratio": 0.70},
    )

    assert compressed == [current_user]
    assert summary is None
    assert report["hard_limit_enforced"] is True
    assert report["estimated_tokens_after"] <= 475
    assert report["hard_limit_dropped_messages"] == 4


@pytest.mark.asyncio
async def test_hard_guard_truncates_only_current_user_when_it_alone_is_too_large():
    original = HumanMessage(content="当前问题" * 3000, id="turn-large-user")

    compressed, summary, report = await compress_messages_for_state(
        [original],
        static_input_tokens=50,
        compression_trigger_tokens=100_000,
        hard_input_limit_tokens=500,
        compression_target_tokens=100,
        config={"enabled": False, "trigger_ratio": 0.70},
    )

    assert summary is None
    assert len(compressed) == 1
    assert compressed[0].id == original.id
    assert compressed[0].content.startswith("[当前用户输入已按上下文安全水位截取")
    assert report["hard_limit_user_message_truncated"] is True
    assert report["estimated_tokens_after"] <= 475


@pytest.mark.asyncio
async def test_hard_guard_truncates_unbroken_ascii_user_input():
    original = HumanMessage(content="x" * 5000, id="turn-large-ascii")

    compressed, summary, report = await compress_messages_for_state(
        [original],
        static_input_tokens=50,
        compression_trigger_tokens=100_000,
        hard_input_limit_tokens=500,
        compression_target_tokens=100,
        config={"enabled": False, "trigger_ratio": 0.70},
    )

    assert summary is None
    assert len(compressed) == 1
    assert compressed[0].id == original.id
    assert len(compressed[0].content) < len(original.content)
    assert report["hard_limit_user_message_truncated"] is True
    assert report["estimated_tokens_after"] <= 475


@pytest.mark.asyncio
async def test_summary_failure_falls_back_to_hard_guard_without_failing():
    async def fail_summary(_messages):
        raise RuntimeError("summary unavailable")

    current_user = HumanMessage(content="当前问题", id="turn-summary-fallback")
    compressed, summary, report = await compress_messages_for_state(
        [
            HumanMessage(content="旧问题" + "历史" * 1000, id="turn-old"),
            AIMessage(content="旧回答" + "历史" * 1000, id="assistant-old"),
            current_user,
        ],
        static_input_tokens=50,
        compression_trigger_tokens=10,
        hard_input_limit_tokens=500,
        compression_target_tokens=100,
        config={"enabled": True, "trigger_ratio": 0.10},
        summarizer=fail_summary,
    )

    assert compressed == [current_user]
    assert summary is None
    assert report["summary_generation_failed"] is True
    assert report["hard_limit_enforced"] is True
    assert report["estimated_tokens_after"] <= 475


@pytest.mark.asyncio
async def test_context_compression_uses_configured_summary_output_budget(monkeypatch):
    captured = {}
    monkeypatch.setenv("CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS", "1536")
    monkeypatch.setattr(
        context_compression,
        "get_model_context_budget",
        lambda model_id, *, max_output_tokens: captured.update({
            "budget_model_id": model_id,
            "budget_max_output_tokens": max_output_tokens,
        }) or SimpleNamespace(hard_input_limit_tokens=9_999),
    )

    class SummaryLLM:
        async def ainvoke(self, _messages):
            return AIMessage(content="压缩摘要")

    def fake_get_chat_data_llm(**kwargs):
        captured.update(kwargs)
        return SummaryLLM()

    monkeypatch.setattr(context_compression, "get_chat_data_llm", fake_get_chat_data_llm)

    assert context_compression._get_summary_input_limit_tokens("summary-model") == context_compression.hard_input_guard_tokens(9_999)
    summary = await context_compression._summarize_messages_with_llm(
        [HumanMessage(content="待摘要历史")],
        model_id="summary-model",
    )

    assert summary == "压缩摘要"
    assert captured["budget_model_id"] == "summary-model"
    assert captured["budget_max_output_tokens"] == 1536
    assert captured["max_tokens"] == 1536


@pytest.mark.asyncio
async def test_context_compression_middleware_persists_and_streams_visible_event(monkeypatch):
    messages = []
    for index in range(4):
        messages.extend([
            HumanMessage(content=f"历史问题{index}" + " 查询订单销售额 " * 300, id=f"user-{index}"),
            AIMessage(content=f"历史回答{index}" + " 销售额同比环比维度指标口径 " * 300, id=f"assistant-{index}"),
        ])
    messages.append(HumanMessage(content="当前问题", id="turn-current"))
    monkeypatch.setattr(
        context_compression.ContextRequestBudget,
        "configure",
        lambda self, system_prompt, tools, model_id=None: ContextBudget(
            context_window=1000,
            max_output_tokens=200,
            compression_trigger_ratio=0.70,
            hard_input_limit_tokens=800,
            compression_trigger_tokens=10,
            compression_target_tokens=1,
        ),
    )
    monkeypatch.setattr(context_compression, "get_context_compression_config", lambda: {"enabled": True, "trigger_ratio": 0.10})
    monkeypatch.setattr(context_compression, "_get_summary_input_limit_tokens", lambda _model_id: 800)

    async def summarize(*_args, **_kwargs):
        return "订单历史摘要"

    monkeypatch.setattr(context_compression, "_summarize_messages_with_llm", summarize)
    streamed_events = []
    monkeypatch.setattr(context_compression, "get_stream_writer", lambda: streamed_events.append)

    middleware = context_compression.create_context_compression_middleware(
        system_prompt="智能问数系统提示词",
        state_schema=dict,
    )
    update = await middleware.abefore_model({
        "messages": messages,
        "workspace": {
            "workspace_root": "/data/chat/threads/u1/c1/user-data",
            "workspace_backend": "sandbox",
        },
    }, SimpleNamespace())

    event = update["context_compression_events"][0]
    assert update["context_compression"]["triggered"] is True
    assert "workspace" not in update
    assert event["turn_id"] == "turn-current"
    assert event["anchor_message_id"] == "turn-current"
    # 摘要开始前先推送 started 占位（仅实时流），完成后推送 completed 分界事件
    assert len(streamed_events) == 2
    started_payload = streamed_events[0]
    assert started_payload["type"] == "context_compression"
    assert started_payload["event"]["phase"] == "started"
    assert started_payload["event"]["turn_id"] == "turn-current"
    assert started_payload["archived_turn_messages"] == []
    assert streamed_events[1] == {
        "type": "context_compression",
        "event": event,
        "archived_turn_messages": [messages[-1]],
    }


def test_context_compression_event_excludes_summary_and_anchors_current_message():
    messages = [
        HumanMessage(content="历史问题", id="turn-old"),
        AIMessage(content="历史回答", id="assistant-old"),
        HumanMessage(content="当前问题", id="turn-current"),
    ]
    event = context_compression._build_context_compression_event(messages, {
        "triggered": True,
        "estimated_tokens_before": 70_000,
        "estimated_tokens_after": 21_000,
        "estimated_cleared_tokens": 49_000,
        "compressed_messages": 2,
        "summary_chars": 999,
    })

    assert event is not None
    assert event["turn_id"] == "turn-current"
    assert event["anchor_message_id"] == "turn-current"
    assert event["estimated_cleared_tokens"] == 49_000
    assert "summary" not in event

    retried_event = context_compression._build_context_compression_event(messages, {
        "triggered": True,
        "estimated_tokens_before": 70_000,
        "estimated_tokens_after": 21_000,
        "estimated_cleared_tokens": 49_000,
        "compressed_messages": 2,
    })
    assert retried_event["id"] == event["id"]


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
        static_input_tokens=0,
        compression_trigger_tokens=1,
        hard_input_limit_tokens=90,
        compression_target_tokens=1,
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
        static_input_tokens=0,
        compression_trigger_tokens=9000,
        hard_input_limit_tokens=9500,
        compression_target_tokens=2100,
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
        static_input_tokens=0,
        compression_trigger_tokens=count_messages_tokens(messages) + raw_summary_tokens,
        hard_input_limit_tokens=100,
        compression_target_tokens=1,
        config={"enabled": True, "trigger_ratio": 0.10},
        previous_summary=previous_summary,
        summarizer=lambda _messages: _fake_summary("新的完整摘要"),
    )

    assert report["triggered"] is True
    assert summary == "新的完整摘要"


@pytest.mark.asyncio
async def test_context_compression_preserves_all_completed_content_when_summary_input_is_within_budget():
    tool_call = {
        "id": "call-1",
        "name": "data_analyzing_tool",
        "args": {"dsl": {"filter": "条件" * 3000}},
    }
    complete_tool_result = "完整工具结果 " * 10000
    messages = [
        HumanMessage(content="历史问题"),
        AIMessage(content="", tool_calls=[tool_call]),
        ToolMessage(content=complete_tool_result, tool_call_id="call-1", name="data_analyzing_tool"),
        AIMessage(content="历史结论"),
        HumanMessage(content="当前问题"),
    ]
    captured = {}

    async def summarize(compressible_messages):
        captured["messages"] = compressible_messages
        captured["prompt"] = build_context_compression_summary_prompt(compressible_messages)
        return "完整历史摘要"

    compressed, summary, report = await compress_messages_for_state(
        messages,
        static_input_tokens=0,
        compression_trigger_tokens=1,
        hard_input_limit_tokens=90,
        compression_target_tokens=1,
        config={"enabled": True, "trigger_ratio": 0.10},
        summarizer=summarize,
        summary_input_limit_tokens=1_000_000,
    )

    assert report["triggered"] is True
    assert report["compressed_messages"] == 4
    assert summary == "完整历史摘要"
    assert compressed == [messages[-1]]
    assert captured["messages"] == messages[:4]
    assert tool_call["args"]["dsl"]["filter"] in captured["prompt"]
    assert complete_tool_result.rstrip() in captured["prompt"]


@pytest.mark.asyncio
async def test_context_compression_compacts_summary_input_without_mutating_runtime_history():
    large_tool_result = "完整工具结果" * 5000
    messages = [
        HumanMessage(content="历史问题"),
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "data_analyzing_tool", "args": {}}]),
        ToolMessage(content=large_tool_result, tool_call_id="call-1", name="data_analyzing_tool"),
        AIMessage(content="历史结论"),
        HumanMessage(content="当前问题"),
    ]
    compacted_tool_result = context_compression._compact_tool_result_for_summary(messages[2])
    summary_limit = context_compression._count_summary_input_tokens(
        [messages[0], messages[1], compacted_tool_result, messages[3]],
        previous_summary=None,
    )
    captured = {}

    async def summarize(summary_messages):
        captured["messages"] = summary_messages
        return "历史摘要"

    compressed, summary, report = await compress_messages_for_state(
        messages,
        static_input_tokens=0,
        compression_trigger_tokens=1,
        hard_input_limit_tokens=100_000,
        compression_target_tokens=1,
        config={"enabled": True, "trigger_ratio": 0.10},
        summarizer=summarize,
        summary_input_limit_tokens=summary_limit,
    )

    assert report["triggered"] is True
    assert report["summary_tool_results_compacted"] == 1
    assert report["summary_input_tokens_after"] <= summary_limit
    assert summary == "历史摘要"
    assert compressed == [messages[-1]]
    assert messages[2].content == large_tool_result
    assert captured["messages"][2].content != large_tool_result
    assert captured["messages"][2].tool_call_id == "call-1"


def test_summary_input_compacts_only_the_largest_tool_result_until_budget_is_met():
    large_tool_result = "最大工具结果" * 5000
    smaller_tool_result = "较小工具结果" * 800
    messages = [
        HumanMessage(content="第一轮问题"),
        AIMessage(content="", tool_calls=[{"id": "call-large", "name": "data_analyzing_tool", "args": {}}]),
        ToolMessage(content=large_tool_result, tool_call_id="call-large", name="data_analyzing_tool"),
        AIMessage(content="第一轮结论"),
        HumanMessage(content="第二轮问题"),
        AIMessage(content="", tool_calls=[{"id": "call-small", "name": "file_read", "args": {}}]),
        ToolMessage(content=smaller_tool_result, tool_call_id="call-small", name="file_read"),
        AIMessage(content="第二轮结论"),
    ]
    compacted_large = context_compression._compact_tool_result_for_summary(messages[2])
    limit = context_compression._count_summary_input_tokens(
        [messages[0], messages[1], compacted_large, *messages[3:]],
        previous_summary=None,
    )

    prepared, report = context_compression._prepare_summary_input(
        messages,
        previous_summary=None,
        summary_input_limit_tokens=limit,
    )

    assert report["summary_tool_results_compacted"] == 1
    assert report["summary_input_pruned_rounds"] == 0
    assert report["summary_input_tokens_after"] <= limit
    assert prepared[2].tool_call_id == "call-large"
    assert "[工具结果已为历史摘要压缩]" in prepared[2].content
    assert prepared[6].content == smaller_tool_result
    assert prepared[6].tool_call_id == "call-small"


def test_summary_input_prunes_oldest_complete_rounds_when_tool_compaction_is_insufficient():
    messages = [
        HumanMessage(content="第一轮问题" + "内容" * 3000),
        AIMessage(content="第一轮回答" + "内容" * 3000),
        HumanMessage(content="第二轮问题"),
        AIMessage(content="第二轮回答"),
        HumanMessage(content="第三轮问题"),
        AIMessage(content="第三轮回答"),
    ]
    marker = context_compression._build_summary_omission_marker(2, 1)
    limit = context_compression._count_summary_input_tokens(
        [marker, *messages[2:]],
        previous_summary=None,
    )

    prepared, report = context_compression._prepare_summary_input(
        messages,
        previous_summary=None,
        summary_input_limit_tokens=limit,
    )

    assert report["summary_tool_results_compacted"] == 0
    assert report["summary_input_pruned_rounds"] == 1
    assert report["summary_input_pruned_messages"] == 2
    assert report["summary_input_tokens_after"] <= limit
    assert prepared[0].additional_kwargs["context_compression_summary_omission"] is True
    assert prepared[1:] == messages[2:]


@pytest.mark.asyncio
async def test_context_compression_keeps_two_recent_completed_rounds_together():
    messages = [
        HumanMessage(content="第一轮问题" + " 历史查询条件" * 200),
        AIMessage(content="第一轮回答" + " 历史指标口径" * 200),
        HumanMessage(content="第二轮问题"),
        AIMessage(content="第二轮回答"),
        HumanMessage(content="第三轮问题"),
        AIMessage(content="第三轮回答"),
        HumanMessage(content="当前问题"),
    ]
    captured = {}
    keep_tokens = count_messages_tokens(messages[2:]) + context_compression._summary_tokens("第一轮摘要")

    async def summarize(compressible_messages):
        captured["messages"] = compressible_messages
        return "第一轮摘要"

    compressed, summary, report = await compress_messages_for_state(
        messages,
        static_input_tokens=0,
        compression_trigger_tokens=1,
        hard_input_limit_tokens=1000,
        compression_target_tokens=keep_tokens,
        config={"enabled": True, "trigger_ratio": 0.70},
        summarizer=summarize,
    )

    assert report["triggered"] is True
    assert summary == "第一轮摘要"
    assert captured["messages"] == messages[:2]
    assert compressed == messages[2:]


async def _fake_summary(text: str) -> str:
    return text


@pytest.mark.asyncio
async def test_chat_data_context_usage_middleware_keeps_tool_messages(monkeypatch):
    from apps.chat_data_app.agent.middleware import context_usage

    original_tool_content = "订单 " * 5000
    recent_tool_content = "结果 " * 1000
    messages = [
        HumanMessage(content="历史问题"),
        ToolMessage(content=original_tool_content, tool_call_id="call_old", name="data_analyzing_tool"),
        AIMessage(content="历史回答"),
        HumanMessage(content="历史问题2"),
        ToolMessage(content=recent_tool_content, tool_call_id="call_recent", name="data_analyzing_tool"),
        HumanMessage(content="当前问题"),
    ]
    monkeypatch.setattr(
        context_usage.ContextRequestBudget,
        "configure",
        lambda self, system_prompt, tools, model_id=None: ContextBudget(
            context_window=10000,
            max_output_tokens=2000,
            compression_trigger_ratio=0.70,
            hard_input_limit_tokens=8000,
            compression_trigger_tokens=7000,
            compression_target_tokens=2100,
        ),
    )
    monkeypatch.setattr(context_usage.ContextRequestBudget, "remaining_for_messages", lambda self: 7000)
    monkeypatch.setattr(context_usage, "_emit_context_usage", lambda _usage: None)
    middleware = context_usage.create_request_context_usage_middleware(tools=[], model_id="qwen-test")
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
        context_usage.ContextRequestBudget,
        "configure",
        lambda self, system_prompt, tools, model_id=None: ContextBudget(
            context_window=1000,
            max_output_tokens=200,
            compression_trigger_ratio=0.70,
            hard_input_limit_tokens=800,
            compression_trigger_tokens=700,
            compression_target_tokens=210,
        ),
    )
    monkeypatch.setattr(context_usage.ContextRequestBudget, "remaining_for_messages", lambda self: 100)
    monkeypatch.setattr(context_usage, "_emit_context_usage", lambda _usage: None)

    middleware = context_usage.create_request_context_usage_middleware(tools=[], model_id="qwen-test")
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
    assert all(not isinstance(message, SystemMessage) for message in sent_messages)
    token_usage = response.command.update["token_usage"]
    assert token_usage["context_compression"]["triggered"] is False


@pytest.mark.asyncio
async def test_tool_call_name_normalizer_middleware_normalizes_tool_call_name():
    middleware = create_tool_call_name_normalizer_middleware([SimpleNamespace(name="file_read")])

    async def handler(_request):
        return ModelResponse(result=[
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_select",
                    "name": "file_readfile_read",
                    "args": {"query": "查询入金金额"},
                }],
            )
        ])

    response = await middleware.awrap_model_call(SimpleNamespace(), handler)

    assert response.result[0].tool_calls[0]["name"] == "file_read"
    assert response.result[0].tool_calls[0]["args"] == {"query": "查询入金金额"}


@pytest.mark.asyncio
async def test_chat_data_context_usage_middleware_keeps_tool_call_name_to_standard_normalizer(monkeypatch):
    monkeypatch.setattr(context_usage, "_emit_context_usage", lambda _usage: None)
    tool = SimpleNamespace(name="file_read")
    middleware = context_usage.create_request_context_usage_middleware(tools=[tool])

    async def handler(_request):
        return ModelResponse(result=[
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_select",
                    "name": "file_readfile_read",
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

    assert response.model_response.result[0].tool_calls[0]["name"] == "file_readfile_read"
    assert response.model_response.result[0].tool_calls[0]["args"] == {"query": "查询入金金额"}


@pytest.mark.asyncio
async def test_chat_data_context_usage_middleware_raises_when_model_fails(monkeypatch):
    monkeypatch.setattr(context_usage, "_emit_context_usage", lambda _usage: None)
    middleware = context_usage.create_request_context_usage_middleware(tools=[], model_id="bad_model")

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
    monkeypatch.setattr(agent_runtime.logger, "error", lambda *args, **_kwargs: errors.append(args))
    warnings = []
    monkeypatch.setattr(agent_runtime.logger, "warning", lambda *args, **_kwargs: warnings.append(args))

    # 预期校验错误走 warning 分级，不刷 error 堆栈
    assert agent_runtime._format_tool_error(ValueError("内部连接信息")) == "工具执行失败: 内部连接信息"
    assert warnings
    assert agent_runtime._format_tool_error(RuntimeError("boom")) == "工具执行失败: boom"
    assert errors

    captured = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(name="agent")

    model_kwargs = {}

    def fake_create_streaming_chat_model(**kwargs):
        model_kwargs.update(kwargs)
        return FakeChatModel()

    monkeypatch.setattr(data_agent, "create_streaming_chat_model", fake_create_streaming_chat_model)
    monkeypatch.setattr(data_agent, "create_agent", fake_create_agent)

    from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions

    agent = data_agent.create_data_agent(
        checkpointer="ckpt",
        identity_context=data_agent.IdentityContext(user_id="u1"),
        datasource_context=data_agent.DatasourceContext(datasource_id="ds_a"),
        model_invocation=data_agent.ModelInvocationConfig(
            system_prompt="智能问数系统提示词", model_id="m1"
        ),
    )

    assert agent.name == "agent"
    assert model_kwargs["model_id"] == "m1"
    assert model_kwargs["stream_usage"] is True
    assert captured["checkpointer"] == "ckpt"
    assert captured["system_prompt"] == "智能问数系统提示词"
    assert captured["state_schema"] is data_agent.ChatDataAgentState
    assert captured["name"] == "chat_data_agent"
    assert [getattr(tool, "name", tool) for tool in captured["tools"]] == [
        "file_read",
        "file_edit",
        "file_write",
        "file_move",
        "file_copy",
        "file_delete",
        "file_glob",
        "file_grep",
        "file_diff",
        "dir_create",
        "dir_list",
        "dir_delete",
        "parse_document",
        "read_image",
        "present_artifacts",
        "get_current_time",
        "data_analyzing_tool",
        "calculator_tool",
        "ask_user_question",
        "officecli_run",
    ]
    assert len(captured["middleware"]) == 11
    assert captured["middleware"][0].name == "ImageInjectionMiddleware"
    assert captured["middleware"][1].name == "ToolTimingMiddleware"
    assert captured["middleware"][2].name == "ToolCallRateAlertMiddleware"
    assert captured["middleware"][3].name == "ToolCallHistoryRepairMiddleware"
    assert captured["middleware"][4].name == "TodoListMiddleware"
    assert captured["middleware"][5].name == "ContextCompressionMiddleware"
    assert captured["middleware"][6].name == "ContextUsageMiddleware"
    assert captured["middleware"][7].name == "ResponseTimestampMiddleware"
    assert captured["middleware"][8].name == "ToolCallNameNormalizerMiddleware"
    assert captured["middleware"][9].name == "ToolResultMiddleware"
    assert captured["middleware"][10].name == "ToolErrorMiddleware"
    assert data_agent.create_data_agent(AgentRuntimeOptions(), model_invocation=data_agent.ModelInvocationConfig(model_id="m1")).name == "agent"
    assert "智能问数与数据分析助手" in captured["system_prompt"]


@pytest.mark.asyncio
async def test_tool_error_middleware_returns_error_tool_message(monkeypatch):
    monkeypatch.setattr(agent_runtime.logger, "error", lambda *args, **_kwargs: None)
    middleware = ToolErrorMiddleware(agent_runtime.on_tool_error)

    async def handler(_request):
        raise RuntimeError("tool down")

    request = SimpleNamespace(tool=None, tool_call={"id": "call_1", "name": "data_analyzing_tool"})
    message = await middleware.awrap_tool_call(request, handler)

    assert message.content == "工具执行失败: tool down"
    assert message.name == "data_analyzing_tool"
    assert message.tool_call_id == "call_1"
    assert message.status == "error"


def test_message_integrity_keeps_complete_tool_groups_and_removes_orphans():
    messages = [
        HumanMessage(content="继续分析", id="turn-1"),
        AIMessage(
            content="",
            id="assistant-1",
            tool_calls=[
                {"id": "call-a", "name": "file_read", "args": {}},
                {"id": "call-b", "name": "calculator_tool", "args": {}},
            ],
        ),
        ToolMessage(content="结果A", tool_call_id="call-a"),
        ToolMessage(content="结果B", tool_call_id="call-b"),
        ToolMessage(content="孤立结果", tool_call_id="orphan"),
        AIMessage(content="最终回答", id="assistant-final"),
    ]

    repaired, report = message_integrity.repair_tool_call_messages(messages)

    assert [getattr(message, "id", None) for message in repaired[:4]] == [
        "turn-1", "assistant-1", repaired[2].id, repaired[3].id,
    ]
    assert [getattr(message, "content", None) for message in repaired] == [
        "继续分析", "", "结果A", "结果B", "最终回答",
    ]
    assert report["removed_call_ids"] == ()
    assert report["removed_message_count"] == 1


def test_message_integrity_removes_interrupted_single_tool_call_tail():
    messages = [
        HumanMessage(content="重新开始", id="turn-2"),
        AIMessage(
            content="",
            id="assistant-tool",
            tool_calls=[{"id": "call-pending", "name": "data_analyzing_tool", "args": {}}],
        ),
    ]

    repaired, report = message_integrity.repair_tool_call_messages(messages)

    assert repaired == [messages[0]]
    assert report["removed_call_ids"] == ("call-pending",)
    assert report["removed_message_count"] == 1


def test_message_integrity_removes_partially_completed_parallel_tool_group():
    messages = [
        HumanMessage(content="继续", id="turn-3"),
        AIMessage(
            content="",
            tool_calls=[
                {"id": "call-done", "name": "file_read", "args": {}},
                {"id": "call-pending", "name": "bash_run", "args": {}},
            ],
        ),
        ToolMessage(content="已完成", tool_call_id="call-done"),
    ]

    repaired, report = message_integrity.repair_tool_call_messages(messages)

    assert repaired == [messages[0]]
    assert report["removed_call_ids"] == ("call-done", "call-pending")
    assert report["removed_message_count"] == 2


@pytest.mark.asyncio
async def test_tool_call_history_repair_middleware_replaces_state_messages(monkeypatch):
    warnings = []
    monkeypatch.setattr(tool_call_history_repair.logger, "warning", lambda *args, **kwargs: warnings.append(args))
    middleware = tool_call_history_repair.create_tool_call_history_repair_middleware(state_schema=dict)
    messages = [
        HumanMessage(content="继续", id="turn-4"),
        AIMessage(content="", tool_calls=[{"id": "call-pending", "name": "file_read", "args": {}}]),
    ]

    update = await middleware.abefore_model({"messages": messages}, SimpleNamespace())

    assert isinstance(update["messages"][0], RemoveMessage)
    assert update["messages"][0].id == REMOVE_ALL_MESSAGES
    assert update["messages"][1:] == [messages[0]]
    assert warnings


def test_sql_assist_agent_uses_create_agent(monkeypatch):
    captured = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(name="sql_agent")

    monkeypatch.setattr(
        sql_assist_agent,
        "get_chat_data_llm",
        lambda streaming=True, **_kwargs: FakeChatModel(),
    )
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
