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

VALID_MCP_TRANSPORTS = frozenset({"streamable_http"})

# server id 只用于日志、缓存键与工具名前缀，约束成安全的标识符片段。
_SERVER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$")


@dataclass(frozen=True)
class MCPServerConfig:
    """单个 MCP server 的连接配置。"""

    server_id: str
    transport: str  # streamable_http
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    # 原始工具名 → 用户可见中文友好名（前端展示用，不参与调用与命名空间）。
    tool_labels: dict[str, str] = field(default_factory=dict)
    # 原始工具名 → 每工具审批档位（ask/allow，缺省 ask）；
    # deny 在工具枚举层剔除（不挂载、不进检索目录），其余档位由审批中间件消费。
    tool_approvals: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


class MCPServerConfigError(ValueError):
    """MCP server 配置非法。"""


def _parse_transport_fields(raw: dict, source: str) -> tuple[str, str]:
    """校验 transport：只允许 streamable_http，且必须提供 http(s) url。"""
    transport = str(raw.get("transport") or "").strip()
    if transport not in VALID_MCP_TRANSPORTS:
        raise MCPServerConfigError(f"{source}: transport 只支持 {sorted(VALID_MCP_TRANSPORTS)}，当前: {transport!r}")
    url = str(raw.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise MCPServerConfigError(f"{source}: streamable_http 传输必须提供 http(s) url")
    return transport, url


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


# 每工具审批档位（B 端固定策略）：ask=执行前必须人工确认（缺省）；allow=无需人工确认。
# 不允许用户会话级切换；不引入 deny/force_ask/auto 等额外档位。
VALID_TOOL_APPROVALS = frozenset({"ask", "allow"})
# 旧版本值迁移：auto 等价 allow；force_ask 等价 ask；deny 仅保留“该工具不挂载”语义。
_LEGACY_TOOL_APPROVALS = {"auto": "allow", "allow": "allow", "ask": "ask", "force_ask": "ask"}


def _clean_tool_approvals(raw_tool_approvals: dict, source: str) -> dict[str, str]:
    """校验 tool_approvals 的档位取值；键值空白或档位非法直接报错。"""
    raw = {str(k).strip(): str(v).strip().lower() for k, v in raw_tool_approvals.items()}
    if any(not key for key in raw):
        raise MCPServerConfigError(f"{source}: tool_approvals 键不能为空白字符串")
    tool_approvals = {key: _LEGACY_TOOL_APPROVALS.get(value, value) for key, value in raw.items()}
    denied = sorted(key for key, value in tool_approvals.items() if value == "deny")
    if denied:
        logger.warning("%s: tool_approvals 旧档位 deny 仅按“该工具不挂载”处理，不再删除整台 server: %s", source, denied)
    allowed_values = VALID_TOOL_APPROVALS | {"deny"}
    invalid = sorted(value for value in tool_approvals.values() if value not in allowed_values)
    if invalid:
        raise MCPServerConfigError(
            f"{source}: tool_approvals 值必须是 {sorted(VALID_TOOL_APPROVALS)} 之一（兼容 auto/force_ask，deny 仅跳过该工具），非法值: {invalid}"
        )
    return tool_approvals


def parse_mcp_server_config(raw: object, *, source: str) -> MCPServerConfig:
    """解析并校验单条 MCP server 配置，非法抛 ``MCPServerConfigError``。"""
    if not isinstance(raw, dict):
        raise MCPServerConfigError(f"{source}: 配置必须是对象")
    server_id = str(raw.get("id") or raw.get("server_id") or "").strip()
    if not _SERVER_ID_PATTERN.fullmatch(server_id):
        raise MCPServerConfigError(f"{source}: id 非法（需匹配 {_SERVER_ID_PATTERN.pattern}）: {server_id!r}")
    transport, url = _parse_transport_fields(raw, source)
    raw_headers = _string_mapping_field(raw, "headers", source)
    tool_labels = _clean_tool_labels(_string_mapping_field(raw, "tool_labels", source), source)
    tool_approvals = _clean_tool_approvals(_string_mapping_field(raw, "tool_approvals", source), source)
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise MCPServerConfigError(f"{source}: enabled 必须是布尔值")
    return MCPServerConfig(
        server_id=server_id,
        transport=transport,
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
