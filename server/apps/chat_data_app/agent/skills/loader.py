"""
Skills Loader - 符合 agentskills.io 规范

API：
- parse_metadata(skill_path): 解析单个技能元数据
- discover_skills(directories): 扫描目录发现技能
- load_metadata(): 启动时加载所有元数据
- build_builtin_skills_prompt(): 构建内置技能 prompt
- format_skills_prompt(): 格式化技能元数据为注入 system prompt 的技能描述
- activate_skill(name): 激活技能，按需加载完整内容
"""

import re
from pathlib import Path
from typing import Any, TypedDict
from xml.sax.saxutils import escape as xml_escape

from utils.logger import get_logger

try:
    import yaml
except Exception:
    yaml = None

logger = get_logger(__name__)


# ============ 类型定义 ============

class SkillMetadata(TypedDict):
    """启动时加载的轻量元数据"""
    name: str
    title: str
    description: str
    domain: str
    category: str
    output_type: str
    input_schema: dict[str, Any]
    path: str  # SKILL.md 文件路径


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


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """解析 SKILL.md frontmatter，优先兼容 YAML，失败时走简单 key/value。"""
    match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
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
    for line in raw_frontmatter.split('\n'):
        if ':' in line:
            k, v = line.split(':', 1)
            frontmatter[k.strip()] = v.strip().strip('"').strip("'")
    return frontmatter


def clear_metadata_cache() -> None:
    """清理技能元数据缓存。"""
    global _metadata_cache
    _metadata_cache = None


# ============ API ============

def parse_metadata(skill_path: Path) -> SkillMetadata | None:
    """解析单个技能的 frontmatter 元数据"""
    try:
        text = skill_path.read_text(encoding="utf-8")
    except Exception:
        return None

    frontmatter = _parse_frontmatter(text)

    name = frontmatter.get("name", "")
    if not name:
        return None
    metadata = frontmatter.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    input_schema = frontmatter.get("input_schema")
    if not isinstance(input_schema, dict):
        input_schema = metadata.get("input_schema") if isinstance(metadata.get("input_schema"), dict) else {}

    return SkillMetadata(
        name=name,
        title=frontmatter.get("title") or metadata.get("title") or name,
        description=frontmatter.get("description", ""),
        domain=frontmatter.get("domain") or metadata.get("domain") or "",
        category=frontmatter.get("category") or metadata.get("category") or "",
        output_type=frontmatter.get("output_type") or metadata.get("output_type") or "text",
        input_schema=input_schema,
        path=str(skill_path)
    )


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

    # 扫描 skills 目录下的所有子目录
    skills_root = Path(__file__).parent
    skill_dirs = [d for d in skills_root.iterdir() if d.is_dir() and not d.name.startswith('_')]
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
    lines = ["<available_skills>"]
    for meta in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{xml_escape(str(meta.get('name') or ''))}</name>")
        lines.append(f"    <title>{xml_escape(str(meta.get('title') or ''))}</title>")
        if meta.get("domain"):
            lines.append(f"    <domain>{xml_escape(str(meta.get('domain') or ''))}</domain>")
        if meta.get("category"):
            lines.append(f"    <category>{xml_escape(str(meta.get('category') or ''))}</category>")
        lines.append(f"    <description>{xml_escape(str(meta['description'] or ''))}</description>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    summary = "\n".join(lines)
    logger.debug(f"[skills] summary 长度: {len(summary)} 字符")
    return summary


def build_builtin_skills_prompt() -> str:
    """构建只包含代码目录内置技能的提示词。"""
    logger.debug("[skills] build_builtin_skills_prompt() -> 构建内置技能 prompt")
    return format_skills_prompt([dict(item) for item in load_metadata().values()])


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
    match = re.match(r'^---\s*\n.*?\n---\s*\n(.*)$', text, re.DOTALL)
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
        content=content
    )
