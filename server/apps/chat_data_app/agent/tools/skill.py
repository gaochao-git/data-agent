"""模型侧技能加载工具：把会话技能快照中的 SKILL.md 正文读入上下文。"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from langchain_core.tools import tool

from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime
from utils.skill_paths import resolve_skill_snapshot_path

SKILL_TOOL_NAME = "load_skill"
_SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _strip_frontmatter(text: str) -> str:
    match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", text, re.DOTALL)
    return match.group(1).strip() if match else text


def _skill_content(name: str, body: str) -> str:
    return (
        f'<skill_content name="{name}">\n'
        f"# Skill: {name}\n\n"
        f"{body}\n\n"
        f"Base directory for this skill: /mnt/skills/{name}\n"
        "Relative paths in this skill are relative to this base directory.\n"
        "</skill_content>"
    )


def create_skill_tool(workspace_runtime: ConversationWorkspaceRuntime) -> Any:
    """构建 ``load_skill`` 工具；只读会话技能快照，不访问管理域。"""

    @tool(SKILL_TOOL_NAME, parse_docstring=True)
    async def load_skill(name: str, intent: str = "") -> str:
        """加载一个已列出的技能全文到当前上下文。

        在调用与该技能相关的工具前，先加载对应技能；加载后按技能正文执行。
        同一技能已加载过时无需重复调用。只用它读 SKILL.md 入口，references 里的
        细分资料仍按技能正文指示用 file_read 读取。

        Args:
            name: 技能名，必须来自 <available_skills>。
            intent: 一句中文说明本次调用目的（不超过50字），面向用户展示。
        """
        skill_name = str(name or "").strip()
        if not _SKILL_NAME_PATTERN.fullmatch(skill_name):
            raise ValueError(f"技能名非法: {name!r}")
        path, _visible = resolve_skill_snapshot_path(
            workspace_runtime.runtime_skills_dir,
            f"/mnt/skills/{skill_name}/SKILL.md",
        )
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except OSError as exc:
            available = "、".join(workspace_runtime.available_skill_names or [])
            raise ValueError(f"技能不存在或不可读: {skill_name}；可用技能: {available or '无'}") from exc
        return _skill_content(skill_name, _strip_frontmatter(text))

    return load_skill
