import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from langchain_core.messages import HumanMessage

from apps.chat_data_app.agent import state
from apps.chat_data_app.agent.tools import common
from utils.lang_util import conversation


def test_query_result_artifact_save_delegates_to_artifact_store(monkeypatch):
    captured = {}

    def fake_save_artifact(artifact_type, data, artifact_id=None, conversation_id=None):
        captured["save"] = (artifact_type, data, artifact_id, conversation_id)
        return "result_1"

    monkeypatch.setattr(state, "save_artifact", fake_save_artifact)

    assert state.save_query_result({"rows": []}, conversation_id="conv_a") == "result_1"
    assert captured["save"] == (state.ArtifactType.QUERY_RESULT, {"rows": []}, None, "conv_a")


def test_state_reducers_cover_empty_merge_append_dedupe_and_window():
    assert state.merge_tables_ddl(None, None) == ""
    assert state.merge_tables_ddl("old", None) == "old"
    assert state.merge_tables_ddl(None, "new") == "new"
    assert state.merge_tables_ddl("old", "new") == "old\n\nnew"

    assert set(state.merge_table_names(["orders"], ["users", "orders"])) == {"orders", "users"}
    assert state.merge_table_names(None, None) == []

    assert state.merge_reasoning_segments(None, [{"id": "r1"}]) == [{"id": "r1"}]
    assert state.merge_reasoning_segments([{"id": "r1"}], [{"id": "r2"}]) == [{"id": "r1"}, {"id": "r2"}]


def test_serialize_semantic_config_selection_keeps_refresh_payload():
    payload = conversation._serialize_semantic_config_selection({
        "semantic_config_id": "cfg_pay",
        "semantic_config_name": "支付汇总",
        "confidence": "high",
        "reason": "命中交易金额",
        "matched_metrics": ["交易金额", ""],
        "matched_dimensions": ["机构"],
        "auto_selected": True,
        "user_selected": True,
    })

    assert payload == {
        "semantic_config_id": "cfg_pay",
        "semantic_config_name": "支付汇总",
        "confidence": "high",
        "reason": "命中交易金额",
        "matched_metrics": ["交易金额", ""],
        "matched_dimensions": ["机构"],
        "auto_selected": True,
        "user_selected": True,
    }
    assert conversation._serialize_semantic_config_selection({}) is None
    assert conversation._serialize_semantic_config_selection(None) is None


def test_serialize_messages_keeps_user_semantic_config_selection():
    messages = [
        HumanMessage(
            content="商品数量有多少",
            additional_kwargs={
                "timestamp": "2026-06-10 11:30:00",
                "semantic_config_selection": {
                    "semantic_config_id": "cfg_test",
                    "semantic_config_name": "测试指标集",
                    "reason": "用户从候选指标集中选择",
                    "user_selected": True,
                },
            },
        )
    ]

    payload = conversation.serialize_messages(messages)

    assert payload[0]["role"] == "user"
    assert payload[0]["semantic_config_selection"]["semantic_config_id"] == "cfg_test"
    assert payload[0]["semantic_config_selection"]["semantic_config_name"] == "测试指标集"
    assert payload[0]["semantic_config_selection"]["user_selected"] is True


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
    assert common.json_dumps({"中文": object()}).startswith("{\n  \"中文\": \"<object object at")
    assert common.normalize_json_like_payload({"a": 1}) == {"a": 1}
    assert common.normalize_json_like_payload("") == ""
    assert common.normalize_json_like_payload("not json") == "not json"
    assert common.normalize_json_like_payload("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert common.normalize_json_like_payload("\"{\\\"a\\\": 1}\"") == {"a": 1}
    assert common.normalize_json_like_payload("```\n{\"a\": 1}") == {"a": 1}
    assert common.normalize_json_like_payload("{\"a\": 1}\n```") == "{\"a\": 1}\n```"
    assert common.normalize_json_like_payload("```\nnot json\n```") == "```\nnot json\n```"


def test_common_iter_files_skips_ignored_root_name(tmp_path):
    ignored_root = tmp_path / "node_modules"
    nested = ignored_root / "a.txt"
    nested.parent.mkdir()
    nested.write_text("a", encoding="utf-8")

    assert common.iter_files(Path(ignored_root)) == []
