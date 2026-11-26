"""工具参数生成过程预览（tool_args_delta）单测。

覆盖：
- 预览缓冲首片立即发、16ms 窗口内合并、8000 字符封顶/截断标记
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


def test_args_preview_buffer_coalesces_within_16ms_window():
    now = 0.0
    buffer = tool_args_preview.ToolArgsPreviewBuffer(clock=lambda: now)

    assert buffer.take("call-1", "x") == ("x", False)
    assert buffer.has_preview("call-1")
    now = 0.005
    assert buffer.take("call-1", "y") is None
    now = 0.010
    assert buffer.take("call-1", "z") is None
    now = 0.017
    assert buffer.take("call-1", "w") == ("yzw", False)


def test_args_preview_buffer_does_not_delay_slow_stream():
    now = 0.0
    buffer = tool_args_preview.ToolArgsPreviewBuffer(clock=lambda: now)

    assert buffer.take("call-1", "x") == ("x", False)
    now = 0.030
    assert buffer.take("call-1", "y") == ("y", False)


def test_args_preview_buffer_caps_and_marks_truncated(monkeypatch):
    monkeypatch.setattr(tool_args_preview, "TOOL_ARGS_PREVIEW_MAX_CHARS", 5)
    monkeypatch.setattr(tool_args_preview, "TOOL_ARGS_PREVIEW_WINDOW_MS", 0)
    buffer = tool_args_preview.ToolArgsPreviewBuffer()

    assert buffer.take("call-1", "ab") == ("ab", False)
    assert buffer.take("call-1", "cd") == ("cd", False)
    assert buffer.take("call-1", "ef") == ("e", True)
    assert not buffer.take("call-1", "g")


def test_args_preview_buffer_exact_cap_is_not_reported_as_truncated(monkeypatch):
    """刚好填满上限时没有丢字符，不能提前显示「预览已截断」。"""
    monkeypatch.setattr(tool_args_preview, "TOOL_ARGS_PREVIEW_MAX_CHARS", 5)
    monkeypatch.setattr(tool_args_preview, "TOOL_ARGS_PREVIEW_WINDOW_MS", 0)
    buffer = tool_args_preview.ToolArgsPreviewBuffer()

    assert buffer.take("call-1", "ab") == ("ab", False)
    assert buffer.take("call-1", "cd") == ("cd", False)
    assert buffer.take("call-1", "e") == ("e", False)
    assert buffer.flush("call-1") is None

    # 下一片确认还有内容被丢弃时，才补发一次截断信号；空增量刷新前端截断标记。
    assert buffer.take("call-1", "f") == ("", True)
    assert buffer.take("call-1", "g") is None


def test_args_preview_buffer_flushes_pending_exact_cap_with_truncation(monkeypatch):
    """窗口内 pending 刚好填满时先补发尾片，再标记截断，避免最后一段丢失。"""
    now = 0.0
    monkeypatch.setattr(tool_args_preview, "TOOL_ARGS_PREVIEW_MAX_CHARS", 5)
    monkeypatch.setattr(tool_args_preview, "TOOL_ARGS_PREVIEW_WINDOW_MS", 16)
    buffer = tool_args_preview.ToolArgsPreviewBuffer(clock=lambda: now)

    assert buffer.take("call-1", "ab") == ("ab", False)
    now = 0.005
    assert buffer.take("call-1", "cde") is None
    now = 0.006
    assert buffer.take("call-1", "f") == ("cde", True)


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


def test_main_stream_flushes_pending_tail_before_authoritative_args():
    """收口时先补发 16ms 窗口内未发出的尾片，避免最后一段参数丢失。"""
    state = agent_stream._ToolStreamState()
    state.handle_tool_call_chunks([{"id": "call-1", "name": "file_read", "index": 0, "args": '{"a":'}])
    # 同一时间窗内的后续分片挂起，等待窗口到期或收口 flush。
    state.handle_tool_call_chunks([{"id": "", "name": "", "index": 0, "args": "1}"}])
    ai = AIMessage(content="", tool_calls=[{"id": "call-1", "name": "file_read", "args": {"a": 1}}])

    events = state.reconcile_state_messages([ai])

    deltas = [event for event in events if sse_event_name(event) == "tool_args_delta"]
    assert deltas
    tail = sse_event_payload(deltas[0])
    assert tail["tool_call_id"] == "call-1"
    assert tail["delta"] == "1}"


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
    assert events[0]["delta"] == "a" * 20

    flushed = state.flush_tool_args_previews()
    assert len(flushed) == 1
    assert flushed[0]["tool_call_id"] == "tool-1"
    assert flushed[0]["delta"] == "b" * 20


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
