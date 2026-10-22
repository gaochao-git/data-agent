"""MCP server 工具探测：管理页“连接一次并列出工具”的实现。"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from utils.logger import get_logger

from .manager import open_mcp_session

logger = get_logger(__name__)

DEFAULT_PROBE_TIMEOUT_SECONDS = 10.0
_ARGS_SECTION_PATTERN = re.compile(r"\n?Args:\s*\n?", re.IGNORECASE)


def _clean_tool_description(description: Any) -> str:
    """去掉 FastMCP 拼进 description 的 Args 段，参数改由 inputSchema 结构化展示。"""
    text = str(description or "").strip()
    return _ARGS_SECTION_PATTERN.split(text, maxsplit=1)[0].strip()


def _format_tool_parameters(input_schema: Any) -> list[dict[str, Any]]:
    """把工具 inputSchema 转成前端可渲染的参数摘要。"""
    schema = input_schema if isinstance(input_schema, dict) else {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required = set(schema.get("required") or []) if isinstance(schema.get("required"), list) else set()
    parameters: list[dict[str, Any]] = []
    for name, prop in properties.items():
        prop = prop if isinstance(prop, dict) else {}
        parameters.append(
            {
                "name": str(name),
                "type": str(prop.get("type") or "any"),
                "description": str(prop.get("description") or ""),
                "required": name in required,
            }
        )
    return parameters


async def probe_mcp_server_tools(
    config: Any,
    *,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """按配置连接 MCP server，返回 ``tools/list`` 的格式化摘要（不执行工具）。"""
    async with open_mcp_session(config) as client:
        result = await asyncio.wait_for(client.list_tools(), timeout=timeout_seconds)
    tools: list[dict[str, Any]] = []
    for tool in list(getattr(result, "tools", None) or []):
        tools.append(
            {
                "name": str(getattr(tool, "name", "") or ""),
                "description": _clean_tool_description(getattr(tool, "description", None)),
                "parameters": _format_tool_parameters(
                    getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
                ),
            }
        )
    return tools


__all__ = ["DEFAULT_PROBE_TIMEOUT_SECONDS", "probe_mcp_server_tools"]
