"""在每次模型调用前注入运行期间追加的用户消息。"""

from __future__ import annotations

from langchain.agents.middleware import before_model
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer

from ...services.agent_run.agent_inbox_service import AgentInboxService


class SteeringMessageTracker:
    """记录本次 graph 调用已经注入、等待确认的消息。"""

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
    inbox_service: AgentInboxService,
    tracker: SteeringMessageTracker,
    state_schema=None,
):
    """创建共享 inbox 驱动的 ``before_model`` middleware。"""

    @before_model(name="SteeringMiddleware", state_schema=state_schema)
    async def inject_steering_messages(state, _runtime):
        messages = state.get("messages") or []
        if not messages or not isinstance(messages[-1], ToolMessage):
            return None
        claimed = await inbox_service.claim_pending_messages(run_id)
        if not claimed.messages:
            return None
        message_ids = [str(message.id or "") for message in claimed.messages]
        tracker.record(message_ids)
        get_stream_writer()({"type": "steer_consumed", "message_ids": message_ids})
        return {"messages": claimed.messages}

    return inject_steering_messages
