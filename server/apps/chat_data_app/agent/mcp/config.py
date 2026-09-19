"""MCP server 配置解析与校验。

配置来源（合并顺序，后者不覆盖前者同 id 项）：
1. 环境变量 ``CHAT_DATA_MCP_SERVERS``（JSON 数组，部署级配置）；
2. 已启用插件 ``plugin.json`` 的 ``mcp_servers`` 声明（见 ``agent/plugins/``）。

单条配置解析失败只记录告警并跳过该条，不让单个错误配置阻断整个装配
（MCP 是增量能力，坏了不该影响内置工具链路）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger(__name__)

MCP_SERVERS_ENV = "CHAT_DATA_MCP_SERVERS"

VALID_MCP_TRANSPORTS = frozenset({"stdio", "streamable_http", "sse"})

# server id 只用于日志、缓存键与工具名前缀，约束成安全的标识符片段。
_SERVER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$")


@dataclass(frozen=True)
class MCPServerConfig:
    """单个 MCP server 的连接配置。"""

    server_id: str
    transport: str  # stdio | streamable_http | sse
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


class MCPServerConfigError(ValueError):
    """MCP server 配置非法。"""


def parse_mcp_server_config(raw: object, *, source: str) -> MCPServerConfig:
    """解析并校验单条 MCP server 配置，非法抛 ``MCPServerConfigError``。"""
    if not isinstance(raw, dict):
        raise MCPServerConfigError(f"{source}: 配置必须是对象")
    server_id = str(raw.get("id") or raw.get("server_id") or "").strip()
    if not _SERVER_ID_PATTERN.fullmatch(server_id):
        raise MCPServerConfigError(f"{source}: id 非法（需匹配 {_SERVER_ID_PATTERN.pattern}）: {server_id!r}")
    transport = str(raw.get("transport") or "").strip()
    if transport not in VALID_MCP_TRANSPORTS:
        raise MCPServerConfigError(
            f"{source}: transport 必须是 {sorted(VALID_MCP_TRANSPORTS)} 之一，当前: {transport!r}"
        )
    command = str(raw.get("command") or "").strip()
    url = str(raw.get("url") or "").strip()
    if transport == "stdio" and not command:
        raise MCPServerConfigError(f"{source}: stdio 传输必须提供 command")
    if transport in ("streamable_http", "sse") and not url.startswith(("http://", "https://")):
        raise MCPServerConfigError(f"{source}: {transport} 传输必须提供 http(s) url")
    raw_args = raw.get("args") or []
    if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
        raise MCPServerConfigError(f"{source}: args 必须是字符串数组")
    raw_env = raw.get("env") or {}
    if not isinstance(raw_env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw_env.items()
    ):
        raise MCPServerConfigError(f"{source}: env 必须是字符串到字符串的映射")
    raw_headers = raw.get("headers") or {}
    if not isinstance(raw_headers, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw_headers.items()
    ):
        raise MCPServerConfigError(f"{source}: headers 必须是字符串到字符串的映射")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise MCPServerConfigError(f"{source}: enabled 必须是布尔值")
    return MCPServerConfig(
        server_id=server_id,
        transport=transport,
        command=command,
        args=tuple(raw_args),
        env=dict(raw_env),
        url=url,
        headers=dict(raw_headers),
        enabled=bool(enabled),
    )


def _env_mcp_server_configs() -> list[MCPServerConfig]:
    """从环境变量读取部署级 MCP server 配置。"""
    raw = str(os.getenv(MCP_SERVERS_ENV) or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError as err:
        logger.warning("环境变量 %s 不是合法 JSON，忽略全部 MCP 环境配置: %s", MCP_SERVERS_ENV, err)
        return []
    if not isinstance(data, list):
        logger.warning("环境变量 %s 必须是 JSON 数组，忽略全部 MCP 环境配置", MCP_SERVERS_ENV)
        return []
    configs: list[MCPServerConfig] = []
    for index, item in enumerate(data):
        try:
            config = parse_mcp_server_config(item, source=f"{MCP_SERVERS_ENV}[{index}]")
        except MCPServerConfigError as err:
            logger.warning("忽略非法 MCP server 配置: %s", err)
            continue
        configs.append(config)
    return configs


def load_mcp_server_configs() -> list[MCPServerConfig]:
    """合并环境配置与已启用插件声明的 MCP server 配置。"""
    configs = [config for config in _env_mcp_server_configs() if config.enabled]
    seen_ids = {config.server_id for config in configs}
    # 延迟导入避免 plugins -> manifest 之外的重依赖链在冷启动被拉起。
    try:
        from ..plugins.loader import enabled_plugin_mcp_servers

        plugin_entries = enabled_plugin_mcp_servers()
    except Exception as err:  # pragma: no cover - 插件加载自身已兜底，这里防御
        logger.warning("读取插件 MCP 配置失败，忽略插件侧配置: %s", err)
        plugin_entries = []
    for index, entry in enumerate(plugin_entries):
        try:
            config = parse_mcp_server_config(entry, source=f"plugin mcp_servers[{index}]")
        except MCPServerConfigError as err:
            logger.warning("忽略插件非法 MCP 配置: %s", err)
            continue
        if not config.enabled:
            continue
        if config.server_id in seen_ids:
            logger.warning("插件 MCP server id 与环境配置重复，忽略: %s", config.server_id)
            continue
        seen_ids.add(config.server_id)
        configs.append(config)
    return configs


__all__ = [
    "MCP_SERVERS_ENV",
    "MCPServerConfig",
    "MCPServerConfigError",
    "VALID_MCP_TRANSPORTS",
    "load_mcp_server_configs",
    "parse_mcp_server_config",
]
