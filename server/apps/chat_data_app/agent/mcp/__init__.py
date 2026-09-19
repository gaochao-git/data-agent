"""MCP client 能力：宿主作为 MCP client 消费外部 MCP server 的工具。

- ``config.py``：server 配置解析（环境变量 ``CHAT_DATA_MCP_SERVERS`` + 插件声明）。
- ``manager.py``：连接管理、工具枚举缓存、调用与健康检查，及 LangChain 工具包装。

与 ``agent/plugins/`` 的关系：插件通过 ``plugin.json`` 声明 MCP server，
装配时与本模块的环境配置合并（环境配置优先）。
"""

from .config import (
    MCPServerConfig,
    MCPServerConfigError,
    MCP_SERVERS_ENV,
    load_mcp_server_configs,
    parse_mcp_server_config,
)
from .manager import (
    MCPToolInfo,
    MCPToolManager,
    build_langchain_tools,
    get_mcp_tool_manager,
    load_mcp_tools,
    open_mcp_session,
    reset_mcp_tool_manager,
    sanitize_mcp_tool_name,
)

__all__ = [
    "MCP_SERVERS_ENV",
    "MCPServerConfig",
    "MCPServerConfigError",
    "MCPToolInfo",
    "MCPToolManager",
    "build_langchain_tools",
    "get_mcp_tool_manager",
    "load_mcp_server_configs",
    "load_mcp_tools",
    "open_mcp_session",
    "parse_mcp_server_config",
    "reset_mcp_tool_manager",
    "sanitize_mcp_tool_name",
]
