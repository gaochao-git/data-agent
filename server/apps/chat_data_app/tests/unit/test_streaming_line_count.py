"""file_write/file_edit 流式行数进度（tool_progress +N / -M）与流式 intent 预渲染单测。

覆盖：
- ``_count_text_lines`` 行数口径（空文本、结尾换行）
- ``_extract_streaming_text_field`` 流式 JSON 字段提取（未闭合、转义、子串安全）
- ``_ToolStreamState.handle_tool_call_chunks`` 集成：file_edit 的 -M（整段替换口径）、
  file_write 无 -M、非目标工具不推送、计数去重、完成态清理
- 流式 intent：所有工具 args 出现 intent 即推送（长耗时工具执行前展示目的）、
  去重、行数与 intent 合并事件、intent-only 事件不携带行数字段
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


def progress_intents(events: list[str]) -> list[str]:
    """从事件流中提取 intent 序列（仅含携带 intent 的事件）。"""
    return [
        payload["intent"]
        for event in events
        if sse_event_name(event) == "tool_progress"
        for payload in [sse_event_payload(event)]
        if "intent" in payload
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
    assert agent_stream._extract_streaming_text_field(args, "content") == ("line1\nline2", True)


def test_extract_field_from_unclosed_json():
    """流式生成中 JSON 未闭合，也能提取当前已生成的部分；closed=False。"""
    args = '{"file_path": "a.html", "content": "line1\\nli'
    assert agent_stream._extract_streaming_text_field(args, "content") == ("line1\nli", False)


def test_extract_field_handles_escapes():
    args = r'{"content": "a\nb\"c\\d\/e\tf"}'
    assert agent_stream._extract_streaming_text_field(args, "content") == ('a\nb"c\\d/e\tf', True)


def test_extract_field_returns_none_before_value_starts():
    assert agent_stream._extract_streaming_text_field('{"file_path": "a.html"', "content") == (None, False)
    assert agent_stream._extract_streaming_text_field('{"file_path": "a.html", "content"', "content") == (None, False)
    assert agent_stream._extract_streaming_text_field('{"file_path": "a.html", "content":', "content") == (None, False)
    assert agent_stream._extract_streaming_text_field('{"file_path": "a.html", "content": 123', "content") == (
        None,
        False,
    )


def test_extract_field_key_substring_safety():
    """``content_type`` 不是 ``content`` 的匹配（marker 含闭合引号）。"""
    args = '{"content_type": "text/html", "content": "body"}'
    assert agent_stream._extract_streaming_text_field(args, "content") == ("body", True)


def test_extract_field_nested_object_value_is_skipped_until_string():
    """值不是字符串字面量（对象/数字）时不提取。"""
    assert agent_stream._extract_streaming_text_field('{"content": {"a": 1}', "content") == (None, False)


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
    events = _feed(
        state,
        [
            {"id": "call-1", "name": "file_edit", "args": "", "index": 0},
            {"id": "", "name": "", "args": '{"file_path": "a.html", "old_content": "l1\\nl2\\nl3"', "index": 0},
            {"id": "", "name": "", "args": ', "new_content": "x\\n', "index": 0},
            {"id": "", "name": "", "args": 'y"}', "index": 0},
        ],
    )

    assert progress_counts(events) == [(0, 3), (1, 3), (2, 3)]
    tool_started = [e for e in events if sse_event_name(e) == "tool_started"]
    assert len(tool_started) == 1


def test_file_edit_dedupes_unchanged_counts():
    """计数未变化的 chunk 不重复推送（old_content 完成后、new_content 开始前）。"""
    state = agent_stream._ToolStreamState()
    events = _feed(
        state,
        [
            {"id": "call-1", "name": "file_edit", "args": "", "index": 0},
            {"id": "", "name": "", "args": '{"old_content": "a\\nb"', "index": 0},
            # 不含新内容的空白 chunk：计数仍为 (0, 2)，不应再推
            {"id": "", "name": "", "args": "   ", "index": 0},
            {"id": "", "name": "", "args": ', "new_content": "c"}', "index": 0},
        ],
    )

    assert progress_counts(events) == [(0, 2), (1, 2)]


def test_file_write_has_no_removed_lines():
    state = agent_stream._ToolStreamState()
    events = _feed(
        state,
        [
            {"id": "call-1", "name": "file_write", "args": '{"file_path": "b.html"', "index": 0},
            {"id": "", "name": "", "args": ' "content": "a\\nb\\nc"}', "index": 0},
        ],
    )

    # content 开始生成前推 (0, 0)；removed_lines 恒为 0
    assert progress_counts(events) == [(0, 0), (3, 0)]


def test_non_target_tool_emits_no_progress():
    state = agent_stream._ToolStreamState()
    events = _feed(
        state,
        [
            {"id": "call-1", "name": "file_read", "args": "", "index": 0},
            {"id": "", "name": "", "args": '{"file_path": "b.html", "content": "a\\nb"}', "index": 0},
        ],
    )

    assert progress_counts(events) == []


def test_intent_streamed_for_any_tool():
    """所有工具的 intent 参数在 args 里出现即推送，非行数工具的事件不带行数字段。"""
    state = agent_stream._ToolStreamState()
    events = _feed(
        state,
        [
            {"id": "call-1", "name": "file_read", "args": "", "index": 0},
            {"id": "", "name": "", "args": '{"file_path": "b.html", "intent": "读取配置文件"}', "index": 0},
        ],
    )

    progress = [sse_event_payload(e) for e in events if sse_event_name(e) == "tool_progress"]
    assert progress_intents(events) == ["读取配置文件"]
    assert all("content_lines" not in payload for payload in progress)
    assert all("removed_lines" not in payload for payload in progress)


def test_intent_not_streamed_until_string_closed():
    """intent 字符串未闭合时不推送（逐字流式 provider 下避免前缀洪峰），闭合后推一次。"""
    state = agent_stream._ToolStreamState()
    events = _feed(
        state,
        [
            {"id": "call-1", "name": "file_read", "args": "", "index": 0},
            # intent 逐字到达：每次前缀都不同，未闭合前不推
            {"id": "", "name": "", "args": '{"intent": "读', "index": 0},
            {"id": "", "name": "", "args": "取配", "index": 0},
            {"id": "", "name": "", "args": "置文件", "index": 0},
            # 闭合才推
            {"id": "", "name": "", "args": '"}', "index": 0},
        ],
    )

    assert progress_intents(events) == ["读取配置文件"]


def test_intent_dedupes_unchanged_value():
    """intent 未变化时不重复推送。"""
    state = agent_stream._ToolStreamState()
    events = _feed(
        state,
        [
            {"id": "call-1", "name": "file_read", "args": "", "index": 0},
            {"id": "", "name": "", "args": '{"intent": "读取配置文件"', "index": 0},
            # intent 已闭合且未变化的空白 chunk：不再推
            {"id": "", "name": "", "args": "   ", "index": 0},
            {"id": "", "name": "", "args": ', "file_path": "b.html"}', "index": 0},
        ],
    )

    assert progress_intents(events) == ["读取配置文件"]


def test_file_write_merges_counts_and_intent_in_one_event():
    """行数与 intent 同时首次出现时合并推一个事件，两者都携带。"""
    state = agent_stream._ToolStreamState()
    events = _feed(
        state,
        [
            {"id": "call-1", "name": "file_write", "args": "", "index": 0},
            {
                "id": "",
                "name": "",
                "args": '{"file_path": "b.html", "content": "a\\nb", "intent": "写入配置"}',
                "index": 0,
            },
        ],
    )

    progress = [sse_event_payload(e) for e in events if sse_event_name(e) == "tool_progress"]
    assert len(progress) == 1
    assert progress[0]["content_lines"] == 2
    assert progress[0]["removed_lines"] == 0
    assert progress[0]["intent"] == "写入配置"


def test_intent_only_event_after_counts_settled():
    """行数已推送后 intent 才到达：第二个事件只带 intent，行数字段缺省（前端保留已有值）。"""
    state = agent_stream._ToolStreamState()
    events = _feed(
        state,
        [
            {"id": "call-1", "name": "file_write", "args": "", "index": 0},
            {"id": "", "name": "", "args": '{"file_path": "b.html", "content": "a\\nb"', "index": 0},
            {"id": "", "name": "", "args": ', "intent": "写入配置"}', "index": 0},
        ],
    )

    progress = [sse_event_payload(e) for e in events if sse_event_name(e) == "tool_progress"]
    assert len(progress) == 2
    assert progress[0]["content_lines"] == 2 and "intent" not in progress[0]
    assert "content_lines" not in progress[1] and progress[1]["intent"] == "写入配置"


def test_progress_state_cleared_after_tool_result():
    """工具结果到达后，行数/intent 去重状态与 args buffer 一起清理。"""
    state = agent_stream._ToolStreamState()
    _feed(
        state,
        [
            {"id": "call-1", "name": "file_write", "args": "", "index": 0},
            {"id": "", "name": "", "args": '{"content": "a", "intent": "写入"}', "index": 0},
        ],
    )
    assert state.progress_line_counts == {"call-1": (1, 0)}
    assert state.progress_intents == {"call-1": "写入"}

    from langchain_core.messages import ToolMessageChunk

    chunk = ToolMessageChunk(content="ok", tool_call_id="call-1", name="file_write")
    state.build_tool_message_payload(chunk)

    assert "call-1" not in state.progress_line_counts
    assert "call-1" not in state.progress_intents
    assert "call-1" not in state.args_buffer
