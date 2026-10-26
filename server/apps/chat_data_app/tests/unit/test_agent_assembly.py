"""``AgentAssemblySpec`` 声明式装配单测：工具名级筛选与规格校验护栏。

``chat_data`` 规格必须始终等于注册表全量（不含运行选项关闭的 bash），
防止规格化改造后行为漂移。
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import pytest  # noqa: E402

from apps.chat_data_app.agent.data_agent import (  # noqa: E402
    CHAT_DATA_AGENT_SPEC,
    AgentAssemblySpec,
    AgentRunControls,
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


def _tool_names(tmp_path, *, options=None, spec=None, run_controls=None):
    tools = assemble_tools(
        options or AgentRuntimeOptions(workspace_backend="local"),
        _runtime(tmp_path),
        identity_context=IdentityContext(user_id="u1", operator_name="测试用户"),
        datasource_context=DatasourceContext(datasource_id="ds_a"),
        spec=spec,
        run_controls=run_controls,
    )
    return [tool.name for tool in tools]


def test_chat_data_spec_is_valid_and_assembles_full_registry(tmp_path):
    validate_agent_assembly_spec(CHAT_DATA_AGENT_SPEC)
    assert _tool_names(tmp_path, spec=CHAT_DATA_AGENT_SPEC) == _tool_names(tmp_path)


def test_unattended_run_excludes_user_interaction_tools(tmp_path):
    """无人值守（定时轮）装配剔除 ask_user_question，其余工具与正常装配一致。"""
    attended = _tool_names(tmp_path)
    unattended = _tool_names(tmp_path, run_controls=AgentRunControls(disable_user_interaction=True))
    assert "ask_user_question" in attended
    assert "ask_user_question" not in unattended
    assert [name for name in attended if name != "ask_user_question"] == unattended


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


# ============ MCP 两级加载规格 ============


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"mcp_tool_search": "always"}, "mcp_tool_search"),
        ({"mcp_tool_search_threshold": 0.0}, "mcp_tool_search_threshold"),
        ({"mcp_tool_search_threshold": 1.5}, "mcp_tool_search_threshold"),
        ({"mcp_server_ids": frozenset({"ssh", " "})}, "mcp_server_ids"),
    ],
)
def test_spec_validation_rejects_invalid_mcp_options(overrides, match):
    spec = AgentAssemblySpec(
        agent_id="x",
        agent_name="x_agent",
        build_system_prompt=lambda _o, _r: "p",
        **overrides,
    )
    with pytest.raises(ValueError, match=match):
        validate_agent_assembly_spec(spec)


def test_spec_mcp_server_ids_pass_through_to_load(monkeypatch):
    """server 白名单在 MCPToolInfo 层过滤：白名单外 server 的工具不返回。"""
    from apps.chat_data_app.agent.data_agent import _load_spec_mcp_tools
    from apps.chat_data_app.agent.mcp import manager as mcp_manager

    def fake_load(server_ids=None):
        infos = [
            SimpleNamespace(server_id="ssh", name="a", qualified_name="mcp_ssh_a", description="d", input_schema={}),
            SimpleNamespace(server_id="db", name="b", qualified_name="mcp_db_b", description="d", input_schema={}),
        ]
        allowed = set(server_ids) if server_ids is not None else None
        return [info.qualified_name for info in infos if allowed is None or info.server_id in allowed]

    monkeypatch.setattr(mcp_manager, "load_mcp_tools", fake_load)
    spec = AgentAssemblySpec(
        agent_id="x",
        agent_name="x_agent",
        build_system_prompt=lambda _o, _r: "p",
        mcp_server_ids=frozenset({"ssh"}),
    )
    tools = _load_spec_mcp_tools(spec)
    assert tools == ["mcp_ssh_a"]
    # None 白名单 = 全部允许
    spec_all = AgentAssemblySpec(
        agent_id="x",
        agent_name="x_agent",
        build_system_prompt=lambda _o, _r: "p",
    )
    assert len(_load_spec_mcp_tools(spec_all)) == 2


def test_resolve_search_service_off_and_auto_below_threshold():
    from apps.chat_data_app.agent.data_agent import _resolve_mcp_tool_search_service

    spec = AgentAssemblySpec(
        agent_id="x",
        agent_name="x_agent",
        build_system_prompt=lambda _o, _r: "p",
        mcp_tool_search="off",
    )
    assert _resolve_mcp_tool_search_service(spec, tool_tokens=10**9, model_id=None) is None
    # auto：工具 token 远低于窗口 10% → 全量挂载
    spec_auto = AgentAssemblySpec(
        agent_id="x",
        agent_name="x_agent",
        build_system_prompt=lambda _o, _r: "p",
    )
    assert _resolve_mcp_tool_search_service(spec_auto, tool_tokens=1, model_id=None) is None


def test_resolve_search_service_auto_above_threshold_creates_service():
    from apps.chat_data_app.agent.context.context_budget import get_model_context_budget
    from apps.chat_data_app.agent.data_agent import _resolve_mcp_tool_search_service
    from apps.chat_data_app.agent.mcp.catalog import MCPToolSearchService

    spec_auto = AgentAssemblySpec(
        agent_id="x",
        agent_name="x_agent",
        build_system_prompt=lambda _o, _r: "p",
    )
    budget = get_model_context_budget(None)
    service = _resolve_mcp_tool_search_service(
        spec_auto,
        tool_tokens=budget.context_window + 1,
        model_id=None,
    )
    assert isinstance(service, MCPToolSearchService)


def test_resolve_search_service_forced_always_enabled():
    from apps.chat_data_app.agent.data_agent import _resolve_mcp_tool_search_service
    from apps.chat_data_app.agent.mcp.catalog import MCPToolSearchService

    spec_forced = AgentAssemblySpec(
        agent_id="x",
        agent_name="x_agent",
        build_system_prompt=lambda _o, _r: "p",
        mcp_tool_search="forced",
    )
    assert isinstance(
        _resolve_mcp_tool_search_service(spec_forced, tool_tokens=1, model_id=None),
        MCPToolSearchService,
    )


def test_env_mcp_tool_search_mode(monkeypatch):
    """chat_data 规格从环境变量读取检索模式：缺省 auto、合法值透传、非法值告警降级 auto。"""
    from apps.chat_data_app.agent.data_agent import (
        MCP_TOOL_SEARCH_ENV,
        _env_mcp_tool_search_mode,
    )

    monkeypatch.delenv(MCP_TOOL_SEARCH_ENV, raising=False)
    assert _env_mcp_tool_search_mode() == "auto"

    monkeypatch.setenv(MCP_TOOL_SEARCH_ENV, "forced")
    assert _env_mcp_tool_search_mode() == "forced"

    monkeypatch.setenv(MCP_TOOL_SEARCH_ENV, "  off  ")
    assert _env_mcp_tool_search_mode() == "off"

    monkeypatch.setenv(MCP_TOOL_SEARCH_ENV, "always")
    assert _env_mcp_tool_search_mode() == "auto"


def test_chat_data_agent_spec_applies_env_mode(monkeypatch):
    """环境变量应在模块导入时固化为 CHAT_DATA_AGENT_SPEC 的 mcp_tool_search。"""
    import importlib

    from apps.chat_data_app.agent import data_agent

    monkeypatch.setenv(data_agent.MCP_TOOL_SEARCH_ENV, "forced")
    reloaded = importlib.reload(data_agent)
    try:
        assert reloaded.CHAT_DATA_AGENT_SPEC.mcp_tool_search == "forced"
    finally:
        monkeypatch.delenv(data_agent.MCP_TOOL_SEARCH_ENV, raising=False)
        importlib.reload(data_agent)


def test_approval_middleware_mounted_for_interactive_runs_only():
    """交互会话始终挂载审批中间件；无人值守不挂载（ask 工具已 fail-closed 剔除）。"""
    from apps.chat_data_app.agent.data_agent import _build_tool_approval_middleware

    assert _build_tool_approval_middleware(None) is None
    assert _build_tool_approval_middleware(AgentRunControls()) is not None
    assert _build_tool_approval_middleware(AgentRunControls(disable_user_interaction=True)) is None


def test_subagents_keep_system_tools_when_they_are_allow(monkeypatch):
    """系统工具统一 allow 后，子智能体保留文件写/删除/移动能力。"""
    from types import SimpleNamespace

    from apps.chat_data_app.agent import data_agent as data_agent_module

    captured: dict = {}

    class _Recorder:
        def __init__(self, specs, **_kwargs):
            captured["specs"] = specs

    monkeypatch.setattr(data_agent_module, "SubagentMiddleware", _Recorder)

    tools = [SimpleNamespace(name=name) for name in ("file_read", "file_write", "file_delete", "file_move", "bash_run")]
    options = AgentRuntimeOptions(general_purpose_subagent_enabled=True)
    data_agent_module.build_subagent_middleware(options, tools, model=object())

    names = {tool.name for spec in captured["specs"] for tool in spec.tools}
    assert {"file_read", "file_delete", "file_move"} <= names
    gp = next(spec for spec in captured["specs"] if spec.name == "general-purpose")
    assert "删除与移动的能力边界" not in gp.system_prompt


def test_unattended_excludes_every_user_interaction_tool(tmp_path):
    """无人值守必须不挂载任何 requires_user_interaction 工具（注册表全量校验）。"""
    from apps.chat_data_app.agent.tools.registry import TOOL_SPECS

    interactive = {name for spec in TOOL_SPECS if spec.requires_user_interaction for name in spec.provides}
    attended = set(_tool_names(tmp_path))
    unattended = set(_tool_names(tmp_path, run_controls=AgentRunControls(disable_user_interaction=True)))
    assert interactive
    assert interactive <= attended
    assert interactive.isdisjoint(unattended)


def test_unattended_filter_excludes_ask_tools_and_builtin_approval():
    from types import SimpleNamespace

    from apps.chat_data_app.agent.data_agent import _filter_tools_for_unattended

    builtin = [SimpleNamespace(name="file_read"), SimpleNamespace(name="file_delete")]
    mcp = [
        SimpleNamespace(name="mcp_ask_tool", metadata={"approval": "ask"}),
        SimpleNamespace(name="mcp_allow_tool", metadata={"approval": "allow"}),
        SimpleNamespace(name="mcp_default_tool", metadata={}),
    ]
    kept_builtin, kept_mcp, excluded = _filter_tools_for_unattended(builtin, mcp)
    assert [tool.name for tool in kept_builtin] == ["file_read", "file_delete"]
    assert [tool.name for tool in kept_mcp] == ["mcp_allow_tool"]
    assert excluded == frozenset({"mcp_ask_tool", "mcp_default_tool"})


def test_write_todos_middleware_uses_compact_chinese_prompt():
    """write_todos 是常驻注入工具：描述与系统提示必须是中文短文本，避免官方长英文占上下文。"""
    from langchain.agents.middleware import TodoListMiddleware

    from apps.chat_data_app.agent.agent_runtime import build_standard_agent_middleware

    middleware = build_standard_agent_middleware([])
    todo = next(item for item in middleware if isinstance(item, TodoListMiddleware))
    description = str(todo.tools[0].description or "")
    system_prompt = str(todo.system_prompt or "")

    assert len(description) <= 400
    assert "任务清单" in description
    assert "in_progress" in description
    assert "completed" in description
    assert len(system_prompt) <= 400
    assert "多步任务" in system_prompt
