#!/usr/bin/env python3
"""SSH Tools MCP Server（Linux 只读巡检）。

一个 MCP 工具 ``ssh_exec``，通过 ``action`` 选择命令：

- 公共参数：``ip`` / ``timeout``
- 命令参数：``action_payload``
- 首次使用先 ``action="help"``，返回每个 action 的 payload 字段、必填项和示例
"""

import json
import logging
from typing import Any

from mcp.server.mcpserver import MCPServer

from mcp_servers.common.config import MCP_SSH_EXEC_PORT
from mcp_servers.common.output import truncate_output
from mcp_servers.ssh_exec_server.tools import (
    disk,
    grep,
    info,
    journal,
    network,
    process,
    read,
    snapshot,
)
from mcp_servers.ssh_exec_server.tools import (
    list as list_module,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = MCPServer("SSH Tools Server")

EMERGENCY_OUTPUT_LIMIT_KB = 2048
_DEFAULT_SINCE = "30m"

# 每个 action 的命令参数契约。公共 ip/timeout 不在这里重复。
_ACTIONS: dict[str, dict[str, Any]] = {
    "overview": {
        "description": "系统概览：CPU、内存、磁盘、负载、网卡",
        "payload": {},
        "example": {"action": "overview", "ip": "10.0.0.12"},
    },
    "disk_usage": {
        "description": "分析磁盘使用情况，可查看目录明细和磁盘 IO",
        "payload": {
            "path": {"type": "string", "required": False, "default": "/", "description": "路径"},
            "detail": {"type": "boolean", "required": False, "default": False, "description": "查看目录/大文件"},
            "io": {"type": "boolean", "required": False, "default": False, "description": "查看磁盘 IO"},
        },
        "example": {"action": "disk_usage", "ip": "10.0.0.12", "action_payload": {"path": "/data", "detail": True}},
    },
    "process_list": {
        "description": "进程列表，可按关键词过滤和 CPU/内存排序",
        "payload": {
            "filter": {"type": "string", "required": False, "description": "进程关键词"},
            "sort_by": {"type": "string", "required": False, "description": "cpu 或 mem"},
        },
        "example": {"action": "process_list", "ip": "10.0.0.12", "action_payload": {"sort_by": "cpu"}},
    },
    "network_overview": {
        "description": "网络总览：网卡、热点连接、监听端口",
        "payload": {},
        "example": {"action": "network_overview", "ip": "10.0.0.12"},
    },
    "network_ping": {
        "description": "Ping 目标主机",
        "payload": {
            "target": {"type": "string", "required": True, "description": "目标主机或 IP"},
            "count": {"type": "integer", "required": False, "default": 3, "description": "次数"},
        },
        "example": {"action": "network_ping", "ip": "10.0.0.12", "action_payload": {"target": "10.0.0.13"}},
    },
    "network_port_check": {
        "description": "检测目标 TCP 端口连通性",
        "payload": {
            "target": {"type": "string", "required": True, "description": "目标主机或 IP"},
            "target_port": {"type": "integer", "required": True, "description": "目标端口"},
        },
        "example": {
            "action": "network_port_check",
            "ip": "10.0.0.12",
            "action_payload": {"target": "10.0.0.13", "target_port": 3306},
        },
    },
    "network_conn_stat": {
        "description": "TCP 连接状态统计",
        "payload": {},
        "example": {"action": "network_conn_stat", "ip": "10.0.0.12"},
    },
    "file_read": {
        "description": "读取远程文件，支持分页",
        "payload": {
            "file_path": {"type": "string", "required": True, "description": "文件绝对路径"},
            "offset": {"type": "integer", "required": False, "default": 0, "description": "起始行"},
            "limit": {"type": "integer", "required": False, "default": 100, "description": "读取行数"},
        },
        "example": {
            "action": "file_read",
            "ip": "10.0.0.12",
            "action_payload": {"file_path": "/var/log/app.log", "limit": 200},
        },
    },
    "file_grep": {
        "description": "在远程文件中用正则检索",
        "payload": {
            "file_path": {"type": "string", "required": True, "description": "文件绝对路径"},
            "pattern": {"type": "string", "required": True, "description": "正则表达式"},
            "max_lines": {"type": "integer", "required": False, "default": 1000, "description": "最大返回行数"},
            "before_lines": {"type": "integer", "required": False, "default": 0, "description": "匹配前 N 行"},
            "after_lines": {"type": "integer", "required": False, "default": 0, "description": "匹配后 N 行"},
        },
        "example": {
            "action": "file_grep",
            "ip": "10.0.0.12",
            "action_payload": {"file_path": "/var/log/app.log", "pattern": "ERROR|Exception"},
        },
    },
    "file_list": {
        "description": "列出远程目录内容",
        "payload": {
            "path": {"type": "string", "required": False, "default": "/tmp", "description": "目录路径"},
            "sort_by": {"type": "string", "required": False, "default": "name", "description": "name/size/time"},
            "limit": {"type": "integer", "required": False, "default": 50, "description": "返回条数"},
        },
        "example": {"action": "file_list", "ip": "10.0.0.12", "action_payload": {"path": "/var/log"}},
    },
    "journal_query": {
        "description": "查询 journalctl 系统日志",
        "payload": {
            "unit": {"type": "string", "required": False, "description": "服务单元，多个用逗号分隔"},
            "priority": {"type": "string", "required": False, "description": "err/warning/info"},
            "since": {"type": "string", "required": False, "description": "开始时间，如 1 hour ago"},
            "until": {"type": "string", "required": False, "description": "结束时间"},
            "grep": {"type": "string", "required": False, "description": "关键词/正则过滤"},
            "lines": {"type": "integer", "required": False, "default": 500, "description": "最大行数"},
            "output_format": {
                "type": "string",
                "required": False,
                "default": "short",
                "description": "short/json/verbose/cat",
            },
        },
        "example": {
            "action": "journal_query",
            "ip": "10.0.0.12",
            "action_payload": {"unit": "nginx", "priority": "err", "since": "30 min ago"},
        },
    },
    "snapshot": {
        "description": "采集现场快照（日志 + 系统状态）",
        "payload": {
            "since": {"type": "string", "required": False, "default": _DEFAULT_SINCE, "description": "日志时间窗"},
            "service": {"type": "string", "required": False, "description": "可选服务名"},
        },
        "example": {"action": "snapshot", "ip": "10.0.0.12", "action_payload": {"service": "mysql.service"}},
    },
}


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
    return json_dumps(
        {
            "status": "ok" if success else "error",
            "msg": msg,
            "data": {"tool": tool_name, "output": output, "meta": meta},
        }
    )


def _help_payload() -> dict[str, Any]:
    """action=help 的返回契约。"""
    return {
        "tool": "ssh_exec",
        "description": "Linux 只读巡检统一入口；先 action=help，再按命令 payload 传参",
        "common": {
            "ip": {"type": "string", "required": True, "description": "目标主机 IP"},
            "timeout": {"type": "integer", "required": False, "default": 30, "description": "命令超时（秒）"},
        },
        "actions": {
            name: {
                "description": spec["description"],
                "action_payload": spec["payload"],
                "example": spec["example"],
            }
            for name, spec in _ACTIONS.items()
        },
    }


def _validate_and_extract(action: str, payload: Any) -> dict[str, Any]:
    """校验 action_payload：只允许该 action 声明的字段，并检查必填。"""
    spec = _ACTIONS.get(action)
    if spec is None:
        raise ValueError(f"不支持的 action: {action!r}；请先调用 action='help'")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError(f"action {action!r} 的 action_payload 必须是对象")
    allowed = set(spec["payload"])
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(
            f"action {action!r} 的 action_payload 包含未知字段: {', '.join(unknown)}；"
            f"可用字段: {', '.join(sorted(allowed)) or '无'}"
        )
    missing = [
        name for name, field in spec["payload"].items() if field.get("required") and payload.get(name) in (None, "")
    ]
    if missing:
        raise ValueError(f"action {action!r} 缺少必填 payload 字段: {', '.join(missing)}")
    defaults = {
        name: field.get("default")
        for name, field in spec["payload"].items()
        if "default" in field and name not in payload
    }
    return {**defaults, **payload}


def _dispatch(action: str, *, ip: str, timeout: int, payload: dict[str, Any]) -> dict:
    if action == "overview":
        return info.execute(host=ip, timeout=timeout)
    if action == "disk_usage":
        return disk.execute(
            host=ip,
            path=str(payload.get("path") or "/"),
            detail=bool(payload.get("detail")),
            io=bool(payload.get("io")),
            timeout=timeout,
        )
    if action == "process_list":
        return process.execute(
            host=ip,
            filter=payload.get("filter"),
            sort_by=payload.get("sort_by"),
            timeout=timeout,
        )
    if action == "network_overview":
        return network.execute(host=ip, timeout=timeout)
    if action == "network_ping":
        return network.ping(
            host=ip,
            target=str(payload.get("target") or ""),
            count=int(payload.get("count", 3)),
            timeout=timeout,
        )
    if action == "network_port_check":
        return network.port_check(
            host=ip,
            target=str(payload.get("target") or ""),
            port=int(payload.get("target_port") or 0),
            timeout=timeout,
        )
    if action == "network_conn_stat":
        return network.conn_stat(host=ip, timeout=timeout)
    if action == "file_read":
        return read.execute(
            host=ip,
            file_path=str(payload.get("file_path") or ""),
            offset=int(payload.get("offset", 0)),
            limit=int(payload.get("limit", 100)),
            timeout=timeout,
        )
    if action == "file_grep":
        return grep.execute(
            host=ip,
            file_path=str(payload.get("file_path") or ""),
            pattern=str(payload.get("pattern") or ""),
            max_lines=int(payload.get("max_lines", 1000)),
            before_lines=int(payload.get("before_lines", 0)),
            after_lines=int(payload.get("after_lines", 0)),
            timeout=timeout,
        )
    if action == "file_list":
        return list_module.execute(
            host=ip,
            path=str(payload.get("path") or "/tmp"),
            sort_by=str(payload.get("sort_by") or "name"),
            limit=int(payload.get("limit", 50)),
            timeout=timeout,
        )
    if action == "journal_query":
        return journal.execute(
            host=ip,
            unit=payload.get("unit"),
            priority=payload.get("priority"),
            since=payload.get("since"),
            until=payload.get("until"),
            grep=payload.get("grep"),
            lines=int(payload.get("lines", 500)),
            output_format=str(payload.get("output_format") or "short"),
            timeout=timeout,
        )
    return snapshot.execute(
        host=ip,
        since=payload.get("since") or _DEFAULT_SINCE,
        service=payload.get("service"),
        timeout=timeout,
    )


@mcp.tool()
async def ssh_exec(
    action: str,
    ip: str = "",
    timeout: int = 30,
    action_payload: dict[str, Any] | None = None,
) -> str:
    """Linux 只读巡检统一入口。

    使用顺序：
    1. 首次调用 action="help"，读取每个命令的 action_payload 字段和示例。
    2. 调用具体 action，公共参数传 ip/timeout，命令参数全部放进 action_payload。

    已支持 action: overview / disk_usage / process_list / network_overview /
    network_ping / network_port_check / network_conn_stat / file_read / file_grep /
    file_list / journal_query / snapshot。
    """
    action = str(action or "").strip()
    if action == "help":
        return json_dumps({"status": "ok", "data": _help_payload()}, indent=2)
    try:
        if not str(ip or "").strip():
            raise ValueError("缺少公共参数 ip")
        payload = _validate_and_extract(action, action_payload)
        result = _dispatch(action, ip=ip, timeout=timeout, payload=payload)
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
