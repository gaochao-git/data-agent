"""MCP server 配置解析与校验。

配置来源：**仅已启用插件** ``plugin.json`` 的 ``mcp_servers`` 声明
（见 ``agent/plugins/``）。环境变量独立挂载 MCP server 暂不启用（
独立挂载/管理页面后续提供），届时在此恢复多来源合并即可。

单条配置解析失败/未通过准入只记录告警并跳过该条，不让单个错误配置阻断
整个装配（MCP 是增量能力，坏了不该影响内置工具链路）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from utils.logger import get_logger

from .admission import validate_mcp_server_config

logger = get_logger(__name__)

# 兼容提示：环境变量单独挂载已于 2026-10 下线（改为仅插件声明）。旧部署若仍配置该变量，
# 明确告警而不是静默忽略，避免"配了却没生效"的排障成本。
if str(os.getenv("CHAT_DATA_MCP_SERVERS") or "").strip():
    logger.warning(
        "CHAT_DATA_MCP_SERVERS 已不再支持：MCP server 当前仅由插件 plugin.json 声明"
        "（独立挂载与管理页面后续提供），该环境变量会在 2026-10 后忽略"
    )

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
    # 原始工具名 → 用户可见中文友好名（前端展示用，不参与调用与命名空间）。
    tool_labels: dict[str, str] = field(default_factory=dict)
    # 原始工具名 → 每工具审批档位（ask/allow/force_ask/deny，缺省 ask）；
    # deny 在工具枚举层剔除（不挂载、不进检索目录），其余档位由审批中间件消费。
    tool_approvals: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


class MCPServerConfigError(ValueError):
    """MCP server 配置非法。"""


def _parse_transport_fields(raw: dict, source: str) -> tuple[str, str, str]:
    """校验 transport 与对应端点（stdio 需 command，远程需 http url），返回 (transport, command, url)。"""
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
    return transport, command, url


def _string_list_field(raw: dict, key: str, source: str) -> list[str]:
    """校验 ``raw[key]`` 为字符串数组（缺省为空数组）。"""
    value = raw.get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MCPServerConfigError(f"{source}: {key} 必须是字符串数组")
    return value


def _string_mapping_field(raw: dict, key: str, source: str) -> dict:
    """校验 ``raw[key]`` 为字符串到字符串的映射（缺省为空映射）。"""
    value = raw.get(key) or {}
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise MCPServerConfigError(f"{source}: {key} 必须是字符串到字符串的映射")
    return value


def _clean_tool_labels(raw_tool_labels: dict, source: str) -> dict[str, str]:
    """清洗 tool_labels 的键值空白；空值条目视为无效配置直接报错。"""
    tool_labels = {str(k).strip(): str(v).strip() for k, v in raw_tool_labels.items()}
    if any(not key or not value for key, value in tool_labels.items()):
        raise MCPServerConfigError(f"{source}: tool_labels 的键值都不能为空白字符串")
    return tool_labels


# 每工具审批档位：ask=执行前审批（缺省）；allow=ask 运行档下免审批；
# force_ask=覆盖运行档位强制审批（无人值守不挂载）；deny=装配期剔除（不可见不可检索）。
VALID_TOOL_APPROVALS = frozenset({"ask", "allow", "force_ask", "deny"})


def _clean_tool_approvals(raw_tool_approvals: dict, source: str) -> dict[str, str]:
    """校验 tool_approvals 的档位取值；键值空白或档位非法直接报错。"""
    tool_approvals = {str(k).strip(): str(v).strip() for k, v in raw_tool_approvals.items()}
    if any(not key for key in tool_approvals):
        raise MCPServerConfigError(f"{source}: tool_approvals 键不能为空白字符串")
    invalid = sorted(value for value in tool_approvals.values() if value not in VALID_TOOL_APPROVALS)
    if invalid:
        raise MCPServerConfigError(
            f"{source}: tool_approvals 值必须是 {sorted(VALID_TOOL_APPROVALS)} 之一，非法值: {invalid}"
        )
    return tool_approvals


def parse_mcp_server_config(raw: object, *, source: str) -> MCPServerConfig:
    """解析并校验单条 MCP server 配置，非法抛 ``MCPServerConfigError``。"""
    if not isinstance(raw, dict):
        raise MCPServerConfigError(f"{source}: 配置必须是对象")
    server_id = str(raw.get("id") or raw.get("server_id") or "").strip()
    if not _SERVER_ID_PATTERN.fullmatch(server_id):
        raise MCPServerConfigError(f"{source}: id 非法（需匹配 {_SERVER_ID_PATTERN.pattern}）: {server_id!r}")
    transport, command, url = _parse_transport_fields(raw, source)
    raw_args = _string_list_field(raw, "args", source)
    raw_env = _string_mapping_field(raw, "env", source)
    raw_headers = _string_mapping_field(raw, "headers", source)
    tool_labels = _clean_tool_labels(_string_mapping_field(raw, "tool_labels", source), source)
    tool_approvals = _clean_tool_approvals(_string_mapping_field(raw, "tool_approvals", source), source)
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise MCPServerConfigError(f"{source}: enabled 必须是布尔值")
    if transport == "sse":
        # HTTP+SSE 传输在 2025-03-26 废弃、2026-07-28 起进入 Deprecated 生命周期；
        # 仅保留与旧 server 的兼容，新部署应使用 streamable_http。
        logger.warning("%s: sse 传输已废弃（MCP 2026-07-28），建议迁移 streamable_http: %s", source, server_id)
    return MCPServerConfig(
        server_id=server_id,
        transport=transport,
        command=command,
        args=tuple(raw_args),
        env=dict(raw_env),
        url=url,
        headers=dict(raw_headers),
        tool_labels=tool_labels,
        tool_approvals=tool_approvals,
        enabled=bool(enabled),
    )


def _admit_config(config: MCPServerConfig, source: str) -> bool:
    """配置级准入：硬错误跳过该 server，告警保留并记日志（不阻断其它 server）。"""
    report = validate_mcp_server_config(config)
    for issue in report.warnings:
        logger.warning(
            "MCP server 准入告警: id=%s source=%s %s: %s", config.server_id, source, issue.code, issue.message
        )
    if not report.ok:
        logger.warning(
            "MCP server 未通过准入，已跳过: id=%s source=%s 原因=%s",
            config.server_id,
            source,
            report.summary(),
        )
        return False
    return True


def load_mcp_server_configs() -> list[MCPServerConfig]:
    """读取已启用插件声明的 MCP server 配置，并逐条做准入检查。

    暂不支持环境变量单独挂载 MCP server：当前唯一来源是插件 ``plugin.json``
    （随插件安装/启停生效），独立挂载与管理页面后续提供。
    """
    configs: list[MCPServerConfig] = []
    seen_ids: set[str] = set()
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
        if not _admit_config(config, f"plugin mcp_servers[{index}]"):
            continue
        if config.server_id in seen_ids:
            logger.warning("插件 MCP server id 重复，忽略: %s", config.server_id)
            continue
        seen_ids.add(config.server_id)
        configs.append(config)
    return configs


__all__ = [
    "MCPServerConfig",
    "MCPServerConfigError",
    "VALID_MCP_TRANSPORTS",
    "VALID_TOOL_APPROVALS",
    "load_mcp_server_configs",
    "parse_mcp_server_config",
]
