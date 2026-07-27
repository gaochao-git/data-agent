"""ChatData skill_load 工具。"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from utils.logger import get_logger
from ...services.skill_service import resolve_skill_name, skill_service
from ...services.conversation_workspace import ConversationWorkspaceRuntime

logger = get_logger(__name__)

MAX_SKILL_CONTEXT_CHARS = 24_000
MAX_REFERENCE_CHARS = 8_000
MAX_REFERENCE_COUNT = 5
REFERENCE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]*$")


# ---------------------------------------------------------------------------
# 参数 schema
# ---------------------------------------------------------------------------

class SkillLoadInput(BaseModel):
    """加载 Skill 的输入参数。"""

    skill_name: str = Field(..., min_length=1, description="要加载的 skill 名称，只能使用可用技能清单中的 name")
    reference_names: Optional[List[str]] = Field(
        default=None,
        description="需要同时加载的 references/*.md 名称，不带 .md；不确定具体 reference 时先不传，只加载 SKILL.md 读取领域目录",
    )

    @field_validator("reference_names", mode="before")
    @classmethod
    def normalize_reference_names(cls, value):
        """兼容模型把 reference_names 作为 JSON 字符串数组传入。"""
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return [text]
            if isinstance(parsed, str):
                return [parsed]
            return parsed
        if isinstance(value, tuple):
            return list(value)
        return value


# ---------------------------------------------------------------------------
# 运行时只读技能映射
# ---------------------------------------------------------------------------

def format_skill_load_result(payload: dict[str, Any]) -> str:
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
    return (
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


async def _build_skill_payload_async(skill_name: str, user_id: str = "") -> tuple[dict[str, Any], Path, str]:
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
    category = "custom" if str(metadata.get("source") or "") == "user" else "public"
    return {
        "name": resolved_name,
        "title": metadata.get("title") or resolved_name,
        "description": metadata.get("description") or "",
        "runtime_path": f"/mnt/skills/{category}/{resolved_name}",
        "visible_path": f"/mnt/skills/{category}/{resolved_name}",
        "skill_md": skill_md,
        "references": references,
        "scripts": scripts,
        "assets": assets,
    }, source_dir, category


# ---------------------------------------------------------------------------
# 内联模式（无工作区时，仅返回 SKILL.md + references 文本）
# ---------------------------------------------------------------------------

def _strip_frontmatter(text: str) -> str:
    match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", text or "", re.DOTALL)
    return match.group(1).strip() if match else str(text or "").strip()


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    normalized = str(text or "")
    if len(normalized) <= max_chars:
        return normalized, False
    return normalized[:max_chars].rstrip() + "\n\n[内容已截断]", True


def _normalize_reference_name(name: Any) -> str:
    text = str(name or "").strip().replace("\\", "/")
    if text.endswith(".md"):
        text = text[:-3]
    if not text or ".." in text.split("/") or not REFERENCE_NAME_PATTERN.match(text):
        raise ValueError(f"reference 名称无效: {name}")
    return text


def _reference_path(skill_dir: Path, reference_name: str) -> Path:
    references_root = (skill_dir / "references").resolve()
    candidate = (references_root / f"{reference_name}.md").resolve()
    if not (candidate == references_root or references_root in candidate.parents):
        raise ValueError(f"reference 路径越界: {reference_name}")
    return candidate


async def _list_references_async(skill_dir: Path) -> List[str]:
    references_root = skill_dir / "references"

    def collect() -> List[str]:
        if not references_root.exists() or not references_root.is_dir():
            return []
        return sorted(str(item.relative_to(references_root).with_suffix("")) for item in references_root.rglob("*.md"))

    return await asyncio.to_thread(collect)


async def _has_files_async(root: Path) -> bool:
    def check() -> bool:
        return root.exists() and root.is_dir() and any(item.is_file() for item in root.rglob("*"))

    return await asyncio.to_thread(check)


async def load_inline_skill_context(
    skill_name: str,
    reference_names: Optional[List[str]] = None,
    user_id: str = "",
    max_chars: int = MAX_SKILL_CONTEXT_CHARS,
) -> dict[str, Any]:
    """加载 Skill 文本上下文（内联模式）：不挂载目录、不暴露脚本/资产。"""
    requested_name = str(skill_name or "").strip()
    if not requested_name:
        raise ValueError("skill_name 不能为空")

    resolved_name = resolve_skill_name(requested_name)
    metadata = await skill_service.resolve_skill_path_async(resolved_name, user_id=user_id)
    if not metadata:
        raise ValueError(f"技能不存在或无权限访问: {requested_name}")

    skill_md_path = Path(str(metadata.get("path") or "")).resolve()
    skill_dir = skill_md_path.parent.resolve()
    if not await asyncio.to_thread(skill_md_path.exists):
        raise ValueError(f"技能文件不存在: {resolved_name}")

    available_references = await _list_references_async(skill_dir)
    loaded_references: List[dict[str, Any]] = []
    missing_references: List[str] = []

    skill_md_raw = await asyncio.to_thread(skill_md_path.read_text, encoding="utf-8", errors="replace")
    skill_md, _ = _truncate_text(_strip_frontmatter(skill_md_raw), max_chars)
    remaining_chars = max(max_chars - len(skill_md), 0)

    seen: set[str] = set()
    normalized_reference_names: List[str] = []
    for raw_name in reference_names or []:
        ref_name = _normalize_reference_name(raw_name)
        if ref_name in seen:
            continue
        seen.add(ref_name)
        normalized_reference_names.append(ref_name)
        if len(normalized_reference_names) >= MAX_REFERENCE_COUNT:
            break

    for ref_name in normalized_reference_names:
        ref_path = _reference_path(skill_dir, ref_name)
        if not await asyncio.to_thread(ref_path.exists):
            missing_references.append(ref_name)
            continue
        if remaining_chars <= 0:
            break
        ref_raw = await asyncio.to_thread(ref_path.read_text, encoding="utf-8", errors="replace")
        ref_content, _ = _truncate_text(ref_raw.strip(), min(MAX_REFERENCE_CHARS, remaining_chars))
        remaining_chars = max(remaining_chars - len(ref_content), 0)
        loaded_references.append({"name": ref_name, "content": ref_content})

    return {
        "name": resolved_name,
        "title": metadata.get("title") or resolved_name,
        "description": metadata.get("description") or "",
        "skill_md": skill_md,
        "available_references": available_references,
        "loaded_references": loaded_references,
        "missing_references": missing_references,
    }


def format_inline_skill_context(payload: dict[str, Any]) -> str:
    """格式化内联 Skill 上下文，供工具返回给模型。"""
    loaded_names = [item.get("name") for item in payload.get("loaded_references") or [] if item.get("name")]
    lines = [
        "[skill_load]",
        f"skill_name={payload.get('name') or ''}",
        f"title={payload.get('title') or ''}",
        f"available_references={', '.join(payload.get('available_references') or [])}",
        f"loaded_references={', '.join(loaded_names)}",
    ]
    missing = payload.get("missing_references") or []
    if missing:
        lines.append(f"missing_references={', '.join(missing)}")
    lines.extend(["", "# SKILL.md", str(payload.get("skill_md") or "").strip()])
    for ref in payload.get("loaded_references") or []:
        lines.extend(["", f"# references/{ref.get('name')}.md", str(ref.get("content") or "").strip()])
    return "\n".join(lines).strip()


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
    async def skill_load(skill_name: str, reference_names: Optional[List[str]] = None) -> str:
        """
        加载指定技能说明，直接返回完整 SKILL.md 正文、references/scripts/assets 清单与只读 `/mnt/skills/...` 路径。

        技能目录保留在既有技能仓库，不复制到会话 workspace。本工具是「获取 SKILL.md 内联文本」的便捷入口；
        读取技能的 references/scripts/assets 子文件时，也可直接用返回的 runtime_path 拼接后交给 file_read/list_dir，
        无需先调用本工具（任何当前用户有权访问的技能路径均可直接读取，按权限校验，对齐 deer-flow 的
        discover + read_file 模型）。

        读取参考文件时，严格使用本工具返回的 runtime_path 拼接子路径，不要自行猜测或省略路径前缀。

        前端选中技能不等于已自动加载。仅当用户本轮显式指定技能时调用本工具，不要因领域词相近或系统技能存在而自动调用。
        返回内容已包含 SKILL.md 正文，通常不需要再用 file_read 读取 SKILL.md 本身。
        """
        try:
            payload, source_dir, category = await _build_skill_payload_async(
                skill_name,
                user_id=user_id,
            )
            payload["visible_path"] = await workspace_runtime.register_runtime_skill(
                name=payload["name"],
                category=category,
                source_dir=source_dir,
            )
            payload["runtime_path"] = payload["visible_path"]
            return format_skill_load_result(payload)
        except Exception as exc:
            logger.error("skill_load 执行失败: %s", exc, exc_info=True)
            return f"skill_load 执行失败: {exc}"

    return skill_load


__all__ = [
    "SkillLoadInput",
    "create_skill_load_tool",
    "load_inline_skill_context",
    "format_inline_skill_context",
]
