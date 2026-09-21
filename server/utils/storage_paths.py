"""Shared filesystem paths derived from CHAT_DATA_HOME settings."""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

def _resolve_chat_data_home() -> Path:
    # chat 会话数据根目录必须显式指定，禁止回退到其它变量推导。
    # 作为 chat 应用唯一的数据根，local / sandbox 模式共用同一份显式根。未设置直接 fail-fast。
    value = os.getenv("CHAT_DATA_HOME")
    if not value:
        raise RuntimeError(
            "CHAT_DATA_HOME is required: chat 会话数据根目录必须显式指定，"
            "禁止回退到其它变量推导"
        )
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeError("CHAT_DATA_HOME 必须是绝对路径")
    return path.resolve()


CHAT_DATA_HOME = str(_resolve_chat_data_home())

THREADS_DIR = Path(CHAT_DATA_HOME) / "threads"

_CREATED_DATE_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

_SEGMENT_SAFE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _normalize_segment(
    value: str | None,
    *,
    replace_invalid: bool,
) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid path segment type")

    trimmed = value.strip()
    if not trimmed:
        raise ValueError("Path segment must not be empty")

    if replace_invalid:
        # Percent-encoding keeps one-to-one mapping for IDs such as ``a/b`` and
        # ``a_b``. Replacing with underscores would let different users share a
        # workspace directory.
        normalized = quote(trimmed, safe="._-")
    elif not _SEGMENT_SAFE_PATTERN.fullmatch(trimmed):
        raise ValueError("Path segment contains unsupported characters")
    else:
        normalized = trimmed

    if not normalized or normalized in {".", ".."} or normalized.startswith("."):
        raise ValueError("Path segment must contain valid characters")

    return normalized


def normalize_user_id(user_id: str) -> str:
    """Return a filesystem-safe representation of the authenticated user id."""
    return _normalize_segment(user_id, replace_invalid=True)


def normalize_conversation_id(conversation_id: str) -> str:
    """Return a collision-free filesystem path segment for a conversation ID."""
    return _normalize_segment(conversation_id, replace_invalid=True)


def normalize_created_date(created_date: str | None = None) -> str:
    """规整会话工作区日期分层：``None`` 取当前日期；非法格式抛 ``ValueError``。

    会话磁盘目录按会话创建日期（``YYYY-MM-DD``）归档，只按创建时间分层、
    不随后续活跃时间变化。
    """
    if created_date is None:
        return date.today().strftime("%Y-%m-%d")
    if not isinstance(created_date, str) or not _CREATED_DATE_PATTERN.fullmatch(created_date):
        raise ValueError(f"created_date 必须为 'YYYY-MM-DD' 格式: {created_date!r}")
    return created_date


def conversation_thread_dir(user_id: str, conversation_id: str, *, created_date: str | None = None) -> Path:
    """会话磁盘目录唯一拼装点：``<CHAT_DATA_HOME>/threads/<uid>/<yyyy-MM-DD>/<cid>``。

    所有会话磁盘路径（工作区、uploads、技能快照、工具结果）必须经由本函数拼装，
    禁止各自拼路径，避免同一会话分裂出多个日期目录。
    """
    return (
        THREADS_DIR
        / normalize_user_id(user_id or "system")
        / normalize_created_date(created_date)
        / normalize_conversation_id(conversation_id)
    )


__all__ = [
    "CHAT_DATA_HOME",
    "THREADS_DIR",
    "conversation_thread_dir",
    "normalize_conversation_id",
    "normalize_created_date",
    "normalize_user_id",
]
