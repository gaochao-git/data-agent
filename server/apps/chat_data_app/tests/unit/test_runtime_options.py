"""agent.runtime_options 运行选项解析测试——覆盖后端回落、覆盖开关与布尔强制转换。"""

from __future__ import annotations

import pytest

from apps.chat_data_app.agent import runtime_options as ro
from apps.chat_data_app.services import runtime_config as rc


def test_defaults_are_local_without_bash():
    options = ro.AgentRuntimeOptions()
    assert options.workspace_backend == "local"
    assert options.bash_enabled is False
    assert options.explore_subagent_enabled is False
    assert options.plan_subagent_enabled is False
    assert options.general_purpose_subagent_enabled is False


def test_post_init_falls_back_to_local_for_invalid_backend():
    assert ro.AgentRuntimeOptions(workspace_backend="nonsense").workspace_backend == "local"
    assert ro.AgentRuntimeOptions(workspace_backend="sandbox").workspace_backend == "sandbox"


def test_get_default_runtime_options_reads_runtime_config(monkeypatch):
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "sandbox")
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_BASH_ENABLED", True)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED", True)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED", True)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_GENERAL_PURPOSE_SUBAGENT_ENABLED", True)

    options = ro.get_chat_data_default_runtime_options()
    assert options.workspace_backend == "sandbox"
    assert options.bash_enabled is True
    assert options.explore_subagent_enabled is True
    assert options.plan_subagent_enabled is True
    assert options.general_purpose_subagent_enabled is True


def test_resolve_applies_hot_subagent_defaults(monkeypatch):
    """async 调用方经 get_runtime_setting 解析的子智能体开关经参数透传为默认值。"""
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "local")
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_BASH_ENABLED", False)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED", False)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED", False)

    options = ro.resolve_agent_runtime_options(
        explore_subagent_enabled=True,
        plan_subagent_enabled=True,
        general_purpose_subagent_enabled=True,
    )
    assert options.explore_subagent_enabled is True
    assert options.plan_subagent_enabled is True
    assert options.general_purpose_subagent_enabled is True


@pytest.mark.asyncio
async def test_get_subagent_hot_defaults_maps_settings_to_kwargs(monkeypatch):
    """热值 helper 把三个子智能体开关的运行时配置映射为 resolve 的同名参数。"""
    from apps.chat_data_app.services import setting_service as ss

    async def fake_setting(key: str) -> bool:
        return key == "CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED"

    monkeypatch.setattr(ss, "get_runtime_setting", fake_setting)
    hot = await ro.get_subagent_hot_defaults()
    assert hot == {
        "explore_subagent_enabled": False,
        "plan_subagent_enabled": True,
        "general_purpose_subagent_enabled": False,
    }


def test_resolve_returns_deployment_defaults(monkeypatch):
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "sandbox")
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_BASH_ENABLED", True)

    options = ro.resolve_agent_runtime_options()
    assert (options.workspace_backend, options.bash_enabled) == ("sandbox", True)


def test_request_level_override_is_gone(monkeypatch):
    """部署能力（隔离后端/Bash）不再接受请求级覆盖：resolve 只回落后端配置与热值。"""
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "sandbox")
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_BASH_ENABLED", False)
    options = ro.resolve_agent_runtime_options(user_id="u1")
    assert options.workspace_backend == "sandbox"
    assert options.bash_enabled is False
