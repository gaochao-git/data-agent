import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services import runtime_config


def test_read_positive_int_env_returns_default_for_empty_invalid_and_non_positive(monkeypatch):
    monkeypatch.delenv("CHATDATA_TEST_INT", raising=False)
    assert runtime_config.read_positive_int_env("CHATDATA_TEST_INT", 9) == 9

    monkeypatch.setenv("CHATDATA_TEST_INT", "bad")
    assert runtime_config.read_positive_int_env("CHATDATA_TEST_INT", 9) == 9

    monkeypatch.setenv("CHATDATA_TEST_INT", "0")
    assert runtime_config.read_positive_int_env("CHATDATA_TEST_INT", 9) == 9

    monkeypatch.setenv("CHATDATA_TEST_INT", "7")
    assert runtime_config.read_positive_int_env("CHATDATA_TEST_INT", 9) == 7


def test_read_bool_env_handles_common_truthy_and_default(monkeypatch):
    monkeypatch.delenv("CHATDATA_TEST_BOOL", raising=False)
    assert runtime_config.read_bool_env("CHATDATA_TEST_BOOL", True) is True
    assert runtime_config.read_bool_env("CHATDATA_TEST_BOOL", False) is False

    for value in ["true", " TRUE ", "True"]:
        monkeypatch.setenv("CHATDATA_TEST_BOOL", value)
        assert runtime_config.read_bool_env("CHATDATA_TEST_BOOL") is True

    for value in ["false", " FALSE ", "False"]:
        monkeypatch.setenv("CHATDATA_TEST_BOOL", value)
        assert runtime_config.read_bool_env("CHATDATA_TEST_BOOL", False) is False

    monkeypatch.delenv("CHATDATA_TEST_BOOL", raising=False)
    assert runtime_config.read_bool_env("CHATDATA_TEST_BOOL", False) is False


def test_read_bool_env_rejects_synonyms_and_invalid_with_error(monkeypatch):
    """1/0、yes/on 等变体与拼写错误不静默生效：安全语义的开关必须 fail-fast。"""
    for value in ["1", "0", "yes", "on", "ture", ""]:
        monkeypatch.setenv("CHATDATA_TEST_BOOL", value)
        if value == "":
            monkeypatch.delenv("CHATDATA_TEST_BOOL", raising=False)
            assert runtime_config.read_bool_env("CHATDATA_TEST_BOOL", False) is False
            continue
        with pytest.raises(ValueError, match="不是合法布尔值"):
            runtime_config.read_bool_env("CHATDATA_TEST_BOOL", False)



def test_read_bounded_float_env_clamps_compression_ratio(monkeypatch):
    monkeypatch.setenv("CHATDATA_TEST_RATIO", "0.75")
    assert runtime_config.read_bounded_float_env("CHATDATA_TEST_RATIO", 0.70, minimum=0.01, maximum=0.80) == 0.75

    monkeypatch.setenv("CHATDATA_TEST_RATIO", "0.95")
    assert runtime_config.read_bounded_float_env("CHATDATA_TEST_RATIO", 0.70, minimum=0.01, maximum=0.80) == 0.80

    monkeypatch.setenv("CHATDATA_TEST_RATIO", "bad")
    assert runtime_config.read_bounded_float_env("CHATDATA_TEST_RATIO", 0.70, minimum=0.01, maximum=0.80) == 0.70


def test_compression_trigger_ratio_rejects_invalid_startup_config(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_CONTEXT_COMPRESSION_TRIGGER_RATIO", raising=False)
    assert runtime_config.read_compression_trigger_ratio_env() == 0.70

    monkeypatch.setenv("CHAT_DATA_CONTEXT_COMPRESSION_TRIGGER_RATIO", "0.95")
    assert runtime_config.read_compression_trigger_ratio_env() == 0.95

    for invalid in ("0", "-0.1", "0.951", "nan", "inf", "bad"):
        monkeypatch.setenv("CHAT_DATA_CONTEXT_COMPRESSION_TRIGGER_RATIO", invalid)
        with pytest.raises(RuntimeError, match="CHAT_DATA_CONTEXT_COMPRESSION_TRIGGER_RATIO"):
            runtime_config.read_compression_trigger_ratio_env()


def test_context_window_reads_chat_data_model_config(monkeypatch, tmp_path):
    import json

    registry = tmp_path / "models.json"
    registry.write_text(json.dumps([
        {"id": "glm-5.1", "temperature": 0.2, "context_window": 128000, "vlm": False,
         "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False}},
        {"id": "kimi-k2.5", "temperature": 0.1, "context_window": 256000, "vlm": False,
         "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False}},
    ]), encoding="utf-8")
    monkeypatch.setenv("CHAT_DATA_MODELS_FILE", str(registry))
    assert runtime_config.get_context_window("") == 128000
    assert runtime_config.get_context_window("glm-5.1") == 128000
    assert runtime_config.get_context_window("kimi-k2.5") == 256000
    # fail-fast：未注册模型报错而非静默回退
    with pytest.raises(ValueError, match="不在"):
        runtime_config.get_context_window("unknown")


def test_get_context_compression_config_returns_runtime_values(monkeypatch):
    monkeypatch.setattr(runtime_config, "CONTEXT_COMPRESSION_ENABLED", False)
    monkeypatch.setenv("CHAT_DATA_CONTEXT_COMPRESSION_TRIGGER_RATIO", "0.70")

    config = runtime_config.get_context_compression_config()

    assert config == {
        "enabled": False,
        "trigger_ratio": 0.70,
    }


def test_context_usage_system_prompt_visibility_flag(monkeypatch):
    monkeypatch.setattr(runtime_config, "CONTEXT_USAGE_SHOW_SYSTEM_PROMPT", False)
    assert runtime_config.is_context_usage_system_prompt_visible() is False

    monkeypatch.setattr(runtime_config, "CONTEXT_USAGE_SHOW_SYSTEM_PROMPT", True)
    assert runtime_config.is_context_usage_system_prompt_visible() is True


def test_normalize_row_limit_uses_default_and_rejects_invalid_or_too_large(monkeypatch):
    monkeypatch.setattr(runtime_config, "DEFAULT_ROW_LIMIT", 100)

    assert runtime_config.normalize_row_limit(None) == 100
    assert runtime_config.normalize_row_limit(20) == 20

    with pytest.raises(ValueError, match="必须为正整数"):
        runtime_config.normalize_row_limit(0)
    with pytest.raises(ValueError, match="必须为正整数"):
        runtime_config.normalize_row_limit("10")
    with pytest.raises(ValueError, match="最多返回 100 条"):
        runtime_config.normalize_row_limit(101)
