"""Structured result protocol shared by ChatData tools and middleware."""

from __future__ import annotations

import json
from typing import Any

INTERNAL_TOOL_RESULT_TYPE = "chatdata_internal_tool_result"
QUERY_RESULT_TYPE = "query_result"


def build_internal_query_result(
    query_result: dict[str, Any],
    *,
    query_mode: str,
    confidence: float,
    save_as: str = "",
) -> str:
    """Return the non-user-facing result consumed by ToolResultMiddleware."""
    return json.dumps(
        {
            "type": INTERNAL_TOOL_RESULT_TYPE,
            "version": 1,
            "kind": QUERY_RESULT_TYPE,
            "payload": query_result,
            "meta": {
                "query_mode": query_mode,
                "confidence": confidence,
                "save_as": str(save_as or "").strip(),
            },
        },
        ensure_ascii=False,
    )


def parse_internal_tool_result(content: Any) -> dict[str, Any] | None:
    """Parse a result protocol emitted by a ChatData tool, if present."""
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("type") != INTERNAL_TOOL_RESULT_TYPE
        or payload.get("kind") != QUERY_RESULT_TYPE
    ):
        return None
    if not isinstance(payload.get("payload"), dict) or not isinstance(payload.get("meta"), dict):
        return None
    return payload


def build_query_result_response(
    *,
    status: str,
    data: dict[str, Any] | None = None,
    error: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Build the stable public ``semantic_query_tool`` response envelope."""
    return json.dumps(
        {
            "type": QUERY_RESULT_TYPE,
            "version": 1,
            "status": status,
            "data": data,
            "error": error,
            "meta": meta or {},
        },
        ensure_ascii=False,
    )
