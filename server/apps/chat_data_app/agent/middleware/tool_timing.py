"""Record per-tool-call start/end timestamps for observability."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest, wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from utils.logger import get_logger

logger = get_logger(__name__)


def create_tool_timing_middleware() -> AgentMiddleware:
    """创建工具执行起止时间 middleware。

    位于租约围栏内侧、其余工具 wrapper 外侧（覆盖 ToolErrorMiddleware 与结果
    卸载），成功与失败的 ToolMessage 都盖上 additional_kwargs 的
    started_at_ms / ended_at_ms（epoch 毫秒），随 checkpoint 持久化；
    耗时由展示端相减得出。
    """

    @wrap_tool_call(name="ToolTimingMiddleware")
    async def tool_timing_middleware(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        started_at_ms = int(time.time() * 1000)
        result = await handler(request)
        ended_at_ms = int(time.time() * 1000)
        message = _extract_tool_message(result)
        if message is not None:
            additional_kwargs = dict(getattr(message, "additional_kwargs", None) or {})
            additional_kwargs["started_at_ms"] = started_at_ms
            additional_kwargs["ended_at_ms"] = ended_at_ms
            try:
                message.additional_kwargs = additional_kwargs
            except Exception:
                logger.warning(
                    "工具执行起止时间写入消息失败: started_at_ms=%s ended_at_ms=%s",
                    started_at_ms,
                    ended_at_ms,
                    exc_info=True,
                )
        logger.info(
            "[tools] 工具执行完成: tool_name=%s tool_call_id=%s status=%s started_at_ms=%s ended_at_ms=%s duration_ms=%s",
            getattr(message, "name", None) or _request_tool_name(request),
            getattr(message, "tool_call_id", None) or _request_tool_call_id(request),
            getattr(message, "status", None),
            started_at_ms,
            ended_at_ms,
            ended_at_ms - started_at_ms,
        )
        return result

    return tool_timing_middleware


def _extract_tool_message(result: Any) -> ToolMessage | None:
    """从工具返回值里取需要盖时间戳的 ToolMessage。

    多数工具直接返回 ToolMessage；``task``（子智能体）等工具返回 ``Command``
    通过 ``update.messages`` 注入结果消息，若只看 ToolMessage 会漏掉这些工具，
    导致前端/历史回放没有总耗时。
    """
    if isinstance(result, ToolMessage):
        return result
    if isinstance(result, Command):
        update = getattr(result, "update", None)
        messages = update.get("messages") if isinstance(update, dict) else None
        for message in messages or []:
            if isinstance(message, ToolMessage):
                return message
    return None


def _request_tool_name(request: ToolCallRequest) -> str:
    tool_call = getattr(request, "tool_call", None)
    return str(tool_call.get("name") or "") if isinstance(tool_call, dict) else ""


def _request_tool_call_id(request: ToolCallRequest) -> str:
    tool_call = getattr(request, "tool_call", None)
    return str(tool_call.get("id") or "") if isinstance(tool_call, dict) else ""
