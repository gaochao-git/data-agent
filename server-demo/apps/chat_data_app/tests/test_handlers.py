import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.handlers import (
    _finish_running_tool,
    _register_running_tool,
    _resolve_active_tool_call_id,
)


def test_resolve_active_tool_call_id_prefers_chunk_id():
    running = ["tool_a", "tool_b"]
    assert _resolve_active_tool_call_id("tool_c", "tool_b", running) == "tool_c"


def test_resolve_active_tool_call_id_uses_current_active_when_chunk_id_missing():
    running = ["tool_a", "tool_b"]
    assert _resolve_active_tool_call_id(None, "tool_b", running) == "tool_b"


def test_finish_running_tool_falls_back_to_previous_running_tool():
    running = []
    _register_running_tool(running, "tool_a")
    _register_running_tool(running, "tool_b")

    next_active = _finish_running_tool(running, "tool_b", "tool_b")

    assert running == ["tool_a"]
    assert next_active == "tool_a"


def test_finish_running_tool_clears_active_when_last_tool_finishes():
    running = []
    _register_running_tool(running, "tool_a")

    next_active = _finish_running_tool(running, "tool_a", "tool_a")

    assert running == []
    assert next_active is None
