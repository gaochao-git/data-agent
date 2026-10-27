"""工具审批策略的唯一解析入口。

系统工具与 MCP 工具当前仍从不同来源读取配置：

- 系统工具：``tools.registry.SYSTEM_TOOL_APPROVALS``；
- MCP 工具：插件 ``mcp_servers[].tools.<name>.approval``，由 manager 透出到工具 metadata。

本模块把两边的差异收敛为一张 ``{tool_name: ask|allow}`` 映射，供审批中间件、
无人值守过滤和子智能体排除集共同消费。未来改为数据库表时，只替换
``resolve_approval_policies`` 的内部实现，消费方无需改动。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from utils.logger import get_logger

from .mcp.catalog import MCP_TOOL_CALL_TOOL_NAME, MCP_TOOL_SEARCH_TOOL_NAME
from .tools.registry import (
    MIDDLEWARE_INJECTED_APPROVAL_TOOLS,
    provided_tool_names,
    system_tool_approval,
)

logger = get_logger(__name__)

DEFAULT_TOOL_APPROVAL = "ask"
VALID_TOOL_APPROVALS = frozenset({"ask", "allow"})

# 控制工具本身不直接产生副作用：search 只读；call 的审批对象是目标工具。
_CONTROL_TOOL_APPROVALS = {
    MCP_TOOL_SEARCH_TOOL_NAME: "allow",
    MCP_TOOL_CALL_TOOL_NAME: "allow",
}


def normalize_tool_approval(value: Any, *, default: str = DEFAULT_TOOL_APPROVAL) -> str:
    """归一化 ask/allow；未知值 fail-closed 回落默认值。"""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALID_TOOL_APPROVALS else default


def _tool_metadata(tool: Any) -> Mapping[str, Any]:
    metadata = getattr(tool, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _is_mcp_tool(tool_name: str, metadata: Mapping[str, Any]) -> bool:
    return str(metadata.get("origin") or "") == "mcp" or tool_name.startswith("mcp_")


def resolve_approval_policies(tools: Iterable[Any]) -> dict[str, str]:
    """解析一次完整工具集的审批策略，返回 ``{tool_name: ask|allow}``。"""
    # 先按全部系统工具名铺底，保证未挂载/未传入的工具也有确定的 allow/ask；
    # middleware 动态注入的工具（如 write_todos）不在注册表，单独铺底纳入。
    policies: dict[str, str] = {
        name: normalize_tool_approval(system_tool_approval(name))
        for name in (*provided_tool_names(), *MIDDLEWARE_INJECTED_APPROVAL_TOOLS)
    }
    policies.update(_CONTROL_TOOL_APPROVALS)
    for tool in tools:
        tool_name = str(getattr(tool, "name", "") or "").strip()
        if not tool_name:
            continue
        metadata = _tool_metadata(tool)
        if _is_mcp_tool(tool_name, metadata):
            raw = metadata.get("approval") or DEFAULT_TOOL_APPROVAL
        else:
            raw = system_tool_approval(tool_name)
        approval = normalize_tool_approval(raw)
        if approval != str(raw or "").strip().lower():
            logger.warning("[approval_policy] 非法审批档位，按 ask 处理: tool=%s value=%r", tool_name, raw)
        policies[tool_name] = approval
    return policies


def ask_tool_names(policies: Mapping[str, str]) -> frozenset[str]:
    """从策略映射中取所有 ask 工具名。"""
    return frozenset(name for name, approval in policies.items() if approval == "ask")


__all__ = [
    "DEFAULT_TOOL_APPROVAL",
    "VALID_TOOL_APPROVALS",
    "ask_tool_names",
    "normalize_tool_approval",
    "resolve_approval_policies",
]
