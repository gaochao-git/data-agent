"""mcp_tool_discovery 残余分支补测：直调拒绝与兜底返回。"""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from apps.chat_data_app.agent.middleware import mcp_tool_discovery as discovery
from apps.chat_data_app.agent.middleware.mcp_tool_discovery import MCPToolDiscoveryMiddleware


def _request(tool_call=None):
    return SimpleNamespace(tool_call=tool_call)


def test_reject_direct_target_passthrough_when_search_inactive():
    middleware = MCPToolDiscoveryMiddleware(search_active=False)

    assert middleware._reject_direct_target(_request({"name": "mcp_sales", "id": "c1"})) is None


def test_reject_direct_target_requires_mapping_tool_call():
    middleware = MCPToolDiscoveryMiddleware(search_active=True)

    assert middleware._reject_direct_target(_request(None)) is None


def test_reject_direct_target_ignores_builtin_tool_name():
    middleware = MCPToolDiscoveryMiddleware(search_active=True)

    assert middleware._reject_direct_target(_request({"name": "file_read", "id": "c1"})) is None


def test_wrap_tool_call_rejects_direct_mcp_target_without_invoking_handler():
    middleware = MCPToolDiscoveryMiddleware(search_active=True)
    called = []
    request = _request({"name": "mcp_sales", "id": "call-1"})

    result = middleware.wrap_tool_call(request, lambda _req: called.append(True))

    assert called == []
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call-1"
    payload = json.loads(result.content)
    assert payload["success"] is False
    assert "mcp_tool_call" in payload["error"]


@pytest.mark.asyncio
async def test_awrap_tool_call_rejects_direct_mcp_target_without_invoking_handler():
    middleware = MCPToolDiscoveryMiddleware(search_active=True)
    called = []

    async def handler(_req):
        called.append(True)
        return ToolMessage(content="ok", tool_call_id="call-2")

    result = await middleware.awrap_tool_call(
        _request({"name": "mcp_sales", "id": "call-2"}),
        handler,
    )

    assert called == []
    assert isinstance(result, ToolMessage)
    assert result.status == "error"


def test_capture_discovery_returns_original_result_when_not_command_or_tool_message(monkeypatch):
    monkeypatch.setattr(discovery, "_names_from_tool_result", lambda _result: ["mcp_sales"])
    middleware = MCPToolDiscoveryMiddleware(search_active=True)
    plain = "plain-result"

    assert middleware._capture_discovery(plain) is plain
