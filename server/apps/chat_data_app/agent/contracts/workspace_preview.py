"""技能 Office 预览所需的 workspace 渲染端口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class WorkspacePreviewPort(Protocol):
    """把技能版本文件渲染为 HTML 预览。"""

    async def render_skill_office_preview(
        self,
        target: Path,
        user_id: str,
        *,
        max_bytes: int,
    ) -> dict[str, Any]: ...


_source: WorkspacePreviewPort | None = None


def register_workspace_preview_source(source: WorkspacePreviewPort) -> None:
    global _source
    _source = source


def get_workspace_preview_source() -> WorkspacePreviewPort | None:
    return _source


__all__ = ["WorkspacePreviewPort", "get_workspace_preview_source", "register_workspace_preview_source"]
