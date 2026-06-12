from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from utils.storage_paths import COMPLAINTS_BASE_DIR


@dataclass(frozen=True)
class SkillConfig:
    """skills 配置与路径信息。"""

    local_root: Path
    claude_global_root: Path
    complains_root: Path
    min_refresh_interval: float

    @classmethod
    def from_env(cls) -> "SkillConfig":
        """从环境变量构建技能配置。"""
        local_root = Path(__file__).resolve().parent / ".complaint_skills"
        complains_root = Path(COMPLAINTS_BASE_DIR) / ".complaint_skills"
        claude_global_root = Path.home() / ".claude" / "skills"
        min_refresh_interval = float(os.getenv("SKILLS_REFRESH_INTERVAL_SECONDS", "5"))
        return cls(
            local_root=local_root,
            complains_root=complains_root,
            claude_global_root=claude_global_root,
            min_refresh_interval=min_refresh_interval,
        )
