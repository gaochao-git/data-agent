from __future__ import annotations

from typing import Iterable

from .models import SkillProperties


class PermissionPolicy:
    """技能白名单权限策略。"""

    def __init__(self) -> None:
        self._allowed = None

    def is_allowed(self, name: str) -> bool:
        """判断技能是否允许访问。"""
        if self._allowed is None:
            return True
        return name in self._allowed

    def filter(self, skills: Iterable[SkillProperties]) -> list[SkillProperties]:
        """过滤技能列表，仅保留允许项。"""
        if self._allowed is None:
            return list(skills)
        return [item for item in skills if item.name in self._allowed]
