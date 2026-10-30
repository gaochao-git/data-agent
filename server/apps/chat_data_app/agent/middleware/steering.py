"""在每次模型调用前注入运行期间追加的用户消息。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, Runtime, before_model
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.config import get_stream_writer

from ..contracts.inbox import InboxPort


class SteeringMessageTracker:
    """记录本次 graph 调用已接纳、等待步骤成功后确认的消息。"""

    def __init__(self) -> None:
        self.message_ids: list[str] = []

    def record(self, message_ids: list[str]) -> None:
        known = set(self.message_ids)
        self.message_ids.extend(message_id for message_id in message_ids if message_id not in known)

    def drain(self) -> list[str]:
        message_ids = self.message_ids
        self.message_ids = []
        return message_ids


def create_steering_middleware(
    *,
    run_id: str,
    owner_token: str,
    inbox_service: InboxPort,
    admit_messages: Callable[[list[HumanMessage]], Awaitable[None]],
    tracker: SteeringMessageTracker,
    state_schema: Any | None = None,
) -> AgentMiddleware:
    """创建共享 inbox 驱动的 ``before_model`` middleware。

    只领取 ``next-step`` 类型的消息。领取后先持久化为 admitted 用户历史，
    再返回给 LangGraph；模型步骤成功后由外层将 claimed 状态确认成 consumed。
    """

    @before_model(name="SteeringMiddleware", state_schema=state_schema)
    async def inject_steering_messages(state: AgentState, _runtime: Runtime) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        # A next-step steer is an interruption at the tool boundary. A plain
        # model turn must finish first; otherwise a message sent while text is
        # streaming gets injected into the same decision and corrupts the
        # current assistant turn.
        if not messages or not isinstance(messages[-1], ToolMessage):
            return None
        claimed = await inbox_service.claim_pending_messages(
            run_id,
            queue_type="next-step",
            owner_token=owner_token,
        )
        if not claimed.messages:
            return None
        await admit_messages(claimed.messages)
        message_ids = [str(message.id or "") for message in claimed.messages]
        tracker.record(message_ids)
        get_stream_writer()({"type": "inbox_admitted", "message_ids": message_ids})
        return {"messages": claimed.messages}

    return inject_steering_messages
