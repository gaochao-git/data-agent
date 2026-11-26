"""
Skills Loader - 符合 agentskills.io 规范

API：
- parse_metadata(skill_path): 解析单个技能元数据
- discover_skills(directories): 扫描目录发现技能
- load_metadata(): 启动时加载所有元数据
- format_skills_prompt(): 格式化技能元数据为注入 system prompt 的技能描述
- activate_skill(name): 激活技能，按需加载完整内容
"""

import re
from pathlib import Path
from typing import Any, TypedDict
from xml.sax.saxutils import escape as xml_escape

from utils.logger import get_logger
from utils.skill_paths import SYSTEM_SKILL_CATEGORY, physical_skill_category_root

# 兼容旧导入/包级重导出：skills 包和测试仍可从 loader 取这些符号。
from ..contracts.skill_metadata import (  # noqa: F401
    SkillMetadata,
    parse_metadata,
    parse_metadata_text,
)

try:
    import yaml
except Exception:
    yaml = None

logger = get_logger(__name__)


# ============ 类型定义 ============


class Skill(TypedDict):
    """按需加载的完整技能"""

    name: str
    title: str
    description: str
    domain: str
    category: str
    output_type: str
    input_schema: dict[str, Any]
    content: str


# ============ 内部状态 ============

_metadata_cache: dict[str, SkillMetadata] | None = None


def clear_metadata_cache() -> None:
    """清理技能元数据缓存。"""
    global _metadata_cache
    _metadata_cache = None


def system_skills_root() -> Path:
    """系统技能源目录：代码包 ``agent/skills``，随发版更新、只读。

    路径口径统一由 ``utils/skill_paths.py`` 决定；不要再引入 ``CHAT_DATA_HOME``
    下的数据根副本（历史实现会与代码包漂移）。
    """
    return physical_skill_category_root(SYSTEM_SKILL_CATEGORY)


def discover_skills(directories: list[Path]) -> list[Path]:
    """扫描目录发现所有技能文件（只扫描 SKILL.md）"""
    skill_files = []
    for directory in directories:
        main_skill = directory / "SKILL.md"
        if main_skill.exists():
            skill_files.append(main_skill)
    return skill_files


def load_metadata() -> dict[str, SkillMetadata]:
    """启动时加载所有技能的元数据（轻量）"""
    global _metadata_cache
    if _metadata_cache is not None:
        logger.debug(f"[skills] 使用缓存，已加载 {len(_metadata_cache)} 个技能元数据")
        return _metadata_cache

    skills_root = system_skills_root()
    skill_dirs = (
        [d for d in skills_root.iterdir() if d.is_dir() and not d.name.startswith("_")] if skills_root.is_dir() else []
    )
    skill_files = discover_skills(skill_dirs)

    _metadata_cache = {}
    for skill_file in skill_files:
        meta = parse_metadata(skill_file)
        if meta:
            _metadata_cache[meta["name"]] = meta
            logger.debug(f"[skills] 元数据: {skill_file.name} -> {meta['name']}")

    logger.info(f"[skills] 初始化完成，共 {len(_metadata_cache)} 个技能: {list(_metadata_cache.keys())}")
    return _metadata_cache


def format_skills_prompt(skills: list[dict[str, Any]]) -> str:
    """格式化技能元数据为 system prompt XML。"""
    lines = ["<available_skills>", "  <loader>load_skill</loader>"]
    for meta in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{xml_escape(str(meta.get('name') or ''))}</name>")
        lines.append(f"    <skill_file>/mnt/skills/{xml_escape(str(meta.get('name') or ''))}/SKILL.md</skill_file>")
        lines.append(f"    <title>{xml_escape(str(meta.get('title') or ''))}</title>")
        lines.append(f"    <description>{xml_escape(str(meta['description'] or ''))}</description>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    lines.append("技能正文必须用 load_skill(name) 加载；未加载前不要按技能内容执行。")
    summary = "\n".join(lines)
    logger.debug(f"[skills] summary 长度: {len(summary)} 字符")
    return summary


def activate_skill(name: str) -> Skill | None:
    """激活技能，按需加载完整内容"""
    logger.debug(f"[skills] activate_skill('{name}') -> 加载详情")
    metadata = load_metadata()
    meta = metadata.get(name)
    if not meta:
        logger.warning(f"[skills] 未找到技能: {name}")
        return None

    # 按需读取完整内容
    try:
        text = Path(meta["path"]).read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"[skills] 读取文件失败: {meta['path']}, {e}")
        return None

    # 去掉 frontmatter，只返回正文
    match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", text, re.DOTALL)
    content = match.group(1).strip() if match else text

    logger.debug(f"[skills] 返回 '{name}' 内容，{len(content)} 字符")
    return Skill(
        name=meta["name"],
        title=meta.get("title") or meta["name"],
        description=meta["description"],
        domain=meta.get("domain") or "",
        category=meta.get("category") or "",
        output_type=meta.get("output_type") or "text",
        input_schema=meta.get("input_schema") or {},
        content=content,
    )
