"""Shared filter normalization helpers for semantic SQL compilation."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .common import is_time_logical_dimension


def split_text_between_filter(node: dict[str, Any]) -> dict[str, Any]:
    """Convert text BETWEEN values like ``a AND b`` into the list form expected by the SQL compiler."""
    operator = str(node.get("operator", "")).strip().upper()
    value = node.get("value")
    if operator != "BETWEEN" or isinstance(value, list) or not isinstance(value, str):
        return node
    parts = re.split(r"\s+AND\s+", value.strip(), maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return node
    normalized = dict(node)
    normalized["value"] = [parts[0].strip(), parts[1].strip()]
    return normalized


def is_time_dimension(config: dict[str, Any]) -> bool:
    """Return whether a semantic field is configured as a time dimension."""
    return is_time_logical_dimension(config)


def time_equal_value_to_range(value: Any) -> tuple[str, str] | None:
    """Map year/month/day equality values to a half-open timestamp range."""
    if isinstance(value, int) and 1000 <= value <= 9999:
        return f"{value:04d}-01-01 00:00:00", f"{value + 1:04d}-01-01 00:00:00"
    if not isinstance(value, str):
        return None

    text = value.strip()
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        return f"{year:04d}-01-01 00:00:00", f"{year + 1:04d}-01-01 00:00:00"
    if re.fullmatch(r"\d{4}-\d{2}", text):
        try:
            start = datetime.strptime(text, "%Y-%m")
        except ValueError:
            return None
        end_year = start.year + (1 if start.month == 12 else 0)
        end_month = 1 if start.month == 12 else start.month + 1
        return f"{start.year:04d}-{start.month:02d}-01 00:00:00", f"{end_year:04d}-{end_month:02d}-01 00:00:00"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            start = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        next_day = start + timedelta(days=1)
        return (
            f"{start.year:04d}-{start.month:02d}-{start.day:02d} 00:00:00",
            f"{next_day.year:04d}-{next_day.month:02d}-{next_day.day:02d} 00:00:00",
        )
    return None


def normalize_time_filter_leaf(node: dict[str, Any], dim_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a single semantic time filter leaf for builder compilation."""
    normalized_node = split_text_between_filter(node)
    if normalized_node is not node:
        return [normalized_node]
    if not is_time_dimension(dim_config):
        return [node]

    operator = str(node.get("operator", "=")).strip().upper()
    if operator != "=" or "value_type" in node or "valueType" in node:
        return [node]
    date_range = time_equal_value_to_range(node.get("value"))
    if not date_range:
        return [node]

    start_value, end_value = date_range
    base = {key: value for key, value in node.items() if key not in {"operator", "value", "value_type", "valueType"}}
    return [
        {**base, "operator": ">=", "value": start_value},
        {**base, "operator": "<", "value": end_value},
    ]
