"""Build chart_spec for current/previous period comparison.

Usage:
    python compare_period_chart.py --current current.json --previous previous.json \
        --output sales.chart.json --title "销售额环比" --key-field month --value-field sales
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump_json(path: str, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def rows_from_query_result(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [row for row in payload["data"] if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    raise ValueError("input must be a query result object with data[] or a row array")


def to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def build_value_map(rows: list[dict[str, Any]], key_field: str, value_field: str) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for row in rows:
        key = row.get(key_field)
        if key is None:
            continue
        result[str(key)] = to_number(row.get(value_field))
    return result


def month_of_year_key(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 7 and text[4] == "-":
        return text[5:7]
    return text


def month_label(value: str) -> str:
    try:
        return f"{int(value)}月"
    except ValueError:
        return value


def build_aligned_value_map(rows: list[dict[str, Any]], key_field: str, value_field: str, align_by: str) -> tuple[dict[str, float | None], dict[str, str]]:
    result: dict[str, float | None] = {}
    labels: dict[str, str] = {}
    for row in rows:
        raw_key = row.get(key_field)
        if raw_key is None:
            continue
        key = month_of_year_key(raw_key) if align_by == "month-of-year" else str(raw_key)
        result[key] = to_number(row.get(value_field))
        labels[key] = str(raw_key)
    return result, labels


def sorted_keys(*maps: dict[str, Any]) -> list[str]:
    keys: set[str] = set()
    for item in maps:
        keys.update(item.keys())
    return sorted(keys)


def rate(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / previous


def build_chart_spec(args: argparse.Namespace) -> dict[str, Any]:
    current_payload = load_json(args.current)
    previous_payload = load_json(args.previous)
    current_map, current_labels = build_aligned_value_map(rows_from_query_result(current_payload), args.key_field, args.value_field, args.align_by)
    previous_map, previous_labels = build_aligned_value_map(rows_from_query_result(previous_payload), args.key_field, args.value_field, args.align_by)
    data = []
    x_field = "month_label" if args.align_by == "month-of-year" else args.key_field
    for key in sorted_keys(current_map, previous_map):
        current = current_map.get(key)
        previous = previous_map.get(key)
        if args.missing_policy == "fill_zero":
            current = 0.0 if current is None else current
            previous = 0.0 if previous is None else previous
        if args.missing_policy == "drop_missing" and (current is None or previous is None):
            continue
        diff = None if current is None or previous is None else current - previous
        row = {
            args.key_field: previous_labels.get(key) or current_labels.get(key) or key,
            "compare_period": current_labels.get(key),
            args.current_field: current,
            args.previous_field: previous,
            "diff": diff,
            "change_rate": rate(current, previous),
        }
        if args.align_by == "month-of-year":
            row["month_no"] = key.zfill(2) if key.isdigit() else key
            row["month_label"] = month_label(key)
        data.append(row)
    return {
        "version": "1.0",
        "title": args.title,
        "chart_type": args.chart_type,
        "data": data,
        "encoding": {"x": x_field, "y": [args.current_field, args.previous_field]},
        "series": [
            {"field": args.current_field, "label": args.current_label, "format": "number"},
            {"field": args.previous_field, "label": args.previous_label, "format": "number"},
            {"field": "change_rate", "label": args.rate_label, "format": "percent"},
        ],
        "sources": [
            {"name": "current", "sql": current_payload.get("sql") if isinstance(current_payload, dict) else None},
            {"name": "previous", "sql": previous_payload.get("sql") if isinstance(previous_payload, dict) else None},
        ],
        "meta": {
            "missing_policy": args.missing_policy,
            "align_by": args.align_by,
            "value_field": args.value_field,
            "sort_field": "month_no" if args.align_by == "month-of-year" else args.key_field,
            "generated_by": "compare_period_chart.py",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", required=True)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--key-field", required=True)
    parser.add_argument("--value-field", required=True)
    parser.add_argument("--chart-type", default="line", choices=["bar", "line"])
    parser.add_argument("--current-field", default="current")
    parser.add_argument("--previous-field", default="previous")
    parser.add_argument("--current-label", default="本期")
    parser.add_argument("--previous-label", default="上期")
    parser.add_argument("--rate-label", default="变化率")
    parser.add_argument("--missing-policy", default="fill_zero", choices=["fill_zero", "keep_null", "drop_missing"])
    parser.add_argument("--align-by", default="exact", choices=["exact", "month-of-year"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dump_json(args.output, build_chart_spec(args))


if __name__ == "__main__":
    main()
