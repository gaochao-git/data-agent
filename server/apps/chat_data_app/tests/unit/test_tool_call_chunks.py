"""主图与子图共用的工具分片身份索引。"""

from apps.chat_data_app.agent.middleware.subagent_stream import _SubagentStreamState
from apps.chat_data_app.agent_stream_events import _ToolStreamState
from apps.chat_data_app.tool_call_chunks import ToolCallChunkIndex


def test_resolve_id_prefers_id_then_index_then_fallback():
    index = ToolCallChunkIndex()
    index.remember("call-1", "file_read", 0)

    assert index.resolve_id("call-2", 0, fallback="active") == "call-2"
    assert index.resolve_id("", 0, fallback="active") == "call-1"
    assert index.resolve_id("", 9, fallback="active") == "active"


def test_remember_name_by_index_supports_late_id():
    index = ToolCallChunkIndex()
    index.remember("", "file_read", 0)

    assert index.resolve_name("call-1", chunk_index=0) == "file_read"

    index.remember("call-1", "", 0)
    assert index.resolve_name("call-1") == ""
    assert index.resolve_id("call-1", 0) == "call-1"


def test_active_id_tracks_latest_chunk_with_id_only():
    index = ToolCallChunkIndex()
    index.remember("call-1", "file_read", 0)
    index.remember("", "", 0)

    assert index.active_id == "call-1"

    index.remember("call-2", "file_write", 1)
    assert index.active_id == "call-2"


def test_main_state_resolves_index_only_chunk_after_first_id():
    state = _ToolStreamState()
    state.handle_tool_call_chunks([{"index": 0, "id": "call-1", "name": "file_read", "args": ""}])
    state.handle_tool_call_chunks([{"index": 0, "args": '{"file_path":"a.txt"}'}])

    assert state.tool_names == {"call-1": "file_read"}
    assert state.args_buffer["call-1"] == '{"file_path":"a.txt"}'


def test_subagent_state_resolves_index_only_chunk_after_first_id():
    state = _SubagentStreamState("parent-1")

    assert state._resolve_tool_args_chunk({"index": 0, "id": "call-1", "name": "file_read", "args": ""}) == (
        "call-1",
        "file_read",
        "",
    )
    assert state._resolve_tool_args_chunk({"index": 0, "args": '{"file_path":"a.txt"}'}) == (
        "call-1",
        "file_read",
        '{"file_path":"a.txt"}',
    )
