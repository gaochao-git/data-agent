"""SKILL.md frontmatter 元数据的纯解析契约。

workspace 快照、插件安装校验、技能服务都需要解析技能元数据，但不需要依赖
整个 ``agent/skills`` 实现包；因此把纯解析放在 contracts 层，避免
``workspace -> skills`` / ``plugins -> skills`` 的包级环。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TypedDict

from utils.logger import get_logger

try:
    import yaml
except Exception:  # pragma: no cover - 可选依赖缺失时走简单解析
    yaml = None

logger = get_logger(__name__)


class SkillMetadata(TypedDict):
    """启动时加载的轻量技能元数据。"""

    name: str
    title: str
    description: str
    domain: str
    category: str
    output_type: str
    input_schema: dict[str, Any]
    path: str


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """解析 SKILL.md frontmatter，优先 YAML，失败时走简单 key/value。"""
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    raw_frontmatter = match.group(1).strip()
    if yaml is not None:
        try:
            parsed = yaml.safe_load(raw_frontmatter)
            if isinstance(parsed, dict):
                return parsed
        except Exception as err:
            logger.warning("[skills] YAML frontmatter 解析失败，回退简单解析: %s", err)
    frontmatter: dict[str, Any] = {}
    for line in raw_frontmatter.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip().strip('"').strip("'")
    return frontmatter


def parse_metadata_text(text: str, *, path: str = "") -> SkillMetadata | None:
    """解析 SKILL.md 文本中的 frontmatter 元数据。"""
    frontmatter = _parse_frontmatter(text)
    name = frontmatter.get("name", "")
    if not name:
        return None
    metadata = frontmatter.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    input_schema: dict[str, Any]
    if isinstance(frontmatter.get("input_schema"), dict):
        input_schema = frontmatter["input_schema"]
    else:
        fallback_schema = metadata.get("input_schema")
        input_schema = fallback_schema if isinstance(fallback_schema, dict) else {}
    return SkillMetadata(
        name=name,
        title=frontmatter.get("title") or metadata.get("title") or name,
        description=frontmatter.get("description", ""),
        domain=frontmatter.get("domain") or metadata.get("domain") or "",
        category=frontmatter.get("category") or metadata.get("category") or "",
        output_type=frontmatter.get("output_type") or metadata.get("output_type") or "text",
        input_schema=input_schema,
        path=path,
    )


def parse_metadata(skill_path: Path) -> SkillMetadata | None:
    """解析单个技能的 frontmatter 元数据。"""
    try:
        text = skill_path.read_text(encoding="utf-8")
    except Exception:
        return None
    return parse_metadata_text(text, path=str(skill_path))


__all__ = ["SkillMetadata", "parse_metadata", "parse_metadata_text"]
