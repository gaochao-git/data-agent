"""MCP client 管理器：连接管理、工具枚举缓存与健康检查。

设计口径（v2，对齐 MCP 规范 2026-07-28 / SDK 2.x）：
- 连接走 SDK 2.x ``Client`` 门面（``mode=auto``）：新协议 server 经
  ``server/discover`` 直接采用新式按请求模型，旧协议 server 回退 initialize
  握手——对现有 1.x server 与 2026-07-28 server 同时兼容。
- 每次枚举/调用按需建连、用完即关，不做常驻连接池；只读场景开销可控。
  （只支持 streamable_http，传输趋于无状态，该取舍与新协议方向一致。）
- 工具枚举结果按 TTL 缓存（``CHAT_DATA_MCP_TOOLS_CACHE_TTL_SECONDS``），
  装配 agent 时同步读取缓存，未命中才真正连接。
- 单个 server 枚举/调用失败只降级自身，不影响其他 server 与内置工具。
- 挂到 LangChain 的工具名统一加 ``mcp_`` 前缀并拼接 server id
  （``mcp_<server_id>_<tool>``），与内置注册表名空间隔离，杜绝撞名。
- ``ping`` 已随 2026-07-28 移除：健康检查改为 initialize/discover + 一次
  ``tools/list`` 探针（新旧 server 通吃）。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import StructuredTool
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field, create_model

from utils.logger import get_logger

from ...services.runtime_config import read_positive_int_env
from .admission import MCPAdmissionError, max_tool_description_chars, validate_mcp_server_tools
from .config import MCPServerConfig, default_mcp_tool_timeout_ms, load_mcp_server_configs

logger = get_logger(__name__)

MCP_TOOL_NAME_PREFIX = "mcp_"
MCP_TOOL_NAME_MAX_LENGTH = 64

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


def _contains_timeout_error(error: BaseException) -> bool:
    """AnyIO TaskGroup 会把 wait_for 的 TimeoutError 包进 ExceptionGroup，递归识别。"""
    if isinstance(error, TimeoutError):
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_contains_timeout_error(child) for child in error.exceptions)
    return False


@dataclass(frozen=True)
class MCPToolInfo:
    """枚举得到的一个 MCP 工具描述。"""

    server_id: str
    name: str  # MCP server 声明的原始工具名
    qualified_name: str  # 挂到 LangChain 的工具名（mcp_<server>_<tool>）
    description: str
    input_schema: dict[str, Any]
    label: str = ""  # 用户可见友好名（plugin.json tools.<name>.label，缺省回退原始工具名）
    approval: str = "ask"  # 审批档位（plugin.json tools.<name>.approval：ask/allow）
    timeout_ms: int = 0  # 最终生效超时（ms）
    configured: bool = False  # 是否在 plugin.json 的 tools 中显式配置


@asynccontextmanager
async def open_mcp_session(config: MCPServerConfig, *, timeout_seconds: int = 60) -> AsyncIterator[Client]:
    """按传输类型打开一个已连接的 2.x ``Client`` 门面。

    ``mode=auto``：新协议 server 走 ``server/discover``（按请求、无握手），
    旧协议 server 回退 initialize 握手——对两代 server 同时兼容。
    streamable_http 的自定义 headers 经 httpx2 客户端注入。
    """
    if config.transport != "streamable_http":
        raise ValueError(f"只支持 streamable_http，当前: {config.transport}")
    if config.headers:
        # 2.x 的 streamable_http_client 不再接受 headers 参数，
        # 以携带默认 headers 的 httpx2 客户端注入（生命周期随连接关闭）。
        import httpx2

        http_client = httpx2.AsyncClient(headers=dict(config.headers))
        server: Any = streamable_http_client(config.url, http_client=http_client)
    else:
        server = config.url

    async with Client(server, mode="auto", read_timeout_seconds=float(timeout_seconds)) as client:
        yield client


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
                qualified_name=f"{info.qualified_name[: MCP_TOOL_NAME_MAX_LENGTH - len(suffix)]}{suffix}",
                description=info.description,
                input_schema=info.input_schema,
                label=info.label,
                approval=info.approval,
                timeout_ms=info.timeout_ms,
                configured=info.configured,
            )
        out.append(info)
    return out


def _schema_to_args_model(qualified_name: str, schema: dict[str, Any]) -> type[BaseModel]:
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
    # MRTR（SEP-2322）：input_required 是多轮往返的中间结果，不是最终答案；
    # 缺省 resultType 的旧 server 结果按 complete 处理（规范要求的兼容行为）。
    if str(getattr(result, "resultType", "") or "") == "input_required":
        return f"[mcp tool input_required] 服务端需要补充输入，本次结果不是最终结论: {text}"
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
    ) -> None:
        self._tool_timeout = (
            tool_timeout_seconds if tool_timeout_seconds is not None else default_mcp_tool_timeout_ms() // 1_000
        )
        self._cache_ttl = (
            cache_ttl_seconds if cache_ttl_seconds is not None else read_positive_int_env(_MCP_TOOLS_CACHE_TTL_ENV, 300)
        )
        self._session_factory = session_factory or (
            lambda config: open_mcp_session(config, timeout_seconds=self._session_timeout_seconds(config))
        )
        self._config_loader = config_loader or load_mcp_server_configs
        self._tools_cache: list[MCPToolInfo] | None = None
        self._cache_time = 0.0
        self._effective_cache_ttl = self._cache_ttl

    # ---- 枚举 ----

    def _session_timeout_seconds(self, config: MCPServerConfig) -> int:
        """session read timeout 必须覆盖该 server 最长的工具超时。"""
        max_ms = config.max_tool_timeout_ms(self._tool_timeout * 1_000)
        return max(self._tool_timeout, (max_ms + 999) // 1_000)

    def _cache_fresh(self) -> bool:
        return self._tools_cache is not None and (time.monotonic() - self._cache_time) < self._effective_cache_ttl

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
        server_ttls: list[int] = []
        for config in configs:
            try:
                server_tools, ttl_ms = await self._list_server_tools(config)
                tools.extend(server_tools)
                if ttl_ms is not None:
                    server_ttls.append(ttl_ms)
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
        # 服务端通过 CacheableResult.ttlMs 声明的缓存新鲜度提示（2026-07-28，毫秒）：
        # 取全部 server 提示的最小值换算为秒，仅用于缩短（不超过环境默认）且不
        # 永久改写基础配置，避免某 server 的提示拖累其他 server 的工具新鲜度。
        self._effective_cache_ttl = self._cache_ttl
        if server_ttls:
            hinted_seconds = max(1, min(server_ttls) // 1000)
            self._effective_cache_ttl = min(self._cache_ttl, hinted_seconds)
        return list(tools)

    async def _list_server_tools(self, config: MCPServerConfig) -> tuple[list[MCPToolInfo], int | None]:
        """枚举单个 server 的工具；返回 (工具清单, 服务端声明的缓存 ttlMs 或 None)。

        进入 ``Client`` 上下文时门面自动完成 discover/initialize（``mode=auto``），
        这里只做枚举探针——该探针同时是健康检查的实现（``ping`` 已随 2026-07-28 移除）。
        """
        async with self._session_factory(config) as client:
            result = await asyncio.wait_for(client.list_tools(), timeout=self._tool_timeout)
        ttl_ms = getattr(result, "ttlMs", None)
        tools: list[MCPToolInfo] = []
        for tool in list(getattr(result, "tools", None) or []):
            policy = config.resolve_tool(tool.name, self._tool_timeout * 1_000)
            tools.append(
                MCPToolInfo(
                    server_id=config.server_id,
                    name=tool.name,
                    qualified_name=sanitize_mcp_tool_name(config.server_id, tool.name),
                    description=str(getattr(tool, "description", None) or "")[: max_tool_description_chars()],
                    # mcp 2.x MCPTool 字段名为 input_schema（inputSchema 仅为序列化 alias），
                    # 误取 alias 会恒为 None，导致全部工具 schema 退化为空、模型入参被丢弃
                    input_schema=dict(getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {})
                    or {"type": "object", "properties": {}},
                    label=policy.label,
                    approval=policy.approval,
                    timeout_ms=policy.timeout_ms or 0,
                    configured=tool.name in config.tools,
                )
            )
        report = validate_mcp_server_tools(
            config.server_id,
            [{"name": info.name, "description": info.description, "input_schema": info.input_schema} for info in tools],
        )
        for issue in report.warnings:
            logger.warning("MCP server 准入告警: id=%s %s: %s", config.server_id, issue.code, issue.message)
        if not report.ok:
            raise MCPAdmissionError(f"MCP server {config.server_id} 未通过准入: {report.summary()}")
        return tools, ttl_ms if isinstance(ttl_ms, int) and ttl_ms > 0 else None

    def list_tools(self, *, force_refresh: bool = False) -> list[MCPToolInfo]:
        """同步入口：供 agent 装配（同步函数）读取 MCP 工具清单。"""
        return _run_coro_sync(self.list_tools_async(force_refresh=force_refresh))

    def get_display_name(self, qualified_name: str) -> str:
        """按限定名查展示名（只读 TTL 缓存，不触发网络枚举）；未命中返回空串。"""
        name = str(qualified_name or "").strip()
        if not name:
            return ""
        for info in self._tools_cache or []:
            if info.qualified_name == name:
                return info.label
        return ""

    # ---- 调用 ----

    async def call_tool_async(self, server_id: str, tool_name: str, arguments: dict[str, Any] | None) -> str:
        """调用指定 server 的工具并返回文本结果。

        门面 ``call_tool`` 内建 MRTR（多轮往返请求，SEP-2322）重试循环：
        新协议 server 返回 ``input_required`` 时按 ``input_required_max_rounds``
        自动补参重试；旧协议 server 的结果缺省 ``resultType`` 按 complete 处理。
        """
        config = next((c for c in self._config_loader() if c.server_id == server_id), None)
        if config is None:
            raise ValueError(f"MCP server 未配置: {server_id}")
        timeout_ms = config.resolve_tool(tool_name, self._tool_timeout * 1_000).timeout_ms or 0
        timeout_seconds = max(timeout_ms / 1_000, 1.0)
        timeout_message = f"MCP tool {server_id}/{tool_name} timed out after {timeout_seconds:g}s"
        try:
            async with self._session_factory(config) as client:
                result = await asyncio.wait_for(
                    client.call_tool(tool_name, arguments or {}),
                    timeout=timeout_seconds,
                )
        except TimeoutError as err:
            raise TimeoutError(timeout_message) from err
        except BaseExceptionGroup as group:
            if _contains_timeout_error(group):
                raise TimeoutError(timeout_message) from group
            raise
        return _extract_call_result_text(result)

    # ---- 健康检查 ----

    async def check_health_async(self, server_id: str) -> bool:
        """对单个 server 做健康检查（discover/initialize + 一次 ``tools/list`` 探针）。

        ``ping`` 已随 MCP 2026-07-28 移除，``tools/list`` 是新旧两代 server
        都支持的轻量探针；进入 ``Client`` 上下文本身即完成协议协商。
        """
        config = next((c for c in self._config_loader() if c.server_id == server_id), None)
        if config is None:
            return False
        try:
            async with self._session_factory(config) as client:
                await asyncio.wait_for(client.list_tools(), timeout=self._tool_timeout)
            return True
        except Exception as err:
            logger.warning("MCP server 健康检查失败: id=%s err=%s", server_id, err)
            return False


def _run_coro_sync(coro: Coroutine[Any, Any, Any]) -> Any:
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


def reset_mcp_tool_manager(**kwargs: Any) -> MCPToolManager:
    """重建单例（测试/配置变更场景）。"""
    global _manager
    _manager = MCPToolManager(**kwargs)
    return _manager


def get_mcp_tool_display_name(qualified_name: str) -> str:
    """事件序列化用：按限定名解析用户可见展示名（只读进程级缓存，不触发枚举）。

    仅命中 MCP 工具时返回非空；内置工具/缓存未热时返回空串，由前端内置
    映射表与限定名兜底展示。
    """
    if _manager is None:
        return ""
    return _manager.get_display_name(qualified_name)


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
            # 可选参数显式 None 时省略：MCP 语义下缺省 = 服务端默认值，显式 null 通常不被服务端 schema 接受
            payload = {key: value for key, value in kwargs.items() if value is not None}
            return await call_tool_async(_server_id, _tool_name, payload)

        wrapped.append(
            StructuredTool(
                name=info.qualified_name,
                description=info.description or info.qualified_name,
                args_schema=_schema_to_args_model(info.qualified_name, info.input_schema),
                coroutine=_acall,
                # 展示名与来源随工具对象透传：审批卡据此展示 "MCP server / 工具"，
                # 而不是把 mcp_<server>_<tool> 原始名直接抛给用户；approval 供
                # 审批中间件区分 ask/allow 档位。
                metadata={
                    "origin": "mcp",
                    "mcp_server_id": info.server_id,
                    "mcp_tool_name": info.name,
                    **({"display_name": info.label} if info.label else {}),
                    **({"approval": info.approval} if info.approval != "ask" else {}),
                },
            )
        )
    return wrapped


def load_mcp_tools(server_ids: frozenset[str] | None = None) -> list[StructuredTool]:
    """agent 装配入口：枚举 MCP 工具并包装为 LangChain 工具；未配置时零开销返回空。

    ``server_ids`` 为装配规格声明的 server 白名单（``None`` 表示全部允许）；
    过滤发生在 MCPToolInfo 层（server_id 精确匹配），不依赖限定名前缀。
    """
    if not load_mcp_server_configs():
        return []
    manager = get_mcp_tool_manager()
    tools_info = manager.list_tools()
    if server_ids is not None:
        allowed = set(server_ids)
        tools_info = [info for info in tools_info if info.server_id in allowed]
    return build_langchain_tools(tools_info, call_tool_async=manager.call_tool_async)


__all__ = [
    "MCPToolInfo",
    "MCPToolManager",
    "build_langchain_tools",
    "get_mcp_tool_display_name",
    "get_mcp_tool_manager",
    "load_mcp_tools",
    "open_mcp_session",
    "reset_mcp_tool_manager",
    "sanitize_mcp_tool_name",
]
