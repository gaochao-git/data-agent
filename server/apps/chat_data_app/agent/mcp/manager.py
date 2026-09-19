"""MCP client 管理器：连接管理、工具枚举缓存与健康检查。

设计口径（v1）：
- 每次枚举/调用按需建立会话、用完即关（``open_mcp_session`` 按传输类型打开
  stdio / streamable_http / sse 三种连接），不做常驻连接池；只读场景下开销
  可控，行为可预期，避免后台任务生命周期管理引入的复杂度。
- 工具枚举结果按 TTL 缓存（``CHAT_DATA_MCP_TOOLS_CACHE_TTL_SECONDS``），
  装配 agent 时同步读取缓存，未命中才真正连接。
- 单个 server 枚举/调用失败只降级自身，不影响其他 server 与内置工具。
- 挂到 LangChain 的工具名统一加 ``mcp_`` 前缀并拼接 server id
  （``mcp_<server_id>_<tool>``），与内置注册表名空间隔离，杜绝撞名。
"""

from __future__ import annotations

import asyncio
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from langchain_core.tools import StructuredTool
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from pydantic import Field, create_model

from utils.logger import get_logger

from ...services.runtime_config import read_positive_int_env
from .config import MCPServerConfig, load_mcp_server_configs

logger = get_logger(__name__)

MCP_TOOL_NAME_PREFIX = "mcp_"
MCP_TOOL_NAME_MAX_LENGTH = 64

_MCP_TOOL_TIMEOUT_ENV = "CHAT_DATA_MCP_TOOL_TIMEOUT_SECONDS"
_MCP_TOOLS_CACHE_TTL_ENV = "CHAT_DATA_MCP_TOOLS_CACHE_TTL_SECONDS"

# MCP 工具入参 JSON schema type 到 Python 类型的映射，未知类型一律 Any。
_JSON_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


@dataclass(frozen=True)
class MCPToolInfo:
    """枚举得到的一个 MCP 工具描述。"""

    server_id: str
    name: str  # MCP server 声明的原始工具名
    qualified_name: str  # 挂到 LangChain 的工具名（mcp_<server>_<tool>）
    description: str
    input_schema: dict[str, Any]


@asynccontextmanager
async def open_mcp_session(config: MCPServerConfig) -> AsyncIterator[ClientSession]:
    """按传输类型打开一个已就绪的 MCP ClientSession。"""
    if config.transport == "stdio":
        parameters = StdioServerParameters(
            command=config.command,
            args=list(config.args),
            env=config.env or None,
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                yield session
    elif config.transport == "streamable_http":
        async with streamablehttp_client(config.url, headers=config.headers or None) as (read, write, _session_id):
            async with ClientSession(read, write) as session:
                yield session
    elif config.transport == "sse":
        async with sse_client(config.url, headers=config.headers or None) as (read, write):
            async with ClientSession(read, write) as session:
                yield session
    else:
        raise ValueError(f"不支持的 MCP transport: {config.transport}")


def sanitize_mcp_tool_name(server_id: str, tool_name: str) -> str:
    """生成 LangChain 可用的限定工具名（``mcp_<server>_<tool>``）。"""
    raw = f"{MCP_TOOL_NAME_PREFIX}{server_id}_{str(tool_name or 'tool').strip()}"
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw)[:MCP_TOOL_NAME_MAX_LENGTH]


def _dedupe_qualified_names(tools: list[MCPToolInfo]) -> list[MCPToolInfo]:
    """限定名超长截断后可能撞名，后到者追加序号保证唯一。"""
    seen: dict[str, int] = {}
    out: list[MCPToolInfo] = []
    for info in tools:
        count = seen.get(info.qualified_name, 0)
        seen[info.qualified_name] = count + 1
        if count:
            suffix = f"_{count}"
            info = MCPToolInfo(
                server_id=info.server_id,
                name=info.name,
                qualified_name=f"{info.qualified_name[:MCP_TOOL_NAME_MAX_LENGTH - len(suffix)]}{suffix}",
                description=info.description,
                input_schema=info.input_schema,
            )
        out.append(info)
    return out


def _schema_to_args_model(qualified_name: str, schema: dict[str, Any]):
    """把 MCP 工具的 JSON Schema 转成 pydantic 模型作为 LangChain args_schema。"""
    properties = schema.get("properties") if isinstance(schema, dict) else None
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    fields: dict[str, Any] = {}
    for prop_name, prop in (properties or {}).items():
        if not isinstance(prop, dict):
            prop = {}
        annotation = _JSON_TYPE_MAP.get(str(prop.get("type") or ""), Any)
        description = str(prop.get("description") or "") or None
        if prop_name in required:
            fields[prop_name] = (annotation, Field(description=description))
        else:
            fields[prop_name] = (annotation | None, Field(default=None, description=description))
    safe_model_name = re.sub(r"[^A-Za-z0-9_]", "_", qualified_name)
    return create_model(f"{safe_model_name}_args", **fields)


def _extract_call_result_text(result: Any) -> str:
    """把 MCP CallToolResult 转成模型可读文本；错误结果带显式前缀。"""
    parts: list[str] = []
    for block in list(getattr(result, "content", None) or []):
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    structured = getattr(result, "structuredContent", None)
    if not parts and structured is not None:
        import json

        try:
            parts.append(json.dumps(structured, ensure_ascii=False))
        except (TypeError, ValueError):
            parts.append(str(structured))
    text = "\n".join(parts)
    if getattr(result, "isError", False):
        return f"[mcp tool error] {text}"
    return text


class MCPToolManager:
    """MCP 工具枚举/调用/健康检查的统一入口。"""

    def __init__(
        self,
        *,
        tool_timeout_seconds: int | None = None,
        cache_ttl_seconds: int | None = None,
        session_factory: Callable | None = None,
        config_loader: Callable[[], list[MCPServerConfig]] | None = None,
    ):
        self._tool_timeout = tool_timeout_seconds if tool_timeout_seconds is not None else read_positive_int_env(
            _MCP_TOOL_TIMEOUT_ENV, 60
        )
        self._cache_ttl = cache_ttl_seconds if cache_ttl_seconds is not None else read_positive_int_env(
            _MCP_TOOLS_CACHE_TTL_ENV, 300
        )
        self._session_factory = session_factory or open_mcp_session
        self._config_loader = config_loader or load_mcp_server_configs
        self._tools_cache: list[MCPToolInfo] | None = None
        self._cache_time = 0.0

    # ---- 枚举 ----

    def _cache_fresh(self) -> bool:
        return self._tools_cache is not None and (time.monotonic() - self._cache_time) < self._cache_ttl

    async def list_tools_async(self, *, force_refresh: bool = False) -> list[MCPToolInfo]:
        """枚举全部已配置 MCP server 的工具（带 TTL 缓存）。"""
        if not force_refresh and self._cache_fresh():
            return list(self._tools_cache or [])
        configs = self._config_loader()
        if not configs:
            self._tools_cache = []
            self._cache_time = time.monotonic()
            return []
        tools: list[MCPToolInfo] = []
        for config in configs:
            try:
                tools.extend(await self._list_server_tools(config))
            except Exception as err:
                logger.warning(
                    "MCP server 工具枚举失败（跳过该 server）: id=%s transport=%s err=%s",
                    config.server_id,
                    config.transport,
                    err,
                )
        tools = _dedupe_qualified_names(tools)
        self._tools_cache = tools
        self._cache_time = time.monotonic()
        return list(tools)

    async def _list_server_tools(self, config: MCPServerConfig) -> list[MCPToolInfo]:
        async with self._session_factory(config) as session:
            await asyncio.wait_for(session.initialize(), timeout=self._tool_timeout)
            result = await asyncio.wait_for(session.list_tools(), timeout=self._tool_timeout)
        return [
            MCPToolInfo(
                server_id=config.server_id,
                name=tool.name,
                qualified_name=sanitize_mcp_tool_name(config.server_id, tool.name),
                description=str(getattr(tool, "description", None) or ""),
                input_schema=dict(getattr(tool, "inputSchema", None) or {}) or {"type": "object", "properties": {}},
            )
            for tool in list(getattr(result, "tools", None) or [])
        ]

    def list_tools(self, *, force_refresh: bool = False) -> list[MCPToolInfo]:
        """同步入口：供 agent 装配（同步函数）读取 MCP 工具清单。"""
        return _run_coro_sync(self.list_tools_async(force_refresh=force_refresh))

    # ---- 调用 ----

    async def call_tool_async(self, server_id: str, tool_name: str, arguments: dict[str, Any] | None) -> str:
        """调用指定 server 的工具并返回文本结果。"""
        config = next((c for c in self._config_loader() if c.server_id == server_id), None)
        if config is None:
            raise ValueError(f"MCP server 未配置: {server_id}")
        async with self._session_factory(config) as session:
            await asyncio.wait_for(session.initialize(), timeout=self._tool_timeout)
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments or {}),
                timeout=self._tool_timeout,
            )
        return _extract_call_result_text(result)

    # ---- 健康检查 ----

    async def check_health_async(self, server_id: str) -> bool:
        """对单个 server 做握手级健康检查（initialize + ping）。"""
        config = next((c for c in self._config_loader() if c.server_id == server_id), None)
        if config is None:
            return False
        try:
            async with self._session_factory(config) as session:
                await asyncio.wait_for(session.initialize(), timeout=self._tool_timeout)
                await asyncio.wait_for(session.send_ping(), timeout=self._tool_timeout)
            return True
        except Exception as err:
            logger.warning("MCP server 健康检查失败: id=%s err=%s", server_id, err)
            return False


def _run_coro_sync(coro):
    """在同步上下文执行协程：无事件循环直接 run；已在循环内则换线程新开循环。

    agent 装配是同步函数，调用点可能位于 FastAPI 事件循环内，不允许嵌套
    ``asyncio.run``，故在独立线程以独立事件循环执行（仅枚举路径走这里，
    工具调用本身仍是异步的）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


_manager: MCPToolManager | None = None


def get_mcp_tool_manager() -> MCPToolManager:
    """进程级单例管理器；测试可通过 ``reset_mcp_tool_manager`` 重建。"""
    global _manager
    if _manager is None:
        _manager = MCPToolManager()
    return _manager


def reset_mcp_tool_manager(**kwargs) -> MCPToolManager:
    """重建单例（测试/配置变更场景）。"""
    global _manager
    _manager = MCPToolManager(**kwargs)
    return _manager


def build_langchain_tools(
    tools_info: list[MCPToolInfo],
    *,
    call_tool_async: Callable,
) -> list[StructuredTool]:
    """把枚举结果包装为 LangChain StructuredTool（仅异步调用）。"""
    wrapped: list[StructuredTool] = []
    for info in tools_info:
        async def _acall(
            _server_id: str = info.server_id,
            _tool_name: str = info.name,
            **kwargs: Any,
        ) -> str:
            return await call_tool_async(_server_id, _tool_name, kwargs)

        wrapped.append(
            StructuredTool(
                name=info.qualified_name,
                description=info.description or info.qualified_name,
                args_schema=_schema_to_args_model(info.qualified_name, info.input_schema),
                coroutine=_acall,
            )
        )
    return wrapped


def load_mcp_tools() -> list[StructuredTool]:
    """agent 装配入口：枚举 MCP 工具并包装为 LangChain 工具；未配置时零开销返回空。"""
    if not load_mcp_server_configs():
        return []
    manager = get_mcp_tool_manager()
    tools_info = manager.list_tools()
    return build_langchain_tools(tools_info, call_tool_async=manager.call_tool_async)


__all__ = [
    "MCPToolInfo",
    "MCPToolManager",
    "build_langchain_tools",
    "get_mcp_tool_manager",
    "load_mcp_tools",
    "open_mcp_session",
    "reset_mcp_tool_manager",
    "sanitize_mcp_tool_name",
]
