"""Fence tool side effects to the current Agent Run owner."""

from __future__ import annotations

from langchain.agents.middleware import wrap_tool_call


def create_tool_execution_fence_middleware(*, run_id: str, owner_token: str, inbox_service):
    """Reject a tool call before execution when this Worker lost ownership."""

    @wrap_tool_call(name="ToolExecutionFenceMiddleware")
    async def tool_execution_fence(request, handler):
        await inbox_service.assert_run_owner(run_id, owner_token)
        return await handler(request)

    return tool_execution_fence
