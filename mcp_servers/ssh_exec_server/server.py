#!/usr/bin/env python3
"""SSH Tools MCP Server（Linux 只读巡检）。

自 fullstack-langgraph 平移并迁移到官方 ``mcp`` 2.x ``MCPServer``（2026-07-28 协议世代）。
巡检实现位于 ``tools/``，输出统一走 ``format_result`` JSON 契约。
"""

import json
import logging
from typing import Optional

from mcp.server.mcpserver import MCPServer

from mcp_servers.common.config import MCP_SSH_EXEC_PORT
from mcp_servers.common.output import truncate_output
from mcp_servers.ssh_exec_server.tools import (
    disk,
    grep,
    info,
    journal,
    list as list_module,
    network,
    process,
    read,
    snapshot,
)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 创建 MCP 服务器实例（官方 2.x MCPServer，streamable-http 传输）
mcp = MCPServer("SSH Tools Server")

# 仅保留紧急安全兜底，正常的大结果由 agent 层统一做 artifact/offload 管理。
EMERGENCY_OUTPUT_LIMIT_KB = 2048


def json_dumps(obj, **kwargs):
    """输出不转义中文的 JSON"""
    return json.dumps(obj, ensure_ascii=False, default=str, **kwargs)


def format_result(result: dict, tool_name: str, max_size_kb: int = EMERGENCY_OUTPUT_LIMIT_KB) -> str:
    """将脚本结果统一包装为 JSON contract。

    Args:
        result: 脚本执行结果 dict
        tool_name: 工具名
        max_size_kb: 最大输出大小（KB）

    Returns:
        统一格式后的 JSON 字符串
    """
    success = bool(result.get("success"))
    error = result.get("error")
    meta = {k: v for k, v in result.items() if k not in {"success", "error", "output", "message"}}

    output = result.get("output")
    if isinstance(output, str) and output:
        truncated = truncate_output(output, max_size_kb=max_size_kb)
        output = truncated["content"]
        if truncated["truncated"]:
            meta["output_truncated"] = True
            meta["output_truncation"] = truncated["metadata"]
            meta["output_warning"] = truncated["warning"]
    elif output is None:
        output = ""

    if success:
        msg = str(result.get("message") or "")
    else:
        msg = error or "执行失败"

    normalized = {
        "status": "ok" if success else "error",
        "msg": msg,
        "data": {
            "tool": tool_name,
            "output": output,
            "meta": meta,
        },
    }
    return json_dumps(normalized)


# ============== System 工具 ==============

@mcp.tool()
async def system_overview(ip: str, timeout: int = 30) -> str:
    """获取远程服务器系统概览（CPU、内存、磁盘、负载、网卡）

    Args:
        ip: 目标主机 IP，字段名固定为 ip
        timeout: 超时时间（秒）
    """
    result = info.execute(host=ip, timeout=timeout)
    return format_result(result, "system_overview")


@mcp.tool()
async def system_disk_usage(
    ip: str,
    path: str = "/",
    detail: bool = False,
    io: bool = False,
    timeout: int = 60
) -> str:
    """分析远程服务器磁盘使用情况

    Args:
        ip: 目标主机 IP，字段名固定为 ip
        path: 分析路径，默认 /
        detail: 是否详细分析（目录大小排名 + 大文件查找）
        io: 是否查看磁盘 IO（util、iowait、top IO 进程）
        timeout: 超时时间（秒）
    """
    result = disk.execute(host=ip, path=path, detail=detail, io=io, timeout=timeout)
    return format_result(result, "system_disk_usage")


@mcp.tool()
async def system_process_list(
    ip: str,
    filter: Optional[str] = None,
    sort_by: Optional[str] = None,
    timeout: int = 30
) -> str:
    """查看远程服务器进程列表

    Args:
        ip: 目标主机 IP，字段名固定为 ip
        filter: 过滤关键词（如 mysqld、nginx）
        sort_by: 排序方式（cpu/mem），使用 top 实时采样
        timeout: 超时时间（秒）
    """
    result = process.execute(host=ip, filter=filter, sort_by=sort_by, timeout=timeout)
    return format_result(result, "system_process_list")


@mcp.tool()
async def system_network_overview(ip: str, timeout: int = 30) -> str:
    """查看远程服务器网络总览（网卡流量/热点连接/监听端口）

    Args:
        ip: 目标主机 IP，字段名固定为 ip
        timeout: 超时时间（秒）
    """
    result = network.execute(host=ip, timeout=timeout)
    return format_result(result, "system_network_overview")


@mcp.tool()
async def system_network_ping(ip: str, target: str, count: int = 3, timeout: int = 30) -> str:
    """Ping 连通性检测

    Args:
        ip: 执行 ping 的源主机 IP，字段名固定为 ip
        target: ping 目标主机
        count: ping 次数
        timeout: 超时时间（秒）
    """
    result = network.ping(host=ip, target=target, count=count, timeout=timeout)
    return format_result(result, "system_network_ping")


@mcp.tool()
async def system_network_port_check(
    ip: str,
    target: str,
    port: int,
    timeout: int = 10
) -> str:
    """TCP 端口连通性检测

    Args:
        ip: 执行检测的源主机 IP，字段名固定为 ip
        target: 目标主机
        port: 目标端口
        timeout: 超时时间（秒）
    """
    result = network.port_check(host=ip, target=target, port=port, timeout=timeout)
    return format_result(result, "system_network_port_check")


@mcp.tool()
async def system_network_conn_stat(ip: str, timeout: int = 30) -> str:
    """TCP 连接状态统计

    Args:
        ip: 目标主机 IP，字段名固定为 ip
        timeout: 超时时间（秒）
    """
    result = network.conn_stat(host=ip, timeout=timeout)
    return format_result(result, "system_network_conn_stat")


@mcp.tool()
async def system_file_read(
    ip: str,
    file_path: str,
    offset: int = 0,
    limit: int = 100,
    timeout: int = 30
) -> str:
    """读取远程服务器文件内容（支持分页）

    Args:
        ip: 目标主机 IP，字段名固定为 ip
        file_path: 文件绝对路径
        offset: 起始行（从 0 开始）
        limit: 读取行数
        timeout: 超时时间（秒）
    """
    result = read.execute(host=ip, file_path=file_path, offset=offset, limit=limit, timeout=timeout)
    return format_result(result, "system_file_read")


@mcp.tool()
async def system_file_grep(
    ip: str,
    file_path: str,
    pattern: str,
    max_lines: int = 1000,
    before_lines: int = 0,
    after_lines: int = 0,
    timeout: int = 30
) -> str:
    """在远程服务器文件中搜索内容（支持正则）

    Args:
        ip: 目标主机 IP，字段名固定为 ip
        file_path: 文件绝对路径
        pattern: 搜索模式（支持正则）
        max_lines: 最大返回行数
        before_lines: 显示匹配前 N 行
        after_lines: 显示匹配后 N 行
        timeout: 超时时间（秒）
    """
    result = grep.execute(
        host=ip, file_path=file_path, pattern=pattern,
        max_lines=max_lines, before_lines=before_lines,
        after_lines=after_lines, timeout=timeout
    )
    return format_result(result, "system_file_grep")


@mcp.tool()
async def system_file_list(
    ip: str,
    path: str = "/tmp",
    sort_by: str = "name",
    limit: int = 50,
    timeout: int = 30
) -> str:
    """列出远程服务器目录内容

    Args:
        ip: 目标主机 IP，字段名固定为 ip
        path: 目录路径
        sort_by: 排序方式（name/size/time）
        limit: 最大返回条数，默认 50，最大 200
        timeout: 超时时间（秒）
    """
    result = list_module.execute(host=ip, path=path, sort_by=sort_by, limit=limit, timeout=timeout)
    return format_result(result, "system_file_list")


@mcp.tool()
async def system_journal_query(
    ip: str,
    unit: Optional[str] = None,
    priority: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    grep: Optional[str] = None,
    lines: int = 500,
    output_format: str = "short",
    timeout: int = 30
) -> str:
    """查询远程服务器系统日志（journalctl）

    Args:
        ip: 目标主机 IP，字段名固定为 ip
        unit: 服务单元，多个用逗号分隔（如 nginx,mysql）
        priority: 优先级（err/warning/info）
        since: 开始时间（如 "1 hour ago"、"2025-01-12 09:00:00"）
        until: 结束时间
        grep: 关键词过滤（支持正则）
        lines: 最大行数
        output_format: 输出格式（short/json/verbose/cat）
        timeout: 超时时间（秒）
    """
    result = journal.execute(
        host=ip, unit=unit, priority=priority,
        since=since, until=until, grep=grep, lines=lines,
        output_format=output_format, timeout=timeout
    )
    return format_result(result, "system_journal_query")


@mcp.tool()
async def system_snapshot(
    ip: str,
    since: Optional[str] = "30m",
    service: Optional[str] = None,
    timeout: int = 90
) -> str:
    """采集远程主机现场快照（直接返回完整快照）

    Args:
        ip: 目标主机 IP，字段名固定为 ip
        since: 日志时间窗口（如 30m、1 hour ago）
        service: 可选服务名（如 mysql.service）
        timeout: 快照总超时（秒，20-300）
    """
    result = snapshot.execute(host=ip, since=since, service=service, timeout=timeout)
    return format_result(result, "system_snapshot")


def main():
    logger.info("Starting SSH Tools Server on port %s", MCP_SSH_EXEC_PORT)
    logger.info("SSH config: username/key_file 见 MCP_DIAG_SSH_* 环境变量")
    # streamable-http 传输（MCP 2026-07-28 世代 SDK）
    mcp.run("streamable-http", host="0.0.0.0", port=MCP_SSH_EXEC_PORT)


if __name__ == "__main__":
    main()
