"""工具参数生成过程预览（tool_args_delta）单测。

覆盖：
- 预览缓冲按 32 字符合并、8000 字符封顶/截断标记
- 主图 ``_ToolStreamState.handle_tool_call_chunks`` 发出生成中事件
- 子图 ``_SubagentStreamState.emit_messages_chunk`` 按 index 反查 id/name 并发事件
- values 快照即使参数口径未变，也补发一次 replace_args 作为「生成结束」收口信号
"""

import json

from langchain_core.messages import AIMessage, AIMessageChunk

from apps.chat_data_app import agent_stream, tool_args_preview
from apps.chat_data_app.agent.middleware.subagent_stream import _SubagentStreamState


def sse_event_name(event: str) -> str:
    return event.split("\n", 1)[0].removeprefix("event: ")


def sse_event_payload(event: str) -> dict:
    return json.loads(event.split("\ndata: ", 1)[1].rsplit("\n", 2)[0])


def test_args_preview_buffer_flushes_at_threshold():
    buffer = tool_args_preview.ToolArgsPreviewBuffer()

    assert buffer.take("call-1", "x" * 31) is None
    assert not buffer.has_preview("call-1")
    assert buffer.take("call-1", "y") == ("x" * 31 + "y", False)
    assert buffer.has_preview("call-1")


def test_args_preview_buffer_caps_and_marks_truncated(monkeypatch):
    monkeypatch.setattr(tool_args_preview, "TOOL_ARGS_PREVIEW_MAX_CHARS", 5)
    monkeypatch.setattr(tool_args_preview, "TOOL_ARGS_PREVIEW_FLUSH_CHARS", 3)
    buffer = tool_args_preview.ToolArgsPreviewBuffer()

    assert buffer.take("call-1", "ab") is None
    assert buffer.take("call-1", "cd") == ("abcd", False)
    assert buffer.take("call-1", "ef") == ("e", True)
    assert not buffer.take("call-1", "g")


def test_main_stream_emits_args_delta_with_generating_status():
    state = agent_stream._ToolStreamState()
    events = state.handle_tool_call_chunks([{"id": "call-1", "name": "file_read", "index": 0, "args": ""}])
    events.extend(state.handle_tool_call_chunks([{"id": "", "name": "", "index": 0, "args": "x" * 32}]))

    deltas = [event for event in events if sse_event_name(event) == "tool_args_delta"]
    assert len(deltas) == 1
    payload = sse_event_payload(deltas[0])
    assert payload["tool_call_id"] == "call-1"
    assert payload["tool_name"] == "file_read"
    assert payload["status"] == "generating"
    assert payload["delta"] == "x" * 32


def test_subagent_stream_emits_args_delta_with_parent_id():
    state = _SubagentStreamState("parent-1")
    emitted: list[dict] = []

    state.emit_messages_chunk(
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"id": "tool-1", "name": "file_read", "index": 0, "args": "x" * 32}],
        ),
        emitted.append,
    )

    events = [item["event"] for item in emitted if item.get("type") == "subagent_tool"]
    deltas = [event for event in events if event.get("event_type") == "tool_args_delta"]
    assert len(deltas) == 1
    assert deltas[0]["tool_call_id"] == "tool-1"
    assert deltas[0]["tool_name"] == "file_read"
    assert deltas[0]["parent_tool_call_id"] == "parent-1"
    assert deltas[0]["delta"] == "x" * 32


def test_subagent_stream_resolves_index_only_args_chunk():
    state = _SubagentStreamState("parent-1")
    emitted: list[dict] = []

    state.emit_messages_chunk(
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "tool-1", "name": "file_read", "index": 0, "args": "a" * 20},
                {"id": "", "name": "", "index": 0, "args": "b" * 20},
            ],
        ),
        emitted.append,
    )

    events = [
        item["event"]
        for item in emitted
        if item.get("type") == "subagent_tool" and item["event"].get("event_type") == "tool_args_delta"
    ]
    assert len(events) == 1
    assert events[0]["tool_call_id"] == "tool-1"
    assert events[0]["delta"] == "a" * 20 + "b" * 20


def test_reconcile_marks_args_complete_even_when_args_unchanged():
    state = agent_stream._ToolStreamState()
    args = {"file_path": "/mnt/user-data/workspace/a.md"}
    args_text = '{"file_path": "/mnt/user-data/workspace/a.md"}'
    # 有的 provider 同时给 tool_calls（完整 args）与 tool_call_chunks（增量），
    # 此时 sent_args_signature 已是完整口径，收口不应重复下发完整参数。
    state.handle_tool_calls([{"id": "call-1", "name": "file_read", "args": args}])
    state.handle_tool_call_chunks([{"id": "call-1", "name": "file_read", "index": 0, "args": args_text}])
    ai = AIMessage(
        content="",
        tool_calls=[{"id": "call-1", "name": "file_read", "args": args}],
    )

    events = state.reconcile_state_messages([ai])

    assert len(events) == 1
    payload = sse_event_payload(events[0])
    assert sse_event_name(events[0]) == "tool_args_delta"
    assert payload["done"] is True
    assert payload["delta"] == ""
    assert "args" not in payload
    assert state.reconcile_state_messages([ai]) == []
