"""Structured prompt-size metrics for LLM requests."""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from utils.logger import get_logger

logger = get_logger(__name__)

_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("llm_prompt_metrics_context", default={})
_CALL_INDEX: contextvars.ContextVar[int] = contextvars.ContextVar("llm_prompt_metrics_call_index", default=0)


def _enabled() -> bool:
    value = str(os.getenv("LLM_PROMPT_METRICS_ENABLED") or "").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _safe_segment(value: Any) -> str:
    text = str(value or "").strip()
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in text)


def _metrics_dir() -> Path | None:
    configured = str(os.getenv("LLM_PROMPT_METRICS_DIR") or "").strip()
    if configured:
        return Path(os.path.expanduser(configured)).resolve()
    data_root = str(os.getenv("DATA_ROOT") or "").strip()
    if data_root:
        return (Path(os.path.expanduser(data_root)).resolve() / "llm_prompt_metrics")
    return None


def _content_chars(content: Any) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    total += len(str(item.get("text") or ""))
                elif "value" in item:
                    total += len(str(item.get("value") or ""))
                elif "image_url" in item:
                    image_url = item.get("image_url")
                    if isinstance(image_url, dict):
                        total += len(str(image_url.get("url") or ""))
                    else:
                        total += len(str(image_url or ""))
                else:
                    total += len(json.dumps(item, ensure_ascii=False, default=str))
            else:
                total += len(str(item))
        return total
    return len(str(content))


def extract_usage_tokens(response: Any) -> dict[str, int]:
    """Extract OpenAI-style token usage from a blocking response."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    elif hasattr(usage, "__dict__") and not isinstance(usage, dict):
        usage = usage.__dict__
    if not isinstance(usage, dict):
        return {}

    def _usage_int(*keys: str) -> int:
        for key in keys:
            try:
                value = usage.get(key)
            except Exception:
                value = None
            if value is not None:
                try:
                    return int(value or 0)
                except Exception:
                    return 0
        return 0

    prompt_tokens = _usage_int("prompt_tokens", "input_tokens")
    completion_tokens = _usage_int("completion_tokens", "output_tokens")
    total_tokens = _usage_int("total_tokens")
    if not total_tokens and (prompt_tokens or completion_tokens):
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def measure_messages(messages: Any, tools: Any = None) -> dict[str, int]:
    """Return role-level character counts for OpenAI-style messages."""
    role_chars: dict[str, int] = {}
    message_count = 0
    if isinstance(messages, (list, tuple)):
        iterable = messages
    else:
        iterable = list(messages or [])
    for msg in iterable:
        message_count += 1
        if isinstance(msg, dict):
            role = str(msg.get("role") or "unknown")
            chars = _content_chars(msg.get("content"))
            if msg.get("tool_calls"):
                chars += len(json.dumps(msg.get("tool_calls"), ensure_ascii=False, default=str))
        else:
            role = str(getattr(msg, "type", "") or getattr(msg, "role", "") or msg.__class__.__name__)
            chars = _content_chars(getattr(msg, "content", ""))
        role_chars[role] = role_chars.get(role, 0) + chars
    tools_chars = len(json.dumps(tools, ensure_ascii=False, default=str)) if tools else 0
    return {
        "message_count": message_count,
        "system_chars": role_chars.get("system", 0),
        "user_chars": role_chars.get("user", 0) + role_chars.get("human", 0),
        "assistant_chars": role_chars.get("assistant", 0) + role_chars.get("ai", 0),
        "tool_chars": role_chars.get("tool", 0) + tools_chars,
        "prompt_chars": sum(role_chars.values()) + tools_chars,
    }


@contextlib.contextmanager
def llm_prompt_metrics_context(**values: Any) -> Iterator[None]:
    """Attach request-level metadata to prompt metrics written in this context."""
    current = dict(_CONTEXT.get() or {})
    current.update({key: value for key, value in values.items() if value not in (None, "")})
    token = _CONTEXT.set(current)
    index_token = _CALL_INDEX.set(0)
    try:
        yield
    finally:
        _CONTEXT.reset(token)
        _CALL_INDEX.reset(index_token)


def _build_base_row(stage: str, model: str | None, metadata: dict[str, Any] | None, call_id: str | None) -> tuple[Path | None, str, dict[str, Any]]:
    target_dir = _metrics_dir()
    if target_dir is None:
        return None, "", {}
    context = dict(_CONTEXT.get() or {})
    if metadata:
        context.update({key: value for key, value in metadata.items() if value not in (None, "")})
    request_id = _safe_segment(context.get("request_id") or context.get("base_id") or context.get("conversation_id") or time.strftime("%Y%m%d"))
    if not request_id:
        request_id = time.strftime("%Y%m%d")
    row = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stage": str(stage or ""),
        "model": str(model or ""),
        "call_id": call_id or uuid.uuid4().hex[:8],
        **context,
    }
    return target_dir, request_id, row


def _append_row(target_dir: Path, request_id: str, row: dict[str, Any]) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    with open(target_dir / f"{request_id}.jsonl", "a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


def record_llm_prompt_metrics(stage: str, *, model: str | None = None, messages: Any = None, tools: Any = None, metadata: dict[str, Any] | None = None, call_id: str | None = None) -> str:
    """Append one JSONL row for a real LLM request prompt."""
    if not _enabled():
        return call_id or ""
    try:
        target_dir, request_id, row = _build_base_row(stage, model, metadata, call_id)
        if target_dir is None:
            return str(row.get("call_id") or call_id or "")
        call_index = _CALL_INDEX.get() + 1
        _CALL_INDEX.set(call_index)
        counts = measure_messages(messages or [], tools)
        row.update({"type": "llm_prompt", "call_index": call_index, **counts})
        _append_row(target_dir, request_id, row)
        return str(row.get("call_id") or "")
    except Exception as exc:
        logger.warning("write llm prompt metrics failed stage=%s err=%s", stage, exc)
        return call_id or ""


def record_llm_output_metrics(stage: str, *, model: str | None = None, output_text: Any = "", first_token_ms: int = 0, llm_duration_ms: int = 0, usage: dict[str, int] | None = None, metadata: dict[str, Any] | None = None, call_id: str | None = None) -> None:
    """Append one JSONL row for a successful LLM response."""
    if not _enabled():
        return
    try:
        target_dir, request_id, row = _build_base_row(stage, model, metadata, call_id)
        if target_dir is None:
            return
        row.update(
            {
                "type": "llm_output",
                "output_chars": _content_chars(output_text),
                "first_token_ms": int(first_token_ms or 0),
                "llm_duration_ms": int(llm_duration_ms or 0),
                "prompt_tokens": int((usage or {}).get("prompt_tokens") or 0),
                "completion_tokens": int((usage or {}).get("completion_tokens") or 0),
                "total_tokens": int((usage or {}).get("total_tokens") or 0),
            }
        )
        _append_row(target_dir, request_id, row)
    except Exception as exc:
        logger.warning("write llm output metrics failed stage=%s err=%s", stage, exc)
