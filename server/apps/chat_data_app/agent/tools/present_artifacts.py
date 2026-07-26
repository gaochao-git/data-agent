"""ChatData present_artifacts tool."""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath
from typing import Any

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.conversation_workspace import ConversationWorkspaceRuntime, workspace_artifact_payload
from .artifact_payloads import load_chart_spec_artifact_payload, load_dashboard_spec_artifact_payload
from .common import json_dumps

logger = get_logger(__name__)


def _artifact_display_name(file_path: str) -> str:
    name = PurePosixPath(str(file_path or "")).name
    if not name:
        return ""
    for suffix in (".chart.json", ".dashboard.json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    if "." in name:
        return name.rsplit(".", 1)[0]
    return name


def _artifact_mime_type(file_path: str, artifact_type: str) -> str:
    if artifact_type in {"chart_spec", "dashboard_spec"}:
        return "application/json"
    guessed, _ = mimetypes.guess_type(str(file_path or ""))
    return guessed or "application/octet-stream"


def _presentation_artifact_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("file_path", "artifact_type", "renderer", "artifact_version"):
        value = artifact.get(key)
        if value is not None:
            payload[key] = value
    file_path = str(payload.get("file_path") or "")
    artifact_type = str(payload.get("artifact_type") or "")
    payload["name"] = str(artifact.get("name") or _artifact_display_name(file_path))
    payload["mime_type"] = str(artifact.get("mime_type") or _artifact_mime_type(file_path, artifact_type))
    return payload


def _load_artifact_payload(workspace_root: str, paths: list[str]) -> dict[str, Any]:
    return load_chart_spec_artifact_payload(workspace_root, paths) or load_dashboard_spec_artifact_payload(
        workspace_root, paths
    )


def create_present_artifacts_tool(workspace_runtime: ConversationWorkspaceRuntime):
    @tool("present_artifacts")
    async def present_artifacts(files: list[str]) -> str:
        """
        将 outputs 中已经生成的最终产物显式呈现给用户。

        - files: 需要展示的文件路径列表，只能传 `/mnt/user-data/outputs/...` 或 `outputs/...`。
        - 支持图表、看板、表格、文档、表格文件、PDF、图片等已注册产物类型。
        - workspace 中间文件不会被展示。
        """
        artifacts: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for file_path in files or []:
            path = str(file_path or "").strip()
            if not path:
                continue
            try:
                artifact = await workspace_artifact_payload(
                    workspace_runtime,
                    file_path=path,
                    local_loader=_load_artifact_payload,
                )
            except Exception as exc:
                logger.error("present_artifacts 校验产物失败: path=%s error=%s", path, exc, exc_info=True)
                errors.append({"file_path": path, "error": str(exc)})
                continue
            if not artifact.get("artifact_type"):
                errors.append({"file_path": path, "error": "文件不是可展示的 outputs 产物"})
                continue
            artifacts.append(_presentation_artifact_payload(artifact))
        payload = {
            "artifacts": artifacts,
            "artifact_count": len(artifacts),
            "errors": errors,
        }
        return "[present_artifacts]\n" + json_dumps(payload)

    return present_artifacts
