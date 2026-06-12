from __future__ import annotations

import json
import time
from typing import Any


def timing_now() -> float:
    """返回单调时钟起点。"""
    return time.perf_counter()


def timing_elapsed_ms(started_at: float) -> int:
    """计算耗时毫秒。"""
    return int((time.perf_counter() - float(started_at)) * 1000)


def _timing_extra_json(extra: dict[str, Any] | None = None) -> str:
    payload = {k: v for k, v in (extra or {}).items() if v is not None and v != ""}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def log_route_timing(logger: Any, app_name: str, route: str, duration_ms: int, success: bool, extra: dict[str, Any] | None = None) -> None:
    """记录接口总耗时日志。"""
    logger.info("[timing] app=%s kind=route route=%s duration_ms=%s success=%s extra=%s", app_name, route, int(duration_ms), bool(success), _timing_extra_json(extra))


def log_phase_timing(logger: Any, app_name: str, route: str, phase: str, duration_ms: int, success: bool, extra: dict[str, Any] | None = None) -> None:
    """记录接口阶段耗时日志。"""
    logger.info("[timing] app=%s kind=phase route=%s phase=%s duration_ms=%s success=%s extra=%s", app_name, route, phase, int(duration_ms), bool(success), _timing_extra_json(extra))
