"""OpenSandbox SDK access, Files API primitives and command execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from utils.logger import get_logger

from .officecli_adapter import OFFICECLI_NO_AUTO_RESIDENT_ENV
from .workspace_sandbox_paths import (
    _SANDBOX_WRITABLE_FILE_MODE,
    _is_outputs_sandbox_path,
    _is_sandbox_descendant,
    _is_sandbox_file_not_found,
    _is_sandbox_not_directory,
    _sandbox_entry_type,
    sandbox_visible_path,
)

logger = get_logger(__name__)

_SANDBOX_COMMAND_UID = 65534
_SANDBOX_COMMAND_GID = 65534

MAX_OUTPUT_CHARS = 12000


@dataclass
class SandboxExecutionSession:
    sandbox: Any
    sandbox_id: str
    workspace_root: str
    mode: str = "sandbox"
    runtime_skills_dir: Path | None = None
    cache_key: str = ""
    application_manifest: dict[str, tuple[int, int, str]] | None = None
    uploads_manifest: dict[str, tuple[int, int, str]] | None = None
    text_line_counts: dict[str, tuple[int, str, int]] = field(default_factory=dict)
    pending_persistence: bool = False
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class SandboxExecutionSessionRef:
    def __init__(self) -> None:
        self.current: SandboxExecutionSession | None = None


def require_sandbox_session(session_ref: SandboxExecutionSessionRef | None) -> SandboxExecutionSession:
    session = session_ref.current if session_ref is not None else None
    if session is None or session.sandbox is None:
        raise ValueError("OpenSandbox 执行环境未准备")
    return session


def _import_opensandbox_sdk() -> tuple[Any, Any, Any, Any]:
    try:
        from opensandbox import Sandbox
        from opensandbox.config import ConnectionConfig
        from opensandbox.models.execd import RunCommandOpts
        from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule
    except ImportError as exc:
        raise ValueError(
            "OpenSandbox Python SDK 未安装或不支持 Files API / NetworkPolicy，请升级 `opensandbox`"
        ) from exc
    return Sandbox, ConnectionConfig, RunCommandOpts, (NetworkPolicy, NetworkRule)


def _import_opensandbox_filesystem_models() -> tuple[Any, Any, Any]:
    try:
        from opensandbox.models.filesystem import MoveEntry, SearchEntry, WriteEntry
    except ImportError as exc:
        raise ValueError("当前 OpenSandbox SDK 不支持 Files API models，请升级 `opensandbox`") from exc
    return MoveEntry, SearchEntry, WriteEntry


def _import_opensandbox_content_replace_entry() -> Any:
    try:
        from opensandbox.models.filesystem import ContentReplaceEntry
    except ImportError as exc:
        raise ValueError("当前 OpenSandbox SDK 不支持 files.replace_contents，请升级 `opensandbox`") from exc
    return ContentReplaceEntry


def _import_opensandbox_directory_list_entry() -> Any:
    try:
        from opensandbox.models.filesystem import DirectoryListEntry
    except ImportError as exc:
        raise ValueError("当前 OpenSandbox SDK 不支持 files.list_directory，请升级 `opensandbox`") from exc
    return DirectoryListEntry


async def _write_sandbox_file(
    *,
    sandbox: Any,
    path: str,
    data: bytes,
    mode: int = _SANDBOX_WRITABLE_FILE_MODE,
) -> None:
    files = getattr(sandbox, "files", None)
    writer = getattr(files, "write_file", None)
    if not callable(writer):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.write_file")
    await writer(path, data, mode=mode)
    logger.info(
        "OpenSandbox Files API 上传完成: sandbox_id=%s path=%s bytes=%s",
        str(getattr(sandbox, "id", "") or ""),
        path,
        len(data),
    )


async def _replace_sandbox_file_content(
    *,
    sandbox: Any,
    path: str,
    old_content: str,
    new_content: str,
) -> None:
    files = getattr(sandbox, "files", None)
    replacer = getattr(files, "replace_contents", None)
    if not callable(replacer):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.replace_contents")
    ContentReplaceEntry = _import_opensandbox_content_replace_entry()
    await replacer(
        [
            ContentReplaceEntry(
                path=path,
                old_content=old_content,
                new_content=new_content,
            )
        ]
    )
    logger.info(
        "OpenSandbox Files API 精确替换完成: sandbox_id=%s path=%s",
        str(getattr(sandbox, "id", "") or ""),
        path,
    )


async def _read_sandbox_file_bytes(*, sandbox: Any, path: str) -> bytes:
    files = getattr(sandbox, "files", None)
    reader = getattr(files, "read_bytes", None)
    if not callable(reader):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.read_bytes")
    data = bytes(await reader(path))
    logger.info(
        "OpenSandbox Files API 下载完成: sandbox_id=%s path=%s bytes=%s",
        str(getattr(sandbox, "id", "") or ""),
        path,
        len(data),
    )
    return data


async def _create_sandbox_directories(*, sandbox: Any, paths: list[str], mode: int) -> None:
    unique_paths = sorted({str(path).rstrip("/") for path in paths if str(path).strip()})
    if not unique_paths:
        return
    files = getattr(sandbox, "files", None)
    creator = getattr(files, "create_directories", None)
    if not callable(creator):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.create_directories")
    _, _, WriteEntry = _import_opensandbox_filesystem_models()
    await creator([WriteEntry(path=path, mode=mode) for path in unique_paths])


async def _list_sandbox_directory_entries(
    *,
    session: SandboxExecutionSession,
    path: str,
) -> list[Any]:
    files = getattr(session.sandbox, "files", None)
    lister = getattr(files, "list_directory", None)
    if not callable(lister):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.list_directory")
    DirectoryListEntry = _import_opensandbox_directory_list_entry()
    try:
        entries = await lister(DirectoryListEntry(path=path, depth=1))
    except Exception as exc:
        if _is_sandbox_file_not_found(exc):
            return []
        raise
    return [entry for entry in entries if _is_sandbox_descendant(str(getattr(entry, "path", "") or ""), path)]


async def _walk_sandbox_tree(
    *,
    session: SandboxExecutionSession,
    root: str,
) -> tuple[list[Any], list[Any]]:
    """Recursively enumerate remote regular files and directories via Files API."""
    pending = [root]
    visited: set[str] = set()
    files: dict[str, Any] = {}
    directories: dict[str, Any] = {}
    while pending:
        directory = pending.pop()
        if directory in visited:
            continue
        visited.add(directory)
        for entry in await _list_sandbox_directory_entries(session=session, path=directory):
            entry_path = str(getattr(entry, "path", "") or "")
            entry_type = _sandbox_entry_type(entry)
            if not entry_path or entry_type == "symlink":
                continue
            path_kind = await _sandbox_path_kind(
                session=session,
                path=entry_path,
                entry=entry,
            )
            if path_kind == "dir":
                directories[entry_path] = entry
                pending.append(entry_path)
            elif path_kind == "file":
                files[entry_path] = entry
    return (
        [files[path] for path in sorted(files)],
        [directories[path] for path in sorted(directories)],
    )


def _extract_command_output(
    execution: Any, command_status: Any | None, status_lookup_error: str = ""
) -> tuple[int, str, str]:
    stdout = "\n".join(
        str(getattr(item, "text", ""))
        for item in getattr(getattr(execution, "logs", None), "stdout", []) or []
        if str(getattr(item, "text", ""))
    )
    stderr_parts = [
        str(getattr(item, "text", ""))
        for item in getattr(getattr(execution, "logs", None), "stderr", []) or []
        if str(getattr(item, "text", ""))
    ]
    execution_error = getattr(execution, "error", None)
    if execution_error is not None:
        error_text = ": ".join(
            part
            for part in (
                str(getattr(execution_error, "name", "")).strip(),
                str(getattr(execution_error, "value", "")).strip(),
            )
            if part
        )
        if error_text:
            stderr_parts.append(error_text)
    elif status_lookup_error:
        stderr_parts.append(f"OpenSandbox 未能确认命令最终退出状态，按失败处理: {status_lookup_error}")
    exit_code = getattr(command_status, "exit_code", None)
    if exit_code is None:
        exit_code = 1 if (execution_error is not None or status_lookup_error) else 0
    return int(exit_code), stdout, "\n".join(stderr_parts)


async def run_command_in_sandbox(
    *,
    session: SandboxExecutionSession,
    command: str,
    timeout_seconds: int = 30,
) -> tuple[int, str, str]:
    """Run a controlled OpenSandbox Commands API operation."""
    _, _, RunCommandOpts, _ = _import_opensandbox_sdk()
    options: dict[str, Any] = {
        "timeout": timedelta(seconds=max(1, timeout_seconds)),
        "uid": _SANDBOX_COMMAND_UID,
        "gid": _SANDBOX_COMMAND_GID,
        # 禁用 officecli resident 自动拉起：常驻进程把写入延迟到 idle auto-flush，
        # 与「命令返回即同步/归因」的时序假设冲突（见 officecli_adapter 注释）。
        "envs": {"HOME": "/tmp", "LANG": "C.UTF-8", **OFFICECLI_NO_AUTO_RESIDENT_ENV},
    }
    execution = await session.sandbox.commands.run(command, opts=RunCommandOpts(**options))
    command_status = None
    status_lookup_error = ""
    execution_id = getattr(execution, "id", None)
    if execution_id:
        try:
            command_status = await session.sandbox.commands.get_command_status(execution_id)
        except Exception as exc:
            status_lookup_error = str(exc)
            logger.warning("OpenSandbox 获取 command status 失败: %s", exc, exc_info=True)
    return _extract_command_output(execution, command_status, status_lookup_error)


async def _resolve_sandbox_real_path(
    *,
    session: SandboxExecutionSession,
    file_path: str,
    writable: bool = False,
    outputs_only: bool = False,
    allow_tool_result_write: bool = False,
) -> str:
    """Apply the model-visible virtual-path policy before calling the Files API.

    The Files API receives only lexical paths rooted in the current sandbox
    snapshot. It must not depend on Commands API just to resolve a path: when
    Bash is disabled no model-visible filesystem operation may spawn a shell.
    """
    target = sandbox_visible_path(
        file_path,
        writable=writable and not allow_tool_result_write,
    )
    if outputs_only and not _is_outputs_sandbox_path(target):
        raise ValueError("只允许呈现 outputs/ 下的产物")
    return target


async def _sandbox_path_info(*, session: SandboxExecutionSession, path: str) -> Any | None:
    files = getattr(session.sandbox, "files", None)
    getter = getattr(files, "get_file_info", None)
    if not callable(getter):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.get_file_info")
    try:
        entries = await getter([path])
    except Exception as exc:
        if _is_sandbox_file_not_found(exc):
            return None
        raise
    if not isinstance(entries, dict):
        return None
    entry = entries.get(path)
    if entry is not None:
        return entry
    return next(
        (item for item in entries.values() if str(getattr(item, "path", "")) == path),
        None,
    )


async def _sandbox_path_kind(
    *,
    session: SandboxExecutionSession,
    path: str,
    entry: Any | None = None,
) -> Literal["file", "dir"]:
    """Resolve a Files API path kind when execd omits optional ``type`` metadata."""
    resolved_entry = entry if entry is not None else await _sandbox_path_info(session=session, path=path)
    if resolved_entry is None:
        raise FileNotFoundError(f"路径不存在: {path}")
    entry_type = _sandbox_entry_type(resolved_entry)
    if entry_type == "symlink":
        raise ValueError("不允许操作符号链接")
    if entry_type == "directory":
        return "dir"
    if entry_type == "file":
        return "file"

    # Older/current execd responses can omit FileInfo.type. A depth-0 list is
    # the only Files API operation that distinguishes directories from files.
    files = getattr(session.sandbox, "files", None)
    lister = getattr(files, "list_directory", None)
    if not callable(lister):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.list_directory")
    DirectoryListEntry = _import_opensandbox_directory_list_entry()
    try:
        await lister(DirectoryListEntry(path=path, depth=0))
    except Exception as exc:
        if _is_sandbox_not_directory(exc):
            return "file"
        raise
    return "dir"


def truncate_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n...[truncated]", True
