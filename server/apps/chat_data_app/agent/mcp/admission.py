"""MCP server 准入检查：不满足要求的 server 不枚举、不挂载、不可被检索。

分两级（与 ``mcp/config.py``、``mcp/manager.py`` 的两段生命周期对应）：

1. ``validate_mcp_server_config``：配置级、无网络。``load_mcp_server_configs``
   逐条校验 env / 插件声明，硬错误跳过该 server（其它 server 不受影响）；
2. ``validate_mcp_server_tools``：``tools/list`` 之后、挂载之前。空工具面、
   非法/超大 schema、工具数超限等硬错误拒绝该 server；描述过长等只告警。

设计口径：
- **只允许 streamable_http**：stdio / sse 不做运行时兼容；远程默认必须 https
  （loopback 例外，兼容本地诊断 server）。
- 可选 host 白名单：``CHAT_DATA_MCP_ALLOWED_HOSTS`` 逗号分隔，支持 ``*.example.com``。
- 硬错误 = 不支持（跳过并说明原因），不靠审批兜底；审批是运行时动作级策略，
  不能掩盖"这个 server 本身不该接"。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ...services.runtime_config import read_bool_env, read_positive_int_env

MCP_REQUIRE_HTTPS_ENV = "CHAT_DATA_MCP_REQUIRE_HTTPS"
MCP_ALLOWED_HOSTS_ENV = "CHAT_DATA_MCP_ALLOWED_HOSTS"
MCP_MAX_TOOLS_ENV = "CHAT_DATA_MCP_MAX_TOOLS_PER_SERVER"
MCP_MAX_TOOL_SCHEMA_BYTES_ENV = "CHAT_DATA_MCP_MAX_TOOL_SCHEMA_BYTES"
MCP_MAX_TOOL_DESCRIPTION_CHARS_ENV = "CHAT_DATA_MCP_MAX_TOOL_DESCRIPTION_CHARS"

_DEFAULT_MAX_TOOLS = 256
_DEFAULT_MAX_TOOL_SCHEMA_BYTES = 64 * 1024
_DEFAULT_MAX_TOOL_DESCRIPTION_CHARS = 4000
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
_NAME_MAX_LENGTH = 128
_ALLOWED_JSON_SCHEMA_TYPES = frozenset({"string", "integer", "number", "boolean", "array", "object", "null"})


class MCPAdmissionError(ValueError):
    """MCP server 未通过准入（硬错误），调用方应跳过/拒绝该 server。"""


@dataclass(frozen=True)
class AdmissionIssue:
    """一条准入结论：level=error 表示不支持，level=warning 表示降级/提示。"""

    level: str
    code: str
    message: str


@dataclass
class AdmissionReport:
    """准入报告：``ok`` 为 True 表示可接入（warning 可接受）。"""

    issues: list[AdmissionIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[AdmissionIssue]:
        return [issue for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> list[AdmissionIssue]:
        return [issue for issue in self.issues if issue.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, level: str, code: str, message: str) -> None:
        self.issues.append(AdmissionIssue(level=level, code=code, message=message))

    def error_codes(self) -> list[str]:
        return [issue.code for issue in self.errors]

    def summary(self) -> str:
        return "；".join(f"{issue.code}: {issue.message}" for issue in self.errors) or "通过"


def _host_allowlist() -> tuple[str, ...]:
    raw = str(os.getenv(MCP_ALLOWED_HOSTS_ENV) or "").strip()
    if not raw:
        return ()
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


def _host_allowed(host: str, allowlist: tuple[str, ...]) -> bool:
    if not allowlist:
        return True
    host = host.lower()
    return any(host == entry or (entry.startswith("*.") and host.endswith(entry[1:])) for entry in allowlist)


def _is_loopback(host: str) -> bool:
    return host.lower().rstrip(".") in _LOOPBACK_HOSTS


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 0x20 or ch == "\x7f" for ch in value)


def _validate_remote_config(report: AdmissionReport, config: Any, transport: str, server_id: str) -> None:
    url = str(getattr(config, "url", "") or "")
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.username or parsed.password:
        report.add("error", "url_credentials_forbidden", f"URL 不允许内嵌凭据: {server_id}")
    if not host:
        report.add("error", "url_host_missing", f"远程传输 URL 缺少 host: {server_id}")
    elif _has_control_chars(url):
        report.add("error", "url_invalid", f"URL 含控制字符: {server_id}")
    else:
        if read_bool_env(MCP_REQUIRE_HTTPS_ENV, True) and parsed.scheme != "https" and not _is_loopback(host):
            report.add(
                "error",
                "https_required",
                f"远程 MCP 必须使用 https（loopback 例外），当前: {parsed.scheme}://{host}",
            )
        if not _host_allowed(host, _host_allowlist()):
            report.add("error", "host_not_allowed", f"host 不在 {MCP_ALLOWED_HOSTS_ENV} 白名单内: {host}")


def _validate_headers(report: AdmissionReport, config: Any) -> None:
    for key, value in (getattr(config, "headers", None) or {}).items():
        if _has_control_chars(str(key)) or _has_control_chars(str(value)):
            report.add("error", "header_invalid", f"headers 含控制字符（防头注入）: {key}")


def validate_mcp_server_config(config: Any) -> AdmissionReport:
    """配置级准入（无网络）：transport、https、URL 凭据、host 白名单、控制字符。"""
    report = AdmissionReport()
    server_id = str(getattr(config, "server_id", "") or "")
    transport = str(getattr(config, "transport", "") or "")
    if transport != "streamable_http":
        report.add(
            "error",
            "transport_not_supported",
            f"只支持 streamable_http，当前: {transport or '(空)'}: {server_id}",
        )
        return report
    _validate_remote_config(report, config, transport, server_id)
    _validate_headers(report, config)
    return report


def _validate_tool_name(report: AdmissionReport, name: str, seen: set[str], server_id: str) -> bool:
    """校验并登记工具名；空名返回 False（调用方跳过该工具）。"""
    if not name:
        report.add("error", "tool_name_invalid", f"存在空工具名: {server_id}")
        return False
    if len(name) > _NAME_MAX_LENGTH:
        report.add("error", "tool_name_too_long", f"工具名超过 {_NAME_MAX_LENGTH} 字符: {name}")
    if name in seen:
        report.add("error", "duplicate_tool_name", f"工具名重复: {name}")
    seen.add(name)
    return True


def _validate_schema_properties(report: AdmissionReport, name: str, properties: dict) -> None:
    for prop_name, prop in properties.items():
        if not isinstance(prop, dict):
            report.add("error", "tool_schema_invalid", f"工具 {name} 的参数 {prop_name} 不是对象")
            continue
        prop_type = prop.get("type")
        if prop_type is not None and str(prop_type) not in _ALLOWED_JSON_SCHEMA_TYPES:
            report.add(
                "error",
                "tool_schema_invalid",
                f"工具 {name} 的参数 {prop_name} 类型不支持: {prop_type}",
            )


def _validate_schema_required(report: AdmissionReport, name: str, schema: dict, properties: dict) -> None:
    required = schema.get("required", [])
    if required is None:
        return
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        report.add("error", "tool_schema_invalid", f"工具 {name} 的 required 必须是字符串数组")
    elif any(item not in properties for item in required):
        report.add("error", "tool_schema_invalid", f"工具 {name} 的 required 含未声明参数")


def _validate_tool_schema(report: AdmissionReport, name: str, schema: Any, max_schema_bytes: int) -> None:
    if schema is None:
        schema = {}
    if not isinstance(schema, dict):
        report.add("error", "tool_schema_invalid", f"工具 {name} 的 input schema 不是对象")
        return
    try:
        schema_bytes = len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        report.add("error", "tool_schema_invalid", f"工具 {name} 的 input schema 不可序列化")
        return
    if schema_bytes > max_schema_bytes:
        report.add(
            "error",
            "tool_schema_too_large",
            f"工具 {name} 的 schema {schema_bytes}B 超过上限 {max_schema_bytes}B",
        )
    properties = schema.get("properties", {})
    if properties is None:
        properties = {}
    if not isinstance(properties, dict):
        report.add("error", "tool_schema_invalid", f"工具 {name} 的 properties 不是对象")
        return
    _validate_schema_properties(report, name, properties)
    _validate_schema_required(report, name, schema, properties)


def _validate_tool_description(report: AdmissionReport, name: str, description: str, max_description: int) -> None:
    if len(description) > max_description:
        report.add(
            "warning",
            "tool_description_too_long",
            f"工具 {name} 描述 {len(description)} 字符超过上限 {max_description}，将按截断处理",
        )
    if _has_control_chars(description):
        report.add("warning", "tool_description_control_chars", f"工具 {name} 描述含控制字符")


def validate_mcp_server_tools(server_id: str, tools: list[dict[str, Any]]) -> AdmissionReport:
    """工具级准入（tools/list 之后）：空工具面、重名、非法/超大 schema、数量上限。"""
    report = AdmissionReport()
    if not tools:
        report.add("error", "no_tools", f"tools/list 为空，无法接入: {server_id}")
        return report
    max_tools = read_positive_int_env(MCP_MAX_TOOLS_ENV, _DEFAULT_MAX_TOOLS)
    if len(tools) > max_tools:
        report.add(
            "error",
            "too_many_tools",
            f"工具数 {len(tools)} 超过上限 {max_tools}（{MCP_MAX_TOOLS_ENV}）: {server_id}",
        )
    max_schema_bytes = read_positive_int_env(MCP_MAX_TOOL_SCHEMA_BYTES_ENV, _DEFAULT_MAX_TOOL_SCHEMA_BYTES)
    max_description = read_positive_int_env(MCP_MAX_TOOL_DESCRIPTION_CHARS_ENV, _DEFAULT_MAX_TOOL_DESCRIPTION_CHARS)
    seen: set[str] = set()
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        if not _validate_tool_name(report, name, seen, server_id):
            continue
        _validate_tool_schema(report, name, tool.get("input_schema"), max_schema_bytes)
        _validate_tool_description(report, name, str(tool.get("description") or ""), max_description)
    return report


__all__ = [
    "MCPAdmissionError",
    "AdmissionIssue",
    "AdmissionReport",
    "validate_mcp_server_config",
    "validate_mcp_server_tools",
]
