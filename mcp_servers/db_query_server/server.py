#!/usr/bin/env python3
"""Database Tools MCP Server（MySQL 只读诊断）。

一个 MCP 工具 ``db_query``，通过 ``action`` 路由到具体诊断能力。这样客户端目录里
只多一个工具 schema，而不是 8 个；action 的取值与参数约束由服务端统一校验。
"""

import json
import logging
from typing import Literal

from mcp.server.mcpserver import MCPServer

from mcp_servers.common.config import MCP_DB_QUERY_PORT
from mcp_servers.common.output import truncate_output
from mcp_servers.db_query_server.tools import (
    mysql_connections,
    mysql_info,
    mysql_locks,
    mysql_replication,
    mysql_slowlog,
    mysql_status,
    mysql_transactions,
    mysql_variables,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = MCPServer("Database Tools Server")

EMERGENCY_OUTPUT_LIMIT_KB = 2048

DBAction = Literal[
    "info",
    "status",
    "variables",
    "connections",
    "locks",
    "replication",
    "transactions",
    "slowlog",
]


def json_dumps(obj, **kwargs):
    """输出不转义中文的 JSON。"""
    return json.dumps(obj, ensure_ascii=False, default=str, **kwargs)


def format_result(result: dict, max_size_kb: int = EMERGENCY_OUTPUT_LIMIT_KB) -> str:
    """格式化脚本执行结果，应用输出截断。"""
    json_str = json_dumps(result, indent=2)
    if len(json_str.encode("utf-8")) <= max_size_kb * 1024:
        return json_str
    truncated = truncate_output(json_str, max_size_kb=max_size_kb)
    if truncated["truncated"]:
        return f"{truncated['content']}\n\n⚠️ {truncated['warning']}"
    return json_str


def _validate_action(action: str) -> str:
    known = set(DBAction.__args__)
    if action not in known:
        raise ValueError(f"不支持的 action: {action!r}；可选: {', '.join(sorted(known))}")
    return action


def _dispatch(
    action: str,
    *,
    ip: str,
    port: int,
    pattern: str,
    min_seconds: int,
    since: str | None,
    until: str | None,
    min_time: float,
    limit: int,
    timeout: int,
) -> dict:
    if action == "info":
        return mysql_info.execute(db_host=ip, port=port)
    if action == "status":
        return mysql_status.execute(db_host=ip, port=port, pattern=pattern)
    if action == "variables":
        return mysql_variables.execute(db_host=ip, port=port, pattern=pattern)
    if action == "connections":
        return mysql_connections.execute(db_host=ip, port=port)
    if action == "locks":
        return mysql_locks.execute(db_host=ip, port=port)
    if action == "replication":
        return mysql_replication.execute(db_host=ip, port=port)
    if action == "transactions":
        return mysql_transactions.execute(db_host=ip, port=port, min_seconds=min_seconds)
    return mysql_slowlog.execute(
        db_host=ip,
        port=port,
        since=since,
        until=until,
        min_time=min_time,
        limit=limit,
        timeout=timeout,
    )


@mcp.tool()
async def db_query(
    action: DBAction,
    ip: str,
    port: int = 3306,
    pattern: str = "",
    min_seconds: int = 1,
    since: str | None = None,
    until: str | None = None,
    min_time: float = 1.0,
    limit: int = 50,
    timeout: int = 60,
) -> str:
    """MySQL 只读诊断统一入口。先选 action，再按该 action 的参数说明传参。

    action:
      info         基本信息（版本/运行时间/库列表/关键配置）；只用 ip/port
      status       SHOW STATUS；pattern 过滤（如 Threads、Conn、Query）
      variables    SHOW VARIABLES；pattern 过滤（如 timeout、buffer、innodb）
      connections  当前连接数/最大连接数/来源分布/活跃查询；只用 ip/port
      locks        锁等待/持锁事务/死锁；只用 ip/port
      replication  主从复制 + 半同步；只用 ip/port
      transactions 长事务；min_seconds 最小未提交秒数
      slowlog      慢查询分析；since/until/min_time/limit/timeout

    Args:
        action: 诊断动作，取值为上述 action 之一
        ip: 数据库主机 IP，字段名固定为 ip
        port: 数据库端口
        pattern: status/variables 的过滤模式，其他 action 忽略
        min_seconds: transactions 的最小事务时长
        since: slowlog 开始时间（"YYYY-MM-DD HH:MM:SS" 或 "1h"/"2d"/"1w"）
        until: slowlog 结束时间
        min_time: slowlog 最小执行时间（秒）
        limit: slowlog 返回 Top N
        timeout: slowlog 分析超时（秒）
    """
    try:
        _validate_action(action)
        result = _dispatch(
            action,
            ip=ip,
            port=port,
            pattern=pattern,
            min_seconds=min_seconds,
            since=since,
            until=until,
            min_time=min_time,
            limit=limit,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - MCP 边界统一转成模型可读错误
        logger.warning("db_query action=%s failed: %s", action, exc)
        result = {"success": False, "error": str(exc)}
    return format_result(result)


def main():
    logger.info("Starting Database Tools Server on port %s", MCP_DB_QUERY_PORT)
    logger.info("Database User: %s", "见 MCP_DIAG_DB_USER 环境变量")
    mcp.run("streamable-http", host="0.0.0.0", port=MCP_DB_QUERY_PORT)


if __name__ == "__main__":
    main()
