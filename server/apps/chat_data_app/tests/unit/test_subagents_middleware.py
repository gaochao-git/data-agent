"""子智能体中间件测试：task 工具派发、状态透传白名单与系统提示合并。"""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage, ToolMessage
from types import SimpleNamespace

from apps.chat_data_app.agent.middleware import subagents as subagents_module
from apps.chat_data_app.agent.middleware.subagents import (
    SUBAGENT_STATE_PASSTHROUGH_KEYS,
    SubagentMiddleware,
    SubagentSpec,
    build_subagent_invocation_config,
    build_task_tool,
    validate_subagent_specs,
)


def _spec(name="data-explore", **overrides):
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


def test_validate_subagent_specs_rejects_empty_and_duplicates():
    with pytest.raises(ValueError, match="至少需要定义一个子智能体"):
        validate_subagent_specs([])
    with pytest.raises(ValueError, match="名称不能为空"):
        validate_subagent_specs([_spec(name="  ")])
    with pytest.raises(ValueError, match="名称重复"):
        validate_subagent_specs([_spec(name="a"), _spec(name="a")])


def test_build_subagent_state_only_passes_whitelisted_keys():
    runtime = _fake_runtime({
        "user_id": "u1",
        "workspace": {"root": "/mnt/user-data"},
        "token_usage": {"input": 1},
        "context_summary": "should-not-leak",
    })

    state = subagents_module._build_subagent_state(runtime, "分析入金趋势")

    assert state["user_id"] == "u1"
    assert state["workspace"] == {"root": "/mnt/user-data"}
    assert "token_usage" not in state
    assert "context_summary" not in state
    assert [message.content for message in state["messages"]] == ["分析入金趋势"]


def test_build_subagent_invocation_config_cuts_callbacks():
    config = build_subagent_invocation_config(_spec(recursion_limit=12))

    assert config["callbacks"] == []
    assert config["recursion_limit"] == 12
    assert config["configurable"] == {"chatdata_subagent": "data-explore"}


@pytest.mark.asyncio
async def test_task_tool_returns_final_report_as_tool_message(monkeypatch):
    captured = {}

    class FakeRunnable:
        async def astream(self, state, config, stream_mode):
            captured["state"] = state
            captured["config"] = config
            captured["stream_mode"] = stream_mode
            yield "updates", {"model": {"messages": [AIMessage(content="近30天入金呈上升趋势")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="分析入金趋势",
        subagent_type="data-explore",
        runtime=_fake_runtime({"user_id": "u1"}),
    )

    assert isinstance(result.update["messages"][0], ToolMessage)
    assert result.update["messages"][0].content == "近30天入金呈上升趋势"
    assert captured["state"]["messages"][0].content == "分析入金趋势"
    assert captured["state"]["user_id"] == "u1"
    assert captured["config"]["callbacks"] == []
    assert captured["stream_mode"] == ["updates", "messages"]


@pytest.mark.asyncio
async def test_task_tool_falls_back_when_subagent_returns_empty_report(monkeypatch):
    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": []}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="分析", subagent_type="data-explore", runtime=_fake_runtime()
    )

    assert "未产出有效结论" in result.update["messages"][0].content


@pytest.mark.asyncio
async def test_task_tool_forwards_inner_tool_events_to_parent_stream(monkeypatch):
    """内层工具生命周期事件经 custom 通道转发，带 parent_tool_call_id。"""
    written = []
    monkeypatch.setattr(
        subagents_module, "get_stream_writer", lambda: written.append
    )

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            message = AIMessage.model_construct(
                content="",
                tool_calls=[{"id": "inner-1", "name": "calculator_tool", "args": '{"expression": "1 + 1"}'}],
            )
            yield "updates", {"model": {"messages": [message]}}
            yield "updates", {"tools": {"messages": [ToolMessage(
                content="2", tool_call_id="inner-1", name="calculator_tool",
            )]}}
            yield "updates", {"model": {"messages": [AIMessage(content="1 + 1 = 2")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="算一下 1+1", subagent_type="data-explore", runtime=_fake_runtime()
    )

    assert result.update["messages"][0].content == "1 + 1 = 2"
    # 工具开始 → 工具结束 → 子智能体最终叙述
    assert [payload["event"]["event_type"] for payload in written] == [
        "tool_started", "tool_completed", "subagent_message",
    ]
    started = written[0]
    completed = written[1]
    assert written[2]["event"]["content"] == "1 + 1 = 2"
    assert started == {"type": "subagent_tool", "event": {
        "event_type": "tool_started",
        "tool_call_id": "inner-1",
        "tool_name": "calculator_tool",
        "args": {"expression": "1 + 1"},
        "parent_tool_call_id": "call_1",
    }}
    assert completed["type"] == "subagent_tool"
    event = completed["event"]
    assert event["event_type"] == "tool_completed"
    assert event["tool_call_id"] == "inner-1"
    assert event["content"] == "2"
    assert event["truncated"] is False
    assert event["parent_tool_call_id"] == "call_1"


@pytest.mark.asyncio
async def test_task_tool_survives_missing_stream_writer(monkeypatch):
    """无 custom 流上下文（如离线批处理）时事件转发静默降级，不影响结论回传。"""
    def _raise():
        raise RuntimeError("outside graph context")

    monkeypatch.setattr(subagents_module, "get_stream_writer", _raise)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="结论")]}} 

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="分析", subagent_type="data-explore", runtime=_fake_runtime()
    )

    assert result.update["messages"][0].content == "结论"


def test_iter_subagent_stream_events_truncates_long_results():
    long_text = "x" * (subagents_module.SUBAGENT_RESULT_PREVIEW_MAX_CHARS + 100)

    events = subagents_module._iter_subagent_stream_events([
        ToolMessage(content=long_text, tool_call_id="inner-1", name="file_read"),
        ToolMessage(content="失败原因", tool_call_id="inner-2", name="semantic_query_tool", status="error"),
    ], parent_tool_call_id="call_1")

    assert len(events) == 2
    truncated = events[0]
    assert truncated["event_type"] == "tool_completed"
    assert truncated["truncated"] is True
    assert len(truncated["content"]) < len(long_text)
    assert "已截断" in truncated["content"]
    failed = events[1]
    assert failed["event_type"] == "tool_failed"
    assert failed["content"] == "失败原因"
    assert failed["truncated"] is False


def test_iter_subagent_stream_events_emits_text_before_tool_calls():
    """同一条 AIMessage 既有文本又有工具调用时按「先说后做」输出。"""
    message = AIMessage(
        content="先算一下 1+1",
        tool_calls=[{"id": "inner-1", "name": "calculator_tool", "args": {"expression": "1 + 1"}}],
    )

    events = subagents_module._iter_subagent_stream_events([message], parent_tool_call_id="call_1")

    assert [event["event_type"] for event in events] == ["subagent_message", "tool_started"]
    assert events[0]["content"] == "先算一下 1+1"
    assert events[0]["truncated"] is False
    assert events[0]["parent_tool_call_id"] == "call_1"
    assert events[0]["message_id"] == message.id
    assert events[1]["tool_call_id"] == "inner-1"


def test_iter_subagent_stream_events_skips_blank_text_and_truncates_long_text():
    long_text = "分析" * (subagents_module.SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS + 10)
    events = subagents_module._iter_subagent_stream_events([
        AIMessage(content="   "),
        AIMessage(content=""),
        AIMessage(content=long_text),
    ], parent_tool_call_id="call_1")

    assert len(events) == 1
    assert events[0]["event_type"] == "subagent_message"
    assert events[0]["truncated"] is True
    assert len(events[0]["content"]) < len(long_text)
    assert "已截断" in events[0]["content"]


def test_iter_subagent_stream_events_coerces_string_args():
    import json

    # langchain_core 1.5.4 构造 AIMessage 时强校验 args 必须 dict，
    # 服务端版本宽松会落出字符串形态，用 model_construct 模拟
    message = AIMessage.model_construct(
        content="",
        tool_calls=[
            {"id": "a", "name": "calculator_tool", "args": json.dumps({"expression": "1+1"})},
            {"id": "b", "name": "file_read", "args": json.dumps(json.dumps({"path": "/a"}))},
        ],
    )

    events = subagents_module._iter_subagent_stream_events([message], parent_tool_call_id="call_1")

    assert events[0]["args"] == {"expression": "1+1"}
    assert events[1]["args"] == {"path": "/a"}


@pytest.mark.asyncio
async def test_task_tool_rejects_unknown_subagent_type():
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="分析", subagent_type="no-such-agent", runtime=_fake_runtime()
    )

    assert "类型不存在" in result
    assert "data-explore" in result


def test_middleware_appends_system_prompt_as_system_message():
    request = SimpleNamespace(
        system_message=SystemMessage(content="主提示词"),
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    merged = subagents_module._append_system_prompt(request, "## 子智能体委派")

    system_message = merged.system_message
    assert isinstance(system_message, SystemMessage)
    assert system_message.content.startswith("主提示词")
    assert "## 子智能体委派" in system_message.content


def test_middleware_creates_system_message_when_request_has_none():
    request = SimpleNamespace(
        system_message=None,
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    merged = subagents_module._append_system_prompt(request, "## 子智能体委派")

    assert isinstance(merged.system_message, SystemMessage)
    assert "## 子智能体委派" in merged.system_message.content


def test_middleware_registers_single_task_tool_with_agent_catalog():
    middleware = SubagentMiddleware([_spec(), _spec(name="report-writer")])

    assert [tool.name for tool in middleware.tools] == ["task"]
    assert middleware.subagent_names == {"data-explore", "report-writer"}
    assert "data-explore" in middleware.tools[0].description
    assert "report-writer" in middleware.tools[0].description


def test_passthrough_keys_cover_subagent_state_schema_fields():
    """白名单必须覆盖子智能体状态 schema 声明的全部业务字段（不含基类框架字段）。"""
    from apps.chat_data_app.agent.middleware.subagents import ChatDataSubAgentState

    framework_keys = {"messages", "jump_to", "structured_response"}
    schema_keys = {
        key for key in ChatDataSubAgentState.__annotations__ if key not in framework_keys
    }
    assert schema_keys and schema_keys <= set(SUBAGENT_STATE_PASSTHROUGH_KEYS)


@pytest.mark.asyncio
async def test_task_tool_streams_text_and_thinking_deltas(monkeypatch):
    """正文/思考以 token 级增量经 custom 流转发；完整消息只补收敛标记不重复带内容。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            assert stream_mode == ["updates", "messages"]
            yield "messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "先查数据"}, id="m1"), {})
            yield "messages", (AIMessageChunk(content="结论是", id="m1"), {})
            yield "messages", (AIMessageChunk(content="上升", id="m1"), {})
            yield "updates", {"model": {"messages": [AIMessage(content="结论是上升", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="分析", subagent_type="data-explore", runtime=_fake_runtime()
    )

    assert result.update["messages"][0].content == "结论是上升"
    assert [payload["event"]["event_type"] for payload in written] == [
        "subagent_thinking_delta", "subagent_message_delta", "subagent_message_delta", "subagent_message",
    ]
    thinking_delta = written[0]["event"]
    assert thinking_delta["delta"] == "先查数据"
    assert thinking_delta["message_id"] == "m1"
    assert thinking_delta["parent_tool_call_id"] == "call_1"
    assert [payload["event"]["delta"] for payload in written[1:3]] == ["结论是", "上升"]
    # 收敛：完整消息事件不再带内容，仅标记已流出
    final_event = written[3]["event"]
    assert final_event["streamed"] is True
    assert final_event["content"] == ""
    assert final_event["truncated"] is False


@pytest.mark.asyncio
async def test_task_tool_delta_streaming_caps_cumulative_text(monkeypatch):
    """正文增量累计超过上限后停止转发，完整消息事件带截断标记。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)
    cap = subagents_module.SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "messages", (AIMessageChunk(content="x" * (cap - 5), id="m1"), {})
            # 最后一小段触发封顶：只外发剩余 5 字符 + 截断标记
            yield "messages", (AIMessageChunk(content="yyyyyyyy", id="m1"), {})
            yield "messages", (AIMessageChunk(content="不会外发", id="m1"), {})
            yield "updates", {"model": {"messages": [AIMessage(content="x" * cap + "yyyyyyyy", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="分析", subagent_type="data-explore", runtime=_fake_runtime()
    )

    assert result.update["messages"][0].content == "x" * cap + "yyyyyyyy"
    deltas = [payload["event"] for payload in written if payload["event"]["event_type"] == "subagent_message_delta"]
    assert len(deltas) == 2
    assert deltas[0]["truncated"] is False
    assert len(deltas[1]["delta"]) == 5 + len("\n…[内容过长已截断]")
    assert deltas[1]["truncated"] is True
    # 封顶后的增量不再外发，完整消息仅补截断标记
    final_event = written[-1]["event"]
    assert final_event["streamed"] is True
    assert final_event["truncated"] is True


def test_iter_chunk_stream_events_keeps_text_alongside_tool_call_chunks():
    """正文与工具调用增量同 chunk 时正文必须转发（丢内容回归）。

    OpenAI 兼容实现里 ``content`` 与 ``tool_calls`` 是独立字段，同一 chunk 完全
    可以「文本 + 工具增量」同时到达。若整块跳过，这段文本永远补不回来——该消息
    已因先前的 delta 进入 ``streamed_text_ids``，完整消息事件按收敛策略只补标记、
    不再携带 content。工具参数碎片也不会漏进来：``_chunk_text_delta`` 只取
    ``str`` 与 ``type == "text"`` 的内容块。
    """
    text_caps: dict = {}

    tool_chunk = AIMessageChunk(
        content="配置文件",
        tool_call_chunks=[{"name": "file_read", "args": '{"path":', "id": "c1", "index": 0, "type": "tool_call_chunk"}],
        id="m1",
    )
    events = subagents_module._iter_chunk_stream_events(tool_chunk, "call_1", text_caps, {})
    assert [event["event_type"] for event in events] == ["subagent_message_delta"]
    assert events[0]["delta"] == "配置文件"
    assert events[0]["message_id"] == "m1"

    # content blocks 形式：非 text 块（工具调用等）不参与正文增量
    block_chunk = AIMessageChunk(
        content=[{"type": "text", "text": "正文片段"}, {"type": "tool_use", "name": "file_read", "input": {}}],
        id="m2",
    )
    events = subagents_module._iter_chunk_stream_events(block_chunk, "call_1", text_caps, {})
    assert [event["delta"] for event in events] == ["正文片段"]

    # 无 message_id 的 chunk 不增量转发（由完整消息事件兜底）
    anonymous = AIMessageChunk(content="无id文本")
    assert subagents_module._iter_chunk_stream_events(anonymous, "call_1", {}, {}) == []

    # 非 AIMessageChunk（如 ToolMessageChunk）不产生文本增量
    assert subagents_module._iter_chunk_stream_events(ToolMessage(content="x", tool_call_id="c"), "call_1", {}, {}) == []


@pytest.mark.asyncio
async def test_task_tool_keeps_text_when_chunk_carries_tool_call(monkeypatch):
    """正文与工具增量同 chunk：前端拼出的全文必须等于完整消息（丢内容回归）。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "messages", (AIMessageChunk(content="我需要先读取", id="m1"), {})
            # 文本与工具增量同时到达（OpenAI 兼容实现常见）
            yield "messages", (
                AIMessageChunk(
                    content="配置文件",
                    tool_call_chunks=[{"name": "file_read", "args": '{"path":', "id": "c1", "index": 0, "type": "tool_call_chunk"}],
                    id="m1",
                ),
                {},
            )
            yield "updates", {"model": {"messages": [AIMessage(content="我需要先读取配置文件", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    await tool.coroutine(description="分析", subagent_type="data-explore", runtime=_fake_runtime())

    events = [payload["event"] for payload in written]
    deltas = [event["delta"] for event in events if event["event_type"] == "subagent_message_delta"]
    final = next(event for event in events if event["event_type"] == "subagent_message")
    # 复刻前端收敛口径：streamed 为真时展示增量拼出的全文
    shown = "".join(deltas) if final["streamed"] else final["content"]
    assert shown == "我需要先读取配置文件"


@pytest.mark.asyncio
async def test_task_tool_assigns_fallback_id_to_messages_without_id(monkeypatch):
    """provider 不返回 chunk id 时，完整消息事件仍带可用 message_id。

    前端以 message_id 作为 trail 条目主键，空 id 会被整条丢弃——不能把「中间文本
    是否可见」押在 provider 一定返回 id 上。
    """
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)
    shared_message = AIMessage(content="无id完整")

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            # 无 id 的增量不转发
            yield "messages", (AIMessageChunk(content="无id增量"), {})
            # 同一条无 id 消息出现在两个节点的 updates 里
            yield "updates", {"model": {"messages": [shared_message]}}
            yield "updates", {"tools": {"messages": [shared_message]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    await tool.coroutine(description="分析", subagent_type="data-explore", runtime=_fake_runtime())

    finals = [
        payload["event"] for payload in written if payload["event"]["event_type"] == "subagent_message"
    ]
    # 同一对象收敛到同一个兜底 id，去重后只发一次
    assert len(finals) == 1
    assert finals[0]["message_id"] == "call_1:anon-1"
    assert finals[0]["content"] == "无id完整"
    assert finals[0]["streamed"] is False


def test_stream_cap_state_returns_none_after_cap():
    cap = subagents_module._StreamCapState()
    assert cap.take("abc", 5) == ("abc", False)
    assert cap.take("d", 5) == ("d", False)
    # 超出剩余额度：外发剩余 1 字符 + 截断标记
    assert cap.take("efg", 5) == ("e" + "\n…[内容过长已截断]", True)
    assert cap.capped is True
    assert cap.take("more", 5) == (None, False)

    # 恰好填满后再来的增量：无内容可带，封顶标记由完整消息事件的 truncated 兜底
    exact = subagents_module._StreamCapState()
    assert exact.take("abcde", 5) == ("abcde", False)
    assert exact.take("f", 5) == (None, False)
    assert exact.capped is True


# ---- 子智能体过程快照（历史回放持久化） ----

def test_build_trail_snapshot_mirrors_sse_shape():
    """快照与前端 SubagentTrailItem 同构：先说后做、工具回填终态。"""
    messages = [
        AIMessage(
            content="先算一下",
            additional_kwargs={"reasoning_content": "需要精确计算"},
            tool_calls=[{"id": "inner-1", "name": "calculator_tool", "args": {"expression": "1 + 1"}}],
        ),
        ToolMessage(content="2", tool_call_id="inner-1", name="calculator_tool"),
        AIMessage(content="1 + 1 = 2", id="m-final"),
    ]

    trail = subagents_module._build_trail_snapshot(messages)

    assert [item["kind"] for item in trail] == ["message", "tool", "message"]
    first = trail[0]
    assert first["id"].startswith("trail-anon-")  # 无 id 的 AIMessage 也要有稳定 key
    assert first["content"] == "先算一下"
    assert first["thinking"] == "需要精确计算"
    assert first["truncated"] is False and first["thinkingTruncated"] is False
    tool_item = trail[1]
    assert tool_item == {
        "kind": "tool",
        "id": "inner-1",
        "tool_name": "calculator_tool",
        "args": {"expression": "1 + 1"},
        "result": "2",
        "status": "completed",
    }
    assert trail[2] == {
        "kind": "message",
        "id": "m-final",
        "content": "1 + 1 = 2",
        "truncated": False,
        "thinking": "",
        "thinkingTruncated": False,
    }


def test_build_trail_snapshot_marks_error_and_truncates():
    long_text = "x" * (subagents_module.SUBAGENT_RESULT_PREVIEW_MAX_CHARS + 100)
    messages = [
        AIMessage.model_construct(
            content="",
            tool_calls=[{"id": "inner-1", "name": "file_read", "args": {"path": "/a"}}],
        ),
        ToolMessage(content=long_text, tool_call_id="inner-1", name="file_read", status="error"),
    ]

    trail = subagents_module._build_trail_snapshot(messages)

    # 纯工具调用消息（无文本无思考）不产生 message 条目
    assert [item["kind"] for item in trail] == ["tool"]
    tool_item = trail[0]
    assert tool_item["status"] == "error"
    assert tool_item["result"].startswith("x" * 10)
    assert "已截断" in tool_item["result"]
    assert len(tool_item["result"]) < len(long_text)


def test_build_trail_snapshot_skips_blank_and_deduplicates():
    messages = [
        AIMessage(content="   "),
        AIMessage(content="结论", id="m1"),
        AIMessage(content="结论", id="m1"),  # 同 id 重复出现（多节点 updates）
    ]

    trail = subagents_module._build_trail_snapshot(messages)

    assert len(trail) == 1
    assert trail[0]["id"] == "m1"
    assert trail[0]["content"] == "结论"


@pytest.mark.asyncio
async def test_task_tool_stamps_trail_snapshot_on_tool_message(monkeypatch):
    """task ToolMessage 携带过程快照，随消息持久化供历史回放还原。"""

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(
                content="开始计算",
                tool_calls=[{"id": "inner-1", "name": "calculator_tool", "args": {"expression": "1 + 1"}}],
            )]}}
            yield "updates", {"tools": {"messages": [ToolMessage(
                content="2", tool_call_id="inner-1", name="calculator_tool",
            )]}}
            yield "updates", {"model": {"messages": [AIMessage(content="1 + 1 = 2", id="m-final")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="算一下 1+1", subagent_type="data-explore", runtime=_fake_runtime()
    )

    tool_message = result.update["messages"][0]
    trail = tool_message.additional_kwargs["subagent_trail"]
    assert [item["kind"] for item in trail] == ["message", "tool", "message"]
    assert trail[1]["result"] == "2"
    assert trail[2]["content"] == "1 + 1 = 2"


@pytest.mark.asyncio
async def test_task_tool_omits_trail_when_no_inner_activity(monkeypatch):
    """子图完全无可见输出时不塞空快照；仅有最终结论时快照与直播一致（含该消息）。"""

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": []}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="分析", subagent_type="data-explore", runtime=_fake_runtime()
    )

    tool_message = result.update["messages"][0]
    assert "subagent_trail" not in tool_message.additional_kwargs


@pytest.mark.asyncio
async def test_task_tool_trail_includes_final_message_like_live_stream(monkeypatch):
    """最终结论消息在直播中本就作为 subagent_message 出现在 trail 尾部，快照保持一致。"""

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="直接给出结论", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="分析", subagent_type="data-explore", runtime=_fake_runtime()
    )

    tool_message = result.update["messages"][0]
    trail = tool_message.additional_kwargs["subagent_trail"]
    assert trail == [{
        "kind": "message",
        "id": "m1",
        "content": "直接给出结论",
        "truncated": False,
        "thinking": "",
        "thinkingTruncated": False,
    }]


@pytest.mark.asyncio
async def test_task_tool_stamps_trail_on_failure(monkeypatch):
    """子图中途抛异常时返回 error ToolMessage 并盖章已收集的 trail 快照。

    真实场景：子图耗尽 recursion_limit —— 直播时内层过程已通过 SSE 转发，
    若失败走 tool_error 中间件（无 additional_kwargs），历史里 trail 会丢失。
    """

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(
                content="开始查询",
                tool_calls=[{"id": "inner-1", "name": "query_data", "args": {}}],
            )]}}
            yield "updates", {"tools": {"messages": [ToolMessage(
                content="查询成功", tool_call_id="inner-1", name="query_data",
            )]}}
            raise RuntimeError("Recursion limit of 40 reached without hitting a stop condition.")

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="分析订单", subagent_type="data-explore", runtime=_fake_runtime()
    )

    tool_message = result.update["messages"][0]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.status == "error"
    assert "子智能体执行失败" in tool_message.content
    assert "Recursion limit" in tool_message.content
    trail = tool_message.additional_kwargs["subagent_trail"]
    assert [item["kind"] for item in trail] == ["message", "tool"]
    assert trail[1]["result"] == "查询成功"


@pytest.mark.asyncio
async def test_task_tool_failure_without_inner_activity_omits_trail(monkeypatch):
    """异常发生在任何可见输出之前时不塞空快照，与成功路径口径一致。"""

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            raise RuntimeError("模型连接失败")
            yield  # pragma: no cover

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="分析", subagent_type="data-explore", runtime=_fake_runtime()
    )

    tool_message = result.update["messages"][0]
    assert tool_message.status == "error"
    assert "子智能体执行失败" in tool_message.content
    assert "subagent_trail" not in tool_message.additional_kwargs



def test_build_trail_snapshot_tolerates_malformed_inner_tool_calls():
    """LLM 畸形 tool_calls 与乱序 ToolMessage 不炸快照、不产生脏条目。"""
    messages = [
        AIMessage.model_construct(
            content="",
            tool_calls=[
                "not-a-dict",                                    # 非 dict：跳过
                {"id": "", "name": "x", "args": {}},             # 空 id：跳过
                {"id": "inner-1", "name": "file_read", "args": {}},
                {"id": "inner-1", "name": "file_read", "args": {}},  # 重复 id：跳过
            ],
        ),
        # 孤儿 ToolMessage：先于对应 tool_calls 到达（乱序输入容忍）
        ToolMessage(content="结果", tool_call_id="inner-2", name="file_read"),
        # 空 tool_call_id 的 ToolMessage：直接跳过
        ToolMessage(content="无id", tool_call_id="", name="file_read"),
    ]

    trail = subagents_module._build_trail_snapshot(messages)

    ids = [item["id"] for item in trail]
    assert ids == ["inner-1", "inner-2"]
    normal = trail[0]
    assert normal["tool_name"] == "file_read"
    assert normal["status"] == "completed"   # 无 ToolMessage 回填 → 默认完成
    assert normal["result"] == ""
    orphan = trail[1]
    assert orphan["result"] == "结果"
    assert orphan["tool_name"] == "file_read"


@pytest.mark.asyncio
async def test_compiled_subagent_mounts_tool_result_middleware(monkeypatch, tmp_path):
    """真实编译的子图必须挂 ToolResultMiddleware：内层查询工具返回的内部协议
    JSON 在出口被外置成小信封（content_in_context=false），全量数据不进子图
    上下文——与主图口径一致（MIDDLEWARE_CANONICAL_ORDER 中紧跟归一化器）。"""
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool as lc_tool

    from apps.chat_data_app.agent import tool_results
    from apps.chat_data_app.agent.workspace import runtime as workspace_runtime_module
    from apps.chat_data_app.services.agent_run import conversation_workspace

    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    paths = conversation_workspace.ensure_conversation_workspace("conv_sub", "user_sub")

    class _ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, _tools, **_kwargs):
            return self

    @lc_tool
    def semantic_query_tool(query: str) -> str:
        """查询数据（测试用：返回内部协议结果）。"""
        return tool_results.build_internal_query_result(
            {
                "status": "succeeded",
                "sql": "select 1",
                "results": [{"status": "succeeded", "data": [{"value": 1}]}],
                "total_count": 1,
            },
            query_mode="dsl2sql",
            confidence=0.9,
        )

    model = _ToolCallingFakeModel(responses=[
        AIMessage(content="", tool_calls=[{
            "id": "inner-q1", "type": "tool_call",
            "name": "semantic_query_tool", "args": {"query": "总量"},
        }]),
        AIMessage(content="查询完成，共 1 行"),
    ])

    spec = _spec(tools=[semantic_query_tool], model=model)
    tool = build_task_tool([spec])

    result = await tool.coroutine(
        description="统计总量",
        subagent_type="data-explore",
        runtime=_fake_runtime({
            "user_id": "user_sub",
            "conversation_id": "conv_sub",
            "workspace": {"workspace_root": paths["root"], "workspace_backend": "local"},
        }),
    )

    tool_message = result.update["messages"][0]
    assert tool_message.content == "查询完成，共 1 行"
    trail = tool_message.additional_kwargs["subagent_trail"]
    tool_entries = [item for item in trail if item["kind"] == "tool"]
    assert len(tool_entries) == 1

    inner_result = tool_entries[0]["result"]
    assert "content_in_context" in inner_result
    assert "result_path" in inner_result
    # 全量数据被外置，不内联进子图上下文
    assert '[{"value": 1}]' not in inner_result
    # 完整结果落到会话工作区私有目录
    saved = list((Path(paths["outputs"]) / "query_result").glob("*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text(encoding="utf-8"))["results"][0]["data"] == [{"value": 1}]


@pytest.mark.asyncio
async def test_compiled_subagent_survives_raising_tool(monkeypatch, tmp_path):
    """工具 raise 后子图不得崩溃：ToolErrorMiddleware 把异常转成 error ToolMessage，
    子智能体读到错误详情后继续产出结论（回归：工具失败显式化改造后，
    子图未挂 ToolErrorMiddleware 会让整个 task 失败）。"""
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool as lc_tool

    from apps.chat_data_app.agent.workspace import runtime as workspace_runtime_module
    from apps.chat_data_app.services.agent_run import conversation_workspace

    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    conversation_workspace.ensure_conversation_workspace("conv_raise", "user_raise")

    class _ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, _tools, **_kwargs):
            return self

    @lc_tool
    def broken_tool(query: str) -> str:
        """查询数据（测试用：直接抛异常）。"""
        raise ValueError("当前会话未绑定数据源")

    model = _ToolCallingFakeModel(responses=[
        AIMessage(content="", tool_calls=[{
            "id": "inner-e1", "type": "tool_call",
            "name": "broken_tool", "args": {"query": "总量"},
        }]),
        AIMessage(content="工具报错未绑定数据源，无法继续分析"),
    ])

    spec = _spec(tools=[broken_tool], model=model)
    tool = build_task_tool([spec])

    result = await tool.coroutine(
        description="统计总量",
        subagent_type="data-explore",
        runtime=_fake_runtime({
            "user_id": "user_raise",
            "conversation_id": "conv_raise",
            "workspace": {"workspace_root": str(tmp_path), "workspace_backend": "local"},
        }),
    )

    tool_message = result.update["messages"][0]
    # 子图正常跑完，回传的是模型最终结论而非"子智能体执行失败"
    assert tool_message.content == "工具报错未绑定数据源，无法继续分析"
    assert tool_message.status != "error"
    trail = tool_message.additional_kwargs["subagent_trail"]
    tool_entries = [item for item in trail if item["kind"] == "tool"]
    assert len(tool_entries) == 1
    assert tool_entries[0]["status"] == "error"
    assert "未绑定数据源" in tool_entries[0]["result"]
