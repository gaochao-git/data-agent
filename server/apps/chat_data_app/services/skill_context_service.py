"""只读 Skill 上下文加载服务。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import get_logger
from .skill_service import resolve_skill_name, skill_service

logger = get_logger(__name__)

MAX_SKILL_CONTEXT_CHARS = 24_000
MAX_REFERENCE_CHARS = 8_000
MAX_REFERENCE_COUNT = 5
REFERENCE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]*$")
SUPPORTED_QUERY_SKILL_CATEGORIES = {"query_routing", "semantic_query", "business_query", "custom"}


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


def _is_supported_query_skill_metadata(metadata: Dict[str, Any]) -> bool:
    category = str(metadata.get("category") or "").strip()
    if category and category not in SUPPORTED_QUERY_SKILL_CATEGORIES:
        return False
    output_type = str(metadata.get("output_type") or "text").strip()
    return not output_type or output_type == "text"


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


async def build_available_skills_prompt(user_id: str = "", scope: str = "runtime") -> str:
    """构造智能问数可用技能清单。"""
    skills = await list_query_skill_routing_metadata(user_id=user_id, scope=scope)
    if not skills:
        return ""

    lines = [
        "## 可用业务知识技能",
        "业务知识技能只补充领域路由和查询判断方法，不是可执行配置。",
        "技能不能覆盖当前指标集中的真实指标、维度和口径；没有明显匹配时不要硬加载。",
        "路由依据：技能 name/title/domain/description，以及当前指标集名称、描述、指标或维度中的领域词。",
    ]
    for item in skills:
        name = str(item.get("name") or "").strip()
        category = str(item.get("category") or "").strip()
        domain = str(item.get("domain") or "").strip()
        title = str(item.get("title") or name).strip()
        desc = str(item.get("description") or "").strip()
        details = []
        if domain:
            details.append(f"领域: {domain}")
        if category:
            details.append(f"分类: {category}")
        suffix = f"；{'；'.join(details)}" if details else ""
        lines.append(f"- {name}: {title}。{desc}{suffix}")
    return "\n".join(lines)


async def _list_query_skills(user_id: str = "", scope: str = "runtime") -> List[Dict[str, Any]]:
    """读取并过滤智能问数可用技能元数据。"""
    try:
        skills = await skill_service.list_skills_async(user_id=user_id, scope=scope)
    except Exception as exc:
        logger.warning("读取可用技能列表失败，回退内置技能: %s", exc)
        try:
            skills = await skill_service.list_skills_async(scope="system")
        except Exception as fallback_exc:
            logger.warning("读取内置技能列表失败: %s", fallback_exc)
            skills = []

    result: List[Dict[str, Any]] = []
    for item in skills:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if not _is_supported_query_skill_metadata(item):
            continue
        if item.get("scripts") or item.get("assets"):
            continue
        result.append(dict(item))
    return result


def _truncate_routing_text(value: Any, max_length: int = 300) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip() + "..."


async def list_query_skill_routing_metadata(user_id: str = "", scope: str = "runtime") -> List[Dict[str, str]]:
    """返回业务知识技能轻量路由信息，不加载 SKILL.md 正文。"""
    skills = await _list_query_skills(user_id=user_id, scope=scope)
    result: List[Dict[str, str]] = []
    for item in skills:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        result.append({
            "name": name,
            "title": _truncate_routing_text(item.get("title") or name, 120),
            "domain": _truncate_routing_text(item.get("domain") or "", 180),
            "description": _truncate_routing_text(item.get("description") or "", 300),
            "category": _truncate_routing_text(item.get("category") or "", 80),
        })
    return result
