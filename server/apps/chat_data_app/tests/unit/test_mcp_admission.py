"""MCP 准入检查测试：配置级（transport/https/host/凭据）+ 工具级（schema/数量/重名）。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from apps.chat_data_app.agent.mcp.admission import (
    MCP_ALLOW_STDIO_ENV,
    MCP_ALLOWED_HOSTS_ENV,
    MCP_MAX_TOOLS_ENV,
    MCP_REQUIRE_HTTPS_ENV,
    validate_mcp_server_config,
    validate_mcp_server_tools,
)
from apps.chat_data_app.agent.mcp.config import MCPServerConfig
from apps.chat_data_app.agent.mcp.manager import MCPToolManager


def _config(**overrides: Any) -> MCPServerConfig:
    base: dict[str, Any] = {"server_id": "srv", "transport": "streamable_http", "url": "https://mcp.example.com/mcp"}
    base.update(overrides)
    return MCPServerConfig(**base)


def _tool(name: str, schema: dict | None = None, description: str = "") -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": {}} if schema is None else schema,
    }


# ============ 配置级 ============


def test_stdio_rejected_by_default(monkeypatch):
    monkeypatch.delenv(MCP_ALLOW_STDIO_ENV, raising=False)
    report = validate_mcp_server_config(_config(transport="stdio", command="npx"))
    assert report.error_codes() == ["stdio_not_allowed"]


def test_stdio_allowed_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv(MCP_ALLOW_STDIO_ENV, "true")
    assert validate_mcp_server_config(_config(transport="stdio", command="npx")).ok


def test_stdio_requires_command(monkeypatch):
    monkeypatch.setenv(MCP_ALLOW_STDIO_ENV, "true")
    report = validate_mcp_server_config(_config(transport="stdio", command=""))
    assert "stdio_command_missing" in report.error_codes()


def test_stdio_command_control_chars_rejected(monkeypatch):
    monkeypatch.setenv(MCP_ALLOW_STDIO_ENV, "true")
    report = validate_mcp_server_config(_config(transport="stdio", command="npx\n--evil"))
    assert "stdio_command_invalid" in report.error_codes()


def test_remote_http_rejected_when_https_required(monkeypatch):
    monkeypatch.delenv(MCP_REQUIRE_HTTPS_ENV, raising=False)
    report = validate_mcp_server_config(_config(url="http://mcp.example.com/mcp"))
    assert "https_required" in report.error_codes()


def test_remote_loopback_http_allowed(monkeypatch):
    monkeypatch.delenv(MCP_REQUIRE_HTTPS_ENV, raising=False)
    assert validate_mcp_server_config(_config(url="http://127.0.0.1:3001/mcp")).ok
    assert validate_mcp_server_config(_config(url="http://localhost:3002/mcp")).ok


def test_remote_http_allowed_when_https_check_disabled(monkeypatch):
    monkeypatch.setenv(MCP_REQUIRE_HTTPS_ENV, "false")
    assert validate_mcp_server_config(_config(url="http://mcp.example.com/mcp")).ok


def test_url_credentials_rejected():
    report = validate_mcp_server_config(_config(url="https://user:pass@mcp.example.com/mcp"))
    assert "url_credentials_forbidden" in report.error_codes()


def test_url_missing_host_rejected():
    # urlsplit 对无 host 的相对写法返回空 host
    report = validate_mcp_server_config(_config(url="https:///mcp"))
    assert "url_host_missing" in report.error_codes()


def test_url_control_chars_rejected():
    report = validate_mcp_server_config(_config(url="https://mcp.example.com/mcp\nX"))
    assert "url_invalid" in report.error_codes()


def test_sse_transport_warns():
    report = validate_mcp_server_config(_config(transport="sse", url="https://mcp.example.com/sse"))
    assert report.ok
    assert [issue.code for issue in report.warnings] == ["sse_deprecated"]


def test_host_allowlist_allows_and_denies(monkeypatch):
    monkeypatch.setenv(MCP_ALLOWED_HOSTS_ENV, "mcp.example.com,*.trusted.io")
    assert validate_mcp_server_config(_config(url="https://mcp.example.com/mcp")).ok
    assert validate_mcp_server_config(_config(url="https://a.trusted.io/mcp")).ok
    denied = validate_mcp_server_config(_config(url="https://evil.example.net/mcp"))
    assert "host_not_allowed" in denied.error_codes()


def test_header_value_control_chars_rejected():
    report = validate_mcp_server_config(_config(headers={"X-Test": "a\nb"}))
    assert "header_invalid" in report.error_codes()


def test_headers_valid_pass():
    assert validate_mcp_server_config(_config(headers={"X-Test": "ok", "Authorization": "Bearer x"})).ok


def test_header_key_control_chars_rejected():
    report = validate_mcp_server_config(_config(headers={"X\nTest": "ok"}))
    assert "header_invalid" in report.error_codes()


def test_unknown_transport_skips_transport_checks():
    # 解析层已拦截未知 transport；准入函数对未知值不做传输断言（防御性放行）
    assert validate_mcp_server_config(_config(transport="unknown")).ok


# ============ 工具级 ============


def test_empty_tools_rejected():
    report = validate_mcp_server_tools("srv", [])
    assert report.error_codes() == ["no_tools"]


def test_too_many_tools_rejected(monkeypatch):
    monkeypatch.setenv(MCP_MAX_TOOLS_ENV, "2")
    report = validate_mcp_server_tools("srv", [_tool("a"), _tool("b"), _tool("c")])
    assert "too_many_tools" in report.error_codes()


def test_tool_name_missing_and_duplicate():
    report = validate_mcp_server_tools("srv", [_tool(""), _tool("a"), _tool("a")])
    codes = report.error_codes()
    assert "tool_name_invalid" in codes
    assert "duplicate_tool_name" in codes


def test_tool_name_too_long():
    report = validate_mcp_server_tools("srv", [_tool("x" * 200)])
    assert "tool_name_too_long" in report.error_codes()


def test_tool_schema_not_object():
    report = validate_mcp_server_tools("srv", [{"name": "a", "input_schema": "nope"}])
    assert "tool_schema_invalid" in report.error_codes()


def test_tool_schema_properties_not_object():
    report = validate_mcp_server_tools("srv", [_tool("a", {"type": "object", "properties": "nope"})])
    assert "tool_schema_invalid" in report.error_codes()


def test_tool_schema_property_not_object():
    report = validate_mcp_server_tools("srv", [_tool("a", {"type": "object", "properties": {"x": "nope"}})])
    assert "tool_schema_invalid" in report.error_codes()


def test_tool_schema_unknown_type_rejected():
    schema = {"type": "object", "properties": {"x": {"type": "geo"}}}
    report = validate_mcp_server_tools("srv", [_tool("a", schema)])
    assert "tool_schema_invalid" in report.error_codes()


def test_tool_schema_required_must_be_strings():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": [1]}
    report = validate_mcp_server_tools("srv", [_tool("a", schema)])
    assert "tool_schema_invalid" in report.error_codes()


def test_tool_schema_required_none_treated_as_absent():
    schema = {"type": "object", "properties": {}, "required": None}
    assert validate_mcp_server_tools("srv", [_tool("a", schema)]).ok


def test_tool_schema_none_treated_as_empty():
    report = validate_mcp_server_tools("srv", [{"name": "a", "input_schema": None}])
    assert report.ok


def test_tool_schema_properties_none_treated_as_empty():
    schema = {"type": "object", "properties": None}
    assert validate_mcp_server_tools("srv", [_tool("a", schema)]).ok


def test_tool_schema_required_undeclared_property_rejected():
    schema = {"type": "object", "properties": {}, "required": ["x"]}
    report = validate_mcp_server_tools("srv", [_tool("a", schema)])
    assert "tool_schema_invalid" in report.error_codes()


def test_tool_schema_too_large_rejected(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_MCP_MAX_TOOL_SCHEMA_BYTES", "10")
    report = validate_mcp_server_tools("srv", [_tool("a")])
    assert "tool_schema_too_large" in report.error_codes()


def test_tool_schema_unserializable_rejected():
    report = validate_mcp_server_tools("srv", [{"name": "a", "input_schema": {"x": object()}}])
    assert "tool_schema_invalid" in report.error_codes()


def test_tool_description_too_long_and_control_chars_warn(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_MCP_MAX_TOOL_DESCRIPTION_CHARS", "5")
    report = validate_mcp_server_tools("srv", [_tool("a", description="123456"), _tool("b", description="x\x01y")])
    codes = [issue.code for issue in report.warnings]
    assert "tool_description_too_long" in codes
    assert "tool_description_control_chars" in codes
    assert report.ok


def test_valid_tools_pass():
    report = validate_mcp_server_tools(
        "srv",
        [
            _tool(
                "echo",
                {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                description="回显",
            )
        ],
    )
    assert report.ok
    assert report.warnings == []


# ============ manager 集成：枚举后准入失败跳过该 server ============


def _make_manager_with_tools(tools):
    @asynccontextmanager
    async def factory(_config):
        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def list_tools(self):
                return SimpleNamespace(tools=tools)

        yield FakeClient()

    return MCPToolManager(
        tool_timeout_seconds=5,
        cache_ttl_seconds=60,
        session_factory=factory,
        config_loader=lambda: [MCPServerConfig(server_id="srv", transport="streamable_http", url="https://x/mcp")],
    )


@pytest.mark.asyncio
async def test_manager_skips_server_with_empty_tools():
    manager = _make_manager_with_tools([])
    assert await manager.list_tools_async() == []


@pytest.mark.asyncio
async def test_manager_keeps_server_with_valid_tools():
    manager = _make_manager_with_tools([SimpleNamespace(name="echo", description="回显", input_schema={})])
    infos = await manager.list_tools_async()
    assert [info.qualified_name for info in infos] == ["mcp_srv_echo"]


def test_report_summary_lists_error_codes():
    report = validate_mcp_server_tools("srv", [])
    assert json.dumps(report.error_codes()) == '["no_tools"]'
    assert "no_tools" in report.summary()
