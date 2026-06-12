from __future__ import annotations

import json
import os

from utils.storage_paths import COMPLAINTS_BASE_DIR
from apps.complaint_app.history_storage import load_chat_events

_CHAT_LOG_FILENAME = "conversation.jsonl"
_STRUCTURED_PREFIX = "【上传文件名称】"


def _unwrap_structured_query(content: str | None) -> str:
    """If content is the structured attachment prompt, return the original user question."""
    if not content or _STRUCTURED_PREFIX not in content:
        return content or ""
    parts = content.split("【原始提问】", 1)
    if len(parts) != 2:
        return content or ""
    tail = parts[1]
    return tail.strip() or content


def fetch_chat_messages(
    *,
    user_id: str,
    base_id: str,
    conversation_id: str,
    first_id: str,
    limit: int,
    latest_conversation_fetcher,
) -> tuple[dict, int]:
    if not base_id:
        return {"error": "base_id 不能为空"}, 400

    resolved_conversation_id = conversation_id or (latest_conversation_fetcher(user_id, base_id) or "")
    if not resolved_conversation_id:
        return {
            "conversation_id": "",
            "messages": [],
            "has_more": False,
            "first_id": None,
        }, 200

    log_path = os.path.join(
        COMPLAINTS_BASE_DIR,
        user_id,
        base_id,
        "chats",
        resolved_conversation_id,
        _CHAT_LOG_FILENAME,
    )
    if os.path.isfile(log_path):
        messages: list[dict] = []
        try:
            with open(log_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        payload = json.loads(line)
                        messages.append(payload)
                    except Exception:
                        continue
        except Exception:
            messages = []
        if messages:
            trimmed = messages[-limit:] if limit > 0 else messages
            normalized = []
            for entry in trimmed:
                role = entry.get("role") or ""
                if role not in {"user", "assistant"}:
                    continue
                content = entry.get("question") if role == "user" else entry.get("answer")
                normalized.append({
                    "role": role,
                    "query": content if role == "user" else None,
                    "answer": content if role == "assistant" else None,
                    "id": entry.get("ts") or entry.get("id"),
                    "created_at": entry.get("ts"),
                    "attachments": entry.get("attachments") or [],
                    "reference_blocks": entry.get("reference_blocks") or [],
                    "metadata": entry.get("metadata") or {},
                    "snapshot": entry.get("snapshot") or entry.get("chat_message") or {},
                })
            return {
                "conversation_id": resolved_conversation_id,
                "messages": normalized,
                "has_more": len(messages) > len(normalized),
                "first_id": None,
            }, 200

    events = load_chat_events(user_id, base_id, resolved_conversation_id, limit=None)
    if events:
        messages: list[dict] = []
        for item in events:
            if not isinstance(item, dict):
                continue
            chat_msg = item.get("chat_message")
            if not isinstance(chat_msg, dict):
                continue
            role = chat_msg.get("role")
            content = chat_msg.get("content") or ""
            messages.append({
                "role": "assistant" if role == "assistant" else "user",
                "query": content if role == "user" else None,
                "answer": content if role == "assistant" else None,
                "id": chat_msg.get("id") or chat_msg.get("ts"),
                "created_at": chat_msg.get("ts"),
                "attachments": chat_msg.get("attachments") or [],
                "chat_message": chat_msg,
            })
        limited = messages[-limit:] if limit > 0 else messages
        return {
            "conversation_id": resolved_conversation_id,
            "messages": limited,
            "has_more": len(messages) > len(limited),
            "first_id": None,
        }, 200

    return {"conversation_id": resolved_conversation_id, "messages": [], "has_more": False, "first_id": None}, 200
