"""事件序列化所需的 MCP 工具展示名端口。"""

from __future__ import annotations

from collections.abc import Callable

_provider: Callable[[str], str] | None = None


def register_mcp_display_name_provider(provider: Callable[[str], str]) -> None:
    global _provider
    _provider = provider


def get_mcp_tool_display_name(qualified_name: str) -> str:
    if _provider is None:
        return ""
    return _provider(qualified_name)


__all__ = ["get_mcp_tool_display_name", "register_mcp_display_name_provider"]
