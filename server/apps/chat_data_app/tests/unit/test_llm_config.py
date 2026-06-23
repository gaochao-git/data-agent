import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
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
    }


def test_chat_data_thinking_disabled_by_default(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("CHAT_DATA_MODEL", "qwen3.6-35b-a3b")
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

    monkeypatch.setenv("CHAT_DATA_MODEL", "qwen3.6-35b-a3b")
    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)

    llm_config.get_chat_data_llm(enable_thinking=True)

    _assert_thinking_extra_body(captured["extra_body"], True)


def test_chat_data_glm_uses_same_thinking_payload(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("CHAT_DATA_MODEL", "glm-5.1")
    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)

    llm_config.get_chat_data_llm(enable_thinking=True)

    assert captured["model"] == "glm-5.1"
    _assert_thinking_extra_body(captured["extra_body"], True)


def test_chat_data_non_qwen_disables_thinking_by_default(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("CHAT_DATA_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("CHAT_DATA_API_KEY", raising=False)
    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)

    llm_config.get_chat_data_llm()

    _assert_thinking_extra_body(captured["extra_body"], False)
    assert captured["api_key"] is None


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


def test_chat_data_model_options_reads_comma_separated_models(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_MODEL", "qwen3.5-35b-a3b")
    monkeypatch.setenv("CHAT_DATA_MODELS", "qwen3.5-35b-a3b,qwen3.5-36b-a3b")

    result = llm_config.get_chat_data_model_options()

    assert result["default_model"] == "qwen3.5-35b-a3b"
    assert result["models"] == [
        {"id": "qwen3.5-35b-a3b", "label": "qwen3.5-35b-a3b", "value": "qwen3.5-35b-a3b", "is_default": True},
        {"id": "qwen3.5-36b-a3b", "label": "qwen3.5-36b-a3b", "value": "qwen3.5-36b-a3b", "is_default": False},
    ]


def test_chat_data_model_options_includes_default_when_missing(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_MODEL", "qwen3.5-35b-a3b")
    monkeypatch.setenv("CHAT_DATA_MODELS", "qwen3.5-36b-a3b")

    result = llm_config.get_chat_data_model_options()

    assert [item["id"] for item in result["models"]] == ["qwen3.5-35b-a3b", "qwen3.5-36b-a3b"]


def test_chat_data_model_options_splits_chat_data_model(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_MODEL", "qwen3.5-35b-a3b,qwen3.6-35b-a3b")
    monkeypatch.delenv("CHAT_DATA_MODELS", raising=False)

    result = llm_config.get_chat_data_model_options()

    assert result["default_model"] == "qwen3.5-35b-a3b"
    assert [item["id"] for item in result["models"]] == ["qwen3.5-35b-a3b", "qwen3.6-35b-a3b"]


def test_chat_data_runtime_config_reads_data_analyze_feature(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_MODEL", "qwen3.5-35b-a3b")
    monkeypatch.setenv("CHAT_DATA_ANALYZE_ENABLED", "true")

    result = get_chat_data_runtime_config()

    assert result["models"][0]["id"] == "qwen3.5-35b-a3b"
    assert "default_model" not in result
    assert result["data_analyze_enabled"] is True


def test_chat_data_llm_uses_first_configured_model(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("CHAT_DATA_MODEL", "qwen3.5-35b-a3b,qwen3.6-35b-a3b")
    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)

    llm_config.get_chat_data_llm()

    assert captured["model"] == "qwen3.5-35b-a3b"


def test_chat_data_llm_temperature_reads_single_env(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(llm_config, "get_llm", fake_get_llm)
    monkeypatch.delenv("CHAT_DATA_TEMPERATURE", raising=False)

    llm_config.get_chat_data_llm()
    assert captured["temperature"] == 0.2

    monkeypatch.setenv("CHAT_DATA_TEMPERATURE", "0.35")
    llm_config.get_chat_data_llm()
    assert captured["temperature"] == 0.35

    llm_config.get_chat_data_llm(temperature=0)
    assert captured["temperature"] == 0

    monkeypatch.setenv("CHAT_DATA_TEMPERATURE", "bad")
    assert llm_config.get_chat_data_temperature() == 0.2
    monkeypatch.setenv("CHAT_DATA_TEMPERATURE", "-0.1")
    assert llm_config.get_chat_data_temperature() == 0.2
    monkeypatch.setenv("CHAT_DATA_TEMPERATURE", "2.1")
    assert llm_config.get_chat_data_temperature() == 0.2


def test_data_analyze_agent_uses_configured_model_fallback(monkeypatch):
    from apps.chat_data_app.agent import data_analyze_graph

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

    monkeypatch.setenv("CHAT_DATA_MODEL", "qwen3.6-35b-a3b")
    monkeypatch.setattr(data_analyze_graph, "get_chat_data_llm", fake_get_chat_data_llm)
    monkeypatch.setattr(data_analyze_graph, "create_agent", fake_create_agent)
    monkeypatch.setattr(data_analyze_graph, "create_data_analyze_tools", lambda *args, **kwargs: [])

    data_analyze_graph.create_data_analyze_agent(
        workspace_root="/tmp/fxcjg_test_data/workspace",
        system_prompt="test",
        model_id="Qwen/Qwen3-30B-A3B-Instruct-2507",
    )

    assert created_models == ["Qwen/Qwen3-30B-A3B-Instruct-2507", "qwen3.6-35b-a3b"]
    assert created_temperatures == [None, None]
    assert len(captured["middleware"]) == 3
    assert captured["middleware"][1].name == "ToolCallNameNormalizerMiddleware"
    assert captured["state_schema"] is data_analyze_graph.DataAnalyzeAgentState
    assert any(isinstance(item, data_analyze_graph.ModelFallbackMiddleware) for item in captured["middleware"])
