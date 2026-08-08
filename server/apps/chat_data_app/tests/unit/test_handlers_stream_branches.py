import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from apps.chat_data_app import handlers
from apps.chat_data_app.services.semantic_conversation_context import SemanticConversationContext


@pytest.fixture(autouse=True)
def _stub_datasource_binding(monkeypatch):
    async def noop_event_write(**_kwargs):
        return None

    async def resolve_binding(_conversation_id, _user_id, requested_datasource_id):
        return requested_datasource_id

    monkeypatch.setattr(handlers.conversation, "resolve_datasource_binding", resolve_binding)
    monkeypatch.setattr(handlers.conversation_event_service, "persist_user_message", noop_event_write)
    monkeypatch.setattr(handlers.conversation_event_service, "persist_turn", noop_event_write)


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
        semantic_config_directory_info="",
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

    monkeypatch.setattr(handlers, "prepare_semantic_conversation_context", prepare_context)


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


async def collect_stream(generator):
    return [chunk async for chunk in generator]


class FakeChunk:
    def __init__(
        self,
        content="",
        *,
        msg_type="ai",
        tool_calls=None,
        tool_call_chunks=None,
        tool_call_id="",
        name="",
        additional_kwargs=None,
        response_metadata=None,
    ):
        self.content = content
        self.type = msg_type
        self.tool_calls = tool_calls or []
        self.tool_call_chunks = tool_call_chunks or []
        self.tool_call_id = tool_call_id
        self.name = name
        self.additional_kwargs = additional_kwargs or {}
        self.response_metadata = response_metadata or {}

    def model_dump(self):
        data = {
            "content": self.content,
            "type": self.type,
        }
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        if self.additional_kwargs:
            data["additional_kwargs"] = self.additional_kwargs
        if self.response_metadata:
            data["response_metadata"] = self.response_metadata
        return data


class FakeCheckpointer:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False



def test_handler_tool_state_helpers_cover_empty_and_fallback_branches():
    assert handlers._resolve_active_tool_call_id(None, "active", []) == "active"
    assert handlers._resolve_active_tool_call_id(None, None, ["older", "latest"]) == "latest"
    assert handlers._resolve_active_tool_call_id(None, None, []) is None

    running = ["a", "b"]
    assert handlers._finish_running_tool(running, "b", "b") == "a"
    assert running == ["a"]
    assert handlers._finish_running_tool(running, "a", "missing") == "a"

    assert handlers._parse_tool_args_buffer("", {"x": 1}) == {"x": 1}
    assert handlers._parse_tool_args_buffer("[bad", {"x": 1}) == {"x": 1}
    assert handlers._parse_tool_args_buffer("[1]", {"x": 1}) == {"x": 1}
    assert handlers._parse_tool_args_buffer('{"x": 2}', {"x": 1}) == {"x": 2}

    assert handlers._is_tool_result_error("") is False
    assert handlers._is_tool_result_error("[dashboard_write] bad json") is False
    assert handlers._is_tool_result_error('[dashboard_write] {"success": false}') is False
    assert handlers._is_tool_result_error("error: bad") is True
    assert handlers._is_tool_result_error("执行失败") is True

    assert handlers._visible_tool_count({}) == 0
    assert handlers._visible_tool_count({
        "hidden": "load_skill_tool",
        "sql": "semantic_query_tool",
        "dsl": "dsl2sql_tool",
    }) == 3

    reasoning_segments = []
    handlers._append_reasoning_segment(reasoning_segments, "工具前", 0)
    handlers._append_reasoning_segment(reasoning_segments, "继续", 0)
    handlers._append_reasoning_segment(reasoning_segments, "工具后", 2)
    assert reasoning_segments == [
        {"id": "reasoning-0", "content": "工具前继续", "tool_call_count": 0},
        {"id": "reasoning-1", "content": "工具后", "tool_call_count": 2},
    ]

    handlers._append_reasoning_segment(reasoning_segments, "下一轮", 0, "turn-2")
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
            yield "messages", (FakeChunk(additional_kwargs={"reasoning_content": "调用 semantic_search"}), {"langgraph_node": "model"})
            yield "messages", (FakeChunk(tool_calls=[{"id": "tc1", "name": "semantic_query_tool", "args": {"dsl": {}}}]), {"langgraph_node": "model"})
            yield "messages", (FakeChunk(content="{}", msg_type="tool", tool_call_id="tc1"), {"langgraph_node": "tools"})
            yield "messages", (FakeChunk(content="完成", msg_type="ai"), {"langgraph_node": "model"})

        async def aupdate_state(self, config, values, as_node=None):
            self.updated.append((config, values, as_node))

        async def aget_state(self, _config):
            return self.state

    agent = Agent()
    adapter = handlers._AgentSseStreamAdapter(
        agent=agent,
        initial_state={"messages": []},
        config={"thread_id": "t"},
        visible_message_filter=handlers._is_chat_data_visible_stream_message,
        reasoning_turn_id="turn-1",
    )

    chunks = await collect_stream(adapter.stream_events())
    end_payload = await adapter.finish()
    text = "".join(chunks)

    assert "event: token_usage" in text
    assert "event: context_compression" in text
    assert "语义检索" in text
    assert "event: tool" in text
    assert "semantic_query_tool" in text
    assert "event: message" in text
    assert end_payload["timestamp"] == "2026-07-15 09:00:00"
    assert end_payload["usage"]["total_tokens"] == 7
    assert agent.updated == []


def test_agent_sse_stream_adapter_first_token_timeout_alarm(monkeypatch):
    alarms = []
    monkeypatch.setattr(handlers, "LLM_FIRST_TOKEN_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(handlers, "emit_llm_first_token_timeout_alarm", lambda **kwargs: alarms.append(kwargs))

    adapter = handlers._AgentSseStreamAdapter(
        agent=SimpleNamespace(),
        initial_state={"messages": []},
        config={"configurable": {"thread_id": "conv_alarm", "checkpoint_ns": ""}},
    )

    adapter._emit_first_token_timeout_if_needed(handlers.time.perf_counter() - 61)
    adapter._emit_first_token_timeout_if_needed(handlers.time.perf_counter() - 62)

    assert len(alarms) == 1
    assert alarms[0]["threshold_seconds"] == 60
    assert alarms[0]["ext1"]["thread_id"] == "conv_alarm"


@pytest.mark.asyncio
async def test_agent_sse_stream_adapter_llm_connection_alarm(monkeypatch):
    alarms = []

    class Agent:
        async def astream(self, *_args, **_kwargs):
            raise RuntimeError("API connection error")
            yield  # pragma: no cover

    monkeypatch.setattr(handlers, "emit_llm_connection_alarm_if_needed", lambda *args, **kwargs: alarms.append((args, kwargs)) or True)

    adapter = handlers._AgentSseStreamAdapter(
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

    monkeypatch.setattr(handlers.conversation_event_service, "persist_turn", persist_turn)

    await handlers._persist_partial_agent_turn_history(
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

    monkeypatch.setattr(handlers.conversation_event_service, "persist_turn", persist_turn_error)
    await handlers._persist_partial_agent_turn_history(
        adapter,
        conversation_id="conv-partial",
        user_id="user-1",
        username="用户一",
        turn_id="turn-1",
    )


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
        }),
        archived_turn_messages=[user, first_ai, first_tool, second_ai, second_tool],
        reasoning_segments=[],
    )

    async def persist_turn(**kwargs):
        persisted.append(kwargs)

    monkeypatch.setattr(handlers.conversation_event_service, "persist_turn", persist_turn)

    await handlers._persist_agent_turn_history(
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
    payload = handlers._message_end_payload_from_state(state, raw_usage)

    assert payload["timestamp"] == "2026-06-21 07:14:44"
    assert payload["usage"]["total_tokens"] == 7
    assert payload["usage"]["breakdown"][0]["content"] == "[系统提示词已隐藏]"
    assert raw_usage["breakdown"][0]["content"] == "SECRET_SYSTEM_PROMPT"


def test_message_end_payload_ignores_empty_latest_ai_timestamp():
    state = SimpleNamespace(values={"messages": [
        AIMessage(content="上一条", id="ai_1", additional_kwargs={"timestamp": "2026-06-21 07:14:40"}),
        AIMessage(content="最新", id="ai_2", additional_kwargs={}),
    ]})

    assert handlers._latest_assistant_timestamp(state) == ""


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
    state = await handlers._persist_latest_ai_message_timestamp(agent, {"thread_id": "t"}, agent.state)

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

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(handlers._generate_chat_data_stream(
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
    assert text.rstrip().endswith("data: [DONE]")


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

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(handlers._generate_chat_data_stream(
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
    chunks = await collect_stream(handlers._generate_chat_data_stream(
        thread_id="conv",
        user_query="查销售额",
        datasource_id="ds",
        semantic_config_id="missing",
    ))
    assert "未找到可用指标集: missing" in "".join(chunks)

    class BadCheckpointer:
        async def __aenter__(self):
            raise RuntimeError("checkpoint down")

        async def __aexit__(self, *_args):
            return False

    _override_semantic_context(monkeypatch)
    monkeypatch.setattr(handlers, "get_checkpointer", lambda: BadCheckpointer())
    chunks = await collect_stream(handlers._generate_chat_data_stream("conv", "hello"))
    assert "智能问数执行失败，请稍后重试。" in "".join(chunks)
    assert "checkpoint down" not in "".join(chunks)


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

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "create_data_agent", lambda *_args, **_kwargs: graph)

    chunks = await collect_stream(handlers._generate_chat_data_stream(
        thread_id="conv",
        user_query="查订单",
        datasource_id="ds_a",
        request=DisconnectAfterFirst(),
    ))

    assert chunks == []
    assert graph.initial_state is None


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

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "create_data_agent", lambda *_args, **_kwargs: graph)

    chunks = await collect_stream(handlers._generate_chat_data_stream(
        thread_id="conv",
        user_query="查订单",
        datasource_id="ds_a",
    ))

    assert "query answer" in "".join(chunks)
    assert graph.initial_state["execution_context"]["datasource_id"] == "ds_a"
    assert "prompt_context" not in graph.initial_state
    assert "tables_ddl_info" not in graph.initial_state
    assert "loaded_table_names" not in graph.initial_state


@pytest.mark.asyncio
async def test_generate_chat_data_stream_tool_chunk_missing_id_and_bad_json(monkeypatch):
    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(tasks=[], values={})

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(tool_calls=[{"id": "tc_bad", "name": "semantic_query_tool"}]), {"langgraph_node": "model"}
            yield FakeChunk(tool_call_chunks=[{"id": None, "name": "", "args": "{bad"}]), {"langgraph_node": "model"}
            yield FakeChunk(content="tool done", msg_type="tool", tool_call_id="tc_bad", name="semantic_query_tool"), {"langgraph_node": "tools"}

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(handlers._generate_chat_data_stream(
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
    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "create_data_agent", lambda *_args, **_kwargs: graph)

    chunks = await collect_stream(handlers._generate_chat_data_stream(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
        semantic_config_id="missing",
    ))

    assert "未找到可用指标集: missing" in "".join(chunks)
    assert graph.initial_state is None


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

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(handlers._generate_chat_data_stream(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
    ))
    text = "".join(chunks)

    assert text.count('"tool_call_id": "tc_dup"') == 1
    assert "fresh done" in text
    assert "message_end" in text


@pytest.mark.asyncio
async def test_generate_chat_data_stream_forced_missing_buffer_and_empty_selection(monkeypatch):
    original_resolver = handlers._resolve_active_tool_call_id

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

    monkeypatch.setattr(handlers, "_resolve_active_tool_call_id", lambda *_args: "forced")
    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(handlers._generate_chat_data_stream(
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

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(handlers._generate_chat_data_stream(
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

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "create_data_agent", lambda *_args, **_kwargs: Graph())

    chunks = await collect_stream(handlers._generate_chat_data_stream(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
    ))
    text = "".join(chunks)

    assert '"status": "pending"' not in text
    assert "message_end" in text
    assert '"timestamp":' in text
    assert '"usage"' not in text
