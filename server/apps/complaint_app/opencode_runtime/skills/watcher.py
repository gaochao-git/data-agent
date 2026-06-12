from __future__ import annotations

import time


class SkillWatcher:
    """skills 热更新检测器。"""

    def __init__(self, config) -> None:
        self._min_refresh_interval = max(0.0, float(config.min_refresh_interval))

    def should_refresh(self, last_scan_at: float | None) -> bool:
        """判断是否应触发重新扫描。"""
        if last_scan_at is None:
            return True
        return (time.time() - last_scan_at) >= self._min_refresh_interval
