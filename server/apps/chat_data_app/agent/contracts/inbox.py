"""Steering 所需的 inbox 能力契约。"""

from __future__ import annotations

from typing import Protocol

from langchain_core.messages import HumanMessage


class ClaimedInboxMessages(Protocol):
    """一次 inbox 领取结果：调用方只需要 messages。"""

    messages: list[HumanMessage]


class InboxPort(Protocol):
    """运行期追加消息的领取端口；具体实现由 run 层提供。"""

    async def claim_pending_messages(
        self,
        run_id: str,
        *,
        queue_type: str | None = "next-step",
        owner_token: str | None = None,
    ) -> ClaimedInboxMessages: ...


__all__ = ["ClaimedInboxMessages", "InboxPort"]
