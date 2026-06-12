"""
Skills Loader - 符合 agentskills.io 规范

API：
- parse_metadata(skill_path): 解析单个技能元数据
- discover_skills(directories): 扫描目录发现技能
- load_metadata(): 启动时加载所有元数据
- build_skills_prompt(): 构建注入 system prompt 的技能描述
- activate_skill(name): 激活技能，按需加载完整内容
"""

import re
from pathlib import Path
from typing import TypedDict

from utils.logger import get_logger

logger = get_logger(__name__)


# ============ 类型定义 ============

class SkillMetadata(TypedDict):
    """启动时加载的轻量元数据"""
    name: str
    description: str
    path: str  # SKILL.md 文件路径


class Skill(TypedDict):
    """按需加载的完整技能"""
    name: str
    description: str
    content: str


# ============ 内部状态 ============

_metadata_cache: dict[str, SkillMetadata] | None = None


# ============ API ============

def parse_metadata(skill_path: Path) -> SkillMetadata | None:
    """解析单个技能的 frontmatter 元数据"""
    try:
        text = skill_path.read_text(encoding="utf-8")
    except Exception:
        return None

    match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    if not match:
        return None

    frontmatter = {}
    for line in match.group(1).strip().split('\n'):
        if ':' in line:
            k, v = line.split(':', 1)
            frontmatter[k.strip()] = v.strip()

    name = frontmatter.get("name", "")
    if not name:
        return None

    return SkillMetadata(
        name=name,
        description=frontmatter.get("description", ""),
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


def build_skills_prompt() -> str:
    """构建注入 system prompt 的技能描述（XML 格式）"""
    logger.info("[skills] build_skills_prompt() -> 构建技能 prompt")
    metadata = load_metadata()

    lines = ["<available_skills>"]
    for meta in metadata.values():
        lines.append("  <skill>")
        lines.append(f"    <name>{meta['name']}</name>")
        lines.append(f"    <description>{meta['description']}</description>")
        lines.append("  </skill>")
    lines.append("</available_skills>")

    summary = "\n".join(lines)
    logger.debug(f"[skills] summary 长度: {len(summary)} 字符")
    return summary


def activate_skill(name: str) -> Skill | None:
    """激活技能，按需加载完整内容"""
    logger.info(f"[skills] activate_skill('{name}') -> 加载详情")
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

    logger.info(f"[skills] 返回 '{name}' 内容，{len(content)} 字符")
    return Skill(
        name=meta["name"],
        description=meta["description"],
        content=content
    )
