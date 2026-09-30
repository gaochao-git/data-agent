import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

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
    registry.write_text(
        json.dumps(
            [
                {
                    "id": "glm-5.1",
                    "temperature": 0.2,
                    "context_window": 128000,
                    "vlm": False,
                    "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False},
                },
                {
                    "id": "kimi-k2.5",
                    "temperature": 0.1,
                    "context_window": 256000,
                    "vlm": False,
                    "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False},
                },
            ]
        ),
        encoding="utf-8",
    )
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


@pytest.mark.asyncio
async def test_image_size_limit_reads_setting_service(monkeypatch):
    """图片大小上限运行期经配置服务读取（表覆盖 > env > 默认）。

    上传大小上限（CHAT_DATA_MAX_UPLOAD_SIZE_MB）与图片上限
    （CHAT_DATA_MAX_IMAGE_SIZE_MB）均已迁到后台配置变量表
    （services/setting_service.py，表覆盖 > env > 默认），不再走模块级常量。
    """
    from apps.chat_data_app.agent.workspace import uploaded_file_parser as parser

    monkeypatch.setenv("CHAT_DATA_MAX_IMAGE_SIZE_MB", "3")
    assert await parser.get_image_size_limit() == (3, 3 * 1024 * 1024)

    # 非法 env 值回退代码默认
    monkeypatch.setenv("CHAT_DATA_MAX_IMAGE_SIZE_MB", "not-a-number")
    assert await parser.get_image_size_limit() == (5, 5 * 1024 * 1024)


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


def test_read_optional_positive_int_env_unset_invalid_and_valid(monkeypatch):
    """「不配置」是合法语义的可选正整数：未设置/非法/非正数都按未设置处理（返回 None）。"""
    monkeypatch.delenv("CHATDATA_TEST_OPT_INT", raising=False)
    assert runtime_config.read_optional_positive_int_env("CHATDATA_TEST_OPT_INT") is None

    monkeypatch.setenv("CHATDATA_TEST_OPT_INT", "bad")
    assert runtime_config.read_optional_positive_int_env("CHATDATA_TEST_OPT_INT") is None

    monkeypatch.setenv("CHATDATA_TEST_OPT_INT", "0")
    assert runtime_config.read_optional_positive_int_env("CHATDATA_TEST_OPT_INT") is None

    monkeypatch.setenv("CHATDATA_TEST_OPT_INT", "7")
    assert runtime_config.read_optional_positive_int_env("CHATDATA_TEST_OPT_INT") == 7


def test_read_optional_positive_ratio_env_validates_range(monkeypatch):
    """可选比例 env：未设置返回 None；合法返回数值；超上限/非法数字 fail-fast 不静默回退。"""
    monkeypatch.delenv("CHATDATA_TEST_RATIO_OPT", raising=False)
    assert runtime_config.read_optional_positive_ratio_env("CHATDATA_TEST_RATIO_OPT", maximum=0.95) is None

    monkeypatch.setenv("CHATDATA_TEST_RATIO_OPT", "0.05")
    assert runtime_config.read_optional_positive_ratio_env("CHATDATA_TEST_RATIO_OPT", maximum=0.95) == 0.05

    monkeypatch.setenv("CHATDATA_TEST_RATIO_OPT", "1.5")
    with pytest.raises(RuntimeError, match="必须大于 0 且不超过 0.95"):
        runtime_config.read_optional_positive_ratio_env("CHATDATA_TEST_RATIO_OPT", maximum=0.95)

    monkeypatch.setenv("CHATDATA_TEST_RATIO_OPT", "bad")
    with pytest.raises(RuntimeError, match="必须是 0 到 0.95 之间的数字"):
        runtime_config.read_optional_positive_ratio_env("CHATDATA_TEST_RATIO_OPT", maximum=0.95)


def test_module_level_backend_fallback_and_bash_local_warning(monkeypatch):
    """模块级自检：非法 workspace_backend 回退 local；bash+local 高危组合触发告警。

    两个分支都在 import 时执行，只能通过带 env 的模块 reload 覆盖。告警走项目
    自定义日志器（handler 绑定全局流，caplog/capsys/capfd 均收不到），故在
    Logger.warning 调用点拦截文本；结束时恢复 env 并再 reload 一次，避免污染
    其他用例读到的模块常量。
    """
    import importlib
    import logging

    records: list[str] = []
    original_warning = logging.Logger.warning

    def _capture_warning(self, msg, *args, **kwargs):
        records.append(msg % args if args else str(msg))
        original_warning(self, msg, *args, **kwargs)

    monkeypatch.setattr(logging.Logger, "warning", _capture_warning)
    monkeypatch.setenv("CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "kubernetes")
    monkeypatch.setenv("CHAT_DATA_DEFAULT_BASH_ENABLED", "true")
    reloaded = importlib.reload(runtime_config)
    try:
        assert reloaded.CHAT_DATA_DEFAULT_WORKSPACE_BACKEND == "local"
        assert any("非法，回退 local" in message for message in records)
        assert any("bash_enabled=true + workspace_backend=local" in message for message in records)
    finally:
        monkeypatch.delenv("CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", raising=False)
        monkeypatch.delenv("CHAT_DATA_DEFAULT_BASH_ENABLED", raising=False)
        importlib.reload(runtime_config)
