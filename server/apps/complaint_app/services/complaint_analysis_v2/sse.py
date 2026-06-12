from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from apps.complaint_app.history_storage import append_upload_stream_event


def _normalize_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(payload or {})
    if "ts" not in data:
        data["ts"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return data


def format_named_sse(event: str, payload: dict[str, Any] | None = None) -> str:
    data = _normalize_payload(payload)
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def format_named_sse_with_history(event: str, payload: dict[str, Any] | None = None, *, user_id: str | None = None, base_id: str | None = None) -> str:
    data = _normalize_payload(payload)
    if user_id and base_id:
        append_upload_stream_event(user_id, base_id, {"event": event, **data})
    return format_named_sse(event, data)
