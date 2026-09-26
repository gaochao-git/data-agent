"""Application manifest fingerprinting, sandbox seeding and persistence sync."""


from __future__ import annotations


import asyncio
import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from utils.logger import get_logger
from utils.tool_result_store import QUERY_RESULT_DIR_NAME, TOOL_OFFLOAD_DIR_NAME
from .workspace_sandbox_api import (
    SandboxExecutionSession,
    _create_sandbox_directories,
    _read_sandbox_file_bytes,
    _walk_sandbox_tree,
    _write_sandbox_file,
)
from .workspace_sandbox_paths import (
    _SANDBOX_READONLY_DIRECTORY_MODE,
    _SANDBOX_READONLY_FILE_MODE,
    _SANDBOX_WRITABLE_DIRECTORY_MODE,
    _SANDBOX_WRITABLE_FILE_MODE,
    _application_workspace_root,
    _is_hidden_tool_results_local_path,
    _is_managed_semantic_model_local_path,
    _is_managed_semantic_model_sandbox_path,
    _is_sandbox_descendant,
    _is_tool_results_sandbox_path,
    _is_unmanaged_local_path,
    _is_unmanaged_sandbox_path,
    _is_uploads_sandbox_path,
    sandbox_outputs_dir,
    sandbox_skills_root,
    sandbox_thread_root,
    sandbox_uploads_dir,
    sandbox_workspace_dir,
)

logger = get_logger(__name__)


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
            if (
                _is_hidden_tool_results_local_path(workspace_root, path)
                or _is_managed_semantic_model_local_path(workspace_root, path)
            ):
                continue
            manifest[f"{directory_name}/{relative}"] = _file_fingerprint(path)
    return manifest


def _mutable_application_metadata_manifest(workspace_root: Path) -> dict[str, tuple[int, int]]:
    """Return path, size and mtime for cheap cached-session validation."""
    manifest: dict[str, tuple[int, int]] = {}
    for directory_name in ("workspace", "outputs"):
        directory = workspace_root / directory_name
        for relative, path in _iter_application_files(directory):
            if (
                _is_hidden_tool_results_local_path(workspace_root, path)
                or _is_managed_semantic_model_local_path(workspace_root, path)
            ):
                continue
            stat = path.stat()
            manifest[f"{directory_name}/{relative}"] = (stat.st_size, stat.st_mtime_ns)
    return manifest


async def _upload_application_tree(
    *,
    sandbox: Any,
    application_root: Path,
    sandbox_root: str,
    file_mode: int,
    readonly_roots: tuple[str, ...] = (),
) -> tuple[int, int]:
    files = _iter_application_files(application_root)
    total_bytes = 0
    for relative, path in files:
        data = await asyncio.to_thread(path.read_bytes)
        sandbox_path = f"{sandbox_root}/{relative}"
        current_mode = (
            _SANDBOX_READONLY_FILE_MODE
            if any(_is_sandbox_descendant(sandbox_path, root) for root in readonly_roots)
            else file_mode
        )
        await _write_sandbox_file(
            sandbox=sandbox,
            path=sandbox_path,
            data=data,
            mode=current_mode,
        )
        total_bytes += len(data)
    return len(files), total_bytes


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
        f"{sandbox_outputs_dir()}/{TOOL_OFFLOAD_DIR_NAME}",
        f"{sandbox_outputs_dir()}/{QUERY_RESULT_DIR_NAME}",
        sandbox_uploads_dir(),
    ])
    writable_directories = [
        path for path in user_data_directories
        if not _is_managed_semantic_model_sandbox_path(path)
        and not _is_tool_results_sandbox_path(path)
        and not _is_uploads_sandbox_path(path)
    ]
    readonly_directories = [
        path for path in user_data_directories
        if (
            _is_managed_semantic_model_sandbox_path(path)
            or _is_tool_results_sandbox_path(path)
            or _is_uploads_sandbox_path(path)
        )
    ]
    await _create_sandbox_directories(
        sandbox=session.sandbox,
        paths=writable_directories,
        mode=_SANDBOX_WRITABLE_DIRECTORY_MODE,
    )
    await _create_sandbox_directories(
        sandbox=session.sandbox,
        paths=readonly_directories,
        mode=_SANDBOX_READONLY_DIRECTORY_MODE,
    )
    user_data_count, user_data_bytes = await _upload_application_tree(
        sandbox=session.sandbox,
        application_root=application_root,
        sandbox_root=sandbox_thread_root(),
        file_mode=_SANDBOX_WRITABLE_FILE_MODE,
        readonly_roots=(
            f"{sandbox_workspace_dir()}/semantic",
            f"{sandbox_outputs_dir()}/{TOOL_OFFLOAD_DIR_NAME}",
            f"{sandbox_outputs_dir()}/{QUERY_RESULT_DIR_NAME}",
            sandbox_uploads_dir(),
        ),
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
        mode=_SANDBOX_READONLY_DIRECTORY_MODE,
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
            mode=_SANDBOX_READONLY_FILE_MODE,
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
    if not relative.parts or relative.parts[0] not in {"workspace", "outputs", "memory"}:
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


def _application_directory_for_sandbox_path(session: SandboxExecutionSession, sandbox_path: str) -> Path:
    local_path = _application_path_for_sandbox_path(session, sandbox_path)
    root = _application_workspace_root(session.workspace_root)
    if local_path == root / "workspace" or local_path == root / "outputs":
        raise ValueError("不允许同步覆盖工作区根目录")
    return local_path


async def _collect_sandbox_remote_paths(session: SandboxExecutionSession) -> tuple[set, set]:
    """遍历沙箱 workspace/outputs，返回需持久化的 (文件路径集合, 目录路径集合)。"""
    remote_files: list[str] = []
    remote_directories: list[str] = []
    for root in (sandbox_workspace_dir(), sandbox_outputs_dir()):
        files, directories = await _walk_sandbox_tree(session=session, root=root)
        remote_files.extend(
            str(getattr(entry, "path", "") or "")
            for entry in files
            if _is_unmanaged_sandbox_path(str(getattr(entry, "path", "") or ""))
        )
        remote_directories.extend(
            str(getattr(entry, "path", "") or "")
            for entry in directories
            if _is_unmanaged_sandbox_path(str(getattr(entry, "path", "") or ""))
        )
    return set(remote_files), set(remote_directories)


def _collect_local_application_paths(application_root: Path) -> tuple[list[Path], list[Path]]:
    """遍历应用侧 workspace/outputs，返回需比对的 (文件, 目录) 列表。"""
    local_files: list[Path] = []
    local_directories: list[Path] = []
    for directory_name in ("workspace", "outputs"):
        local_files.extend(
            path for _, path in _iter_application_files(application_root / directory_name)
            if _is_unmanaged_local_path(application_root, path)
        )
        root = application_root / directory_name
        local_directories.extend(
            path
            for path in root.rglob("*")
            if path.is_dir()
            and not path.is_symlink()
            and _is_unmanaged_local_path(application_root, path)
        )
    return local_files, local_directories


async def _sync_sandbox_working_trees(session: SandboxExecutionSession) -> None:
    """Persist the sandbox-owned mutable trees after commands with unknown file effects."""
    remote_set, remote_directory_set = await _collect_sandbox_remote_paths(session)
    application_root = _application_workspace_root(session.workspace_root)
    local_files, local_directories = _collect_local_application_paths(application_root)
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
