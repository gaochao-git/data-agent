"""SSE 传输层心跳单测。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from apps.chat_data_app.agent.run.sse_heartbeat import stream_sse_with_heartbeat


async def _collect(stream: AsyncIterator[str], timeout: float = 2.0) -> list[str]:
    frames: list[str] = []

    async def read() -> None:
        async for frame in stream:
            frames.append(frame)

    await asyncio.wait_for(read(), timeout=timeout)
    return frames


@pytest.mark.asyncio
async def test_sse_heartbeat_before_and_between_events() -> None:
    async def events() -> AsyncIterator[dict[str, Any]]:
        yield {"stream_id": "1-0", "payload": "data: first\n\n"}
        await asyncio.sleep(0.05)
        yield {"stream_id": "2-0", "payload": "data: second\n\n"}

    frames = await _collect(stream_sse_with_heartbeat(events(), interval=0.01))

    assert frames[0].startswith("event: heartbeat\n")
    assert any("id: 1-0" in frame and "data: first" in frame for frame in frames)
    assert any("id: 2-0" in frame and "data: second" in frame for frame in frames)
    assert any("event: heartbeat" in frame for frame in frames[1:])


@pytest.mark.asyncio
async def test_sse_heartbeat_stops_when_client_disconnects() -> None:
    async def events() -> AsyncIterator[dict[str, Any]]:
        await asyncio.sleep(10)
        yield {"stream_id": "never", "payload": "data: never\n\n"}

    async def disconnected() -> bool:
        return True

    frames = await _collect(
        stream_sse_with_heartbeat(events(), is_disconnected=disconnected, interval=1),
        timeout=1,
    )
    assert len(frames) == 1
    assert frames[0].startswith("event: heartbeat\n")
