"""ChatData present_artifacts tool."""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath
from typing import Any

from langchain_core.tools import BaseTool, tool

from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime
from utils.logger import get_logger

from .common import tool_result_payload

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
    if artifact_type in {"skill", "markdown"}:
        return "text/markdown"
    if artifact_type == "yaml":
        return "application/yaml"
    guessed, _ = mimetypes.guess_type(str(file_path or ""))
    return guessed or "application/octet-stream"


def _presentation_artifact_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in (
        "file_path",
        "artifact_type",
        "renderer",
        "artifact_version",
        "skill_title",
        "description",
    ):
        value = artifact.get(key)
        if value is not None:
            payload[key] = value
    file_path = str(payload.get("file_path") or "")
    artifact_type = str(payload.get("artifact_type") or "")
    payload["name"] = str(artifact.get("name") or _artifact_display_name(file_path))
    payload["mime_type"] = str(artifact.get("mime_type") or _artifact_mime_type(file_path, artifact_type))
    return payload


def create_present_artifacts_tool(workspace_runtime: ConversationWorkspaceRuntime) -> BaseTool:
    @tool("present_artifacts", parse_docstring=True)
    async def present_artifacts(files: list[str], intent: str = "") -> str:
        """
        将 outputs 中已经生成的最终产物显式呈现给用户。

        - 支持图表、看板、技能、Markdown、自包含 HTML 页面、表格、文档、演示文稿、PDF、图片等已注册产物类型。
        - 技能只能传 /mnt/user-data/outputs/<skill-name>/SKILL.md，frontmatter 的 name 必须与目录名一致。
        - workspace 中间文件不会被展示。

        Args:
            files: 需要展示的文件路径列表，只能传 `/mnt/user-data/outputs/...`。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        artifacts: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for file_path in files or []:
            path = str(file_path or "").strip()
            if not path:
                continue
            try:
                artifact = await workspace_runtime.artifact_payload(file_path=path)
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
        return tool_result_payload("present_artifacts", payload)

    return present_artifacts
