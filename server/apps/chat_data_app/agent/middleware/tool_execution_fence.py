"""Fence tool side effects to the current Agent Run owner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest, wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ..contracts.subagent_task import SubagentTaskLeaseLostError, get_subagent_task_service


def create_tool_execution_fence_middleware(*, run_id: str, owner_token: str, inbox_service: Any) -> AgentMiddleware:
    """Reject a tool call before execution when this Worker lost ownership."""

    @wrap_tool_call(name="ToolExecutionFenceMiddleware")
    async def tool_execution_fence(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        await inbox_service.assert_run_owner(run_id, owner_token)
        return await handler(request)

    return tool_execution_fence


def create_subagent_execution_fence_middleware() -> AgentMiddleware:
    """Fence background subgraph tools to the current task execution attempt."""

    @wrap_tool_call(name="ToolExecutionFenceMiddleware")
    async def subagent_execution_fence(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        state = getattr(request, "state", None) or {}
        task_id = str(state.get("subagent_task_id") or "")
        owner_token = str(state.get("subagent_owner_token") or "")
        service = get_subagent_task_service()
        if not task_id or not owner_token or service is None:
            raise SubagentTaskLeaseLostError("子智能体任务执行身份缺失，禁止继续调用工具")
        await service.assert_task_owner(task_id, owner_token)
        return await handler(request)

    return subagent_execution_fence
