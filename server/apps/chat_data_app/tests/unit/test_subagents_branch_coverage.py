"""subagents 中间件流式/轨迹辅助函数的分支覆盖补测。"""
import os
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    ToolMessage,
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from apps.chat_data_app.agent.middleware.subagents import (
    _StreamCapState,
    _build_trail_snapshot,
    _chunk_reasoning_text,
    _chunk_text_delta,
    _collect_subagent_tool_results,
    _extract_final_report,
    _iter_chunk_stream_events,
    _iter_subagent_stream_events,
    _plain_text,
    _run_subagent_with_events,
)


def test_extract_final_report_skips_non_ai_and_empty_text():
    """非 AIMessage 跳过（133->134），空文本回循环（136->132）。"""
    messages = [
        HumanMessage("问题"),
        AIMessage(""),           # 空文本：回循环
        AIMessage("中间结论"),
        "not-a-message",
    ]
    assert _extract_final_report(messages) == "中间结论"
    assert _extract_final_report([]) == ""


def test_plain_text_joins_multimodal_list():
    assert _plain_text(["a", 1, "b"]) == "a\n1\nb"
    assert _plain_text("plain") == "plain"


def test_chunk_text_delta_ignores_structured_blocks():
    """非 str/list 内容返回空（202->210）；list 里 str 块与 text 块拼接（205->206）。"""
    assert _chunk_text_delta(123) == ""
    blocks = ["a", {"type": "text", "text": "b"}, {"type": "tool_call", "args": {}}]
    assert _chunk_text_delta(blocks) == "ab"


def test_chunk_reasoning_text_skips_non_dict_attrs():
    """additional_kwargs 非 dict 时跳过并继续尝试 response_metadata（222->223）。"""
    chunk = SimpleNamespace(additional_kwargs="bad", response_metadata={"reasoning": "思考中"})
    assert _chunk_reasoning_text(chunk) == "思考中"


def test_iter_chunk_stream_events_capped_thinking_emits_nothing():
    """思考增量已被封顶时 allowed 为 None，不产出事件（299->307）。"""
    caps = {"m1": _StreamCapState()}
    caps["m1"].capped = True
    chunk = AIMessageChunk(content="", id="m1", additional_kwargs={"reasoning_content": "abc"})
    assert _iter_chunk_stream_events(chunk, None, {}, caps) == []


def test_iter_subagent_stream_events_skips_unknown_message_types():
    """HumanMessage 等未知消息类型回循环（362->350），tool_call 非 dict 跳过（403->404）。"""
    message = AIMessage.model_construct(
        content="分析",
        id="m1",
        tool_calls=["bad-call", {"id": "t1", "name": "sql_query", "args": {"sql": "SELECT 1"}}],
    )
    events = _iter_subagent_stream_events([HumanMessage("q"), message, 42], None)
    assert events[0]["event_type"] == "subagent_message"
    tool_events = [event for event in events if event["event_type"] == "tool_started"]
    # 非 dict 的 tool_call 被跳过，只有合法的那个产出事件
    assert [event["tool_call_id"] for event in tool_events] == ["t1"]


def test_collect_update_messages_skips_invalid_shapes():
    """非 dict 节点（470->471）、reducer dict（473->475）、dict 消息（477->478）都被跳过。"""
    state = _StreamStateStub()
    update = {
        "node_str": "not-a-dict",
        "node_reducer": {"messages": {"REMOVE_ALL_MESSAGES": True}},
        "node_ok": {"messages": [{"inline": "dict"}, AIMessage(content="hi", id="m1")]},
    }
    collected = state.collect_update_messages(update)
    assert collected == [update["node_ok"]["messages"][1]]


class _StreamStateStub:
    """用真实 _SubagentStreamState 的公开方法跑 collect_update_messages。"""

    def __init__(self):
        from apps.chat_data_app.agent.middleware.subagents import _SubagentStreamState

        self._state = _SubagentStreamState(None)

    def __getattr__(self, name):
        return getattr(self._state, name)


class _FakeRunnable:
    """按脚本回放 astream chunk，覆盖 _run_subagent_with_events 的形态守卫。"""

    def __init__(self, chunks):
        self._chunks = chunks

    async def astream(self, state, config, stream_mode=None):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_run_subagent_with_events_guards_all_chunk_shapes():
    """collected=None（513->515）、非二元组（516->517）、messages 非二元组 payload
    （520->522）、updates 非法形态（523->524）都不炸且被跳过。"""
    good_chunk = AIMessageChunk(content="hi", id="m1")
    chunks = [
        None,                                   # 516->517
        "garbage",                              # 516->517
        ("messages", "not-a-tuple"),            # 520->522
        ("messages", (good_chunk, {"meta": 1})),  # 正常增量
        ("updates", "not-a-dict"),              # 523->524
        ("other-mode", {"node": {}}),           # 523->524
        ("updates", {"node": {"messages": [AIMessage(content="done", id="m2")]}}),
    ]
    collected = await _run_subagent_with_events(_FakeRunnable(chunks), {}, {}, None)
    assert [getattr(m, "id", None) for m in collected] == ["m2"]


def test_build_trail_snapshot_skips_unknown_message_types():
    """HumanMessage 等类型不产出 trail 条目（646->643），孤儿工具容忍。"""
    messages = [
        HumanMessage("问题"),
        AIMessage(content="分析", id="m1", tool_calls=[{"id": "t1", "name": "sql_query", "args": {}}]),
        ToolMessage(content="结果", tool_call_id="t1", name="sql_query", status="success"),
        42,
        ToolMessage(content="孤儿", tool_call_id="t-orphan", name="other", status="success"),
    ]
    trail = _build_trail_snapshot(messages)
    kinds = [entry["kind"] for entry in trail]
    assert kinds.count("message") == 1
    assert kinds.count("tool") == 2  # t1 由 AI 消息回填 + 孤儿 ToolMessage
    assert _collect_subagent_tool_results(messages)
