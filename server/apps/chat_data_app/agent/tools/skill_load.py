"""DataAnalyze skill_load tool."""

from __future__ import annotations

import json
import asyncio
from typing import Any
from pathlib import Path

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.skill_service import resolve_skill_name, skill_service
from ...services.workspace_execution import SandboxExecutionSessionRef, upload_skill_directory_to_sandbox

logger = get_logger(__name__)


def format_skill_load_result(payload: dict[str, Any]) -> str:
    """格式化 skill_load 工具结果，供模型工具和编排预加载复用。"""
    return (
        "[skill_load]\n"
        f"skill_name={payload['name']}\n"
        f"title={payload['title']}\n"
        f"runtime_path={payload['visible_path']}\n"
        f"references={json.dumps(payload['references'], ensure_ascii=False)}\n"
        f"scripts={json.dumps(payload['scripts'], ensure_ascii=False)}\n"
        f"assets={json.dumps(payload.get('assets') or [], ensure_ascii=False)}\n\n"
        f"{payload['skill_md']}"
    )


async def _build_skill_payload_async(skill_name: str, user_id: str = "") -> tuple[dict[str, Any], Path]:
    """异步构建技能加载结果，避免工具执行时阻塞事件循环。"""
    resolved_name = resolve_skill_name(skill_name)
    metadata = await skill_service.resolve_skill_path_async(resolved_name, user_id=user_id)
    if not metadata:
        raise ValueError(f"技能不存在: {skill_name}")
    source_skill_md = Path(str(metadata.get("path") or "")).resolve()
    source_dir = source_skill_md.parent
    if not await asyncio.to_thread(source_skill_md.exists) or not await asyncio.to_thread(source_dir.exists):
        raise ValueError(f"技能文件不存在: {resolved_name}")
    references_root = source_dir / "references"
    scripts_root = source_dir / "scripts"
    assets_root = source_dir / "assets"
    references = await asyncio.to_thread(
        lambda: sorted(str(item.relative_to(references_root).with_suffix("")) for item in references_root.rglob("*.md")) if references_root.exists() else []
    )
    scripts = await asyncio.to_thread(
        lambda: sorted(str(item.relative_to(scripts_root)) for item in scripts_root.rglob("*") if item.is_file()) if scripts_root.exists() else []
    )
    assets = await asyncio.to_thread(
        lambda: sorted(str(item.relative_to(assets_root)) for item in assets_root.rglob("*") if item.is_file()) if assets_root.exists() else []
    )
    skill_md = await asyncio.to_thread(source_skill_md.read_text, encoding="utf-8", errors="replace")
    return {
        "name": resolved_name,
        "title": metadata.get("title") or resolved_name,
        "description": metadata.get("description") or "",
        "runtime_path": f"/mnt/user-data/runtime-skills/{resolved_name}",
        "visible_path": f"/mnt/user-data/runtime-skills/{resolved_name}",
        "skill_md": skill_md,
        "references": references,
        "scripts": scripts,
        "assets": assets,
    }, source_dir


def create_skill_load_tool(workspace_root: str, user_id: str = "", execution_session_ref: SandboxExecutionSessionRef | None = None):
    @tool("skill_load")
    async def skill_load(skill_name: str) -> str:
        """
        加载指定技能，把技能目录复制到当前会话 runtime-skills，并返回完整 SKILL.md 正文与参考文件列表。

        前端选中技能不等于已加载技能。只有本工具成功返回 [skill_load] 后，才算技能已加载，
        才可以继续读取 runtime-skills 下的 references/assets。返回内容已包含 SKILL.md 正文，
        不需要再用 file_read 读取 SKILL.md 本身。
        """
        try:
            _ = workspace_root
            payload, source_dir = await _build_skill_payload_async(skill_name, user_id=user_id)
            payload["visible_path"] = await upload_skill_directory_to_sandbox(session_ref=execution_session_ref, skill_name=payload["name"], source_dir=source_dir)
            payload["runtime_path"] = payload["visible_path"]
            return format_skill_load_result(payload)
        except Exception as exc:
            logger.error("skill_load 执行失败: %s", exc, exc_info=True)
            return f"skill_load 执行失败: {exc}"

    return skill_load
