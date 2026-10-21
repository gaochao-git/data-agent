"""``agent/middleware/mcp_tool_discovery`` 单测：发现纯函数与过滤/捕获分支。"""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from apps.chat_data_app.agent.mcp.catalog import MCP_TOOL_SEARCH_TOOL_NAME
from apps.chat_data_app.agent.middleware.mcp_tool_discovery import (
    MCPToolDiscoveryMiddleware,
    _discovered_from_history,
    _discovered_from_state,
    _names_from_search_content,
)


def _tool(name: str):
    return SimpleNamespace(name=name)


def _search_content(names: list[str]) -> str:
    return json.dumps({"results": [{"name": name} for name in names]})


def _search_tool_message(content: str, *, status: str = "success") -> ToolMessage:
    return ToolMessage(content=content, name=MCP_TOOL_SEARCH_TOOL_NAME, tool_call_id="call-1", status=status)


class _FakeRequest:
    """带 override 的假 ModelRequest。"""

    def __init__(self, *, tools=None, state=None, messages=None, tool_call=None):
        self.tools = tools
        self.state = state
        self.messages = messages
        self.tool_call = tool_call
        self.overridden = None

    def override(self, **kwargs):
        self.overridden = kwargs
        return self


# ---- 纯函数：_discovered_from_state / _names_from_search_content / _discovered_from_history ----


def test_discovered_from_state_rejects_non_dict():
    assert _discovered_from_state(None) == []
    assert _discovered_from_state("not-dict") == []


def test_discovered_from_state_reads_names():
    assert _discovered_from_state({"discovered_tool_names": ["mcp_a", "mcp_b"]}) == ["mcp_a", "mcp_b"]


def test_names_from_search_content_rejects_non_string():
    assert _names_from_search_content(None) == []
    assert _names_from_search_content(b"bytes") == []


def test_names_from_search_content_rejects_invalid_json():
    assert _names_from_search_content("not-json") == []


def test_names_from_search_content_rejects_non_dict_payload():
    assert _names_from_search_content(json.dumps([1, 2, 3])) == []


def test_names_from_search_content_extracts_names_and_skips_invalid_items():
    payload = json.dumps({"results": [{"name": "mcp_a"}, {"no_name": 1}, "junk", {"name": ""}]})
    assert _names_from_search_content(payload) == ["mcp_a"]


def test_discovered_from_history_rejects_non_list():
    assert _discovered_from_history("not-list") == []


def test_discovered_from_history_collects_search_tool_messages():
    hit = _search_tool_message(_search_content(["mcp_a"]))
    other = ToolMessage(content="x", name="other_tool", tool_call_id="call-2")
    assert _discovered_from_history([hit, other, "junk"]) == ["mcp_a"]


# ---- _filter_request：search_active / 无 MCP 工具 / 全部已发现 / 过滤 ----


def _middleware(search_active=True):
    return MCPToolDiscoveryMiddleware(search_active=search_active)


def test_filter_request_passthrough_when_search_inactive():
    request = _FakeRequest(tools=[_tool("mcp_secret")])
    result = _middleware(search_active=False)._filter_request(request)
    assert result is request


def test_filter_request_passthrough_without_mcp_tools():
    request = _FakeRequest(tools=[_tool("file_read")])
    result = _middleware()._filter_request(request)
    assert result is request


def test_filter_request_does_not_expose_previous_discovery():
    request = _FakeRequest(
        tools=[_tool("mcp_known"), _tool("file_read")],
        state={"mcp_run_discovered_tool_names": ["mcp_known"]},
    )
    result = _middleware()._filter_request(request)
    assert result.overridden is not None
    assert [tool.name for tool in result.overridden["tools"]] == ["file_read"]


def test_filter_request_hides_undiscovered_mcp_tools():
    request = _FakeRequest(
        tools=[_tool("mcp_known"), _tool("mcp_hidden"), _tool("file_read")],
        state={"mcp_run_discovered_tool_names": ["mcp_known"]},
    )
    result = _middleware()._filter_request(request)
    assert result.overridden is not None
    names = [tool.name for tool in result.overridden["tools"]]
    assert names == ["file_read"]


def test_filter_request_keeps_always_mounted_without_state():
    request = _FakeRequest(tools=[_tool("mcp_always"), _tool("mcp_hidden"), _tool("file_read")])
    result = _middleware()._filter_request(request)
    names = [tool.name for tool in result.overridden["tools"]]
    assert names == ["file_read"]


# ---- wrap_tool_call / awrap_tool_call：检索捕获与非检索直通 ----


@pytest.mark.asyncio
async def test_wrap_tool_call_passthrough_for_non_search_request():
    request = _FakeRequest(tool_call={"name": "file_read"})
    passthrough = ToolMessage(content="plain", tool_call_id="call-3")
    result = _middleware().wrap_tool_call(request, lambda _req: passthrough)
    assert result is passthrough


def test_wrap_tool_call_captures_search_hit_into_command():
    request = _FakeRequest(tool_call={"name": MCP_TOOL_SEARCH_TOOL_NAME})
    message = _search_tool_message(_search_content(["mcp_a"]))
    result = _middleware().wrap_tool_call(request, lambda _req: message)
    assert isinstance(result, Command)
    assert result.update["discovered_tool_names"] == ["mcp_a"]


@pytest.mark.asyncio
async def test_awrap_tool_call_merges_names_into_command_result():
    request = _FakeRequest(tool_call={"name": MCP_TOOL_SEARCH_TOOL_NAME})
    message = _search_tool_message(_search_content(["mcp_b"]))
    existing = Command(
        update={
            "messages": [message],
            "discovered_tool_names": ["mcp_a"],
        }
    )

    async def handler(_req):
        return existing

    result = await _middleware().awrap_tool_call(request, handler)
    assert isinstance(result, Command)
    assert result.update["discovered_tool_names"] == ["mcp_a", "mcp_b"]


def test_capture_discovery_ignores_empty_names():
    _request = _FakeRequest(tool_call={"name": MCP_TOOL_SEARCH_TOOL_NAME})
    message = _search_tool_message(_search_content([]))
    result = _middleware()._capture_discovery(message)
    assert result is message


def test_search_result_parser_handles_command_without_message_and_plain_result():
    from apps.chat_data_app.agent.middleware.mcp_tool_discovery import _names_from_tool_result

    assert _names_from_tool_result(Command(update={"messages": []})) == []
    assert _names_from_tool_result("plain") == []

    middleware = MCPToolDiscoveryMiddleware(search_active=True)
    request = _FakeRequest(tool_call={"name": MCP_TOOL_SEARCH_TOOL_NAME})
    assert middleware.wrap_tool_call(request, lambda _req: "plain") == "plain"
