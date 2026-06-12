import json
import os
import time
from typing import Any

try:
    from server.utils.storage_paths import CASE_LIBRARY_BASE_DIR
except ModuleNotFoundError:
    from utils.storage_paths import CASE_LIBRARY_BASE_DIR


def _env_flag(key: str, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    trimmed = value.strip()
    if not trimmed:
        return default
    return trimmed.lower() in {"1", "true", "yes", "y", "on"}


def trace_enabled() -> bool:
    return _env_flag("AI_TRACE", False)


def _trace_dir() -> str:
    return os.getenv(
        "AI_TRACE_DIR",
        os.path.join(CASE_LIBRARY_BASE_DIR, "trace"),
    )


def _ensure_trace_dir() -> str:
    trace_dir = _trace_dir()
    os.makedirs(trace_dir, exist_ok=True)
    return trace_dir


def _trace_path(conversation_id: str) -> str:
    safe_id = (conversation_id or "unknown").strip() or "unknown"
    filename = f"{safe_id}.jsonl"
    return os.path.join(_ensure_trace_dir(), filename)


def write_trace(conversation_id: str, event: str, payload: Any) -> None:
    if not trace_enabled():
        return
    record = {
        "ts_ms": int(time.time() * 1000),
        "event": event,
        "payload": payload,
    }
    try:
        serialized = json.dumps(record, ensure_ascii=False)
    except TypeError:
        record["payload"] = str(payload)
        serialized = json.dumps(record, ensure_ascii=False)
    path = _trace_path(conversation_id)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(serialized + "\n")


def read_trace(conversation_id: str) -> list[dict[str, Any]]:
    path = _trace_path(conversation_id)
    if not os.path.exists(path):
        return []
    events: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events
