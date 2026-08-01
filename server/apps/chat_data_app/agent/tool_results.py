"""Structured result protocol shared by ChatData tools and middleware."""

from __future__ import annotations

import json
from typing import Any

INTERNAL_TOOL_RESULT_TYPE = "chatdata_internal_tool_result"
QUERY_RESULT_TYPE = "query_result"
TOOL_RESULT_RECOVERY_KEY = "chatdata_tool_result_recovery"
TOOL_RESULT_RECOVERY_VERSION = 1


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


def build_tool_result_recovery(
    *,
    source_tool: str,
    result_path: str,
    result_size_chars: int | None = None,
    result_id: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build checkpoint-only metadata for reconstructing a tool result."""
    recovery: dict[str, Any] = {
        "version": TOOL_RESULT_RECOVERY_VERSION,
        "source_tool": str(source_tool or "").strip(),
        "result_path": str(result_path or "").strip(),
    }
    if result_size_chars is not None:
        recovery["result_size_chars"] = max(int(result_size_chars), 0)
    if result_id:
        recovery["result_id"] = str(result_id).strip()
    if offset is not None:
        recovery["offset"] = max(int(offset), 0)
    if limit is not None:
        recovery["limit"] = max(int(limit), 1)
    return recovery


def tool_result_recovery(artifact: Any) -> dict[str, Any] | None:
    """Read a valid recovery descriptor from a ToolMessage artifact."""
    if not isinstance(artifact, dict):
        return None
    recovery = artifact.get(TOOL_RESULT_RECOVERY_KEY)
    if not isinstance(recovery, dict):
        return None
    if recovery.get("version") != TOOL_RESULT_RECOVERY_VERSION:
        return None
    if not str(recovery.get("result_path") or "").strip():
        return None
    return recovery


def merge_tool_result_recovery_artifact(artifact: Any, recovery: dict[str, Any]) -> dict[str, Any]:
    """Attach recovery metadata without discarding an existing tool artifact."""
    if isinstance(artifact, dict):
        merged = dict(artifact)
    elif artifact is None:
        merged = {}
    else:
        merged = {"source_artifact": artifact}
    merged[TOOL_RESULT_RECOVERY_KEY] = dict(recovery)
    return merged
