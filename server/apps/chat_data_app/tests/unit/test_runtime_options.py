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


def test_post_init_falls_back_to_local_for_invalid_backend():
    assert ro.AgentRuntimeOptions(workspace_backend="nonsense").workspace_backend == "local"
    assert ro.AgentRuntimeOptions(workspace_backend="sandbox").workspace_backend == "sandbox"


def test_get_default_runtime_options_reads_runtime_config(monkeypatch):
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "sandbox")
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_BASH_ENABLED", True)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED", True)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED", True)

    options = ro.get_chat_data_default_runtime_options()
    assert options.workspace_backend == "sandbox"
    assert options.bash_enabled is True
    assert options.explore_subagent_enabled is True
    assert options.plan_subagent_enabled is True


def test_resolve_returns_base_without_override(monkeypatch):
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "sandbox")
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_BASH_ENABLED", True)

    options = ro.resolve_agent_runtime_options()
    assert (options.workspace_backend, options.bash_enabled) == ("sandbox", True)


def test_resolve_applies_override_when_overridable(monkeypatch):
    monkeypatch.setattr(ro, "_is_runtime_option_user_overridable", lambda: True)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "local")
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_BASH_ENABLED", False)

    options = ro.resolve_agent_runtime_options(
        user_id="u1", override={"workspace_backend": "sandbox", "bash_enabled": "yes"}
    )
    assert options.workspace_backend == "sandbox"
    assert options.bash_enabled is True


def test_resolve_applies_subagent_override_when_overridable(monkeypatch):
    monkeypatch.setattr(ro, "_is_runtime_option_user_overridable", lambda: True)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "local")
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_BASH_ENABLED", False)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED", False)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED", False)

    options = ro.resolve_agent_runtime_options(
        user_id="u1", override={"explore_subagent_enabled": True, "plan_subagent_enabled": "yes"}
    )
    assert options.explore_subagent_enabled is True
    assert options.plan_subagent_enabled is True

    options = ro.resolve_agent_runtime_options(user_id="u1", override={})
    assert options.explore_subagent_enabled is False
    assert options.plan_subagent_enabled is False


def test_resolve_ignores_override_when_not_overridable(monkeypatch):
    monkeypatch.setattr(ro, "_is_runtime_option_user_overridable", lambda: False)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "local")
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_BASH_ENABLED", False)

    options = ro.resolve_agent_runtime_options(override={"workspace_backend": "sandbox"})
    assert options.workspace_backend == "local"


def test_resolve_drops_invalid_backend_from_override(monkeypatch):
    monkeypatch.setattr(ro, "_is_runtime_option_user_overridable", lambda: True)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "sandbox")
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_BASH_ENABLED", False)

    options = ro.resolve_agent_runtime_options(override={"workspace_backend": "drop-me"})
    assert options.workspace_backend == "sandbox"


def test_resolve_treats_empty_backend_as_absent(monkeypatch):
    monkeypatch.setattr(ro, "_is_runtime_option_user_overridable", lambda: True)
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_WORKSPACE_BACKEND", "sandbox")
    monkeypatch.setattr(rc, "CHAT_DATA_DEFAULT_BASH_ENABLED", True)

    options = ro.resolve_agent_runtime_options(override={"workspace_backend": None})
    assert options.workspace_backend == "sandbox"
    assert options.bash_enabled is True


@pytest.mark.parametrize("value", [True, "true", "TRUE", " 1 ", "yes", "y", "on", 1, 2, 0.5])
def test_coerce_bool_truthy_values(value):
    assert ro._coerce_bool(value, False) is True


@pytest.mark.parametrize("value", [False, "false", "0", "no", "n", "off", 0, 0.0])
def test_coerce_bool_falsy_values(value):
    assert ro._coerce_bool(value, True) is False


@pytest.mark.parametrize("value", [None, "unknown", [], {}, object()])
def test_coerce_bool_falls_back_to_default(value):
    assert ro._coerce_bool(value, True) is True
    assert ro._coerce_bool(value, False) is False


def test_is_runtime_option_user_overridable_delegates(monkeypatch):
    monkeypatch.setattr(rc, "is_chat_data_runtime_option_user_overridable", lambda: True)
    assert ro._is_runtime_option_user_overridable() is True
