"""Output artifact registry shared by local and sandbox execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable

from ..agent.skills.loader import parse_metadata_text
from ..chart_types import (
    chart_type_max_y_fields,
    chart_type_requires_data,
    chart_type_requires_x,
    chart_type_requires_y,
    is_supported_chart_type_input,
    normalize_chart_type,
)

ArtifactMatcher = Callable[[PurePosixPath], bool]
ArtifactParser = Callable[[PurePosixPath, bytes], dict[str, Any] | None]


@dataclass(frozen=True)
class ArtifactDefinition:
    """One final-output type registered for explicit presentation."""

    artifact_type: str
    renderer: str
    matches: ArtifactMatcher
    parse: ArtifactParser


def parse_chart_spec_payload_value(payload: Any) -> dict[str, Any] | None:
    """Validate and normalize a chart specification payload."""
    if not isinstance(payload, dict) or not is_supported_chart_type_input(payload.get("chart_type")):
        return None
    chart_type = normalize_chart_type(payload.get("chart_type"))
    payload["chart_type"] = chart_type
    if str(payload.get("version") or "").strip() != "1.0":
        return None
    if chart_type == "graph":
        graph_payload = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        links = graph_payload.get("links") if isinstance(graph_payload.get("links"), list) else graph_payload.get("edges")
        if isinstance(graph_payload.get("nodes"), list) and isinstance(links, list) and graph_payload.get("nodes") and links:
            return payload
    if not isinstance(payload.get("data"), list):
        return None
    data = payload["data"]
    if chart_type_requires_data(chart_type) and not data:
        return None
    encoding = payload.get("encoding")
    if not isinstance(encoding, dict):
        return None
    y_fields = encoding.get("y")
    if chart_type_requires_y(chart_type) and (not isinstance(y_fields, list) or not any(str(item).strip() for item in y_fields)):
        return None
    max_y_fields = chart_type_max_y_fields(chart_type)
    if max_y_fields is not None and isinstance(y_fields, list) and len([item for item in y_fields if str(item).strip()]) > max_y_fields:
        return None
    if chart_type_requires_x(chart_type) and not str(encoding.get("x") or "").strip():
        return None
    if data:
        row_fields = {str(key) for row in data if isinstance(row, dict) for key in row}
        x_field = str(encoding.get("x") or "").strip()
        if chart_type_requires_x(chart_type) and x_field and x_field not in row_fields:
            return None
        if isinstance(y_fields, list) and any(str(item or "").strip() not in row_fields for item in y_fields if str(item or "").strip()):
            return None
    return payload


def _json_payload(data: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _matches_skill(path: PurePosixPath) -> bool:
    return path.name == "SKILL.md"


def _matches_json(path: PurePosixPath) -> bool:
    return path.name.lower().endswith(".json")


def _matches_suffixes(*suffixes: str) -> ArtifactMatcher:
    return lambda path: path.name.lower().endswith(suffixes)


def _skill_artifact(path: PurePosixPath, data: bytes) -> dict[str, Any] | None:
    if len(path.parts) != 3 or path.parts[0] != "outputs" or path.parts[1].startswith("."):
        return None
    skill_name = path.parts[1]
    metadata = parse_metadata_text(data.decode("utf-8", errors="replace"), path=path.as_posix())
    if not metadata or metadata.get("name") != skill_name:
        return None
    return {
        "name": skill_name,
        "skill_title": metadata.get("title") or skill_name,
        "description": metadata.get("description") or "",
    }


def _dashboard_artifact(_path: PurePosixPath, data: bytes) -> dict[str, Any] | None:
    payload = _json_payload(data)
    if not isinstance(payload, dict) or not isinstance(payload.get("cards"), list):
        return None
    return {"artifact_payload": payload, "dashboard_spec": payload}


def _chart_artifact(_path: PurePosixPath, data: bytes) -> dict[str, Any] | None:
    payload = parse_chart_spec_payload_value(_json_payload(data))
    if payload is None:
        return None
    return {"artifact_payload": payload, "chart_spec": payload}


def _generic_artifact(_path: PurePosixPath, _data: bytes) -> dict[str, Any]:
    return {}


ARTIFACT_DEFINITIONS: tuple[ArtifactDefinition, ...] = (
    ArtifactDefinition("skill", "skill", _matches_skill, _skill_artifact),
    # Suffix-specific output formats take precedence over generic JSON payload
    # inspection. A table export can legitimately contain chart-shaped fields.
    ArtifactDefinition("table", "table", _matches_suffixes(".table.json", ".table.csv"), _generic_artifact),
    ArtifactDefinition("document", "document", _matches_suffixes(".docx"), _generic_artifact),
    ArtifactDefinition("spreadsheet", "spreadsheet", _matches_suffixes(".xlsx"), _generic_artifact),
    ArtifactDefinition("pdf", "pdf", _matches_suffixes(".pdf"), _generic_artifact),
    ArtifactDefinition("image", "image", _matches_suffixes(".png", ".jpg", ".jpeg", ".gif", ".webp"), _generic_artifact),
    ArtifactDefinition("dashboard_spec", "dashboard", _matches_json, _dashboard_artifact),
    ArtifactDefinition("chart_spec", "chart", _matches_json, _chart_artifact),
)
ARTIFACT_REGISTRY = {definition.artifact_type: definition for definition in ARTIFACT_DEFINITIONS}


def _output_relative_path(visible_path: str) -> PurePosixPath | None:
    raw = str(visible_path or "").strip().replace("\\", "/")
    prefixes = ("/mnt/user-data/", "/", "")
    for prefix in prefixes:
        if raw.startswith(prefix):
            relative = raw[len(prefix):]
            if relative == "outputs" or relative.startswith("outputs/"):
                return PurePosixPath(relative)
    return None


def parse_output_artifact(*, visible_path: str, data: bytes) -> dict[str, Any]:
    """Convert a final output file into the stable presentation payload."""
    path = _output_relative_path(visible_path)
    if path is None:
        return {}
    for definition in ARTIFACT_DEFINITIONS:
        if not definition.matches(path):
            continue
        payload = definition.parse(path, data)
        if payload is not None:
            return {
                "file_path": visible_path,
                "artifact_type": definition.artifact_type,
                "renderer": definition.renderer,
                "artifact_version": "v1",
                **payload,
            }
    return {}
