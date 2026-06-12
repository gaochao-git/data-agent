from __future__ import annotations

from pathlib import Path

from utils.logger import get_logger

from .config import SkillConfig
from .models import SkillProperties
from .permission import PermissionPolicy
from .registry import SkillRegistry
from .watcher import SkillWatcher

logger = get_logger(__name__)


class SkillService:
    """skills 服务入口。"""

    def __init__(self) -> None:
        self._permission = PermissionPolicy()
        self._config = SkillConfig.from_env()
        self._registry = SkillRegistry(self._config, SkillWatcher(self._config))

    def list(self) -> list[SkillProperties]:
        """返回可用技能列表。"""
        skills = self._registry.all()
        return self._permission.filter(skills)

    def get(self, name: str) -> SkillProperties | None:
        """按名称获取技能信息（含权限校验）。"""
        if not self._permission.is_allowed(name):
            logger.warning("skill access denied: %s", name)
            return None
        return self._registry.get(name)

    def load_content(self, name: str) -> str:
        """读取 SKILL.md 正文内容。"""
        props = self._registry.get(name)
        location = props.location if props else None
        if not location:
            return ""
        try:
            return Path(location).read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("skill content load failed: %s", exc)
            return ""

    def tool_prompt(self) -> str:
        """生成 <available_skills> XML 片段。"""
        skills = self.list()
        if not skills:
            description = (
                "Load a skill to get detailed instructions for a specific task. "
                "No skills are currently available."
            )
            return description
        lines = ["<available_skills>"]
        for skill in skills:
            lines.append(skill.to_prompt_xml())
        lines.append("</available_skills>")
        description = "\n".join(
            [
                "Load a skill to get detailed instructions for a specific task.",
                "Skills provide specialized knowledge and step-by-step guidance.",
                "Use this when a task matches an available skill's description.",
                "Only the skills listed here are available:",
                "\n".join(lines)
            ]
        )
        return description

    def resolve_references(self, name: str) -> list[dict[str, str]]:
        """读取 references 目录内容（可选）。"""
        location = self._registry.get_location(name)
        if not location:
            return []
        root = Path(location).parent / "references"
        if not root.exists():
            return []
        results: list[dict[str, str]] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                results.append({"path": str(path), "content": path.read_text(encoding="utf-8")})
            except Exception as exc:
                logger.warning("reference load failed: %s", exc)
        return results

    def list_assets(self, name: str) -> list[str]:
        """返回 assets 目录资源列表（可选）。"""
        location = self._registry.get_location(name)
        if not location:
            return []
        root = Path(location).parent / "assets"
        if not root.exists():
            return []
        return [str(path) for path in root.rglob("*") if path.is_file()]
