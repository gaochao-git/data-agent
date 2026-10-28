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

from ...services.runtime_config import read_positive_int_env
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

# 每工具审批档位（B 端固定策略）：ask=执行前必须人工确认（缺省）；allow=无需人工确认。
VALID_TOOL_APPROVALS = frozenset({"ask", "allow"})
DEFAULT_TOOL_APPROVAL = "ask"
TOOL_POLICY_KEYS = frozenset({"label", "approval", "timeout_ms"})
MCP_TOOL_LABEL_MAX_LENGTH = 100
# 工具级超时（毫秒）：低于下限视为非法；高于上限截到上限。
MCP_TOOL_TIMEOUT_MIN_MS = 1_000
MCP_TOOL_TIMEOUT_MAX_MS = 30 * 60 * 1_000
MCP_TOOL_TIMEOUT_ENV = "CHAT_DATA_MCP_TOOL_TIMEOUT_SECONDS"


def default_mcp_tool_timeout_ms() -> int:
    """全局默认 MCP 工具超时（毫秒）；与 ``MCPToolManager`` 共用同一 env 口径。"""
    return read_positive_int_env(MCP_TOOL_TIMEOUT_ENV, 60) * 1_000


@dataclass(frozen=True)
class MCPToolPolicy:
    """单个 MCP 工具的用户可配策略（解析后会补齐默认值）。"""

    label: str = ""
    approval: str = DEFAULT_TOOL_APPROVAL
    timeout_ms: int | None = None


@dataclass(frozen=True)
class MCPServerConfig:
    """单个 MCP server 的连接配置与工具策略。"""

    server_id: str
    transport: str  # streamable_http
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    # MCP 原始工具名 → 工具策略（label/approval/timeout_ms）；未配置的工具走默认值。
    tools: dict[str, MCPToolPolicy] = field(default_factory=dict)
    enabled: bool = True

    def resolve_tool(self, tool_name: str, global_timeout_ms: int) -> MCPToolPolicy:
        """返回工具最终生效策略：工具配置 > 全局默认。"""
        policy = self.tools.get(str(tool_name))
        name = str(tool_name or "").strip()
        return MCPToolPolicy(
            label=(policy.label if policy else "") or name,
            approval=policy.approval if policy else DEFAULT_TOOL_APPROVAL,
            timeout_ms=(policy.timeout_ms if policy and policy.timeout_ms else None) or global_timeout_ms,
        )

    def max_tool_timeout_ms(self, global_timeout_ms: int) -> int:
        """该 server 下最长工具超时；用于设置 session read timeout，避免连接层先超时。"""
        values = [global_timeout_ms]
        values.extend(policy.timeout_ms for policy in self.tools.values() if policy.timeout_ms)
        return max(values)


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


def _parse_tool_label(raw_value: object, source: str, tool_name: str) -> str:
    if not isinstance(raw_value, str):
        raise MCPServerConfigError(f"{source}: tools.{tool_name}.label 必须是字符串")
    label = raw_value.strip()
    if len(label) > MCP_TOOL_LABEL_MAX_LENGTH:
        raise MCPServerConfigError(f"{source}: tools.{tool_name}.label 不能超过 {MCP_TOOL_LABEL_MAX_LENGTH} 字符")
    return label


def _parse_tool_approval(raw_value: object, source: str, tool_name: str) -> str:
    approval = str(raw_value if raw_value is not None else DEFAULT_TOOL_APPROVAL).strip().lower()
    if approval not in VALID_TOOL_APPROVALS:
        raise MCPServerConfigError(
            f"{source}: tools.{tool_name}.approval 必须是 {sorted(VALID_TOOL_APPROVALS)} 之一，当前: {approval!r}"
        )
    return approval


def _parse_tool_timeout(raw_value: object, source: str, tool_name: str) -> int | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise MCPServerConfigError(f"{source}: tools.{tool_name}.timeout_ms 必须是正整数")
    if raw_value < MCP_TOOL_TIMEOUT_MIN_MS:
        raise MCPServerConfigError(f"{source}: tools.{tool_name}.timeout_ms 不能小于 {MCP_TOOL_TIMEOUT_MIN_MS}ms")
    return min(raw_value, MCP_TOOL_TIMEOUT_MAX_MS)


def _parse_tool_policy(tool_name: object, value: object, source: str) -> tuple[str, MCPToolPolicy]:
    name = str(tool_name or "").strip()
    if not name:
        raise MCPServerConfigError(f"{source}: tools 的工具名不能为空白字符串")
    if not isinstance(value, dict):
        raise MCPServerConfigError(f"{source}: tools.{name} 必须是对象")
    unknown = sorted(set(value) - TOOL_POLICY_KEYS)
    if unknown:
        raise MCPServerConfigError(f"{source}: tools.{name} 包含未知字段 {unknown}")
    return name, MCPToolPolicy(
        label=_parse_tool_label(value.get("label", ""), source, name),
        approval=_parse_tool_approval(value.get("approval", DEFAULT_TOOL_APPROVAL), source, name),
        timeout_ms=_parse_tool_timeout(value.get("timeout_ms"), source, name),
    )


def _parse_tools(raw: object, source: str) -> dict[str, MCPToolPolicy]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise MCPServerConfigError(f"{source}: tools 必须是对象")
    return dict(_parse_tool_policy(name, value, source) for name, value in raw.items())


def parse_mcp_server_config(raw: object, *, source: str) -> MCPServerConfig:
    """解析并校验单条 MCP server 配置，非法抛 ``MCPServerConfigError``。"""
    if not isinstance(raw, dict):
        raise MCPServerConfigError(f"{source}: 配置必须是对象")
    for legacy_key in ("tool_labels", "tool_approvals"):
        if legacy_key in raw:
            raise MCPServerConfigError(
                f"{source}: {legacy_key} 已废弃，请改用 tools: {{<工具名>: {{label/approval/timeout_ms}}}}"
            )
    server_id = str(raw.get("id") or raw.get("server_id") or "").strip()
    if not _SERVER_ID_PATTERN.fullmatch(server_id):
        raise MCPServerConfigError(f"{source}: id 非法（需匹配 {_SERVER_ID_PATTERN.pattern}）: {server_id!r}")
    transport, url = _parse_transport_fields(raw, source)
    raw_headers = _string_mapping_field(raw, "headers", source)
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise MCPServerConfigError(f"{source}: enabled 必须是布尔值")
    return MCPServerConfig(
        server_id=server_id,
        transport=transport,
        url=url,
        headers=dict(raw_headers),
        tools=_parse_tools(raw.get("tools"), source),
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
    # 通过端口读取插件声明，避免 mcp -> plugins 的包级依赖/环。
    from ..contracts.mcp_server_source import get_mcp_server_source

    source = get_mcp_server_source()
    try:
        plugin_entries = source.enabled_plugin_mcp_servers() if source is not None else []
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
    "DEFAULT_TOOL_APPROVAL",
    "MCP_TOOL_TIMEOUT_ENV",
    "MCP_TOOL_TIMEOUT_MAX_MS",
    "MCP_TOOL_TIMEOUT_MIN_MS",
    "MCPToolPolicy",
    "MCPServerConfig",
    "MCPServerConfigError",
    "VALID_MCP_TRANSPORTS",
    "VALID_TOOL_APPROVALS",
    "default_mcp_tool_timeout_ms",
    "load_mcp_server_configs",
    "parse_mcp_server_config",
]
