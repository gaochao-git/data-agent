"""Build chart_spec by joining multiple query results as chart series.

The manifest keeps model-generated code small and auditable.

Manifest example:
{
  "title": "多口径趋势对比",
  "chart_type": "line",
  "key_field": "month",
  "missing_policy": "fill_zero",
  "series": [
    {"label": "订单数", "path": "orders.json", "value_field": "cnt"},
    {"label": "销售额", "path": "sales.json", "value_field": "amount"}
  ]
}

Usage:
    python join_results_chart.py --manifest manifest.json --output sales.chart.json
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


def safe_field(label: str, index: int) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in label.strip())
    normalized = "_".join(part for part in normalized.split("_") if part)
    return normalized or f"series_{index + 1}"


def to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def build_series_map(rows: list[dict[str, Any]], key_field: str, value_field: str) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for row in rows:
        key = row.get(key_field)
        if key is None:
            continue
        result[str(key)] = to_number(row.get(value_field))
    return result


def normalize_series(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    series = manifest.get("series")
    if not isinstance(series, list) or not series:
        raise ValueError("manifest.series must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(series):
        if not isinstance(item, dict):
            raise ValueError("each series item must be an object")
        label = str(item.get("label") or f"系列{index + 1}")
        path = str(item.get("path") or "").strip()
        value_field = str(item.get("value_field") or "").strip()
        if not path or not value_field:
            raise ValueError("series item requires path and value_field")
        normalized.append({
            "label": label,
            "field": str(item.get("field") or safe_field(label, index)),
            "path": path,
            "key_field": str(item.get("key_field") or manifest["key_field"]),
            "value_field": value_field,
        })
    return normalized


def apply_missing_policy(value: float | None, policy: str) -> float | None:
    if value is None and policy == "fill_zero":
        return 0.0
    return value


def build_chart_spec(manifest: dict[str, Any]) -> dict[str, Any]:
    key_field = str(manifest.get("key_field") or "").strip()
    if not key_field:
        raise ValueError("manifest.key_field is required")
    missing_policy = str(manifest.get("missing_policy") or "fill_zero")
    if missing_policy not in {"fill_zero", "keep_null", "drop_missing"}:
        raise ValueError("missing_policy must be fill_zero, keep_null, or drop_missing")

    chart_type = str(manifest.get("chart_type") or "line")
    series_items = normalize_series(manifest)
    if chart_type == "combo":
        series_items = series_items[:2]
    maps: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    all_keys: set[str] = set()
    for item in series_items:
        payload = load_json(item["path"])
        value_map = build_series_map(rows_from_query_result(payload), item["key_field"], item["value_field"])
        maps.append(value_map)
        all_keys.update(value_map.keys())
        sources.append({"name": item["label"], "sql": payload.get("sql") if isinstance(payload, dict) else None})

    data = []
    for key in sorted(all_keys):
        row = {key_field: key}
        missing = False
        for item, value_map in zip(series_items, maps):
            value = apply_missing_policy(value_map.get(key), missing_policy)
            if value is None:
                missing = True
            row[item["field"]] = value
        if missing_policy == "drop_missing" and missing:
            continue
        data.append(row)

    y_fields = [item["field"] for item in series_items]
    if str(manifest.get("chart_type") or "").strip() == "combo":
        y_fields = y_fields[:2]
    return {
        "version": "1.0",
        "title": str(manifest.get("title") or "多系列图表"),
        "chart_type": chart_type,
        "data": data,
        "encoding": {"x": key_field, "y": y_fields},
        "series": [{"field": item["field"], "label": item["label"], "format": "number"} for item in series_items],
        "sources": sources,
        "meta": {"missing_policy": missing_policy, "generated_by": "join_results_chart.py"},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = load_json(args.manifest)
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    dump_json(args.output, build_chart_spec(manifest))


if __name__ == "__main__":
    main()
