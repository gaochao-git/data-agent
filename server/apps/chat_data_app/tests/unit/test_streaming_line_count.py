"""file_write/file_edit 流式行数进度（tool_progress +N / -M）单测。

覆盖：
- ``_count_text_lines`` 行数口径（空文本、结尾换行）
- ``_extract_streaming_text_field`` 流式 JSON 字段提取（未闭合、转义、子串安全）
- ``_ToolStreamState.handle_tool_call_chunks`` 集成：file_edit 的 -M（整段替换口径）、
  file_write 无 -M、非目标工具不推送、计数去重、完成态清理
"""
import json

from apps.chat_data_app import agent_stream


def sse_event_name(event: str) -> str:
    return event.split("\n", 1)[0].removeprefix("event: ")


def sse_event_payload(event: str) -> dict:
    return json.loads(event.split("\ndata: ", 1)[1].rsplit("\n", 2)[0])


def progress_counts(events: list[str]) -> list[tuple[int, int]]:
    """从事件流中提取 (content_lines, removed_lines) 序列。"""
    return [
        (payload["content_lines"], payload["removed_lines"])
        for event in events
        if sse_event_name(event) == "tool_progress"
        for payload in [sse_event_payload(event)]
    ]


# --------------------------------------------------------------------------- #
# _count_text_lines
# --------------------------------------------------------------------------- #


def test_count_text_lines_empty_and_partial():
    assert agent_stream._count_text_lines("") == 0
    assert agent_stream._count_text_lines("a") == 1
    assert agent_stream._count_text_lines("a\n") == 1
    assert agent_stream._count_text_lines("a\nb") == 2
    assert agent_stream._count_text_lines("a\nb\n") == 2


# --------------------------------------------------------------------------- #
# _extract_streaming_text_field
# --------------------------------------------------------------------------- #


def test_extract_field_from_complete_json():
    args = '{"file_path": "a.html", "content": "line1\\nline2"}'
    assert agent_stream._extract_streaming_text_field(args, "content") == "line1\nline2"


def test_extract_field_from_unclosed_json():
    """流式生成中 JSON 未闭合，也能提取当前已生成的部分。"""
    args = '{"file_path": "a.html", "content": "line1\\nli'
    assert agent_stream._extract_streaming_text_field(args, "content") == "line1\nli"


def test_extract_field_handles_escapes():
    args = r'{"content": "a\nb\"c\\d\/e\tf"}'
    assert agent_stream._extract_streaming_text_field(args, "content") == 'a\nb"c\\d/e\tf'


def test_extract_field_returns_none_before_value_starts():
    assert agent_stream._extract_streaming_text_field('{"file_path": "a.html"', "content") is None
    assert agent_stream._extract_streaming_text_field('{"file_path": "a.html", "content"', "content") is None
    assert agent_stream._extract_streaming_text_field('{"file_path": "a.html", "content":', "content") is None
    assert agent_stream._extract_streaming_text_field('{"file_path": "a.html", "content": 123', "content") is None


def test_extract_field_key_substring_safety():
    """``content_type`` 不是 ``content`` 的匹配（marker 含闭合引号）。"""
    args = '{"content_type": "text/html", "content": "body"}'
    assert agent_stream._extract_streaming_text_field(args, "content") == "body"


def test_extract_field_nested_object_value_is_skipped_until_string():
    """值不是字符串字面量（对象/数字）时不提取。"""
    assert agent_stream._extract_streaming_text_field('{"content": {"a": 1}', "content") is None


# --------------------------------------------------------------------------- #
# handle_tool_call_chunks 集成
# --------------------------------------------------------------------------- #


def _feed(state: agent_stream._ToolStreamState, chunks: list[dict]) -> list[str]:
    events: list[str] = []
    for chunk in chunks:
        events.extend(state.handle_tool_call_chunks([chunk]))
    return events


def test_file_edit_streams_added_and_removed_lines():
    """file_edit：old_content 先到计 -M，new_content 后到计 +N（整段替换口径）。"""
    state = agent_stream._ToolStreamState()
    events = _feed(state, [
        {"id": "call-1", "name": "file_edit", "args": "", "index": 0},
        {"id": "", "name": "", "args": '{"file_path": "a.html", "old_content": "l1\\nl2\\nl3"', "index": 0},
        {"id": "", "name": "", "args": ', "new_content": "x\\n', "index": 0},
        {"id": "", "name": "", "args": 'y"}', "index": 0},
    ])

    assert progress_counts(events) == [(0, 3), (1, 3), (2, 3)]
    tool_started = [e for e in events if sse_event_name(e) == "tool_started"]
    assert len(tool_started) == 1


def test_file_edit_dedupes_unchanged_counts():
    """计数未变化的 chunk 不重复推送（old_content 完成后、new_content 开始前）。"""
    state = agent_stream._ToolStreamState()
    events = _feed(state, [
        {"id": "call-1", "name": "file_edit", "args": "", "index": 0},
        {"id": "", "name": "", "args": '{"old_content": "a\\nb"', "index": 0},
        # 不含新内容的空白 chunk：计数仍为 (0, 2)，不应再推
        {"id": "", "name": "", "args": '   ', "index": 0},
        {"id": "", "name": "", "args": ', "new_content": "c"}', "index": 0},
    ])

    assert progress_counts(events) == [(0, 2), (1, 2)]


def test_file_write_has_no_removed_lines():
    state = agent_stream._ToolStreamState()
    events = _feed(state, [
        {"id": "call-1", "name": "file_write", "args": '{"file_path": "b.html"', "index": 0},
        {"id": "", "name": "", "args": ' "content": "a\\nb\\nc"}', "index": 0},
    ])

    # content 开始生成前推 (0, 0)；removed_lines 恒为 0
    assert progress_counts(events) == [(0, 0), (3, 0)]


def test_non_target_tool_emits_no_progress():
    state = agent_stream._ToolStreamState()
    events = _feed(state, [
        {"id": "call-1", "name": "file_read", "args": "", "index": 0},
        {"id": "", "name": "", "args": '{"file_path": "b.html", "content": "a\\nb"}', "index": 0},
    ])

    assert progress_counts(events) == []


def test_progress_state_cleared_after_tool_result():
    """工具结果到达后，行数去重状态与 args buffer 一起清理。"""
    state = agent_stream._ToolStreamState()
    _feed(state, [
        {"id": "call-1", "name": "file_write", "args": "", "index": 0},
        {"id": "", "name": "", "args": '{"content": "a"}', "index": 0},
    ])
    assert state.progress_line_counts == {"call-1": (1, 0)}

    from langchain_core.messages import ToolMessageChunk

    chunk = ToolMessageChunk(content="ok", tool_call_id="call-1", name="file_write")
    state.build_tool_message_payload(chunk)

    assert "call-1" not in state.progress_line_counts
    assert "call-1" not in state.args_buffer
