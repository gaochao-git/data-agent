"""``AgentAssemblySpec`` 声明式装配单测：工具名级筛选与规格校验护栏。

``chat_data`` 规格必须始终等于注册表全量（不含运行选项关闭的 bash），
防止规格化改造后行为漂移。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import pytest  # noqa: E402

from apps.chat_data_app.agent.data_agent import (  # noqa: E402
    AgentAssemblySpec,
    CHAT_DATA_AGENT_SPEC,
    DatasourceContext,
    IdentityContext,
    assemble_tools,
    validate_agent_assembly_spec,
)
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions  # noqa: E402
from apps.chat_data_app.services.agent_run.conversation_workspace import (  # noqa: E402
    ConversationWorkspaceRuntime,
)


def _runtime(tmp_path):
    return ConversationWorkspaceRuntime(
        workspace_root=str(tmp_path),
        user_id="u1",
        backend="local",
    )


def _tool_names(tmp_path, *, options=None, spec=None):
    tools = assemble_tools(
        options or AgentRuntimeOptions(workspace_backend="local"),
        _runtime(tmp_path),
        identity_context=IdentityContext(user_id="u1", operator_name="测试用户"),
        datasource_context=DatasourceContext(datasource_id="ds_a"),
        spec=spec,
    )
    return [tool.name for tool in tools]


def test_chat_data_spec_is_valid_and_assembles_full_registry(tmp_path):
    validate_agent_assembly_spec(CHAT_DATA_AGENT_SPEC)
    assert _tool_names(tmp_path, spec=CHAT_DATA_AGENT_SPEC) == _tool_names(tmp_path)


def test_spec_includes_filters_builtin_tools(tmp_path):
    spec = AgentAssemblySpec(
        agent_id="mini",
        agent_name="mini_agent",
        build_system_prompt=lambda _options, _root: "mini",
        tool_includes=frozenset({"file_read", "get_current_time"}),
    )
    names = _tool_names(tmp_path, spec=spec)
    assert names == ["file_read", "get_current_time"]


def test_spec_excludes_take_precedence(tmp_path):
    spec = AgentAssemblySpec(
        agent_id="ro",
        agent_name="ro_agent",
        build_system_prompt=lambda _options, _root: "ro",
        tool_excludes=frozenset({"file_write", "file_edit"}),
    )
    names = _tool_names(tmp_path, spec=spec)
    assert "file_write" not in names
    assert "file_edit" not in names
    assert "file_read" in names


def test_spec_validation_unknown_include_raises():
    spec = AgentAssemblySpec(
        agent_id="x",
        agent_name="x_agent",
        build_system_prompt=lambda _o, _r: "p",
        tool_includes=frozenset({"not_a_tool"}),
    )
    with pytest.raises(ValueError, match="tool_includes"):
        validate_agent_assembly_spec(spec)


def test_spec_validation_unknown_exclude_raises():
    spec = AgentAssemblySpec(
        agent_id="x",
        agent_name="x_agent",
        build_system_prompt=lambda _o, _r: "p",
        tool_excludes=frozenset({"not_a_tool"}),
    )
    with pytest.raises(ValueError, match="tool_excludes"):
        validate_agent_assembly_spec(spec)


def test_spec_validation_include_exclude_conflict_raises():
    spec = AgentAssemblySpec(
        agent_id="x",
        agent_name="x_agent",
        build_system_prompt=lambda _o, _r: "p",
        tool_includes=frozenset({"file_read", "file_write"}),
        tool_excludes=frozenset({"file_write"}),
    )
    with pytest.raises(ValueError, match="includes/excludes"):
        validate_agent_assembly_spec(spec)
