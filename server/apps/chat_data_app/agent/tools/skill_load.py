"""ChatData skill_load 工具。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from utils.logger import get_logger
from utils.skill_paths import runtime_skill_visible_root
from ...services.conversation_workspace import ConversationWorkspaceRuntime
from ...services.runtime_config import FILE_READ_MAX_CHARS
from ...services.skill_service import resolve_skill_name

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 参数 schema
# ---------------------------------------------------------------------------

class SkillLoadInput(BaseModel):
    """加载 Skill 的输入参数。"""

    skill_name: str = Field(..., min_length=1, description="要加载的 skill 名称，只能使用可用技能清单中的 name")


# ---------------------------------------------------------------------------
# 运行时只读技能映射
# ---------------------------------------------------------------------------

def format_skill_load_result(payload: dict[str, Any], max_chars: int = FILE_READ_MAX_CHARS) -> str:
    """格式化 skill_load 工具结果，供模型工具和编排预加载复用。

    references/scripts/assets 一律输出可直接 file_read 的完整虚拟路径，避免模型自行
    拼接前缀时写出 /mnt/user-data/runtime-skills/ 等不存在的路径。
    """
    visible_root = payload["visible_path"]
    references_full = [
        f"{visible_root}/references/{ref}.md" for ref in payload.get("references") or []
    ]
    scripts_full = [
        f"{visible_root}/scripts/{script}" for script in payload.get("scripts") or []
    ]
    assets_full = [
        f"{visible_root}/assets/{asset}" for asset in payload.get("assets") or []
    ]
    result = (
        "[skill_load]\n"
        f"skill_name={payload['name']}\n"
        f"title={payload['title']}\n"
        f"runtime_path={visible_root}\n"
        "⚠️ 读取技能参考文件必须使用上面的 runtime_path 拼接，禁止自造路径前缀；"
        "错误示例 /mnt/user-data/runtime-skills/、缺少 /mnt/skills/ 前缀的写法都会让 file_read 直接报错。\n"
        f"references={json.dumps(references_full, ensure_ascii=False)}\n"
        f"scripts={json.dumps(scripts_full, ensure_ascii=False)}\n"
        f"assets={json.dumps(assets_full, ensure_ascii=False)}\n\n"
        f"{payload['skill_md']}"
    )
    if len(result) <= max_chars:
        return result
    marker = (
        f"\n\n[skill_load 内容已截断。需要继续读取时，使用 "
        f"file_read(file_path={visible_root}/SKILL.md, offset=..., limit=...) 按行读取。]"
    )
    return result[:max(0, max_chars - len(marker))].rstrip() + marker


async def _build_skill_payload_async(
    skill_name: str,
    user_id: str = "",
    workspace_runtime: ConversationWorkspaceRuntime | None = None,
) -> dict[str, Any]:
    """异步构建技能加载结果，避免工具执行时阻塞事件循环。

    只从本会话运行域快照（``workspace_runtime.runtime_skills_dir``）读取 SKILL.md 与引用清单，
    与管理域实时目录完全解耦——模型随后在沙箱里 ``file_read(/mnt/skills/<name>/...)`` 走的是
    同一份冻结副本，从根上杜绝版本漂移。草稿/禁用技能本就不进入快照，加载不到时统一报
    “技能不存在或无权限访问”，因此不再回退管理域做二次判断。
    """
    if workspace_runtime is None:
        raise ValueError("skill_load 需要会话运行域快照，当前不可用")
    resolved_name = resolve_skill_name(skill_name)
    # 惰性确保本会话快照已构建（与 file_read / 沙箱启动同源的兜底）。
    await workspace_runtime._ensure_snapshot_async()
    snapshot_dir = (workspace_runtime.runtime_skills_dir / resolved_name).resolve()
    if not await asyncio.to_thread(lambda: snapshot_dir.is_dir() and not snapshot_dir.is_symlink()):
        raise ValueError(f"技能不存在或无权限访问: {resolved_name}")
    source_skill_md = (snapshot_dir / "SKILL.md").resolve()
    if not await asyncio.to_thread(source_skill_md.exists):
        raise ValueError(f"技能文件不存在: {resolved_name}")
    references_root = snapshot_dir / "references"
    scripts_root = snapshot_dir / "scripts"
    assets_root = snapshot_dir / "assets"
    references = await asyncio.to_thread(
        lambda: sorted(str(item.relative_to(references_root).with_suffix("")) for item in references_root.rglob("*.md")) if references_root.exists() else []
    )
    scripts = await asyncio.to_thread(
        lambda: sorted(str(item.relative_to(scripts_root)) for item in scripts_root.rglob("*") if item.is_file()) if scripts_root.exists() else []
    )
    assets = await asyncio.to_thread(
        lambda: sorted(str(item.relative_to(assets_root)) for item in assets_root.rglob("*") if item.is_file()) if assets_root.exists() else []
    )
    skill_md = await asyncio.to_thread(
        _read_text_prefix,
        source_skill_md,
        FILE_READ_MAX_CHARS + 1,
    )
    title, description = _parse_skill_frontmatter(skill_md)
    return {
        "name": resolved_name,
        "title": title or resolved_name,
        "description": description or "",
        "runtime_path": runtime_skill_visible_root(resolved_name),
        "visible_path": runtime_skill_visible_root(resolved_name),
        "skill_md": skill_md,
        "references": references,
        "scripts": scripts,
        "assets": assets,
    }


def _parse_skill_frontmatter(text: str) -> tuple[str, str]:
    """从 SKILL.md 的 YAML frontmatter 中提取 title / description。

    优先用 ``title``，缺省时回退 ``name``；解析失败或缺失时返回空串。
    仅在快照目录内读取，不再回退管理域。
    """
    if not text.startswith("---\n"):
        return "", ""
    closing_index = text.find("\n---\n", len("---\n"))
    if closing_index < 0:
        return "", ""
    fm_title = fm_name = description = ""
    for line in text[len("---\n"):closing_index].splitlines():
        stripped = line.strip()
        if stripped.startswith("title:"):
            fm_title = stripped.split(":", 1)[1].strip().strip('"').strip("'")
        elif stripped.startswith("name:"):
            fm_name = stripped.split(":", 1)[1].strip().strip('"').strip("'")
        elif stripped.startswith("description:"):
            description = stripped.split(":", 1)[1].strip().strip('"').strip("'")
    return (fm_title or fm_name), description


def _read_text_prefix(path: Path, max_chars: int) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as file_obj:
        return file_obj.read(max_chars)


# ---------------------------------------------------------------------------
# 统一工具工厂
# ---------------------------------------------------------------------------

def create_skill_load_tool(workspace_runtime: ConversationWorkspaceRuntime, user_id: str = ""):
    """创建统一 skill_load 工具。

    Args:
        workspace_runtime: 当前会话的本地或 OpenSandbox 工作区。
        user_id: 用于按权限解析技能。
    """

    @tool("skill_load", args_schema=SkillLoadInput)
    async def skill_load(skill_name: str) -> str:
        """
        加载指定技能说明，返回受 CHAT_DATA_FILE_READ_MAX_CHARS 限制的 SKILL.md 正文、references/scripts/assets 清单与只读 `/mnt/skills/...` 路径。

        技能目录保留在既有技能仓库，不复制到会话 workspace。本工具是「获取 SKILL.md 内联文本」的便捷入口；
        读取技能的 references/scripts/assets 子文件时，也可直接用返回的 runtime_path 拼接后交给 file_read/list_dir，
        无需先调用本工具（任何当前用户有权访问的技能路径均可直接读取，按权限校验，对齐 deer-flow 的
        discover + read_file 模型）。

        读取参考文件时，严格使用本工具返回的 runtime_path 拼接子路径，不要自行猜测或省略路径前缀。

        前端选中技能不等于已自动加载。仅当用户本轮显式指定技能时调用本工具，不要因领域词相近或系统技能存在而自动调用。
        返回内容被截断时，使用 file_read 从 `/mnt/skills/<技能名>/SKILL.md` 按行读取；单行超过 500 个字符时会直接截断。
        """
        try:
            payload = await _build_skill_payload_async(
                skill_name,
                user_id=user_id,
                workspace_runtime=workspace_runtime,
            )
            payload["visible_path"] = runtime_skill_visible_root(payload["name"])
            payload["runtime_path"] = payload["visible_path"]
            return format_skill_load_result(payload, max_chars=FILE_READ_MAX_CHARS)
        except Exception as exc:
            logger.error("skill_load 执行失败: %s", exc, exc_info=True)
            return f"skill_load 执行失败: {exc}"

    return skill_load


__all__ = [
    "SkillLoadInput",
    "create_skill_load_tool",
]
