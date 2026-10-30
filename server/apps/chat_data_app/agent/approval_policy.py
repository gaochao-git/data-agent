"""工具审批策略的唯一解析入口。

系统工具与 MCP 工具当前仍从不同来源读取配置：

- 系统工具：``tools.registry.SYSTEM_TOOL_APPROVALS``；
- MCP 工具：插件 ``mcp_servers[].tools.<name>.approval``，由 manager 透出到工具 metadata。

本模块把两边的差异收敛为一张 ``{tool_name: ask|allow}`` 映射，供审批中间件、
无人值守过滤和子智能体审批剔除共同消费。未来改为数据库表时，只替换
``resolve_approval_policies`` 的内部实现，消费方无需改动。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .tools.catalog import (
    DEFAULT_TOOL_APPROVAL,
    VALID_TOOL_APPROVALS,
    build_tool_catalog,
    normalize_tool_approval,
)

__all__ = [
    "DEFAULT_TOOL_APPROVAL",
    "VALID_TOOL_APPROVALS",
    "ask_tool_names",
    "normalize_tool_approval",
    "resolve_approval_policies",
]


def resolve_approval_policies(tools: Iterable[object]) -> dict[str, str]:
    """解析一次完整工具集的审批策略，返回 ``{tool_name: allow|ask|deny}``。

    统一委托 ``ToolCatalog``：内置/中间件/动态 MCP 的来源、审批与风险都在目录里，
    审批中间件不再自己判断工具来源。
    """
    return build_tool_catalog(tools).approval_policies()


def ask_tool_names(policies: Mapping[str, str]) -> frozenset[str]:
    """从策略映射中取所有 ask 工具名。"""
    return frozenset(name for name, approval in policies.items() if approval == "ask")
