"""Shared artifact payload helpers for data analyze tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...services.artifact_registry import parse_chart_spec_payload_value, parse_output_artifact
from ...services.conversation_workspace import (
    is_tool_result_workspace_path,
    resolve_workspace_path,
    to_visible_path,
)

def _iter_targets(root: str, targets: list[str | Path]):
    for item in reversed(targets):
        if isinstance(item, Path):
            yield item
        else:
            yield resolve_workspace_path(root, item)


def _is_outputs_target(root: str, target: Path) -> bool:
    if is_tool_result_workspace_path(root, target):
        return False
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
        return parse_chart_spec_payload_value(json.loads(target.read_text(encoding="utf-8")))
    except Exception:
        return None


def load_output_artifact_payload(
    root: str,
    targets: list[str | Path],
    *,
    artifact_type: str | None = None,
) -> dict[str, Any]:
    """Read local output files then delegate type recognition to the registry."""
    for target in _iter_targets(root, targets):
        if not _is_outputs_target(root, target) or not target.exists() or not target.is_file():
            continue
        try:
            payload = parse_output_artifact(
                visible_path=to_visible_path(root, target),
                data=target.read_bytes(),
            )
        except OSError:
            continue
        if payload and (artifact_type is None or payload.get("artifact_type") == artifact_type):
            return payload
    return {}


def load_skill_artifact_payload(root: str, targets: list[str | Path]) -> dict[str, Any]:
    return load_output_artifact_payload(root, targets, artifact_type="skill")


def load_chart_spec_artifact_payload(root: str, targets: list[str | Path]) -> dict[str, Any]:
    return load_output_artifact_payload(root, targets, artifact_type="chart_spec")


def load_dashboard_spec_artifact_payload(root: str, targets: list[str | Path]) -> dict[str, Any]:
    return load_output_artifact_payload(root, targets, artifact_type="dashboard_spec")
