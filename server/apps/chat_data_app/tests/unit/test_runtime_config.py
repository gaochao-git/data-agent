import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
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

    for value in ["1", "true", "yes", "y", "on", " TRUE "]:
        monkeypatch.setenv("CHATDATA_TEST_BOOL", value)
        assert runtime_config.read_bool_env("CHATDATA_TEST_BOOL") is True

    for value in ["0", "false", "no", "off", "bad"]:
        monkeypatch.setenv("CHATDATA_TEST_BOOL", value)
        assert runtime_config.read_bool_env("CHATDATA_TEST_BOOL", True) is False


def test_context_window_reads_chat_data_model_config(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_MODEL", "glm-5.1;0.2;128000,kimi-k2.5;0.1;256000")
    assert runtime_config.get_context_window("") == 128000
    assert runtime_config.get_context_window("glm-5.1") == 128000
    assert runtime_config.get_context_window("kimi-k2.5") == 256000
    assert runtime_config.get_context_window("unknown") == 128000


def test_get_context_edit_config_returns_runtime_values(monkeypatch):
    monkeypatch.setattr(runtime_config, "CONTEXT_EDIT_ENABLED", False)

    config = runtime_config.get_context_edit_config()

    assert config == {
        "enabled": False,
        "trigger_ratio": 0.70,
    }


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
