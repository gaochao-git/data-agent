"""SSE 传输层心跳：慢模型/长工具期间保持连接活跃，不写入业务事件。"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from apps.chat_data_app.sse_protocol import SSE_EVENT_HEARTBEAT, format_sse

HEARTBEAT_INTERVAL_SECONDS = 5.0
DisconnectCheck = Callable[[], Awaitable[bool] | bool] | bool


def _heartbeat_frame() -> str:
    return format_sse(SSE_EVENT_HEARTBEAT, {"ts": int(time.time() * 1000)})


async def stream_sse_with_heartbeat(
    events: AsyncIterator[dict[str, Any]],
    *,
    is_disconnected: DisconnectCheck | None = None,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> AsyncIterator[str]:
    """pump task 独立消费事件，主生成器超时发心跳；超时不能取消上游生成器。"""
    queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()
    end = object()

    async def pump() -> None:
        try:
            async for event in events:
                await queue.put(event)
        finally:
            await queue.put(end)

    async def disconnected() -> bool:
        if is_disconnected is None:
            return False
        if isinstance(is_disconnected, bool):
            return is_disconnected
        result = is_disconnected()
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    pump_task = asyncio.create_task(pump(), name="chatdata-sse-pump")
    try:
        yield _heartbeat_frame()
        while True:
            if await disconnected():
                return
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
            except TimeoutError:
                yield _heartbeat_frame()
                continue
            if item is end:
                return
            event = cast(dict[str, Any], item)
            yield f"id: {event['stream_id']}\n{event['payload']}"
    finally:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task
