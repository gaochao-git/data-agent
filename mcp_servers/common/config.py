"""mcp_server 运行配置：全部来自环境变量（``server/.env``）。

敏感值（数据库只读账号、SSH 私钥路径）不进仓库，占位见 ``.env.example``。
替代原 fullstack-langgraph ``mcp_servers/config.yaml`` 的口径。
"""

import os
from pathlib import Path


# 与主应用同一数据根（CHAT_DATA_HOME 必填，由部署环境提供）；独立运行时必须显式设置。
CHAT_DATA_HOME = os.environ["CHAT_DATA_HOME"]


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    return str(os.getenv(name) or "").strip() or default


# ---- 端口 ----
MCP_DB_QUERY_PORT = _env_int("MCP_DB_QUERY_PORT", 3001)
MCP_SSH_EXEC_PORT = _env_int("MCP_SSH_EXEC_PORT", 3002)

# ---- 目标数据库只读账号（db_query_server 工具按 ip/port 连接目标库时使用）----
MCP_DIAG_DB_USER = _env_str("MCP_DIAG_DB_USER", "root")
MCP_DIAG_DB_PASSWORD = os.getenv("MCP_DIAG_DB_PASSWORD", "")

# ---- SSH 默认连接参数（ssh_exec_server 工具按 host 连接目标机时使用）----
MCP_DIAG_SSH_PORT = _env_int("MCP_DIAG_SSH_PORT", 22)
MCP_DIAG_SSH_USERNAME = _env_str("MCP_DIAG_SSH_USERNAME", "root")
MCP_DIAG_SSH_KEY_FILE = _env_str("MCP_DIAG_SSH_KEY_FILE", "~/.ssh/id_rsa")


def mcp_server_tmp_dir() -> Path:
    """mcp_server 专用临时目录（慢日志下载等中间产物）。"""
    tmp_dir = Path(CHAT_DATA_HOME) / "tmp" / "mcp_servers"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir
