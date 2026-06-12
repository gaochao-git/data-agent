"""Helper utilities for persisting complaint history artifacts."""
from __future__ import annotations

import contextlib
import asyncio
import fcntl
# import winfcntl as fcntl
import json
import os
import shutil
from datetime import datetime
import time
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
from pathlib import Path
from typing import Any, Iterable

from utils.storage_paths import (
    COMPLAINTS_BASE_DIR,
    get_complaints_real_path,
    get_user_dir,
)

_ANALYSIS_DIRNAME = "analysis"
_UPLOAD_STREAM_FILENAME = "upload_stream.jsonl"
_MANIFEST_FILENAME = "manifest.json"
_CHAT_LOG_FILENAME = "conversation.jsonl"
_CHAT_EVENTS_FILENAME = "dify_events.jsonl"
_CHAT_METADATA_FILENAME = "metadata.json"
_CHATS_OVERVIEW_FILENAME = "metadata.json"
_HISTORY_FILENAME = "history.json"


def _ensure_directory(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def _append_jsonl(path: str, payload: dict[str, Any]) -> None:
    try:
        _ensure_directory(os.path.dirname(path))
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def relative_to_complaints(path: str | None) -> str | None:
    if not path:
        return path
    try:
        resolved = Path(path).resolve()
        return str(resolved.relative_to(Path(COMPLAINTS_BASE_DIR).resolve()))
    except Exception:
        return path


def append_upload_stream_event(user_id: str, base_id: str, payload: dict[str, Any]) -> None:
    if not base_id or not isinstance(payload, dict):
        return
    try:
        stream_path = get_complaints_real_path(
            user_id, base_id, os.path.join(_ANALYSIS_DIRNAME, _UPLOAD_STREAM_FILENAME)
        )
        payload = {
            **payload,
            "ts": payload.get("ts") or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        _append_jsonl(stream_path, payload)
    except Exception:
        pass


def save_analysis_manifest(user_id: str, base_id: str, manifest: dict[str, Any]) -> None:
    if not base_id or not isinstance(manifest, dict):
        return
    try:
        manifest_path = get_complaints_real_path(user_id, base_id, _MANIFEST_FILENAME)
        _ensure_directory(os.path.dirname(manifest_path))
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass


def _conversation_dir(user_id: str, base_id: str, conversation_id: str) -> str | None:
    if not base_id or not conversation_id:
        return None
    try:
        return get_complaints_real_path(user_id, base_id, os.path.join("chats", conversation_id))
    except Exception:
        return None


def _chats_root_dir(user_id: str, base_id: str) -> str | None:
    if not base_id:
        return None
    try:
        return get_complaints_real_path(user_id, base_id, "chats")
    except Exception:
        return None


def _persist_json_atomically(path: str, payload: dict[str, Any]) -> None:
    temp_path = f"{path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
        os.replace(temp_path, path)
    except Exception:
        try:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


def _update_chats_overview_metadata(user_id: str, base_id: str, conversation_id: str, ts: str) -> None:
    root_dir = _chats_root_dir(user_id, base_id)
    if not root_dir or not conversation_id:
        return
    _ensure_directory(root_dir)
    overview_path = os.path.join(root_dir, _CHATS_OVERVIEW_FILENAME)
    overview: dict[str, Any] = {
        "base_id": base_id,
        "conversations": [],
        "conversation_count": 0,
    }
    try:
        if os.path.isfile(overview_path):
            with open(overview_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                overview.update({k: v for k, v in loaded.items() if k != "conversations"})
                if isinstance(loaded.get("conversations"), list):
                    overview["conversations"] = loaded["conversations"]
    except Exception:
        pass

    existing_entries = overview.get("conversations") or []
    conv_mapping: dict[str, dict[str, Any]] = {}
    for entry in existing_entries:
        if not isinstance(entry, dict):
            continue
        conv_id = str(entry.get("conversation_id") or "")
        if not conv_id:
            continue
        conv_mapping[conv_id] = entry
    conv_entry = conv_mapping.get(conversation_id) or {"conversation_id": conversation_id}
    conv_entry["last_updated_at"] = ts
    conv_mapping[conversation_id] = conv_entry
    sorted_entries = sorted(
        conv_mapping.values(),
        key=lambda item: str(item.get("last_updated_at") or ""),
        reverse=True,
    )

    overview["conversations"] = sorted_entries
    overview["conversation_count"] = len(sorted_entries)
    overview["updated_at"] = ts
    overview["base_id"] = base_id
    _persist_json_atomically(overview_path, overview)


def _rename_chats_overview_entry(user_id: str, base_id: str, old_id: str, new_id: str) -> None:
    if not old_id or not new_id or old_id == new_id:
        return
    root_dir = _chats_root_dir(user_id, base_id)
    if not root_dir:
        return
    overview_path = os.path.join(root_dir, _CHATS_OVERVIEW_FILENAME)
    if not os.path.isfile(overview_path):
        return
    try:
        with open(overview_path, "r", encoding="utf-8") as handle:
            overview = json.load(handle)
    except Exception:
        return
    if not isinstance(overview, dict):
        return
    conversations = overview.get("conversations")
    if not isinstance(conversations, list):
        return
    updated = False
    for entry in conversations:
        if not isinstance(entry, dict):
            continue
        if entry.get("conversation_id") == old_id:
            entry["conversation_id"] = new_id
            updated = True
            break
    if not updated:
        return
    overview["conversations"] = conversations
    overview["updated_at"] = overview.get("updated_at") or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _persist_json_atomically(overview_path, overview)


def load_chats_overview(user_id: str, base_id: str) -> dict[str, Any] | None:
    """读取举报件聊天总览 metadata.json。"""
    root_dir = _chats_root_dir(user_id, base_id)
    if not root_dir:
        return None
    overview_path = os.path.join(root_dir, _CHATS_OVERVIEW_FILENAME)
    if not os.path.isfile(overview_path):
        return None
    try:
        with open(overview_path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            return loaded
    except Exception:
        return None
    return None


def latest_conversation_id(user_id: str, base_id: str) -> str | None:
    """返回总览里最新的 conversation_id（按 conversations 顺序第一条）。"""
    overview = load_chats_overview(user_id, base_id) or {}
    conversations = overview.get("conversations")
    if not isinstance(conversations, list):
        return None
    for entry in conversations:
        if isinstance(entry, dict):
            conv_id = str(entry.get("conversation_id") or "").strip()
            if conv_id:
                return conv_id
    return None


def migrate_conversation_directory(user_id: str, base_id: str, old_id: str, new_id: str) -> None:
    if not base_id or not old_id or not new_id or old_id == new_id:
        return
    old_dir = _conversation_dir(user_id, base_id, old_id)
    new_dir = _conversation_dir(user_id, base_id, new_id)
    if not old_dir or not new_dir:
        return
    if not os.path.isdir(old_dir):
        return
    try:
        _ensure_directory(os.path.dirname(new_dir))
        if os.path.isdir(new_dir):
            shutil.rmtree(new_dir)
        shutil.move(old_dir, new_dir)
        _rename_chats_overview_entry(user_id, base_id, old_id, new_id)
    except Exception:
        pass


def _append_chat_log(user_id: str, base_id: str, conversation_id: str, entry: dict[str, Any]) -> None:
    conv_dir = _conversation_dir(user_id, base_id, conversation_id)
    if not conv_dir:
        return
    entry.setdefault("ts", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    entry.setdefault("conversation_id", conversation_id)
    _append_jsonl(os.path.join(conv_dir, _CHAT_LOG_FILENAME), entry)


def _update_chat_metadata(
    user_id: str,
    base_id: str,
    conversation_id: str,
    ts: str,
    *,
    attachments: Iterable[dict[str, Any]] | None = None,
) -> None:
    conv_dir = _conversation_dir(user_id, base_id, conversation_id)
    if not conv_dir:
        return
    metadata_path = os.path.join(conv_dir, _CHAT_METADATA_FILENAME)
    metadata: dict[str, Any] = {
        "conversation_id": conversation_id,
        "base_id": base_id,
    }
    try:
        if os.path.isfile(metadata_path):
            with open(metadata_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                metadata.update(loaded)
    except Exception:
        pass
    metadata.setdefault("created_at", ts)
    metadata["last_updated_at"] = ts
    metadata["messages_count"] = int(metadata.get("messages_count", 0)) + 1
    if attachments:
        stored = list(metadata.get("attachments") or [])
        stored.extend(list(attachments))
        metadata["attachments"] = stored
    try:
        with open(metadata_path, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass


def record_chat_user_message(
    user_id: str,
    base_id: str,
    conversation_id: str,
    question: str,
    *,
    structured_payload: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    reference_blocks: list[dict[str, Any]] | None = None,
) -> None:
    if not base_id or not conversation_id:
        return
    entry = {
        "role": "user",
        "question": question,
    }
    if structured_payload:
        entry["structured_payload"] = structured_payload
    if context:
        entry["context"] = context
    if attachments:
        entry["attachments"] = attachments
    if reference_blocks:
        entry["reference_blocks"] = reference_blocks
    _append_chat_log(user_id, base_id, conversation_id, entry)
    _update_chat_metadata(user_id, base_id, conversation_id, entry["ts"], attachments=attachments)
    _update_chats_overview_metadata(user_id, base_id, conversation_id, entry["ts"])


def record_chat_assistant_message(
    user_id: str,
    base_id: str,
    conversation_id: str,
    *,
    answer: str | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
    chat_message: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> None:
    """记录 AI 消息，支持携带完整 chat_message 或最终快照。

    - chat_message: 原始推送的单条消息（可选）
    - snapshot: 汇总后的段落结构（可选，推荐用于历史回看）
    """
    if not base_id or not conversation_id:
        return
    entry = {
        "role": "assistant",
    }
    if answer is not None:
        entry["answer"] = answer
    if error is not None:
        entry["error"] = error
    if metadata:
        entry["metadata"] = metadata
    if chat_message:
        entry["chat_message"] = chat_message
    if snapshot:
        entry["snapshot"] = snapshot
    _append_chat_log(user_id, base_id, conversation_id, entry)
    _update_chat_metadata(user_id, base_id, conversation_id, entry["ts"])
    _update_chats_overview_metadata(user_id, base_id, conversation_id, entry["ts"])


def append_chat_sse_event(user_id: str, base_id: str, conversation_id: str, payload: dict[str, Any]) -> None:
    if not base_id or not conversation_id or not isinstance(payload, dict):
        return
    conv_dir = _conversation_dir(user_id, base_id, conversation_id)
    if not conv_dir:
        return
    payload = {
        **payload,
        "ts": payload.get("ts") or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _append_jsonl(os.path.join(conv_dir, _CHAT_EVENTS_FILENAME), payload)


def load_chat_events(user_id: str, base_id: str, conversation_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """加载已落盘的 chat_message SSE 事件，按写入顺序返回，可选截断最近 N 条。"""
    if not base_id or not conversation_id:
        return []
    conv_dir = _conversation_dir(user_id, base_id, conversation_id)
    if not conv_dir:
        return []
    path = os.path.join(conv_dir, _CHAT_EVENTS_FILENAME)
    events: list[dict[str, Any]] = []
    if not os.path.isfile(path):
        return events
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        events.append(obj)
                except Exception:
                    continue
    except Exception:
        return []
    if limit is not None and limit > 0:
        return events[-limit:]
    return events


def _history_file_path(user_id: str) -> str:
    user_root = get_user_dir(user_id)
    _ensure_directory(user_root)
    return os.path.join(user_root, _HISTORY_FILENAME)


def _history_lock_path(user_id: str) -> str:
    return _history_file_path(user_id) + ".lock"


@contextlib.contextmanager
def _history_lock(user_id: str, *, exclusive: bool):
    lock_path = _history_lock_path(user_id)
    lock_dir = os.path.dirname(lock_path)
    _ensure_directory(lock_dir)
    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lock_handle, mode)
        try:
            yield
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)


def _read_history_entries_no_lock(path: str) -> list[dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    normalized_entries: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        normalized_entries.append(entry)
    return normalized_entries


def _normalize_history_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        normalized.append({
            "order": int(entry.get("order") or 0),
            "base_id": str(entry.get("base_id") or ""),
            "name": str(entry.get("name") or ""),
            "last_update_time": str(entry.get("last_update_time") or ""),
        })
    normalized.sort(key=lambda item: item["order"])
    return normalized


def _get_history_timezone():
    tz_name = os.getenv("TIMEZONE") or os.getenv("HISTORY_TIMEZONE") or "Asia/Shanghai"
    if ZoneInfo:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return None
    return None


def _current_history_timestamp() -> str:
    tz = _get_history_timezone()
    if tz is not None:
        return datetime.now(tz).strftime("%Y/%m/%d %H:%M:%S")
    return time.strftime("%Y/%m/%d %H:%M:%S", time.localtime())


def load_user_history(user_id: str) -> list[dict[str, Any]]:
    history_path = _history_file_path(user_id)
    with _history_lock(user_id, exclusive=False):
        entries = _read_history_entries_no_lock(history_path)
    return _normalize_history_entries(entries)


def _touch_user_history_sync(user_id: str, base_id: str, *, name: str | None = None) -> bool:
    history_path = _history_file_path(user_id)
    is_new_entry = False
    with _history_lock(user_id, exclusive=True):
        entries = _read_history_entries_no_lock(history_path)
        normalized_entries = {entry.get("base_id"): entry for entry in entries}
        timestamp = _current_history_timestamp()
        target_entry = normalized_entries.get(base_id)
        if target_entry:
            target_entry["last_update_time"] = timestamp
            if name:
                target_entry["name"] = name
        else:
            is_new_entry = True
            max_order = max((int(entry.get("order") or 0) for entry in entries), default=0)
            normalized_entries[base_id] = {
                "order": max_order + 1,
                "base_id": base_id,
                "name": name or base_id,
                "last_update_time": timestamp,
            }

        updated_entries = list(normalized_entries.values())
        sorted_entries = _normalize_history_entries(updated_entries)

        temp_path = f"{history_path}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as temp_handle:
                json.dump(sorted_entries, temp_handle, ensure_ascii=False, indent=2, default=str)
            os.replace(temp_path, history_path)
        except Exception:
            try:
                if os.path.isfile(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
    return is_new_entry


async def touch_user_history(user_id: str, base_id: str, *, name: str | None = None) -> None:
    is_new_entry = await asyncio.to_thread(_touch_user_history_sync, user_id, base_id, name=name)
    await _sync_to_user_threads(user_id, base_id, name, is_new_entry)


async def _sync_to_user_threads(user_id: str, base_id: str, name: str | None, is_new: bool) -> None:
    """同步投诉举报会话到统一的 user_threads 表"""
    from utils.lang_util.conversation import sync_thread_to_db
    await sync_thread_to_db(user_id=user_id, thread_id=base_id, agent_id="complaint_agent", title=name, is_new=is_new)


__all__ = [
    "append_chat_sse_event",
    "append_upload_stream_event",
    "load_chat_events",
    "load_user_history",
    "load_chats_overview",
    "latest_conversation_id",
    "migrate_conversation_directory",
    "record_chat_assistant_message",
    "record_chat_user_message",
    "relative_to_complaints",
    "save_analysis_manifest",
    "touch_user_history",
]
