#!/usr/bin/env python3
"""Database Tools MCP Server（MySQL 只读诊断）。

自 fullstack-langgraph 平移并迁移到官方 ``mcp`` 2.x ``MCPServer``（2026-07-28 协议世代）。
诊断实现位于 ``tools/``，输出统一走 ``format_result`` JSON 契约。
"""

import json
import logging

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

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 创建 MCP 服务器实例（官方 2.x MCPServer，streamable-http 传输）
mcp = MCPServer("Database Tools Server")

# 仅保留紧急安全兜底，正常的大结果由 agent 层统一做 artifact/offload 管理。
EMERGENCY_OUTPUT_LIMIT_KB = 2048


def json_dumps(obj, **kwargs):
    """输出不转义中文的 JSON"""
    return json.dumps(obj, ensure_ascii=False, default=str, **kwargs)


def format_result(result: dict, max_size_kb: int = EMERGENCY_OUTPUT_LIMIT_KB) -> str:
    """格式化脚本执行结果，应用输出截断

    Args:
        result: 脚本执行结果 dict
        max_size_kb: 最大输出大小（KB）

    Returns:
        格式化后的 JSON 字符串
    """
    # 先序列化为 JSON
    json_str = json_dumps(result, indent=2)

    # 检查大小
    if len(json_str.encode('utf-8')) <= max_size_kb * 1024:
        return json_str

    # 需要截断
    truncated = truncate_output(json_str, max_size_kb=max_size_kb)

    if truncated["truncated"]:
        # 返回截断后的内容和警告
        return f"{truncated['content']}\n\n⚠️ {truncated['warning']}"

    return json_str


# ============== MySQL 工具 ==============

@mcp.tool()
async def db_mysql_info(ip: str, port: int = 3306) -> str:
    """获取 MySQL 数据库基本信息（版本、运行时间、数据库列表、关键配置）

    参考技能: skill_load("ops-health-inspection", ["sop/mysql-check"])

    Args:
        ip: 数据库主机 IP，字段名固定为 ip
        port: 数据库端口
    """
    result = mysql_info.execute(db_host=ip, port=port)
    return format_result(result)


@mcp.tool()
async def db_mysql_status(ip: str, port: int = 3306, pattern: str = "") -> str:
    """查询 MySQL 状态变量（SHOW STATUS）

    Args:
        ip: 数据库主机 IP，字段名固定为 ip
        port: 数据库端口
        pattern: 过滤模式（如 Threads、Conn、Query）
    """
    result = mysql_status.execute(db_host=ip, port=port, pattern=pattern)
    return format_result(result)


@mcp.tool()
async def db_mysql_variables(ip: str, port: int = 3306, pattern: str = "") -> str:
    """查询 MySQL 配置变量（SHOW VARIABLES）

    Args:
        ip: 数据库主机 IP，字段名固定为 ip
        port: 数据库端口
        pattern: 过滤模式（如 timeout、buffer、innodb）
    """
    result = mysql_variables.execute(db_host=ip, port=port, pattern=pattern)
    return format_result(result)


@mcp.tool()
async def db_mysql_connections(ip: str, port: int = 3306) -> str:
    """检查 MySQL 连接数（当前连接数、最大连接数、连接来源分布、活跃查询）

    Args:
        ip: 数据库主机 IP，字段名固定为 ip
        port: 数据库端口
    """
    result = mysql_connections.execute(db_host=ip, port=port)
    return format_result(result)


@mcp.tool()
async def db_mysql_locks(ip: str, port: int = 3306) -> str:
    """检查 MySQL 锁等待（锁等待、持锁事务、死锁信息）

    Args:
        ip: 数据库主机 IP，字段名固定为 ip
        port: 数据库端口
    """
    result = mysql_locks.execute(db_host=ip, port=port)
    return format_result(result)


@mcp.tool()
async def db_mysql_replication(ip: str, port: int = 3306) -> str:
    """检查 MySQL 复制状态（主从复制 + 半同步）

    Args:
        ip: 数据库主机 IP，字段名固定为 ip
        port: 数据库端口
    """
    result = mysql_replication.execute(db_host=ip, port=port)
    return format_result(result)


@mcp.tool()
async def db_mysql_transactions(ip: str, port: int = 3306, min_seconds: int = 1) -> str:
    """检查 MySQL 长事务（超过指定秒数的未提交事务）

    Args:
        ip: 数据库主机 IP，字段名固定为 ip
        port: 数据库端口
        min_seconds: 最小运行时间（秒）
    """
    result = mysql_transactions.execute(db_host=ip, port=port, min_seconds=min_seconds)
    return format_result(result)


@mcp.tool()
async def db_mysql_slowlog(
    ip: str,
    port: int = 3306,
    since: str = None,
    until: str = None,
    min_time: float = 1.0,
    limit: int = 50,
    timeout: int = 60
) -> str:
    """分析 MySQL 慢查询日志（使用 pt-query-digest）

    Args:
        ip: 数据库主机 IP，字段名固定为 ip
        port: 数据库端口，默认 3306
        since: 开始时间，格式 "YYYY-MM-DD HH:MM:SS" 或 "1h"/"2d"/"1w"
        until: 结束时间，格式同 since
        min_time: 最小执行时间（秒），过滤掉更快的查询，默认 1.0
        limit: 返回 Top N 条慢查询，默认 50
        timeout: 超时时间（秒），默认 60
    """
    result = mysql_slowlog.execute(
        db_host=ip, port=port, since=since, until=until,
        min_time=min_time, limit=limit, timeout=timeout
    )
    return format_result(result)


def main():
    logger.info("Starting Database Tools Server on port %s", MCP_DB_QUERY_PORT)
    logger.info("Database User: %s", "见 MCP_DIAG_DB_USER 环境变量")
    # streamable-http 传输（MCP 2026-07-28 世代 SDK）
    mcp.run("streamable-http", host="0.0.0.0", port=MCP_DB_QUERY_PORT)


if __name__ == "__main__":
    main()
