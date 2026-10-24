"""middleware 分支缺口补测：压缩/子智能体/上下文计量/消息完整性/MCP 发现。

覆盖点集中在各模块「异常兜底、防御性跳过、失败降级」分支：

- context_compression：压缩开始回调与事件推送失败、摘要重试轮不写 state、
  todo 快照跳过非 dict、硬上限超限抛错、非法数值兜底、分界事件无 turn_id；
- subagents：预算口径查询失败降级、结果预览截断、工具事件转发失败、
  SubagentMiddleware 同步/异步系统提示追加；
- context_usage：非 mapping usage 返回 None、摘要构造失败原样返回、
  时间戳写回失败只告警、缓存命中率缺输入时跳过、bool 不当 token 数、
  未知消息类型角色兜底；
- message_integrity：非法 tool_call 条目、无 id 组、孤儿 ToolMessage 修复；
- mcp_tool_discovery：直调拒绝的三种前置条件与未知结果原样返回。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import (
    AIMessage,
    ChatMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from apps.chat_data_app.agent.context.context_budget import ContextBudget
from apps.chat_data_app.agent.middleware import context_compression as compression
from apps.chat_data_app.agent.middleware import context_usage as usage
from apps.chat_data_app.agent.middleware import mcp_tool_discovery as discovery
from apps.chat_data_app.agent.middleware import message_integrity as integrity
from apps.chat_data_app.agent.middleware import subagents
from apps.chat_data_app.agent.middleware.context_compression import ContextInputLimitExceededError
from apps.chat_data_app.agent.middleware.mcp_tool_discovery import MCPToolDiscoveryMiddleware
from apps.chat_data_app.agent.middleware.subagents import SubagentMiddleware, SubagentSpec


def _ai(tool_calls: list) -> AIMessage:
    """构造允许携带非法 tool_calls 的 AIMessage，绕过 pydantic 校验。"""
    return AIMessage.model_construct(content="", tool_calls=tool_calls)


# ---------------------------------------------------------------------------
# context_compression
# ---------------------------------------------------------------------------


def test_notify_compression_started_swallows_callback_error():
    def _boom() -> None:
        raise RuntimeError("stream unavailable")

    compression._notify_compression_started(_boom)
    compression._notify_compression_started(None)


@pytest.mark.asyncio
async def test_build_update_skips_write_when_summary_retry_scheduled(monkeypatch):
    report = {"triggered": True, "summary_retry_next_turn": True}

    async def fake_compress(*_args, **_kwargs):
        return [], None, report

    monkeypatch.setattr(compression, "compress_messages_for_state", fake_compress)

    updates, summary, result_report = await compression.build_context_compression_update(
        [],
        static_input_tokens=0,
        compression_trigger_tokens=1,
        hard_input_limit_tokens=1000,
        compression_target_tokens=100,
    )

    assert updates is None
    assert summary is None
    assert result_report is report


def test_format_todo_snapshot_lines_skips_non_dict_items():
    lines, unfinished = compression.format_todo_snapshot_lines(
        [None, "junk", {"content": "补数据", "status": "in_progress"}]
    )

    assert lines == ["1. [进行中] 补数据"]
    assert unfinished == 1


@pytest.mark.asyncio
async def test_middleware_raises_when_estimated_after_exceeds_hard_limit(monkeypatch):
    budget = ContextBudget(
        context_window=1000,
        max_output_tokens=10,
        compression_trigger_ratio=0.7,
        hard_input_limit_tokens=10,
        compression_trigger_tokens=5,
        compression_target_tokens=1,
    )
    monkeypatch.setattr(
        compression.ContextRequestBudget,
        "configure",
        lambda self, system_prompt, tools, model_id=None: budget,
    )
    monkeypatch.setattr(compression, "get_context_compression_config", lambda: {"enabled": True})
    monkeypatch.setattr(compression, "_get_summary_input_limit_tokens", lambda _model_id: 1000)

    async def fake_update(*_args, **_kwargs):
        return None, None, {"estimated_tokens_after": 11, "triggered": False}

    monkeypatch.setattr(compression, "build_context_compression_update", fake_update)
    middleware = compression.create_context_compression_middleware(system_prompt="系统提示词", state_schema=dict)

    with pytest.raises(ContextInputLimitExceededError):
        await middleware.abefore_model({"messages": []}, SimpleNamespace())


def test_emit_context_compression_event_swallows_writer_error(monkeypatch):
    def _broken_writer():
        raise RuntimeError("no stream context")

    monkeypatch.setattr(compression, "get_stream_writer", _broken_writer)

    compression._emit_context_compression_event({"phase": "started"})


def test_non_negative_int_returns_zero_on_invalid_value():
    assert compression._non_negative_int("not-a-number") == 0
    assert compression._non_negative_int(-3) == 0
    assert compression._non_negative_int("7") == 7


def test_started_event_without_human_message_and_empty_turn_id():
    empty_event = compression._build_context_compression_started_event([])
    assert empty_event is not None
    assert empty_event["turn_id"] == ""

    event = compression._build_context_compression_started_event(
        [AIMessage(content="模型输出"), HumanMessage(content="用户问题", id="")]
    )
    assert event is not None
    assert event["turn_id"] == ""


def test_prepare_summary_input_breaks_when_nothing_removable(monkeypatch):
    monkeypatch.setattr(compression, "_drop_oldest_summary_round", lambda messages: ([], 0))
    messages = [HumanMessage(content="历史问题" * 500)]
    marker = compression._build_summary_omission_marker(1, 1)
    limit = compression._count_summary_input_tokens([marker], previous_summary=None)
    before = compression._count_summary_input_tokens(messages, previous_summary=None)
    assert before > limit

    prepared, report = compression._prepare_summary_input(
        messages,
        previous_summary=None,
        summary_input_limit_tokens=limit,
    )

    assert len(prepared) == 1
    assert prepared[0].additional_kwargs.get("context_compression_summary_omission") is True
    assert report["summary_input_pruned_messages"] == 0
    assert report["summary_input_unusable"] is False


def test_emit_compression_started_event_skips_when_builder_returns_none(monkeypatch):
    monkeypatch.setattr(compression, "_build_context_compression_started_event", lambda _messages: None)
    emitted: list = []
    monkeypatch.setattr(compression, "_emit_context_compression_event", lambda *args, **kwargs: emitted.append(args))

    compression._emit_compression_started_event([HumanMessage(content="问题")])

    assert emitted == []


def test_neutralize_stale_todo_tool_messages_replaces_content():
    first = ToolMessage(content="旧待办快照1", name="write_todos", tool_call_id="c1")
    second = ToolMessage(content="旧待办快照2", name="write_todos", tool_call_id="c2")

    result = compression.neutralize_stale_todo_tool_messages([first, second])

    assert result is not None
    assert result[0] is not first
    assert result[1] is not second
    assert "已由实时 state 快照" in result[0].content
    assert "已由实时 state 快照" in result[1].content
    assert first.content == "旧待办快照1"
    assert second.content == "旧待办快照2"


# ---------------------------------------------------------------------------
# subagents
# ---------------------------------------------------------------------------


def test_safe_subagent_budget_meta_returns_none_on_error(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise ValueError("budget config broken")

    monkeypatch.setattr(subagents, "_subagent_budget_meta", _boom)

    assert subagents._safe_subagent_budget_meta(SimpleNamespace(), []) is None


def test_truncate_subagent_result_truncates_long_content():
    preview = subagents._truncate_subagent_result("结" * 40000)

    assert "过长已截断" in preview
    assert preview.startswith("结结")
    assert len(preview) < 40000


def test_emit_subagent_tool_event_swallows_writer_error():
    def _broken_writer(_payload):
        raise RuntimeError("writer closed")

    subagents._emit_subagent_tool_event(_broken_writer, {"event_type": "tool_started"})
    subagents._emit_subagent_tool_event(None, {"event_type": "tool_started"})


def _subagent_middleware() -> SubagentMiddleware:
    spec = SubagentSpec(
        name="explore",
        description="深度分析",
        system_prompt="你是分析子智能体",
        tools=[],
        model=object(),
    )
    return SubagentMiddleware([spec])


def _prompt_request() -> SimpleNamespace:
    return SimpleNamespace(
        system_message=SystemMessage(content="主提示词"),
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )


def test_subagent_middleware_wrap_model_call_appends_prompt():
    middleware = _subagent_middleware()
    seen: dict = {}

    def handler(request):
        seen["request"] = request
        return "handled"

    assert middleware.wrap_model_call(_prompt_request(), handler) == "handled"
    assert "## 子智能体委派" in seen["request"].system_message.content


@pytest.mark.asyncio
async def test_subagent_middleware_awrap_model_call_appends_prompt():
    middleware = _subagent_middleware()
    seen: dict = {}

    async def handler(request):
        seen["request"] = request
        return "handled"

    assert await middleware.awrap_model_call(_prompt_request(), handler) == "handled"
    assert "## 子智能体委派" in seen["request"].system_message.content


def test_streaming_tool_call_filter_flushes_full_opener_marker():
    markup_filter = subagents._StreamingToolCallMarkupFilter()
    markup_filter._pending = subagents._TOOL_CALL_OPENER

    assert markup_filter.flush() == ""


def test_collect_update_messages_skips_removed_ids_and_keeps_new_messages():
    state = subagents._SubagentStreamState("call-1")
    removed = AIMessage(content="旧消息", id="m1")
    kept = AIMessage(content="新消息", id="m2")
    state.seen_ids.add("m1")

    new_messages = state.collect_update_messages({"node": {"messages": [RemoveMessage(id="m1"), removed, kept]}})

    assert new_messages == [kept]
    assert "m1" not in state.seen_ids
    assert "m2" in state.seen_ids


def test_subagent_report_token_limit_returns_none_for_zero_hard_limit():
    runtime = SimpleNamespace(state={"token_usage": {"hard_input_limit_tokens": 0}})

    assert subagents._subagent_report_token_limit(runtime) is None
    assert subagents._subagent_report_token_limit(SimpleNamespace(state={})) is None


def test_compile_subagent_appends_fence_when_run_controls_complete(monkeypatch):
    captured: dict = {}

    def fake_create_agent(_model, **kwargs):
        captured["middleware"] = kwargs["middleware"]
        return "compiled"

    monkeypatch.setattr(subagents, "create_agent", fake_create_agent)
    monkeypatch.setattr(subagents, "create_tool_execution_fence_middleware", lambda **_kwargs: "fence")
    run_controls = SimpleNamespace(
        is_complete=lambda: True,
        run_id="run-1",
        owner_token="owner-1",
        inbox_service=object(),
    )
    spec = SubagentSpec(name="explore", description="深度分析", system_prompt="sys", tools=[], model=object())

    assert subagents._compile_subagent(spec, run_controls=run_controls) == "compiled"
    assert "fence" in captured["middleware"]


# ---------------------------------------------------------------------------
# context_usage
# ---------------------------------------------------------------------------


def test_compact_token_usage_for_state_returns_none_for_non_mapping():
    assert usage.compact_token_usage_for_state(None) is None
    assert usage.compact_token_usage_for_state([]) is None


def test_merge_compression_summary_returns_original_when_builder_returns_none(monkeypatch):
    monkeypatch.setattr(usage, "build_runtime_context_summary_message", lambda _summary: None)

    prompt, messages = usage._merge_compression_summary_into_system_prompt(
        "系统提示词",
        [HumanMessage(content="历史")],
        context_summary="已压缩摘要",
    )

    assert prompt == "系统提示词"
    assert messages == [messages[0]]
    assert messages[0].content == "历史"


class _TimingWriteFailure:
    """模拟不可写 additional_kwargs 的响应消息，触发写回异常兜底。"""

    @property
    def additional_kwargs(self):
        return None

    @additional_kwargs.setter
    def additional_kwargs(self, _value):
        raise RuntimeError("message is frozen")


def test_stamp_response_timing_swallows_write_failure(monkeypatch):
    monkeypatch.setattr(usage, "_extract_response_message", lambda _response: _TimingWriteFailure())

    usage._stamp_response_timing(object(), started_at_ms=1, ended_at_ms=2)


def test_add_cache_hit_rate_skips_without_reported_input():
    payload: dict = {}
    usage._add_cache_hit_rate(payload, "current_cache_hit_rate", 5, None)
    usage._add_cache_hit_rate(payload, "cumulative_cache_hit_rate", 5, 10, has_reported_input=False)
    usage._add_cache_hit_rate(payload, "zero_input_rate", 5, 0)

    assert payload == {}


def test_cached_tokens_from_openai_usage_reads_token_details():
    assert usage._cached_tokens_from_openai_usage({"input_token_details": {"cache_read": 4}}) == 4
    assert usage._cached_tokens_from_openai_usage({"prompt_tokens_details": {"cached_tokens": 2}}) == 2
    assert usage._cached_tokens_from_openai_usage("not-a-dict") is None


def test_optional_non_negative_int_rejects_bool():
    assert usage._optional_non_negative_int(True) is None
    assert usage._optional_non_negative_int(False) is None
    assert usage._optional_non_negative_int(3) == 3


def test_message_role_falls_back_for_unknown_message_type():
    assert usage._message_role(ChatMessage(content="hi", role="user")) == "message"


# ---------------------------------------------------------------------------
# message_integrity
# ---------------------------------------------------------------------------


def test_message_tool_call_ids_skips_non_mapping_and_dedupes():
    message = _ai(["junk", {"id": "  "}, {"id": "call-1"}, {"id": "call-1"}])

    assert integrity.message_tool_call_ids(message) == ("call-1",)


def test_complete_boundaries_skips_ai_message_without_ids():
    messages = [_ai([{"name": "run_sql", "args": {}}]), HumanMessage(content="下一问")]

    assert integrity.complete_message_boundaries(messages) == [2]


def test_collect_group_result_ids_ignores_unrelated_tool_message():
    messages = [
        _ai([{"id": "call-1", "name": "run_sql", "args": {}}]),
        ToolMessage(content="其他结果", tool_call_id="call-2"),
        HumanMessage(content="下一问"),
    ]

    result_ids, next_index = integrity._collect_ai_group_result_ids(messages, 0, ("call-1",))

    assert result_ids == set()
    assert next_index == 2


def test_repair_removes_standalone_tool_messages():
    human = HumanMessage(content="问题")
    messages = [ToolMessage(content="孤儿结果", tool_call_id="call-9"), human]

    repaired, report = integrity.repair_tool_call_messages(messages)

    assert repaired == [human]
    assert report["removed_message_count"] == 1
    assert report["repaired"] is True


def test_skip_orphan_tool_results_drops_empty_ids_then_stops():
    empty_id = ToolMessage(content="缺少 id", tool_call_id="")
    unrelated = ToolMessage(content="无关结果", tool_call_id="call-9")
    messages = [empty_id, unrelated, HumanMessage(content="问题")]

    index, removed = integrity._skip_orphan_tool_results(messages, 0, ())

    assert index == 1
    assert removed == 1


# ---------------------------------------------------------------------------
# mcp_tool_discovery
# ---------------------------------------------------------------------------


def test_reject_direct_target_returns_none_for_inactive_and_non_mcp_requests():
    inactive = MCPToolDiscoveryMiddleware(search_active=False)
    assert inactive._reject_direct_target(SimpleNamespace(tool_call={"name": "mcp_sales", "id": "c1"})) is None

    active = MCPToolDiscoveryMiddleware(search_active=True)
    assert active._reject_direct_target(SimpleNamespace(tool_call=None)) is None
    assert active._reject_direct_target(SimpleNamespace(tool_call={"name": "file_read", "id": "c1"})) is None


def test_wrap_tool_call_returns_rejection_without_invoking_handler():
    middleware = MCPToolDiscoveryMiddleware(search_active=True)
    called: list = []

    result = middleware.wrap_tool_call(
        SimpleNamespace(tool_call={"name": "mcp_sales", "id": "call-1"}),
        lambda _request: called.append(True),
    )

    assert called == []
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call-1"


@pytest.mark.asyncio
async def test_awrap_tool_call_returns_rejection_without_invoking_handler():
    middleware = MCPToolDiscoveryMiddleware(search_active=True)
    called: list = []

    async def handler(_request):
        called.append(True)
        return ToolMessage(content="ok", tool_call_id="call-2")

    result = await middleware.awrap_tool_call(
        SimpleNamespace(tool_call={"name": "mcp_sales", "id": "call-2"}),
        handler,
    )

    assert called == []
    assert isinstance(result, ToolMessage)
    assert result.status == "error"


def test_capture_discovery_returns_unknown_result_unchanged(monkeypatch):
    monkeypatch.setattr(discovery, "_names_from_tool_result", lambda _result: ["mcp_sales"])
    middleware = MCPToolDiscoveryMiddleware(search_active=True)
    plain = "plain-result"

    assert middleware._capture_discovery(plain) is plain
