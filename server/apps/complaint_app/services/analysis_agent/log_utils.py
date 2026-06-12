from __future__ import annotations

import json
import re
from typing import Any

LOG_PREVIEW_LIMIT = 8192
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_ID_CARD_RE = re.compile(r"(?<![0-9A-Za-z])(\d{17}[\dXx])(?![0-9A-Za-z])")
_BANK_CARD_RE = re.compile(r"(?<!\d)(\d{12,19})(?!\d)")


def _mask_middle(value: str, keep_prefix: int, keep_suffix: int) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep_prefix + keep_suffix:
        return "*" * len(text)
    return f"{text[:keep_prefix]}{'*' * (len(text) - keep_prefix - keep_suffix)}{text[-keep_suffix:]}"


def _mask_bank_card_match(match: re.Match) -> str:
    text = str(match.group(1) or "")
    if len(text) < 12:
        return text
    if _PHONE_RE.fullmatch(text):
        return text
    return _mask_middle(text, 4, 4)


def mask_sensitive_text(text: str) -> str:
    value = str(text or "")
    value = _ID_CARD_RE.sub(lambda m: _mask_middle(str(m.group(1) or ""), 6, 4), value)
    value = _PHONE_RE.sub(lambda m: _mask_middle(str(m.group(1) or ""), 3, 2), value)
    value = _BANK_CARD_RE.sub(_mask_bank_card_match, value)
    return value


def log_preview_text(value: Any, limit: int = LOG_PREVIEW_LIMIT) -> str:
    masked = mask_sensitive_text(str(value or "")).strip()
    if len(masked) <= max(0, int(limit)):
        return masked
    return f"{masked[:max(0, int(limit))]}...[truncated]"


def log_preview_json(value: Any, limit: int = LOG_PREVIEW_LIMIT) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return log_preview_text(text, limit=limit)


def build_trace_tag(trace_context: dict[str, Any] | None = None, match_strategy: str | None = None) -> str:
    context = trace_context or {}
    request_id = str(context.get("request_id") or "-")
    base_id = str(context.get("base_id") or "-")
    user_id = str(context.get("user_id") or "-")
    file_id = str(context.get("file_id") or "-")
    parts = [f"request_id={request_id}", f"base_id={base_id}", f"user_id={user_id}", f"file_id={file_id}"]
    if match_strategy:
        parts.append(f"match_strategy={match_strategy}")
    for key in ("scope", "scope_path", "org_id", "entity_id", "reporter_name", "issue_analysis_version", "complaint_index", "complaint_id", "complaint_hash"):
        value = str(context.get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    return " ".join(parts)


__all__ = [
    "LOG_PREVIEW_LIMIT",
    "mask_sensitive_text",
    "log_preview_text",
    "log_preview_json",
    "build_trace_tag",
]
