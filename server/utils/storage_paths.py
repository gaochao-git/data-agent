"""Shared filesystem paths derived from CHAT_DATA_HOME settings."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote


def _resolve_chat_data_home() -> Path:
    # chat 会话数据根目录必须显式指定，禁止回退到其它变量推导。
    # 作为 chat 应用唯一的数据根，local / sandbox 模式共用同一份显式根。未设置直接 fail-fast。
    value = os.getenv("CHAT_DATA_HOME")
    if not value:
        raise RuntimeError("CHAT_DATA_HOME is required: chat 会话数据根目录必须显式指定，禁止回退到其它变量推导")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeError("CHAT_DATA_HOME 必须是绝对路径")
    return path.resolve()


CHAT_DATA_HOME = str(_resolve_chat_data_home())


def _resolve_chat_data_cache_home() -> Path:
    """本地派生缓存根；未配置时放在共享根同级的 ``<name>-cache``。"""
    value = os.getenv("CHAT_DATA_CACHE_HOME")
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise RuntimeError("CHAT_DATA_CACHE_HOME 必须是绝对路径")
        return path.resolve()
    shared = Path(CHAT_DATA_HOME)
    return (shared.parent / f"{shared.name}-cache").resolve()


CHAT_DATA_CACHE_HOME = str(_resolve_chat_data_cache_home())

THREADS_DIR = Path(CHAT_DATA_HOME) / "threads"
CACHE_THREADS_DIR = Path(CHAT_DATA_CACHE_HOME) / "threads"
CACHE_CONTEXT_DIR = Path(CHAT_DATA_CACHE_HOME) / "context"

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
    # 正则只能校验形状（2026-02-31 也会通过），这里再做一次真实日历校验，避免
    # 生成语义上不存在的日期目录。
    datetime.strptime(created_date, "%Y-%m-%d")
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


def conversation_cache_dir(user_id: str, conversation_id: str, *, created_date: str | None = None) -> Path:
    """会话派生缓存目录：``<CHAT_DATA_CACHE_HOME>/threads/<uid>/<date>/<cid>``。"""
    return (
        CACHE_THREADS_DIR
        / normalize_user_id(user_id or "system")
        / normalize_created_date(created_date)
        / normalize_conversation_id(conversation_id)
    )


def conversation_cache_dir_from_workspace(workspace_root: str) -> Path:
    """从共享工作区根定位同会话的本地缓存目录。"""
    workspace = Path(str(workspace_root or "")).expanduser().resolve()
    thread_dir = workspace.parent
    try:
        relative = thread_dir.relative_to(THREADS_DIR.resolve())
    except ValueError:
        parts = thread_dir.parts
        indexes = [index for index, part in enumerate(parts) if part == "threads"]
        if indexes:
            relative = Path(*parts[indexes[-1] + 1 :])
        else:
            digest = hashlib.sha1(str(thread_dir).encode("utf-8")).hexdigest()  # noqa: S324 - 仅用于路径去重
            relative = Path("adhoc") / digest
    return (CACHE_THREADS_DIR / relative).resolve()


def conversation_semantic_dir(user_id: str, conversation_id: str, *, created_date: str | None = None) -> Path:
    """会话语义模型缓存目录（派生数据，不进入共享会话目录）。"""
    return (
        CACHE_CONTEXT_DIR
        / "semantic"
        / normalize_user_id(user_id or "system")
        / normalize_created_date(created_date)
        / normalize_conversation_id(conversation_id)
    )


def conversation_semantic_dir_from_workspace(workspace_root: str) -> Path:
    """从共享工作区根定位同会话的语义模型缓存目录。"""
    cached_thread = conversation_cache_dir_from_workspace(workspace_root)
    relative = cached_thread.relative_to(CACHE_THREADS_DIR)
    return (CACHE_CONTEXT_DIR / "semantic" / relative).resolve()


def assert_workspace_root(
    workspace_root: str,
    *,
    chat_data_home: str | None = None,
    require_user_data_name: bool = False,
) -> Path:
    """校验 workspace root 属于 ChatData 共享数据目录。

    会话工作区、sandbox 同步和本地后端各自需要同一套边界；集中在这里，避免
    “某个入口只检查了一半”的漂移。``require_user_data_name`` 供 sandbox 使用：
    工作区根必须正好是 ``.../<uid>/<date>/<cid>/user-data``。
    """
    root = Path(chat_data_home or CHAT_DATA_HOME).expanduser().resolve()
    target = Path(str(workspace_root or "")).expanduser().resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"会话工作区不在 CHAT_DATA_HOME 内: {workspace_root}")
    if require_user_data_name and target.name != "user-data":
        raise ValueError(f"会话工作区必须是 user-data 目录: {workspace_root}")
    return target


__all__ = [
    "CHAT_DATA_CACHE_HOME",
    "CHAT_DATA_HOME",
    "assert_workspace_root",
    "CACHE_CONTEXT_DIR",
    "CACHE_THREADS_DIR",
    "THREADS_DIR",
    "conversation_cache_dir",
    "conversation_cache_dir_from_workspace",
    "conversation_semantic_dir",
    "conversation_semantic_dir_from_workspace",
    "conversation_thread_dir",
    "normalize_conversation_id",
    "normalize_created_date",
    "normalize_user_id",
]
