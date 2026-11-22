"""工具参数生成过程的实时预览（主图 / 子图共用口径）。

模型逐 token 生成 tool_call.args 时，LangChain 通过 ``tool_call_chunks`` 给出
连续片段。这里按 tool_call_id 做 16ms 时间窗合并：首片立即下发，同一窗口内的
后续片段合并后再发；模型没有增量时不会凭空产生增量。最多预览 8000 字符，
最终完整参数仍由 ``tool_started`` / ``replace_args`` 快照收口。
"""

import time
from collections.abc import Callable
from typing import Any

from .sse_protocol import SSE_EVENT_TOOL_ARGS_DELTA
from .sse_protocol import format_sse as _format_sse

TOOL_ARGS_PREVIEW_MAX_CHARS = 8000
TOOL_ARGS_PREVIEW_WINDOW_MS = 16


class ToolArgsPreviewBuffer:
    """按 tool_call_id 做时间窗合并并截断工具参数预览。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._pending: dict[str, str] = {}
        self._last_emit_at: dict[str, float] = {}
        self._sent_chars: dict[str, int] = {}
        self._truncated: set[str] = set()
        self._emitted: set[str] = set()

    def take(self, tool_call_id: str, delta: str) -> tuple[str, bool] | None:
        """并入一个参数分片；窗口到期或封顶时返回 ``(预览增量, 是否截断)``。"""
        if not tool_call_id or not delta or tool_call_id in self._truncated:
            return None
        sent = self._sent_chars.get(tool_call_id, 0)
        remaining = TOOL_ARGS_PREVIEW_MAX_CHARS - sent
        if remaining <= 0:
            self._truncated.add(tool_call_id)
            return None
        accepted = delta[:remaining]
        self._sent_chars[tool_call_id] = sent + len(accepted)
        pending_before = self._pending.get(tool_call_id, "")
        now = self._clock()
        self._pending[tool_call_id] = pending_before + accepted
        truncated = len(accepted) < len(delta) or self._sent_chars[tool_call_id] >= TOOL_ARGS_PREVIEW_MAX_CHARS
        if truncated:
            self._truncated.add(tool_call_id)
        pending = self._pending[tool_call_id]
        last_emit_at = self._last_emit_at.get(tool_call_id)
        in_window = (
            last_emit_at is not None
            and (now - last_emit_at) * 1000 < TOOL_ARGS_PREVIEW_WINDOW_MS
        )
        # 首片立即发；上次发送后 16ms 内的后续分片合并，避免慢流被窗口拖住。
        if in_window and not truncated:
            return None
        self._pending.pop(tool_call_id, None)
        self._last_emit_at[tool_call_id] = now
        self._emitted.add(tool_call_id)
        return pending, truncated

    def flush(self, tool_call_id: str) -> tuple[str, bool] | None:
        """把窗口内尚未发送的尾部分片立即吐出；无待发内容时返回 None。"""
        pending = self._pending.pop(tool_call_id, None)
        if not pending:
            return None
        self._last_emit_at[tool_call_id] = self._clock()
        self._emitted.add(tool_call_id)
        return pending, tool_call_id in self._truncated

    def pending_tool_call_ids(self) -> list[str]:
        """返回仍有窗口内待发内容的工具调用 id，供收口时定向 flush。"""
        return list(self._pending)

    def has_preview(self, tool_call_id: str) -> bool:
        """该工具是否真的向客户端下发过参数预览（决定收口时是否需要补发快照）。"""
        return tool_call_id in self._emitted


def build_tool_args_delta_payload(
    tool_call_id: str,
    tool_name: str,
    delta: str,
    *,
    truncated: bool = False,
    done: bool = False,
    parent_tool_call_id: str | None = None,
) -> dict[str, Any]:
    """构造工具参数增量事件载荷；子智能体只差一个 parent_tool_call_id。"""
    payload: dict[str, Any] = {
        "type": SSE_EVENT_TOOL_ARGS_DELTA,
        "event_type": SSE_EVENT_TOOL_ARGS_DELTA,
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "status": "generating",
        "delta": delta,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if truncated:
        payload["truncated"] = True
    if done:
        payload["done"] = True
    if parent_tool_call_id:
        payload["parent_tool_call_id"] = parent_tool_call_id
    return payload


def format_tool_args_delta_event(
    tool_call_id: str,
    tool_name: str,
    delta: str,
    *,
    truncated: bool = False,
    done: bool = False,
) -> str:
    """序列化主智能体的工具参数增量 SSE 帧。"""
    return _format_sse(
        SSE_EVENT_TOOL_ARGS_DELTA,
        build_tool_args_delta_payload(tool_call_id, tool_name, delta, truncated=truncated, done=done),
    )
