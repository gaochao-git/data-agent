"""agent_stream 残余分支补测：消息构造、统计口径、SSE 事件与收口辅助函数。"""

import time
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from apps.chat_data_app import agent_stream
from apps.chat_data_app.agent_stream import (
    TurnResumeContext,
    TurnRuntimeContext,
    TurnUserInput,
    _admit_agent_inbox_messages,
    _AgentSseStreamAdapter,
    _build_user_message,
    _iter_breakdown_rows,
    _latest_assistant_timestamp,
    _mark_agent_waiting_for_user,
    _model_first_token_from_messages,
    _resolve_agent_turn_inputs,
    _tool_duration_in_window,
    _usage_aux_stats,
)


def _adapter(*, agent=None, **attrs):
    adapter = _AgentSseStreamAdapter(
        agent=agent or SimpleNamespace(),
        initial_state={},
        config={},
    )
    for key, value in attrs.items():
        setattr(adapter, key, value)
    return adapter


class _EmptyAgent:
    async def astream(self, _state, config=None, stream_mode=None):
        for _ in ():
            yield _


class _ExplodingAgent:
    async def astream(self, _state, config=None, stream_mode=None):
        raise RuntimeError("boom")
        yield  # pragma: no cover - 保持异步生成器形态


def test_build_user_message_keeps_non_string_content_untouched():
    message = _build_user_message(["第一段", "第二段"])

    assert message.content == ["第一段", "第二段"]
    assert "引用文件" not in str(message.content)


def test_latest_assistant_timestamp_skips_non_ai_tail_message():
    state = SimpleNamespace(
        values={
            "messages": [
                AIMessage(content="回答", additional_kwargs={"timestamp": "2026-01-01 00:00:00"}),
                HumanMessage(content="追问"),
            ]
        }
    )

    assert _latest_assistant_timestamp(state) == "2026-01-01 00:00:00"


def test_tool_duration_in_window_skips_invalid_and_out_of_window_rows():
    breakdown = [
        None,
        {"role": "tool", "started_at_ms": "bad", "ended_at_ms": 5},
        {"role": "tool", "started_at_ms": 10, "ended_at_ms": 5},
        {"role": "user", "started_at_ms": 10, "ended_at_ms": 20},
        {"role": "tool", "started_at_ms": 10, "ended_at_ms": 20},
        {"role": "tool", "started_at_ms": 100, "ended_at_ms": 200},
    ]

    assert _tool_duration_in_window(breakdown, 0, 150) == 10


def test_iter_breakdown_rows_skips_non_dict_nodes():
    rows = list(_iter_breakdown_rows([None, {"key": "a", "children": [{"key": "b"}, 3]}]))

    assert [row["key"] for row in rows] == ["a", "b"]


def test_model_first_token_rejects_missing_wall_clock():
    assert _model_first_token_from_messages([], None) is None


def test_model_first_token_skips_summary_and_finds_covering_window():
    messages = [
        AIMessage(content="摘要", additional_kwargs={"context_summary": True, "started_at_ms": 0, "ended_at_ms": 1000}),
        AIMessage(content="回答", additional_kwargs={"started_at_ms": 100, "ended_at_ms": 200}),
    ]

    assert _model_first_token_from_messages(messages, 150) == 50


def test_model_first_token_continues_when_window_does_not_cover():
    messages = [
        AIMessage(content="更早", additional_kwargs={"started_at_ms": 500, "ended_at_ms": 600}),
        AIMessage(content="命中", additional_kwargs={"started_at_ms": 100, "ended_at_ms": 200}),
    ]

    assert _model_first_token_from_messages(messages, 150) == 50


def test_usage_aux_stats_rejects_non_mapping_usage():
    assert _usage_aux_stats("not-a-dict") == {}


def test_usage_aux_stats_omits_cache_hit_rate_without_reported_rate():
    aux = _usage_aux_stats({"input_tokens": 20, "cumulative_cached_input_tokens": 10})

    assert aux["cached_input_tokens"] == 10
    assert "cache_hit_rate" not in aux


@pytest.mark.asyncio
async def test_stream_events_keeps_preconfigured_stream_started_anchor():
    adapter = _adapter(agent=_EmptyAgent(), _stream_started_wall_ms=12345)

    events = [event async for event in adapter.stream_events()]

    assert events == []
    assert adapter._stream_started_wall_ms == 12345
    assert adapter._stream_ended_wall_ms is not None


@pytest.mark.asyncio
async def test_stream_events_skips_timeout_emit_when_payload_already_seen(monkeypatch):
    emitted = []
    monkeypatch.setattr(agent_stream, "emit_llm_connection_alarm_if_needed", lambda *a, **k: emitted.append("conn"))
    monkeypatch.setattr(agent_stream, "emit_llm_first_token_timeout_alarm", lambda *a, **k: emitted.append("ttft"))
    adapter = _adapter(agent=_ExplodingAgent())
    adapter._first_visible_payload_seen = True

    with pytest.raises(RuntimeError, match="boom"):
        [event async for event in adapter.stream_events()]

    assert emitted == ["conn"]


@pytest.mark.asyncio
async def test_finalize_interrupt_durations_without_callback_is_noop():
    adapter = _adapter()
    adapter.state = SimpleNamespace(interrupts=[SimpleNamespace(value={"question": "q"})])
    adapter._stream_started_wall_ms = 1000
    adapter._stream_ended_wall_ms = 1200
    adapter._on_interrupt_finalize = None

    assert await adapter._resolve_task_durations() == {"total_duration_ms": 200}


@pytest.mark.asyncio
async def test_resolve_task_durations_skips_negative_segment():
    adapter = _adapter()
    adapter._stream_started_wall_ms = 2000
    adapter._stream_ended_wall_ms = 1000

    assert await adapter._resolve_task_durations() == {}


def test_token_usage_payload_without_usage_yields_no_event():
    adapter = _adapter()

    assert adapter._handle_token_usage_payload({}) == []


def test_context_compression_payload_requires_event_mapping():
    adapter = _adapter()

    assert adapter._handle_context_compression_payload({"event": "not-a-dict"}) == []


def test_inbox_admitted_payload_requires_message_id_list():
    adapter = _adapter()

    assert adapter._handle_inbox_admitted_payload({"message_ids": "not-a-list"}) == []


def test_subagent_delta_payload_without_delta_is_dropped():
    adapter = _adapter()

    result = adapter._handle_subagent_tool_payload(
        {"event": {"event_type": agent_stream.SSE_EVENT_SUBAGENT_MESSAGE_DELTA, "delta": ""}}
    )

    assert result == []


def test_record_first_visible_payload_falls_back_to_perf_clock(monkeypatch):
    monkeypatch.setattr(agent_stream, "emit_llm_first_token_timeout_alarm", lambda *a, **k: None)
    adapter = _adapter()
    adapter._stream_started_wall_ms = None

    adapter._record_first_visible_payload(0.0)

    assert isinstance(adapter.first_token_ms, int)
    assert adapter.first_token_ms >= 0


@pytest.mark.asyncio
async def test_message_chunk_with_empty_tool_call_chunks_still_emits_message():
    class _TruthyEmptyList(list):
        """truthy 但不含元素：覆盖 for 语句零迭代分支。"""

        def __bool__(self):
            return True

    adapter = _adapter()
    chunk = AIMessageChunk.model_construct(content="", tool_call_chunks=_TruthyEmptyList())

    events = [event async for event in adapter._handle_message_chunk(chunk, {})]

    assert len(events) == 1
    assert "message" in events[0]


@pytest.mark.asyncio
async def test_message_chunk_with_tool_call_chunk_yields_tool_events():
    adapter = _adapter()
    chunk = AIMessageChunk.model_construct(
        content="",
        tool_call_chunks=[{"id": "call-c1", "name": "calculator_tool", "args": "", "index": 0}],
    )

    events = [event async for event in adapter._handle_message_chunk(chunk, {})]

    assert any("tool_started" in event for event in events)


@pytest.mark.asyncio
async def test_message_chunk_with_block_content_skips_generation_speed_sampling():
    adapter = _adapter()
    chunk = AIMessageChunk.model_construct(content=[{"type": "text", "text": "hi"}], tool_call_chunks=[])

    events = [event async for event in adapter._handle_message_chunk(chunk, {})]

    assert len(events) == 1
    assert len(adapter._speed_samples) == 0


@pytest.mark.asyncio
async def test_admit_inbox_messages_with_empty_list_only_persists_empty(monkeypatch):
    persisted = []

    async def fake_persist(callbacks, messages):
        persisted.append(list(messages))

    monkeypatch.setattr(agent_stream, "_persist_admitted_inbox_messages", fake_persist)

    await _admit_agent_inbox_messages(SimpleNamespace(workspace_root="/tmp/ws", user_id="u1", thread_id="c1"), [])

    assert persisted == [[]]


@pytest.mark.asyncio
async def test_admit_inbox_messages_replaces_and_persists_each_message(monkeypatch):
    resolved = HumanMessage(content="解析后", id="m1")
    persisted = []

    async def fake_resolve(message, **_kwargs):
        assert message.id == "m1"
        return resolved

    async def fake_persist(callbacks, messages):
        persisted.append(list(messages))

    monkeypatch.setattr(agent_stream, "_resolve_inbox_message_references", fake_resolve)
    monkeypatch.setattr(agent_stream, "_persist_admitted_inbox_messages", fake_persist)
    messages = [HumanMessage(content="原始", id="m1")]

    await _admit_agent_inbox_messages(SimpleNamespace(workspace_root="/tmp/ws", user_id="u1", thread_id="c1"), messages)

    assert messages == [resolved]
    assert persisted == [[resolved]]


@pytest.mark.asyncio
async def test_mark_waiting_for_user_skips_without_run_id(monkeypatch):
    calls = []

    async def fake_mark(*args):
        calls.append(args)

    monkeypatch.setattr(agent_stream.agent_inbox_service, "mark_waiting_for_user", fake_mark)
    callbacks = SimpleNamespace(
        run_id="",
        effective_turn_id="turn-1",
        user_id="u1",
        username=None,
        owner_token="token",
    )
    question = {"question": "继续吗"}

    await _mark_agent_waiting_for_user(callbacks, question)

    assert calls == []
    assert question == {"question": "继续吗"}


@pytest.mark.asyncio
async def test_mark_waiting_for_user_sets_turn_and_calls_service(monkeypatch):
    calls = []

    async def fake_mark(*args):
        calls.append(args)

    monkeypatch.setattr(agent_stream.agent_inbox_service, "mark_waiting_for_user", fake_mark)
    callbacks = SimpleNamespace(
        run_id="run-1",
        effective_turn_id="turn-2",
        user_id="u1",
        username="张三",
        owner_token="token",
    )
    question = {"question": "继续吗"}

    await _mark_agent_waiting_for_user(callbacks, question)

    assert question["turn_id"] == "turn-2"
    assert calls == [("run-1", question, "u1", "张三", "token")]


@pytest.mark.asyncio
async def test_resolve_agent_turn_inputs_without_receipt_timestamp(monkeypatch):
    monkeypatch.setattr(agent_stream, "build_unified_system_prompt", lambda *a, **k: "系统提示词")
    user_input = TurnUserInput(
        user_query="销售额",
        message_content="销售额",
        file_selections=[],
        vision_attachments=[],
        skill_names=[],
        quoted_snippets=[],
    )
    resume = TurnResumeContext(resume_answer=None, turn_id="turn-1")
    runtime = TurnRuntimeContext(
        user_id="u1",
        username="张三",
        thread_id="c1",
        datasource_id=None,
        semantic_context=SimpleNamespace(available_member_datasource_ids=[], matched_member_datasource_ids=[]),
        runtime_options=SimpleNamespace(workspace_backend="local"),
        workspace_root="/tmp/ws",
        data_context={},
        available_skills_xml="",
        memory_block="",
    )

    user_message, initial_state, system_prompt, adopted = await _resolve_agent_turn_inputs(
        user_input=user_input,
        resume=resume,
        runtime=runtime,
        receipt_timestamp_ms=None,
    )

    assert user_message is not None
    assert user_message.id == "turn-1"
    assert "timestamp" in user_message.additional_kwargs
    assert initial_state["messages"][-1] is user_message
    assert system_prompt == "系统提示词"
    assert adopted is None


@pytest.mark.asyncio
async def test_resolve_agent_turn_inputs_uses_receipt_timestamp(monkeypatch):
    monkeypatch.setattr(agent_stream, "build_unified_system_prompt", lambda *a, **k: "系统提示词")
    user_input = TurnUserInput(
        user_query="销售额",
        message_content="销售额",
        file_selections=[],
        vision_attachments=[],
        skill_names=[],
        quoted_snippets=[],
    )
    resume = TurnResumeContext(resume_answer=None, turn_id="turn-2")
    runtime = TurnRuntimeContext(
        user_id="u1",
        username="张三",
        thread_id="c1",
        datasource_id=None,
        semantic_context=SimpleNamespace(available_member_datasource_ids=[], matched_member_datasource_ids=[]),
        runtime_options=SimpleNamespace(workspace_backend="local"),
        workspace_root="/tmp/ws",
        data_context={},
        available_skills_xml="",
        memory_block="",
    )

    user_message, _initial_state, _system_prompt, _adopted = await _resolve_agent_turn_inputs(
        user_input=user_input,
        resume=resume,
        runtime=runtime,
        receipt_timestamp_ms=1_700_000_000_000,
    )

    assert user_message is not None
    assert user_message.additional_kwargs["timestamp"] == time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(1_700_000_000)
    )
