"""MCP client 能力：宿主作为 MCP client 消费外部 MCP server 的工具。

- ``config.py``：server 配置解析（当前仅插件 ``plugin.json`` 声明；独立挂载后续提供）。
- ``manager.py``：连接管理、工具枚举缓存、调用与健康检查，及 LangChain 工具包装。
- ``catalog.py``：工具检索目录（BM25）与 ``mcp_tool_search`` 按需检索工具，
  两级加载（目录 + 按需检索）的第二级，见 ``agent/middleware/mcp_tool_discovery.py``。

与 ``agent/plugins/`` 的关系：插件通过 ``plugin.json`` 声明 MCP server，
装配时作为 MCP server 的唯一配置来源。
"""

from .catalog import (
    MCP_TOOL_SEARCH_TOOL_NAME,
    MCPCatalog,
    MCPToolSearchService,
    ToolCatalogEntry,
    create_mcp_tool_search_tool,
    warn_long_mcp_descriptions,
)
from .config import (
    MCPServerConfig,
    MCPServerConfigError,
    load_mcp_server_configs,
    parse_mcp_server_config,
)
from .manager import (
    MCPToolInfo,
    MCPToolManager,
    build_langchain_tools,
    get_mcp_tool_display_name,
    get_mcp_tool_manager,
    load_mcp_tools,
    open_mcp_session,
    reset_mcp_tool_manager,
    sanitize_mcp_tool_name,
)

__all__ = [
    "MCP_TOOL_SEARCH_TOOL_NAME",
    "MCPCatalog",
    "MCPServerConfig",
    "MCPServerConfigError",
    "MCPToolInfo",
    "MCPToolManager",
    "MCPToolSearchService",
    "ToolCatalogEntry",
    "build_langchain_tools",
    "create_mcp_tool_search_tool",
    "get_mcp_tool_display_name",
    "get_mcp_tool_manager",
    "load_mcp_server_configs",
    "load_mcp_tools",
    "open_mcp_session",
    "parse_mcp_server_config",
    "reset_mcp_tool_manager",
    "sanitize_mcp_tool_name",
    "warn_long_mcp_descriptions",
]
