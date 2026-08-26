"""Shared execution order for streamed chat-data agent turns."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


AsyncHook = Callable[[], Awaitable[None]]
AsyncInterruptHook = Callable[[dict[str, Any]], Awaitable[None]]
SseFormatter = Callable[[str, dict[str, Any] | str], str]
ResourceFinalizer = Callable[[bool], Awaitable[None]]


async def stream_agent_turn(
    *,
    stream_adapter: Any,
    format_sse: SseFormatter,
    persist_user_message: AsyncHook,
    persist_turn_history: AsyncHook,
    persist_partial_history: AsyncHook,
    before_stream: Callable[[], Awaitable[bool]] | None = None,
    after_finish: AsyncHook | None = None,
    after_turn: AsyncHook | None = None,
    after_stream: AsyncHook | None = None,
    on_interrupt: AsyncInterruptHook | None = None,
    on_disconnect: AsyncHook | None = None,
    finalize_stream_kwargs: dict[str, Any] | None = None,
    finalize_resources: ResourceFinalizer | None = None,
    cleanup_success_on_cancel: bool = False,
) -> AsyncIterator[str]:
    """Run the common persisted streaming lifecycle for one agent conversation turn.

    Agent-specific handlers prepare prompts and resources before calling this
    function. Optional hooks cover resources whose lifetime is exactly one turn,
    such as the DataAnalyze sandbox execution session. Resource finalization
    runs after the model stream but before ``message_end`` is emitted.
    """
    resources_finalized = False
    user_persistence_task: asyncio.Task | None = None

    async def wait_user_persistence() -> None:
        if user_persistence_task is not None:
            await user_persistence_task

    try:
        # The user message is already visible in the local UI. Persist it in
        # parallel with Agent startup, but wait before final history is saved
        # so the durable event order remains user -> assistant/tool events.
        user_persistence_task = asyncio.create_task(persist_user_message())
        if before_stream and not await before_stream():
            await wait_user_persistence()
            return

        async for sse_event in stream_adapter.stream_events():
            yield sse_event

        await wait_user_persistence()
        end_payload = await stream_adapter.finish(**(finalize_stream_kwargs or {}))
        if after_finish:
            await after_finish()
        await persist_turn_history()
        if after_stream:
            await after_stream()
        pending_interrupt = getattr(stream_adapter, "pending_interrupt", None)
        interrupt_payload = pending_interrupt() if callable(pending_interrupt) else None
        if interrupt_payload:
            if on_interrupt:
                await on_interrupt(interrupt_payload)
            if finalize_resources:
                await finalize_resources(True)
                resources_finalized = True
            yield format_sse("question_requested", interrupt_payload)
            # done 由 Runtime 统一收口后发布（结构化 status），生成器只产出事实。
            return
        if stream_adapter.disconnected:
            if on_disconnect:
                await on_disconnect()
            if finalize_resources:
                await finalize_resources(True)
                resources_finalized = True
            return
        if finalize_resources:
            await finalize_resources(True)
            resources_finalized = True
        if after_turn:
            await after_turn()
        yield format_sse("message_end", end_payload)
        # done 由 Runtime 统一收口后发布（结构化 status），生成器只产出事实。
    except asyncio.CancelledError:
        if user_persistence_task and not user_persistence_task.done():
            user_persistence_task.cancel()
        if user_persistence_task:
            await asyncio.gather(user_persistence_task, return_exceptions=True)
        if finalize_resources:
            await finalize_resources(cleanup_success_on_cancel)
            resources_finalized = True
        raise
    except Exception:
        if user_persistence_task:
            await asyncio.gather(user_persistence_task, return_exceptions=True)
        await persist_partial_history()
        raise
    finally:
        if user_persistence_task and not user_persistence_task.done():
            user_persistence_task.cancel()
            await asyncio.gather(user_persistence_task, return_exceptions=True)
        if finalize_resources and not resources_finalized:
            await finalize_resources(False)
