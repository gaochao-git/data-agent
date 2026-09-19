"""mcp_servers 共享层：配置、输出截断、参数校验、SSH/DB 客户端。"""

from mcp_servers.common.config import (
    MCP_DIAG_DB_PASSWORD,
    MCP_DIAG_DB_USER,
    MCP_DB_QUERY_PORT,
    MCP_DIAG_SSH_KEY_FILE,
    MCP_DIAG_SSH_PORT,
    MCP_DIAG_SSH_USERNAME,
    MCP_SSH_EXEC_PORT,
    mcp_server_tmp_dir,
)
from mcp_servers.common.output import debug_log, truncate_output
from mcp_servers.common.validation import validate_filter, validate_host, validate_path
from mcp_servers.common.ssh_client import SSHClient, get_ssh_client
from mcp_servers.common.db_client import DBClient, get_db_client

__all__ = [
    "MCP_DIAG_DB_PASSWORD",
    "MCP_DIAG_DB_USER",
    "MCP_DB_QUERY_PORT",
    "MCP_DIAG_SSH_KEY_FILE",
    "MCP_DIAG_SSH_PORT",
    "MCP_DIAG_SSH_USERNAME",
    "MCP_SSH_EXEC_PORT",
    "DBClient",
    "SSHClient",
    "debug_log",
    "get_db_client",
    "get_ssh_client",
    "mcp_server_tmp_dir",
    "truncate_output",
    "validate_filter",
    "validate_host",
    "validate_path",
]
