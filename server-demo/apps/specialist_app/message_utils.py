import json
import time
import uuid
from typing import Any


def _current_timestamp_ms() -> int:
    return int(time.time() * 1000)


def _iso_utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def create_message(
    role: str,
    content: Any,
    *,
    message_id: str | None = None,
    visible_for_display: bool = True,
    display_type: str | None = None,
    display_content: str | None = None,
    thinking: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry_id = message_id or f"m_{uuid.uuid4().hex}"
    if isinstance(content, str):
        serialized = content
    elif isinstance(content, (dict, list)):
        serialized = json.dumps(content, ensure_ascii=False)
    else:
        serialized = str(content)
    message_entry: dict[str, Any] = {
        "id": entry_id,
        "role": role,
        "content": serialized,
        "timestamp": _current_timestamp_ms(),
        "created_at": _iso_utc_now(),
    }
    display_meta: dict[str, Any] = {"visible": bool(visible_for_display)}
    if display_type:
        display_meta["type"] = display_type
    if visible_for_display:
        display_meta["content"] = (
            display_content if isinstance(display_content, str) else serialized
        )
    elif isinstance(display_content, str):
        display_meta["content"] = display_content
    if thinking:
        display_meta["thinking"] = thinking
    message_entry["display"] = display_meta
    if extra:
        message_entry.update(extra)
    return message_entry

