from __future__ import annotations

import json
import re
from typing import Any

PHONE_PATTERN = re.compile(r"(?<!\d)(1\d{10})(?!\d)")
ID_CARD_PATTERN = re.compile(r"(?<![0-9Xx])(\d{17}[0-9Xx]|\d{15})(?![0-9Xx])")
BANK_CARD_PATTERN = re.compile(r"(?<!\d)(\d{12,19})(?!\d)")


def _mask_middle(value: str, keep_left: int, keep_right: int) -> str:
    text = str(value or "")
    if len(text) <= keep_left + keep_right:
        return text
    return f"{text[:keep_left]}{'*' * (len(text) - keep_left - keep_right)}{text[-keep_right:]}"


def _mask_phone_match(match: re.Match[str]) -> str:
    return _mask_middle(match.group(1), 3, 2)


def _mask_id_card_match(match: re.Match[str]) -> str:
    return _mask_middle(match.group(1), 6, 4)


def _mask_bank_card_match(match: re.Match[str]) -> str:
    return _mask_middle(match.group(1), 4, 4)


def mask_sensitive_text(value: str) -> str:
    text = str(value or "")
    text = PHONE_PATTERN.sub(_mask_phone_match, text)
    text = ID_CARD_PATTERN.sub(_mask_id_card_match, text)
    text = BANK_CARD_PATTERN.sub(_mask_bank_card_match, text)
    return text


def mask_sensitive_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {str(key): mask_sensitive_payload(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [mask_sensitive_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return [mask_sensitive_payload(item) for item in payload]
    if isinstance(payload, str):
        return mask_sensitive_text(payload)
    return payload


def dump_masked_payload(payload: Any) -> str:
    masked = mask_sensitive_payload(payload)
    if isinstance(masked, str):
        return masked
    try:
        return json.dumps(masked, ensure_ascii=False, default=str)
    except Exception:
        return mask_sensitive_text(str(masked))
