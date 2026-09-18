import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, HumanMessageChunk, ToolMessage, ToolMessageChunk

from apps.chat_data_app import agent_run_support, agent_stream
from apps.chat_data_app.services.semantic.run.semantic_conversation_context import SemanticConversationContext


@pytest.fixture(autouse=True)
def _stub_datasource_binding(monkeypatch):
    async def noop_event_write(**_kwargs):
        return None

    async def resolve_binding(_conversation_id, _user_id, requested_datasource_id):
        return requested_datasource_id

    monkeypatch.setattr(agent_stream.conversation, "resolve_datasource_binding", resolve_binding)
    monkeypatch.setattr(agent_stream.conversation_event_service, "persist_user_message", noop_event_write)
    monkeypatch.setattr(agent_stream.conversation_event_service, "persist_turn", noop_event_write)

    # 预物化不是这些用例的关注点：默认桩为“本轮无模型需物化”。
    async def noop_prematerialize(**_kwargs):
        return {"total": 0, "failed_ids": []}

    monkeypatch.setattr(agent_run_support, "prematerialize_enabled_semantic_models", noop_prematerialize)


def _semantic_context_from_request(**kwargs) -> SemanticConversationContext:
    datasource_id = str(kwargs.get("datasource_id") or "").strip() or None
    semantic_config_id = str(kwargs.get("semantic_config_id") or "").strip() or None
    selected_member_ids = list(dict.fromkeys(
        str(item).strip()
        for item in kwargs.get("member_datasource_ids") or []
        if str(item).strip()
    ))
    semantic_config = (
        {"id": semantic_config_id, "name": semantic_config_id, "metrics": [], "dimensions": []}
        if semantic_config_id else None
    )
    return SemanticConversationContext(
        datasource_id=datasource_id,
        datasource_meta={"query_context": ""} if datasource_id else None,
        semantic_config_id=semantic_config_id,
        semantic_config=semantic_config,
        execution_targets=[],
        available_member_datasource_ids=selected_member_ids,
        matched_member_datasource_ids=[],
        selected_member_datasource_ids=selected_member_ids,
        datasource_group_context="",
    )


def _override_semantic_context(monkeypatch, **overrides):
    async def prepare_context(**kwargs):
        context = _semantic_context_from_request(**kwargs)
        values = {
            key: value(context, kwargs) if callable(value) else value
            for key, value in overrides.items()
        }
        return context.__class__(**{**context.__dict__, **values})

    monkeypatch.setattr(agent_run_support, "prepare_semantic_conversation_context", prepare_context)


def tool_event_contents(events: list[str]) -> list[str]:
    """解析 SSE 工具事件中的原始工具结果。"""
    contents = []
    for event in events:
        if not event.startswith("event: tool\n"):
            continue
        payload = json.loads(event.split("data: ", 1)[1].strip())
        content = str(payload.get("content") or "")
        if content:
            contents.append(content)
    return contents


def sse_event_name(event: str) -> str:
    """读取单个 SSE 块的事件名。"""
    return event.split("\n", 1)[0].removeprefix("event: ")


def test_checkpoint_resume_status_uses_pending_interrupts():
    assert agent_run_support._checkpoint_resume_status(SimpleNamespace(
        pending_writes=[("task-1", "__interrupt__", [])],
    )) is False
    assert agent_run_support._checkpoint_resume_status(SimpleNamespace(pending_writes=[])) is True
    assert agent_run_support._checkpoint_resume_status(SimpleNamespace()) is None


@pytest.mark.asyncio
async def test_generate_stream_emits_terminal_error_before_prepare_finishes(monkeypatch):
    async def fail_prepare(**_kwargs):
        raise RuntimeError("semantic context failed")

    monkeypatch.setattr(agent_run_support, "prepare_semantic_conversation_context", fail_prepare)
    events = []

    with pytest.raises(RuntimeError, match="semantic context failed"):
        async for event in agent_stream._generate_chat_data_stream(
            thread_id="conv-1",
            user_query="查询订单",
        ):
            events.append(event)

    assert [sse_event_name(event) for event in events] == ["error"]  # done 由 Runtime 统一发布


async def collect_stream(generator):
    return [chunk async for chunk in generator]


async def _collect_stream_until_failure(generator):
    """收集事件并按契约捕获 AgentRunStreamFailedError，返回 (chunks, failure)。

    failure 非 None 表示生成器把失败异常抛给了调用方（Runtime）；
    其他异常（如准备阶段的 RuntimeError）不在此捕获，继续向外抛。
    """
    chunks = []
    try:
        async for chunk in generator:
            chunks.append(chunk)
    except agent_stream.AgentRunStreamFailedError as exc:
        return chunks, exc
    return chunks, None


def test_tool_lifecycle_events_keep_legacy_tool_payload_and_map_failure():
    state = agent_stream._ToolStreamState()

    started = state.handle_tool_calls([
        {"id": "call-1", "name": "semantic_query_tool", "args": {"dsl": {}}},
    ])
    completed_payload = state.build_tool_message_payload(FakeChunk(
        content="查询完成",
        msg_type="tool",
        tool_call_id="call-1",
        name="semantic_query_tool",
    ))
    completed = state.build_completion_event(completed_payload)

    assert [sse_event_name(event) for event in started] == ["tool_started", "tool"]
    assert sse_event_name(completed) == "tool_completed"
    assert '"status": "completed"' in completed
    assert '"content": "查询完成"' in completed

    state.handle_tool_calls([{"id": "call-2", "name": "semantic_query_tool"}])
    # 成败只认显式协议位：query_result envelope 的 status=failed 判失败，裸文本不算
    failed_payload = state.build_tool_message_payload(FakeChunk(
        content='{"type": "query_result", "status": "failed", "error": "字段不存在"}',
        msg_type="tool",
        tool_call_id="call-2",
        name="semantic_query_tool",
    ))

    assert sse_event_name(state.build_completion_event(failed_payload)) == "tool_failed"

    # ToolMessage 自带 status="error"（ToolErrorMiddleware 路径）时无条件判失败
    state.handle_tool_calls([{"id": "call-3", "name": "semantic_query_tool"}])
    native_failed_payload = state.build_tool_message_payload(FakeChunk(
        content="查询完成",
        msg_type="tool",
        tool_call_id="call-3",
        name="semantic_query_tool",
        status="error",
    ))

    assert sse_event_name(state.build_completion_event(native_failed_payload)) == "tool_failed"


@pytest.mark.asyncio
async def test_agent_sse_stream_adapter_forwards_valid_tool_progress_only():
    class Agent:
        async def astream(self, *_args, **_kwargs):
            yield "custom", {
                "type": "tool_progress",
                "tool_call_id": "call-1",
                "tool_name": "officecli_run",
                "message": "正在生成预览",
                "progress": 50,
            }
            yield "custom", {"type": "tool_progress", "tool_call_id": "call-2"}

    adapter = agent_stream._AgentSseStreamAdapter(
        agent=Agent(),
        initial_state={},
        config={},
    )
    events = await collect_stream(adapter.stream_events())

    assert [sse_event_name(event) for event in events] == ["tool_progress"]
    assert '"tool_name": "officecli_run"' in events[0]
    assert '"progress": 50' in events[0]


@pytest.mark.asyncio
async def test_agent_sse_stream_adapter_emits_queued_and_custom_steer_events():
    class Agent:
        async def astream(self, *_args, **_kwargs):
            yield "custom", {"type": "inbox_admitted", "message_ids": ["message-2"]}

    adapter = agent_stream._AgentSseStreamAdapter(agent=Agent(), initial_state={}, config={})
    adapter.queue_event(agent_stream._format_sse("inbox_admitted", {"message_ids": ["message-1"]}))

    events = await collect_stream(adapter.stream_events())

    assert [sse_event_name(event) for event in events] == ["inbox_admitted", "inbox_admitted"]
    assert '"message_ids": ["message-1"]' in events[0]
    assert '"message_ids": ["message-2"]' in events[1]


@pytest.mark.asyncio
async def test_stream_agent_turn_emits_question_and_skips_message_end():
    from apps.chat_data_app.agent.run.conversation_runtime import stream_agent_turn

    question = {"question_id": "q1", "questions": [{"id": "x", "question": "请选择"}]}
    calls = []

    class InterruptAdapter:
        disconnected = False

        async def stream_events(self):
            if False:
                yield ""

        async def finish(self, **_kwargs):
            return {"usage": None}

        def pending_interrupt(self):
            return question

    async def noop():
        return None

    async def on_interrupt(payload):
        calls.append(payload)

    events = [event async for event in stream_agent_turn(
        stream_adapter=InterruptAdapter(),
        format_sse=agent_stream._format_sse,
        persist_user_message=noop,
        persist_turn_history=noop,
        persist_partial_history=noop,
        on_interrupt=on_interrupt,
    )]

    assert calls == [question]
    assert [sse_event_name(event) for event in events] == ["question_requested"]


def FakeChunk(
    content="",
    *,
    msg_type="ai",
    tool_calls=None,
    tool_call_chunks=None,
    tool_call_id="",
    name="",
    additional_kwargs=None,
    response_metadata=None,
    status=None,
):
    """测试工厂：只返回 LangChain 标准消息 Chunk。"""
    if msg_type == "human":
        return HumanMessageChunk(content=content, additional_kwargs=additional_kwargs or {})
    if msg_type == "tool":
        chunk_kwargs = {}
        if status is not None:
            chunk_kwargs["status"] = status
        return ToolMessageChunk(
            content=content,
            tool_call_id=tool_call_id,
            name=name or None,
            additional_kwargs=additional_kwargs or {},
            **chunk_kwargs,
        )
    normalized_tool_calls = [{**tool_call, "args": tool_call.get("args") or {}} for tool_call in tool_calls or []]
    return AIMessageChunk(
        content=content,
        tool_calls=normalized_tool_calls,
        tool_call_chunks=tool_call_chunks or [],
        additional_kwargs=additional_kwargs or {},
        response_metadata=response_metadata or {},
    )


class FakeCheckpointer:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def aget_tuple(self, _config):
        return None



def test_handler_tool_state_helpers_cover_empty_and_fallback_branches():
    assert agent_stream._resolve_active_tool_call_id(None, "active", []) == "active"
    assert agent_stream._resolve_active_tool_call_id(None, None, ["older", "latest"]) == "latest"
    assert agent_stream._resolve_active_tool_call_id(None, None, []) is None

    running = ["a", "b"]
    assert agent_stream._finish_running_tool(running, "b", "b") == "a"
    assert running == ["a"]
    assert agent_stream._finish_running_tool(running, "a", "missing") == "a"

    assert agent_stream._parse_tool_args_buffer("", {"x": 1}) == {"x": 1}
    assert agent_stream._parse_tool_args_buffer("[bad", {"x": 1}) == {"x": 1}
    assert agent_stream._parse_tool_args_buffer("[1]", {"x": 1}) == {"x": 1}
    assert agent_stream._parse_tool_args_buffer('{"x": 2}', {"x": 1}) == {"x": 2}

    assert agent_stream._is_tool_result_error("") is False
    assert agent_stream._is_tool_result_error("[dashboard_write] bad json") is False
    assert agent_stream._is_tool_result_error('[dashboard_write] {"success": false}') is False
    # 成败只认显式协议位（native status / query_result envelope），任何文本模式都不再判定
    assert agent_stream._is_tool_result_error("error: bad") is False
    assert agent_stream._is_tool_result_error("执行失败") is False
    assert agent_stream._is_tool_result_error("正文中间提到执行失败也不算") is False
    assert agent_stream._is_tool_result_error("bash_run 执行失败: exit 1") is False
    assert agent_stream._is_tool_result_error("file_read 工具错误: 文件不存在") is False
    assert agent_stream._is_tool_result_error('{"type":"query_result","status":"failed"}') is True
    assert agent_stream._is_tool_result_error('{"type":"query_result","status":"succeeded"}') is False

    assert agent_stream._visible_tool_count({}) == 0
    assert agent_stream._visible_tool_count({
        "hidden": "load_skill_tool",
        "sql": "data_analyzing_tool",
        "dsl": "calculator_tool",
    }) == 3

    reasoning_segments = []
    agent_stream._append_reasoning_segment(reasoning_segments, "工具前", 0)
    agent_stream._append_reasoning_segment(reasoning_segments, "继续", 0)
    agent_stream._append_reasoning_segment(reasoning_segments, "工具后", 2)
    assert reasoning_segments == [
        {"id": "reasoning-0", "content": "工具前继续", "tool_call_count": 0},
        {"id": "reasoning-1", "content": "工具后", "tool_call_count": 2},
    ]

    agent_stream._append_reasoning_segment(reasoning_segments, "下一轮", 0, "turn-2")
    assert reasoning_segments[-1] == {
        "id": "reasoning-turn-2-2",
        "content": "下一轮",
        "tool_call_count": 0,
        "reasoning_turn_id": "turn-2",
    }


@pytest.mark.asyncio
async def test_agent_sse_stream_adapter_emits_shared_events_and_message_end():
    class Agent:
        def __init__(self):
            self.updated = []
            self.state = SimpleNamespace(values={
                "messages": [AIMessage(content="最终", additional_kwargs={"timestamp": "2026-07-15 09:00:00"})],
                "token_usage": {"total_tokens": 9},
            })

        async def astream(self, *_args, **_kwargs):
            yield "custom", {"type": "token_usage", "usage": {"total_tokens": 7}}
            yield "custom", {"type": "context_compression", "event": {"id": "context-compression-1"}}
            yield "messages", (FakeChunk(additional_kwargs={"reasoning_content": "调用 data_analyzing_tool"}), {"langgraph_node": "model"})
            yield "messages", (FakeChunk(tool_calls=[{"id": "tc1", "name": "data_analyzing_tool", "args": {"dsl": {}}}]), {"langgraph_node": "model"})
            yield "messages", (FakeChunk(content="{}", msg_type="tool", tool_call_id="tc1"), {"langgraph_node": "tools"})
            yield "messages", (FakeChunk(content="完成", msg_type="ai"), {"langgraph_node": "model"})

        async def aupdate_state(self, config, values, as_node=None):
            self.updated.append((config, values, as_node))

        async def aget_state(self, _config):
            return self.state

    agent = Agent()
    adapter = agent_stream._AgentSseStreamAdapter(
        agent=agent,
        initial_state={"messages": []},
        config={"thread_id": "t"},
        visible_message_filter=agent_stream._is_chat_data_visible_stream_message,
        reasoning_turn_id="turn-1",
    )

    chunks = await collect_stream(adapter.stream_events())
    end_payload = await adapter.finish()
    text = "".join(chunks)

    assert "event: token_usage" in text
    assert "event: context_compression" in text
    assert "数据分析" in text
    assert "event: tool" in text
    assert "data_analyzing_tool" in text
    assert "event: message" in text
    assert end_payload["timestamp"] == "2026-07-15 09:00:00"
    assert end_payload["usage"]["total_tokens"] == 7
    assert agent.updated == []


@pytest.mark.asyncio
async def test_agent_sse_stream_adapter_does_not_emit_steer_human_message():
    class Agent:
        async def astream(self, *_args, **_kwargs):
            yield "messages", (FakeChunk(content="追加要求", msg_type="human"), {"langgraph_node": "model"})
            yield "messages", (FakeChunk(content="已按要求处理", msg_type="ai"), {"langgraph_node": "model"})

    adapter = agent_stream._AgentSseStreamAdapter(
        agent=Agent(),
        initial_state={"messages": []},
        config={"thread_id": "t"},
        visible_message_filter=agent_stream._is_chat_data_visible_stream_message,
    )

    text = "".join(await collect_stream(adapter.stream_events()))

    assert "追加要求" not in text
    assert "已按要求处理" in text


@pytest.mark.asyncio
async def test_agent_sse_stream_adapter_supports_langchain_chunk_message_types():
    class Agent:
        async def astream(self, *_args, **_kwargs):
            yield "messages", (
                HumanMessageChunk(content="不要输出的 steer 消息"),
                {"langgraph_node": "model"},
            )
            yield "messages", (
                AIMessageChunk(content="", additional_kwargs={"reasoning_content": "正在思考"}),
                {"langgraph_node": "model"},
            )
            yield "messages", (
                AIMessageChunk(content="千问正常回答"),
                {"langgraph_node": "model"},
            )
            yield "messages", (
                ToolMessageChunk(content="工具结果", tool_call_id="call-1", name="file_read"),
                {"langgraph_node": "tools"},
            )

    adapter = agent_stream._AgentSseStreamAdapter(
        agent=Agent(),
        initial_state={"messages": []},
        config={"thread_id": "t"},
        visible_message_filter=agent_stream._is_chat_data_visible_stream_message,
    )

    text = "".join(await collect_stream(adapter.stream_events()))

    assert "不要输出的 steer 消息" not in text
    assert "正在思考" in text
    assert "千问正常回答" in text
    assert "工具结果" in text


def test_agent_sse_stream_adapter_first_token_timeout_alarm(monkeypatch):
    alarms = []
    monkeypatch.setattr(agent_stream, "LLM_FIRST_TOKEN_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(agent_stream, "emit_llm_first_token_timeout_alarm", lambda **kwargs: alarms.append(kwargs))

    adapter = agent_stream._AgentSseStreamAdapter(
        agent=SimpleNamespace(),
        initial_state={"messages": []},
        config={"configurable": {"thread_id": "conv_alarm", "checkpoint_ns": ""}},
    )

    adapter._emit_first_token_timeout_if_needed(agent_stream.time.perf_counter() - 61)
    adapter._emit_first_token_timeout_if_needed(agent_stream.time.perf_counter() - 62)

    assert len(alarms) == 1
    assert alarms[0]["threshold_seconds"] == 60
    assert alarms[0]["ext1"]["thread_id"] == "conv_alarm"


def test_agent_sse_stream_adapter_alarm_context_carries_user_id(monkeypatch):
    """_alarm_context 需携带 user_id，使 LLM 类告警能定位到人而不止到会话。"""
    alarms = []
    monkeypatch.setattr(agent_stream, "LLM_FIRST_TOKEN_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(agent_stream, "emit_llm_first_token_timeout_alarm", lambda **kwargs: alarms.append(kwargs))

    adapter = agent_stream._AgentSseStreamAdapter(
        agent=SimpleNamespace(),
        initial_state={"messages": []},
        config={"configurable": {"thread_id": "conv_user", "checkpoint_ns": ""}},
        debug_label="AI Content from model",
        user_id="10001",
    )

    adapter._emit_first_token_timeout_if_needed(agent_stream.time.perf_counter() - 61)

    assert len(alarms) == 1
    assert alarms[0]["ext1"]["user_id"] == "10001"
    assert alarms[0]["ext1"]["thread_id"] == "conv_user"
    # 未传 user_id 的既有路径保持空串，不改变告警结构
    bare = agent_stream._AgentSseStreamAdapter(agent=SimpleNamespace(), initial_state={}, config={})
    assert bare._alarm_context()["user_id"] == ""


@pytest.mark.asyncio
async def test_agent_sse_stream_adapter_llm_connection_alarm(monkeypatch):
    alarms = []

    class Agent:
        async def astream(self, *_args, **_kwargs):
            raise RuntimeError("API connection error")
            yield  # pragma: no cover

    monkeypatch.setattr(agent_stream, "emit_llm_connection_alarm_if_needed", lambda *args, **kwargs: alarms.append((args, kwargs)) or True)

    adapter = agent_stream._AgentSseStreamAdapter(
        agent=Agent(),
        initial_state={"messages": []},
        config={"configurable": {"thread_id": "conv_conn", "checkpoint_ns": ""}},
    )

    with pytest.raises(RuntimeError, match="API connection error"):
        await collect_stream(adapter.stream_events())

    assert alarms[0][0][0].args[0] == "API connection error"
    assert alarms[0][1]["ext1"]["thread_id"] == "conv_conn"


@pytest.mark.asyncio
async def test_partial_turn_history_persists_checkpoint_state_without_masking_errors(monkeypatch):
    persisted = []

    class Agent:
        async def aget_state(self, config):
            assert config == {"configurable": {"thread_id": "conv-partial"}}
            return SimpleNamespace(values={"messages": [AIMessage(content="部分回答", id="assistant-1")]})

    adapter = SimpleNamespace(
        agent=Agent(),
        config={"configurable": {"thread_id": "conv-partial"}},
        state=None,
        reasoning_segments=[{"id": "reasoning-1", "content": "部分思考", "tool_call_count": 0}],
    )

    async def persist_turn(**kwargs):
        persisted.append(kwargs)

    monkeypatch.setattr(agent_stream.conversation_event_service, "persist_turn", persist_turn)

    await agent_run_support._persist_partial_agent_turn_history(
        adapter,
        conversation_id="conv-partial",
        user_id="user-1",
        username="用户一",
        turn_id="turn-1",
    )

    assert persisted[0]["messages"][0].content == "部分回答"
    assert persisted[0]["reasoning_segments"][0]["content"] == "部分思考"

    async def persist_turn_error(**_kwargs):
        raise RuntimeError("event store unavailable")

    monkeypatch.setattr(agent_stream.conversation_event_service, "persist_turn", persist_turn_error)
    await agent_run_support._persist_partial_agent_turn_history(
        adapter,
        conversation_id="conv-partial",
        user_id="user-1",
        username="用户一",
        turn_id="turn-1",
    )


@pytest.mark.asyncio
async def test_latest_model_snapshot_writer_coalesces_pending_snapshots():
    started = agent_stream.asyncio.Event()
    release = agent_stream.asyncio.Event()
    written_versions = []

    async def write_snapshot(state):
        written_versions.append(state["version"])
        if state["version"] == 1:
            started.set()
            await release.wait()

    writer = agent_stream._LatestModelSnapshotWriter(write_snapshot, task_name="snapshot-test")
    writer.submit({"version": 1, "messages": []})
    await started.wait()
    writer.submit({"version": 2, "messages": []})
    writer.submit({"version": 3, "messages": []})
    release.set()

    await writer.flush()

    assert written_versions == [1, 3]


@pytest.mark.asyncio
async def test_persist_turn_history_merges_pre_compression_tools_with_final_state(monkeypatch):
    persisted = []
    user = HumanMessage(content="生成报告", id="turn-archive")
    first_ai = AIMessage(
        content="",
        id="assistant-1",
        tool_calls=[{"id": "call-1", "name": "file_read", "args": {}}],
    )
    first_tool = ToolMessage(
        content="第一份完整结果",
        id="tool-1",
        name="file_read",
        tool_call_id="call-1",
    )
    second_ai = AIMessage(
        content="",
        id="assistant-2",
        tool_calls=[{"id": "call-2", "name": "calculator_tool", "args": {}}],
    )
    second_tool = ToolMessage(
        content="第二份结果",
        id="tool-2",
        name="calculator_tool",
        tool_call_id="call-2",
    )
    final_ai = AIMessage(content="报告已完成", id="assistant-final")
    adapter = SimpleNamespace(
        state=SimpleNamespace(values={
            "messages": [user, second_ai, second_tool, final_ai],
            "context_compression_events": [],
            "token_usage": {"used": 8, "total": 100},
        }),
        archived_turn_messages=[user, first_ai, first_tool, second_ai, second_tool],
        reasoning_segments=[],
        latest_token_usage={
            "used": 24,
            "total": 100,
            "breakdown": [{"key": "messages", "tokens": 24}],
        },
    )

    async def persist_turn(**kwargs):
        persisted.append(kwargs)

    monkeypatch.setattr(agent_stream.conversation_event_service, "persist_turn", persist_turn)

    await agent_run_support._persist_agent_turn_history(
        adapter,
        conversation_id="conv-archive",
        user_id="user-1",
        username="用户一",
        turn_id="turn-archive",
    )

    messages = persisted[0]["messages"]
    assert [message.id for message in messages] == [
        "turn-archive",
        "assistant-1",
        "tool-1",
        "assistant-2",
        "tool-2",
        "assistant-final",
    ]
    assert messages[2].content == "第一份完整结果"
    assert persisted[0]["token_usage"] == {
        "used": 24,
        "total": 100,
        "breakdown": [{"key": "messages", "tokens": 24}],
    }


def test_message_end_payload_prefers_persisted_ai_timestamp():
    state = SimpleNamespace(values={"messages": [
        AIMessage(content="中间回复", id="ai_1", additional_kwargs={"timestamp": "2026-06-21 07:14:40"}),
        ToolMessage(content="工具", tool_call_id="tc", id="tool_1"),
        AIMessage(content="最终回复", id="ai_2", additional_kwargs={"timestamp": "2026-06-21 07:14:44"}),
    ]})

    raw_usage = {
        "total_tokens": 7,
        "breakdown": [{"key": "system_prompt", "content": "SECRET_SYSTEM_PROMPT"}],
    }
    payload = agent_stream._message_end_payload_from_state(state, raw_usage)

    assert payload["timestamp"] == "2026-06-21 07:14:44"
    assert payload["usage"]["total_tokens"] == 7
    # 瘦身后 payload 不携带正文，system 项以 redacted 描述占位。
    assert "content" not in payload["usage"]["breakdown"][0]
    assert payload["usage"]["breakdown"][0]["description"] == "[系统提示词已隐藏]"
    assert payload["usage"]["breakdown"][0]["redacted"] is True
    assert raw_usage["breakdown"][0]["content"] == "SECRET_SYSTEM_PROMPT"


def test_message_end_payload_ignores_empty_latest_ai_timestamp():
    state = SimpleNamespace(values={"messages": [
        AIMessage(content="上一条", id="ai_1", additional_kwargs={"timestamp": "2026-06-21 07:14:40"}),
        AIMessage(content="最新", id="ai_2", additional_kwargs={}),
    ]})

    assert agent_stream._latest_assistant_timestamp(state) == ""


@pytest.mark.asyncio
async def test_persist_latest_ai_message_timestamp_uses_langchain_agent_model_node():
    class Agent:
        def __init__(self):
            self.updated = []
            self.state = SimpleNamespace(values={"messages": [
                AIMessage(content="最终回复", id="ai_1", additional_kwargs={}),
            ]})

        async def aupdate_state(self, config, values, as_node=None):
            self.updated.append((config, values, as_node))
            self.state = SimpleNamespace(values={"messages": values["messages"]})

        async def aget_state(self, _config):
            return self.state

    agent = Agent()
    state = await agent_stream._persist_latest_ai_message_timestamp(agent, {"thread_id": "t"}, agent.state)

    assert agent.updated[0][2] == "model"
    assert state.values["messages"][0].additional_kwargs["timestamp"]


@pytest.mark.asyncio
async def test_generate_chat_data_stream_tool_chunks_retrieval_completes(monkeypatch):
    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={"token_usage": {"total_tokens": 7}},
                tasks=[],
            )

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(content="skip", msg_type="ai"), {"langgraph_node": "tools"}
            yield FakeChunk(), {"langgraph_node": "model"}
            yield FakeChunk(tool_calls=[{"id": "tc1", "name": "semantic_search"}]), {"langgraph_node": "model"}
            yield FakeChunk(tool_call_chunks=[{"id": "tc1", "name": "semantic_search", "args": '{"query":'}]), {"langgraph_node": "model"}
            yield FakeChunk(tool_call_chunks=[{"id": None, "args": '"查销售额"}'}]), {"langgraph_node": "model"}
            yield FakeChunk(
                content='[semantic_search] {"success": true, "semantic_config_ids": ["cfg"], "semantic_configs": {"cfg": {"id": "cfg", "name": "指标集", "metrics": [{"name": "销售额"}]}}}',
                msg_type="tool",
                tool_call_id="tc1",
            ), {"langgraph_node": "tools"}
            yield FakeChunk(content="answer", msg_type="ai"), {"langgraph_node": "model"}

    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="查销售额",
        datasource_id="ds",
        user_id="u1",
    ))
    text = "".join(chunks)
    assert '"semantic_config_ids": ["cfg"]' in tool_event_contents(chunks)[-1]
    assert "accumulated_args" in text
    assert "event: message" in text
    assert "event: tool" in text
    assert "semantic_config_selected" not in text
    assert "semantic_config_select_required" not in text
    assert '"status": "pending"' not in text
    assert "message_end" in text
    assert "event: done" not in text  # done 由 Runtime 统一发布，生成器不再输出


@pytest.mark.asyncio
async def test_generate_chat_data_stream_emits_thinking_event(monkeypatch):
    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(
                msg_type="ai",
                additional_kwargs={"reasoning_content": "正在判断问题类型"},
            ), {"langgraph_node": "model"}
            yield FakeChunk(content="你好", msg_type="ai"), {"langgraph_node": "model"}

    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="你好",
        datasource_id="ds",
        user_id="u1",
    ))

    text = "".join(chunks)
    assert "event: thinking" in text
    assert "正在判断问题类型" in text
    assert "event: message" in text


@pytest.mark.asyncio
async def test_generate_chat_data_stream_missing_semantic_config_and_graph_error(monkeypatch):
    _override_semantic_context(
        monkeypatch,
        semantic_config=None,
        semantic_config_error="未找到可用指标集: missing",
    )
    chunks, failure = await _collect_stream_until_failure(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="查销售额",
        datasource_id="ds",
        semantic_config_id="missing",
    ))
    assert "未找到可用指标集: missing" in "".join(chunks)
    assert failure is not None  # 失败异常抛给 Runtime 收口，不被 Handler 吞掉

    class BadCheckpointer:
        async def __aenter__(self):
            raise RuntimeError("checkpoint down")

        async def __aexit__(self, *_args):
            return False

    _override_semantic_context(monkeypatch)
    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: BadCheckpointer())
    # checkpointer 失败发生在图执行守卫段内：补发 error 后按流失败契约抛
    # AgentRunStreamFailedError，由 Runtime 收口为 failed。
    chunks, failure = await _collect_stream_until_failure(agent_stream._generate_chat_data_stream("conv", "hello"))
    assert "智能问数执行失败，请稍后重试。" in "".join(chunks)
    assert "checkpoint down" not in "".join(chunks)
    assert failure is not None


@pytest.mark.asyncio
async def test_generate_chat_data_stream_disconnect_uses_clean_query_state(monkeypatch):
    class Graph:
        def __init__(self):
            self.initial_state = None

        async def aget_state(self, _config):
            return SimpleNamespace(values={"token_usage": {"total_tokens": 5}}, tasks=[])

        async def astream(self, initial_state, *_args, **_kwargs):
            self.initial_state = initial_state
            yield FakeChunk(content="answer", msg_type="ai"), {"langgraph_node": "model"}

    class DisconnectAfterFirst:
        async def is_disconnected(self):
            return True

    graph = Graph()

    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: graph)

    chunks = await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="查订单",
        datasource_id="ds_a",
        request=DisconnectAfterFirst(),
    ))

    assert chunks == []
    assert graph.initial_state is None


@pytest.mark.asyncio
async def test_resume_disconnect_before_graph_restores_pending_question(monkeypatch):
    calls = []

    class Graph:
        initial_state = None

        async def astream(self, initial_state, *_args, **_kwargs):
            self.initial_state = initial_state
            if False:
                yield None

    class DisconnectedRequest:
        async def is_disconnected(self):
            return True

    async def restore(run_id, user_id, operator_name, _owner_token):
        calls.append((run_id, user_id, operator_name))

    graph = Graph()
    _override_semantic_context(monkeypatch)
    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: graph)
    monkeypatch.setattr(agent_stream.agent_inbox_service, "restore_question_answer", restore)

    chunks = await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="查订单",
        user_id="u1",
        username="用户一",
        request=DisconnectedRequest(),
        run_id="run-1",
        resume_answer={"answers": []},
        resume_turn_id="turn-1",
    ))

    assert chunks == []
    assert graph.initial_state is None
    assert calls == [("run-1", "u1", "用户一")]


@pytest.mark.asyncio
async def test_heartbeat_cancels_owner_when_run_is_stopping(monkeypatch):
    class OwnerTask:
        cancelled = False

        def done(self):
            return False

        def cancel(self, *_args):
            self.cancelled = True

    async def no_wait(_seconds):
        return None

    async def heartbeat(_run_id, _owner_token=None):
        return "cancelling"

    owner = OwnerTask()
    monkeypatch.setattr(agent_stream.asyncio, "sleep", no_wait)
    monkeypatch.setattr(agent_stream.agent_inbox_service, "heartbeat_run", heartbeat)
    monkeypatch.setattr(agent_stream.agent_run_event_service, "renew_owner", lambda *_args: None)

    await agent_stream._heartbeat_agent_run("run-1", owner)

    assert owner.cancelled is True


@pytest.mark.asyncio
async def test_heartbeat_does_not_cancel_owner_after_normal_inactive_transition(monkeypatch):
    class OwnerTask:
        cancelled = False

        def done(self):
            return False

        def cancel(self, *_args):
            self.cancelled = True

    async def no_wait(_seconds):
        return None

    async def heartbeat(_run_id, _owner_token=None):
        return "inactive"

    owner = OwnerTask()
    monkeypatch.setattr(agent_stream.asyncio, "sleep", no_wait)
    monkeypatch.setattr(agent_stream.agent_inbox_service, "heartbeat_run", heartbeat)
    monkeypatch.setattr(agent_stream.agent_run_event_service, "renew_owner", lambda *_args: None)

    await agent_stream._heartbeat_agent_run("run-1", owner)

    assert owner.cancelled is False


@pytest.mark.asyncio
async def test_heartbeat_renews_mysql_and_redis_owner(monkeypatch):
    calls = []

    class OwnerTask:
        cancelled = False

        def done(self):
            return False

        def cancel(self, *_args):
            self.cancelled = True

    async def no_wait(_seconds):
        return None

    async def heartbeat(run_id, owner_token=None):
        calls.append(("mysql", run_id, owner_token))
        return "active" if len(calls) == 1 else "inactive"

    async def renew(run_id, owner_token):
        calls.append(("redis", run_id, owner_token))

    owner = OwnerTask()
    monkeypatch.setattr(agent_stream.asyncio, "sleep", no_wait)
    monkeypatch.setattr(agent_stream.agent_inbox_service, "heartbeat_run", heartbeat)
    monkeypatch.setattr(agent_stream.agent_run_event_service, "renew_owner", renew)

    await agent_stream._heartbeat_agent_run("run-1", owner, "2:owner")

    assert calls[:2] == [
        ("mysql", "run-1", "2:owner"),
        ("redis", "run-1", "2:owner"),
    ]
    assert owner.cancelled is False


@pytest.mark.asyncio
async def test_generate_chat_data_stream_initial_state_uses_context_groups(monkeypatch):
    class Graph:
        def __init__(self):
            self.initial_state = None

        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

        async def astream(self, initial_state, *_args, **_kwargs):
            self.initial_state = initial_state
            yield FakeChunk(content="query answer", msg_type="ai"), {"langgraph_node": "model"}

    graph = Graph()

    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: graph)

    chunks = await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="查订单",
        datasource_id="ds_a",
    ))

    assert "query answer" in "".join(chunks)
    assert [message.content for message in graph.initial_state["messages"]] == ["查订单"]
    assert graph.initial_state["execution_context"]["datasource_id"] == "ds_a"
    assert "prompt_context" not in graph.initial_state
    assert "tables_ddl_info" not in graph.initial_state
    assert "loaded_table_names" not in graph.initial_state


@pytest.mark.asyncio
async def test_generate_chat_data_stream_injects_adopted_queue_messages(monkeypatch):
    """队列执行/续跑路径（query 为空）：模型输入只含接管的队列消息。

    停止后发消息语义收口后，生产链路不会再出现"遗留队列 + 非空新输入"
    同一次模型调用的组合：输入框发送走 preserve（不接管），单条执行/
    continuation 走空 query + adopted。本用例钉住后者。
    """
    class Graph:
        def __init__(self):
            self.initial_state = None

        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

        async def astream(self, initial_state, *_args, **_kwargs):
            self.initial_state = initial_state
            yield FakeChunk(content="继续回答", msg_type="ai"), {"langgraph_node": "model"}

    graph = Graph()
    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: graph)

    adopted = [HumanMessage(content="遗留排队消息", id="adopted-1")]
    chunks = await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="",
        datasource_id="ds_a",
        adopted_messages=adopted,
    ))

    text = "".join(chunks)
    assert "inbox_admitted" in text
    assert "adopted-1" in text
    state_messages = graph.initial_state["messages"]
    assert [message.content for message in state_messages] == ["遗留排队消息"]
    assert str(state_messages[0].id) == "adopted-1"


@pytest.mark.asyncio
async def test_recovery_resumes_only_when_checkpoint_contains_current_turn(monkeypatch):
    class Checkpointer(FakeCheckpointer):
        async def aget_tuple(self, _config):
            return SimpleNamespace(checkpoint={
                "channel_values": {"messages": [HumanMessage(content="当前问题", id="turn-1")]},
            })

    class Graph:
        initial_state = "unset"

        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": [HumanMessage(content="当前问题", id="turn-1")]}, tasks=[])

        async def astream(self, initial_state, *_args, **_kwargs):
            self.initial_state = initial_state
            yield FakeChunk(content="恢复回答", msg_type="ai"), {"langgraph_node": "model"}

    reconciled = []

    async def reconcile(run_id, message_ids, owner_token):
        reconciled.append((run_id, message_ids, owner_token))

    async def complete(*_args, **_kwargs):
        return None

    graph = Graph()
    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: Checkpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: graph)
    monkeypatch.setattr(agent_stream.agent_inbox_service, "reconcile_recovered_messages", reconcile)
    monkeypatch.setattr(agent_stream.agent_inbox_service, "complete_run", complete)

    chunks = await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="当前问题",
        datasource_id="ds_a",
        user_id="u1",
        run_id="run-1",
        owner_token="2:owner",
        turn_id="turn-1",
        recovering=True,
    ))

    assert graph.initial_state is None
    assert reconciled == [("run-1", {"turn-1"}, "2:owner")]
    assert "run_restarted" in "".join(chunks)


@pytest.mark.asyncio
async def test_recovery_rebuilds_input_when_checkpoint_is_from_previous_turn(monkeypatch):
    class Checkpointer(FakeCheckpointer):
        async def aget_tuple(self, _config):
            return SimpleNamespace(checkpoint={
                "channel_values": {"messages": [HumanMessage(content="旧问题", id="old-turn")]},
            })

    class Graph:
        initial_state = None

        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

        async def astream(self, initial_state, *_args, **_kwargs):
            self.initial_state = initial_state
            yield FakeChunk(content="新回答", msg_type="ai"), {"langgraph_node": "model"}

    graph = Graph()

    async def complete(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: Checkpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: graph)
    monkeypatch.setattr(agent_stream.agent_inbox_service, "complete_run", complete)

    await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="新问题",
        datasource_id="ds_a",
        user_id="u1",
        run_id="run-1",
        owner_token="2:owner",
        turn_id="turn-1",
        recovering=True,
    ))

    assert graph.initial_state is not None
    assert [str(message.id) for message in graph.initial_state["messages"]] == ["turn-1"]


@pytest.mark.asyncio
async def test_generate_chat_data_stream_tool_chunk_missing_id_and_bad_json(monkeypatch):
    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(tasks=[], values={})

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(tool_calls=[{"id": "tc_bad", "name": "semantic_query_tool"}]), {"langgraph_node": "model"}
            yield FakeChunk(tool_call_chunks=[{"id": None, "name": "", "args": "{bad"}]), {"langgraph_node": "model"}
            yield FakeChunk(content="tool done", msg_type="tool", tool_call_id="tc_bad", name="semantic_query_tool"), {"langgraph_node": "tools"}

    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
    ))
    text = "".join(chunks)
    assert "semantic_query_tool" in text
    assert "tool done" in text
    assert '"accumulated_args": {}' in text


@pytest.mark.asyncio
async def test_generate_chat_data_stream_non_query_missing_semantic_config_and_no_selected_tables(monkeypatch):
    class Graph:
        def __init__(self):
            self.initial_state = None

        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

        async def astream(self, initial_state, *_args, **_kwargs):
            self.initial_state = initial_state
            yield FakeChunk(content="fallback answer", msg_type="ai"), {"langgraph_node": "model"}

    graph = Graph()

    _override_semantic_context(
        monkeypatch,
        semantic_config=None,
        semantic_config_error="未找到可用指标集: missing",
    )
    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: graph)

    chunks, failure = await _collect_stream_until_failure(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
        semantic_config_id="missing",
    ))

    assert "未找到可用指标集: missing" in "".join(chunks)
    assert graph.initial_state is None
    assert failure is not None  # 失败异常抛给 Runtime 收口，不被 Handler 吞掉


@pytest.mark.asyncio
async def test_generate_chat_data_stream_tool_call_duplicate_missing_name_and_empty_buffer(monkeypatch):
    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(tasks=[], values={"token_usage": {"total_tokens": 4}})

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(tool_calls=[
                {"id": "", "name": "skip_missing_id"},
                {"id": "tc_dup", "name": "semantic_query_tool"},
            ]), {"langgraph_node": "model"}
            yield FakeChunk(tool_calls=[{"id": "tc_dup", "name": "semantic_query_tool"}]), {"langgraph_node": "model"}
            yield FakeChunk(tool_call_chunks=[{"id": "tc_fresh", "name": "semantic_query_tool", "args": ""}]), {"langgraph_node": "model"}
            yield FakeChunk(content="fresh done", msg_type="tool", tool_call_id="tc_fresh", name="semantic_query_tool"), {"langgraph_node": "tools"}

    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
    ))
    text = "".join(chunks)

    duplicate_blocks = [
        block for block in text.split("\n\n")
        if '"tool_call_id": "tc_dup"' in block
    ]
    assert [sse_event_name(block) for block in duplicate_blocks] == ["tool_started", "tool"]
    assert "fresh done" in text
    assert "message_end" in text


@pytest.mark.asyncio
async def test_generate_chat_data_stream_forced_missing_buffer_and_empty_selection(monkeypatch):
    original_resolver = agent_stream._resolve_active_tool_call_id

    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(tasks=[], values={})

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(tool_call_chunks=[{"id": None, "args": '{"forced":true}'}]), {"langgraph_node": "model"}
            yield FakeChunk(content="forced done", msg_type="tool", tool_call_id="forced"), {"langgraph_node": "tools"}
            yield FakeChunk(tool_calls=[{"id": "tc_select", "name": "semantic_search"}]), {"langgraph_node": "model"}
            yield FakeChunk(
                content='[semantic_search] {"success": true, "semantic_config_ids": [], "semantic_configs": {}}',
                msg_type="tool",
                tool_call_id="tc_select",
            ), {"langgraph_node": "tools"}

    monkeypatch.setattr(agent_stream, "_resolve_active_tool_call_id", lambda *_args: "forced")
    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
    ))
    text = "".join(chunks)

    assert '"accumulated_args": {"forced": true}' in text
    assert "semantic_config_selected" not in text
    assert "semantic_config_select_required" not in text
    assert original_resolver(None, None, []) is None


@pytest.mark.asyncio
async def test_generate_chat_data_stream_thinking_with_content_and_untracked_tool(monkeypatch):
    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(
                content="带思考的回答",
                msg_type="ai",
                additional_kwargs={"reasoning_content": "先想一下"},
            ), {"langgraph_node": "model"}
            yield FakeChunk(content="untracked tool", msg_type="tool", tool_call_id="missing"), {"langgraph_node": "tools"}
            yield FakeChunk(tool_calls=[{"id": "tc_select_bad", "name": "semantic_search"}]), {"langgraph_node": "model"}
            yield FakeChunk(
                content="[semantic_search] not-json",
                msg_type="tool",
                tool_call_id="tc_select_bad",
            ), {"langgraph_node": "tools"}

    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
    ))
    text = "".join(chunks)

    assert "event: thinking" in text
    assert "带思考的回答" in text
    assert "untracked tool" in text
    assert "semantic_config_selected" not in text
    assert "semantic_config_select_required" not in text


@pytest.mark.asyncio
async def test_generate_chat_data_stream_message_end_without_token_usage(monkeypatch):
    class Graph:
        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(tool_calls=[{"id": "tc_pending", "name": "semantic_query_tool"}]), {"langgraph_node": "model"}

        async def aget_state(self, _config):
            return SimpleNamespace(
                values={},
                tasks=[],
            )

    monkeypatch.setattr(agent_stream, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(agent_stream, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(agent_stream._generate_chat_data_stream(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
    ))
    text = "".join(chunks)

    assert '"status": "pending"' not in text
    assert "message_end" in text
    assert '"timestamp":' in text
    assert '"usage"' not in text





@pytest.mark.asyncio
async def test_lease_lost_cancellation_publishes_nothing(monkeypatch):
    """租约丢失的取消不发布终态事件——流已归新 Worker 所有。"""
    import asyncio

    async def fake_inner(**_kwargs):
        raise asyncio.CancelledError("agent_run_lease_lost")
        yield 'event: message\ndata: {}\n\n'  # pragma: no cover -- 使其成为异步生成器

    monkeypatch.setattr(agent_stream, "_run_chat_data_agent_stream", fake_inner)

    events = []
    with pytest.raises(asyncio.CancelledError):
        async for event in agent_stream._generate_chat_data_stream(thread_id="conv", user_query="查"):
            events.append(event)

    assert events == []
