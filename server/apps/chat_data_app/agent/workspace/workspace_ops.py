"""Model-facing sandbox file operations (read/list/create/delete/move/copy/write)."""


from __future__ import annotations


from pathlib import Path, PurePosixPath
import shlex
from typing import Any, Literal, Optional
from uuid import uuid4

from utils.logger import get_logger
from .artifact_registry import parse_output_artifact
from .workspace_sandbox_api import (
    SandboxExecutionSession,
    SandboxExecutionSessionRef,
    _create_sandbox_directories,
    _import_opensandbox_directory_list_entry,
    _import_opensandbox_filesystem_models,
    _list_sandbox_directory_entries,
    _read_sandbox_file_bytes,
    _replace_sandbox_file_content,
    _resolve_sandbox_real_path,
    _sandbox_path_info,
    _sandbox_path_kind,
    _write_sandbox_file,
    require_sandbox_session,
    run_command_in_sandbox,
)
from .workspace_sandbox_paths import (
    _SANDBOX_READONLY_DIRECTORY_MODE,
    _SANDBOX_READONLY_FILE_MODE,
    _SANDBOX_WRITABLE_DIRECTORY_MODE,
    _SANDBOX_WRITABLE_FILE_MODE,
    _is_managed_semantic_model_sandbox_path,
    _is_sandbox_file_not_found,
    _is_tool_results_sandbox_path,
    _sandbox_entry_type,
    sandbox_outputs_dir,
    sandbox_path_to_visible,
    sandbox_visible_path,
    sandbox_workspace_dir,
)
from .workspace_sync import (
    _persist_sandbox_paths,
    _recover_pending_persistence,
    sync_sandbox_working_trees,
)
from .workspace_text_budget import allocate_file_read_line_budgets

logger = get_logger(__name__)

MAX_OFFICE_HTML_CHARS = 12 * 1024 * 1024


def _artifact_payload_from_bytes(*, visible_path: str, data: bytes) -> dict[str, Any]:
    return parse_output_artifact(visible_path=visible_path, data=data)


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


async def _sandbox_text_line_count(
    *,
    session: SandboxExecutionSession,
    path: str,
    entry: Any,
) -> int:
    """Count lines inside the sandbox and cache by remote file metadata."""
    size = int(getattr(entry, "size", 0) or 0)
    modified_at = str(getattr(entry, "modified_at", "") or "")
    cached = session.text_line_counts.get(path)
    if cached is not None and cached[:2] == (size, modified_at):
        return cached[2]

    command = f"awk 'END {{ print NR }}' < {shlex.quote(path)}"
    exit_code, stdout, stderr = await run_command_in_sandbox(
        session=session,
        command=command,
        timeout_seconds=30,
    )
    if exit_code != 0:
        raise RuntimeError(stderr or "沙箱文本行数统计失败")
    try:
        total_lines = int((stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError("沙箱文本行数统计结果无效") from exc
    session.text_line_counts[path] = (size, modified_at, total_lines)
    return total_lines


async def _prepare_sandbox_text_reader(
    session: Any,
    target: Any,
    entry: Any,
) -> tuple[Any, Optional[dict[str, Any]]]:
    """确认目标是可读文本；目录/二进制文件时返回 (None, 提前返回结果)。"""
    path_kind = await _sandbox_path_kind(session=session, path=target, entry=entry)
    if path_kind == "dir":
        return None, {"exists": True, "is_dir": True, "path": sandbox_path_to_visible(target)}
    files = getattr(session.sandbox, "files", None)
    reader = getattr(files, "read_bytes", None)
    if not callable(reader):
        raise ValueError("当前 OpenSandbox SDK 不支持 files.read_bytes")
    sample = bytes(await reader(target, range_header="bytes=0-4095"))
    if b"\x00" in sample:
        return reader, {"exists": True, "is_binary": True, "path": sandbox_path_to_visible(target)}
    return reader, None


async def _sandbox_total_lines_or_none(session: Any, target: Any, entry: Any) -> Optional[int]:
    """统计总行数；统计失败时记 warning 并返回 None，不阻塞正文读取。"""
    try:
        return await _sandbox_text_line_count(session=session, path=target, entry=entry)
    except Exception as exc:
        logger.warning(
            "沙箱文本行数统计失败，继续读取正文: sandbox_id=%s path=%s error=%s",
            session.sandbox_id,
            target,
            exc,
        )
        return None


def _split_file_page_lines(page_text: str) -> list[str]:
    source_lines = page_text.split("\n") if page_text else []
    if source_lines and source_lines[-1] == "" and page_text.endswith("\n"):
        source_lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in source_lines]


def _apply_file_read_line_budgets(
    selected_source_lines: list[str],
    max_chars: int,
    start_line: int,
) -> tuple[list[str], list[dict[str, Any]], bool]:
    """按字符预算截断各行为；返回 (lines, truncated_lines, 是否因预算提前截断)。"""
    budgets = allocate_file_read_line_budgets(
        [len(line) for line in selected_source_lines],
        max_chars,
    )
    selected_lines = selected_source_lines[:len(budgets)]
    lines = [line[:budget] for line, budget in zip(selected_lines, budgets, strict=True)]
    truncated_lines = [
        {
            "line_number": start_line + position,
            "original_chars": len(line),
            "returned_chars": budget,
        }
        for position, (line, budget) in enumerate(zip(selected_lines, budgets, strict=True))
        if budget < len(line)
    ]
    return lines, truncated_lines, len(budgets) < len(selected_source_lines)


async def sandbox_read_text_file(
    *,
    session_ref: SandboxExecutionSessionRef,
    file_path: str,
    offset: int,
    limit: int,
    max_chars: int,
) -> dict[str, Any]:
    """Read one page through Files API and count lines inside the sandbox."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _resolve_sandbox_real_path(session=session, file_path=file_path)
        visible_path = sandbox_path_to_visible(target)
        entry = await _sandbox_path_info(session=session, path=target)
        if entry is None:
            return {"exists": False, "path": visible_path}
        reader, early_result = await _prepare_sandbox_text_reader(session, target, entry)
        if early_result is not None:
            return early_result
        total_lines = await _sandbox_total_lines_or_none(session, target, entry)
        source_offset = max(0, int(offset)) + 1
        source_limit = max(1, int(limit)) + 1
        page_text = bytes(await reader(target, offset=source_offset, limit=source_limit)).decode(
            "utf-8", errors="replace"
        )
        source_lines = _split_file_page_lines(page_text)
        has_more_lines = len(source_lines) > source_limit - 1
        selected_source_lines = source_lines[:source_limit - 1]
        if not selected_source_lines:
            return {
                "exists": True,
                "is_dir": False,
                "is_binary": False,
                "path": visible_path,
                "total_lines": total_lines,
                "empty": int(getattr(entry, "size", 0) or 0) == 0,
                "offset_out_of_range": int(getattr(entry, "size", 0) or 0) > 0,
            }
        lines, truncated_lines, budget_trimmed = _apply_file_read_line_budgets(
            selected_source_lines, max_chars, source_offset,
        )
        has_more_lines = has_more_lines or budget_trimmed
        result = {
            "exists": True,
            "is_dir": False,
            "is_binary": False,
            "path": visible_path,
            "lines": lines,
            "total_lines": total_lines,
            "start_line": source_offset,
            "end_line": source_offset + len(lines) - 1,
            "next_offset": source_offset - 1 + len(lines) if has_more_lines else None,
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


async def _build_sandbox_list_entry(
    session: SandboxExecutionSession,
    entry: Any,
    target_prefix: str,
) -> dict[str, Any] | None:
    """构造单条目录列举条目；非本目录前缀或 symlink 时跳过（返回 None）。"""
    entry_path = str(getattr(entry, "path", "") or "")
    entry_type = _sandbox_entry_type(entry)
    if not entry_path.startswith(target_prefix) or entry_type == "symlink":
        return None
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
    return {
        "type": "dir" if is_dir else "file",
        "path": visible_path + ("/" if is_dir else ""),
        "size": "-" if is_dir else int(getattr(entry, "size", 0) or 0),
        "mtime": mtime,
    }


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
        target = await _resolve_sandbox_real_path(session=session, file_path=path)
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
            built = await _build_sandbox_list_entry(session, entry, target_prefix)
            if built is None:
                continue
            entries.append(built)
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
            if not recursive and await _list_sandbox_directory_entries(session=session, path=target):
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


async def sandbox_copy_path(
    *,
    session_ref: SandboxExecutionSessionRef,
    source_path: str,
    destination_path: str,
) -> dict[str, Any]:
    """Copy one readable file (workspace/outputs/uploads/skills) into a mutable destination."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        source = await _resolve_sandbox_real_path(session=session, file_path=source_path)
        if _is_tool_results_sandbox_path(source):
            raise ValueError("系统结果目录（tool-offload/query_result）不能复制")
        source_kind = await _sandbox_path_kind(session=session, path=source)
        if source_kind != "file":
            raise ValueError(f"file_copy 只支持复制单个文件: {source_path}")
        destination = await _sandbox_mutable_path(session=session, file_path=destination_path)
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
        data = await _read_sandbox_file_bytes(sandbox=session.sandbox, path=source)
        await _write_sandbox_file(sandbox=session.sandbox, path=destination, data=data)
        await _persist_sandbox_paths(session=session, changed_paths=[destination])
        logger.info(
            "OpenSandbox file_copy 完成: sandbox_id=%s source=%s destination=%s bytes=%s",
            session.sandbox_id,
            source,
            destination,
            len(data),
        )
        return {
            "source_path": sandbox_path_to_visible(source),
            "destination_path": sandbox_path_to_visible(destination),
            "type": "file",
            "bytes": len(data),
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
        await _write_sandbox_file(
            sandbox=session.sandbox,
            path=target,
            data=data,
            mode=_SANDBOX_WRITABLE_FILE_MODE,
        )
        await _persist_sandbox_paths(session=session, changed_paths=[target])
        return {
            "operation": "write",
            "file_path": sandbox_path_to_visible(target),
            "mode": mode,
            "bytes": len(data),
        }


async def sandbox_write_internal_text_files(
    *,
    session_ref: SandboxExecutionSessionRef,
    entries: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Write generated text files in one OpenSandbox Files API request."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        targets = [
            (_resolve_sandbox_target_for_batch(session, path), str(content or ""))
            for path, content in entries
        ]
        parents = [str(PurePosixPath(target).parent) for target, _ in targets]
        semantic_parents = [path for path in parents if _is_managed_semantic_model_sandbox_path(path)]
        writable_parents = [path for path in parents if not _is_managed_semantic_model_sandbox_path(path)]
        await _create_sandbox_directories(
            sandbox=session.sandbox,
            paths=writable_parents,
            mode=_SANDBOX_WRITABLE_DIRECTORY_MODE,
        )
        await _create_sandbox_directories(
            sandbox=session.sandbox,
            paths=semantic_parents,
            mode=_SANDBOX_READONLY_DIRECTORY_MODE,
        )
        _, _, WriteEntry = _import_opensandbox_filesystem_models()
        writer = getattr(getattr(session.sandbox, "files", None), "write_files", None)
        if not callable(writer):
            raise ValueError("当前 OpenSandbox SDK 不支持 files.write_files")
        await writer([
            WriteEntry(
                path=target,
                data=content.encode("utf-8"),
                mode=(
                    _SANDBOX_READONLY_FILE_MODE
                    if _is_managed_semantic_model_sandbox_path(target)
                    else _SANDBOX_WRITABLE_FILE_MODE
                ),
            )
            for target, content in targets
        ])
        await _persist_sandbox_paths(
            session=session,
            changed_paths=[target for target, _ in targets],
        )
        results = [{
                "operation": "write",
                "file_path": sandbox_path_to_visible(target),
                "mode": "overwrite",
                "bytes": len(content.encode("utf-8")),
            } for target, content in targets]
        logger.info(
            "OpenSandbox Files API 批量上传完成: sandbox_id=%s files=%s bytes=%s",
            session.sandbox_id,
            len(results),
            sum(item["bytes"] for item in results),
        )
        return results


def _resolve_sandbox_target_for_batch(session: SandboxExecutionSession, file_path: str) -> str:
    """Resolve a generated workspace path before the batch Files API call."""
    target = sandbox_visible_path(
        file_path,
        writable=True,
        allow_managed_semantic_model_write=True,
    )
    if not _is_managed_semantic_model_sandbox_path(target):
        raise ValueError("系统批量写入仅允许 /mnt/user-data/workspace/semantic/ 下的 YAML 语义模型文件")
    return target


async def sandbox_delete_internal_text_file(
    *,
    session_ref: SandboxExecutionSessionRef,
    path: str,
) -> dict[str, Any]:
    """Delete one system-owned semantic model file via the OpenSandbox Files API.

    仅服务于系统内部的语义残留清理通道（停用/删除模型遗留），路径解析带
    ``allow_managed_semantic_model_write=True``，模型工具面仍保持 semantic 只读。
    """
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = sandbox_visible_path(
            path,
            writable=True,
            allow_managed_semantic_model_write=True,
        )
        if not _is_managed_semantic_model_sandbox_path(target):
            raise ValueError("系统删除仅允许 /mnt/user-data/workspace/semantic/ 下的 YAML 语义模型文件")
        files = getattr(session.sandbox, "files", None)
        deleter = getattr(files, "delete_files", None)
        if not callable(deleter):
            raise ValueError("当前 OpenSandbox SDK 不支持 files.delete_files")
        await deleter([target])
        await sync_sandbox_working_trees(session)
        logger.info(
            "OpenSandbox 语义模型残留文件删除完成: sandbox_id=%s path=%s",
            session.sandbox_id,
            target,
        )
        return {"path": sandbox_path_to_visible(target), "type": "file"}


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
            raise ValueError("内部工具结果只能写入系统结果目录")
        try:
            await _read_sandbox_file_bytes(sandbox=session.sandbox, path=target)
        except Exception as exc:
            if not _is_sandbox_file_not_found(exc):
                raise
        else:
            raise ValueError("工具结果文件已存在")
        data = content.encode("utf-8")
        await _write_sandbox_file(
            sandbox=session.sandbox,
            path=target,
            data=data,
            mode=_SANDBOX_READONLY_FILE_MODE,
        )
        await _persist_sandbox_paths(session=session, changed_paths=[target])
        return {"file_path": sandbox_path_to_visible(target), "bytes": len(data)}
