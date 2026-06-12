"""Build chart_spec from one SQL query result.

Usage:
    python single_result_chart.py --input query_result.json --output sales.chart.json \
        --title "月度销售额趋势" --chart-type line --x-field month --y-fields sales
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


def columns_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in columns:
                columns.append(key)
    return columns


def is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value))
        return True
    except ValueError:
        return False


def infer_numeric_columns(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    numeric: list[str] = []
    sample = rows[:20]
    for column in columns:
        values = [row.get(column) for row in sample if row.get(column) is not None]
        if values and all(is_number(value) for value in values):
            numeric.append(column)
    return numeric


def infer_x_field(columns: list[str], numeric_columns: list[str]) -> str:
    non_numeric = [column for column in columns if column not in numeric_columns]
    if non_numeric:
        return non_numeric[0]
    if columns:
        return columns[0]
    raise ValueError("cannot infer x field from empty columns")


def infer_chart_type(x_field: str, y_fields: list[str], rows: list[dict[str, Any]]) -> str:
    lowered = x_field.lower()
    if any(token in lowered for token in ("date", "time", "month", "day", "year", "period")):
        return "line"
    if len(y_fields) == 1 and 2 <= len(rows) <= 10:
        return "pie"
    return "bar"


def infer_graph_y_fields(columns: list[str], x_field: str, numeric_columns: list[str]) -> list[str]:
    target_field = next((column for column in columns if column != x_field and column not in numeric_columns), "")
    if not target_field:
        target_field = next((column for column in columns if column != x_field), "")
    if not target_field:
        return []
    value_field = next((column for column in numeric_columns if column not in {x_field, target_field}), "")
    return [target_field, value_field] if value_field else [target_field]


def normalize_rows(rows: list[dict[str, Any]], y_fields: list[str], chart_type: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        numeric_y_fields = y_fields[1:] if chart_type == "graph" else y_fields
        for field in numeric_y_fields:
            value = item.get(field)
            item[field] = float(value) if is_number(value) else None
        normalized.append(item)
    return normalized


def build_chart_spec(args: argparse.Namespace) -> dict[str, Any]:
    payload = load_json(args.input)
    rows = rows_from_query_result(payload)
    if not rows:
        raise ValueError("query result has no rows")
    columns = payload.get("columns") if isinstance(payload, dict) and isinstance(payload.get("columns"), list) else columns_from_rows(rows)
    numeric_columns = infer_numeric_columns(rows, columns)
    x_field = args.x_field or infer_x_field(columns, numeric_columns)
    chart_type = args.chart_type or infer_chart_type(x_field, args.y_fields or numeric_columns[:3], rows)
    y_fields = args.y_fields or (infer_graph_y_fields(columns, x_field, numeric_columns) if chart_type == "graph" else numeric_columns[:3])
    if not y_fields:
        raise ValueError("at least one y field is required")
    data = normalize_rows(rows, y_fields, chart_type)
    series = [
        {"field": field, "label": field, "format": "text" if chart_type == "graph" and index == 0 else "number"}
        for index, field in enumerate(y_fields)
    ]
    return {
        "version": "1.0",
        "title": args.title,
        "chart_type": chart_type,
        "data": data,
        "encoding": {"x": x_field, "y": y_fields},
        "series": series,
        "sources": [{"name": "main", "sql": payload.get("sql") if isinstance(payload, dict) else None}],
        "meta": {"missing_policy": "keep_null", "generated_by": "single_result_chart.py"},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--chart-type", choices=["table", "kpi", "bar", "horizontal_bar", "line", "area", "pie", "map", "combo", "funnel", "graph"])
    parser.add_argument("--x-field")
    parser.add_argument("--y-fields", nargs="+")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dump_json(args.output, build_chart_spec(args))


if __name__ == "__main__":
    main()
