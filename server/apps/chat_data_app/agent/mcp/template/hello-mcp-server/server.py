#!/usr/bin/env python3
"""Hello World MCP Server（MCP 2026-07-28，streamable_http）+ Bearer Token。

运行环境：Python 3.10+（官方 mcp 2.x 要求；本仓库使用 Python 3.12）。

鉴权实现拆在 auth.py；本文件只负责组装 Server、注册工具、启动。
    MCP_AUTH_TOKEN=change-me python server.py
"""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer
from mcp_auth import build_bearer_auth

auth_settings, token_verifier = build_bearer_auth()
mcp = MCPServer(
    "Hello MCP Server",
    version="0.1.0",
    auth=auth_settings,
    token_verifier=token_verifier,
)


@mcp.tool()
def hello(name: str = "world") -> str:
    """返回一句 Hello，用于验证工具发现与调用。"""
    return f"Hello, {name}!"


@mcp.tool()
def add(a: int, b: int) -> int:
    """返回两个整数之和，用于演示多个工具和结构化参数。"""
    return a + b


def main() -> None:
    token = os.getenv("MCP_AUTH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("MCP_AUTH_TOKEN is required; clients must send Authorization: Bearer <token>")
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8091"))
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path="/mcp",
    )


if __name__ == "__main__":
    main()
