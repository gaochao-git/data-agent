"""``agent/mcp`` 管理器单测：配置解析、枚举缓存、调用与健康检查。

用门面契约的假客户端（async with + ``list_tools`` / ``call_tool``）模拟
MCP server 响应，不需要真实进程/网络；真实传输（stdio/http/sse）仅做
配置解析校验，协议行为由 SDK 自身与真机回环探针覆盖。
"""

import json
import os
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.agent.mcp.config import (  # noqa: E402
    MCP_SERVERS_ENV,
    MCPServerConfig,
    MCPServerConfigError,
    load_mcp_server_configs,
    parse_mcp_server_config,
)
from apps.chat_data_app.agent.mcp.manager import (  # noqa: E402
    MCPToolInfo,
    MCPToolManager,
    build_langchain_tools,
    get_mcp_tool_display_name,
    reset_mcp_tool_manager,
    sanitize_mcp_tool_name,
)


def _tool_obj(name: str, description: str, schema: dict):
    """模拟 2.x ``list_tools`` 返回的工具条目（字段名 input_schema，camelCase 仅为序列化 alias）。"""
    return SimpleNamespace(name=name, description=description, input_schema=schema)


def _tool_obj_serialized_alias(name: str, description: str, schema: dict):
    """模拟反序列化后同时可按 alias 访问的条目，确保两个属性路径都兼容。"""
    return SimpleNamespace(name=name, description=description, input_schema=schema, inputSchema=schema)


def _list_result(tools, ttl_ms=None):
    """模拟 2.x ``tools/list`` 结果（含 CacheableResult 可选字段）。"""
    result = SimpleNamespace(tools=tools)
    if ttl_ms is not None:
        result.ttlMs = ttl_ms
    return result


def _call_result(text: str, *, is_error: bool = False, result_type: str | None = None):
    """模拟 ``call_tool`` 结果：content[].text + isError + resultType（MRTR）。"""
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        structuredContent=None,
        isError=is_error,
        resultType=result_type,
    )


def _fake_client(response_builder):
    class FakeClient:
        """门面契约假客户端：async with + list_tools/call_tool（无 initialize，门面内部协商）。"""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def list_tools(self):
            return response_builder()

        async def call_tool(self, name, arguments):
            if name == "echo":
                return _call_result(f"echo: {arguments.get('text', '')}")
            if name == "greet":
                return _call_result(f"{arguments.get('greeting', 'hello')}, {arguments.get('name', '')}")
            return _call_result(f"unknown tool: {name}")

    return FakeClient()


def _make_manager(*, config_loader=None, cache_ttl=60):
    opens = {"count": 0}

    @asynccontextmanager
    async def factory(config: MCPServerConfig):
        if config.server_id == "bad":
            raise RuntimeError("simulated connection failure")
        opens["count"] += 1
        yield _fake_client(
            lambda: _list_result(
                [
                    _tool_obj(
                        "echo",
                        "回显输入文本",
                        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                    ),
                    _tool_obj(
                        "greet",
                        "必选+可选参数",
                        {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "greeting": {"type": "string"}},
                            "required": ["name"],
                        },
                    ),
                ]
            )
        )

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


def test_parse_mcp_server_config_tool_labels_ok():
    config = parse_mcp_server_config(
        {
            "id": "srv",
            "transport": "stdio",
            "command": "x",
            "tool_labels": {" echo ": " 回显 ", "greet": "问候"},
        },
        source="env",
    )
    assert config.tool_labels == {"echo": "回显", "greet": "问候"}


@pytest.mark.parametrize(
    "tool_labels",
    [
        "not-a-dict",
        {"echo": 1},
        {1: "回显"},
        {"echo": "  "},
        {" ": "回显"},
    ],
)
def test_parse_mcp_server_config_rejects_invalid_tool_labels(tool_labels):
    with pytest.raises(MCPServerConfigError):
        parse_mcp_server_config(
            {"id": "ok", "transport": "stdio", "command": "x", "tool_labels": tool_labels},
            source="env",
        )


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
        json.dumps(
            [
                {"id": "good", "transport": "stdio", "command": "x"},
                {"id": "BAD ID", "transport": "stdio", "command": "x"},
            ]
        ),
    )
    configs = mcp_config._env_mcp_server_configs()
    assert [c.server_id for c in configs] == ["good"]


def test_env_config_disabled_entry_excluded(monkeypatch):
    monkeypatch.setenv(
        MCP_SERVERS_ENV,
        json.dumps(
            [
                {"id": "off", "transport": "stdio", "command": "x", "enabled": False},
                {"id": "on", "transport": "stdio", "command": "x"},
            ]
        ),
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
    # schema 必须从 input_schema 字段透传（回归：误取序列化 alias 会退化为空 properties）
    assert echo_info.input_schema["properties"] == {"text": {"type": "string"}}
    assert echo_info.input_schema["required"] == ["text"]
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
async def test_list_tools_async_labels_from_config_and_fallback():
    """tool_labels 命中原始工具名时透传 label，未配置的工具回退原始工具名。"""

    def config_loader():
        return [
            MCPServerConfig(
                server_id="srv",
                transport="stdio",
                command="x",
                tool_labels={"echo": "回显输入"},
            ),
        ]

    manager, _opens = _make_manager(config_loader=config_loader)
    tools = await manager.list_tools_async()
    by_qualified = {t.qualified_name: t for t in tools}
    assert by_qualified["mcp_srv_echo"].label == "回显输入"
    assert by_qualified["mcp_srv_greet"].label == "greet"


def test_dedupe_qualified_names_preserves_label():
    from apps.chat_data_app.agent.mcp.manager import _dedupe_qualified_names

    infos = [
        MCPToolInfo(server_id="s", name="t", qualified_name="mcp_s_t", description="", input_schema={}, label="甲"),
        MCPToolInfo(server_id="s", name="t", qualified_name="mcp_s_t", description="", input_schema={}, label="乙"),
    ]
    out = _dedupe_qualified_names(infos)
    assert [t.qualified_name for t in out] == ["mcp_s_t", "mcp_s_t_1"]
    assert [t.label for t in out] == ["甲", "乙"]


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
async def test_call_tool_async_marks_input_required_result():
    """MRTR（SEP-2322）：input_required 中间结果必须显式标记，不能被当成最终答案。"""

    @asynccontextmanager
    async def factory(config: MCPServerConfig):
        class InputRequiredClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def list_tools(self):
                return _list_result([])

            async def call_tool(self, name, arguments):
                return _call_result("请补充查询时间范围", result_type="input_required")

        yield InputRequiredClient()

    manager = MCPToolManager(
        tool_timeout_seconds=10,
        session_factory=factory,
        config_loader=lambda: [MCPServerConfig(server_id="srv", transport="stdio", command="x")],
    )
    result = await manager.call_tool_async("srv", "ask", {})
    assert result.startswith("[mcp tool input_required]")


@pytest.mark.asyncio
async def test_list_tools_async_adopts_server_ttl_hint():
    """2026-07-28 CacheableResult：服务端 ttlMs 提示应缩短本地缓存 TTL。"""

    @asynccontextmanager
    async def factory(config: MCPServerConfig):
        yield _fake_client(lambda: _list_result([], ttl_ms=30_000))

    manager = MCPToolManager(
        tool_timeout_seconds=10,
        cache_ttl_seconds=300,
        session_factory=factory,
        config_loader=lambda: [MCPServerConfig(server_id="srv", transport="stdio", command="x")],
    )
    await manager.list_tools_async()
    # 基础 TTL 不被永久改写；生效 TTL 取服务端提示换算的更短者
    assert manager._cache_ttl == 300
    assert manager._effective_cache_ttl == 30  # 30000ms → 30s


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


@pytest.mark.asyncio
async def test_build_langchain_tools_metadata_display_name():
    """label 随 StructuredTool.metadata 透传；无 label 的工具不下发 metadata。"""
    tools_info = [
        MCPToolInfo(
            server_id="srv",
            name="echo",
            qualified_name="mcp_srv_echo",
            description="回显输入文本",
            input_schema={"type": "object", "properties": {}},
            label="回显输入",
        ),
        MCPToolInfo(
            server_id="srv",
            name="raw",
            qualified_name="mcp_srv_raw",
            description="",
            input_schema={"type": "object", "properties": {}},
        ),
    ]

    async def _call(_server_id, _tool_name, _payload):
        return ""

    wrapped = build_langchain_tools(tools_info, call_tool_async=_call)
    by_name = {tool.name: tool for tool in wrapped}
    assert by_name["mcp_srv_echo"].metadata == {"display_name": "回显输入"}
    assert not by_name["mcp_srv_raw"].metadata


def test_get_mcp_tool_display_name_reads_singleton_cache():
    """展示名解析只读进程级缓存：命中返回 label，未命中/空名返回空串。"""
    manager = reset_mcp_tool_manager(tool_timeout_seconds=10, cache_ttl_seconds=60)
    try:
        manager._tools_cache = [
            MCPToolInfo(
                server_id="srv",
                name="echo",
                qualified_name="mcp_srv_echo",
                description="",
                input_schema={},
                label="回显输入",
            ),
        ]
        assert get_mcp_tool_display_name("mcp_srv_echo") == "回显输入"
        assert get_mcp_tool_display_name("mcp_srv_missing") == ""
        assert get_mcp_tool_display_name("") == ""
    finally:
        manager._tools_cache = None


# ============ 装配入口（零配置零开销） ============


def test_load_mcp_tools_empty_without_config(monkeypatch):
    from apps.chat_data_app.agent.mcp import manager as mcp_manager

    monkeypatch.setenv(MCP_SERVERS_ENV, "")
    assert mcp_manager.load_mcp_tools() == []
