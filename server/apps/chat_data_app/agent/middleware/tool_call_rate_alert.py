"""按会话统计工具调用频率，超阈值时写告警日志（合规要求）。

单个会话在统计窗口内调用工具的次数超过阈值即触发告警，不阻断工具执行。
本中间件挂在 ``ToolTimingMiddleware`` 内侧，因此只统计**真正通过租约围栏、
进入执行链路**的调用：所有权丢失被围栏拒绝的调用不计入。统计与告警都不改变
工具执行结果——任何统计异常都只记 warning 后继续，绝不影响工具返回值。

计数在单个 worker 进程内按会话聚合（``GUNICORN_WORKERS`` 默认 4）。同一会话的
一次运行由同一 worker 的长连接处理，故**单次运行内计数精确**；仅当同一会话在
多进程间并发运行时计数才被拆分，且只可能低估（漏报，不会误报）。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import wrap_tool_call

from apps.chat_data_app.services.setting_service import get_runtime_setting
from utils.alarm import emit_tool_call_rate_alarm
from utils.logger import get_logger

logger = get_logger(__name__)

# 计数表上限：超过后回收窗口已滑过的会话，避免长跑进程内存无界增长。
MAX_TRACKED_CONVERSATIONS = 2000


def _trim_expired(bucket: deque[float], cutoff: float) -> None:
    """丢弃窗口外的历史时间戳。"""
    while bucket and bucket[0] < cutoff:
        bucket.popleft()


class ConversationToolCallWindow:
    """按会话维护滑动窗口内的调用时间戳与上次告警时间。

    时间源为 ``time.monotonic``（不受系统时钟回拨影响）；``now`` 参数仅供测试注入。
    """

    def __init__(self, *, max_conversations: int = MAX_TRACKED_CONVERSATIONS) -> None:
        self._timestamps: dict[str, deque[float]] = {}
        self._last_alert_at: dict[str, float] = {}
        self._max_conversations = max_conversations

    def record(self, conversation_id: str, window_seconds: int, now: float | None = None) -> int:
        """登记一次调用，返回窗口内（含当次）的调用次数。"""
        current = time.monotonic() if now is None else now
        cutoff = current - window_seconds
        bucket = self._timestamps.setdefault(conversation_id, deque())
        _trim_expired(bucket, cutoff)
        bucket.append(current)
        self._evict_stale(cutoff)
        return len(bucket)

    def alerted_recently(self, conversation_id: str, window_seconds: int, now: float | None = None) -> bool:
        """该会话在本窗口内是否已告警过（同一窗口内最多告警一次）。"""
        current = time.monotonic() if now is None else now
        last_alert_at = self._last_alert_at.get(conversation_id)
        return last_alert_at is not None and last_alert_at >= current - window_seconds

    def mark_alerted(self, conversation_id: str, now: float | None = None) -> None:
        """记录该会话的告警时刻，用于窗口内去重。"""
        self._last_alert_at[conversation_id] = time.monotonic() if now is None else now

    def _evict_stale(self, cutoff: float) -> None:
        """会话数超上限时回收窗口已滑过的记录（仍在窗口内的计数保持不变）。"""
        if len(self._timestamps) <= self._max_conversations:
            return
        for key in [item for item, bucket in self._timestamps.items() if not bucket or bucket[-1] < cutoff]:
            self._timestamps.pop(key, None)
        for key in [item for item, at in self._last_alert_at.items() if at < cutoff]:
            self._last_alert_at.pop(key, None)


_WINDOW = ConversationToolCallWindow()


def reset_tool_call_rate_window() -> None:
    """重置计数与告警去重状态（仅供测试使用）。"""
    global _WINDOW
    _WINDOW = ConversationToolCallWindow()


def resolve_conversation_id(request: Any) -> str:
    """解析会话标识：优先业务态 ``conversation_id``，回退 LangGraph ``thread_id``。"""
    state = getattr(request, "state", None)
    if isinstance(state, Mapping):
        conversation_id = str(state.get("conversation_id") or "").strip()
        if conversation_id:
            return conversation_id
    config = getattr(getattr(request, "runtime", None), "config", None)
    if not isinstance(config, Mapping):
        return ""
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return ""
    return str(configurable.get("thread_id") or "").strip()


def resolve_user_id(request: Any) -> str:
    """从 agent state 取业务用户，供告警侧定位责任人。"""
    state = getattr(request, "state", None)
    if not isinstance(state, Mapping):
        return ""
    return str(state.get("user_id") or "").strip()


def resolve_tool_name(request: Any) -> str:
    """取当次调用的工具名。"""
    tool_call = getattr(request, "tool_call", None)
    if not isinstance(tool_call, Mapping):
        return ""
    return str(tool_call.get("name") or "").strip()


def record_tool_call_and_alert(request: Any, *, threshold: int, window_seconds: int) -> None:
    """登记一次工具调用并按需告警；任何异常都只记 warning，不向上抛出。"""
    try:
        conversation_id = resolve_conversation_id(request)
        if not conversation_id:
            # 无会话标识（如脱离 configurable 的直调）无法归属，跳过计数。
            return
        count = _WINDOW.record(conversation_id, window_seconds)
        if count <= threshold or _WINDOW.alerted_recently(conversation_id, window_seconds):
            return
        _WINDOW.mark_alerted(conversation_id)
        emit_tool_call_rate_alarm(
            conversation_id=conversation_id,
            count=count,
            threshold=threshold,
            window_seconds=window_seconds,
            tool_name=resolve_tool_name(request),
            user_id=resolve_user_id(request),
        )
    except Exception:
        logger.warning("工具调用频率统计失败，跳过本次告警判定", exc_info=True)


def create_tool_call_rate_alert_middleware(
    *,
    threshold: int | None = None,
    window_seconds: int | None = None,
):
    """创建会话级工具调用频率告警 middleware。

    默认阈值与窗口运行期经后台配置变量表读取（表覆盖 > env > 默认，TTL 缓存内
    基本无开销）；两参数仅供测试注入更小值。
    """

    @wrap_tool_call(name="ToolCallRateAlertMiddleware")
    async def tool_call_rate_alert_middleware(request, handler):
        resolved_threshold = threshold if threshold is not None else await get_runtime_setting("CHAT_DATA_TOOL_CALL_RATE_ALERT_THRESHOLD")
        resolved_window = window_seconds if window_seconds is not None else await get_runtime_setting("CHAT_DATA_TOOL_CALL_RATE_ALERT_WINDOW_SECONDS")
        record_tool_call_and_alert(request, threshold=resolved_threshold, window_seconds=resolved_window)
        return await handler(request)

    return tool_call_rate_alert_middleware
