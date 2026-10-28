"""Harness Port 的唯一装配入口。

各实现模块不再在 import 时注册全局 Port；由 composition root 显式调用本函数
完成绑定，避免隐式 import 副作用和注册顺序依赖。重复调用是幂等的。
"""

from __future__ import annotations

_configured = False


def configure_default_harness_ports() -> None:
    """把默认实现注册到 contracts 端口；由 agent 装配入口调用一次。"""
    global _configured
    if _configured:
        return

    from .contracts.mcp_display import register_mcp_display_name_provider
    from .contracts.mcp_server_source import register_mcp_server_source
    from .contracts.skill_source import register_skill_snapshot_source, register_skill_source
    from .contracts.workspace_preview import register_workspace_preview_source
    from .mcp.manager import get_mcp_tool_display_name
    from .plugins.loader import PluginMcpServerSource, PluginSkillSource
    from .skills.skill_service import SkillSnapshotSource
    from .workspace.skill_office_preview import WorkspacePreviewRenderer

    register_skill_source(PluginSkillSource())
    register_mcp_server_source(PluginMcpServerSource())
    register_skill_snapshot_source(SkillSnapshotSource())
    register_workspace_preview_source(WorkspacePreviewRenderer())
    register_mcp_display_name_provider(get_mcp_tool_display_name)
    _configured = True


__all__ = ["configure_default_harness_ports"]
