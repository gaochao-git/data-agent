#!/usr/bin/env python3
"""SSH Tools MCP Server（Linux 只读巡检）。

一个 MCP 工具 ``ssh_exec``，通过 ``action`` 路由到具体巡检能力。这样客户端目录里
只多一个工具 schema，而不是 12 个；action 的取值与必填参数由服务端统一校验。
"""

import json
import logging
from typing import Literal

from mcp.server.mcpserver import MCPServer

from mcp_servers.common.config import MCP_SSH_EXEC_PORT
from mcp_servers.common.output import truncate_output
from mcp_servers.ssh_exec_server.tools import (
    disk,
    info,
    journal,
    network,
    process,
    read,
    snapshot,
)
from mcp_servers.ssh_exec_server.tools import (
    grep as grep_module,
)
from mcp_servers.ssh_exec_server.tools import (
    list as list_module,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = MCPServer("SSH Tools Server")

EMERGENCY_OUTPUT_LIMIT_KB = 2048

SSHAction = Literal[
    "overview",
    "disk_usage",
    "process_list",
    "network_overview",
    "network_ping",
    "network_port_check",
    "network_conn_stat",
    "file_read",
    "file_grep",
    "file_list",
    "journal_query",
    "snapshot",
]


def json_dumps(obj, **kwargs):
    """输出不转义中文的 JSON。"""
    return json.dumps(obj, ensure_ascii=False, default=str, **kwargs)


def format_result(result: dict, tool_name: str, max_size_kb: int = EMERGENCY_OUTPUT_LIMIT_KB) -> str:
    """将脚本结果统一包装为 JSON contract。"""
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

    msg = str(result.get("message") or "") if success else (error or "执行失败")
    normalized = {
        "status": "ok" if success else "error",
        "msg": msg,
        "data": {"tool": tool_name, "output": output, "meta": meta},
    }
    return json_dumps(normalized)


def _validate_action(action: str) -> None:
    known = set(SSHAction.__args__)
    if action not in known:
        raise ValueError(f"不支持的 action: {action!r}；可选: {', '.join(sorted(known))}")


def _require(action: str, **values: object) -> None:
    missing = [name for name, value in values.items() if value in (None, "")]
    if missing:
        raise ValueError(f"action {action!r} 缺少必填参数: {', '.join(missing)}")


def _dispatch(
    action: str,
    *,
    ip: str,
    path: str | None,
    detail: bool,
    io: bool,
    process_filter: str | None,
    sort_by: str | None,
    target: str | None,
    count: int,
    target_port: int,
    file_path: str | None,
    offset: int,
    limit: int,
    pattern: str | None,
    max_lines: int,
    before_lines: int,
    after_lines: int,
    unit: str | None,
    priority: str | None,
    since: str | None,
    until: str | None,
    grep: str | None,
    lines: int,
    output_format: str,
    service: str | None,
    timeout: int,
) -> dict:
    if action == "overview":
        return info.execute(host=ip, timeout=timeout)
    if action == "disk_usage":
        return disk.execute(host=ip, path=path or "/", detail=detail, io=io, timeout=timeout)
    if action == "process_list":
        return process.execute(host=ip, filter=process_filter, sort_by=sort_by, timeout=timeout)
    if action == "network_overview":
        return network.execute(host=ip, timeout=timeout)
    if action == "network_ping":
        _require(action, target=target)
        return network.ping(host=ip, target=target, count=count, timeout=timeout)
    if action == "network_port_check":
        _require(action, target=target)
        if target_port <= 0:
            raise ValueError("action 'network_port_check' 缺少必填参数: target_port")
        return network.port_check(host=ip, target=target, port=target_port, timeout=timeout)
    if action == "network_conn_stat":
        return network.conn_stat(host=ip, timeout=timeout)
    if action == "file_read":
        _require(action, file_path=file_path)
        return read.execute(host=ip, file_path=file_path, offset=offset, limit=limit, timeout=timeout)
    if action == "file_grep":
        _require(action, file_path=file_path, pattern=pattern)
        return grep_module.execute(
            host=ip,
            file_path=file_path,
            pattern=pattern,
            max_lines=max_lines,
            before_lines=before_lines,
            after_lines=after_lines,
            timeout=timeout,
        )
    if action == "file_list":
        return list_module.execute(
            host=ip,
            path=path or "/tmp",
            sort_by=sort_by or "name",
            limit=limit,
            timeout=timeout,
        )
    if action == "journal_query":
        return journal.execute(
            host=ip,
            unit=unit,
            priority=priority,
            since=since,
            until=until,
            grep=grep,
            lines=lines,
            output_format=output_format,
            timeout=timeout,
        )
    return snapshot.execute(host=ip, since=since or "30m", service=service, timeout=timeout)


@mcp.tool()
async def ssh_exec(
    action: SSHAction,
    ip: str,
    path: str | None = None,
    detail: bool = False,
    io: bool = False,
    process_filter: str | None = None,
    sort_by: str | None = None,
    target: str | None = None,
    count: int = 3,
    target_port: int = 0,
    file_path: str | None = None,
    offset: int = 0,
    limit: int = 100,
    pattern: str | None = None,
    max_lines: int = 1000,
    before_lines: int = 0,
    after_lines: int = 0,
    unit: str | None = None,
    priority: str | None = None,
    since: str | None = None,
    until: str | None = None,
    grep: str | None = None,
    lines: int = 500,
    output_format: str = "short",
    service: str | None = None,
    timeout: int = 30,
) -> str:
    """Linux 只读巡检统一入口。先选 action，再按该 action 的参数说明传参。

    action:
      overview           系统概览（CPU/内存/磁盘/负载/网卡）；只用 ip/timeout
      disk_usage         磁盘用量；path 路径，detail 目录/大文件，io 磁盘 IO
      process_list       进程列表；process_filter 关键词，sort_by=cpu/mem
      network_overview   网络总览（网卡/热点连接/监听端口）
      network_ping       Ping；必填 target，count 次数
      network_port_check TCP 端口；必填 target、target_port
      network_conn_stat  TCP 连接状态统计
      file_read          读文件；必填 file_path，offset/limit 分页
      file_grep          文件检索；必填 file_path、pattern，max_lines/before_lines/after_lines
      file_list          目录列表；path、sort_by=name/size/time、limit
      journal_query      journalctl；unit/priority/since/until/grep/lines/output_format
      snapshot           现场快照；since 时间窗（默认 30m）、service、timeout

    Args:
        action: 巡检动作，取值为上述 action 之一
        ip: 目标主机 IP，字段名固定为 ip
        path: disk_usage/file_list 的路径
        detail: disk_usage 是否详细分析
        io: disk_usage 是否查看磁盘 IO
        process_filter: process_list 的过滤关键词
        sort_by: process_list 用 cpu/mem；file_list 用 name/size/time
        target: network_ping/network_port_check 的目标主机
        count: network_ping 次数
        target_port: network_port_check 的目标端口
        file_path: file_read/file_grep 的文件绝对路径
        offset: file_read 起始行
        limit: file_read 读取行数 / file_list 返回条数
        pattern: file_grep 搜索模式（支持正则）
        max_lines: file_grep 最大返回行数
        before_lines: file_grep 匹配前 N 行
        after_lines: file_grep 匹配后 N 行
        unit: journal_query 服务单元，多个用逗号分隔
        priority: journal_query 优先级（err/warning/info）
        since: journal_query 开始时间 / snapshot 时间窗
        until: journal_query 结束时间
        grep: journal_query 关键词过滤
        lines: journal_query 最大行数
        output_format: journal_query 输出格式（short/json/verbose/cat）
        service: snapshot 可选服务名
        timeout: 超时时间（秒）
    """
    try:
        _validate_action(action)
        result = _dispatch(
            action,
            ip=ip,
            path=path,
            detail=detail,
            io=io,
            process_filter=process_filter,
            sort_by=sort_by,
            target=target,
            count=count,
            target_port=target_port,
            file_path=file_path,
            offset=offset,
            limit=limit,
            pattern=pattern,
            max_lines=max_lines,
            before_lines=before_lines,
            after_lines=after_lines,
            unit=unit,
            priority=priority,
            since=since,
            until=until,
            grep=grep,
            lines=lines,
            output_format=output_format,
            service=service,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - MCP 边界统一转成模型可读错误
        logger.warning("ssh_exec action=%s failed: %s", action, exc)
        result = {"success": False, "error": str(exc)}
    return format_result(result, f"ssh_exec:{action}")


def main():
    logger.info("Starting SSH Tools Server on port %s", MCP_SSH_EXEC_PORT)
    logger.info("SSH config: username/key_file 见 MCP_DIAG_SSH_* 环境变量")
    mcp.run("streamable-http", host="0.0.0.0", port=MCP_SSH_EXEC_PORT)


if __name__ == "__main__":
    main()
