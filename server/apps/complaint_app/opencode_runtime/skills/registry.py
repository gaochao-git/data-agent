from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Optional

from utils.logger import get_logger

from .config import SkillConfig
from .models import SkillProperties
from .parser import find_skill_md
from .watcher import SkillWatcher
from .validator import validate
from .errors import SkillError
from .parser import read_properties

logger = get_logger(__name__)


def scan(roots: Iterable[Path]) -> list[Path]:
    """扫描技能目录，返回包含 SKILL.md 的目录路径。"""
    matches: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("SKILL.md"):
            if path.is_file():
                matches.add(path.parent)
        for path in root.rglob("skill.md"):
            if path.is_file():
                matches.add(path.parent)
    return sorted(matches)


def load(skill_dir: Path) -> Optional[SkillProperties]:
    """加载技能目录，返回 SkillProperties（严格校验）。"""
    errors = validate(skill_dir)
    if errors:
        logger.warning("skill validation failed: %s errors=%s", skill_dir, errors)
        return None
    try:
        return read_properties(skill_dir)
    except SkillError as exc:
        logger.warning("skill properties read failed: %s err=%s", skill_dir, exc)
        return None


class SkillRegistry:
    """skills 缓存与查询入口。"""

    def __init__(self, config: SkillConfig, watcher: SkillWatcher) -> None:
        self._config = config
        self._watcher = watcher
        self._skills: dict[str, SkillProperties] = {}
        self._last_scan_at: float | None = None

    def _scan_roots(self) -> list[tuple[Path, str]]:
        roots: list[tuple[Path, str]] = [(self._config.local_root, "local"),
                                         (self._config.claude_global_root, "global")]
        return roots

    def refresh_if_needed(self, force: bool = False) -> None:
        """按需刷新技能缓存。"""
        if not force and not self._watcher.should_refresh(self._last_scan_at):
            return
        roots = self._scan_roots()
        all_dirs: list[tuple[Path, str]] = []
        for root, source in roots:
            for skill_dir in scan([root]):
                all_dirs.append((skill_dir, source))

        skills: dict[str, SkillProperties] = {}
        scan_time = time.time()
        for skill_dir, source in all_dirs:
            skill_md = find_skill_md(skill_dir)
            if skill_md is None:
                continue
            try:
                mtime = skill_md.stat().st_mtime
            except Exception as exc:
                logger.warning("skill stat failed: %s err=%s", skill_md, exc)
                continue
            props = load(skill_dir)
            if not props:
                continue
            name = props.name
            existing = skills.get(name)
            if existing and existing.source == "local" and source == "global":
                logger.warning("duplicate skill ignored (keep local): %s", name)
                continue
            if existing and existing.source == "local" and source == "local":
                logger.warning("duplicate local skill ignored: %s", name)
                continue
            if existing and existing.source == "global" and source == "local":
                logger.warning("duplicate skill overridden by local: %s", name)
            props.location = str(skill_md)
            props.source = source
            props.mtime = mtime
            props.last_scan_at = scan_time
            skills[name] = props

        self._skills = skills
        self._last_scan_at = scan_time

    def all(self) -> list[SkillProperties]:
        """返回所有技能信息。"""
        self.refresh_if_needed()
        return list(self._skills.values())

    def get(self, name: str) -> SkillProperties | None:
        """按名称获取技能信息。"""
        self.refresh_if_needed()
        return self._skills.get(name)

    def get_location(self, name: str) -> str | None:
        """获取技能 SKILL.md 路径。"""
        self.refresh_if_needed()
        item = self._skills.get(name)
        return item.location if item else None

    def get_source(self, name: str) -> str | None:
        """获取技能来源（local/global）。"""
        self.refresh_if_needed()
        item = self._skills.get(name)
        return item.source if item else None
