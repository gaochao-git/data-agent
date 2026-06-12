"""Utility to orchestrate grouped async streams.

Model:
- Groups run in parallel.
- Streams inside a group run sequentially.
- You can add streams at any time; if a group is active the new stream joins
  its queue, otherwise a new group task is started.
- Call ``finish_group`` (or ``close``) when a group should stop so ``run`` can
  complete.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, Optional

_STOP = object()
_GROUP_DONE = object()


class _GroupContext:
    def __init__(self, name: Any):
        self.name = name
        self.queue: asyncio.Queue[AsyncIterator[Any] | object] = asyncio.Queue()
        self.accepting = True
        self.task: Optional[asyncio.Task[Any]] = None

    def add_stream(self, stream: AsyncIterator[Any]) -> None:
        if not self.accepting:
            raise RuntimeError(f"group {self.name!r} is closed")
        self.queue.put_nowait(stream)

    def finish(self) -> None:
        if not self.accepting:
            return
        self.accepting = False
        self.queue.put_nowait(_STOP)


class StreamManager:
    """Run multiple groups of async iterators.

    - Group: sequential streams.
    - Groups: run concurrently.
    - ``add`` can be called before or after ``run`` starts.
    - When a group finishes (via ``finish_group``/``close``) and completes, it
      is removed; adding the same group name later will start a fresh group.
    """

    def __init__(self) -> None:
        self._groups: Dict[Any, _GroupContext] = {}
        self._output_queue: asyncio.Queue[tuple[_GroupContext, Any]] = asyncio.Queue()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._active_tasks = 0
        self._new_task_event = asyncio.Event()
        self._running = False
        self._closed = False

    def add(self, stream: AsyncIterator[Any], group: Any) -> None:
        if self._closed:
            raise RuntimeError("manager is closed")

        ctx = self._groups.get(group)
        if ctx is None or not ctx.accepting:
            ctx = _GroupContext(group)
            self._groups[group] = ctx
            if self._running:
                self._start_group(ctx)

        ctx.add_stream(stream)

    def finish_group(self, group: Any) -> None:
        ctx = self._groups.get(group)
        if ctx:
            ctx.finish()

    def close(self) -> None:
        self._closed = True
        for ctx in list(self._groups.values()):
            ctx.finish()
        self._new_task_event.set()

    async def run(self) -> AsyncIterator[Any]:
        if self._running:
            raise RuntimeError("run already in progress")
        self._running = True

        for ctx in list(self._groups.values()):
            self._start_group(ctx)

        try:
            while True:
                if self._active_tasks == 0:
                    if self._closed:
                        break
                    await self._wait_for_new_task()
                    continue

                ctx, item = await self._output_queue.get()
                if item is _GROUP_DONE:
                    self._active_tasks -= 1
                    if self._groups.get(ctx.name) is ctx:
                        self._groups.pop(ctx.name, None)
                    continue

                yield item
        finally:
            self._running = False

    def _start_group(self, ctx: _GroupContext) -> None:
        if ctx.task:
            return
        ctx.task = asyncio.create_task(self._run_group(ctx))
        self._tasks.add(ctx.task)
        self._active_tasks += 1
        self._new_task_event.set()
        ctx.task.add_done_callback(self._tasks.discard)

    async def _run_group(self, ctx: _GroupContext) -> None:
        try:
            while True:
                stream = await ctx.queue.get()
                if stream is _STOP:
                    break
                async for chunk in stream:
                    await self._output_queue.put((ctx, chunk))
        finally:
            await self._output_queue.put((ctx, _GROUP_DONE))

    async def _wait_for_new_task(self) -> None:
        if self._active_tasks > 0:
            return
        if self._new_task_event.is_set():
            self._new_task_event.clear()
            return
        await self._new_task_event.wait()
        self._new_task_event.clear()


__all__ = ["StreamManager"]
