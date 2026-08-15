"""Shared execution order for streamed chat-data agent turns."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


AsyncHook = Callable[[], Awaitable[None]]
AsyncPredicate = Callable[[], Awaitable[bool]]
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
    after_turn: AsyncHook | None = None,
    after_stream: AsyncHook | None = None,
    continue_stream: AsyncPredicate | None = None,
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
    try:
        await persist_user_message()
        if before_stream and not await before_stream():
            return

        while True:
            async for sse_event in stream_adapter.stream_events():
                yield sse_event

            end_payload = await stream_adapter.finish(**(finalize_stream_kwargs or {}))
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
                yield format_sse("done", "[DONE]")
                return
            if stream_adapter.disconnected:
                if on_disconnect:
                    await on_disconnect()
                if finalize_resources:
                    await finalize_resources(True)
                    resources_finalized = True
                return
            if not continue_stream or not await continue_stream():
                break
        if finalize_resources:
            await finalize_resources(True)
            resources_finalized = True
        if after_turn:
            await after_turn()
        yield format_sse("message_end", end_payload)
        yield format_sse("done", "[DONE]")
    except asyncio.CancelledError:
        if finalize_resources:
            await finalize_resources(cleanup_success_on_cancel)
            resources_finalized = True
        raise
    except Exception:
        await persist_partial_history()
        raise
    finally:
        if finalize_resources and not resources_finalized:
            await finalize_resources(False)
