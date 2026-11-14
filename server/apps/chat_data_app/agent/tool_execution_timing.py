"""Per-tool execution clock shared by middleware and serialized workspace operations.

LangGraph starts all tool calls from one assistant message concurrently. Sandbox
operations then serialize on the workspace operation lock, so the middleware
wrapper start includes queue wait. The lock marks the first moment a tool truly
gets execution ownership; timing middleware persists that value when available.
"""

from __future__ import annotations

import time
from contextvars import ContextVar, Token
from dataclasses import dataclass


def _now_epoch_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ToolExecutionClock:
    wrapper_started_at_ms: int
    execution_started_at_ms: int | None = None


_CURRENT_CLOCK: ContextVar[ToolExecutionClock | None] = ContextVar(
    "chat_data_tool_execution_clock",
    default=None,
)


def begin_tool_execution_clock() -> tuple[ToolExecutionClock, Token[ToolExecutionClock | None]]:
    """Start a per-tool clock in the current coroutine context."""
    clock = ToolExecutionClock(wrapper_started_at_ms=_now_epoch_ms())
    return clock, _CURRENT_CLOCK.set(clock)


def reset_tool_execution_clock(token: Token[ToolExecutionClock | None]) -> None:
    _CURRENT_CLOCK.reset(token)


def mark_tool_execution_started(started_at_ms: int | None = None) -> None:
    """Mark first real execution instant; no-op outside a timed tool call.

    A tool can acquire multiple serialized resources. Only the first acquisition
    is the user-meaningful start of that tool's execution.
    """
    clock = _CURRENT_CLOCK.get()
    if clock is None or clock.execution_started_at_ms is not None:
        return
    candidate = started_at_ms if isinstance(started_at_ms, int) else _now_epoch_ms()
    clock.execution_started_at_ms = max(candidate, clock.wrapper_started_at_ms)
