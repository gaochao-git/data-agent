"""Shared filesystem paths derived from CHAT_DATA_HOME settings."""

from __future__ import annotations

import json
import os
import re
import uuid
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


def user_runtime_dir(user_id: str) -> Path:
    """用户级运行依赖目录：``THREADS_DIR/<uid>/runtime``。"""
    return THREADS_DIR / normalize_user_id(user_id or "system") / "runtime"


def _workspace_relative_parts(workspace_root: str) -> tuple[str, str, str]:
    """从 ``.../threads/<uid>/<date>/<cid>/user-data`` 推导 uid/date/cid。"""
    workspace = Path(str(workspace_root or "")).expanduser().resolve()
    thread_dir = workspace.parent
    try:
        relative = thread_dir.relative_to(THREADS_DIR.resolve())
    except ValueError:
        # 兼容测试/临时工作区：从路径中的 threads 段推导，推导失败再用稳定占位身份。
        parts = thread_dir.parts
        indexes = [index for index, part in enumerate(parts) if part == "threads"]
        if not indexes:
            return "system", date.today().strftime("%Y-%m-%d"), thread_dir.name or "adhoc"
        tail = parts[indexes[-1] + 1 :]
        if len(tail) >= 3:
            return tail[0], tail[1], tail[2]
        if len(tail) == 2:
            return tail[0], date.today().strftime("%Y-%m-%d"), tail[1]
        return "system", date.today().strftime("%Y-%m-%d"), thread_dir.name or "adhoc"
    parts = relative.parts
    if len(parts) < 3:
        return parts[0] if parts else "system", date.today().strftime("%Y-%m-%d"), thread_dir.name or "adhoc"
    return parts[0], parts[1], parts[2]


def conversation_semantic_ref_path(workspace_root: str) -> Path:
    """会话与数据源组的绑定文件：会话目录下的 ``semantic-ref.json``。"""
    workspace = Path(str(workspace_root or "")).expanduser().resolve()
    return workspace.parent / "semantic-ref.json"


def _legacy_semantic_ref_path(workspace_root: str) -> Path:
    """兼容上一版放在用户运行目录 ``semantic/.refs`` 下的 ref。"""
    user_id, created_date, conversation_id = _workspace_relative_parts(workspace_root)
    return (
        user_runtime_dir(user_id)
        / "semantic"
        / ".refs"
        / created_date
        / f"{normalize_conversation_id(conversation_id)}.json"
    )


def _read_semantic_ref_file(path: Path) -> dict[str, str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    datasource_id = str(data.get("datasource_id") or "").strip()
    if not datasource_id:
        return None
    values = {"datasource_id": datasource_id}
    user_id = str(data.get("user_id") or "").strip()
    if user_id:
        values["user_id"] = user_id
    return values


def read_semantic_cache_ref(workspace_root: str) -> dict[str, str] | None:
    """读取会话语义绑定：新会话目录优先，兼容旧 ``.refs``。"""
    ref = _read_semantic_ref_file(conversation_semantic_ref_path(workspace_root))
    if ref is not None:
        return ref
    return _read_semantic_ref_file(_legacy_semantic_ref_path(workspace_root))


def write_semantic_cache_ref(
    workspace_root: str,
    *,
    datasource_id: str,
) -> Path:
    """原子写入会话语义缓存引用，供同会话后续读取。"""
    path = conversation_semantic_ref_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"datasource_id": datasource_id}, ensure_ascii=False, sort_keys=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _workspace_user_id(workspace_root: str) -> str:
    return _workspace_relative_parts(workspace_root)[0]


def workspace_user_id(workspace_root: str) -> str:
    """返回工作区所属用户的规范化 ID。"""
    return _workspace_user_id(workspace_root)


def conversation_semantic_dir_from_workspace(workspace_root: str) -> Path:
    """定位会话当前语义缓存；ref 是唯一真值，无 ref 返回不存在占位目录。"""
    ref = read_semantic_cache_ref(workspace_root)
    user_id, created_date, conversation_id = _workspace_relative_parts(workspace_root)
    if ref is None:
        return (
            user_runtime_dir(user_id)
            / "semantic"
            / ".no-ref"
            / created_date
            / normalize_conversation_id(conversation_id)
        ).resolve()
    return semantic_cache_dir(user_id, ref["datasource_id"]).resolve()


def runtime_skills_cache_dir(user_id: str) -> Path:
    """用户运行技能快照目录：``threads/<uid>/runtime/skills``。"""
    return user_runtime_dir(user_id) / "skills"


def semantic_cache_dir(user_id: str, datasource_id: str) -> Path:
    """用户语义缓存目录：``threads/<uid>/runtime/semantic/<datasource>``。"""
    datasource_segment = _normalize_segment(datasource_id, replace_invalid=True)
    return user_runtime_dir(user_id) / "semantic" / datasource_segment


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
    "CHAT_DATA_HOME",
    "assert_workspace_root",
    "THREADS_DIR",
    "user_runtime_dir",
    "workspace_user_id",
    "conversation_semantic_dir_from_workspace",
    "conversation_semantic_ref_path",
    "read_semantic_cache_ref",
    "semantic_cache_dir",
    "write_semantic_cache_ref",
    "conversation_thread_dir",
    "normalize_conversation_id",
    "runtime_skills_cache_dir",
    "normalize_created_date",
    "normalize_user_id",
]
