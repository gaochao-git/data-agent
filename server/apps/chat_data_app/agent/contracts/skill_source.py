"""技能服务消费的插件技能源契约。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class SkillSourcePort(Protocol):
    """已启用插件声明的技能源。"""

    def enabled_plugin_skill_sources(self) -> list[tuple[str, Path]]: ...

    async def refresh_plugin_state(self) -> None: ...


_source: SkillSourcePort | None = None


def register_skill_source(source: SkillSourcePort) -> None:
    """由装配/插件层注册实现；重复注册以最后一次为准。"""
    global _source
    _source = source


def get_skill_source() -> SkillSourcePort | None:
    return _source


__all__ = ["SkillSourcePort", "get_skill_source", "register_skill_source"]


class SkillSnapshotPort(Protocol):
    """workspace 构建会话技能快照所需的技能源。"""

    async def list_runtime_skill_sources_async(self, user_id: str) -> list[tuple[str, Path]]: ...


_snapshot_source: SkillSnapshotPort | None = None


def register_skill_snapshot_source(source: SkillSnapshotPort) -> None:
    global _snapshot_source
    _snapshot_source = source


def get_skill_snapshot_source() -> SkillSnapshotPort | None:
    return _snapshot_source


__all__ += ["SkillSnapshotPort", "get_skill_snapshot_source", "register_skill_snapshot_source"]
