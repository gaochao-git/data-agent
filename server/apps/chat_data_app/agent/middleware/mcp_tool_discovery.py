"""MCP 工具按需发现中间件：两级加载的请求侧执行点。

与 ``agent/mcp/catalog.py`` 的分工：目录与检索工具在那边，本模块负责把
"发现"落到 agent 运行环路上：

- ``awrap_tool_call``：拦截 ``mcp_tool_search`` 的执行结果，把命中工具名
  并入 state 的 ``discovered_tool_names``（会话累积、随 checkpoint 持久）。
- ``awrap_model_call``：检索模式下过滤每次模型请求的 ``tools``——内置工具
  与检索工具始终可见，MCP 工具只暴露"始终挂载 + 已发现"两类；未发现的
  MCP 工具仍注册在 ToolNode 中可执行，只是不进入模型工具列表。

工具执行查找基于 create_agent 时的全量注册（``ToolNode.tools_by_name``），
与本模块的请求级过滤无关；因此模型"先检索、后调用"链路无需额外注册。
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from utils.logger import get_logger

from ..agent_state import merge_discovered_tool_names
from ..mcp.catalog import MCP_TOOL_SEARCH_TOOL_NAME

logger = get_logger(__name__)


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", "") or "")


def _is_mcp_tool(tool: Any) -> bool:
    name = _tool_name(tool)
    return name.startswith("mcp_") and name != MCP_TOOL_SEARCH_TOOL_NAME


def _discovered_from_state(state: Any) -> list[str]:
    if not isinstance(state, dict):
        return []
    return merge_discovered_tool_names(None, state.get("discovered_tool_names"))


def _names_from_search_content(content: Any) -> list[str]:
    """解析 mcp_tool_search 消息的 JSON 内容，返回命中工具名列表（结构非法返回空）。"""
    if not isinstance(content, str):
        return []
    try:
        payload = json.loads(content)
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    return [str(item["name"]) for item in payload.get("results") or [] if isinstance(item, dict) and item.get("name")]


def _discovered_from_history(messages: Any) -> list[str]:
    """从历史消息里的 mcp_tool_search 结果反推已发现工具（state 缺失时兜底）。"""
    if not isinstance(messages, list):
        return []
    names: list[str] = []
    for message in messages:
        if not isinstance(message, ToolMessage) or str(message.name or "") != MCP_TOOL_SEARCH_TOOL_NAME:
            continue
        names.extend(_names_from_search_content(message.content))
    return merge_discovered_tool_names(None, names)


class MCPToolDiscoveryMiddleware(AgentMiddleware):
    """检索模式下按"始终挂载 + 已发现"过滤每轮模型请求的 MCP 工具。"""

    def __init__(self, *, search_active: bool, always_mounted_tools: frozenset[str] | set[str] = frozenset()):
        self.search_active = bool(search_active)
        self.always_mounted_tools = frozenset(always_mounted_tools)

    # ---- model request 过滤 ----

    def wrap_model_call(self, request, handler):
        return handler(self._filter_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._filter_request(request))

    def _filter_request(self, request):
        if not self.search_active:
            return request
        tools = list(getattr(request, "tools", None) or [])
        mcp_tools = [tool for tool in tools if _is_mcp_tool(tool)]
        if not mcp_tools:
            return request
        keep = set(self.always_mounted_tools) | set(_discovered_from_state(getattr(request, "state", None)))
        keep |= set(_discovered_from_history(getattr(request, "messages", None)))
        exposed = [tool for tool in mcp_tools if _tool_name(tool) in keep]
        if len(exposed) == len(mcp_tools):
            return request
        kept_builtin = [tool for tool in tools if not _is_mcp_tool(tool)]
        logger.info(
            "[mcp_tool_discovery] 请求工具过滤: total=%d mcp=%d exposed=%d",
            len(tools),
            len(mcp_tools),
            len(exposed),
        )
        return request.override(tools=[*kept_builtin, *exposed])

    # ---- 检索结果捕获 ----

    def wrap_tool_call(self, request, handler):
        if not self._is_search_request(request):
            return handler(request)
        return self._capture_discovery(handler(request))

    async def awrap_tool_call(self, request, handler):
        if not self._is_search_request(request):
            return await handler(request)
        return self._capture_discovery(await handler(request))

    def _is_search_request(self, request: Any) -> bool:
        tool_call = getattr(request, "tool_call", None)
        return isinstance(tool_call, dict) and str(tool_call.get("name") or "") == MCP_TOOL_SEARCH_TOOL_NAME

    def _capture_discovery(self, result: Any):
        """把检索命中的工具名并入 state；解析失败原样返回不阻断链路。"""
        names = _names_from_tool_result(result)
        if not names:
            return result
        if isinstance(result, Command):
            update = dict(result.update or {})
            update["discovered_tool_names"] = merge_discovered_tool_names(update.get("discovered_tool_names"), names)
            return Command(update=update, goto=result.goto, resume=result.resume)
        if isinstance(result, ToolMessage):
            logger.info("[mcp_tool_discovery] 发现工具: %s", names)
            return Command(
                update={
                    "messages": [result],
                    "discovered_tool_names": names,
                }
            )
        return result


def _names_from_tool_result(result: Any) -> list[str]:
    """从检索工具的 ToolMessage 内容解析命中的 qualified_name 列表。"""
    message = result
    if isinstance(result, Command):
        messages = (result.update or {}).get("messages")
        if isinstance(messages, list) and messages and isinstance(messages[0], ToolMessage):
            message = messages[0]
        else:
            return []
    if not isinstance(message, ToolMessage) or message.status == "error":
        return []
    return merge_discovered_tool_names(None, _names_from_search_content(message.content))


__all__ = ["MCPToolDiscoveryMiddleware"]
