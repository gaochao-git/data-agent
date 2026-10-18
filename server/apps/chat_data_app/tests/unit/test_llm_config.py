import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.services import llm_config
from apps.chat_data_app.services.llm_config import EffortLevel, ThinkingCapability
from apps.chat_data_app.services.runtime_config import get_chat_data_runtime_config

# ---------------------------------------------------------------------------
# 测试辅助：把模型清单写入临时注册表文件
# ---------------------------------------------------------------------------


def E(*levels: str) -> tuple:
    """构造 EffortLevel 元组（value 缺省=档位同名）。"""
    return tuple(EffortLevel(level=l) for l in levels)


def _set_registry(monkeypatch, tmp_path, models: list[dict]) -> str:
    path = tmp_path / "models.json"
    path.write_text(json.dumps(models, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CHAT_DATA_MODELS_FILE", str(path))
    monkeypatch.delenv("CHAT_DATA_MODEL", raising=False)
    # 连接信息 fail-fast 要求模型行带 key：统一桩掉解密，避免真实 SM4 依赖
    monkeypatch.setattr(llm_config, "decrypt_password", lambda value: "test-plain-key")
    return str(path)


def _ox_alpha(**overrides) -> dict:
    entry = {
        "id": "stealth/ox-alpha",
        "temperature": 0.2,
        "context_window": 256000,
        "vlm": True,
        "base_url": "http://127.0.0.1:9/v1",
        "api_key_cipher": "dGVzdC1pdi12YWx1ZQ==:dGVzdC1jaXBoZXJ0ZXN0",
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
        "base_url": "http://127.0.0.1:9/v1",
        "api_key_cipher": "dGVzdC1pdi12YWx1ZQ==:dGVzdC1jaXBoZXJ0ZXN0",
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
            _ox_alpha(
                thinking={"mode": "optional", "dialect": "chat_template", "default_on": True, "efforts": ["low"]}
            ),
            "chat_template 方言",
        ),
        (
            _ox_alpha(
                thinking={
                    "mode": "optional",
                    "dialect": "openrouter",
                    "default_on": True,
                    "efforts": ["low"],
                    "default_effort": "high",
                }
            ),
            "default_effort",
        ),
        (_ox_alpha(thinking={"mode": "none", "dialect": "openrouter"}), "mode=none"),
    ],
)
def test_registry_validation_fail_fast(monkeypatch, tmp_path, entry, match):
    _set_registry(monkeypatch, tmp_path, [entry])
    with pytest.raises(ValueError, match=match):
        llm_config.load_chat_data_model_configs()


def test_registry_derives_default_on_from_default_effort(monkeypatch, tmp_path):
    """default_on 不再可配置：由默认档位推导（普通档=默认开；off/none/未勾=默认关）；显式 default_on 被忽略。"""
    _set_registry(
        monkeypatch,
        tmp_path,
        [
            _ox_alpha(
                thinking={
                    "mode": "optional",
                    "dialect": "reasoning_effort",
                    "efforts": ["off", "low"],
                    "default_on": False,  # 废弃字段：显式配置被忽略
                    "default_effort": "low",
                }
            ),
            _ox_alpha(
                id="stealth/ox-beta",
                thinking={
                    "mode": "optional",
                    "dialect": "reasoning_effort",
                    "efforts": ["off", "low"],
                    "default_effort": "off",
                },
            ),
            _ox_alpha(
                id="stealth/ox-gamma",
                thinking={"mode": "optional", "dialect": "reasoning_effort"},
            ),
        ],
    )
    caps = [config["thinking"] for config in llm_config.get_chat_data_model_configs()]
    assert caps[0].default_on is True  # 默认档位=普通档 low
    assert caps[1].default_on is False  # 默认档位=关哨兵 off
    assert caps[2].default_on is False  # 未勾默认档位


def test_registry_tolerates_empty_efforts_for_chat_template(monkeypatch, tmp_path):
    """空 efforts 数组等同缺省（管理端表单会发空数组），不得触发方言冲突。"""
    _set_registry(
        monkeypatch,
        tmp_path,
        [_ox_alpha(thinking={"mode": "optional", "dialect": "chat_template", "default_on": True, "efforts": []})],
    )
    config = llm_config.get_chat_data_model_configs()[0]
    assert config["thinking"].efforts == ()


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
        mode="forced",
        dialect="openrouter",
        efforts=E("low", "medium", "high"),
        default_effort="high",
    )
    assert configs[1]["thinking"] == ThinkingCapability(
        mode="optional",
        dialect="reasoning_effort",
        efforts=E("low", "high"),
        default_on=True,  # 由默认档位 high 推导（显式 default_on=False 已废弃被忽略）
        default_effort="high",
    )
    assert configs[2]["thinking"] == ThinkingCapability(mode="none")
    assert configs[0]["max_output_tokens"] is None
    assert llm_config.get_default_chat_data_model() == "stealth/ox-alpha"


def test_unknown_model_id_raises_instead_of_fallback(monkeypatch, tmp_path):
    _set_registry(monkeypatch, tmp_path, [_ox_alpha()])
    with pytest.raises(ValueError, match="不在"):
        llm_config.get_chat_data_model_config("nope-model")


def test_normalize_model_id_downgrades_stale_model(monkeypatch, tmp_path):
    """历史会话携带已删/停用 model_id 时降级默认模型；有效 id 与 None 原样透传。"""
    from apps.chat_data_app.agent import data_agent

    _set_registry(monkeypatch, tmp_path, [_ox_alpha()])
    assert data_agent._normalize_model_id("stealth/ox-alpha") == "stealth/ox-alpha"
    assert data_agent._normalize_model_id("nope-model") == "stealth/ox-alpha"
    assert data_agent._normalize_model_id(None) is None


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
        (
            ThinkingCapability(mode="optional", dialect="openrouter", default_on=True, default_effort="medium"),
            True,
            ("on", "medium"),
        ),
        (
            ThinkingCapability(mode="optional", dialect="openrouter", default_on=True, default_effort="medium"),
            False,
            ("off", None),
        ),
        (
            ThinkingCapability(mode="optional", dialect="openrouter", default_on=True, default_effort="medium"),
            None,
            ("on", "medium"),
        ),
        (
            ThinkingCapability(mode="optional", dialect="openrouter", default_on=False, default_effort="high"),
            None,
            ("off", None),
        ),
        # forced：忽略关闭请求，永不产生"关"形态
        (ThinkingCapability(mode="forced", dialect="openrouter", default_effort="high"), False, ("on", "high")),
        (ThinkingCapability(mode="forced", dialect="openrouter", default_effort="high"), True, ("on", "high")),
        (ThinkingCapability(mode="forced", dialect="openrouter"), None, ("on", None)),
    ],
)
def test_resolve_thinking_matrix(capability, user_enabled, expected):
    assert llm_config.resolve_thinking(capability, user_enabled) == expected


@pytest.mark.parametrize(
    "capability,user_enabled,user_effort,expected",
    [
        # 用户档位在 efforts 内：生效
        (
            ThinkingCapability(
                mode="optional",
                dialect="openrouter",
                default_on=True,
                efforts=E("low", "medium", "high"),
                default_effort="high",
            ),
            True,
            "low",
            ("on", "low"),
        ),
        # forced 也接受用户档位
        (
            ThinkingCapability(
                mode="forced", dialect="openrouter", efforts=E("low", "medium", "high"), default_effort="high"
            ),
            None,
            "medium",
            ("on", "medium"),
        ),
        # 非法档位回退 default_effort
        (
            ThinkingCapability(
                mode="optional", dialect="openrouter", default_on=True, efforts=E("low", "high"), default_effort="high"
            ),
            True,
            "ultra",
            ("on", "high"),
        ),
        # 模型未声明 efforts：用户档位被忽略
        (ThinkingCapability(mode="forced", dialect="openrouter", default_effort=None), None, "low", ("on", None)),
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
    cap = ThinkingCapability(
        mode="optional", dialect="reasoning_effort", efforts=E("low", "high"), default_on=False, default_effort="high"
    )
    assert _extra_body(cap, True) == {"reasoning_effort": "high"}
    assert _extra_body(cap, False) == {"reasoning_effort": "none"}


def test_extra_body_openrouter_dialect():
    cap = ThinkingCapability(mode="optional", dialect="openrouter", default_on=False, default_effort="high")
    assert _extra_body(cap, True) == {"reasoning": {"effort": "high"}}
    assert _extra_body(cap, False) == {"reasoning": {"enabled": False}}


def test_extra_body_forced_without_efforts_emits_nothing():
    """forced 未配置等级时不发任何键，由端点按自身默认强度思考。"""
    cap = ThinkingCapability(mode="forced", dialect="openrouter")
    assert _extra_body(cap, True) == {}
    assert _extra_body(cap, False) == {}


def test_extra_body_optional_reasoning_effort_without_efforts_emits_nothing():
    """reasoning_effort 方言未声明档位时：开启也不发思考键。

    配置是唯一事实源：需要思考的模型必须在注册表（模型管理面板维护）
    声明 efforts/default_effort，代码不做兼容兜底。
    历史教训：deepseek-v4-flash 曾缺档位声明，定时任务与手动聊天的
    「开启思考」被静默吞掉。
    """
    cap = ThinkingCapability(mode="optional", dialect="reasoning_effort", default_on=False)
    assert _extra_body(cap, True) == {}
    # 未显式开启时跟随 default_on=False 裁决为关，发关闭形态键（正常行为）
    assert _extra_body(cap, None) == {"reasoning_effort": "none"}
    assert _extra_body(cap, False) == {"reasoning_effort": "none"}

    # 声明了档位与默认档后，显式开启正确落到默认档
    declared = ThinkingCapability(
        mode="optional",
        dialect="reasoning_effort",
        efforts=E("low", "medium", "high"),
        default_effort="low",
        default_on=False,
    )
    assert _extra_body(declared, True) == {"reasoning_effort": "low"}


def test_extra_body_forced_never_emits_disable_shape():
    """forced 在任何方言下都不产生"关"形态——ox-alpha 400 的根治断言。"""
    forced_or = ThinkingCapability(mode="forced", dialect="openrouter", default_effort="high")
    body = _extra_body(forced_or, False)
    assert body == {"reasoning": {"effort": "high"}}
    assert "enabled" not in body["reasoning"]

    forced_effort = ThinkingCapability(mode="forced", dialect="reasoning_effort", default_effort="medium")
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


def test_chat_data_connection_info_resolves_from_model_row_only(monkeypatch):
    """连接信息只以模型行为准：行内密文解密使用，行内为空返回 None（无环境变量回退）。"""
    monkeypatch.setattr(llm_config, "decrypt_password", lambda value: f"plain:{value}")
    assert llm_config._resolve_chat_data_api_key({"api_key_cipher": "cipher-x"}) == "plain:cipher-x"
    assert llm_config._resolve_chat_data_api_key({"api_key_cipher": ""}) is None
    assert llm_config._resolve_chat_data_base_url({"base_url": "http://127.0.0.1:8100/v1"}) == "http://127.0.0.1:8100/v1"
    assert llm_config._resolve_chat_data_base_url({"base_url": ""}) is None


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
        "mode": "forced",
        "efforts": ["low", "medium", "high"],
        "default_on": False,
        "default_effort": "high",
    }
    assert second["thinking"] == {
        "mode": "optional",
        "efforts": [],
        "default_on": False,
        "default_effort": None,
    }


def test_context_compression_summary_output_max_tokens_reads_own_environment_variable(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS", "1536")
    monkeypatch.setenv("CHAT_DATA_OUTPUT_MAX_TOKENS", "8192")

    assert llm_config.get_context_compression_summary_output_max_tokens() == 1536


@pytest.mark.parametrize("value", ["", "0", "-1", "invalid"])
def test_context_compression_summary_output_max_tokens_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS", value)

    # 未显式配置（或缺省非法）返回 None，由调用侧按模型窗口动态计算摘要输出预算
    assert llm_config.get_context_compression_summary_output_max_tokens() is None


def test_chat_data_runtime_config_reads_runtime_options(monkeypatch, tmp_path):
    registry_path = _set_registry(monkeypatch, tmp_path, [_ox_alpha()])
    monkeypatch.setattr("apps.chat_data_app.services.runtime_config.CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "sandbox")
    monkeypatch.setattr("apps.chat_data_app.services.runtime_config.CHAT_DATA_DEFAULT_BASH_ENABLED", True)
    monkeypatch.setattr("apps.chat_data_app.services.runtime_config.CHAT_DATA_CAPABILITY_USER_OVERRIDABLE", True)

    # 服务路径冷缓存会拒绝回落文件（DB 唯一真值）：先模拟启动预热成功后的缓存落位
    monkeypatch.setattr(llm_config, "_registry_cache", llm_config.load_chat_data_model_configs())
    monkeypatch.setattr(llm_config, "_registry_cache_loaded_at", time.monotonic())
    monkeypatch.setattr(llm_config, "_registry_cache_origin", registry_path)
    monkeypatch.setattr(llm_config, "_registry_refresh_task", None)

    import asyncio

    result = asyncio.run(get_chat_data_runtime_config())

    assert result["models"][0]["id"] == "stealth/ox-alpha"
    assert "default_model" not in result
    assert result["runtime_options"] == {
        "workspace_backend": "sandbox",
        "bash_enabled": True,
        "explore_subagent_enabled": False,
        "plan_subagent_enabled": False,
        "general_purpose_subagent_enabled": False,
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


# ---------------------------------------------------------------------------
# 思考档位可配置化：新对象数组 / 废弃 off_value 兼容 / 往返一致
# ---------------------------------------------------------------------------


def test_registry_parses_new_effort_objects_with_wire_values(monkeypatch, tmp_path):
    """新形状 [{level, value}]：value 缺省=档位同名。"""
    _set_registry(
        monkeypatch,
        tmp_path,
        [
            _ox_alpha(
                thinking={
                    "mode": "optional",
                    "dialect": "reasoning_effort",
                    "default_on": False,
                    "efforts": [{"level": "low", "value": "minimal"}, {"level": "high"}],
                    "default_effort": "low",
                }
            )
        ],
    )
    cap = llm_config.get_chat_data_model_configs()[0]["thinking"]
    assert cap.efforts == (llm_config.EffortLevel(level="low", value="minimal"), llm_config.EffortLevel(level="high"))


@pytest.mark.parametrize(
    "efforts,match",
    [
        ([{"level": ""}], "档位名不能为空"),
        ([{"level": "bad name!"}], "档位名"),
        ([{"level": "l" * 40}], "档位名"),
        ([{"level": "low"}, {"level": "low"}], "档位名重复"),
        ([{"level": f"l{i}"} for i in range(11)], "超过上限"),
        ([{"level": "low", "value": {"nested": 1}}], "参数值应为字符串"),
        ([{"level": "low", "value": ["x"]}], "参数值应为字符串"),
        ([{"level": "low", "value": "x" * 65}], "字符串参数值超长"),
        ([{"level": "low", "value": float("nan")}], "NaN"),
        ([{"level": "low", "extra": 1}], "未知字段"),
        ([{"value": "minimal"}], "档位 level 应为字符串"),
        ([42], "档位应为字符串或"),
    ],
)
def test_registry_rejects_illegal_effort_shapes(monkeypatch, tmp_path, efforts, match):
    first_level = efforts[0].get("level") if isinstance(efforts[0], dict) else efforts[0]
    entry = _ox_alpha(
        thinking={
            "mode": "optional",
            "dialect": "reasoning_effort",
            "default_on": False,
            "efforts": efforts,
            "default_effort": first_level if isinstance(first_level, str) else "low",
        }
    )
    _set_registry(monkeypatch, tmp_path, [entry])
    with pytest.raises(ValueError, match=match):
        llm_config.load_chat_data_model_configs()


def test_registry_tolerates_legacy_off_value(monkeypatch, tmp_path):
    """off_value 已废弃：存量配置携带时容忍并静默丢弃（关形态走方言默认兜底），不报未知字段。"""
    base = {"mode": "optional", "dialect": "reasoning_effort", "default_on": False, "off_value": "low"}
    _set_registry(monkeypatch, tmp_path, [_ox_alpha(thinking=base)])
    llm_config.reset_registry_cache()
    cap = llm_config.get_chat_data_model_configs()[0]["thinking"]
    assert not hasattr(cap, "off_value")
    # 落库形状不再回写 off_value
    assert "off_value" not in llm_config._thinking_cap_to_json(cap)


def test_extra_body_custom_wire_values_and_off_fallback_matrix():
    """序列化矩阵：自定义 value / 关形态按方言固定兜底。"""
    # reasoning_effort：low 发 minimal
    cap = ThinkingCapability(
        mode="optional",
        dialect="reasoning_effort",
        efforts=(llm_config.EffortLevel(level="low", value="minimal"), llm_config.EffortLevel(level="high")),
        default_on=False,
        default_effort="low",
    )
    assert llm_config._get_chat_data_extra_body(cap, True, "low") == {"reasoning_effort": "minimal"}
    assert llm_config._get_chat_data_extra_body(cap, True, "high") == {"reasoning_effort": "high"}
    assert llm_config._get_chat_data_extra_body(cap, False) == {"reasoning_effort": "none"}

    cap_default = ThinkingCapability(
        mode="optional",
        dialect="reasoning_effort",
        efforts=(llm_config.EffortLevel(level="low", value=2),),
        default_on=False,
        default_effort="low",
    )
    assert llm_config._get_chat_data_extra_body(cap_default, True, "low") == {"reasoning_effort": 2}
    assert llm_config._get_chat_data_extra_body(cap_default, False) == {"reasoning_effort": "none"}

    cap_or = ThinkingCapability(
        mode="optional",
        dialect="openrouter",
        efforts=(llm_config.EffortLevel(level="xhigh"),),
        default_on=False,
        default_effort="xhigh",
    )
    assert llm_config._get_chat_data_extra_body(cap_or, True) == {"reasoning": {"effort": "xhigh"}}
    assert llm_config._get_chat_data_extra_body(cap_or, False) == {"reasoning": {"enabled": False}}


def test_extra_body_off_sentinel_levels_serialize_as_off():
    """关哨兵档位（off/none）选中时按「关」形态序列化，不把字面量当 on wire 值发出。

    背景：DeepSeek 新 API 只接受 none/minimal/.../max，reasoning_effort: "off" 直接 422。
    """
    # reasoning_effort：哨兵档 → 关形态（固定兜底 "none"）
    cap = ThinkingCapability(
        mode="optional",
        dialect="reasoning_effort",
        efforts=E("off", "low", "none"),
        default_on=False,
        default_effort="low",
    )
    assert llm_config._get_chat_data_extra_body(cap, True, "off") == {"reasoning_effort": "none"}
    assert llm_config._get_chat_data_extra_body(cap, True, "none") == {"reasoning_effort": "none"}
    assert llm_config._get_chat_data_extra_body(cap, True, "low") == {"reasoning_effort": "low"}

    # chat_template：哨兵档 → 布尔关
    cap_ct = ThinkingCapability(
        mode="optional",
        dialect="chat_template",
        efforts=E("off", "high"),
        default_on=False,
        default_effort="high",
    )
    assert llm_config._get_chat_data_extra_body(cap_ct, True, "off") == {
        "enable_thinking": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    # openrouter：哨兵档 → {"enabled": false}
    cap_or = ThinkingCapability(
        mode="optional",
        dialect="openrouter",
        efforts=E("none", "low"),
        default_on=False,
        default_effort="low",
    )
    assert llm_config._get_chat_data_extra_body(cap_or, True, "none") == {"reasoning": {"enabled": False}}


def test_effort_suggestions_cover_deepseek_variants():
    """档位候选覆盖 DeepSeek 新 API 档位族（含关哨兵 off）。"""
    assert set(llm_config.EFFORT_SUGGESTIONS) >= {"off", "minimal", "low", "medium", "high", "xhigh", "ultra", "max"}


def test_thinking_roundtrip_preserves_new_shape():
    """_thinking_cap_to_json ↔ _parse_thinking 往返一致（含 value）。"""
    cap = ThinkingCapability(
        mode="optional",
        dialect="reasoning_effort",
        efforts=(llm_config.EffortLevel(level="low", value="minimal"), llm_config.EffortLevel(level="high")),
        default_on=True,
        default_effort="high",
    )
    packed = llm_config._thinking_cap_to_json(cap)
    assert packed["efforts"] == [{"level": "low", "value": "minimal"}, {"level": "high"}]
    reparsed = llm_config._parse_thinking(packed, path="t", model_id="m")
    assert reparsed == cap


def test_model_options_expose_effort_names_only(monkeypatch, tmp_path):
    """/config 只下发档位名，不下发 wire 值（前端不与协议耦合）。"""
    _set_registry(
        monkeypatch,
        tmp_path,
        [
            _ox_alpha(
                thinking={
                    "mode": "optional",
                    "dialect": "reasoning_effort",
                    "default_on": False,
                    "efforts": [{"level": "low", "value": "minimal"}, {"level": "xhigh"}],
                    "default_effort": "low",
                }
            )
        ],
    )
    thinking = llm_config.get_chat_data_model_options()["models"][0]["thinking"]
    assert thinking["efforts"] == ["low", "xhigh"]
