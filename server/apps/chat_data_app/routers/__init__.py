"""ChatData routers and shared OpenAPI definitions."""

from __future__ import annotations

from typing import Any

from ..schema import ApiResponse

# 显式对齐 FastAPI responses 参数签名（dict[int | str, dict[str, Any]]），避免各 router 逐一报 arg-type。
JSON_API_RESPONSES: dict[int | str, dict[str, Any]] = {200: {"model": ApiResponse}}
SSE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Server-sent event stream",
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    }
}
BINARY_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Binary file response",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


__all__ = ["BINARY_RESPONSES", "JSON_API_RESPONSES", "SSE_RESPONSES"]
