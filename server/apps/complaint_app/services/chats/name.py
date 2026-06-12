"""Conversation name resolution for chat (local fallback)."""

from __future__ import annotations

def fetch_conversation_name(
    *,
    conversation_id: str,
    name_override: str | None = None,
) -> tuple[dict, int]:
    if not conversation_id:
        return {"error": "conversation_id 不能为空"}, 400

    if name_override and name_override.strip():
        return {"name": name_override.strip()}, 200

    return {"name": f"会话-{conversation_id[:8]}"}, 200

