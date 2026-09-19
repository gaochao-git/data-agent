"""``agent/mcp`` 管理器单测：配置解析、枚举缓存、调用与健康检查。

用 mcp SDK 的内存会话（``create_connected_server_and_client_session``）模拟
MCP server，不需要真实进程/网络；真实传输（stdio/http/sse）仅做配置解析校验。
"""

import json
import os
import sys
from contextlib import asynccontextmanager

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from apps.chat_data_app.agent.mcp.config import (  # noqa: E402
    MCP_SERVERS_ENV,
    MCPServerConfig,
    MCPServerConfigError,
    load_mcp_server_configs,
    parse_mcp_server_config,
)
from apps.chat_data_app.agent.mcp.manager import (  # noqa: E402
    MCPToolManager,
    build_langchain_tools,
    sanitize_mcp_tool_name,
)

_test_server = FastMCP("test-server")


@_test_server.tool(description="回显输入文本")
def echo(text: str) -> str:
    return f"echo: {text}"


@_test_server.tool(description="必选+可选参数")
def greet(name: str, greeting: str = "hello") -> str:
    return f"{greeting}, {name}"


def _make_manager(*, config_loader=None, cache_ttl=60):
    opens = {"count": 0}

    @asynccontextmanager
    async def factory(config: MCPServerConfig):
        if config.server_id == "bad":
            raise RuntimeError("simulated connection failure")
        opens["count"] += 1
        async with create_connected_server_and_client_session(_test_server, raise_exceptions=True) as session:
            yield session

    manager = MCPToolManager(
        tool_timeout_seconds=10,
        cache_ttl_seconds=cache_ttl,
        session_factory=factory,
        config_loader=config_loader or (lambda: [MCPServerConfig(server_id="srv", transport="stdio", command="x")]),
    )
    return manager, opens


# ============ 配置解析 ============


def test_parse_mcp_server_config_stdio_ok():
    config = parse_mcp_server_config(
        {"id": "fs", "transport": "stdio", "command": "npx", "args": ["-y", "srv"], "env": {"A": "1"}},
        source="env",
    )
    assert config.server_id == "fs"
    assert config.transport == "stdio"
    assert config.args == ("-y", "srv")
    assert config.env == {"A": "1"}
    assert config.enabled is True


def test_parse_mcp_server_config_http_ok():
    config = parse_mcp_server_config(
        {"id": "remote", "transport": "streamable_http", "url": "https://mcp.example.com/mcp", "headers": {"k": "v"}},
        source="env",
    )
    assert config.url == "https://mcp.example.com/mcp"
    assert config.headers == {"k": "v"}


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-dict",
        {},
        {"id": "bad id!", "transport": "stdio", "command": "x"},
        {"id": "ok", "transport": "websocket", "command": "x"},
        {"id": "ok", "transport": "stdio"},
        {"id": "ok", "transport": "sse"},
        {"id": "ok", "transport": "sse", "url": "ftp://x"},
        {"id": "ok", "transport": "stdio", "command": "x", "args": "no"},
        {"id": "ok", "transport": "stdio", "command": "x", "env": {"a": 1}},
        {"id": "ok", "transport": "stdio", "command": "x", "enabled": "yes"},
    ],
)
def test_parse_mcp_server_config_rejects_invalid(raw):
    with pytest.raises(MCPServerConfigError):
        parse_mcp_server_config(raw, source="env")


def test_env_config_invalid_json_is_ignored(monkeypatch):
    from apps.chat_data_app.agent.mcp import config as mcp_config

    monkeypatch.setenv(MCP_SERVERS_ENV, "{not json")
    assert mcp_config._env_mcp_server_configs() == []
    monkeypatch.setenv(MCP_SERVERS_ENV, '{"id": "single-object"}')
    assert mcp_config._env_mcp_server_configs() == []


def test_env_config_invalid_entry_skipped_valid_kept(monkeypatch):
    from apps.chat_data_app.agent.mcp import config as mcp_config

    monkeypatch.setenv(
        MCP_SERVERS_ENV,
        json.dumps([
            {"id": "good", "transport": "stdio", "command": "x"},
            {"id": "BAD ID", "transport": "stdio", "command": "x"},
        ]),
    )
    configs = mcp_config._env_mcp_server_configs()
    assert [c.server_id for c in configs] == ["good"]


def test_env_config_disabled_entry_excluded(monkeypatch):
    monkeypatch.setenv(
        MCP_SERVERS_ENV,
        json.dumps([
            {"id": "off", "transport": "stdio", "command": "x", "enabled": False},
            {"id": "on", "transport": "stdio", "command": "x"},
        ]),
    )
    configs = load_mcp_server_configs()
    assert [c.server_id for c in configs] == ["on"]


# ============ 工具名 ============


def test_sanitize_mcp_tool_name():
    assert sanitize_mcp_tool_name("srv", "read_file") == "mcp_srv_read_file"
    assert sanitize_mcp_tool_name("中文", "读文件") == "mcp_______"
    long = sanitize_mcp_tool_name("s" * 80, "t" * 80)
    assert len(long) <= 64
    assert long.startswith("mcp_")
    assert sanitize_mcp_tool_name("srv", "1tool") == "mcp_srv_1tool"


# ============ 枚举 / 调用 / 健康检查 ============


@pytest.mark.asyncio
async def test_list_tools_async_enumerates_and_caches():
    manager, opens = _make_manager()
    tools = await manager.list_tools_async()
    assert opens["count"] == 1
    names = {tool.qualified_name for tool in tools}
    assert "mcp_srv_echo" in names
    assert "mcp_srv_greet" in names
    echo_info = next(t for t in tools if t.qualified_name == "mcp_srv_echo")
    assert echo_info.server_id == "srv"
    assert echo_info.name == "echo"
    assert "回显" in echo_info.description
    # 命中缓存不再打开会话
    again = await manager.list_tools_async()
    assert opens["count"] == 1
    assert {t.qualified_name for t in again} == names
    # 强刷重新枚举
    await manager.list_tools_async(force_refresh=True)
    assert opens["count"] == 2


@pytest.mark.asyncio
async def test_list_tools_async_fail_soft_on_bad_server():
    def config_loader():
        return [
            MCPServerConfig(server_id="srv", transport="stdio", command="x"),
            MCPServerConfig(server_id="bad", transport="stdio", command="x"),
        ]

    manager, _opens = _make_manager(config_loader=config_loader)
    tools = await manager.list_tools_async()
    assert {t.server_id for t in tools} == {"srv"}


@pytest.mark.asyncio
async def test_list_tools_async_dedupes_colliding_qualified_names():
    def config_loader():
        return [MCPServerConfig(server_id="s" * 80, transport="stdio", command="x")]

    manager, _opens = _make_manager(config_loader=config_loader)
    tools = await manager.list_tools_async()
    qualified = [t.qualified_name for t in tools]
    assert len(qualified) == len(set(qualified))


@pytest.mark.asyncio
async def test_call_tool_async_returns_text():
    manager, _opens = _make_manager()
    result = await manager.call_tool_async("srv", "echo", {"text": "hi"})
    assert result == "echo: hi"


@pytest.mark.asyncio
async def test_call_tool_async_optional_param_defaults():
    manager, _opens = _make_manager()
    result = await manager.call_tool_async("srv", "greet", {"name": "world"})
    assert result == "hello, world"


@pytest.mark.asyncio
async def test_call_tool_async_unknown_server_raises():
    manager, _opens = _make_manager()
    with pytest.raises(ValueError, match="MCP server 未配置"):
        await manager.call_tool_async("nope", "echo", {})


@pytest.mark.asyncio
async def test_check_health_async():
    manager, _opens = _make_manager()
    assert await manager.check_health_async("srv") is True
    assert await manager.check_health_async("bad") is False
    assert await manager.check_health_async("missing") is False


def test_sync_bridge_list_tools():
    """同步入口（装配路径）：无事件循环时直接 asyncio.run。"""
    manager, _opens = _make_manager()
    tools = manager.list_tools()
    assert {t.qualified_name for t in tools} >= {"mcp_srv_echo", "mcp_srv_greet"}


# ============ LangChain 包装 ============


@pytest.mark.asyncio
async def test_build_langchain_tools_schema_and_call():
    manager, _opens = _make_manager()
    tools_info = await manager.list_tools_async(force_refresh=True)
    wrapped = build_langchain_tools(tools_info, call_tool_async=manager.call_tool_async)
    by_name = {tool.name: tool for tool in wrapped}
    assert set(by_name) == {"mcp_srv_echo", "mcp_srv_greet"}
    echo_tool = by_name["mcp_srv_echo"]
    schema = echo_tool.args_schema.model_json_schema()
    assert schema["properties"]["text"]["type"] == "string"
    assert "text" in schema.get("required", [])
    greet_tool = by_name["mcp_srv_greet"]
    greet_schema = greet_tool.args_schema.model_json_schema()
    assert "greeting" not in greet_schema.get("required", [])
    result = await echo_tool.coroutine(text="hello")
    assert result == "echo: hello"


# ============ 装配入口（零配置零开销） ============


def test_load_mcp_tools_empty_without_config(monkeypatch):
    from apps.chat_data_app.agent.mcp import manager as mcp_manager

    monkeypatch.setenv(MCP_SERVERS_ENV, "")
    assert mcp_manager.load_mcp_tools() == []
