"""Shared artifact payload helpers for data analyze tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...chart_types import chart_type_max_y_fields, chart_type_requires_data, chart_type_requires_x, chart_type_requires_y, is_supported_chart_type_input, normalize_chart_type
from ...services.data_analyze_workspace import resolve_workspace_path, to_visible_path

def _iter_targets(root: str, targets: list[str | Path]):
    for item in reversed(targets):
        if isinstance(item, Path):
            yield item
        else:
            yield resolve_workspace_path(root, item)


def _is_outputs_target(root: str, target: Path) -> bool:
    outputs_root = resolve_workspace_path(root, "outputs")
    try:
        target.resolve().relative_to(outputs_root.resolve())
        return True
    except ValueError:
        return False


def parse_chart_spec_payload(target: Path) -> dict[str, Any] | None:
    if not target.exists() or not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if not is_supported_chart_type_input(payload.get("chart_type")):
        return None
    chart_type = normalize_chart_type(payload.get("chart_type"))
    payload["chart_type"] = chart_type
    if str(payload.get("version") or "").strip() != "1.0":
        return None
    if chart_type == "graph":
        graph_payload = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        links = graph_payload.get("links") if isinstance(graph_payload.get("links"), list) else graph_payload.get("edges")
        if isinstance(graph_payload.get("nodes"), list) and isinstance(links, list):
            if graph_payload.get("nodes") and links:
                return payload
    if not isinstance(payload.get("data"), list):
        return None
    data = payload.get("data")
    if chart_type_requires_data(chart_type) and not data:
        return None
    encoding = payload.get("encoding")
    if not isinstance(encoding, dict):
        return None
    y_fields = encoding.get("y")
    if chart_type_requires_y(chart_type):
        if not isinstance(y_fields, list) or not any(str(item).strip() for item in y_fields):
            return None
        max_y_fields = chart_type_max_y_fields(chart_type)
        if max_y_fields is not None and len([item for item in y_fields if str(item).strip()]) > max_y_fields:
            return None
    if chart_type_requires_x(chart_type):
        if not str(encoding.get("x") or "").strip():
            return None
    if data:
        row_fields = set()
        for row in data:
            if isinstance(row, dict):
                row_fields.update(str(key) for key in row.keys())
        x_field = str(encoding.get("x") or "").strip()
        if chart_type_requires_x(chart_type) and x_field and x_field not in row_fields:
            return None
        if isinstance(y_fields, list):
            for item in y_fields:
                y_field = str(item or "").strip()
                if y_field and y_field not in row_fields:
                    return None
    return payload


def load_chart_spec_artifact_payload(root: str, targets: list[str | Path]) -> dict[str, Any]:
    for target in _iter_targets(root, targets):
        if not _is_outputs_target(root, target):
            continue
        payload = parse_chart_spec_payload(target)
        if payload is None:
            continue
        return {
            "file_path": to_visible_path(root, target),
            "artifact_type": "chart_spec",
            "artifact_version": "v1",
            "artifact_payload": payload,
            "chart_spec": payload,
        }
    return {}


def load_dashboard_spec_artifact_payload(root: str, targets: list[str | Path]) -> dict[str, Any]:
    for target in _iter_targets(root, targets):
        if not _is_outputs_target(root, target):
            continue
        if not target.exists() or not target.is_file():
            continue
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("cards"), list):
            visible_path = to_visible_path(root, target)
            return {
                "file_path": visible_path,
                "artifact_type": "dashboard_spec",
                "artifact_version": "v1",
                "artifact_payload": payload,
                "dashboard_spec": payload,
            }
    return {}
