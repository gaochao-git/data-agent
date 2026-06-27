"""只读 Skill 上下文加载服务。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .skill_service import resolve_skill_name, skill_service

MAX_SKILL_CONTEXT_CHARS = 24_000
MAX_REFERENCE_CHARS = 8_000
MAX_REFERENCE_COUNT = 5
REFERENCE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]*$")
SUPPORTED_QUERY_SKILL_CATEGORIES = {"query_routing", "semantic_query", "business_query", "custom"}
SUPPORTED_QUERY_SKILL_SCOPES = {"query", "all"}


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


async def _read_text_async(path: Path) -> str:
    return await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")


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


async def load_readonly_skill_context(
    skill_name: str,
    reference_names: Optional[List[str]] = None,
    user_id: str = "",
    max_chars: int = MAX_SKILL_CONTEXT_CHARS,
) -> Dict[str, Any]:
    """加载只读 Skill 文本上下文，不挂载目录，不暴露脚本或资产。"""
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
    skill_scope = str(metadata.get("skill_scope") or "analyze").strip().lower()
    if skill_scope not in SUPPORTED_QUERY_SKILL_SCOPES:
        raise ValueError(f"智能问数只读 skill_load 仅支持问数可用技能: {resolved_name}")
    category = str(metadata.get("category") or "").strip()
    if category and category not in SUPPORTED_QUERY_SKILL_CATEGORIES:
        raise ValueError(f"智能问数只读 skill_load 仅支持问数类技能: {resolved_name}")
    output_type = str(metadata.get("output_type") or "text").strip()
    if output_type and output_type != "text":
        raise ValueError(f"智能问数只读 skill_load 仅支持文本技能: {resolved_name}")
    if await _has_files_async(skill_dir / "scripts"):
        raise ValueError(f"智能问数只读 skill_load 不支持包含 scripts 的技能: {resolved_name}")
    if await _has_files_async(skill_dir / "assets"):
        raise ValueError(f"智能问数只读 skill_load 不支持包含 assets 的技能: {resolved_name}")

    available_references = await _list_references_async(skill_dir)
    loaded_references: List[Dict[str, Any]] = []
    missing_references: List[str] = []

    skill_md_raw = await _read_text_async(skill_md_path)
    skill_md, skill_truncated = _truncate_text(_strip_frontmatter(skill_md_raw), max_chars)
    remaining_chars = max(max_chars - len(skill_md), 0)

    normalized_reference_names: List[str] = []
    seen: set[str] = set()
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
        ref_raw = await _read_text_async(ref_path)
        ref_content, truncated = _truncate_text(ref_raw.strip(), min(MAX_REFERENCE_CHARS, remaining_chars))
        remaining_chars = max(remaining_chars - len(ref_content), 0)
        loaded_references.append({
            "name": ref_name,
            "content": ref_content,
            "truncated": truncated,
        })

    return {
        "name": resolved_name,
        "title": metadata.get("title") or resolved_name,
        "description": metadata.get("description") or "",
        "skill_md": skill_md,
        "skill_md_truncated": skill_truncated,
        "available_references": available_references,
        "loaded_references": loaded_references,
        "missing_references": missing_references,
        "capabilities": {
            "read_references": True,
            "use_scripts": False,
            "use_assets": False,
            "sandbox": False,
        },
    }


def format_readonly_skill_context(payload: Dict[str, Any]) -> str:
    """格式化只读 Skill 上下文，供工具返回给模型。"""
    loaded_names = [item.get("name") for item in payload.get("loaded_references") or [] if item.get("name")]
    lines = [
        "[skill_load]",
        f"skill_name={payload.get('name') or ''}",
        f"title={payload.get('title') or ''}",
        "mode=readonly",
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
