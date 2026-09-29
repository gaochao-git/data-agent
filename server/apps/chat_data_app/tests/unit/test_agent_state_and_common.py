import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.agent import (
    agent_runtime,
    agent_state,  # noqa: F401  # 测试按模块属性引用 agent_state，属有意重导出
    tool_results,
)
from apps.chat_data_app.agent.tools import common


def test_internal_query_result_protocol_is_not_public_envelope():
    content = tool_results.build_internal_query_result(
        {"status": "succeeded", "rows": []},
        query_mode="dsl2sql",
    )

    payload = tool_results.parse_internal_tool_result(content)

    assert payload is not None
    assert payload["type"] == tool_results.INTERNAL_TOOL_RESULT_TYPE
    assert payload["kind"] == tool_results.QUERY_RESULT_TYPE
    assert payload["payload"] == {"status": "succeeded", "rows": []}
    assert payload["meta"] == {"query_mode": "dsl2sql"}


def test_agent_runtime_helpers_build_shared_agent_bits():
    model_calls = []

    def fake_llm_factory(**kwargs):
        model_calls.append(kwargs)
        return {"llm": kwargs.get("model")}

    assert agent_runtime.create_streaming_chat_model(
        model_id="m1",
        enable_thinking=True,
        stream_usage=True,
        llm_factory=fake_llm_factory,
    ) == {"llm": "m1"}
    assert model_calls[-1]["streaming"] is True
    assert model_calls[-1]["stream_usage"] is True
    assert model_calls[-1]["enable_thinking"] is True


def test_common_workspace_root_validation_and_binary_detection(tmp_path):
    with pytest.raises(ValueError, match="缺少 workspace_root"):
        common.validate_workspace_root("  ")

    assert common.validate_workspace_root(str(tmp_path)) == str(tmp_path)

    text_file = tmp_path / "plain.txt"
    binary_file = tmp_path / "binary.bin"
    text_file.write_text("hello", encoding="utf-8")
    binary_file.write_bytes(b"abc\x00def")

    assert common.looks_binary(text_file) is False
    assert common.looks_binary(binary_file) is True
    assert common.looks_binary(tmp_path / "missing.bin") is False


def test_common_iter_files_skips_ignored_dirs_and_handles_single_file(tmp_path):
    normal = tmp_path / "src" / "a.txt"
    ignored = tmp_path / "node_modules" / "b.txt"
    nested_ignored = tmp_path / "src" / "__pycache__" / "c.pyc"
    normal.parent.mkdir(parents=True)
    ignored.parent.mkdir(parents=True)
    nested_ignored.parent.mkdir(parents=True)
    normal.write_text("a", encoding="utf-8")
    ignored.write_text("b", encoding="utf-8")
    nested_ignored.write_text("c", encoding="utf-8")

    assert common.iter_files(normal) == [normal]

    names = {path.name for path in common.iter_files(tmp_path)}

    assert names == {"a.txt"}


def test_common_format_json_and_normalize_json_like_payload():
    assert common.format_lines(["a", "b"], 3) == "     3 a\n     4 b"
    assert common.json_dumps({"中文": object()}).startswith('{\n  "中文": "<object object at')
    assert common.normalize_json_like_payload({"a": 1}) == {"a": 1}
    assert common.normalize_json_like_payload("") == ""
    assert common.normalize_json_like_payload("not json") == "not json"
    assert common.normalize_json_like_payload('```json\n{"a": 1}\n```') == {"a": 1}
    assert common.normalize_json_like_payload('"{\\"a\\": 1}"') == {"a": 1}
    assert common.normalize_json_like_payload('```\n{"a": 1}') == {"a": 1}
    assert common.normalize_json_like_payload('{"a": 1}\n```') == '{"a": 1}\n```'
    assert common.normalize_json_like_payload("```\nnot json\n```") == "```\nnot json\n```"


def test_common_iter_files_skips_ignored_root_name(tmp_path):
    ignored_root = tmp_path / "node_modules"
    nested = ignored_root / "a.txt"
    nested.parent.mkdir()
    nested.write_text("a", encoding="utf-8")

    assert common.iter_files(Path(ignored_root)) == []
