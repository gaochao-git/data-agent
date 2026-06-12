from __future__ import annotations

import asyncio
import os
from typing import Any


def _env_float(key: str, default: float, min_value: float) -> float:
    raw = str(os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except Exception:
        return default
    if value < min_value:
        return default
    return value


def get_stream_timeout_seconds(default: float = 120.0) -> float:
    """读取流式调用超时时间（秒）。"""
    return _env_float("COMPLAINT_LLM_STREAM_TIMEOUT_SECONDS", default, 1.0)


def get_stream_heartbeat_seconds(default: float = 15.0) -> float:
    """读取流式调用心跳日志间隔（秒）。"""
    return _env_float("COMPLAINT_LLM_STREAM_HEARTBEAT_SECONDS", default, 0.0)


async def read_next_stream_chunk(stream_iter: Any, timeout_seconds: float) -> tuple[Any, bool]:
    """按超时读取下一条流式 chunk。"""
    try:
        chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=max(1.0, float(timeout_seconds)))
        return chunk, False
    except StopAsyncIteration:
        return None, True

