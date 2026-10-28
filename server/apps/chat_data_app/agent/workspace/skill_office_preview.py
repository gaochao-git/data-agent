"""技能版本目录 Office 文件预览：复用主对话工作区渲染引擎。"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import runtime as workspace_runtime
from . import workspace_special_services


async def render_skill_office_preview(target: Path, user_id: str, *, max_bytes: int) -> dict[str, Any]:
    """把技能版本目录中的 Office 文件暂存到 workspace 结构后渲染 HTML。

    引擎路径策略要求虚拟路径位于 workspace/outputs/uploads；技能版本目录不在该
    空间内，因此硬链接/复制到临时 workspace 结构再渲染。
    """
    if target.stat().st_size > max_bytes:
        raise ValueError(f"文件过大，暂不支持预览超过 {_format_size_limit(max_bytes)} 的 Office 文件")
    with tempfile.TemporaryDirectory(prefix="skill-office-preview-") as stage_root:
        stage_dir = Path(stage_root) / "workspace"
        stage_dir.mkdir()
        staged = stage_dir / target.name
        try:
            os.link(target, staged)
        except OSError:
            await asyncio.to_thread(shutil.copyfile, target, staged)
        runtime = workspace_runtime.ConversationWorkspaceRuntime(
            workspace_root=stage_root,
            user_id=user_id,
            backend="local",
        )
        preview = await workspace_special_services.workspace_office_preview_service.render_html(
            runtime,
            file_path=f"/mnt/user-data/workspace/{target.name}",
            max_chars=workspace_runtime.OFFICE_PREVIEW_MAX_CHARS,
        )
    if bool(preview.get("truncated")):
        return {"available": False, "reason": "preview_too_large"}
    html = str(preview.get("html") or "")
    if not html:
        raise RuntimeError("OfficeCLI 未返回 HTML 预览内容")
    return {"available": True, "renderer": "officecli-html", "html": html, "truncated": False}


def _format_size_limit(max_bytes: int) -> str:
    if max_bytes >= 1024 * 1024:
        return f"{max_bytes / (1024 * 1024):.0f}MB"
    return f"{max_bytes / 1024:.0f}KB"


class WorkspacePreviewRenderer:
    async def render_skill_office_preview(
        self,
        target: Path,
        user_id: str,
        *,
        max_bytes: int,
    ) -> dict[str, Any]:
        return await render_skill_office_preview(target, user_id, max_bytes=max_bytes)


__all__ = ["WorkspacePreviewRenderer", "render_skill_office_preview"]
