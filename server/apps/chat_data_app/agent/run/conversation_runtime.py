"""Shared execution order for streamed chat-data agent turns."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


AsyncHook = Callable[[], Awaitable[None]]
AsyncInterruptHook = Callable[[dict[str, Any]], Awaitable[None]]
SseFormatter = Callable[[str, dict[str, Any] | str], str]
ResourceFinalizer = Callable[[bool], Awaitable[None]]


async def _run_optional_hook(hook: AsyncHook | None) -> None:
    if hook is not None:
        await hook()


async def _abort_user_persistence(task: asyncio.Task | None) -> None:
    """取消并等待用户消息持久化任务（已结束时直接等待收敛）。"""
    if task is not None and not task.done():
        task.cancel()
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)


def _resolve_turn_outcome(stream_adapter: Any) -> tuple[str, Any]:
    """正常流结束后的出口判定：interrupt / disconnected / completed。"""
    pending_interrupt = getattr(stream_adapter, "pending_interrupt", None)
    interrupt_payload = pending_interrupt() if callable(pending_interrupt) else None
    if interrupt_payload:
        return "interrupt", interrupt_payload
    if stream_adapter.disconnected:
        return "disconnected", None
    return "completed", None


async def _stream_turn_wait_user_persistence(user_persistence_task: asyncio.Task | None) -> None:
    if user_persistence_task is not None:
        await user_persistence_task


async def _stream_turn_finalize_now(finalize_resources: ResourceFinalizer | None, success: bool) -> bool:
    if finalize_resources is not None:
        await finalize_resources(success)
        return True
    return False


async def _maybe_invoke_interrupt_hook(on_interrupt: AsyncInterruptHook | None, interrupt_payload: Any) -> None:
    if on_interrupt is not None:
        await on_interrupt(interrupt_payload)


async def _maybe_invoke_disconnect_hook(on_disconnect: AsyncHook | None) -> None:
    if on_disconnect is not None:
        await on_disconnect()


async def _await_user_persistence_safely(user_persistence_task: asyncio.Task | None) -> None:
    if user_persistence_task is not None:
        await asyncio.gather(user_persistence_task, return_exceptions=True)


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

    try:
        # The user message is already visible in the local UI. Persist it in
        # parallel with Agent startup, but wait before final history is saved
        # so the durable event order remains user -> assistant/tool events.
        user_persistence_task = asyncio.create_task(persist_user_message())
        if before_stream is not None and not await before_stream():
            await _stream_turn_wait_user_persistence(user_persistence_task)
            return

        async for sse_event in stream_adapter.stream_events():
            yield sse_event

        await _stream_turn_wait_user_persistence(user_persistence_task)
        end_payload = await stream_adapter.finish(**(finalize_stream_kwargs or {}))
        await _run_optional_hook(after_finish)
        await persist_turn_history()
        await _run_optional_hook(after_stream)
        outcome, interrupt_payload = _resolve_turn_outcome(stream_adapter)
        if outcome == "interrupt":
            await _maybe_invoke_interrupt_hook(on_interrupt, interrupt_payload)
            resources_finalized = await _stream_turn_finalize_now(finalize_resources, True)
            yield format_sse("question_requested", interrupt_payload)
            # done 由 Runtime 统一收口后发布（结构化 status），生成器只产出事实。
            return
        if outcome == "disconnected":
            await _maybe_invoke_disconnect_hook(on_disconnect)
            resources_finalized = await _stream_turn_finalize_now(finalize_resources, True)
            return
        resources_finalized = await _stream_turn_finalize_now(finalize_resources, True)
        if after_turn is not None:
            await after_turn()
        yield format_sse("message_end", end_payload)
        # done 由 Runtime 统一收口后发布（结构化 status），生成器只产出事实。
    except asyncio.CancelledError:
        await _abort_user_persistence(user_persistence_task)
        resources_finalized = await _stream_turn_finalize_now(finalize_resources, cleanup_success_on_cancel)
        raise
    except Exception:
        await _await_user_persistence_safely(user_persistence_task)
        await persist_partial_history()
        raise
    finally:
        await _abort_user_persistence(user_persistence_task)
        if finalize_resources is not None and not resources_finalized:
            await finalize_resources(False)
