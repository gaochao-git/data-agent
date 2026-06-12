from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping

from utils.logger import get_logger

logger = get_logger(__name__)

_MCP_IMPORT_ERROR: Exception | None = None
try:
    import mcp.types as mcp_types
    from mcp.client.sse import sse_client
    from mcp.shared.message import SessionMessage
except Exception as exc:
    _MCP_IMPORT_ERROR = exc
    mcp_types = None
    sse_client = None
    SessionMessage = None


def mcp_available() -> bool:
    return bool(mcp_types and sse_client and SessionMessage)


def mcp_import_error() -> Exception | None:
    return _MCP_IMPORT_ERROR


def run_coro_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result_holder: Dict[str, Any] = {}
    error_holder: Dict[str, Exception] = {}

    def _runner() -> None:
        try:
            result_holder["result"] = asyncio.run(coro)
        except Exception as exc:
            error_holder["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in error_holder:
        raise error_holder["error"]
    return result_holder.get("result")


@dataclass
class MCPClientConfig:
    url: str
    protocol_version: str = "2025-11-25"
    client_name: str = "complaint_opencode"
    client_version: str = "0.1.0"
    timeout_seconds: float = 8.0


class MCPClient:
    def __init__(self, config: MCPClientConfig) -> None:
        self._config = config
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _send(self, write_stream, method: str, params: Mapping[str, Any], req_id: Any) -> None:
        msg = mcp_types.JSONRPCMessage(root=mcp_types.JSONRPCRequest(method=method, params=dict(params), id=req_id, jsonrpc="2.0"))
        await write_stream.send(SessionMessage(msg))

    async def _recv_until(self, read_stream, target_id: Any) -> Mapping[str, Any]:
        async def _wait() -> Mapping[str, Any]:
            while True:
                message = await read_stream.receive()
                root = message.message.root
                if root.id != target_id:
                    continue
                if isinstance(root, mcp_types.JSONRPCResponse):
                    return root.result or {}
                if isinstance(root, mcp_types.JSONRPCError):
                    raise RuntimeError(f"MCP error: {root.error}")

        return await asyncio.wait_for(_wait(), timeout=self._config.timeout_seconds)

    async def _initialize(self, read_stream, write_stream) -> None:
        init_id = self._next_id()
        await self._send(
            write_stream,
            "initialize",
            {
                "protocolVersion": self._config.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": self._config.client_name, "version": self._config.client_version},
            },
            init_id,
        )
        await self._recv_until(read_stream, init_id)

    async def list_tools(self) -> List[Dict[str, Any]]:
        if not mcp_available():
            raise RuntimeError(f"mcp package unavailable: {mcp_import_error()}")
        async with sse_client(self._config.url) as (read_stream, write_stream):
            await self._initialize(read_stream, write_stream)
            req_id = self._next_id()
            await self._send(write_stream, "tools/list", {}, req_id)
            response = await self._recv_until(read_stream, req_id)
            tools = response.get("tools") if isinstance(response, dict) else []
            return [item for item in (tools or []) if isinstance(item, dict)]

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Dict[str, Any]:
        if not mcp_available():
            raise RuntimeError(f"mcp package unavailable: {mcp_import_error()}")
        async with sse_client(self._config.url) as (read_stream, write_stream):
            await self._initialize(read_stream, write_stream)
            req_id = self._next_id()
            await self._send(write_stream, "tools/call", {"name": name, "arguments": dict(arguments)}, req_id)
            response = await self._recv_until(read_stream, req_id)
            return dict(response) if isinstance(response, dict) else {"result": response}


def stringify_tool_result(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
            if text_parts:
                return "\n".join(text_parts).strip()
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.warning("[mcp_client] stringify dict failed err=%s", exc)
            return str(result)
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:
        logger.warning("[mcp_client] stringify result failed err=%s", exc)
        return str(result)


__all__ = ["MCPClient", "MCPClientConfig", "mcp_available", "mcp_import_error", "run_coro_sync", "stringify_tool_result"]
