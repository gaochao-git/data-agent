"""Accuracy scorers for ChatData evaluation cases."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


SLOT_KEYS = ("metrics", "dimensions", "filters", "having", "order_by", "limit", "offset")
DATETIME_STORAGE_FORMAT = "%Y-%m-%d %H:%M:%S"


def normalize_dimension(value: Any) -> Any:
    if isinstance(value, dict):
        name = value.get("name")
        time_grain = value.get("time_grain") or value.get("time_granularity")
        if not time_grain:
            return name
        return {"name": name, "time_grain": time_grain}
    return value


def normalize_list(value: Any, *, dimensions: bool = False) -> list[Any]:
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
    normalized = [normalize_dimension(item) if dimensions else item for item in items]
    return sorted(normalized, key=lambda item: str(item))


def is_temporal_dimension(value: Any) -> bool:
    text = str(value or "").lower()
    return any(token in text for token in ("时间", "日期", "time", "date"))


def parse_temporal_value(value: Any) -> tuple[datetime, str] | None:
    text = str(value or "").strip().replace("T", " ")
    for pattern, precision in (
        ("%Y-%m-%d %H:%M:%S", "second"),
        ("%Y-%m-%d", "day"),
        ("%Y-%m", "month"),
    ):
        try:
            return datetime.strptime(text, pattern), precision
        except ValueError:
            continue
    return None


def add_month(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    return value.replace(year=year, month=month)


def format_temporal_value(value: datetime) -> str:
    return value.strftime(DATETIME_STORAGE_FORMAT)


def normalize_temporal_lower(value: Any) -> str | None:
    parsed = parse_temporal_value(value)
    if not parsed:
        return None
    moment, _precision = parsed
    return format_temporal_value(moment)


def normalize_temporal_exclusive_upper(value: Any, *, inclusive: bool) -> str | None:
    parsed = parse_temporal_value(value)
    if not parsed:
        return None
    moment, precision = parsed
    if inclusive:
        if precision == "month":
            moment = add_month(moment)
        elif precision == "day":
            moment = moment + timedelta(days=1)
        else:
            moment = moment + timedelta(seconds=1)
    return format_temporal_value(moment)


def normalize_filter(filter_item: Any) -> list[Any]:
    if not isinstance(filter_item, dict):
        return [filter_item]
    dimension = filter_item.get("dimension") or filter_item.get("field") or filter_item.get("name")
    operator = str(filter_item.get("operator") or "").strip().lower()
    value = filter_item.get("value")

    if is_temporal_dimension(dimension) and operator == "between" and isinstance(value, (list, tuple)) and len(value) == 2:
        lower = normalize_temporal_lower(value[0])
        upper = normalize_temporal_exclusive_upper(value[1], inclusive=True)
        if lower and upper:
            return [
                {"dimension": dimension, "operator": ">=", "value": lower},
                {"dimension": dimension, "operator": "<", "value": upper},
            ]

    if is_temporal_dimension(dimension):
        normalized_value = normalize_temporal_lower(value)
        if operator in (">=", ">") and normalized_value:
            return [{"dimension": dimension, "operator": operator, "value": normalized_value}]
        if operator == "<":
            upper = normalize_temporal_exclusive_upper(value, inclusive=False)
            if upper:
                return [{"dimension": dimension, "operator": "<", "value": upper}]
        if operator == "<=":
            upper = normalize_temporal_exclusive_upper(value, inclusive=True)
            if upper:
                return [{"dimension": dimension, "operator": "<", "value": upper}]

    return [{
        "dimension": dimension,
        "operator": filter_item.get("operator"),
        "value": value,
    }]


def normalize_filters(value: Any) -> list[Any]:
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
    normalized: list[Any] = []
    for item in items:
        normalized.extend(normalize_filter(item))
    return sorted(normalized, key=lambda item: str(item))


def normalize_int_value(value: Any) -> int | Any:
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def normalize_dsl(dsl: dict[str, Any] | None) -> dict[str, Any]:
    payload = dsl or {}
    normalized = {
        "metrics": normalize_list(payload.get("metrics")),
        "dimensions": normalize_list(payload.get("dimensions"), dimensions=True),
        "filters": normalize_filters(payload.get("filters")),
        "having": normalize_list(payload.get("having")),
        "order_by": normalize_list(payload.get("order_by")),
        "limit": normalize_int_value(payload.get("limit")),
        "offset": normalize_int_value(payload.get("offset")),
    }
    return normalized


def score_dsl(predicted: dict[str, Any] | None, expected: dict[str, Any] | None) -> dict[str, Any]:
    """Score predicted DSL against expected DSL by exact match and slot accuracy."""
    predicted_norm = normalize_dsl(predicted)
    expected_norm = normalize_dsl(expected)
    slot_scores: dict[str, float] = {}
    for key in SLOT_KEYS:
        if key == "order_by" and expected_norm.get(key) in (None, {}, []):
            slot_scores[key] = 1.0
            continue
        if key not in expected_norm or expected_norm.get(key) in (None, {}, []):
            slot_scores[key] = 1.0 if predicted_norm.get(key) in (None, {}, []) else 0.0
        else:
            slot_scores[key] = 1.0 if predicted_norm.get(key) == expected_norm.get(key) else 0.0
    slot_accuracy = sum(slot_scores.values()) / len(slot_scores)
    return {
        "dsl_exact_match": all(score == 1.0 for score in slot_scores.values()),
        "dsl_slot_accuracy": slot_accuracy,
        "semantic_accuracy": slot_accuracy,
        "slot_scores": slot_scores,
        "predicted_dsl": predicted_norm,
        "expected_dsl": expected_norm,
    }
