"""Fence tool side effects to the current Agent Run owner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest, wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.types import Command


def create_tool_execution_fence_middleware(
    *, run_id: str, owner_token: str, inbox_service: Any
) -> AgentMiddleware:
    """Reject a tool call before execution when this Worker lost ownership."""

    @wrap_tool_call(name="ToolExecutionFenceMiddleware")
    async def tool_execution_fence(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        await inbox_service.assert_run_owner(run_id, owner_token)
        return await handler(request)

    return tool_execution_fence
