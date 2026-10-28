"""Output artifact registry shared by local and sandbox execution."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ...chart_types import (
    chart_type_max_y_fields,
    chart_type_requires_data,
    chart_type_requires_x,
    chart_type_requires_y,
    is_supported_chart_type_input,
    normalize_chart_type,
)
from ..contracts.skill_metadata import parse_metadata_text

ArtifactMatcher = Callable[[PurePosixPath], bool]
ArtifactParser = Callable[[PurePosixPath, bytes], dict[str, Any] | None]


@dataclass(frozen=True)
class ArtifactDefinition:
    """One final-output type registered for explicit presentation."""

    artifact_type: str
    renderer: str
    matches: ArtifactMatcher
    parse: ArtifactParser


def _chart_spec_graph_passthrough(chart_type: str, payload: dict[str, Any]) -> bool:
    """graph 类型只要 nodes/links 合法即通过，无需 data/encoding。"""
    if chart_type != "graph":
        return False
    data_payload = payload.get("data")
    graph_payload: dict[str, Any] = data_payload if isinstance(data_payload, dict) else payload
    links = graph_payload.get("links") if isinstance(graph_payload.get("links"), list) else graph_payload.get("edges")
    return bool(
        isinstance(graph_payload.get("nodes"), list)
        and isinstance(links, list)
        and graph_payload.get("nodes")
        and links
    )


def _chart_spec_encoding_valid(chart_type: str, encoding: Any) -> bool:
    if not isinstance(encoding, dict):
        return False
    y_fields = encoding.get("y")
    if chart_type_requires_y(chart_type) and (
        not isinstance(y_fields, list) or not any(str(item).strip() for item in y_fields)
    ):
        return False
    max_y_fields = chart_type_max_y_fields(chart_type)
    if (
        max_y_fields is not None
        and isinstance(y_fields, list)
        and len([item for item in y_fields if str(item).strip()]) > max_y_fields
    ):
        return False
    return not (chart_type_requires_x(chart_type) and not str(encoding.get("x") or "").strip())


def _chart_spec_fields_exist(chart_type: str, data: list, encoding: dict[str, Any], y_fields: Any) -> bool:
    row_fields = {str(key) for row in data if isinstance(row, dict) for key in row}
    x_field = str(encoding.get("x") or "").strip()
    if chart_type_requires_x(chart_type) and x_field and x_field not in row_fields:
        return False
    return not (
        isinstance(y_fields, list)
        and any(str(item or "").strip() not in row_fields for item in y_fields if str(item or "").strip())
    )


def parse_chart_spec_payload_value(payload: Any) -> dict[str, Any] | None:
    """Validate and normalize a chart specification payload."""
    if not isinstance(payload, dict) or not is_supported_chart_type_input(payload.get("chart_type")):
        return None
    chart_type = normalize_chart_type(payload.get("chart_type"))
    payload["chart_type"] = chart_type
    if str(payload.get("version") or "").strip() != "1.0":
        return None
    if _chart_spec_graph_passthrough(chart_type, payload):
        return payload
    if not isinstance(payload.get("data"), list):
        return None
    data = payload["data"]
    if chart_type_requires_data(chart_type) and not data:
        return None
    encoding = payload.get("encoding")
    if not isinstance(encoding, dict) or not _chart_spec_encoding_valid(chart_type, encoding):
        return None
    if data and not _chart_spec_fields_exist(chart_type, data, encoding, encoding.get("y")):
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


def _matches_markdown(path: PurePosixPath) -> bool:
    """Accept regular Markdown outputs but reserve SKILL.md for skill import."""
    return path.name.lower().endswith(".md") and path.name.lower() != "skill.md"


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
    ArtifactDefinition("presentation", "presentation", _matches_suffixes(".pptx"), _generic_artifact),
    ArtifactDefinition("pdf", "pdf", _matches_suffixes(".pdf"), _generic_artifact),
    ArtifactDefinition(
        "image", "image", _matches_suffixes(".png", ".jpg", ".jpeg", ".gif", ".webp"), _generic_artifact
    ),
    ArtifactDefinition("markdown", "markdown", _matches_markdown, _generic_artifact),
    # 自包含 HTML 页面（内联 CSS/JS/数据），前端以沙箱 iframe 渲染
    ArtifactDefinition("html", "html", _matches_suffixes(".html", ".htm"), _generic_artifact),
    # YAML 产物（如回导用的语义模型快照），前端以纯文本预览
    ArtifactDefinition("yaml", "yaml", _matches_suffixes(".yaml", ".yml"), _generic_artifact),
    ArtifactDefinition("dashboard_spec", "dashboard", _matches_json, _dashboard_artifact),
    ArtifactDefinition("chart_spec", "chart", _matches_json, _chart_artifact),
)
ARTIFACT_REGISTRY = {definition.artifact_type: definition for definition in ARTIFACT_DEFINITIONS}


def _output_relative_path(visible_path: str) -> PurePosixPath | None:
    raw = str(visible_path or "").strip().replace("\\", "/")
    prefixes = ("/mnt/user-data/", "/", "")
    for prefix in prefixes:
        if raw.startswith(prefix):
            relative = raw[len(prefix) :]
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
