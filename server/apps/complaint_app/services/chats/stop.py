"""Local stop handler for chat streaming."""

from __future__ import annotations

from .state import cancel_conversation


def stop_chat(
    *,
    conversation_id: str,
    task_id: str | None = None,
) -> tuple[dict, int]:
    if not conversation_id:
        return {"error": "conversation_id 不能为空"}, 400

    cancelled = cancel_conversation(conversation_id)
    if not cancelled:
        return {"message": "对话已结束或不存在，无需停止"}, 200

    return {"message": "对话已停止"}, 200

