"""MCP server 工具探测：管理页“连接一次并列出工具”的实现。"""

from __future__ import annotations

import asyncio
from typing import Any

from utils.logger import get_logger

from .manager import open_mcp_session

logger = get_logger(__name__)

DEFAULT_PROBE_TIMEOUT_SECONDS = 10.0


async def probe_mcp_server_tools(
    config: Any,
    *,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> list[dict[str, str]]:
    """按配置连接 MCP server，返回 ``tools/list`` 的工具摘要（不执行工具）。"""
    async with open_mcp_session(config) as client:
        result = await asyncio.wait_for(client.list_tools(), timeout=timeout_seconds)
    tools: list[dict[str, str]] = []
    for tool in list(getattr(result, "tools", None) or []):
        tools.append(
            {
                "name": str(getattr(tool, "name", "") or ""),
                "description": str(getattr(tool, "description", None) or ""),
            }
        )
    return tools


__all__ = ["DEFAULT_PROBE_TIMEOUT_SECONDS", "probe_mcp_server_tools"]
