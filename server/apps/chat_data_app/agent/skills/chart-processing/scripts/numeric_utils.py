"""Shared numeric safety checks for chart-processing scripts."""

from __future__ import annotations

import math
import re
from typing import Any


JS_SAFE_INTEGER_MAX = 2**53 - 1
NUMERIC_TEXT_PATTERN = re.compile(r"^-?(?:\d+|\d*\.\d+)$")


def _normalize_decimal_text(value: str) -> str:
    is_negative = value.startswith("-")
    unsigned_value = value[1:] if is_negative else value
    integer_part, _, fraction_part = unsigned_value.partition(".")
    normalized_integer = integer_part.lstrip("0") or "0"
    normalized_fraction = fraction_part.rstrip("0")
    normalized_value = f"{normalized_integer}.{normalized_fraction}" if normalized_fraction else normalized_integer
    return f"-{normalized_value}" if is_negative and normalized_value != "0" else normalized_value


def to_safe_number(value: Any) -> float | None:
    """Return a float only when conversion preserves the source numeric value."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        if abs(value) > JS_SAFE_INTEGER_MAX:
            raise ValueError("numeric value exceeds JavaScript safe integer precision")
        return float(value)
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > JS_SAFE_INTEGER_MAX:
            raise ValueError("numeric value exceeds JavaScript safe precision")
        return value
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not NUMERIC_TEXT_PATTERN.fullmatch(text):
        return None
    normalized = _normalize_decimal_text(text)
    number = float(normalized)
    if not math.isfinite(number) or abs(number) > JS_SAFE_INTEGER_MAX:
        raise ValueError("numeric value exceeds JavaScript safe precision")

    fraction_length = len(normalized.partition(".")[2])
    if fraction_length == 0:
        if not number.is_integer():
            raise ValueError("numeric value cannot be represented safely")
        return number
    if fraction_length > 100 or _normalize_decimal_text(f"{number:.{fraction_length}f}") != normalized:
        raise ValueError("decimal value cannot be represented safely")
    return number
