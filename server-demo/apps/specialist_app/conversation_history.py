"""Shared helpers for persisting and reading AI chat conversation history."""

from __future__ import annotations

import atexit
import json
import os
import uuid
import asyncio
import anyio
from typing import Any, Optional

try:
    from server.utils.storage_paths import CASE_LIBRARY_BASE_DIR
except ModuleNotFoundError:
    from utils.storage_paths import CASE_LIBRARY_BASE_DIR

from .utils import utc_now_iso

HISTORY_FILE = os.path.join(CASE_LIBRARY_BASE_DIR, "history.json")
USER_CONVERSATION_INDEX_KEY = "__user_conversations__"
_VISIBLE_ROLES = {"user", "assistant"}


def _ensure_history_dir() -> None:
    history_dir = os.path.dirname(HISTORY_FILE)
    if history_dir:
        os.makedirs(history_dir, exist_ok=True)


def _is_displayable_message(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    role = entry.get("role")
    if role not in _VISIBLE_ROLES:
        return False
    display_meta = entry.get("display")
    if isinstance(display_meta, dict) and display_meta.get("visible") is False:
        return False
    return True


def _extract_display_content(entry: dict[str, Any]) -> str:
    display_meta = entry.get("display")
    if isinstance(display_meta, dict):
        display_content = display_meta.get("content")
        if isinstance(display_content, str):
            return display_content
    content = entry.get("content")
    return "" if content is None else str(content)


def _extract_thinking_steps(entry: dict[str, Any]) -> list[dict[str, Any]]:
    display_meta = entry.get("display")
    if not isinstance(display_meta, dict):
        return []
    raw_thinking = display_meta.get("thinking")
    if not isinstance(raw_thinking, list):
        return []
    sanitized: list[dict[str, Any]] = []
    for step in raw_thinking:
        if isinstance(step, dict):
            event_name = str(step.get("event") or "").strip()
            # 对 node_* 事件保留完整字段，方便前端重建树结构
            if event_name.startswith("node_"):
                sanitized.append(step)
                continue
            desc = step.get("desc")
            if not desc:
                continue
            record: dict[str, Any] = {"desc": str(desc)}
            for key in ("event", "tool_call_id", "status", "created"):
                value = step.get(key)
                if value is not None:
                    record[key] = value
            sanitized.append(record)
        elif isinstance(step, str) and step.strip():
            sanitized.append({"desc": step.strip()})
    return sanitized


_ensure_history_dir()


def _load_history() -> dict[str, Any]:
    """从JSON文件加载历史记录"""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


match_history = _load_history()
match_history.setdefault(USER_CONVERSATION_INDEX_KEY, {})


def load_history(
    conversation_id: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """
    Return the entire in-memory history store, or the message list for a given
    conversation when an id is provided. Always ensures the bucket exists.
    """
    match_history.setdefault(USER_CONVERSATION_INDEX_KEY, {})
    if conversation_id:
        bucket = match_history.get(conversation_id)
        if not isinstance(bucket, list):
            bucket = []
            match_history[conversation_id] = bucket
        return bucket
    return match_history


def save_history() -> None:
    """将历史记录保存到JSON文件"""
    _ensure_history_dir()
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(match_history, f, ensure_ascii=False, indent=2)
    print(f"✅ Conversation history saved to {HISTORY_FILE}")


atexit.register(save_history)


def get_user_conversations(user_id: str) -> list[dict[str, Any]]:
    """根据用户 ID 返回按最近使用时间倒序排列的对话列表"""
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return []

    user_index = match_history.get(USER_CONVERSATION_INDEX_KEY, {})
    user_conversations = user_index.get(normalized_user_id)
    if not isinstance(user_conversations, dict):
        return []

    conversations: list[dict[str, Any]] = []
    for conversation_id, meta in user_conversations.items():
        created_at = ""
        last_used_at = ""
        title = ""
        if isinstance(meta, dict):
            created_at = str(meta.get("created_at") or "")
            last_used_at = str(meta.get("last_used_at") or created_at)
            title = str(meta.get("title") or "")
        elif isinstance(meta, str):
            created_at = meta
            last_used_at = meta

        resolved_title = title.strip() or "新会话"
        # 不再读取详细消息，按用户列表直接返回元数据；预览字段留空。
        message_count = 0
        latest_message_preview = ""
        first_message_preview = ""

        conversations.append(
            {
                "conversation_id": conversation_id,
                "created_at": created_at,
                "last_used_at": last_used_at or created_at,
                "title": resolved_title,
                "message_count": message_count,
                "latest_message_preview": latest_message_preview,
                "first_message_preview": first_message_preview,
            }
        )

    conversations.sort(key=lambda item: item.get("last_used_at") or "", reverse=True)
    return conversations

def resolve_user_id(current_user) -> str:
    """
    Extract a normalized user_id (falling back to `username`) from the current_user object.
    Returns empty string when unavailable.
    """
    if not current_user:
        return ""
    user_id = str(getattr(current_user, "user_id", "") or "").strip()
    if not user_id:
        user_id = str(getattr(current_user, "username", "") or "").strip()
    return user_id


def list_conversations_for_user(current_user) -> dict[str, Any]:
    """Return the normalized user_id plus recent conversation summaries."""
    user_id = resolve_user_id(current_user)
    conversations = get_user_conversations(user_id) if user_id else []
    return {
        "user_id": user_id,
        "conversations": conversations,
    }


def resolve_conversation_id(
    current_user,
    incoming_conversation_id: str,
) -> str:
    """
    Ensure a conversation is bound to the current user; create or reuse as needed.
    """
    conversation_id = str(incoming_conversation_id or "").strip()
    user_id = resolve_user_id(current_user)

    if not user_id:
        return conversation_id or f"conv_{uuid.uuid4().hex}"

    user_index = match_history.setdefault(USER_CONVERSATION_INDEX_KEY, {})
    user_conversations = user_index.setdefault(user_id, {})

    now_iso = utc_now_iso()

    if conversation_id:
        # 传入了有效的 conversation_id，直接使用（可能是统一接口创建的）
        existing_record = user_conversations.get(conversation_id)
        was_new = True
        if existing_record is not None:
            # 本地有记录，更新时间
            if isinstance(existing_record, dict):
                existing_record["last_used_at"] = now_iso
            else:
                user_conversations[conversation_id] = {
                    "created_at": existing_record,
                    "last_used_at": now_iso,
                }
        else:
            # 本地没有记录（可能是统一接口创建的），创建本地记录
            user_conversations[conversation_id] = {
                "created_at": now_iso,
                "last_used_at": now_iso,
            }
        save_history()
        # 同步更新 user_threads
        _schedule_sync_to_user_threads(user_id, conversation_id, None, is_new=was_new)
        return conversation_id

    # 没有传入 conversation_id，创建新会话
    conversation_id = f"conv_{uuid.uuid4().hex}"
    user_conversations[conversation_id] = {
        "created_at": now_iso,
        "last_used_at": now_iso,
    }
    save_history()
    # 同步新建到 user_threads
    _schedule_sync_to_user_threads(user_id, conversation_id, "新会话", is_new=True)
    return conversation_id


def _schedule_sync_to_user_threads(user_id: str, conversation_id: str, title: Optional[str], is_new: bool) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        loop.create_task(_sync_to_user_threads(user_id, conversation_id, title, is_new))
    else:
        try:
            anyio.from_thread.run(_sync_to_user_threads, user_id, conversation_id, title, is_new)
        except Exception:
            asyncio.run(_sync_to_user_threads(user_id, conversation_id, title, is_new))


async def _sync_to_user_threads(user_id: str, conversation_id: str, title: Optional[str], is_new: bool) -> None:
    """同步专家系统会话到统一的 user_threads 表"""
    from utils.lang_util.database import CHECKPOINTER_TYPE
    if CHECKPOINTER_TYPE == "mysql":
        from utils.lang_util.conversation import _async_sync_thread_to_db
        await _async_sync_thread_to_db(
            user_id=user_id,
            thread_id=conversation_id,
            agent_id="expert_agent",
            title=title,
            is_new=is_new
        )
    else:
        from utils.lang_util.conversation import sync_thread_to_db
        await asyncio.to_thread(
            sync_thread_to_db,
            user_id=user_id,
            thread_id=conversation_id,
            agent_id="expert_agent",
            title=title,
            is_new=is_new
        )


def update_conversation_title(user_id: str, conversation_id: str, title: str) -> None:
    """
    Update or create the conversation meta record with a new title for the given user.
    """
    normalized_user_id = str(user_id or "").strip()
    normalized_conversation_id = str(conversation_id or "").strip()
    normalized_title = str(title or "").strip()
    if not normalized_user_id or not normalized_conversation_id or not normalized_title:
        return

    user_index = match_history.setdefault(USER_CONVERSATION_INDEX_KEY, {})
    user_conversations = user_index.setdefault(normalized_user_id, {})

    meta = user_conversations.get(normalized_conversation_id)
    now_iso = utc_now_iso()
    if meta is None:
        user_conversations[normalized_conversation_id] = {
            "created_at": now_iso,
            "last_used_at": now_iso,
            "title": normalized_title,
        }
    elif isinstance(meta, dict):
        meta["title"] = normalized_title
    else:
        user_conversations[normalized_conversation_id] = {
            "created_at": meta,
            "last_used_at": meta,
            "title": normalized_title,
        }
    save_history()
    # 同步标题到 user_threads
    _schedule_sync_to_user_threads(normalized_user_id, normalized_conversation_id, normalized_title, is_new=False)


def get_conversation_detail(
    user_id: str,
    conversation_id: str,
) -> Optional[dict[str, Any]]:
    """
    返回指定用户下某个会话的完整消息列表。

    如果会话不存在或不属于该用户，则返回 None。
    """
    normalized_user_id = str(user_id or "").strip()
    normalized_conversation_id = str(conversation_id or "").strip()
    if not normalized_user_id or not normalized_conversation_id:
        return None

    user_index = match_history.get(USER_CONVERSATION_INDEX_KEY, {})
    user_conversations = user_index.get(normalized_user_id)
    if not isinstance(user_conversations, dict):
        return None

    meta = user_conversations.get(normalized_conversation_id)
    if meta is None:
        return None

    created_at = ""
    last_used_at = ""
    if isinstance(meta, dict):
        created_at = str(meta.get("created_at") or "")
        last_used_at = str(meta.get("last_used_at") or created_at)
    elif isinstance(meta, str):
        created_at = meta
        last_used_at = meta

    raw_messages = match_history.get(normalized_conversation_id)
    messages: list[dict[str, Any]] = []
    if isinstance(raw_messages, list):
        for index, entry in enumerate(raw_messages):
            if not _is_displayable_message(entry):
                continue
            role = entry.get("role")
            visible_content = _extract_display_content(entry)
            if not visible_content and role != "assistant":
                continue
            message_id = entry.get("id")
            if not isinstance(message_id, str) or not message_id.strip():
                message_id = f"{normalized_conversation_id}-{index}"
            thinking_steps = _extract_thinking_steps(entry)
            message_payload = {
                "id": message_id,
                "role": role,
                "content": visible_content,
                "timestamp": str(entry.get("timestamp") or ""),
            }
            references = entry.get("references")
            if isinstance(references, list) and references:
                message_payload["references"] = references
            if thinking_steps:
                message_payload["thinking"] = thinking_steps
            messages.append(message_payload)

    return {
        "conversation_id": normalized_conversation_id,
        "created_at": created_at,
        "last_used_at": last_used_at or created_at,
        "message_count": len(messages),
        "messages": messages,
    }

def get_conversation_detail_for_user(current_user, conversation_id: str):
    """Convenience wrapper using a user object instead of explicit user_id."""
    user_id = resolve_user_id(current_user)
    if not user_id:
        return None
    return get_conversation_detail(user_id, conversation_id)


__all__ = [
    "HISTORY_FILE",
    "USER_CONVERSATION_INDEX_KEY",
    "get_conversation_detail",
    "get_conversation_detail_for_user",
    "list_conversations_for_user",
    "get_user_conversations",
    "resolve_conversation_id",
    "resolve_user_id",
    "update_conversation_title",
    "match_history",
    "load_history",
    "save_history",
]
