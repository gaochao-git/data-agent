"""ChatData routers and shared OpenAPI definitions."""

from __future__ import annotations

from ..schema import ApiResponse


JSON_API_RESPONSES = {200: {"model": ApiResponse}}
SSE_RESPONSES = {
    200: {
        "description": "Server-sent event stream",
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    }
}
BINARY_RESPONSES = {
    200: {
        "description": "Binary file response",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


__all__ = ["BINARY_RESPONSES", "JSON_API_RESPONSES", "SSE_RESPONSES"]
