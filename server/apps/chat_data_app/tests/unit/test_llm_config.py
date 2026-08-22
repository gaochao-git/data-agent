import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services import llm_config
from apps.chat_data_app.services.runtime_config import get_chat_data_runtime_config


def _assert_thinking_extra_body(extra_body, enabled: bool):
    assert extra_body == {
        "enable_thinking": enabled,
        "chat_template_kwargs": {
            "enable_thinking": enabled,
        },
        "reasoning_effort": "high" if enabled else "none",
    }


def _set_models(monkeypatch, value: str) -> None:
    monkeypatch.setenv("CHAT_DATA_MODEL", value)


def test_chat_data_thinking_disabled_by_default(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    _set_models(monkeypatch, "qwen3.6-35b-a3b;0.2;128000;vlm-off")
    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)

    llm_config.get_chat_data_llm()

    assert captured["model"] == "qwen3.6-35b-a3b"
    assert captured["temperature"] == 0.2
    _assert_thinking_extra_body(captured["extra_body"], False)


def test_chat_data_qwen_can_enable_thinking(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    _set_models(monkeypatch, "qwen3.6-35b-a3b;0.2;128000;vlm-off")
    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)

    llm_config.get_chat_data_llm(enable_thinking=True)

    _assert_thinking_extra_body(captured["extra_body"], True)


def test_chat_data_glm_uses_same_thinking_payload(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    _set_models(monkeypatch, "glm-5.1;0.2;128000;vlm-off")
    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)

    llm_config.get_chat_data_llm(enable_thinking=True)

    assert captured["model"] == "glm-5.1"
    _assert_thinking_extra_body(captured["extra_body"], True)


def test_chat_data_non_qwen_disables_thinking_by_default(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    _set_models(monkeypatch, "gpt-4o-mini;0.2;128000;vlm-off")
    monkeypatch.delenv("CHAT_DATA_API_KEY", raising=False)
    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)

    llm_config.get_chat_data_llm()

    _assert_thinking_extra_body(captured["extra_body"], False)
    assert captured["api_key"] is None


def test_chat_data_reasoning_effort_carried_for_thinking_toggle(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    _set_models(monkeypatch, "deepseek-reasoner;0.2;64000;vlm-off")
    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)

    llm_config.get_chat_data_llm(enable_thinking=True)
    assert captured["extra_body"]["reasoning_effort"] == "high"

    llm_config.get_chat_data_llm(enable_thinking=False)
    assert captured["extra_body"]["reasoning_effort"] == "none"


def test_chat_data_api_key_is_decrypted_from_existing_env_name(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("CHAT_DATA_API_KEY", "encrypted-api-key")
    monkeypatch.setattr(llm_config, "decrypt_password", lambda value: f"plain:{value}")
    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)

    llm_config.get_chat_data_llm()

    assert captured["api_key"] == "plain:encrypted-api-key"


def test_chat_data_model_options_reads_model_configs(monkeypatch):
    _set_models(monkeypatch, "qwen3.5-35b-a3b;0.2;128000;vlm-off,qwen3.5-36b-a3b;0.1;256000;vlm-off")

    result = llm_config.get_chat_data_model_options()

    assert result["default_model"] == "qwen3.5-35b-a3b"
    assert result["models"] == [
        {"id": "qwen3.5-35b-a3b", "label": "qwen3.5-35b-a3b", "value": "qwen3.5-35b-a3b", "is_default": True},
        {"id": "qwen3.5-36b-a3b", "label": "qwen3.5-36b-a3b", "value": "qwen3.5-36b-a3b", "is_default": False},
    ]
    assert llm_config.get_chat_data_model_config("qwen3.5-36b-a3b") == {
        "id": "qwen3.5-36b-a3b",
        "temperature": 0.1,
        "context_window": 256000,
        "vlm": False,
    }


def test_chat_data_model_config_parses_vlm_capability(monkeypatch):
    _set_models(
        monkeypatch,
        "stealth/ox-alpha;0.2;256000;vlm-on,z-ai/glm-5.2:free;0.2;256000;vlm-off",
    )

    configs = llm_config.get_chat_data_model_configs()

    assert configs[0] == {
        "id": "stealth/ox-alpha",
        "temperature": 0.2,
        "context_window": 256000,
        "vlm": True,
    }
    assert configs[1] == {
        "id": "z-ai/glm-5.2:free",
        "temperature": 0.2,
        "context_window": 256000,
        "vlm": False,
    }


def test_chat_data_model_config_requires_capability_segment(monkeypatch):
    _set_models(monkeypatch, "stealth/ox-alpha;0.2;256000")

    with pytest.raises(ValueError, match="格式错误"):
        llm_config.get_chat_data_model_configs()


def test_chat_data_model_config_rejects_unknown_capability(monkeypatch):
    _set_models(monkeypatch, "stealth/ox-alpha;0.2;256000;vision")

    with pytest.raises(ValueError, match="能力位不支持"):
        llm_config.get_chat_data_model_configs()


def test_context_compression_summary_output_max_tokens_reads_own_environment_variable(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS", "1536")
    monkeypatch.setenv("CHAT_DATA_OUTPUT_MAX_TOKENS", "8192")

    assert llm_config.get_context_compression_summary_output_max_tokens() == 1536


@pytest.mark.parametrize("value", ["", "0", "-1", "invalid"])
def test_context_compression_summary_output_max_tokens_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS", value)

    assert (
        llm_config.get_context_compression_summary_output_max_tokens()
        == llm_config.DEFAULT_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS
    )


def test_chat_data_runtime_config_reads_runtime_options(monkeypatch):
    _set_models(monkeypatch, "qwen3.5-35b-a3b;0.2;256000;vlm-off")
    monkeypatch.setattr("apps.chat_data_app.services.runtime_config.CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "sandbox")
    monkeypatch.setattr("apps.chat_data_app.services.runtime_config.CHAT_DATA_DEFAULT_BASH_ENABLED", True)
    monkeypatch.setattr("apps.chat_data_app.services.runtime_config.CHAT_DATA_CAPABILITY_USER_OVERRIDABLE", True)

    result = get_chat_data_runtime_config()

    assert result["models"][0]["id"] == "qwen3.5-35b-a3b"
    assert "default_model" not in result
    assert result["runtime_options"] == {"workspace_backend": "sandbox", "bash_enabled": True}
    assert result["user_overridable"] is True
    assert result["context_window"] == 256000


def test_chat_data_llm_uses_first_configured_model(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    _set_models(monkeypatch, "qwen3.5-35b-a3b;0.2;128000;vlm-off,qwen3.6-35b-a3b;0.1;256000;vlm-off")
    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)

    llm_config.get_chat_data_llm()

    assert captured["model"] == "qwen3.5-35b-a3b"


def test_chat_data_llm_temperature_reads_model_config(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    _set_models(monkeypatch, "qwen3.5-35b-a3b;0.35;128000;vlm-off")
    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)

    llm_config.get_chat_data_llm()
    assert captured["temperature"] == 0.35

    llm_config.get_chat_data_llm(temperature=0)
    assert captured["temperature"] == 0

    assert llm_config.get_chat_data_temperature("qwen3.5-35b-a3b") == 0.35


def test_chat_data_model_config_rejects_invalid_format(monkeypatch):
    for value in ["qwen", "qwen;bad;128000", "qwen;-0.1;128000", "qwen;0.2;bad", "qwen;0.2;0"]:
        monkeypatch.setenv("CHAT_DATA_MODEL", value)
        try:
            llm_config.get_chat_data_model_configs()
        except ValueError:
            continue
        raise AssertionError(f"invalid CHAT_DATA_MODEL was accepted: {value}")


def test_data_agent_uses_configured_model_without_fallback(monkeypatch, tmp_path):
    from apps.chat_data_app.agent import data_agent
    from apps.chat_data_app.agent.agent_state import ChatDataAgentState
    from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions
    from apps.chat_data_app.services.agent_run.conversation_workspace import ConversationWorkspaceRuntime

    created_models = []
    created_temperatures = []
    captured = {}

    class FakeLLM:
        pass

    def fake_get_chat_data_llm(**kwargs):
        created_models.append(kwargs.get("model"))
        created_temperatures.append(kwargs.get("temperature"))
        return FakeLLM()

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return object()

    _set_models(monkeypatch, "qwen3.6-35b-a3b;0.2;128000;vlm-off")
    monkeypatch.setattr(data_agent, "get_chat_data_llm", fake_get_chat_data_llm)
    monkeypatch.setattr(data_agent, "create_agent", fake_create_agent)
    monkeypatch.setattr(data_agent, "assemble_tools", lambda *args, **kwargs: [])

    data_agent.create_data_agent(
        AgentRuntimeOptions(workspace_backend="local", bash_enabled=False),
        workspace_runtime=ConversationWorkspaceRuntime(
            workspace_root=str(tmp_path),
            user_id="u1",
            backend="local",
        ),
        model_invocation=data_agent.ModelInvocationConfig(
            system_prompt="test",
            model_id="Qwen/Qwen3-30B-A3B-Instruct-2507",
        ),
    )

    assert created_models == ["Qwen/Qwen3-30B-A3B-Instruct-2507"]
    assert created_temperatures == [None]
    middleware_names = [getattr(item, "name", "") for item in captured["middleware"]]
    assert "ToolCallNameNormalizerMiddleware" in middleware_names
    assert "ToolErrorMiddleware" in middleware_names
    assert captured["state_schema"] is ChatDataAgentState
