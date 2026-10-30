#!/usr/bin/env python3
"""Database Tools MCP Server（MySQL 只读诊断）。

一个 MCP 工具 ``db_query``，通过 ``action`` 选择命令：

- 公共参数：``ip`` / ``port`` / ``timeout``
- 命令参数：``action_payload``
- 首次使用先 ``action="help"``，返回每个 action 的 payload 字段、必填项和示例

这样工具 schema 不会随命令数量膨胀，同时每个命令仍有独立的参数契约。
"""

import json
import logging
from typing import Any

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

# 每个 action 的命令参数契约。公共 ip/port/timeout 不在这里重复。
_ACTIONS: dict[str, dict[str, Any]] = {
    "info": {
        "description": "MySQL 基本信息：版本、运行时间、数据库列表、关键配置",
        "payload": {},
        "example": {"action": "info", "ip": "10.0.0.12", "port": 3306},
    },
    "status": {
        "description": "SHOW STATUS 状态变量",
        "payload": {
            "pattern": {"type": "string", "required": False, "description": "变量名过滤，如 Threads、Conn、Query"},
        },
        "example": {"action": "status", "ip": "10.0.0.12", "action_payload": {"pattern": "Threads"}},
    },
    "variables": {
        "description": "SHOW VARIABLES 系统变量",
        "payload": {
            "pattern": {"type": "string", "required": False, "description": "变量名过滤，如 timeout、buffer、innodb"},
        },
        "example": {"action": "variables", "ip": "10.0.0.12", "action_payload": {"pattern": "timeout"}},
    },
    "connections": {
        "description": "当前连接数、最大连接数、来源分布、活跃查询",
        "payload": {},
        "example": {"action": "connections", "ip": "10.0.0.12", "port": 3306},
    },
    "locks": {
        "description": "锁等待、持锁事务、死锁",
        "payload": {},
        "example": {"action": "locks", "ip": "10.0.0.12", "port": 3306},
    },
    "replication": {
        "description": "主从复制、半同步状态",
        "payload": {},
        "example": {"action": "replication", "ip": "10.0.0.12", "port": 3306},
    },
    "transactions": {
        "description": "长事务",
        "payload": {
            "min_seconds": {
                "type": "integer",
                "required": False,
                "default": 1,
                "description": "最小未提交秒数",
            },
        },
        "example": {"action": "transactions", "ip": "10.0.0.12", "action_payload": {"min_seconds": 5}},
    },
    "slowlog": {
        "description": "慢查询日志分析",
        "payload": {
            "since": {
                "type": "string",
                "required": False,
                "description": "开始时间，如 1h/2d/1w 或 YYYY-MM-DD HH:MM:SS",
            },
            "until": {"type": "string", "required": False, "description": "结束时间，格式同 since"},
            "min_time": {"type": "number", "required": False, "default": 1.0, "description": "最小执行时间（秒）"},
            "limit": {"type": "integer", "required": False, "default": 50, "description": "返回 Top N"},
        },
        "example": {
            "action": "slowlog",
            "ip": "10.0.0.12",
            "action_payload": {"since": "1h", "min_time": 2, "limit": 20},
        },
    },
}


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


def _help_payload() -> dict[str, Any]:
    """action=help 的返回契约。"""
    return {
        "tool": "db_query",
        "description": "MySQL 只读诊断统一入口；先 action=help，再按命令 payload 传参",
        "common": {
            "ip": {"type": "string", "required": True, "description": "数据库主机 IP"},
            "port": {"type": "integer", "required": False, "default": 3306},
            "timeout": {"type": "integer", "required": False, "default": 60, "description": "命令超时（秒）"},
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
        name: field["default"] for name, field in spec["payload"].items() if "default" in field and name not in payload
    }
    return {**defaults, **payload}


def _dispatch(
    action: str,
    *,
    ip: str,
    port: int,
    timeout: int,
    payload: dict[str, Any],
) -> dict:
    if action == "info":
        return mysql_info.execute(db_host=ip, port=port)
    if action == "status":
        return mysql_status.execute(db_host=ip, port=port, pattern=str(payload.get("pattern") or ""))
    if action == "variables":
        return mysql_variables.execute(db_host=ip, port=port, pattern=str(payload.get("pattern") or ""))
    if action == "connections":
        return mysql_connections.execute(db_host=ip, port=port)
    if action == "locks":
        return mysql_locks.execute(db_host=ip, port=port)
    if action == "replication":
        return mysql_replication.execute(db_host=ip, port=port)
    if action == "transactions":
        return mysql_transactions.execute(db_host=ip, port=port, min_seconds=int(payload.get("min_seconds", 1)))
    return mysql_slowlog.execute(
        db_host=ip,
        port=port,
        since=payload.get("since"),
        until=payload.get("until"),
        min_time=float(payload.get("min_time", 1.0)),
        limit=int(payload.get("limit", 50)),
        timeout=timeout,
    )


def _error_result(action: str, exc: Exception) -> dict:
    """参数/执行错误时附带 help，让模型能直接按 action_help 重试。"""
    actions = _help_payload()["actions"]
    if action in actions:
        return {
            "success": False,
            "error": str(exc),
            "hint": f"action={action} 参数错误；先调用 action='help'，或按下方 action_help 修正 action_payload",
            "action_help": actions[action],
        }
    return {
        "success": False,
        "error": str(exc),
        "hint": "先调用 action='help' 查看可用 action 及各命令的 action_payload",
        "available_actions": sorted(_ACTIONS),
    }


@mcp.tool()
async def db_query(
    action: str,
    ip: str = "",
    port: int = 3306,
    timeout: int = 60,
    action_payload: dict[str, Any] | None = None,
) -> str:
    """MySQL 只读诊断统一入口。

    使用顺序：
    1. 首次调用 action="help"，读取每个命令的 action_payload 字段和示例。
    2. 调用具体 action，公共参数传 ip/port/timeout，命令参数全部放进 action_payload。

    参数错误时返回体会带 hint/action_help（未知 action 带 available_actions），按提示修正后重试。

    已支持 action: info / status / variables / connections / locks / replication / transactions / slowlog。
    """
    action = str(action or "").strip()
    if action == "help":
        return json_dumps({"status": "ok", "data": _help_payload()}, indent=2)
    try:
        if not str(ip or "").strip():
            raise ValueError("缺少公共参数 ip")
        payload = _validate_and_extract(action, action_payload)
        result = _dispatch(action, ip=ip, port=port, timeout=timeout, payload=payload)
    except Exception as exc:  # noqa: BLE001 - MCP 边界统一转成模型可读错误
        logger.warning("db_query action=%s failed: %s", action, exc)
        result = _error_result(action, exc)
    return format_result(result)


def main():
    logger.info("Starting Database Tools Server on port %s", MCP_DB_QUERY_PORT)
    logger.info("Database User: %s", "见 MCP_DIAG_DB_USER 环境变量")
    mcp.run("streamable-http", host="0.0.0.0", port=MCP_DB_QUERY_PORT)


if __name__ == "__main__":
    main()
