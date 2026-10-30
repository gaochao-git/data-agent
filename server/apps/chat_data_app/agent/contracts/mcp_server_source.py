"""MCP 配置加载所需的插件 server 声明来源。"""

from __future__ import annotations

from typing import Any, Protocol


class McpServerSourcePort(Protocol):
    """已启用插件声明的原始 MCP server 配置。"""

    def enabled_plugin_mcp_servers(self) -> list[dict[str, Any]]: ...


_source: McpServerSourcePort | None = None


def register_mcp_server_source(source: McpServerSourcePort) -> None:
    global _source
    _source = source


def get_mcp_server_source() -> McpServerSourcePort | None:
    return _source


__all__ = ["McpServerSourcePort", "get_mcp_server_source", "register_mcp_server_source"]
