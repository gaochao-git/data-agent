"""OpenSandbox workspace execution for ChatData conversations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import time
from typing import Any, Literal
from urllib.parse import unquote
from uuid import uuid4

from utils.crypto import decrypt_env_value
from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT
from utils.storage_paths import CHAT_DATA_HOME
from .artifact_registry import parse_output_artifact

logger = get_logger(__name__)

OPENSANDBOX_BACKEND = "opensandbox"
MAX_OUTPUT_CHARS = 12000
MAX_FILE_READ_LINE_CHARS = 500
_TEXT_GREP_MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_OFFICE_HTML_CHARS = 12 * 1024 * 1024
# OpenSandbox execd parses permission values as base-8 *strings*.  These must
# therefore be decimal integers whose digits are the intended Unix mode.
_SANDBOX_WRITABLE_FILE_MODE = 666
_SANDBOX_WRITABLE_DIRECTORY_MODE = 777
_SANDBOX_READONLY_FILE_MODE = 444
_SANDBOX_READONLY_DIRECTORY_MODE = 555


def _sandbox_root() -> str:
    # 沙箱 Files API 快照根固定为 /mnt（sandbox_visible_path / sandbox_workspace_dir
    # 等映射硬编码 /mnt/user-data），不对外暴露为可配置项，避免前缀不一致。
    return "/mnt"


def sandbox_thread_root() -> str:
    return f"{_sandbox_root()}/user-data"


def sandbox_workspace_dir() -> str:
    return f"{sandbox_thread_root()}/workspace"


def sandbox_outputs_dir() -> str:
    return f"{sandbox_thread_root()}/outputs"


def sandbox_uploads_dir() -> str:
    return f"{sandbox_thread_root()}/uploads"


def sandbox_skills_root() -> str:
    # 沙箱内技能快照根与 Agent 视角的虚拟根一致。
    return SKILLS_VIRTUAL_ROOT


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
    pending_persistence: bool = False
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


@dataclass
class CachedSandboxExecutionSession:
    session: SandboxExecutionSession
    expires_at: float


class SandboxExecutionSessionRef:
    def __init__(self) -> None:
        self.current: SandboxExecutionSession | None = None


_WORKSPACE_EXECUTION_SESSION_CACHE: dict[str, CachedSandboxExecutionSession] = {}
_WORKSPACE_EXECUTION_SESSION_LOCKS: dict[str, asyncio.Lock] = {}


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
        raise ValueError("OpenSandbox Python SDK 未安装或不支持 Files API / NetworkPolicy，请升级 `opensandbox`") from exc
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
        raise ValueError(
            "当前 OpenSandbox SDK 不支持 files.list_directory，请升级 `opensandbox`") from exc
    return DirectoryListEntry


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("OpenSandbox 配置不是合法整数: %s=%r，使用默认值 %s", name, raw, default)
        return default


def _str_to_bool(value: str | None, default: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _workspace_session_cache_key(
    workspace_root: str,
    mode: str = "sandbox",
    runtime_skills_dir: Path | None = None,
) -> str:
    skills = str(runtime_skills_dir or "")
    return f"{_application_workspace_root(workspace_root)}#{mode}#{skills}"


def _workspace_session_ttl_seconds() -> int:
    timeout_seconds = _env_int("OPENSANDBOX_TIMEOUT_SECONDS", 600)
    return max(30, min(timeout_seconds - 30, 300))


def _workspace_session_lock(cache_key: str) -> asyncio.Lock:
    lock = _WORKSPACE_EXECUTION_SESSION_LOCKS.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _WORKSPACE_EXECUTION_SESSION_LOCKS[cache_key] = lock
    return lock


def _network_default_action() -> str | None:
    raw = str(os.getenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION") or "deny").strip().lower()
    if raw in {"allow", "deny"}:
        return raw
    logger.warning("OpenSandbox 网络默认策略配置非法: OPENSANDBOX_NETWORK_DEFAULT_ACTION=%r，使用 deny", raw)
    return "deny"


def _network_allow_targets() -> list[str]:
    raw = str(os.getenv("OPENSANDBOX_NETWORK_ALLOW_TARGETS") or "")
    targets: list[str] = []
    seen: set[str] = set()
    for part in raw.replace("\n", ",").split(","):
        target = part.strip()
        if not target or target in seen:
            continue
        seen.add(target)
        targets.append(target)
    return targets


def _validate_workspace_backend_config() -> None:
    # 应用侧 CHAT_DATA_HOME 是唯一持久化根。它不需要暴露给 OpenSandbox 宿主机，
    # 但必须是绝对路径，以免运行期上传/下载落到随进程目录变化的位置。
    raw_home = str(os.getenv("CHAT_DATA_HOME") or "").strip()
    if not Path(raw_home).expanduser().is_absolute():
        raise ValueError("sandbox 模式下 CHAT_DATA_HOME 必须为应用节点上的绝对路径")


def _application_workspace_root(workspace_root: str) -> Path:
    """Validate the application-owned persistent conversation directory."""
    resolved = Path(workspace_root).expanduser().resolve()
    chat_data_root = Path(CHAT_DATA_HOME).expanduser().resolve()
    if chat_data_root != resolved and chat_data_root not in resolved.parents:
        raise ValueError(f"会话工作区不在 CHAT_DATA_HOME 内: {workspace_root}")
    if resolved.name != "user-data":
        raise ValueError(f"会话工作区必须是 user-data 目录: {workspace_root}")
    return resolved



def _connection_config(timeout_seconds: int) -> Any:
    _, ConnectionConfig, _, _ = _import_opensandbox_sdk()
    sandbox_url = str(os.getenv("OPENSANDBOX_URL") or "").strip()
    if not sandbox_url:
        raise ValueError("未配置 OPENSANDBOX_URL，无法创建 OpenSandbox")
    return ConnectionConfig(
        domain=sandbox_url,
        api_key=decrypt_env_value("OPENSANDBOX_API_KEY") or None,
        request_timeout=timedelta(seconds=max(30, timeout_seconds)),
        use_server_proxy=_str_to_bool(os.getenv("OPENSANDBOX_USE_SERVER_PROXY"), False),
    )


def _build_network_policy() -> Any | None:
    action = _network_default_action()
    if not action or action == "allow":
        return None
    _, _, _, models = _import_opensandbox_sdk()
    NetworkPolicy, NetworkRule = models
    egress_rules = None
    allow_targets = _network_allow_targets()
    if allow_targets:
        egress_rules = [NetworkRule(action="allow", target=target) for target in allow_targets]
    return NetworkPolicy(defaultAction=action, egress=egress_rules)


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
    await replacer([
        ContentReplaceEntry(
            path=path,
            old_content=old_content,
            new_content=new_content,
        )
    ])
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


def _iter_application_files(root: Path) -> list[tuple[str, Path]]:
    """Return regular files only; uploads must never follow application symlinks."""
    if not root.is_dir() or root.is_symlink():
        return []
    files: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            files.append((path.relative_to(root).as_posix(), path))
    return files


def _iter_application_directories(root: Path) -> list[str]:
    """Return relative directories without following application symlinks."""
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_dir() and not path.is_symlink()
    )


def _file_fingerprint(path: Path) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, digest.hexdigest()


def _application_manifest(workspace_root: Path) -> dict[str, tuple[int, int, str]]:
    manifest: dict[str, tuple[int, int, str]] = {}
    for directory_name in ("workspace", "outputs", "uploads"):
        directory = workspace_root / directory_name
        for relative, path in _iter_application_files(directory):
            manifest[f"{directory_name}/{relative}"] = _file_fingerprint(path)
    return manifest


def _mutable_application_manifest(workspace_root: Path) -> dict[str, tuple[int, int, str]]:
    """Fingerprint only trees that a sandbox operation is allowed to persist."""
    manifest: dict[str, tuple[int, int, str]] = {}
    for directory_name in ("workspace", "outputs"):
        directory = workspace_root / directory_name
        for relative, path in _iter_application_files(directory):
            manifest[f"{directory_name}/{relative}"] = _file_fingerprint(path)
    return manifest


async def _upload_application_tree(
    *,
    sandbox: Any,
    application_root: Path,
    sandbox_root: str,
    file_mode: int,
) -> tuple[int, int]:
    files = _iter_application_files(application_root)
    total_bytes = 0
    for relative, path in files:
        data = await asyncio.to_thread(path.read_bytes)
        await _write_sandbox_file(
            sandbox=sandbox,
            path=f"{sandbox_root}/{relative}",
            data=data,
            mode=file_mode,
        )
        total_bytes += len(data)
    return len(files), total_bytes


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


def _sandbox_tree_directories(application_root: Path, sandbox_root: str) -> list[str]:
    return [sandbox_root] + [
        f"{sandbox_root}/{relative}" for relative in _iter_application_directories(application_root)
    ]


async def _initialize_sandbox_filesystem(
    *,
    session: SandboxExecutionSession,
) -> None:
    """Seed the isolated sandbox from application-owned persistent files."""
    application_root = _application_workspace_root(session.workspace_root)
    user_data_directories = _sandbox_tree_directories(application_root, sandbox_thread_root())
    user_data_directories.extend([
        sandbox_workspace_dir(),
        sandbox_outputs_dir(),
        f"{sandbox_outputs_dir()}/.tool-offload",
        sandbox_uploads_dir(),
    ])
    await _create_sandbox_directories(
        sandbox=session.sandbox,
        paths=user_data_directories,
        mode=_SANDBOX_WRITABLE_DIRECTORY_MODE,
    )
    user_data_count, user_data_bytes = await _upload_application_tree(
        sandbox=session.sandbox,
        application_root=application_root,
        sandbox_root=sandbox_thread_root(),
        file_mode=_SANDBOX_WRITABLE_FILE_MODE,
    )
    skill_count = skill_bytes = 0
    if session.runtime_skills_dir is not None:
        await _create_sandbox_directories(
            sandbox=session.sandbox,
            paths=_sandbox_tree_directories(session.runtime_skills_dir, sandbox_skills_root()),
            mode=_SANDBOX_READONLY_DIRECTORY_MODE,
        )
        skill_count, skill_bytes = await _upload_application_tree(
            sandbox=session.sandbox,
            application_root=session.runtime_skills_dir,
            sandbox_root=sandbox_skills_root(),
            file_mode=_SANDBOX_READONLY_FILE_MODE,
        )
    logger.info(
        "OpenSandbox 会话快照上传完成: sandbox_id=%s user_data_files=%s user_data_bytes=%s skill_files=%s skill_bytes=%s",
        session.sandbox_id,
        user_data_count,
        user_data_bytes,
        skill_count,
        skill_bytes,
    )
    session.application_manifest = await asyncio.to_thread(_mutable_application_manifest, application_root)
    session.uploads_manifest = await asyncio.to_thread(
        _tree_manifest,
        application_root / "uploads",
        "uploads",
    )


def _tree_manifest(root: Path, prefix: str) -> dict[str, tuple[int, int, str]]:
    return {
        f"{prefix}/{relative}": _file_fingerprint(path)
        for relative, path in _iter_application_files(root)
    }


async def _sync_sandbox_uploads(session: SandboxExecutionSession) -> None:
    """Reconcile later application uploads into a cached sandbox session."""
    application_root = _application_workspace_root(session.workspace_root)
    uploads_root = application_root / "uploads"
    current_manifest = await asyncio.to_thread(_tree_manifest, uploads_root, "uploads")
    if session.uploads_manifest == current_manifest:
        return

    local_files = {
        relative: path for relative, path in _iter_application_files(uploads_root)
    }
    await _create_sandbox_directories(
        sandbox=session.sandbox,
        paths=_sandbox_tree_directories(uploads_root, sandbox_uploads_dir()),
        mode=_SANDBOX_WRITABLE_DIRECTORY_MODE,
    )
    previous_manifest = session.uploads_manifest or {}
    changed_files = sorted(
        relative for relative, fingerprint in current_manifest.items()
        if previous_manifest.get(relative) != fingerprint
    )
    for relative in changed_files:
        path = local_files[relative.removeprefix("uploads/")]
        await _write_sandbox_file(
            sandbox=session.sandbox,
            path=f"{sandbox_uploads_dir()}/{relative.removeprefix('uploads/')}",
            data=await asyncio.to_thread(path.read_bytes),
            mode=_SANDBOX_WRITABLE_FILE_MODE,
        )

    remote_files, _ = await _walk_sandbox_tree(
        session=session,
        root=sandbox_uploads_dir(),
    )
    expected_remote_paths = {
        f"{sandbox_uploads_dir()}/{relative.removeprefix('uploads/')}"
        for relative in current_manifest
    }
    stale_remote_paths = sorted(
        str(getattr(entry, "path", "") or "")
        for entry in remote_files
        if str(getattr(entry, "path", "") or "") not in expected_remote_paths
    )
    if stale_remote_paths:
        deleter = getattr(getattr(session.sandbox, "files", None), "delete_files", None)
        if not callable(deleter):
            raise ValueError("当前 OpenSandbox SDK 不支持 files.delete_files")
        await deleter(stale_remote_paths)
    session.uploads_manifest = current_manifest
    logger.info(
        "OpenSandbox uploads 增量同步完成: sandbox_id=%s changed_files=%s deleted_files=%s",
        session.sandbox_id,
        len(changed_files),
        len(stale_remote_paths),
    )


def _application_path_for_sandbox_path(session: SandboxExecutionSession, sandbox_path: str) -> Path:
    root = _application_workspace_root(session.workspace_root)
    sandbox_root = PurePosixPath(sandbox_thread_root())
    target = PurePosixPath(sandbox_path)
    try:
        relative = target.relative_to(sandbox_root)
    except ValueError as exc:
        raise ValueError(f"沙箱路径不属于当前会话: {sandbox_path}") from exc
    if not relative.parts or relative.parts[0] not in {"workspace", "outputs"}:
        raise ValueError(f"沙箱路径不属于允许同步目录: {sandbox_path}")
    local_path = (root / relative.as_posix()).resolve()
    if root not in local_path.parents:
        raise ValueError(f"沙箱同步路径越界: {sandbox_path}")
    return local_path


def _write_application_bytes_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.parent / f".{path.name}.{uuid4().hex}.sync"
    try:
        with temporary_path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _delete_application_file(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"拒绝删除应用侧符号链接: {path}")
    if path.exists():
        if not path.is_file():
            raise ValueError(f"拒绝用文件同步覆盖目录: {path}")
        path.unlink()


async def _persist_sandbox_paths(
    *,
    session: SandboxExecutionSession,
    changed_paths: list[str],
    deleted_paths: list[str] | None = None,
) -> None:
    """Download completed sandbox writes before committing them to CHAT_DATA_HOME."""
    staged: list[tuple[Path, bytes]] = []
    try:
        for sandbox_path in changed_paths:
            local_path = _application_path_for_sandbox_path(session, sandbox_path)
            data = await _read_sandbox_file_bytes(sandbox=session.sandbox, path=sandbox_path)
            staged.append((local_path, data))
        _ensure_application_paths_unchanged(
            session=session,
            paths=[path for path, _ in staged]
            + [
                _application_path_for_sandbox_path(session, sandbox_path)
                for sandbox_path in deleted_paths or []
            ],
        )
        for local_path, data in staged:
            await asyncio.to_thread(_write_application_bytes_atomically, local_path, data)
        for sandbox_path in deleted_paths or []:
            local_path = _application_path_for_sandbox_path(session, sandbox_path)
            await asyncio.to_thread(_delete_application_file, local_path)
    except Exception:
        session.pending_persistence = True
        raise
    session.application_manifest = await asyncio.to_thread(
        _mutable_application_manifest,
        _application_workspace_root(session.workspace_root),
    )
    session.pending_persistence = False
    logger.info(
        "OpenSandbox 工作区同步回写完成: sandbox_id=%s changed_files=%s changed_bytes=%s deleted_files=%s",
        session.sandbox_id,
        len(staged),
        sum(len(data) for _, data in staged),
        len(deleted_paths or []),
    )


def _ensure_application_paths_unchanged(
    *,
    session: SandboxExecutionSession,
    paths: list[Path],
) -> None:
    expected = session.application_manifest
    if expected is None:
        return
    root = _application_workspace_root(session.workspace_root)
    current = _mutable_application_manifest(root)
    conflicts = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if current.get(relative) != expected.get(relative):
            conflicts.append(relative)
    if conflicts:
        raise ValueError(
            "应用侧文件在沙箱运行期间已变更，拒绝覆盖: " + ", ".join(conflicts[:3])
        )


def _is_sandbox_descendant(path: str, root: str) -> bool:
    try:
        relative = PurePosixPath(path).relative_to(PurePosixPath(root))
    except ValueError:
        return False
    return bool(relative.parts)


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
    return [
        entry
        for entry in entries
        if _is_sandbox_descendant(str(getattr(entry, "path", "") or ""), path)
    ]


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


def _application_directory_for_sandbox_path(session: SandboxExecutionSession, sandbox_path: str) -> Path:
    local_path = _application_path_for_sandbox_path(session, sandbox_path)
    root = _application_workspace_root(session.workspace_root)
    if local_path == root / "workspace" or local_path == root / "outputs":
        raise ValueError("不允许同步覆盖工作区根目录")
    return local_path


def _is_hidden_tool_results_local_path(application_root: Path, path: Path) -> bool:
    hidden_root = application_root / "outputs" / ".tool-offload"
    return path == hidden_root or hidden_root in path.parents


async def _sync_sandbox_working_trees(session: SandboxExecutionSession) -> None:
    """Persist the sandbox-owned mutable trees after commands with unknown file effects."""
    remote_files: list[str] = []
    remote_directories: list[str] = []
    for root in (sandbox_workspace_dir(), sandbox_outputs_dir()):
        files, directories = await _walk_sandbox_tree(session=session, root=root)
        remote_files.extend(str(getattr(entry, "path", "") or "") for entry in files)
        remote_directories.extend(str(getattr(entry, "path", "") or "") for entry in directories)
    remote_set = set(remote_files)
    remote_directory_set = set(remote_directories)
    application_root = _application_workspace_root(session.workspace_root)
    local_files: list[Path] = []
    local_directories: list[Path] = []
    for directory_name in ("workspace", "outputs"):
        local_files.extend(path for _, path in _iter_application_files(application_root / directory_name))
        root = application_root / directory_name
        local_directories.extend(
            path
            for path in root.rglob("*")
            if path.is_dir()
            and not path.is_symlink()
            and not _is_hidden_tool_results_local_path(application_root, path)
        )
    deleted_paths = [
        f"{sandbox_thread_root()}/{path.relative_to(application_root).as_posix()}"
        for path in local_files
        if f"{sandbox_thread_root()}/{path.relative_to(application_root).as_posix()}" not in remote_set
    ]
    await _persist_sandbox_paths(
        session=session,
        changed_paths=sorted(remote_set),
        deleted_paths=deleted_paths,
    )
    for sandbox_path in sorted(remote_directory_set):
        if _is_tool_results_sandbox_path(sandbox_path):
            continue
        local_path = _application_directory_for_sandbox_path(session, sandbox_path)
        await asyncio.to_thread(local_path.mkdir, parents=True, exist_ok=True)
    for local_path in sorted(local_directories, key=lambda path: len(path.parts), reverse=True):
        sandbox_path = f"{sandbox_thread_root()}/{local_path.relative_to(application_root).as_posix()}"
        if sandbox_path not in remote_directory_set and local_path.exists():
            await asyncio.to_thread(local_path.rmdir)


async def sync_sandbox_working_trees(session: SandboxExecutionSession) -> None:
    """Persist workspace and outputs after one sandbox command has completed."""
    await _sync_sandbox_working_trees(session)


async def _recover_pending_persistence(session: SandboxExecutionSession) -> None:
    if session.pending_persistence:
        await _sync_sandbox_working_trees(session)


def _is_sandbox_file_not_found(error: Exception) -> bool:
    """Recognize an absent remote file without hiding transport or permission errors."""
    if isinstance(error, FileNotFoundError):
        return True
    if getattr(error, "status_code", None) == 404:
        return True
    message = str(error).lower()
    return "filenotfounderror" in message or "no such file or directory" in message


def _extract_command_output(execution: Any, command_status: Any | None, status_lookup_error: str = "") -> tuple[int, str, str]:
    stdout = "\n".join(str(getattr(item, "text", "")) for item in getattr(getattr(execution, "logs", None), "stdout", []) or [] if str(getattr(item, "text", "")))
    stderr_parts = [str(getattr(item, "text", "")) for item in getattr(getattr(execution, "logs", None), "stderr", []) or [] if str(getattr(item, "text", ""))]
    execution_error = getattr(execution, "error", None)
    if execution_error is not None:
        error_text = ": ".join(part for part in (str(getattr(execution_error, "name", "")).strip(), str(getattr(execution_error, "value", "")).strip()) if part)
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
    options: dict[str, Any] = {"timeout": timedelta(seconds=max(1, timeout_seconds))}
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


async def prepare_workspace_execution_session(
    workspace_root: str,
    user_id: str = "system",
    mode: str = "sandbox",
    runtime_skills_dir: Path | None = None,
) -> SandboxExecutionSession:
    _validate_workspace_backend_config()
    Sandbox, _, _, _ = _import_opensandbox_sdk()
    timeout_seconds = _env_int("OPENSANDBOX_TIMEOUT_SECONDS", 600)
    ready_timeout = _env_int("OPENSANDBOX_READY_TIMEOUT_SECONDS", 60, minimum=10)
    image = str(os.getenv("OPENSANDBOX_IMAGE") or "ubuntu").strip() or "ubuntu"
    create_kwargs = {
        "image": image,
        "timeout": timedelta(seconds=timeout_seconds),
        "ready_timeout": timedelta(seconds=ready_timeout),
        "entrypoint": ["sleep", "infinity"],
        "connection_config": _connection_config(timeout_seconds),
        "metadata": {"managed_by": "fxcjg", "module": "chat_data_app", "workspace_backend": OPENSANDBOX_BACKEND, "sandbox_mode": mode},
    }
    network_policy = _build_network_policy()
    if network_policy is not None:
        create_kwargs["network_policy"] = network_policy
    sandbox = await Sandbox.create(**create_kwargs)
    session = SandboxExecutionSession(
        sandbox=sandbox,
        sandbox_id=str(getattr(sandbox, "id", "") or ""),
        workspace_root=str(_application_workspace_root(workspace_root)),
        mode=mode,
        runtime_skills_dir=runtime_skills_dir,
        cache_key=_workspace_session_cache_key(workspace_root, mode, runtime_skills_dir),
    )
    try:
        await _initialize_sandbox_filesystem(session=session)
    except Exception:
        await cleanup_workspace_execution_session(session)
        raise
    return session


async def get_workspace_execution_session(
    workspace_root: str,
    user_id: str = "system",
    mode: str = "sandbox",
    runtime_skills_dir: Path | None = None,
) -> SandboxExecutionSession:
    cache_key = _workspace_session_cache_key(workspace_root, mode, runtime_skills_dir)
    now = time.monotonic()
    cached = _WORKSPACE_EXECUTION_SESSION_CACHE.get(cache_key)
    if cached is not None and cached.expires_at > now:
        async with cached.session.operation_lock:
            await _sync_sandbox_uploads(cached.session)
        return cached.session

    async with _workspace_session_lock(cache_key):
        now = time.monotonic()
        cached = _WORKSPACE_EXECUTION_SESSION_CACHE.get(cache_key)
        if cached is not None and cached.expires_at > now:
            async with cached.session.operation_lock:
                await _sync_sandbox_uploads(cached.session)
            return cached.session
        if cached is not None:
            _WORKSPACE_EXECUTION_SESSION_CACHE.pop(cache_key, None)
            await release_workspace_execution_session(cached.session)
        session = await prepare_workspace_execution_session(
            workspace_root,
            user_id,
            mode,
            runtime_skills_dir,
        )
        _WORKSPACE_EXECUTION_SESSION_CACHE[cache_key] = CachedSandboxExecutionSession(
            session=session,
            expires_at=time.monotonic() + _workspace_session_ttl_seconds(),
        )
        return session


async def attach_workspace_execution_session(session: SandboxExecutionSession | None, user_id: str = "system") -> None:
    if session is None:
        return
    cache_key = session.cache_key or _workspace_session_cache_key(
        session.workspace_root,
        session.mode,
        session.runtime_skills_dir,
    )
    cached = _WORKSPACE_EXECUTION_SESSION_CACHE.get(cache_key)
    if cached is not None and cached.session is not session:
        await release_workspace_execution_session(cached.session)
    _WORKSPACE_EXECUTION_SESSION_CACHE[cache_key] = CachedSandboxExecutionSession(
        session=session,
        expires_at=time.monotonic() + _workspace_session_ttl_seconds(),
    )


async def invalidate_workspace_execution_session(session: SandboxExecutionSession | None, user_id: str = "system") -> None:
    if session is None:
        return
    cache_key = session.cache_key or _workspace_session_cache_key(
        session.workspace_root,
        session.mode,
        session.runtime_skills_dir,
    )
    cached = _WORKSPACE_EXECUTION_SESSION_CACHE.get(cache_key)
    if cached is not None and cached.session is session:
        _WORKSPACE_EXECUTION_SESSION_CACHE.pop(cache_key, None)
    await cleanup_workspace_execution_session(session)


async def cleanup_workspace_execution_session(session: SandboxExecutionSession | None) -> None:
    if session is None or session.sandbox is None:
        return
    for cache_key, cached in list(_WORKSPACE_EXECUTION_SESSION_CACHE.items()):
        if cached.session is session:
            _WORKSPACE_EXECUTION_SESSION_CACHE.pop(cache_key, None)
    try:
        await session.sandbox.kill()
    except Exception as exc:
        logger.warning("OpenSandbox kill sandbox 失败: %s", exc, exc_info=True)
    try:
        await session.sandbox.close()
    except Exception as exc:
        logger.warning("OpenSandbox close sandbox 失败: %s", exc, exc_info=True)


async def release_workspace_execution_session(session: SandboxExecutionSession | None) -> None:
    if session is None or session.sandbox is None:
        return
    try:
        await session.sandbox.close()
    except Exception as exc:
        logger.warning("OpenSandbox close sandbox 失败: %s", exc, exc_info=True)


def sandbox_visible_path(
    path: str,
    *,
    writable: bool = False,
) -> str:
    raw = unquote(str(path or "").strip())
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return sandbox_thread_root()
    if "\x00" in raw:
        raise ValueError("路径不能包含空字节")
    mappings = (
        ("/mnt/user-data/workspace", sandbox_workspace_dir()),
        ("/mnt/user-data/outputs", sandbox_outputs_dir()),
        ("/mnt/user-data/uploads", sandbox_uploads_dir()),
        ("/workspace", sandbox_workspace_dir()),
        ("/outputs", sandbox_outputs_dir()),
        ("/uploads", sandbox_uploads_dir()),
        (sandbox_skills_root(), sandbox_skills_root()),
    )
    sandbox_path = ""
    for prefix, target_prefix in mappings:
        if raw == prefix:
            sandbox_path = target_prefix
            break
        if raw.startswith(f"{prefix}/"):
            sandbox_path = f"{target_prefix}{raw[len(prefix):]}"
            break
    if not sandbox_path:
        if raw.startswith("/"):
            raise ValueError(f"只允许访问当前会话目录内的路径: {path}")
        sandbox_path = f"{sandbox_thread_root()}/{raw.lstrip('/')}"
    if ".." in PurePosixPath(sandbox_path).parts:
        raise ValueError(f"路径越界，禁止访问当前会话目录之外的路径: {path}")
    # 沙箱快照使用固定虚拟根：/mnt/user-data（可写）和 /mnt/skills（只读）。
    workspace_roots = (sandbox_workspace_dir(), sandbox_outputs_dir(), sandbox_uploads_dir())
    readable_roots = workspace_roots + (sandbox_skills_root(),)
    if not any(sandbox_path == root or sandbox_path.startswith(f"{root}/") for root in readable_roots):
        raise ValueError(f"只允许访问 workspace/、outputs/、uploads/ 或技能路径: {path}")
    if (
        sandbox_path == sandbox_skills_root()
        or (
            sandbox_path.startswith(f"{sandbox_skills_root()}/")
            and len(PurePosixPath(sandbox_path).parts) < 4
        )
    ):
        raise ValueError(f"技能路径必须包含技能名: {path}")
    if writable:
        writable_roots = (sandbox_workspace_dir(), sandbox_outputs_dir())
        if not any(sandbox_path == root or sandbox_path.startswith(f"{root}/") for root in writable_roots):
            raise ValueError("只允许写入 workspace/ 或 outputs/")
        if _is_tool_results_sandbox_path(sandbox_path):
            raise ValueError("不允许通过文件编辑工具修改 outputs/.tool-offload/")
    return sandbox_path


def sandbox_path_to_visible(path: str) -> str:
    mappings = (
        (sandbox_workspace_dir(), f"{sandbox_thread_root()}/workspace"),
        (sandbox_outputs_dir(), f"{sandbox_thread_root()}/outputs"),
        (sandbox_uploads_dir(), f"{sandbox_thread_root()}/uploads"),
        (sandbox_skills_root(), sandbox_skills_root()),
    )
    value = str(path or "").strip()
    for prefix, visible in mappings:
        if value == prefix:
            return visible
        if value.startswith(f"{prefix}/"):
            return f"{visible}{value[len(prefix):]}"
    return value


def _is_outputs_sandbox_path(path: str) -> bool:
    return path == sandbox_outputs_dir() or path.startswith(f"{sandbox_outputs_dir()}/")


def _is_tool_results_sandbox_path(path: str) -> bool:
    root = f"{sandbox_outputs_dir()}/.tool-offload"
    return path == root or path.startswith(f"{root}/")


def _artifact_payload_from_bytes(*, visible_path: str, data: bytes) -> dict[str, Any]:
    return parse_output_artifact(visible_path=visible_path, data=data)


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


def _sandbox_entry_type(entry: Any) -> str:
    """Normalize the SDK's optional entry type without trusting a path suffix."""
    value = getattr(entry, "entry_type", getattr(entry, "type", ""))
    return str(value or "").strip().lower()


def _is_sandbox_not_directory(error: Exception) -> bool:
    message = str(error).lower()
    return "not a directory" in message or "enotdir" in message


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


def _allocate_file_read_line_budgets(line_lengths: list[int], max_chars: int) -> list[int]:
    """Allocate the shared file_read body budget in source-line order."""
    budgets: list[int] = []
    remaining = max(0, int(max_chars))
    for original_length in line_lengths:
        separator_chars = 1 if budgets else 0
        if remaining < separator_chars:
            break
        desired_chars = min(max(0, int(original_length)), MAX_FILE_READ_LINE_CHARS)
        available_chars = remaining - separator_chars
        if desired_chars > available_chars:
            if available_chars <= 0 and desired_chars > 0:
                break
            budgets.append(max(0, available_chars))
            break
        budgets.append(desired_chars)
        remaining -= desired_chars + separator_chars
    return budgets


async def sandbox_artifact_payload(*, session_ref: SandboxExecutionSessionRef, file_path: str) -> dict[str, Any]:
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        lexical_target = sandbox_visible_path(file_path)
        if _is_tool_results_sandbox_path(lexical_target):
            return {}
        try:
            target = await _resolve_sandbox_real_path(
                session=session, file_path=file_path, outputs_only=True
            )
        except ValueError:
            return {}
        try:
            data = await _read_sandbox_file_bytes(sandbox=session.sandbox, path=target)
        except Exception:
            return {}
        visible_path = sandbox_path_to_visible(target)
        return _artifact_payload_from_bytes(visible_path=visible_path, data=data)


async def sandbox_render_office_html(
    *,
    session_ref: SandboxExecutionSessionRef,
    file_path: str,
    max_chars: int,
) -> dict[str, Any]:
    """Render an Office Open XML file into an isolated HTML preview."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _resolve_sandbox_real_path(session=session, file_path=file_path)
        if Path(target).suffix.lower() not in {".docx", ".xlsx", ".pptx"}:
            raise ValueError("OfficeCLI HTML 预览仅支持 .docx、.xlsx、.pptx 文件")
        preview_path = f"/tmp/chat-data-office-preview-{uuid4().hex}.html"
        command = "officecli view {source} html --out {preview}".format(
            source=shlex.quote(target),
            preview=shlex.quote(preview_path),
        )
        try:
            exit_code, _stdout, stderr = await run_command_in_sandbox(
                session=session,
                command=command,
                timeout_seconds=60,
            )
            if exit_code != 0:
                raise RuntimeError(stderr or "OfficeCLI HTML 预览失败")
            limit = max(1, min(int(max_chars), MAX_OFFICE_HTML_CHARS))
            preview_entry = await _sandbox_path_info(session=session, path=preview_path)
            preview_size = int(getattr(preview_entry, "size", 0) or 0)
            if preview_size > limit:
                return {
                    "renderer": "officecli-html",
                    "source_path": sandbox_path_to_visible(target),
                    "html": "",
                    "truncated": True,
                }
            html = (await _read_sandbox_file_bytes(
                sandbox=session.sandbox,
                path=preview_path,
            )).decode("utf-8", errors="replace")
        finally:
            deleter = getattr(getattr(session.sandbox, "files", None), "delete_files", None)
            if callable(deleter):
                try:
                    await deleter([preview_path])
                except Exception as exc:
                    logger.warning(
                        "清理 OfficeCLI 临时预览失败: sandbox_id=%s path=%s error=%s",
                        session.sandbox_id,
                        preview_path,
                        exc,
                    )
        truncated = len(html) > limit
        return {
            "renderer": "officecli-html",
            "source_path": sandbox_path_to_visible(target),
            "html": html[:limit],
            "truncated": truncated,
        }


async def sandbox_read_text_file(
    *,
    session_ref: SandboxExecutionSessionRef,
    file_path: str,
    offset: int,
    limit: int,
    max_chars: int,
) -> dict[str, Any]:
    """Read a text page through OpenSandbox Files API, never Commands API."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _resolve_sandbox_real_path(session=session, file_path=file_path)
        visible_path = sandbox_path_to_visible(target)
        entry = await _sandbox_path_info(session=session, path=target)
        if entry is None:
            return {"exists": False, "path": visible_path}
        path_kind = await _sandbox_path_kind(session=session, path=target, entry=entry)
        if path_kind == "dir":
            return {"exists": True, "is_dir": True, "path": visible_path}
        files = getattr(session.sandbox, "files", None)
        reader = getattr(files, "read_bytes", None)
        if not callable(reader):
            raise ValueError("当前 OpenSandbox SDK 不支持 files.read_bytes")
        sample = bytes(await reader(target, range_header="bytes=0-4095"))
        if b"\x00" in sample:
            return {"exists": True, "is_binary": True, "path": visible_path}
        source_offset = max(0, int(offset)) + 1
        source_limit = max(1, int(limit)) + 1
        page_text = bytes(await reader(target, offset=source_offset, limit=source_limit)).decode(
            "utf-8", errors="replace"
        )
        source_lines = page_text.splitlines()
        has_more_lines = len(source_lines) > source_limit - 1
        selected_source_lines = source_lines[:source_limit - 1]
        if not selected_source_lines:
            is_empty = int(getattr(entry, "size", 0) or 0) == 0
            return {
                "exists": True,
                "is_dir": False,
                "is_binary": False,
                "path": visible_path,
                "total_lines": 0 if is_empty else None,
                "empty": is_empty,
                "offset_out_of_range": not is_empty,
            }
        budgets = _allocate_file_read_line_budgets(
            [len(line) for line in selected_source_lines],
            max_chars,
        )
        selected_lines = selected_source_lines[:len(budgets)]
        lines = [line[:budget] for line, budget in zip(selected_lines, budgets)]
        start_line = max(0, int(offset)) + 1
        truncated_lines = [
            {
                "line_number": start_line + position,
                "original_chars": len(line),
                "returned_chars": budget,
            }
            for position, (line, budget) in enumerate(zip(selected_lines, budgets))
            if budget < len(line)
        ]
        has_more_lines = has_more_lines or len(budgets) < len(selected_source_lines)
        result = {
            "exists": True,
            "is_dir": False,
            "is_binary": False,
            "path": visible_path,
            "lines": lines,
            "total_lines": None,
            "start_line": start_line,
            "end_line": start_line + len(lines) - 1,
            "next_offset": max(0, int(offset)) + len(lines) if has_more_lines else None,
            "has_more_lines": has_more_lines,
            "has_more": has_more_lines,
            "truncated_lines": truncated_lines,
        }
        logger.info(
            "OpenSandbox file_read 完成: sandbox_id=%s path=%s offset=%s limit=%s returned_lines=%s returned_chars=%s has_more=%s",
            session.sandbox_id,
            result["path"],
            offset,
            limit,
            len(result.get("lines") or []),
            sum(len(str(line)) for line in result.get("lines") or []),
            bool(result.get("has_more")),
        )
        return result


async def sandbox_load_skill_payload(
    *,
    session_ref: SandboxExecutionSessionRef,
    skill_name: str,
    max_chars: int,
) -> dict[str, Any]:
    """Load one mounted runtime skill without consulting the management directory."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        normalized_name = str(skill_name or "").strip()
        # 运行时快照既允许 ``officecli`` 这类单词技能名，也允许
        # ``dashboard-generation`` 这类连字符技能名。实际可访问性仍由
        # 下方快照目录存在性校验决定，不能仅以命名规则作为权限判断。
        if not re.fullmatch(r"[a-z]+(?:-[a-z]+)*", normalized_name):
            raise ValueError(f"技能名无效: {skill_name}")
        skill_root = await _resolve_sandbox_real_path(
            session=session,
            file_path=f"{sandbox_skills_root()}/{normalized_name}",
        )
        root_entry = await _sandbox_path_info(session=session, path=skill_root)
        if root_entry is None or await _sandbox_path_kind(
            session=session,
            path=skill_root,
            entry=root_entry,
        ) != "dir":
            raise ValueError(f"技能不存在或无权限访问: {normalized_name}")
        skill_md_path = f"{skill_root}/SKILL.md"
        skill_md_entry = await _sandbox_path_info(session=session, path=skill_md_path)
        if skill_md_entry is None or await _sandbox_path_kind(
            session=session,
            path=skill_md_path,
            entry=skill_md_entry,
        ) != "file":
            raise ValueError(f"技能文件不存在: {normalized_name}")
        max_skill_chars = max(1, int(max_chars))
        skill_bytes = await _read_sandbox_bytes_prefix(
            sandbox=session.sandbox,
            path=skill_md_path,
            max_chars=max_skill_chars,
        )
        skill_md = skill_bytes.decode("utf-8", errors="replace")[:max_skill_chars]
        title, description = _parse_sandbox_skill_frontmatter(skill_md)
        files, _ = await _walk_sandbox_tree(session=session, root=skill_root)
        references: list[str] = []
        scripts: list[str] = []
        assets: list[str] = []
        for entry in files:
            entry_path = str(getattr(entry, "path", "") or "")
            relative = PurePosixPath(entry_path).relative_to(PurePosixPath(skill_root))
            if not relative.parts:
                continue
            category = relative.parts[0]
            item = PurePosixPath(*relative.parts[1:]).as_posix()
            if category == "references" and item.endswith(".md"):
                references.append(item[:-3])
            elif category == "scripts":
                scripts.append(item)
            elif category == "assets":
                assets.append(item)
        result = {
            "name": normalized_name,
            "title": title or normalized_name,
            "description": description,
            "skill_md": skill_md,
            "references": sorted(references),
            "scripts": sorted(scripts),
            "assets": sorted(assets),
            "visible_path": f"{sandbox_skills_root()}/{normalized_name}",
        }
        logger.info(
            "OpenSandbox skill_load 完成: sandbox_id=%s skill_name=%s skill_chars=%s references=%s scripts=%s assets=%s",
            session.sandbox_id,
            normalized_name,
            len(str(result.get("skill_md") or "")),
            len(result.get("references") or []),
            len(result.get("scripts") or []),
            len(result.get("assets") or []),
        )
        return result


def _parse_sandbox_skill_frontmatter(text: str) -> tuple[str, str]:
    """Parse only the display fields needed by skill_load."""
    if not text.startswith("---\n"):
        return "", ""
    closing = text.find("\n---\n", len("---\n"))
    if closing < 0:
        return "", ""
    title = description = ""
    for line in text[len("---\n"):closing].splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        normalized = value.strip().strip('"').strip("'")
        if key.strip() == "title":
            title = normalized
        elif key.strip() == "description":
            description = normalized
    return title, description


async def _read_sandbox_bytes_prefix(*, sandbox: Any, path: str, max_chars: int) -> bytes:
    """Read at most the UTF-8 byte budget needed to satisfy a text char limit."""
    files = getattr(sandbox, "files", None)
    reader = getattr(files, "read_bytes", None)
    if not callable(reader):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.read_bytes")
    max_bytes = max(1, int(max_chars)) * 4
    return bytes(await reader(path, range_header=f"bytes=0-{max_bytes - 1}"))


async def sandbox_list_dir(
    *,
    session_ref: SandboxExecutionSessionRef,
    path: str,
    max_depth: int,
) -> list[dict[str, Any]]:
    """List one sandbox-visible directory through OpenSandbox Files API."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        lexical_target = sandbox_visible_path(path)
        if _is_tool_results_sandbox_path(lexical_target):
            raise ValueError("outputs/.tool-offload/ 是隐藏的工具结果缓存，不能列出")
        target = await _resolve_sandbox_real_path(session=session, file_path=path)
        if _is_tool_results_sandbox_path(target):
            raise ValueError("outputs/.tool-offload/ 是隐藏的工具结果缓存，不能列出")
        if await _sandbox_path_kind(session=session, path=target) != "dir":
            raise ValueError(f"路径不是目录: {path}")
        files = getattr(session.sandbox, "files", None)
        lister = getattr(files, "list_directory", None)
        if not callable(lister):
            raise ValueError("当前 OpenSandbox SDK 不支持 files.list_directory")
        DirectoryListEntry = _import_opensandbox_directory_list_entry()
        raw_entries = await lister(
            DirectoryListEntry(path=target, depth=max(1, int(max_depth)))
        )
        target_prefix = f"{target.rstrip('/')}/"
        entries: list[dict[str, Any]] = []
        for entry in raw_entries:
            entry_path = str(getattr(entry, "path", "") or "")
            entry_type = _sandbox_entry_type(entry)
            if (
                not entry_path.startswith(target_prefix)
                or _is_tool_results_sandbox_path(entry_path)
                or entry_type == "symlink"
            ):
                continue
            is_dir = await _sandbox_path_kind(
                session=session,
                path=entry_path,
                entry=entry,
            ) == "dir"
            modified_at = getattr(entry, "modified_at", None)
            mtime = (
                modified_at.strftime("%Y-%m-%d %H:%M")
                if hasattr(modified_at, "strftime")
                else str(modified_at or "")
            )
            visible_path = sandbox_path_to_visible(entry_path)
            entries.append({
                "type": "dir" if is_dir else "file",
                "path": visible_path + ("/" if is_dir else ""),
                "size": "-" if is_dir else int(getattr(entry, "size", 0) or 0),
                "mtime": mtime,
            })
        return sorted(entries, key=lambda item: (item["type"] == "file", item["path"]))[:500]


async def _sandbox_mutable_path(
    *,
    session: SandboxExecutionSession,
    file_path: str,
) -> str:
    target = await _resolve_sandbox_real_path(
        session=session,
        file_path=file_path,
        writable=True,
    )
    if target in {sandbox_workspace_dir(), sandbox_outputs_dir()}:
        raise ValueError("不允许操作 workspace/ 或 outputs/ 根目录")
    return target


async def sandbox_create_dir(*, session_ref: SandboxExecutionSessionRef, path: str) -> dict[str, Any]:
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _sandbox_mutable_path(session=session, file_path=path)
        try:
            await _sandbox_path_kind(session=session, path=target)
        except Exception as exc:
            if not _is_sandbox_file_not_found(exc):
                raise
        else:
            raise ValueError(f"目录已存在: {path}")
        await _create_sandbox_directories(
            sandbox=session.sandbox,
            paths=[target],
            mode=_SANDBOX_WRITABLE_DIRECTORY_MODE,
        )
        await sync_sandbox_working_trees(session)
        logger.info("OpenSandbox dir_create 完成: sandbox_id=%s path=%s", session.sandbox_id, target)
        return {"path": sandbox_path_to_visible(target), "type": "dir"}


async def sandbox_delete_path(
    *,
    session_ref: SandboxExecutionSessionRef,
    path: str,
    recursive: bool,
    expected_type: Literal["file", "dir"],
) -> dict[str, Any]:
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _sandbox_mutable_path(session=session, file_path=path)
        path_type = await _sandbox_path_kind(session=session, path=target)
        if path_type != expected_type:
            raise ValueError(f"目标是{path_type}，不能作为{expected_type}删除")
        files = getattr(session.sandbox, "files", None)
        if path_type == "dir":
            if not recursive:
                if await _list_sandbox_directory_entries(session=session, path=target):
                    raise ValueError("目录非空；如确认删除，请设置 recursive=true")
            deleter = getattr(files, "delete_directories", None)
        else:
            deleter = getattr(files, "delete_files", None)
        if not callable(deleter):
            raise ValueError("当前 OpenSandbox SDK 不支持对应的删除 Files API")
        await deleter([target])
        await sync_sandbox_working_trees(session)
        logger.info("OpenSandbox file_delete 完成: sandbox_id=%s path=%s type=%s", session.sandbox_id, target, path_type)
        return {"path": sandbox_path_to_visible(target), "type": path_type}


async def sandbox_move_path(
    *,
    session_ref: SandboxExecutionSessionRef,
    source_path: str,
    destination_path: str,
) -> dict[str, Any]:
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        source = await _sandbox_mutable_path(session=session, file_path=source_path)
        destination = await _sandbox_mutable_path(session=session, file_path=destination_path)
        path_type = await _sandbox_path_kind(session=session, path=source)
        try:
            await _sandbox_path_kind(session=session, path=destination)
        except Exception as exc:
            if not _is_sandbox_file_not_found(exc):
                raise
        else:
            raise ValueError(f"目标路径已存在: {destination_path}")
        parent_type = await _sandbox_path_kind(
            session=session,
            path=str(PurePosixPath(destination).parent),
        )
        if parent_type != "dir":
            raise ValueError(f"目标父路径不是目录: {destination_path}")
        files = getattr(session.sandbox, "files", None)
        mover = getattr(files, "move_files", None)
        if not callable(mover):
            raise ValueError("当前 OpenSandbox SDK 不支持 files.move_files")
        MoveEntry, _, _ = _import_opensandbox_filesystem_models()
        await mover([MoveEntry(source=source, destination=destination)])
        await sync_sandbox_working_trees(session)
        logger.info(
            "OpenSandbox file_move 完成: sandbox_id=%s source=%s destination=%s type=%s",
            session.sandbox_id,
            source,
            destination,
            path_type,
        )
        return {
            "source_path": sandbox_path_to_visible(source),
            "destination_path": sandbox_path_to_visible(destination),
            "type": path_type,
        }


async def sandbox_search_files(
    *,
    session_ref: SandboxExecutionSessionRef,
    path: str,
    pattern: str,
    max_results: int,
) -> list[dict[str, Any]]:
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        lexical_target = sandbox_visible_path(path)
        if _is_tool_results_sandbox_path(lexical_target):
            raise ValueError("outputs/.tool-offload/ 是隐藏的工具结果缓存，不能搜索")
        target = await _resolve_sandbox_real_path(session=session, file_path=path)
        files = getattr(session.sandbox, "files", None)
        searcher = getattr(files, "search", None)
        if not callable(searcher):
            raise ValueError("当前 OpenSandbox SDK 不支持 files.search")
        _, SearchEntry, _ = _import_opensandbox_filesystem_models()
        entries = await searcher(SearchEntry(path=target, pattern=pattern))
        results: list[dict[str, Any]] = []
        target_prefix = f"{target.rstrip('/')}/"
        for entry in sorted(entries, key=lambda item: str(getattr(item, "path", "") or "")):
            entry_path = str(getattr(entry, "path", "") or "")
            if (
                not entry_path
                or not entry_path.startswith(target_prefix)
                or _is_tool_results_sandbox_path(entry_path)
            ):
                continue
            results.append({"path": sandbox_path_to_visible(entry_path), "type": "file"})
            if len(results) >= max(1, int(max_results)):
                break
        logger.info(
            "OpenSandbox file_glob 完成: sandbox_id=%s path=%s pattern=%s count=%s",
            session.sandbox_id,
            target,
            pattern,
            len(results),
        )
        return results


def _matches_sandbox_glob(relative_path: str, pattern: str) -> bool:
    """Match root files for **/ patterns like pathlib's recursive glob does."""
    normalized = str(pattern or "**/*")
    return fnmatch.fnmatchcase(relative_path, normalized) or (
        normalized.startswith("**/")
        and fnmatch.fnmatchcase(relative_path, normalized[3:])
    )


def build_rg_command_str(
    target: str,
    patterns: list[str],
    *,
    glob: str = "**/*",
) -> str:
    """Build an rg command string for the sandbox backend (shell-executed).

    -F: fixed strings, --json: structured JSON Lines output for robust parsing.
    rg skips hidden dirs (``.tool-offload``) and binary files by default.
    """
    parts = ["rg", "-F", "--json", "--hidden"]
    if glob and glob != "**/*":
        parts.extend(["--glob", shlex.quote(glob)])
    parts.append(shlex.quote(target))
    for p in patterns:
        parts.extend(["-e", shlex.quote(p)])
    return " ".join(parts)


def parse_rg_output(
    stdout: str,
    target: str,
    *,
    max_results: int,
    max_chars: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Parse ``rg --json`` output into (matches, has_more).

    Each line is a JSON object. ``type == "match"`` entries contain
    ``data.path.text``, ``data.line_number`` and ``data.lines.text``.
    The ``target`` prefix is stripped from the file path so that ``file``
    in each match is relative to the search root.
    """
    normalized_target = target.rstrip("/")
    prefix = normalized_target + "/"
    prefix_len = len(prefix)
    matches: list[dict[str, Any]] = []
    body_chars = 0
    has_more = False
    for line in stdout.splitlines():
        if len(matches) >= max_results:
            has_more = True
            break
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if data.get("type") != "match":
            continue
        pdata = data.get("data") or {}
        file_path = (pdata.get("path") or {}).get("text") or ""
        if file_path == normalized_target:
            file_rel = ""
        elif file_path.startswith(prefix):
            file_rel = file_path[prefix_len:]
        else:
            continue
        line_no = pdata.get("line_number")
        if line_no is None:
            continue
        content = ((pdata.get("lines") or {}).get("text") or "").rstrip("\n")[:MAX_FILE_READ_LINE_CHARS]
        line_cost = len(content) + (1 if matches else 0)
        if body_chars + line_cost > max_chars:
            has_more = True
            break
        matches.append({"file": file_rel, "line_number": int(line_no), "content": content})
        body_chars += line_cost
    return matches, has_more


async def sandbox_grep_text(
    *,
    session_ref: SandboxExecutionSessionRef,
    path: str,
    patterns: list[str],
    glob: str,
    max_results: int,
    max_chars: int,
) -> dict[str, Any]:
    """Search literal text in sandbox files via rg."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _resolve_sandbox_real_path(session=session, file_path=path)

        command = build_rg_command_str(target, patterns, glob=glob)
        exit_code, stdout, stderr = await run_command_in_sandbox(
            session=session,
            command=f"bash -lc {shlex.quote(command)}",
            timeout_seconds=30,
        )
        if exit_code == 127 or "command not found" in (stderr or "").lower():
            raise RuntimeError(
                "file_grep 需要 ripgrep (rg)，沙箱环境未安装。"
                "请在沙箱镜像中安装 ripgrep: apt-get install ripgrep"
            )
        if exit_code > 1:
            raise RuntimeError(f"rg 执行失败 (exit={exit_code}): {stderr}")

        raw_matches, has_more = parse_rg_output(
            stdout, target, max_results=max_results, max_chars=max_chars,
        )
        stripped_target = target.rstrip("/")
        matches = [
            {
                "path": sandbox_path_to_visible(
                    f"{stripped_target}/{m['file']}" if m["file"] else stripped_target
                ),
                "line_number": m["line_number"],
                "line": m["content"],
            }
            for m in raw_matches
        ]
        payload = {"matches": matches, "has_more": has_more, "skipped_binary": 0, "skipped_large": 0}
        logger.info(
            "OpenSandbox file_grep 完成: sandbox_id=%s path=%s matches=%s has_more=%s",
            session.sandbox_id, target, len(matches), payload["has_more"],
        )
        return payload


async def sandbox_write_text_file(*, session_ref: SandboxExecutionSessionRef, file_path: str, content: str, mode: str) -> dict[str, Any]:
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        if mode != "overwrite":
            raise ValueError("内部文件写入只支持 overwrite")
        target = await _resolve_sandbox_real_path(session=session, file_path=file_path, writable=True)
        parent = str(PurePosixPath(target).parent)
        await _create_sandbox_directories(
            sandbox=session.sandbox,
            paths=[parent],
            mode=_SANDBOX_WRITABLE_DIRECTORY_MODE,
        )
        data = content.encode("utf-8")
        await _write_sandbox_file(sandbox=session.sandbox, path=target, data=data)
        await _persist_sandbox_paths(session=session, changed_paths=[target])
        return {
            "operation": "write",
            "file_path": sandbox_path_to_visible(target),
            "mode": mode,
            "bytes": len(data),
        }


async def sandbox_replace_text_file(
    *,
    session_ref: SandboxExecutionSessionRef,
    file_path: str,
    old_content: str,
    new_content: str,
) -> dict[str, Any]:
    """Replace one validated text fragment through the OpenSandbox Files API."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _resolve_sandbox_real_path(session=session, file_path=file_path, writable=True)
        try:
            content = (await _read_sandbox_file_bytes(
                sandbox=session.sandbox,
                path=target,
            )).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"暂不支持修改二进制文件: {file_path}") from exc
        match_count = content.count(old_content)
        if match_count == 0:
            raise ValueError("old_content 未在文件中找到；请先用 file_read 获取最新内容")
        if match_count > 1:
            raise ValueError("old_content 在文件中匹配多处；请提供更长且唯一的原文")
        await _replace_sandbox_file_content(
            sandbox=session.sandbox,
            path=target,
            old_content=old_content,
            new_content=new_content,
        )
        await _persist_sandbox_paths(session=session, changed_paths=[target])
        return {
            "operation": "edit",
            "file_path": sandbox_path_to_visible(target),
            "replaced_count": 1,
            "bytes": len(new_content.encode("utf-8")),
        }


async def sandbox_write_tool_result(
    *,
    session_ref: SandboxExecutionSessionRef,
    file_path: str,
    content: str,
) -> dict[str, Any]:
    """Write a middleware-owned hidden result to the active sandbox view once."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _resolve_sandbox_real_path(
            session=session,
            file_path=file_path,
            writable=True,
            allow_tool_result_write=True,
        )
        if not _is_tool_results_sandbox_path(target):
            raise ValueError("内部工具结果只能写入 outputs/.tool-offload/")
        try:
            await _read_sandbox_file_bytes(sandbox=session.sandbox, path=target)
        except Exception as exc:
            if not _is_sandbox_file_not_found(exc):
                raise
        else:
            raise ValueError("工具结果文件已存在")
        data = content.encode("utf-8")
        await _write_sandbox_file(sandbox=session.sandbox, path=target, data=data)
        await _persist_sandbox_paths(session=session, changed_paths=[target])
        return {"file_path": sandbox_path_to_visible(target), "bytes": len(data)}


def truncate_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n...[truncated]", True
