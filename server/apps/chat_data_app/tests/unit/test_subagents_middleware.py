"""子智能体中间件测试：task 工具派发、状态透传白名单与系统提示合并。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from apps.chat_data_app.agent.middleware import subagents as subagents_module
from apps.chat_data_app.agent.middleware.subagents import (
    SUBAGENT_STATE_PASSTHROUGH_KEYS,
    SubagentMiddleware,
    SubagentSpec,
    build_subagent_invocation_config,
    build_task_tool,
    validate_subagent_specs,
)


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


def test_validate_subagent_specs_rejects_empty_and_duplicates():
    with pytest.raises(ValueError, match="至少需要定义一个子智能体"):
        validate_subagent_specs([])
    with pytest.raises(ValueError, match="名称不能为空"):
        validate_subagent_specs([_spec(name="  ")])
    with pytest.raises(ValueError, match="名称重复"):
        validate_subagent_specs([_spec(name="a"), _spec(name="a")])


def test_subagent_system_prompt_injects_skills_catalog():
    """一致继承：spec 带技能清单时，子图 prompt 追加目录段。"""
    xml = (
        "<available_skills>\n  <skill>\n    <name>data-analyzing</name>\n"
        "    <skill_file>/mnt/skills/data-analyzing/SKILL.md</skill_file>\n  </skill>\n</available_skills>"
    )
    spec = _spec(available_skills_xml=xml)
    prompt = subagents_module._subagent_system_prompt(spec)
    assert prompt.startswith("你是分析子智能体")
    assert "## 可用技能" in prompt
    assert "/mnt/skills/data-analyzing/SKILL.md" in prompt


def test_subagent_system_prompt_never_injects_datasource_section():
    """静态注入下线固化：数据上下文段（执行对象清单/方言）不再进子图 prompt。

    执行对象清单与方言改为 data_analyzing_tool 的 list_execution_targets 按需
    实时获取，子智能体与主智能体共用同一工具通道，无需提示词镜像。
    """
    xml = (
        "<available_skills>\n  <skill>\n    <name>data-analyzing</name>\n"
        "    <skill_file>/mnt/skills/data-analyzing/SKILL.md</skill_file>\n  </skill>\n</available_skills>"
    )
    spec = _spec(available_skills_xml=xml)
    prompt = subagents_module._subagent_system_prompt(spec)
    assert "## 可用技能" in prompt
    assert "## 数据上下文" not in prompt
    assert "member_datasource_ids" not in prompt


def test_subagent_system_prompt_keeps_original_when_catalog_empty():
    """未传目录或空目录块（无 <skill> 条目）时不注入，prompt 原样。"""
    spec = _spec()
    assert subagents_module._subagent_system_prompt(spec) == spec.system_prompt
    empty_catalog = _spec(available_skills_xml="<available_skills>\n</available_skills>")
    assert subagents_module._subagent_system_prompt(empty_catalog) == spec.system_prompt


def test_build_subagent_middleware_passes_skills_catalog_to_specs():
    """data_agent.build_subagent_middleware 把技能清单透传给每个子智能体 spec。"""
    from apps.chat_data_app.agent.data_agent import AgentRuntimeOptions, build_subagent_middleware

    options = AgentRuntimeOptions(
        explore_subagent_enabled=True,
        plan_subagent_enabled=True,
    )
    xml = "<available_skills>\n  <skill>\n    <name>x</name>\n  </skill>\n</available_skills>"
    mw = build_subagent_middleware(
        options,
        tools=[],
        model=object(),
        model_id="m1",
        available_skills_xml=xml,
    )
    assert mw is not None
    assert {spec.name for spec in mw.specs} == {"explore", "plan"}
    assert all(spec.available_skills_xml == xml for spec in mw.specs)
    mw_default = build_subagent_middleware(options, tools=[], model=object(), model_id="m1")
    assert all(spec.available_skills_xml == "" for spec in mw_default.specs)


def test_build_subagent_state_only_passes_whitelisted_keys():
    runtime = _fake_runtime(
        {
            "user_id": "u1",
            "workspace": {"root": "/mnt/user-data"},
            "token_usage": {"input": 1},
            "context_summary": "should-not-leak",
        }
    )

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
    assert config["configurable"] == {"chatdata_subagent": "explore"}


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
        subagent_type="explore",
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

    result = await tool.coroutine(description="分析", subagent_type="explore", runtime=_fake_runtime())

    assert "未产出有效结论" in result.update["messages"][0].content


@pytest.mark.asyncio
async def test_task_tool_forwards_inner_tool_events_to_parent_stream(monkeypatch):
    """内层工具生命周期事件经 custom 通道转发，带 parent_tool_call_id。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            message = AIMessage.model_construct(
                content="",
                tool_calls=[{"id": "inner-1", "name": "calculator_tool", "args": '{"expression": "1 + 1"}'}],
            )
            yield "updates", {"model": {"messages": [message]}}
            yield (
                "updates",
                {
                    "tools": {
                        "messages": [
                            ToolMessage(
                                content="2",
                                tool_call_id="inner-1",
                                name="calculator_tool",
                            )
                        ]
                    }
                },
            )
            yield "updates", {"model": {"messages": [AIMessage(content="1 + 1 = 2")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(description="算一下 1+1", subagent_type="explore", runtime=_fake_runtime())

    assert result.update["messages"][0].content == "1 + 1 = 2"
    # 用量事件（每个 updates 后一条）与过程事件同通道，序列断言先滤掉
    written_filtered = [
        payload for payload in written if payload["event"].get("event_type") != "subagent_context_usage"
    ]
    # 工具开始 → 工具结束 → 子智能体最终叙述
    assert [payload["event"]["event_type"] for payload in written_filtered] == [
        "tool_started",
        "tool_completed",
        "subagent_message",
    ]
    started = written_filtered[0]
    completed = written_filtered[1]
    assert written_filtered[2]["event"]["content"] == "1 + 1 = 2"
    assert started == {
        "type": "subagent_tool",
        "event": {
            "event_type": "tool_started",
            "tool_call_id": "inner-1",
            "tool_name": "calculator_tool",
            "args": {"expression": "1 + 1"},
            "parent_tool_call_id": "call_1",
        },
    }
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

    result = await tool.coroutine(description="分析", subagent_type="explore", runtime=_fake_runtime())

    assert result.update["messages"][0].content == "结论"


def test_iter_subagent_stream_events_truncates_long_results():
    long_text = "x" * (subagents_module.SUBAGENT_RESULT_PREVIEW_MAX_CHARS + 100)

    events = subagents_module._iter_subagent_stream_events(
        [
            ToolMessage(content=long_text, tool_call_id="inner-1", name="file_read"),
            ToolMessage(content="失败原因", tool_call_id="inner-2", name="data_analyzing_tool", status="error"),
        ],
        parent_tool_call_id="call_1",
    )

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
    events = subagents_module._iter_subagent_stream_events(
        [
            AIMessage(content="   "),
            AIMessage(content=""),
            AIMessage(content=long_text),
        ],
        parent_tool_call_id="call_1",
    )

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

    result = await tool.coroutine(description="分析", subagent_type="no-such-agent", runtime=_fake_runtime())

    assert "类型不存在" in result
    assert "explore" in result


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
    assert middleware.subagent_names == {"explore", "report-writer"}
    assert "explore" in middleware.tools[0].description
    assert "report-writer" in middleware.tools[0].description


def test_passthrough_keys_cover_subagent_state_schema_fields():
    """白名单必须覆盖子智能体状态 schema 的全部透传业务字段。

    不含基类框架字段；压缩三键（context_summary / context_compression /
    context_compression_events）是子图自有状态（子图压缩中间件写入，
    生命周期属于本次派发），有意不从主图透传，也不得出现在白名单里。
    """
    from apps.chat_data_app.agent.middleware.subagents import ChatDataSubAgentState

    framework_keys = {"messages", "jump_to", "structured_response"}
    subgraph_owned_keys = {"context_summary", "context_compression", "context_compression_events", "loaded_skills"}
    schema_keys = {
        key
        for key in ChatDataSubAgentState.__annotations__
        if key not in framework_keys and key not in subgraph_owned_keys
    }
    assert schema_keys and schema_keys <= set(SUBAGENT_STATE_PASSTHROUGH_KEYS)
    assert not (set(SUBAGENT_STATE_PASSTHROUGH_KEYS) & subgraph_owned_keys)


@pytest.mark.asyncio
async def test_task_tool_streams_text_and_thinking_deltas(monkeypatch):
    """正文/思考以 token 级增量经 custom 流转发；完整消息只补收敛标记不重复带内容。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            assert stream_mode == ["updates", "messages"]
            yield (
                "messages",
                (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "先查数据"}, id="m1"), {}),
            )
            yield "messages", (AIMessageChunk(content="结论是", id="m1"), {})
            yield "messages", (AIMessageChunk(content="上升", id="m1"), {})
            yield "updates", {"model": {"messages": [AIMessage(content="结论是上升", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(description="分析", subagent_type="explore", runtime=_fake_runtime())

    assert result.update["messages"][0].content == "结论是上升"
    # 用量事件（每个 updates 后一条）与过程事件同通道，序列断言先滤掉
    written = [payload for payload in written if payload["event"].get("event_type") != "subagent_context_usage"]
    assert [payload["event"]["event_type"] for payload in written] == [
        "subagent_thinking_delta",
        "subagent_message_delta",
        "subagent_message_delta",
        "subagent_message",
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

    result = await tool.coroutine(description="分析", subagent_type="explore", runtime=_fake_runtime())

    assert result.update["messages"][0].content == "x" * cap + "yyyyyyyy"
    # 用量事件（每个 updates 后一条）与过程事件同通道，先滤掉再断言增量序列
    written = [payload for payload in written if payload["event"].get("event_type") != "subagent_context_usage"]
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
    assert (
        subagents_module._iter_chunk_stream_events(ToolMessage(content="x", tool_call_id="c"), "call_1", {}, {}) == []
    )


@pytest.mark.asyncio
async def test_task_tool_keeps_text_when_chunk_carries_tool_call(monkeypatch):
    """正文与工具增量同 chunk：前端拼出的全文必须等于完整消息（丢内容回归）。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "messages", (AIMessageChunk(content="我需要先读取", id="m1"), {})
            # 文本与工具增量同时到达（OpenAI 兼容实现常见）
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="配置文件",
                        tool_call_chunks=[
                            {"name": "file_read", "args": '{"path":', "id": "c1", "index": 0, "type": "tool_call_chunk"}
                        ],
                        id="m1",
                    ),
                    {},
                ),
            )
            yield "updates", {"model": {"messages": [AIMessage(content="我需要先读取配置文件", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    await tool.coroutine(description="分析", subagent_type="explore", runtime=_fake_runtime())

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

    await tool.coroutine(description="分析", subagent_type="explore", runtime=_fake_runtime())

    finals = [payload["event"] for payload in written if payload["event"]["event_type"] == "subagent_message"]
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
            yield (
                "updates",
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="开始计算",
                                tool_calls=[
                                    {"id": "inner-1", "name": "calculator_tool", "args": {"expression": "1 + 1"}}
                                ],
                            )
                        ]
                    }
                },
            )
            yield (
                "updates",
                {
                    "tools": {
                        "messages": [
                            ToolMessage(
                                content="2",
                                tool_call_id="inner-1",
                                name="calculator_tool",
                            )
                        ]
                    }
                },
            )
            yield "updates", {"model": {"messages": [AIMessage(content="1 + 1 = 2", id="m-final")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(description="算一下 1+1", subagent_type="explore", runtime=_fake_runtime())

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

    result = await tool.coroutine(description="分析", subagent_type="explore", runtime=_fake_runtime())

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

    result = await tool.coroutine(description="分析", subagent_type="explore", runtime=_fake_runtime())

    tool_message = result.update["messages"][0]
    trail = tool_message.additional_kwargs["subagent_trail"]
    assert trail == [
        {
            "kind": "message",
            "id": "m1",
            "content": "直接给出结论",
            "truncated": False,
            "thinking": "",
            "thinkingTruncated": False,
        }
    ]


@pytest.mark.asyncio
async def test_task_tool_stamps_trail_on_failure(monkeypatch):
    """子图中途抛异常时返回 error ToolMessage 并盖章已收集的 trail 快照。

    真实场景：子图耗尽 recursion_limit —— 直播时内层过程已通过 SSE 转发，
    若失败走 tool_error 中间件（无 additional_kwargs），历史里 trail 会丢失。
    """

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield (
                "updates",
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="开始查询",
                                tool_calls=[{"id": "inner-1", "name": "query_data", "args": {}}],
                            )
                        ]
                    }
                },
            )
            yield (
                "updates",
                {
                    "tools": {
                        "messages": [
                            ToolMessage(
                                content="查询成功",
                                tool_call_id="inner-1",
                                name="query_data",
                            )
                        ]
                    }
                },
            )
            raise RuntimeError("Recursion limit of 40 reached without hitting a stop condition.")

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(description="分析订单", subagent_type="explore", runtime=_fake_runtime())

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

    result = await tool.coroutine(description="分析", subagent_type="explore", runtime=_fake_runtime())

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
                "not-a-dict",  # 非 dict：跳过
                {"id": "", "name": "x", "args": {}},  # 空 id：跳过
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
    assert normal["status"] == "completed"  # 无 ToolMessage 回填 → 默认完成
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
    def query_tool(query: str) -> str:
        """查询数据（测试用：返回内部协议结果）。"""
        return tool_results.build_internal_query_result(
            {
                "status": "succeeded",
                "sql": "select 1",
                "results": [{"status": "succeeded", "data": [{"value": 1}]}],
                "total_count": 1,
            },
            query_mode="dsl2sql",
        )

    model = _ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "inner-q1",
                        "type": "tool_call",
                        "name": "query_tool",
                        "args": {"query": "总量"},
                    }
                ],
            ),
            AIMessage(content="查询完成，共 1 行"),
        ]
    )

    spec = _spec(tools=[query_tool], model=model)
    tool = build_task_tool([spec])

    result = await tool.coroutine(
        description="统计总量",
        subagent_type="explore",
        runtime=_fake_runtime(
            {
                "user_id": "user_sub",
                "conversation_id": "conv_sub",
                "workspace": {"workspace_root": paths["root"], "workspace_backend": "local"},
            }
        ),
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

    model = _ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "inner-e1",
                        "type": "tool_call",
                        "name": "broken_tool",
                        "args": {"query": "总量"},
                    }
                ],
            ),
            AIMessage(content="工具报错未绑定数据源，无法继续分析"),
        ]
    )

    spec = _spec(tools=[broken_tool], model=model)
    tool = build_task_tool([spec])

    result = await tool.coroutine(
        description="统计总量",
        subagent_type="explore",
        runtime=_fake_runtime(
            {
                "user_id": "user_raise",
                "conversation_id": "conv_raise",
                "workspace": {"workspace_root": str(tmp_path), "workspace_backend": "local"},
            }
        ),
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


def test_budget_alert_middleware_silent_below_threshold(monkeypatch):
    """上下文未达告警阈值时原样放行请求，不追加任何提示。"""
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(compression_trigger_tokens=10**9),
    )
    middleware = subagents_module.create_subagent_budget_alert_middleware(_spec(), [])
    request = SimpleNamespace(
        system_message=SystemMessage(content="主提示词"),
        messages=[AIMessage(content="短任务结论")],
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    seen = {}

    def handler(req):
        seen["request"] = req
        return "done"

    assert middleware.wrap_model_call(request, handler) == "done"
    assert seen["request"] is request


class _FakeModelRequest:
    """保留属性的假 ModelRequest：override 合并字段，供跨轮次断言 tools/system_message。"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def override(self, **kwargs):
        merged = dict(self.__dict__)
        merged.update(kwargs)
        return _FakeModelRequest(**merged)


def _over_threshold_request():
    return _FakeModelRequest(
        system_message=SystemMessage(content="主提示词"),
        messages=[AIMessage(content="事实" * 1000)],
        tools=["file_read"],
    )


def test_budget_alert_middleware_appends_warning_over_threshold(monkeypatch):
    """上下文估算达到阈值时，本轮请求尾部追加预算收尾提示 + tool_choice="none"（双层收尾）。"""
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(compression_trigger_tokens=100),
    )
    middleware = subagents_module.create_subagent_budget_alert_middleware(_spec(), [])
    seen = {}

    def handler(req):
        seen["request"] = req
        return "done"

    request = _over_threshold_request()

    assert middleware.wrap_model_call(request, handler) == "done"
    # 注入通道=隐藏 HumanMessage 追加在消息尾部，system prompt 保持原样
    request_messages = seen["request"].messages
    assert len(request_messages) == len(request.messages) + 1
    alert_message = request_messages[-1]
    assert isinstance(alert_message, HumanMessage)
    assert alert_message.additional_kwargs.get("budget_alert") is True
    assert alert_message.additional_kwargs.get("hide_from_ui") is True
    content = alert_message.content
    assert "上下文预算提示" in content
    assert "当前已完成任务为" in content
    assert "待完成任务为" in content
    # 只汇报事实，不替主智能体决定后续安排
    assert "续接任务书为" not in content
    assert seen["request"].system_message.content == "主提示词"
    assert "建议" not in content
    # 工具 schema 保留（与已废弃的 override(tools=[]) 的本质区别：schema 消失才是
    # Qwen 裸 <tool_call> 正文退化的诱因），仅 API 参数层禁止本轮调用
    assert seen["request"].tools == ["file_read"]
    assert seen["request"].tool_choice == "none"

    # 低于阈值零注入，tool_choice 不设置（模型正常自主决策）
    fresh = _FakeModelRequest(
        system_message=SystemMessage(content="主提示词"),
        messages=[AIMessage(content="短")],
        tools=["file_read"],
    )
    assert middleware.wrap_model_call(fresh, handler) == "done"
    assert seen["request"].system_message.content == "主提示词"
    assert getattr(seen["request"], "tool_choice", None) is None


def test_budget_alert_middleware_env_override_takes_precedence(monkeypatch):
    """SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO 覆盖主图同源阈值（压测/调试用）。

    主图预算取超大值（正常情况下永不提示）；覆盖比例 0.5 × 窗口 200 = 100，
    同样体量的消息即触发提示，且提示文案里的阈值是换算后的覆盖值而非主图值；
    覆盖线被硬限 clamp（150）兜住；软约束不摘工具。
    """
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(
            compression_trigger_tokens=10**9,
            context_window=200,
            hard_input_limit_tokens=150,
        ),
    )
    monkeypatch.setattr(subagents_module, "SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO", 0.5)
    middleware = subagents_module.create_subagent_budget_alert_middleware(_spec(), [])
    seen = {}

    def handler(req):
        seen["request"] = req
        return "done"

    request = _over_threshold_request()

    assert middleware.wrap_model_call(request, handler) == "done"
    # 注入通道=隐藏 HumanMessage 追加在消息尾部（非 system prompt 追加）
    request_messages = seen["request"].messages
    assert len(request_messages) == len(request.messages) + 1
    alert_message = request_messages[-1]
    assert isinstance(alert_message, HumanMessage)
    assert alert_message.additional_kwargs.get("budget_alert") is True
    assert alert_message.additional_kwargs.get("hide_from_ui") is True
    assert "上下文预算提示" in alert_message.content
    assert "100 tokens" in alert_message.content
    assert seen["request"].system_message.content == "主提示词"
    assert seen["request"].tools == ["file_read"]


def test_budget_alert_ratio_override_clamped_by_hard_limit(monkeypatch):
    """比例换算出的告警线超过硬限时被 clamp 到硬限，不会出现告警线越过硬限。"""
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(
            compression_trigger_tokens=10**9,
            context_window=1000,
            hard_input_limit_tokens=60,
        ),
    )
    monkeypatch.setattr(subagents_module, "SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO", 0.9)
    meta = subagents_module._subagent_budget_meta(_spec(), [])
    assert meta["threshold_tokens"] == 60


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


@pytest.mark.asyncio
async def test_task_tool_streamed_deltas_exclude_markup(monkeypatch):
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
    tool = build_task_tool([_spec()])

    await tool.coroutine(description="盘点", subagent_type="explore", runtime=_fake_runtime())

    deltas = [payload["event"] for payload in written if payload["event"]["event_type"] == "subagent_message_delta"]
    joined = "".join(event["delta"] for event in deltas)
    assert "<tool_call" not in joined
    assert "file_read" not in joined
    assert joined.startswith("由于部分文件较长")


@pytest.mark.asyncio
async def test_task_tool_emits_context_usage_events_and_stamps_tool_message(monkeypatch):
    """每个节点更新后发 subagent_context_usage 事件，最终用量盖到 ToolMessage。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(compression_trigger_tokens=10_000),
    )

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="第一步结论", id="m1")]}}
            yield "updates", {"model": {"messages": [AIMessage(content="最终结论", id="m2")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(description="盘点", subagent_type="explore", runtime=_fake_runtime())

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
    assert usage_events[1]["percent"] == usage_events[1]["used_tokens"] * 100 // 10_000
    stamped = result.update["messages"][0].additional_kwargs["subagent_context_usage"]
    assert stamped == {
        "used_tokens": usage_events[1]["used_tokens"],
        "threshold_tokens": usage_events[1]["threshold_tokens"],
        "percent": usage_events[1]["percent"],
    }


@pytest.mark.asyncio
async def test_task_tool_prepends_budget_notice_when_forced_to_wrap_up(monkeypatch):
    """用量达阈值后，回传正文陈述事实（结论可能残缺），不替主智能体安排后续。"""
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
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(description="盘点", subagent_type="explore", runtime=_fake_runtime())

    message = result.update["messages"][0]
    assert message.content.startswith("【子智能体预算提示】")
    assert "提前收尾" in message.content
    assert "结论可能不完整" in message.content
    # 只陈述事实：不给主智能体安排后续动作
    assert "重新派发子任务续接" not in message.content
    assert "提前收尾的盘点结论" in message.content
    # 未达阈值的正常路径不带提示（覆盖见上一条用量事件测试）
    assert message.status != "error"


@pytest.mark.asyncio
async def test_task_tool_offloads_report_over_parent_budget(monkeypatch):
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
        return "[tool_result_offloaded]\nsource_tool=task", {"source_tool": "task"}

    monkeypatch.setattr(subagents_module, "externalize_tool_text", fake_externalize)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="结论" * 3000, id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])
    runtime = _fake_runtime(
        state={
            "token_usage": {"hard_input_limit_tokens": 1000},
            "workspace": {"workspace_root": "/tmp/ws"},
        }
    )

    result = await tool.coroutine(description="盘点", subagent_type="explore", runtime=runtime)

    message = result.update["messages"][0]
    assert message.content.startswith("[tool_result_offloaded]")
    assert message.status == "success"
    assert calls[0]["source_tool"] == "task"
    assert calls[0]["suffix"] == ".md"
    assert calls[0]["preview_chars"] == subagents_module.SUBAGENT_REPORT_PREVIEW_CHARS
    assert calls[0]["state"]["token_usage"]["hard_input_limit_tokens"] == 1000


@pytest.mark.asyncio
async def test_task_tool_keeps_report_within_parent_budget(monkeypatch):
    """报告未超父上下文预算时不外置，正文保持结论原文。"""

    async def fake_externalize(*_args, **_kwargs):
        raise AssertionError("未超预算时不应触发外置")

    monkeypatch.setattr(subagents_module, "externalize_tool_text", fake_externalize)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="简短结论", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])
    runtime = _fake_runtime(state={"token_usage": {"hard_input_limit_tokens": 1_000_000}})

    result = await tool.coroutine(description="盘点", subagent_type="explore", runtime=runtime)

    message = result.update["messages"][0]
    assert message.content == "简短结论"
    assert message.status == "success"


@pytest.mark.asyncio
async def test_task_tool_keeps_report_without_parent_usage(monkeypatch):
    """父 state 没有用量时无法按预算封顶，保持原文（不猜窗口大小）。"""

    async def fake_externalize(*_args, **_kwargs):
        raise AssertionError("缺少父用量时不应触发外置")

    monkeypatch.setattr(subagents_module, "externalize_tool_text", fake_externalize)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="结论" * 3000, id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(description="盘点", subagent_type="explore", runtime=_fake_runtime())

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


def test_budget_alert_injects_runtime_summary_below_threshold(monkeypatch):
    """子图压缩摘要（state.context_summary）无条件注入请求头部。

    摘要是给模型看的：低于告警阈值也要以隐藏 HumanMessage 前插（与主图
    ContextUsageMiddleware 同语义），但不触发收尾提示。
    """
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(compression_trigger_tokens=10**9),
    )
    middleware = subagents_module.create_subagent_budget_alert_middleware(_spec(), [])
    seen = {}

    def handler(req):
        seen["request"] = req
        return "done"

    request = _FakeModelRequest(
        system_message=SystemMessage(content="主提示词"),
        messages=[AIMessage(content="短", id="m1")],
        tools=["file_read"],
        state={"context_summary": "此前已完成 X 模型的字段盘点"},
    )

    assert middleware.wrap_model_call(request, handler) == "done"
    req = seen["request"]
    assert req is not request
    assert len(req.messages) == 2
    summary_message = req.messages[0]
    assert isinstance(summary_message, HumanMessage)
    assert summary_message.additional_kwargs.get("context_summary") is True
    assert summary_message.additional_kwargs.get("hide_from_ui") is True
    assert "此前已完成 X 模型的字段盘点" in summary_message.content
    # 无告警：system prompt 保持原样
    assert req.system_message.content == "主提示词"


def test_budget_alert_counts_summary_tokens_for_threshold(monkeypatch):
    """预算计数必须包含摘要 tokens：摘要把用量顶过阈值时同样触发收尾提示。"""
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(compression_trigger_tokens=100),
    )
    middleware = subagents_module.create_subagent_budget_alert_middleware(_spec(), [])
    seen = {}

    def handler(req):
        seen["request"] = req
        return "done"

    # 消息本身很短，摘要足够长——只算消息不会超线，算上摘要必须超线
    request = _FakeModelRequest(
        system_message=SystemMessage(content="主提示词"),
        messages=[AIMessage(content="短", id="m1")],
        tools=["file_read"],
        state={"context_summary": "历史背景" * 100},
    )

    assert middleware.wrap_model_call(request, handler) == "done"
    req = seen["request"]
    # 告警走隐藏 HumanMessage 追加在消息尾部，system prompt 保持原样
    assert req.system_message.content == "主提示词"
    assert len(req.messages) == 3  # 原始 1 条 + 头部摘要 + 尾部告警
    # 摘要消息仍在头部（提示与摘要并存，模型既知历史又能收尾）
    assert req.messages[0].additional_kwargs.get("context_summary") is True
    assert "上下文预算提示" in req.messages[-1].content
    assert req.messages[-1].additional_kwargs.get("budget_alert") is True
    assert req.tools == ["file_read"]


@pytest.mark.asyncio
async def test_task_tool_keeps_report_when_offload_fails(monkeypatch):
    """落盘失败不能丢掉子智能体结论，也不得误报已外置。"""

    async def failed_externalize(*_args, **_kwargs):
        return "[tool_result_truncated]", None

    monkeypatch.setattr(subagents_module, "externalize_tool_text", failed_externalize)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="关键结论" * 3000, id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])
    runtime = _fake_runtime(
        state={
            "token_usage": {"hard_input_limit_tokens": 1000},
            "workspace": {"workspace_root": "/tmp/ws"},
        }
    )

    result = await tool.coroutine(description="盘点", subagent_type="explore", runtime=runtime)

    message = result.update["messages"][0]
    assert message.content == "关键结论" * 3000
    assert message.status == "success"


def test_task_tool_description_is_compact_chinese():
    """task 是常驻注入工具：任务书要求保留，但描述模板不能无限膨胀。"""
    from apps.chat_data_app.agent.middleware.subagents import _TASK_TOOL_DESCRIPTION_TEMPLATE

    assert len(_TASK_TOOL_DESCRIPTION_TEMPLATE) <= 1200
    for keyword in ("任务书", "explore", "file_write", "present_artifacts"):
        assert keyword in _TASK_TOOL_DESCRIPTION_TEMPLATE


def test_sandbox_renewal_middleware_renews_before_and_after_model():
    """子图 model 调用同样续期：前/后各一次，覆盖子智能体长任务。"""
    import asyncio

    from langchain_core.messages import AIMessage

    from apps.chat_data_app.agent.middleware.sandbox_renewal import create_sandbox_renewal_middleware

    calls: list[str] = []

    async def renewal():
        calls.append("renew")

    middleware = create_sandbox_renewal_middleware(renewal)
    request = SimpleNamespace(system_message=None, messages=[], tools=[])

    async def handler(_request):
        return AIMessage(content="ok")

    asyncio.run(middleware.awrap_model_call(request, handler))
    assert len(calls) == 2
