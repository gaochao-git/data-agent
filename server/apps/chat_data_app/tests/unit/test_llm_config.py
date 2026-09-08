import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services import llm_config
from apps.chat_data_app.services.llm_config import ThinkingCapability
from apps.chat_data_app.services.runtime_config import get_chat_data_runtime_config


# ---------------------------------------------------------------------------
# 测试辅助：把模型清单写入临时注册表文件
# ---------------------------------------------------------------------------

def _set_registry(monkeypatch, tmp_path, models: list[dict]) -> str:
    path = tmp_path / "models.json"
    path.write_text(json.dumps(models, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CHAT_DATA_MODELS_FILE", str(path))
    monkeypatch.delenv("CHAT_DATA_MODEL", raising=False)
    return str(path)


def _ox_alpha(**overrides) -> dict:
    entry = {
        "id": "stealth/ox-alpha",
        "temperature": 0.2,
        "context_window": 256000,
        "vlm": True,
        "thinking": {
            "mode": "forced",
            "dialect": "openrouter",
            "efforts": ["low", "medium", "high"],
            "default_effort": "high",
        },
    }
    entry.update(overrides)
    return entry


def _glm_free() -> dict:
    return {
        "id": "z-ai/glm-5.2:free",
        "temperature": 0.2,
        "context_window": 256000,
        "vlm": False,
        "thinking": {
            "mode": "optional",
            "dialect": "openrouter",
            "default_on": False,
        },
    }


def _fake_get_llm(captured: dict):
    def fake(**kwargs):
        captured.update(kwargs)
        return object()
    return fake


# ---------------------------------------------------------------------------
# 注册表加载与校验（fail-fast）
# ---------------------------------------------------------------------------

def test_legacy_chat_data_model_variable_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAT_DATA_MODEL", "qwen;0.2;128000;vlm-off")

    with pytest.raises(ValueError, match="迁移"):
        llm_config.load_chat_data_model_configs()


def test_missing_models_file_env_rejected(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_MODELS_FILE", raising=False)
    monkeypatch.delenv("CHAT_DATA_MODEL", raising=False)

    with pytest.raises(ValueError, match="CHAT_DATA_MODELS_FILE"):
        llm_config.load_chat_data_model_configs()


def test_missing_file_and_invalid_json_rejected(monkeypatch, tmp_path):
    missing = str(tmp_path / "nope.json")
    monkeypatch.setenv("CHAT_DATA_MODELS_FILE", missing)
    with pytest.raises(ValueError, match="不存在"):
        llm_config.load_chat_data_model_configs()

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("CHAT_DATA_MODELS_FILE", str(bad))
    with pytest.raises(ValueError, match="JSON"):
        llm_config.load_chat_data_model_configs()


def test_duplicate_model_id_rejected(monkeypatch, tmp_path):
    _set_registry(monkeypatch, tmp_path, [_ox_alpha(), _ox_alpha()])
    with pytest.raises(ValueError, match="id 重复"):
        llm_config.load_chat_data_model_configs()


@pytest.mark.parametrize(
    "entry,match",
    [
        ({"temperature": 0.2}, "缺少 id"),
        (_ox_alpha(temperature=2.5), "temperature"),
        (_ox_alpha(context_window=0), "context_window"),
        (_ox_alpha(vlm="yes"), "vlm"),
        (_ox_alpha(max_output_tokens=0), "max_output_tokens"),
        (_ox_alpha(max_output_tokens=True), "max_output_tokens"),
        (_ox_alpha(max_output_tokens=256000), "必须小于 context_window"),
        (_ox_alpha(thinking={"mode": "maybe"}), "mode"),
        (_ox_alpha(thinking={"mode": "forced", "dialect": "anthropic"}), "dialect"),
        (
            _ox_alpha(thinking={"mode": "optional", "dialect": "chat_template", "default_on": True,
                                "efforts": ["low"]}),
            "chat_template 方言",
        ),
        (
            _ox_alpha(thinking={"mode": "optional", "dialect": "openrouter", "default_on": True,
                                "efforts": ["low"], "default_effort": "high"}),
            "default_effort",
        ),
        (_ox_alpha(thinking={"mode": "none", "dialect": "openrouter"}), "mode=none"),
        (_ox_alpha(thinking={"mode": "forced", "dialect": "openrouter", "default_on": True}), "default_on"),
        (_ox_alpha(thinking={"mode": "optional", "dialect": "openrouter"}), "default_on"),
    ],
)
def test_registry_validation_fail_fast(monkeypatch, tmp_path, entry, match):
    _set_registry(monkeypatch, tmp_path, [entry])
    with pytest.raises(ValueError, match=match):
        llm_config.load_chat_data_model_configs()


def test_registry_parses_thinking_capability(monkeypatch, tmp_path):
    deepseek = {
        "id": "deepseek-v4-flash",
        "temperature": 0.35,
        "context_window": 128000,
        "vlm": False,
        "thinking": {
            "mode": "optional",
            "dialect": "reasoning_effort",
            "efforts": ["low", "high"],
            "default_on": False,
            "default_effort": "high",
        },
    }
    mlx = {
        "id": "Qwen3.6-35B-A3B-4bit",
        "temperature": 0.2,
        "context_window": 48000,
        "vlm": True,
        "thinking": {"mode": "none"},
    }
    _set_registry(monkeypatch, tmp_path, [_ox_alpha(), deepseek, mlx])

    configs = llm_config.get_chat_data_model_configs()

    assert configs[0]["thinking"] == ThinkingCapability(
        mode="forced", dialect="openrouter",
        efforts=("low", "medium", "high"), default_effort="high",
    )
    assert configs[1]["thinking"] == ThinkingCapability(
        mode="optional", dialect="reasoning_effort",
        efforts=("low", "high"), default_on=False, default_effort="high",
    )
    assert configs[2]["thinking"] == ThinkingCapability(mode="none")
    assert configs[0]["max_output_tokens"] is None
    assert llm_config.get_default_chat_data_model() == "stealth/ox-alpha"


def test_unknown_model_id_raises_instead_of_fallback(monkeypatch, tmp_path):
    _set_registry(monkeypatch, tmp_path, [_ox_alpha()])
    with pytest.raises(ValueError, match="不在"):
        llm_config.get_chat_data_model_config("nope-model")


# ---------------------------------------------------------------------------
# 决策层：resolve_thinking 九宫格
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "capability,user_enabled,expected",
    [
        # none：一律不发
        (ThinkingCapability(mode="none"), True, ("omit", None)),
        (ThinkingCapability(mode="none"), False, ("omit", None)),
        (ThinkingCapability(mode="none"), None, ("omit", None)),
        # optional：跟随用户意图，未传按 default_on
        (ThinkingCapability(mode="optional", dialect="openrouter", default_on=True,
                            default_effort="medium"), True, ("on", "medium")),
        (ThinkingCapability(mode="optional", dialect="openrouter", default_on=True,
                            default_effort="medium"), False, ("off", None)),
        (ThinkingCapability(mode="optional", dialect="openrouter", default_on=True,
                            default_effort="medium"), None, ("on", "medium")),
        (ThinkingCapability(mode="optional", dialect="openrouter", default_on=False,
                            default_effort="high"), None, ("off", None)),
        # forced：忽略关闭请求，永不产生"关"形态
        (ThinkingCapability(mode="forced", dialect="openrouter",
                            default_effort="high"), False, ("on", "high")),
        (ThinkingCapability(mode="forced", dialect="openrouter",
                            default_effort="high"), True, ("on", "high")),
        (ThinkingCapability(mode="forced", dialect="openrouter"), None, ("on", None)),
    ],
)
def test_resolve_thinking_matrix(capability, user_enabled, expected):
    assert llm_config.resolve_thinking(capability, user_enabled) == expected


@pytest.mark.parametrize(
    "capability,user_enabled,user_effort,expected",
    [
        # 用户档位在 efforts 内：生效
        (ThinkingCapability(mode="optional", dialect="openrouter", default_on=True,
                            efforts=("low", "medium", "high"), default_effort="high"),
         True, "low", ("on", "low")),
        # forced 也接受用户档位
        (ThinkingCapability(mode="forced", dialect="openrouter",
                            efforts=("low", "medium", "high"), default_effort="high"),
         None, "medium", ("on", "medium")),
        # 非法档位回退 default_effort
        (ThinkingCapability(mode="optional", dialect="openrouter", default_on=True,
                            efforts=("low", "high"), default_effort="high"),
         True, "ultra", ("on", "high")),
        # 模型未声明 efforts：用户档位被忽略
        (ThinkingCapability(mode="forced", dialect="openrouter", default_effort=None),
         None, "low", ("on", None)),
    ],
)
def test_resolve_thinking_user_effort(capability, user_enabled, user_effort, expected):
    assert llm_config.resolve_thinking(capability, user_enabled, user_effort) == expected


# ---------------------------------------------------------------------------
# 协议层：方言 × 决策 → extra_body 矩阵
# ---------------------------------------------------------------------------

def _extra_body(capability, user_enabled=None):
    return llm_config._get_chat_data_extra_body(capability, enable_thinking=user_enabled)


def test_extra_body_omits_all_keys_for_none_mode():
    assert _extra_body(ThinkingCapability(mode="none"), True) == {}
    assert _extra_body(ThinkingCapability(mode="none"), False) == {}


def test_extra_body_chat_template_dialect():
    cap = ThinkingCapability(mode="optional", dialect="chat_template", default_on=False)
    assert _extra_body(cap, True) == {
        "enable_thinking": True,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    assert _extra_body(cap, False) == {
        "enable_thinking": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def test_extra_body_reasoning_effort_dialect():
    cap = ThinkingCapability(mode="optional", dialect="reasoning_effort",
                             efforts=("low", "high"), default_on=False, default_effort="high")
    assert _extra_body(cap, True) == {"reasoning_effort": "high"}
    assert _extra_body(cap, False) == {"reasoning_effort": "none"}


def test_extra_body_openrouter_dialect():
    cap = ThinkingCapability(mode="optional", dialect="openrouter", default_on=False,
                             default_effort="high")
    assert _extra_body(cap, True) == {"reasoning": {"effort": "high"}}
    assert _extra_body(cap, False) == {"reasoning": {"enabled": False}}


def test_extra_body_forced_without_efforts_emits_nothing():
    """forced 未配置等级时不发任何键，由端点按自身默认强度思考。"""
    cap = ThinkingCapability(mode="forced", dialect="openrouter")
    assert _extra_body(cap, True) == {}
    assert _extra_body(cap, False) == {}


def test_extra_body_forced_never_emits_disable_shape():
    """forced 在任何方言下都不产生"关"形态——ox-alpha 400 的根治断言。"""
    forced_or = ThinkingCapability(mode="forced", dialect="openrouter", default_effort="high")
    body = _extra_body(forced_or, False)
    assert body == {"reasoning": {"effort": "high"}}
    assert "enabled" not in body["reasoning"]

    forced_effort = ThinkingCapability(mode="forced", dialect="reasoning_effort",
                                       default_effort="medium")
    assert _extra_body(forced_effort, False) == {"reasoning_effort": "medium"}

    forced_ctk = ThinkingCapability(mode="forced", dialect="chat_template")
    assert _extra_body(forced_ctk, False)["enable_thinking"] is True


# ---------------------------------------------------------------------------
# LLM 构造与对外接口
# ---------------------------------------------------------------------------

def test_get_chat_data_llm_dispatches_by_registry(monkeypatch, tmp_path):
    captured = {}
    _set_registry(monkeypatch, tmp_path, [_ox_alpha(), _glm_free()])
    monkeypatch.setattr(llm_config, "get_llm", _fake_get_llm(captured))
    monkeypatch.delenv("CHAT_DATA_API_KEY", raising=False)

    # 默认取首项 ox-alpha（forced），关思考请求被忽略，仍发 effort
    llm_config.get_chat_data_llm(enable_thinking=False)
    assert captured["model"] == "stealth/ox-alpha"
    assert captured["temperature"] == 0.2
    assert captured["extra_body"] == {"reasoning": {"effort": "high"}}

    # 指定 glm（optional），关思考走 openrouter 关闭形态
    llm_config.get_chat_data_llm(model="z-ai/glm-5.2:free", enable_thinking=False)
    assert captured["model"] == "z-ai/glm-5.2:free"
    assert captured["extra_body"] == {"reasoning": {"enabled": False}}

    # 显式温度覆盖配置值
    llm_config.get_chat_data_llm(model="z-ai/glm-5.2:free", temperature=0)
    assert captured["temperature"] == 0


def test_chat_data_api_key_is_decrypted_from_existing_env_name(monkeypatch, tmp_path):
    captured = {}
    _set_registry(monkeypatch, tmp_path, [_ox_alpha()])
    monkeypatch.setenv("CHAT_DATA_API_KEY", "encrypted-api-key")
    monkeypatch.setattr(llm_config, "decrypt_password", lambda value: f"plain:{value}")
    monkeypatch.setattr(llm_config, "get_llm", _fake_get_llm(captured))

    llm_config.get_chat_data_llm()

    assert captured["api_key"] == "plain:encrypted-api-key"


def test_get_chat_data_temperature_reads_registry(monkeypatch, tmp_path):
    _set_registry(monkeypatch, tmp_path, [_ox_alpha()])
    assert llm_config.get_chat_data_temperature("stealth/ox-alpha") == 0.2


def test_max_output_tokens_prefers_registry_then_env_default(monkeypatch, tmp_path):
    _set_registry(monkeypatch, tmp_path, [_ox_alpha(max_output_tokens=8192), _glm_free()])
    monkeypatch.setenv("CHAT_DATA_OUTPUT_MAX_TOKENS", "4096")

    assert llm_config.get_chat_data_model_max_output_tokens("stealth/ox-alpha") == 8192
    assert llm_config.get_chat_data_model_max_output_tokens("z-ai/glm-5.2:free") == 4096

    monkeypatch.delenv("CHAT_DATA_OUTPUT_MAX_TOKENS", raising=False)
    assert (
        llm_config.get_chat_data_model_max_output_tokens("z-ai/glm-5.2:free")
        == llm_config.DEFAULT_CHAT_DATA_OUTPUT_MAX_TOKENS
    )


def test_get_chat_data_llm_uses_per_model_max_tokens(monkeypatch, tmp_path):
    captured = {}
    _set_registry(monkeypatch, tmp_path, [_ox_alpha(max_output_tokens=8192), _glm_free()])
    monkeypatch.setattr(llm_config, "get_llm", _fake_get_llm(captured))
    monkeypatch.delenv("CHAT_DATA_API_KEY", raising=False)
    monkeypatch.setenv("CHAT_DATA_OUTPUT_MAX_TOKENS", "4096")

    llm_config.get_chat_data_llm()
    assert captured["max_tokens"] == 8192

    llm_config.get_chat_data_llm(model="z-ai/glm-5.2:free")
    assert captured["max_tokens"] == 4096

    llm_config.get_chat_data_llm(model="stealth/ox-alpha", max_tokens=1024)
    assert captured["max_tokens"] == 1024


def test_get_chat_data_model_options_exposes_thinking(monkeypatch, tmp_path):
    _set_registry(monkeypatch, tmp_path, [_ox_alpha(), _glm_free()])

    result = llm_config.get_chat_data_model_options()

    assert result["default_model"] == "stealth/ox-alpha"
    first, second = result["models"]
    assert first["is_default"] is True
    assert first["thinking"] == {
        "mode": "forced", "efforts": ["low", "medium", "high"],
        "default_on": False, "default_effort": "high",
    }
    assert second["thinking"] == {
        "mode": "optional", "efforts": [], "default_on": False, "default_effort": None,
    }


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


def test_chat_data_runtime_config_reads_runtime_options(monkeypatch, tmp_path):
    _set_registry(monkeypatch, tmp_path, [_ox_alpha()])
    monkeypatch.setattr("apps.chat_data_app.services.runtime_config.CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "sandbox")
    monkeypatch.setattr("apps.chat_data_app.services.runtime_config.CHAT_DATA_DEFAULT_BASH_ENABLED", True)
    monkeypatch.setattr("apps.chat_data_app.services.runtime_config.CHAT_DATA_CAPABILITY_USER_OVERRIDABLE", True)

    result = get_chat_data_runtime_config()

    assert result["models"][0]["id"] == "stealth/ox-alpha"
    assert "default_model" not in result
    assert result["runtime_options"] == {
        "workspace_backend": "sandbox",
        "bash_enabled": True,
        "explore_subagent_enabled": False,
        "writer_subagent_enabled": False,
    }
    assert result["user_overridable"] is True
    assert result["context_window"] == 256000


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

    _set_registry(monkeypatch, tmp_path, [_ox_alpha()])
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
            model_id="stealth/ox-alpha",
        ),
    )

    assert created_models == ["stealth/ox-alpha"]
    assert created_temperatures == [None]
    middleware_names = [getattr(item, "name", "") for item in captured["middleware"]]
    assert "ToolCallNameNormalizerMiddleware" in middleware_names
    assert "ToolErrorMiddleware" in middleware_names
    assert captured["state_schema"] is ChatDataAgentState
