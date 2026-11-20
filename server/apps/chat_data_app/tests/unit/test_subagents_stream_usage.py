"""子智能体流式输出、上下文用量与报告卸载测试。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from apps.chat_data_app.agent.middleware import subagent_stream as subagent_stream_module
from apps.chat_data_app.agent.middleware import subagents as subagents_module
from apps.chat_data_app.agent.middleware.subagents import (
    SubagentSpec,
)
from apps.chat_data_app.tests.unit.subagent_test_support import build_subagent_tool, invoke_subagent_tool


def _spec(name="explore", **overrides):
    kwargs = {
        "name": name,
        "description": "深度分析子智能体",
        "system_prompt": "你是分析子智能体",
        "tools": [],
        "model": object(),
    }
    kwargs.update(overrides)
    return SubagentSpec(**kwargs)


def _fake_runtime(state=None, tool_call_id="call_1"):
    return SimpleNamespace(state=state or {}, tool_call_id=tool_call_id)


def test_extract_final_report_strips_tool_call_markup():
    """无工具轮次退化的裸 <tool_call> 标记被剥掉，不混进回传报告。"""
    messages = [
        AIMessage(
            content=(
                "<tool_call>\n<function=file_read>\n<parameter=file_path>/mnt/a.yaml</parameter>\n"
                "</function>\n</tool_call>\n子智能体已完成盘点。\n让我为你展示结果:"
            ),
            id="m1",
        )
    ]
    report = subagents_module._extract_final_report(messages)
    assert "<tool_call>" not in report
    assert "file_read" not in report
    assert report.startswith("子智能体已完成盘点。")

    # 悬空块（只有开头没有结尾）同样剥到末尾
    dangling = subagents_module._extract_final_report(
        [AIMessage(content="结论先行\n<tool_call>\n<function=list>", id="m2")]
    )
    assert dangling == "结论先行"

    # 全部是标记时报告为空（上层兜底为「未产出有效结论」提示）
    assert (
        subagents_module._extract_final_report(
            [AIMessage(content="<tool_call>\n<function=list>\n</function>\n</tool_call>", id="m3")]
        )
        == ""
    )


_RAW_MARKUP_MESSAGE = (
    "由于部分文件较长，我需要继续读取剩余内容。\n\n"
    "<tool_call>\n<function=file_read>\n<parameter=file_path>/mnt/a.yaml</parameter>\n</function>\n</tool_call>\n"
    "<tool_call>\n<function=file_read>\n<parameter=file_path>/mnt/b.yaml</parameter>\n</function>\n</tool_call>"
)


def test_streaming_markup_filter_strips_blocks_across_chunks():
    """裸 <tool_call> 标记被切成任意碎片时仍被流式剥除，普通文本保留。"""
    filt = subagents_module._StreamingToolCallMarkupFilter()
    # 逐字符喂入（最极端的碎片切分）
    out = "".join(filt.feed(ch) for ch in _RAW_MARKUP_MESSAGE) + filt.flush()
    assert "<tool_call" not in out
    assert "file_read" not in out
    assert out.startswith("由于部分文件较长，我需要继续读取剩余内容。")

    # 按不规则块喂入
    filt = subagents_module._StreamingToolCallMarkupFilter()
    out = (
        "".join(filt.feed(part) for part in ["正常开头\n<tool", "_call>\n<function=x>\n</tool_call>", "结尾文本"])
        + filt.flush()
    )
    assert out == "正常开头\n结尾文本"

    # 悬空块（只有开头没有结尾）：吞到消息末尾
    filt = subagents_module._StreamingToolCallMarkupFilter()
    out = filt.feed("结论先行\n<tool_call>\n<function=list>") + filt.flush()
    assert out == "结论先行\n"

    # 普通文本里的 "<" 不是标记开头，原样透传
    filt = subagents_module._StreamingToolCallMarkupFilter()
    out = filt.feed("a < b <toolx> c") + filt.flush()
    assert out == "a < b <toolx> c"

    # 普通文本不含任何 "<"：直通
    filt = subagents_module._StreamingToolCallMarkupFilter()
    assert filt.feed("纯文本增量") + filt.flush() == "纯文本增量"


def test_iter_chunk_stream_events_survives_fully_stripped_chunks():
    """filter 吞光整个 chunk 文本时不崩（回归：UnboundLocalError）。

    退化轮次里整块 <tool_call> 落在单个 chunk、或 inside 态下后续 chunk 全是
    标记内文本时，feed 返回空串——事件翻译必须静默产出空事件而不是崩溃
    （2026-09-21 深度 review 实锤：if allowed 悬空在 if text 之外导致
    UnboundLocalError，整个子图直播流被打断）。
    """
    # 整块标记在单个 chunk 内
    events = subagents_module._iter_chunk_stream_events(
        AIMessageChunk(content="<tool_call>xxx</tool_call>", id="m1"),
        "p1",
        {},
        {},
        {},
    )
    assert events == []

    # inside 态下后续 chunk 全为标记内文本
    filters = {}
    filters.setdefault("m2", subagents_module._StreamingToolCallMarkupFilter()).feed("<tool_call>begin ")
    events = subagents_module._iter_chunk_stream_events(
        AIMessageChunk(content="more junk", id="m2"),
        "p1",
        {},
        {},
        filters,
    )
    assert events == []

    # 标记闭合后的正常文本照常外发
    events = subagents_module._iter_chunk_stream_events(
        AIMessageChunk(content="tail</tool_call>收尾结论", id="m2"),
        "p1",
        {},
        {},
        filters,
    )
    assert len(events) == 1
    assert events[0]["delta"] == "收尾结论"


def test_full_message_events_and_trail_sanitize_markup():
    """完整消息事件与 trail 快照都不携带裸 <tool_call> 标记（历史回放路径）。"""
    event = subagents_module._subagent_message_event("m1", _RAW_MARKUP_MESSAGE, "call_1", already_streamed=False)
    assert "<tool_call" not in event["content"]
    assert event["content"].startswith("由于部分文件较长")

    trail = subagents_module._build_trail_snapshot([AIMessage(content=_RAW_MARKUP_MESSAGE, id="m1")])
    assert "<tool_call" not in trail[0]["content"]
    assert trail[0]["content"].startswith("由于部分文件较长")


def test_streamed_message_event_keeps_content_for_late_joiners():
    """快照晚加入的订阅者没有收到增量，完整消息帧必须自带正文与思考。"""
    event = subagents_module._subagent_message_event(
        "m1",
        "最终正文",
        "call_1",
        already_streamed=True,
        thinking="思考全文",
    )
    assert event["streamed"] is True
    assert event["content"] == "最终正文"
    assert event["thinking"] == "思考全文"
    assert event["thinking_truncated"] is False


@pytest.mark.asyncio
async def test_subagent_tool_streamed_deltas_exclude_markup(monkeypatch):
    """硬线摘工具轮次的退化文本流经增量通道时同样被剥除（直播路径）。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)
    markup = _RAW_MARKUP_MESSAGE

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            # 标记在增量边界被切碎（真实流式形态）
            yield "messages", (AIMessageChunk(content=markup[:40], id="m1"), {})
            yield "messages", (AIMessageChunk(content=markup[40:], id="m1"), {})
            yield "updates", {"model": {"messages": [AIMessage(content=markup, id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_subagent_tool([_spec()])

    await invoke_subagent_tool(
        tool, "dispatch", background=False, description="盘点", subagent_type="explore", runtime=_fake_runtime()
    )
    deltas = [payload["event"] for payload in written if payload["event"]["event_type"] == "subagent_message_delta"]
    joined = "".join(event["delta"] for event in deltas)
    assert "<tool_call" not in joined
    assert "file_read" not in joined
    assert joined.startswith("由于部分文件较长")


@pytest.mark.asyncio
async def test_subagent_tool_emits_context_usage_events_and_stamps_tool_message(monkeypatch):
    """每个节点更新后发 subagent_context_usage 事件，最终用量盖到 ToolMessage。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(compression_trigger_tokens=10_000, context_window=100_000),
    )

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="第一步结论", id="m1")]}}
            yield "updates", {"model": {"messages": [AIMessage(content="最终结论", id="m2")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_subagent_tool([_spec()])

    result = await invoke_subagent_tool(
        tool, "dispatch", background=False, description="盘点", subagent_type="explore", runtime=_fake_runtime()
    )

    usage_events = [
        payload["event"]
        for payload in written
        if isinstance(payload, dict)
        and payload.get("type") == "subagent_tool"
        and payload.get("event", {}).get("event_type") == "subagent_context_usage"
    ]
    # 两次节点更新各发一次，用量随消息累积单调不减
    assert len(usage_events) == 2
    assert usage_events[0]["used_tokens"] <= usage_events[1]["used_tokens"]
    assert usage_events[1]["threshold_tokens"] == 10_000
    assert usage_events[1]["context_window"] == 100_000
    # percent = 占模型窗口（与主对话同口径）；budget_percent = 占压缩触发阈值
    assert usage_events[1]["percent"] == usage_events[1]["used_tokens"] * 100 // 100_000
    assert usage_events[1]["budget_percent"] == usage_events[1]["used_tokens"] * 100 // 10_000
    stamped = result.update["messages"][0].additional_kwargs["subagent_context_usage"]
    assert stamped == {
        "used_tokens": usage_events[1]["used_tokens"],
        "threshold_tokens": usage_events[1]["threshold_tokens"],
        "context_window": usage_events[1]["context_window"],
        "percent": usage_events[1]["percent"],
        "budget_percent": usage_events[1]["budget_percent"],
    }


@pytest.mark.asyncio
async def test_subagent_usage_detail_flows_from_state_update_to_sse_and_snapshot():
    """updates 里的 token_usage 明细要同时进 SSE、回放快照与持久化产物。"""
    written = []
    detail = {
        "used": 1234,
        "input_tokens": 1234,
        "total": 64000,
        "breakdown": [
            {"key": "system_prompt", "label": "系统提示词", "tokens": 800, "percentage": 64.8},
            {"key": "tool_schema", "role": "tool_schema", "label": "工具定义", "tokens": 434, "percentage": 35.2},
        ],
    }

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield (
                "updates",
                {
                    "model": {
                        "messages": [AIMessage(content="结论", id="m1")],
                        "token_usage": detail,
                    }
                },
            )

    captured: dict = {}
    collected = await subagents_module._run_subagent_with_events(
        FakeRunnable(),
        {},
        {},
        written.append,
        collected=[],
        captured=captured,
        usage_meta={"static_tokens": 0, "threshold_tokens": 32000, "context_window": 64000},
    )

    usage_events = [
        payload["event"] for payload in written if payload["event"]["event_type"] == "subagent_context_usage"
    ]
    assert usage_events
    assert usage_events[-1]["token_usage_detail"]["breakdown"][0]["label"] == "系统提示词"
    assert captured["context_usage"]["token_usage_detail"]["breakdown"][1]["tokens"] == 434

    snapshot = subagents_module._subagent_process_snapshot(
        collected,
        captured["context_usage"],
        None,
        subagents_module._resolve_subagent_usage_detail(captured),
    )
    assert snapshot["subagent_token_usage_detail"]["breakdown"]


@pytest.mark.asyncio
async def test_subagent_live_context_event_carries_accumulated_billing_tokens():
    """运行中的上下文事件同时携带已累计的 billing token，前端无需等任务收口。"""
    written = []

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield (
                "updates",
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="第一步结论",
                                id="m1",
                                usage_metadata={
                                    "input_tokens": 1_200,
                                    "output_tokens": 120,
                                    "total_tokens": 1_320,
                                    "input_token_details": {"cache_read": 600},
                                    "output_token_details": {"reasoning": 80},
                                },
                                additional_kwargs={
                                    "started_at_ms": 1_700_000_000_000,
                                    "ended_at_ms": 1_700_000_001_000,
                                },
                            )
                        ]
                    }
                },
            )
            yield (
                "updates",
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="最终结论",
                                id="m2",
                                usage_metadata={
                                    "input_tokens": 2_000,
                                    "output_tokens": 200,
                                    "total_tokens": 2_200,
                                    "input_token_details": {"cache_read": 1_000},
                                },
                                additional_kwargs={
                                    "started_at_ms": 1_700_000_001_000,
                                    "ended_at_ms": 1_700_000_003_000,
                                },
                            )
                        ],
                        "token_usage": {
                            "llm_calls": [{"model": "SenseNova-Loop-9B-MLX-6bit"}],
                        },
                    }
                },
            )

    captured: dict = {}
    collected = await subagents_module._run_subagent_with_events(
        FakeRunnable(),
        {},
        {},
        written.append,
        collected=[],
        captured=captured,
        usage_meta={"static_tokens": 0, "threshold_tokens": 32_000, "context_window": 64_000},
    )

    usage_events = [
        payload["event"] for payload in written if payload["event"]["event_type"] == "subagent_context_usage"
    ]
    assert len(usage_events) == 2
    assert usage_events[0]["subagent_token_usage"] == {
        "input_tokens": 1_200,
        "output_tokens": 120,
        "total_tokens": 1_320,
        "cache_read_input_tokens": 600,
        "cache_creation_input_tokens": 0,
        "reasoning_tokens": 80,
    }
    assert usage_events[1]["subagent_token_usage"] == {
        "model": "SenseNova-Loop-9B-MLX-6bit",
        "input_tokens": 3_200,
        "output_tokens": 320,
        "total_tokens": 3_520,
        "cache_read_input_tokens": 1_600,
        "cache_creation_input_tokens": 0,
        "reasoning_tokens": 80,
    }
    snapshot = subagents_module._subagent_process_snapshot(
        collected,
        captured["context_usage"],
        model_name=captured.get("model"),
    )
    assert snapshot["subagent_token_usage"] == usage_events[1]["subagent_token_usage"]


def test_aggregate_subagent_token_usage_uses_decode_window_and_deducts_tools():
    """完成态速率与主对话同口径：首 token→流结束，扣工具时间，TTFT 不进分母。"""
    messages = [
        AIMessage(
            content="结论",
            id="m1",
            usage_metadata={"input_tokens": 100, "output_tokens": 200, "total_tokens": 300},
        )
    ]

    usage = subagents_module._aggregate_subagent_token_usage(
        messages,
        {
            "first_token_wall_ms": 1_000,
            "stream_ended_wall_ms": 3_000,
            "tool_duration_ms": 500,
        },
    )

    assert usage["output_tokens"] == 200
    assert usage["tokens_per_second"] == 133  # 200 / ((3000 - 1000 - 500) / 1000)


def test_iter_chunk_stream_events_samples_speed_before_preview_cap(monkeypatch):
    """实时速率采样使用封顶前的完整可见文本，避免超过预览上限后冻结。"""
    monkeypatch.setattr(subagent_stream_module, "SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS", 5)
    speed_chunks: list[str] = []
    text_caps: dict = {}

    first_events = subagents_module._iter_chunk_stream_events(
        AIMessageChunk(content="abcdef", id="m1"),
        "p1",
        text_caps,
        {},
        speed_chunks=speed_chunks,
    )
    second_events = subagents_module._iter_chunk_stream_events(
        AIMessageChunk(content="ghij", id="m1"),
        "p1",
        text_caps,
        {},
        speed_chunks=speed_chunks,
    )

    assert first_events[0]["truncated"] is True
    assert second_events == []
    assert speed_chunks == ["abcdef", "ghij"]


def test_emit_messages_chunk_keeps_speed_events_after_preview_cap(monkeypatch):
    """预览已达上限后，速率滑窗仍按封顶前文本持续产出事件。"""
    monkeypatch.setattr(subagent_stream_module, "SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS", 5)
    now_ms = {"value": 1_000}
    monkeypatch.setattr(subagent_stream_module.time, "time", lambda: now_ms["value"] / 1000)

    state = subagent_stream_module._SubagentStreamState("parent-1")
    emitted: list[dict] = []

    state.emit_messages_chunk(AIMessageChunk(content="abcdef", id="m1"), emitted.append)
    now_ms["value"] += 600
    state.emit_messages_chunk(AIMessageChunk(content="ghij", id="m1"), emitted.append)

    speed_events = [
        item["event"]
        for item in emitted
        if item.get("type") == "subagent_tool"
        and item.get("event", {}).get("event_type") == "subagent_generation_speed"
    ]
    assert len(speed_events) == 2
    assert speed_events[1]["tokens_per_second"] > 0


@pytest.mark.asyncio
async def test_subagent_tool_keeps_report_without_budget_notice(monkeypatch):
    """上下文超过窗口口径时不再改写报告正文，执行链路只依赖压缩机制。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(compression_trigger_tokens=1),
    )

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="提前收尾的盘点结论", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_subagent_tool([_spec()])

    result = await invoke_subagent_tool(
        tool, "dispatch", background=False, description="盘点", subagent_type="explore", runtime=_fake_runtime()
    )

    message = result.update["messages"][0]
    assert message.content == "提前收尾的盘点结论"
    assert "子智能体预算提示" not in message.content
    assert message.status != "error"


@pytest.mark.asyncio
async def test_subagent_tool_offloads_report_over_parent_budget(monkeypatch):
    """报告超过父上下文预算占比时落盘外置，正文换成统一信封。"""
    calls: list[dict] = []

    async def fake_externalize(content, *, source_tool, state, suffix, preview_chars):
        calls.append(
            {
                "content": content,
                "source_tool": source_tool,
                "state": state,
                "suffix": suffix,
                "preview_chars": preview_chars,
            }
        )
        return "[tool_result_offloaded]\nsource_tool=subagent", {"source_tool": "subagent"}

    monkeypatch.setattr(subagents_module, "externalize_tool_text", fake_externalize)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="结论" * 3000, id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_subagent_tool([_spec()])
    runtime = _fake_runtime(
        state={
            "token_usage": {"hard_input_limit_tokens": 1000},
            "workspace": {"workspace_root": "/tmp/ws"},
        }
    )

    result = await invoke_subagent_tool(
        tool,
        "dispatch",
        background=False,
        description="盘点",
        subagent_type="explore",
        runtime=runtime,
    )
    message = result.update["messages"][0]
    assert message.content.startswith("[tool_result_offloaded]")
    assert message.status == "success"
    assert calls[0]["source_tool"] == "subagent"
    assert calls[0]["suffix"] == ".md"
    assert calls[0]["preview_chars"] == subagents_module.SUBAGENT_REPORT_PREVIEW_CHARS
    assert calls[0]["state"]["token_usage"]["hard_input_limit_tokens"] == 1000


@pytest.mark.asyncio
async def test_subagent_tool_keeps_report_within_parent_budget(monkeypatch):
    """报告未超父上下文预算时不外置，正文保持结论原文。"""

    async def fake_externalize(*_args, **_kwargs):
        raise AssertionError("未超预算时不应触发外置")

    monkeypatch.setattr(subagents_module, "externalize_tool_text", fake_externalize)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="简短结论", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_subagent_tool([_spec()])
    runtime = _fake_runtime(state={"token_usage": {"hard_input_limit_tokens": 1_000_000}})

    result = await invoke_subagent_tool(
        tool,
        "dispatch",
        background=False,
        description="盘点",
        subagent_type="explore",
        runtime=runtime,
    )
    message = result.update["messages"][0]
    assert message.content == "简短结论"
    assert message.status == "success"


@pytest.mark.asyncio
async def test_subagent_tool_keeps_report_without_parent_usage(monkeypatch):
    """父 state 没有用量时无法按预算封顶，保持原文（不猜窗口大小）。"""

    async def fake_externalize(*_args, **_kwargs):
        raise AssertionError("缺少父用量时不应触发外置")

    monkeypatch.setattr(subagents_module, "externalize_tool_text", fake_externalize)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="结论" * 3000, id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_subagent_tool([_spec()])

    result = await invoke_subagent_tool(
        tool, "dispatch", background=False, description="盘点", subagent_type="explore", runtime=_fake_runtime()
    )

    message = result.update["messages"][0]
    assert message.content == "结论" * 3000


# ---------------------------------------------------------------------------
# 子图压缩（ContextCompressionMiddleware 复用）：collect 记账重建 + 摘要注入
# ---------------------------------------------------------------------------


def test_collect_update_messages_rebuilds_after_compression():
    """压缩更新（REMOVE_ALL + compressed 列表）到达时重建记账且不重复外发。

    压缩保留的是早前已见过的同一批消息对象：collected 必须收缩为保留集
    （丢弃的消息不再计入用量、trail 与报告），且保留消息不作为「新增」
    再次外发事件；压缩之后的正常轮次照常收集与外发。
    """
    state = subagents_module._SubagentStreamState("task-1")
    ai1 = AIMessage(content="第一轮结论", id="a1")
    tool_msg = ToolMessage(content="查询结果", tool_call_id="t1")
    ai2 = AIMessage(content="第二轮结论", id="a2")

    state.collect_update_messages({"model": {"messages": [ai1, tool_msg]}})
    state.collect_update_messages({"model": {"messages": [ai2]}})
    assert state.collected == [ai1, tool_msg, ai2]

    # 压缩节点更新：全量替换为 [ai1, tool_msg]，ai2 被摘要吸收后丢弃
    new_messages = state.collect_update_messages(
        {
            "ContextCompressionMiddleware.before_model": {
                "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), ai1, tool_msg],
            },
        }
    )
    assert state.collected == [ai1, tool_msg]
    # 保留消息是已见过的同一批对象：不再作为新增外发，避免前端重复条目
    assert new_messages == []

    # 压缩之后的正常轮次：新消息照常收集并外发
    ai3 = AIMessage(content="压缩后继续", id="a3")
    new_messages = state.collect_update_messages({"model": {"messages": [ai3]}})
    assert new_messages == [ai3]
    assert state.collected == [ai1, tool_msg, ai3]


def test_collect_update_messages_handles_targeted_removal():
    """按 id 的定点 RemoveMessage：只摘除对应消息，其余记账保持。"""
    state = subagents_module._SubagentStreamState("task-1")
    ai1 = AIMessage(content="第一条", id="a1")
    ai2 = AIMessage(content="第二条", id="a2")
    state.collect_update_messages({"model": {"messages": [ai1, ai2]}})

    new_messages = state.collect_update_messages(
        {
            "model": {"messages": [RemoveMessage(id="a1")]},
        }
    )
    assert state.collected == [ai2]
    assert new_messages == []


@pytest.mark.asyncio
async def test_subagent_tool_keeps_report_when_offload_fails(monkeypatch):
    """落盘失败不能丢掉子智能体结论，也不得误报已外置。"""

    async def failed_externalize(*_args, **_kwargs):
        return "[tool_result_truncated]", None

    monkeypatch.setattr(subagents_module, "externalize_tool_text", failed_externalize)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="关键结论" * 3000, id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_subagent_tool([_spec()])
    runtime = _fake_runtime(
        state={
            "token_usage": {"hard_input_limit_tokens": 1000},
            "workspace": {"workspace_root": "/tmp/ws"},
        }
    )

    result = await invoke_subagent_tool(
        tool,
        "dispatch",
        background=False,
        description="盘点",
        subagent_type="explore",
        runtime=runtime,
    )
    message = result.update["messages"][0]
    assert message.content == "关键结论" * 3000
    assert message.status == "success"
