"""工具参数生成过程的实时预览（主图 / 子图共用口径）。

模型逐 token 生成 tool_call.args 时，LangChain 通过 ``tool_call_chunks`` 给出
连续片段。这里按 tool_call_id 合并到 32 字符再上报，避免逐 token SSE 洪峰；
最多预览 8000 字符，最终完整参数仍由 ``tool_started`` / ``replace_args`` 快照收口。
"""

import time
from typing import Any

from .sse_protocol import SSE_EVENT_TOOL_ARGS_DELTA
from .sse_protocol import format_sse as _format_sse

TOOL_ARGS_PREVIEW_MAX_CHARS = 8000
TOOL_ARGS_PREVIEW_FLUSH_CHARS = 32


class ToolArgsPreviewBuffer:
    """按 tool_call_id 合并并截断工具参数预览。"""

    def __init__(self) -> None:
        self._pending: dict[str, str] = {}
        self._sent_chars: dict[str, int] = {}
        self._truncated: set[str] = set()
        self._emitted: set[str] = set()

    def take(self, tool_call_id: str, delta: str) -> tuple[str, bool] | None:
        """收集一个参数分片；达到发送阈值或封顶时返回 ``(预览增量, 是否截断)``。"""
        if not tool_call_id or not delta or tool_call_id in self._truncated:
            return None
        sent = self._sent_chars.get(tool_call_id, 0)
        remaining = TOOL_ARGS_PREVIEW_MAX_CHARS - sent
        if remaining <= 0:
            self._truncated.add(tool_call_id)
            return None
        accepted = delta[:remaining]
        self._sent_chars[tool_call_id] = sent + len(accepted)
        self._pending[tool_call_id] = self._pending.get(tool_call_id, "") + accepted
        truncated = len(accepted) < len(delta) or self._sent_chars[tool_call_id] >= TOOL_ARGS_PREVIEW_MAX_CHARS
        if truncated:
            self._truncated.add(tool_call_id)
        pending = self._pending[tool_call_id]
        if len(pending) < TOOL_ARGS_PREVIEW_FLUSH_CHARS and not truncated:
            return None
        self._pending[tool_call_id] = ""
        self._emitted.add(tool_call_id)
        return pending, truncated

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
