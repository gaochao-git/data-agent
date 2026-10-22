"""MCP 工具探测的单元测试。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.mcp import probe
from apps.chat_data_app.agent.plugins import installer


def test_probe_mcp_server_tools_returns_summary(monkeypatch):
    class FakeClient:
        async def list_tools(self):
            return SimpleNamespace(
                tools=[SimpleNamespace(name="echo", description="回显"), SimpleNamespace(name="add", description=None)]
            )

    @asynccontextmanager
    async def fake_session(_config):
        yield FakeClient()

    monkeypatch.setattr(probe, "open_mcp_session", fake_session)
    result = asyncio.run(probe.probe_mcp_server_tools(SimpleNamespace()))
    assert result == [
        {"name": "echo", "description": "回显"},
        {"name": "add", "description": ""},
    ]


def test_installer_probe_tools_adds_approval(monkeypatch):
    entry = {
        "id": "demo",
        "transport": "streamable_http",
        "url": "https://mcp.example.com/mcp",
        "tool_approvals": {"echo": "allow"},
    }
    metadata = SimpleNamespace(manifest=SimpleNamespace(mcp_servers=(entry,)))
    monkeypatch.setattr(installer, "load_plugins_metadata", lambda: {"p": metadata})

    async def fake_probe(_config):
        return [{"name": "echo", "description": "回显"}, {"name": "add", "description": "加法"}]

    monkeypatch.setattr(installer, "_probe_mcp_server_tools", fake_probe)
    result = asyncio.run(installer.probe_mcp_server_tools("p", "demo"))
    assert result["tools"][0]["approval"] == "allow"
    assert result["tools"][1]["approval"] == "ask"


def test_installer_probe_rejects_unknown_plugin_and_server(monkeypatch):
    monkeypatch.setattr(installer, "load_plugins_metadata", lambda: {})
    with pytest.raises(ValueError, match="插件不存在"):
        asyncio.run(installer.probe_mcp_server_tools("missing", "demo"))

    entry = {"id": "demo", "transport": "streamable_http", "url": "https://mcp.example.com/mcp"}
    metadata = SimpleNamespace(manifest=SimpleNamespace(mcp_servers=(entry,)))
    monkeypatch.setattr(installer, "load_plugins_metadata", lambda: {"p": metadata})
    with pytest.raises(ValueError, match="未声明"):
        asyncio.run(installer.probe_mcp_server_tools("p", "missing"))
