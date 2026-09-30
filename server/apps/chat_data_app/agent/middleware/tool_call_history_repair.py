"""Repair interrupted tool-call tails before the next model request."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentState, Runtime, before_model
from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from utils.logger import get_logger

from .message_integrity import repair_tool_call_messages

logger = get_logger(__name__)


def create_tool_call_history_repair_middleware(
    *, state_schema: Any | None = None
) -> Callable[[AgentState, Runtime], dict[str, Any] | None]:
    """Create a state middleware that removes incomplete persisted tool groups."""

    @before_model(name="ToolCallHistoryRepairMiddleware", state_schema=state_schema)
    async def repair_tool_call_history(state: AgentState, _runtime: Runtime) -> dict[str, Any] | None:
        messages = list((state or {}).get("messages") or [])
        repaired_messages, report = repair_tool_call_messages(messages)
        if not report["repaired"]:
            return None
        conversation_id = str((state or {}).get("conversation_id") or "").strip()
        logger.warning(
            "已清理未闭合工具调用历史: conversation_id=%s removed_call_ids=%s removed_message_count=%s",
            conversation_id,
            list(report["removed_call_ids"]),
            report["removed_message_count"],
        )
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired_messages]}

    return repair_tool_call_history
