"""Persist completed model snapshots for refresh and history recovery."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentState, Runtime, after_model

from utils.logger import get_logger

logger = get_logger(__name__)


def create_after_model_history_middleware(
    persist_snapshot: Callable[[dict[str, Any]], Awaitable[None]],
    *,
    state_schema: Any | None = None,
) -> Callable[[AgentState, Runtime], None]:
    """Persist a durable conversation snapshot after every model call.

    The callback is deliberately best-effort. History persistence must not
    turn a successful model/tool run into a failed run when the event store is
    temporarily unavailable; the final turn write remains the authoritative
    retry point.
    """

    @after_model(name="AfterModelHistoryMiddleware", state_schema=state_schema)
    async def persist_after_model(state: AgentState, _runtime: Runtime) -> None:
        try:
            await persist_snapshot(dict(state or {}))
        except Exception:
            logger.warning("模型调用完成后的会话历史快照写入失败", exc_info=True)
        return None

    return persist_after_model
