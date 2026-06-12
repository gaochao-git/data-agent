from __future__ import annotations

from dataclasses import dataclass
import os
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RuntimeConfig:
    """运行时配置（来自环境变量）。"""
    default_agent: str = "build"
    enable_planner: bool = False
    max_parallel_tasks: int = 3
    compaction_threshold: float = 0.92
    compaction_target_ratio: float = 0.75

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        def _env_bool(key: str, default: bool) -> bool:
            val = os.getenv(key)
            if val is None:
                return default
            stripped = val.strip().lower()
            if not stripped:
                return default
            return stripped in {"1", "true", "yes", "y", "on"}

        def _env_int(key: str, default: int) -> int:
            raw = os.getenv(key)
            if not raw:
                return default
            try:
                return int(raw)
            except Exception as exc:
                logger.warning("[opencode_runtime.config] parse int env failed key=%s raw=%s err=%s", key, raw, exc)
                return default

        def _env_float(key: str, default: float) -> float:
            raw = os.getenv(key)
            if raw is None:
                return default
            try:
                return float(raw)
            except Exception as exc:
                logger.warning("[opencode_runtime.config] parse float env failed key=%s raw=%s err=%s", key, raw, exc)
                return default

        return cls(
            default_agent=os.getenv("OPENCODE_DEFAULT_AGENT", "build").strip() or "build",
            enable_planner=_env_bool("OPENCODE_ENABLE_PLANNER", False),
            max_parallel_tasks=_env_int("OPENCODE_MAX_PARALLEL_TASKS", 3),
            compaction_threshold=_env_float("OPENCODE_COMPACTION_THRESHOLD", 0.92),
            compaction_target_ratio=_env_float("OPENCODE_COMPACTION_TARGET_RATIO", 0.75),
        )
