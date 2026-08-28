"""ChatData 会话工作区 local 后端：路径校验后的宿主机文件系统实现（自 conversation_workspace 拆出）。"""

from __future__ import annotations

import asyncio
import datetime
import fnmatch
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Literal

from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT, runtime_skill_visible_root

from .artifact_registry import parse_output_artifact
from .conversation_workspace import (
    ConversationWorkspaceRuntime,
    WorkspacePathMapping,
    _absolute_command_path_tokens,
    _ensure_workspace_root_in_chat_data_home,
    _render_local_office_html,
    is_managed_semantic_model_workspace_path,
    is_system_result_workspace_path,
    resolve_workspace_path,
    to_visible_path,
)
from apps.chat_data_app.agent.workspace.officecli_adapter import (
    command_path_values,
    parse_officecli_args,
    translate_command_args,
)
from apps.chat_data_app.agent.workspace.workspace_execution import parse_rg_output
from apps.chat_data_app.agent.workspace.workspace_text_budget import allocate_file_read_line_budgets

logger = get_logger(__name__)

_TEXT_READ_CHUNK_CHARS = 64 * 1024
_TEXT_LINE_SEPARATOR_RE = re.compile(r"\n")
_TEXT_GREP_MAX_FILE_BYTES = 2 * 1024 * 1024


def _iter_text_line_fragments(file_obj: Any):
    """逐块产出 LF 分隔的物理文本行片段，并去除 CRLF 中的 CR。"""
    trailing_cr = False
    while chunk := file_obj.read(_TEXT_READ_CHUNK_CHARS):
        if trailing_cr:
            chunk = f"\r{chunk}"
            trailing_cr = False
        if chunk.endswith("\r"):
            chunk = chunk[:-1]
            trailing_cr = True
        position = 0
        for match in _TEXT_LINE_SEPARATOR_RE.finditer(chunk):
            part = chunk[position:match.start()]
            yield part[:-1] if part.endswith("\r") else part, True
            position = match.end()
        if position < len(chunk):
            yield chunk[position:], False
    if trailing_cr:
        yield "\r", False



def _scan_text_page_line_lengths(path: Path, *, offset: int, limit: int) -> dict[str, Any]:
    """流式读取请求页的行长度，不为分页正文扫描到文件末尾。"""
    selected_lines: list[dict[str, int]] = []
    line_index = 0
    pending_chars = 0

    def finish_line() -> bool:
        nonlocal line_index, pending_chars
        if offset <= line_index < offset + limit:
            selected_lines.append({
                "line_number": line_index + 1,
                "original_chars": pending_chars,
            })
        elif line_index >= offset + limit:
            return True
        line_index += 1
        pending_chars = 0
        return False

    with path.open("r", encoding="utf-8", errors="replace", newline="") as file_obj:
        for part, ends_line in _iter_text_line_fragments(file_obj):
            pending_chars += len(part)
            if ends_line and finish_line():
                return {"selected_lines": selected_lines, "has_more_lines": True}
    if pending_chars:
        if finish_line():
            return {"selected_lines": selected_lines, "has_more_lines": True}

    return {
        "selected_lines": selected_lines,
        "has_more_lines": False,
    }


@lru_cache(maxsize=512)
def _count_local_text_lines_cached(
    path_text: str,
    file_size: int,
    modified_ns: int,
    inode: int,
) -> int:
    """Count text records without loading the file into Python memory."""
    awk_path = shutil.which("awk") or "awk"
    completed = subprocess.run(
        [awk_path, "END { print NR }", path_text],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise OSError((completed.stderr or "").strip() or "awk 行数统计失败")
    try:
        return int((completed.stdout or "").strip())
    except (IndexError, ValueError) as exc:
        raise OSError("awk 行数统计结果无效") from exc


def _count_local_text_lines(target: Path) -> int:
    """Return LF-delimited physical line count from the operating system."""
    stat = target.stat()
    return _count_local_text_lines_cached(
        str(target),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(getattr(stat, "st_ino", 0) or 0),
    )


def _read_text_page_prefixes(path: Path, selected_lines: list[dict[str, int]], budgets: list[int]) -> list[str]:
    selected_by_index = {
        item["line_number"] - 1: (position, item)
        for position, item in enumerate(selected_lines)
    }
    captured_parts = [[] for _ in selected_lines]
    captured_chars = [0 for _ in selected_lines]
    current_line_index = 0
    current_line_char = 0
    last_line_index = selected_lines[-1]["line_number"] - 1

    def consume(part: str) -> None:
        nonlocal current_line_char
        selected = selected_by_index.get(current_line_index)
        if selected is not None:
            position, _metadata = selected
            remaining = budgets[position] - captured_chars[position]
            if remaining > 0:
                piece = part[:remaining]
                captured_parts[position].append(piece)
                captured_chars[position] += len(piece)
        current_line_char += len(part)

    def finish_line() -> bool:
        nonlocal current_line_index, current_line_char
        current_line_index += 1
        current_line_char = 0
        return current_line_index > last_line_index

    with path.open("r", encoding="utf-8", errors="replace", newline="") as file_obj:
        for part, ends_line in _iter_text_line_fragments(file_obj):
            consume(part)
            if ends_line and finish_line():
                return ["".join(parts) for parts in captured_parts]
    return ["".join(parts) for parts in captured_parts]


def _read_text_page_from_path(
    target: Path,
    visible_path: str,
    *,
    offset: int,
    limit: int,
    max_chars: int,
) -> dict[str, Any]:
    if not target.exists():
        return {"exists": False, "path": visible_path}
    if target.is_dir():
        return {"exists": True, "is_dir": True, "path": visible_path}
    with target.open("rb") as file_obj:
        sample = file_obj.read(4096)
    if b"\x00" in sample:
        return {"exists": True, "is_binary": True, "path": visible_path}

    try:
        total_lines = _count_local_text_lines(target)
    except Exception as exc:
        total_lines = None
        logger.warning("本地文本行数统计失败，继续读取正文: path=%s error=%s", target, exc)
    page = _scan_text_page_line_lengths(target, offset=offset, limit=limit)
    selected_lines = list(page["selected_lines"])
    if not selected_lines:
        return {
            "exists": True,
            "is_dir": False,
            "is_binary": False,
            "path": visible_path,
            "total_lines": total_lines,
            "empty": target.stat().st_size == 0,
            "offset_out_of_range": target.stat().st_size > 0,
        }
    budgets = allocate_file_read_line_budgets(
        [item["original_chars"] for item in selected_lines],
        max_chars,
    )
    selected_lines = selected_lines[:len(budgets)]
    if not selected_lines:
        return {
            "exists": True,
            "is_dir": False,
            "is_binary": False,
            "path": visible_path,
            "total_lines": total_lines,
            "empty": True,
        }
    lines = _read_text_page_prefixes(target, selected_lines, budgets)
    truncated_lines = [
        {
            "line_number": item["line_number"],
            "original_chars": item["original_chars"],
            "returned_chars": budget,
        }
        for item, budget in zip(selected_lines, budgets)
        if budget < item["original_chars"]
    ]
    has_more_lines = bool(page["has_more_lines"]) or len(budgets) < len(page["selected_lines"])
    return {
        "exists": True,
        "is_dir": False,
        "is_binary": False,
        "path": visible_path,
        "lines": lines,
        "total_lines": total_lines,
        "start_line": selected_lines[0]["line_number"],
        "end_line": selected_lines[-1]["line_number"],
        "next_offset": offset + len(selected_lines) if has_more_lines else None,
        "has_more_lines": has_more_lines,
        "has_more": has_more_lines,
        "truncated_lines": truncated_lines,
    }


def _read_local_text_file(
    workspace_root: str,
    file_path: str,
    offset: int,
    limit: int,
    max_chars: int,
) -> dict[str, Any]:
    target = resolve_workspace_path(workspace_root, file_path)
    return _read_text_page_from_path(
        target,
        to_visible_path(workspace_root, target),
        offset=offset,
        limit=limit,
        max_chars=max_chars,
    )


def _read_local_runtime_skill_file(
    target: Path,
    visible_path: str,
    offset: int,
    limit: int,
    max_chars: int,
) -> dict[str, Any]:
    return _read_text_page_from_path(
        target,
        visible_path,
        offset=offset,
        limit=limit,
        max_chars=max_chars,
    )


def _write_local_text_file(
    workspace_root: str,
    file_path: str,
    content: str,
    mode: str,
    *,
    allow_managed_semantic_model_write: bool = False,
) -> dict[str, Any]:
    if mode != "overwrite":
        raise ValueError("内部文件写入只支持 overwrite")
    target = resolve_workspace_path(
        workspace_root,
        file_path,
        writable=True,
        allow_managed_semantic_model_write=allow_managed_semantic_model_write,
    )
    if allow_managed_semantic_model_write and (
        not is_managed_semantic_model_workspace_path(workspace_root, target)
        or target.suffix.lower() != ".yaml"
    ):
        raise ValueError("内部语义模型写入只能覆盖 /mnt/user-data/workspace/semantic/ 下的 YAML 文件")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as file_obj:
            temporary_path = Path(file_obj.name)
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return {
        "operation": "write",
        "file_path": to_visible_path(workspace_root, target),
        "mode": mode,
        "bytes": len(content.encode("utf-8")),
    }


def _replace_local_text_file(
    workspace_root: str,
    file_path: str,
    old_content: str,
    new_content: str,
) -> dict[str, Any]:
    target = resolve_workspace_path(workspace_root, file_path, writable=True)
    if not target.exists():
        raise ValueError(f"文件不存在: {file_path}")
    if not target.is_file():
        raise ValueError(f"目标是目录，不是文件: {file_path}")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"暂不支持修改二进制文件: {file_path}") from exc
    match_count = content.count(old_content)
    if match_count == 0:
        raise ValueError("old_content 未在文件中找到；请先用 file_read 获取最新内容")
    if match_count > 1:
        raise ValueError("old_content 在文件中匹配多处；请提供更长且唯一的原文")
    target.write_text(content.replace(old_content, new_content, 1), encoding="utf-8")
    return {
        "operation": "edit",
        "file_path": to_visible_path(workspace_root, target),
        "replaced_count": 1,
        "bytes": len(new_content.encode("utf-8")),
    }


def _write_local_tool_result(workspace_root: str, file_path: str, content: str) -> dict[str, Any]:
    """Write a private result once; this path is never exposed to file_edit."""
    target = resolve_workspace_path(workspace_root, file_path)
    if not is_system_result_workspace_path(workspace_root, target):
        raise ValueError("内部工具结果只能写入系统结果目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    with target.open("xb") as file_obj:
        file_obj.write(data)
    return {"file_path": to_visible_path(workspace_root, target), "bytes": len(data)}


def _load_local_artifact_payload(workspace_root: str, file_path: str) -> dict[str, Any]:
    try:
        target = resolve_workspace_path(workspace_root, file_path)
    except ValueError:
        return {}
    if is_system_result_workspace_path(workspace_root, target):
        return {}
    outputs_root = resolve_workspace_path(workspace_root, "/mnt/user-data/outputs")
    if target != outputs_root and outputs_root not in target.parents:
        return {}
    if not target.is_file():
        return {}
    try:
        return parse_output_artifact(
            visible_path=to_visible_path(workspace_root, target),
            data=target.read_bytes(),
        )
    except OSError:
        return {}


def _list_local_workspace_dir(
    runtime: ConversationWorkspaceRuntime,
    path: str,
    max_depth: int,
) -> list[dict[str, Any]]:
    raw = str(path or "/mnt/user-data/workspace").strip().replace("\\", "/")
    if raw.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
        target, visible_root = _resolve_local_skill_path(runtime, raw)
        is_skill = True
    else:
        target = resolve_workspace_path(runtime.workspace_root, raw)
        visible_root = ""
        is_skill = False
    if not target.exists():
        raise FileNotFoundError(f"目录不存在: {raw}")
    if not target.is_dir():
        raise ValueError(f"目标不是目录: {raw}")

    entries: list[dict[str, Any]] = []
    root = Path(runtime.workspace_root).resolve()

    def visit(directory: Path, visible_dir: str, depth: int) -> None:
        if len(entries) >= 500:
            return
        try:
            children = sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name))
        except OSError:
            return
        for child in children:
            if len(entries) >= 500:
                return
            resolved = child.resolve()
            if child.is_symlink():
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            if is_skill:
                visible_path = f"{visible_dir}/{child.name}"
            else:
                try:
                    visible_path = "/mnt/user-data/" + resolved.relative_to(root).as_posix()
                except ValueError:
                    continue
            is_dir = child.is_dir()
            entries.append({
                "type": "dir" if is_dir else "file",
                "path": visible_path + ("/" if is_dir else ""),
                "size": "-" if is_dir else stat.st_size,
                "mtime": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
            if is_dir and depth + 1 < max_depth:
                visit(child, visible_path, depth + 1)

    visit(target, visible_root, 0)
    return entries


def _resolve_local_mutable_path(workspace_root: str, path: str) -> Path:
    target = resolve_workspace_path(workspace_root, path, writable=True)
    mutable_roots = {
        resolve_workspace_path(workspace_root, "/mnt/user-data/workspace"),
        resolve_workspace_path(workspace_root, "/mnt/user-data/outputs"),
    }
    if target in mutable_roots:
        raise ValueError("不允许操作 workspace 或 outputs 根目录")
    return target


def _create_local_dir(workspace_root: str, path: str) -> dict[str, Any]:
    target = _resolve_local_mutable_path(workspace_root, path)
    if target.exists() or target.is_symlink():
        raise ValueError(f"目录已存在: {path}")
    target.mkdir(parents=True, exist_ok=False)
    return {"path": to_visible_path(workspace_root, target), "type": "dir"}


def _delete_local_path(
    workspace_root: str,
    path: str,
    recursive: bool,
    expected_type: Literal["file", "dir"],
) -> dict[str, Any]:
    target = _resolve_local_mutable_path(workspace_root, path)
    if target.is_symlink():
        raise ValueError("不允许操作符号链接")
    if not target.exists():
        raise FileNotFoundError(f"路径不存在: {path}")
    is_dir = target.is_dir()
    actual_type = "dir" if is_dir else "file"
    if actual_type != expected_type:
        raise ValueError(f"目标是{actual_type}，不能作为{expected_type}删除")
    if is_dir:
        if recursive:
            shutil.rmtree(target)
        else:
            target.rmdir()
    else:
        target.unlink()
    return {"path": to_visible_path(workspace_root, target), "type": "dir" if is_dir else "file"}


def _move_local_path(workspace_root: str, source_path: str, destination_path: str) -> dict[str, Any]:
    source = _resolve_local_mutable_path(workspace_root, source_path)
    destination = _resolve_local_mutable_path(workspace_root, destination_path)
    if source.is_symlink() or destination.is_symlink():
        raise ValueError("不允许操作符号链接")
    if not source.exists():
        raise FileNotFoundError(f"源路径不存在: {source_path}")
    if destination.exists():
        raise ValueError(f"目标路径已存在: {destination_path}")
    if not destination.parent.is_dir():
        raise ValueError(f"目标父目录不存在: {destination_path}")
    source.rename(destination)
    return {
        "source_path": to_visible_path(workspace_root, source),
        "destination_path": to_visible_path(workspace_root, destination),
        "type": "dir" if destination.is_dir() else "file",
    }


def _copy_local_resolved_path(
    workspace_root: str,
    source: Path,
    source_visible_path: str,
    destination_path: str,
) -> dict[str, Any]:
    """Copy one already-resolved readable file into a mutable workspace destination."""
    if source.is_symlink():
        raise ValueError("不允许操作符号链接")
    if not source.exists():
        raise FileNotFoundError(f"源路径不存在: {source_visible_path}")
    if not source.is_file():
        raise ValueError(f"file_copy 只支持复制单个文件: {source_visible_path}")
    destination = _resolve_local_mutable_path(workspace_root, destination_path)
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"目标路径已存在: {destination_path}")
    if not destination.parent.is_dir():
        raise ValueError(f"目标父目录不存在: {destination_path}")
    shutil.copy2(source, destination)
    return {
        "source_path": source_visible_path,
        "destination_path": to_visible_path(workspace_root, destination),
        "type": "file",
        "bytes": destination.stat().st_size,
    }


def _copy_local_path(workspace_root: str, source_path: str, destination_path: str) -> dict[str, Any]:
    source = resolve_workspace_path(workspace_root, source_path)
    if is_system_result_workspace_path(workspace_root, source):
        raise ValueError("系统结果目录（tool-offload/query_result）不能复制")
    return _copy_local_resolved_path(
        workspace_root,
        source,
        to_visible_path(workspace_root, source),
        destination_path,
    )


def _search_local_workspace_files(
    runtime: ConversationWorkspaceRuntime,
    path: str,
    pattern: str,
    max_results: int,
) -> list[dict[str, Any]]:
    raw = str(path or "/mnt/user-data/workspace").strip().replace("\\", "/")
    if raw.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
        target, visible_root = _resolve_local_skill_path(runtime, raw)
        is_skill = True
    else:
        target = resolve_workspace_path(runtime.workspace_root, raw)
        visible_root = to_visible_path(runtime.workspace_root, target)
        is_skill = False
    if not target.is_dir() or target.is_symlink():
        raise ValueError(f"搜索路径必须是目录: {path}")
    matches: list[dict[str, Any]] = []
    for item in sorted(target.rglob("*")):
        if len(matches) >= max_results:
            break
        if item.is_symlink() or not item.is_file() or not fnmatch.fnmatchcase(item.name, pattern):
            continue
        resolved = item.resolve()
        if is_skill:
            visible_path = visible_root.rstrip("/") + "/" + item.relative_to(target).as_posix()
        else:
            visible_path = to_visible_path(runtime.workspace_root, resolved)
        matches.append({"path": visible_path, "type": "file"})
    return matches


_rg_available: bool | None = None


def _check_rg_available() -> bool:
    """Detect ripgrep at module level (cached)."""
    global _rg_available
    if _rg_available is None:
        try:
            subprocess.run(["rg", "--version"], capture_output=True, timeout=5)
            _rg_available = True
        except Exception:
            _rg_available = False
    return _rg_available


def _grep_local_workspace_text(
    runtime: ConversationWorkspaceRuntime,
    path: str,
    patterns: list[str],
    glob: str,
    max_results: int,
    max_chars: int,
) -> dict[str, Any]:
    raw = str(path or "/mnt/user-data/workspace").strip().replace("\\", "/")
    if raw.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
        target, visible_root = _resolve_local_skill_path(runtime, raw)
        is_skill = True
    else:
        target = resolve_workspace_path(runtime.workspace_root, raw)
        visible_root = to_visible_path(runtime.workspace_root, target)
        is_skill = False
    if target.is_symlink() or not target.exists():
        raise FileNotFoundError(f"路径不存在: {path}")

    if not _check_rg_available():
        raise RuntimeError("file_grep 需要 ripgrep (rg)，当前环境未安装。请安装 ripgrep: brew install ripgrep (macOS) 或 apt-get install ripgrep (Linux)")

    args: list[str] = ["rg", "-F", "--json", "--hidden"]
    if glob and glob != "**/*":
        args.extend(["--glob", glob])
    args.append(str(target))
    for p in patterns:
        args.extend(["-e", p])
    proc = subprocess.run(args, capture_output=True, timeout=30)
    if proc.returncode > 1:
        raise RuntimeError(f"rg 执行失败 (exit={proc.returncode}): {proc.stderr.decode('utf-8', errors='replace')}")

    stdout = proc.stdout.decode("utf-8", errors="replace")
    raw_matches, has_more = parse_rg_output(
        stdout, str(target), max_results=max_results, max_chars=max_chars,
    )
    matches: list[dict[str, Any]] = []
    for m in raw_matches:
        file_rel = m["file"]
        if is_skill:
            visible_path = visible_root.rstrip("/") + ("/" + file_rel if file_rel else "")
        else:
            resolved = (target / file_rel).resolve()
            visible_path = to_visible_path(runtime.workspace_root, resolved)
        matches.append({
            "path": visible_path,
            "line_number": m["line_number"],
            "line": m["content"],
        })
    return {"matches": matches, "has_more": has_more, "skipped_binary": 0, "skipped_large": 0}


def _resolve_local_skill_path(runtime: ConversationWorkspaceRuntime, path: str) -> tuple[Path, str]:
    raw = str(path or "").strip().replace("\\", "/")
    prefix = f"{SKILLS_VIRTUAL_ROOT}/"
    if not raw.startswith(prefix):
        raise ValueError(f"只允许访问技能路径: {path}")
    parts = PurePosixPath(raw).parts
    if len(parts) < 4 or parts[1:3] != ("mnt", "skills") or ".." in parts[4:]:
        raise ValueError(f"技能路径无效: {path}")
    name = parts[3]
    snapshot_root = runtime.runtime_skills_dir.resolve()
    source_entry = runtime.runtime_skills_dir / name
    source = source_entry.resolve()
    if (
        source_entry.is_symlink()
        or not source.is_dir()
        or snapshot_root not in source.parents
    ):
        raise ValueError(f"技能不存在或无权限访问: {name}")
    target = source.joinpath(*parts[4:]).resolve()
    if target != source and source not in target.parents:
        raise ValueError(f"技能路径越界: {path}")
    return target, runtime_skill_visible_root(name) + (
        "/" + "/".join(parts[4:]) if parts[4:] else ""
    )


@dataclass
class LocalWorkspaceBackend:
    """Path-validated local filesystem backend for a conversation workspace."""

    name: str = "local"

    async def read_text_file(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        offset: int,
        limit: int,
        max_chars: int,
    ) -> dict[str, Any]:
        raw_path = str(file_path or "").strip()
        if raw_path.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            target, visible_path = await runtime.resolve_readonly_skill_path_async(file_path)
            return await asyncio.to_thread(
                _read_local_runtime_skill_file, target, visible_path, offset, limit, max_chars
            )
        _ensure_workspace_root_in_chat_data_home(runtime.workspace_root)
        return await asyncio.to_thread(
            _read_local_text_file, runtime.workspace_root, file_path, offset, limit, max_chars
        )

    async def write_text_file(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        content: str,
        mode: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(_write_local_text_file, runtime.workspace_root, file_path, content, mode)

    async def write_internal_text_files(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        entries: list[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        def write_batch() -> list[dict[str, Any]]:
            return [
                _write_local_text_file(
                    runtime.workspace_root,
                    path,
                    content,
                    "overwrite",
                    allow_managed_semantic_model_write=True,
                )
                for path, content in entries
            ]

        return await asyncio.to_thread(write_batch)

    async def write_tool_result(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        content: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            _write_local_tool_result, runtime.workspace_root, file_path, content
        )

    async def replace_text_file(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        old_content: str,
        new_content: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            _replace_local_text_file,
            runtime.workspace_root,
            file_path,
            old_content,
            new_content,
        )

    async def artifact_payload(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(_load_local_artifact_payload, runtime.workspace_root, file_path)

    async def render_office_html(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        max_chars: int,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            _render_local_office_html,
            runtime.workspace_root,
            file_path,
            max_chars,
        )

    async def list_dir(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
        max_depth: int,
    ) -> list[dict[str, Any]]:
        if str(path or "").strip().startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            await runtime._ensure_snapshot_async()
        return await asyncio.to_thread(
            _list_local_workspace_dir, runtime, path, max_depth
        )

    async def create_dir(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(_create_local_dir, runtime.workspace_root, path)

    async def delete_path(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
        recursive: bool,
        expected_type: Literal["file", "dir"],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            _delete_local_path,
            runtime.workspace_root,
            path,
            recursive,
            expected_type,
        )

    async def move_path(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        source_path: str,
        destination_path: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            _move_local_path,
            runtime.workspace_root,
            source_path,
            destination_path,
        )

    async def copy_path(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        source_path: str,
        destination_path: str,
    ) -> dict[str, Any]:
        raw_source = str(source_path or "").strip()
        if raw_source.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            source, visible_source = await runtime.resolve_readonly_skill_path_async(raw_source)
            return await asyncio.to_thread(
                _copy_local_resolved_path,
                runtime.workspace_root,
                source,
                visible_source,
                destination_path,
            )
        return await asyncio.to_thread(
            _copy_local_path,
            runtime.workspace_root,
            source_path,
            destination_path,
        )

    async def search_files(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
        pattern: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        if str(path or "").strip().startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            await runtime._ensure_snapshot_async()
        return await asyncio.to_thread(
            _search_local_workspace_files,
            runtime,
            path,
            pattern,
            max_results,
        )

    async def grep_text(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
        patterns: list[str],
        glob: str,
        max_results: int,
        max_chars: int,
    ) -> dict[str, Any]:
        if str(path or "").strip().startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            await runtime._ensure_snapshot_async()
        return await asyncio.to_thread(
            _grep_local_workspace_text,
            runtime,
            path,
            patterns,
            glob,
            max_results,
            max_chars,
        )

    async def validate_bash_paths(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        command: str,
    ) -> None:
        for token in _absolute_command_path_tokens(command):
            try:
                if token.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
                    await runtime.resolve_readonly_skill_path_async(token)
                else:
                    resolve_workspace_path(runtime.workspace_root, token)
            except ValueError as exc:
                raise ValueError(
                    "bash_run 只允许访问当前会话目录或当前用户有权访问的技能路径: " + token
                ) from exc

    async def run_bash(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        command: str,
        cwd: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        raw_cwd = str(cwd or "/mnt/user-data/workspace").strip()
        if raw_cwd.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            local_cwd = (await runtime.resolve_readonly_skill_path_async(raw_cwd))[0]
        else:
            local_cwd = resolve_workspace_path(runtime.workspace_root, raw_cwd)
        path_mapping = WorkspacePathMapping(
            workspace_root=runtime.workspace_root,
            runtime_skills_dir=runtime.runtime_skills_dir,
        )
        inner = "cd {cwd} && {command}".format(
            cwd=shlex.quote(str(local_cwd)),
            command=path_mapping.translate(command),
        )
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-o",
            "pipefail",
            "-lc",
            inner,
            cwd=str(local_cwd),
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(local_cwd), "LANG": "C.UTF-8"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
            return {
                "backend": self.name,
                "sandbox_id": "",
                "cwd": to_visible_path(runtime.workspace_root, local_cwd),
                "exit_code": int(process.returncode or 0),
                "stdout": path_mapping.mask(stdout_bytes.decode("utf-8", errors="replace")),
                "stderr": path_mapping.mask(stderr_bytes.decode("utf-8", errors="replace")),
                "timed_out": False,
            }
        except TimeoutError:
            process.kill()
            await process.wait()
            return {
                "backend": self.name,
                "sandbox_id": "",
                "cwd": to_visible_path(runtime.workspace_root, local_cwd),
                "exit_code": 124,
                "stdout": "",
                "stderr": "command timed out",
                "timed_out": True,
            }

    async def run_officecli(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        args: list[str],
        cwd: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """本地后端：用宿主机 ``officecli`` 二进制以 argv 数组直接 execve，不经过 shell。

        路径按 bash 的越界校验（已排除 OfficeCLI DOM 选择器）后，**逐个 argv 元素**做
        虚拟→物理翻译。不能走"拼串翻译再 shlex.split 还原"：物理根一旦含空格
        （如 ``/Users/gao chao/...``），还原时参数会被劈成多个 token。
        环境变量按 bash_run 同样的白名单裁剪，避免把服务进程的数据库口令等配置带给子进程。
        """
        officecli_bin = shutil.which("officecli")
        if not officecli_bin:
            raise RuntimeError("本地环境未安装 OfficeCLI，无法执行 officecli_run")
        raw_cwd = str(cwd or "/mnt/user-data/workspace").strip()
        if raw_cwd.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            raise ValueError("officecli_run 不允许将 cwd 设置为技能目录")
        else:
            local_cwd = resolve_workspace_path(runtime.workspace_root, raw_cwd)

        command_spec = parse_officecli_args(args)
        visible_cwd = str(cwd or "/mnt/user-data/workspace").strip()
        for index, path in command_path_values(command_spec, visible_cwd):
            if path.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
                raise ValueError(f"officecli_run 不支持技能路径: {path}")
            try:
                resolve_workspace_path(
                    runtime.workspace_root,
                    path,
                    writable=index in command_spec.write_indices,
                )
            except ValueError as exc:
                raise ValueError(f"officecli_run 路径校验失败: {exc}") from exc
        path_mapping = WorkspacePathMapping(
            workspace_root=runtime.workspace_root,
            runtime_skills_dir=runtime.runtime_skills_dir,
        )
        translated_args = translate_command_args(
            command_spec, visible_cwd, path_mapping.translate
        )

        process = await asyncio.create_subprocess_exec(
            officecli_bin,
            *translated_args,
            cwd=str(local_cwd),
            # HOME 指向会话根（非 workspace），officecli 的配置/缓存不会污染用户可见目录。
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(runtime.workspace_root),
                "LANG": "C.UTF-8",
            },
            stdin=asyncio.subprocess.DEVNULL,  # batch 等子命令会读 stdin，避免继承并卡到超时
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
            return {
                "backend": self.name,
                "sandbox_id": "",
                "cwd": to_visible_path(runtime.workspace_root, local_cwd),
                "args": args,
                "exit_code": int(process.returncode or 0),
                "stdout": path_mapping.mask(stdout_bytes.decode("utf-8", errors="replace")),
                "stderr": path_mapping.mask(stderr_bytes.decode("utf-8", errors="replace")),
                "timed_out": False,
            }
        except TimeoutError:
            process.kill()
            await process.wait()
            return {
                "backend": self.name,
                "sandbox_id": "",
                "cwd": to_visible_path(runtime.workspace_root, local_cwd),
                "args": args,
                "exit_code": 124,
                "stdout": "",
                "stderr": "officecli 执行超时",
                "timed_out": True,
            }
