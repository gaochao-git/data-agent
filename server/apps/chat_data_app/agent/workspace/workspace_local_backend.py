"""ChatData 会话工作区 local 后端：路径校验后的宿主机文件系统实现。"""

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
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from apps.chat_data_app.agent.workspace.artifact_registry import parse_output_artifact
from apps.chat_data_app.agent.workspace.officecli_adapter import (
    OFFICECLI_NO_AUTO_RESIDENT_ENV,
    command_path_values,
    parse_officecli_args,
    translate_command_args,
)
from apps.chat_data_app.agent.workspace.runtime import (
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
from apps.chat_data_app.agent.workspace.workspace_search import (
    build_rg_args,
    parse_rg_files_or_counts,
    parse_rg_output,
)
from apps.chat_data_app.agent.workspace.workspace_sync import (
    capture_mutable_manifest,
    diff_mutable_manifests,
)
from apps.chat_data_app.agent.workspace.workspace_text_budget import allocate_file_read_line_budgets
from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT, resolve_skill_snapshot_path

logger = get_logger(__name__)

_TEXT_READ_CHUNK_CHARS = 64 * 1024
_TEXT_LINE_SEPARATOR_RE = re.compile(r"\n")
_TEXT_GREP_MAX_FILE_BYTES = 2 * 1024 * 1024


def _iter_text_line_fragments(file_obj: Any) -> Iterator[tuple[str, bool]]:
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
            part = chunk[position : match.start()]
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
            selected_lines.append(
                {
                    "line_number": line_index + 1,
                    "original_chars": pending_chars,
                }
            )
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
    if pending_chars and finish_line():
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
    selected_by_index = {item["line_number"] - 1: (position, item) for position, item in enumerate(selected_lines)}
    captured_parts: list[list[str]] = [[] for _ in selected_lines]
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
    selected_lines = selected_lines[: len(budgets)]
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
        for item, budget in zip(selected_lines, budgets, strict=True)
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
        not is_managed_semantic_model_workspace_path(workspace_root, target) or target.suffix.lower() != ".yaml"
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


_MAX_LIST_ENTRIES = 500


def _resolve_list_target(
    runtime: ConversationWorkspaceRuntime,
    raw: str,
) -> tuple[Path, str, bool]:
    """把 dir_list 入参解析为 (实际路径, 技能可见根, 是否技能路径)。"""
    if raw.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
        target, visible_root = _resolve_local_skill_path(runtime, raw)
        return target, visible_root, True
    return resolve_workspace_path(runtime.workspace_root, raw), "", False


def _sorted_local_children(directory: Path) -> list[Path] | None:
    """返回排序后的子项；目录不可读时返回 None。"""
    try:
        return sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name))
    except OSError:
        return None


def _local_entry_visible_path(
    child: Path,
    *,
    is_skill: bool,
    visible_dir: str,
    root: Path,
) -> str | None:
    if is_skill:
        return f"{visible_dir}/{child.name}"
    try:
        return "/mnt/user-data/" + child.resolve().relative_to(root).as_posix()
    except ValueError:
        return None


def _local_dir_entry(
    child: Path,
    stat: os.stat_result,
    is_dir: bool,
    *,
    is_skill: bool,
    visible_dir: str,
    root: Path,
) -> dict[str, Any] | None:
    """构造单个目录项；路径越出工作区根（无法转可见路径）时返回 None。"""
    visible_path = _local_entry_visible_path(child, is_skill=is_skill, visible_dir=visible_dir, root=root)
    if visible_path is None:
        return None
    return {
        "type": "dir" if is_dir else "file",
        "path": visible_path + ("/" if is_dir else ""),
        "size": "-" if is_dir else stat.st_size,
        "mtime": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
    }


def _visit_local_dir(
    directory: Path,
    visible_dir: str,
    depth: int,
    *,
    entries: list[dict[str, Any]],
    root: Path,
    is_skill: bool,
    max_depth: int,
) -> None:
    if len(entries) >= _MAX_LIST_ENTRIES:
        return
    children = _sorted_local_children(directory)
    if children is None:
        return
    for child in children:
        if len(entries) >= _MAX_LIST_ENTRIES:
            return
        if child.is_symlink():
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        is_dir = child.is_dir()
        entry = _local_dir_entry(child, stat, is_dir, is_skill=is_skill, visible_dir=visible_dir, root=root)
        if entry is None:
            continue
        entries.append(entry)
        if is_dir and depth + 1 < max_depth:
            _visit_local_dir(
                child,
                entry["path"].rstrip("/"),
                depth + 1,
                entries=entries,
                root=root,
                is_skill=is_skill,
                max_depth=max_depth,
            )


def _list_local_workspace_dir(
    runtime: ConversationWorkspaceRuntime,
    path: str,
    max_depth: int,
) -> list[dict[str, Any]]:
    raw = str(path or "/mnt/user-data/workspace").strip().replace("\\", "/")
    target, visible_root, is_skill = _resolve_list_target(runtime, raw)
    if not target.exists():
        raise FileNotFoundError(f"目录不存在: {raw}")
    if not target.is_dir():
        raise ValueError(f"目标不是目录: {raw}")

    entries: list[dict[str, Any]] = []
    root = Path(runtime.workspace_root).resolve()
    _visit_local_dir(
        target,
        visible_root,
        0,
        entries=entries,
        root=root,
        is_skill=is_skill,
        max_depth=max_depth,
    )
    return entries


def _resolve_local_mutable_path(
    workspace_root: str,
    path: str,
    *,
    allow_uploads: bool = False,
    allow_managed_semantic_model_write: bool = False,
) -> Path:
    """解析应用侧可变更路径。

    ``allow_uploads`` 只为人侧删除接口打开 uploads：用户要能清理自己上传的文件，
    但 uploads 根目录本身仍不可操作（只删文件，不删目录结构）。
    ``allow_managed_semantic_model_write`` 只为系统内部的语义模型写入/残留清理
    通道打开 semantic 目录；模型工具始终走默认值（semantic 只读快照）。
    """
    target = resolve_workspace_path(
        workspace_root,
        path,
        writable=True,
        allow_uploads_write=allow_uploads,
        allow_managed_semantic_model_write=allow_managed_semantic_model_write,
    )
    mutable_roots = {
        resolve_workspace_path(workspace_root, "/mnt/user-data/workspace"),
        resolve_workspace_path(workspace_root, "/mnt/user-data/outputs"),
    }
    if allow_uploads:
        mutable_roots.add(resolve_workspace_path(workspace_root, "/mnt/user-data/uploads"))
    if target in mutable_roots:
        raise ValueError("不允许操作工作区根目录")
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
    *,
    allow_uploads: bool = False,
    allow_managed_semantic_model_write: bool = False,
) -> dict[str, Any]:
    target = _resolve_local_mutable_path(
        workspace_root,
        path,
        allow_uploads=allow_uploads,
        allow_managed_semantic_model_write=allow_managed_semantic_model_write,
    )
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


def delete_local_workspace_file(
    workspace_root: str,
    path: str,
    *,
    allow_uploads: bool = False,
) -> dict[str, Any]:
    """删除应用侧权威副本中的单个文件（人侧 HTTP 接口入口）。

    刻意只暴露「删单个文件」这一种能力：目录递归删除需要独立的确认交互，不由这里代劳。
    路径安全完全复用 ``_delete_local_path``（符号链接拒绝、realpath 越界校验、
    系统结果目录拒绝），因此 query_result 这类审计留存目录在这里就被挡住。
    """
    return _delete_local_path(
        workspace_root,
        path,
        recursive=False,
        expected_type="file",
        allow_uploads=allow_uploads,
    )


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
        except Exception as exc:
            # 一次性降级缓存:rg 缺失属部署环境问题,缓存 False 避免每次 grep 都重复拉起
            # 失败子进程;调用方随后会给出明确的安装提示错误,不再重试探测。
            _rg_available = False
            logger.warning("检测 ripgrep (rg) 失败,本进程内按未安装处理,file_grep 将返回安装提示: %s", exc)
    return _rg_available


def _resolve_grep_target(runtime: ConversationWorkspaceRuntime, path: str) -> tuple[Path, str, bool]:
    """解析 grep 目标：skills 虚拟根走技能快照解析，其余走工作区路径解析。

    返回 (物理目标, 可见根路径, 是否技能目录)。
    """
    raw = str(path or "/mnt/user-data/workspace").strip().replace("\\", "/")
    if raw.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
        target, visible_root = _resolve_local_skill_path(runtime, raw)
        return target, visible_root, True
    target = resolve_workspace_path(runtime.workspace_root, raw)
    return target, to_visible_path(runtime.workspace_root, target), False


def _run_local_rg(args: list[str]) -> str:
    """执行本地 rg 并返回解码后的 stdout；缺失/执行失败抛带安装提示的错误。"""
    if not _check_rg_available():
        raise RuntimeError(
            "file_grep 需要 ripgrep (rg)，当前环境未安装。请安装 ripgrep: brew install ripgrep (macOS) 或 apt-get install ripgrep (Linux)"
        )
    proc = subprocess.run(args, capture_output=True, timeout=30)
    if proc.returncode > 1:
        raise RuntimeError(f"rg 执行失败 (exit={proc.returncode}): {proc.stderr.decode('utf-8', errors='replace')}")
    return proc.stdout.decode("utf-8", errors="replace")


def _grep_local_visible_path(
    file_rel: str,
    is_skill: bool,
    target: Path,
    visible_root: str,
    runtime: ConversationWorkspaceRuntime,
) -> str:
    """按 skill / workspace 两种可见路径规则把相对路径转成模型可见路径。"""
    if is_skill:
        return visible_root.rstrip("/") + ("/" + file_rel if file_rel else "")
    return to_visible_path(runtime.workspace_root, (target / file_rel).resolve())


def _build_local_grep_match(
    m: dict[str, Any],
    is_skill: bool,
    target: Path,
    visible_root: str,
    runtime: ConversationWorkspaceRuntime,
) -> dict[str, Any]:
    """构造单条 grep 命中条目（可见路径 + 行号 + 内容 + 可选上下文）。"""
    entry: dict[str, Any] = {
        "path": _grep_local_visible_path(m["file"], is_skill, target, visible_root, runtime),
        "line_number": m["line_number"],
        "line": m["content"],
    }
    if m.get("before"):
        entry["before"] = m["before"]
    if m.get("after"):
        entry["after"] = m["after"]
    return entry


def _grep_local_workspace_text(
    runtime: ConversationWorkspaceRuntime,
    path: str,
    patterns: list[str],
    glob: str,
    max_results: int,
    max_chars: int,
    use_regex: bool = False,
    ignore_case: bool = False,
    context_lines: int = 0,
    output_mode: str = "content",
) -> dict[str, Any]:
    target, visible_root, is_skill = _resolve_grep_target(runtime, path)
    if target.is_symlink() or not target.exists():
        raise FileNotFoundError(f"路径不存在: {path}")
    args = build_rg_args(
        str(target),
        patterns,
        glob=glob,
        use_regex=use_regex,
        ignore_case=ignore_case,
        context_lines=context_lines,
        output_mode=output_mode,
    )
    stdout = _run_local_rg(args)

    if output_mode in ("files_with_matches", "count"):
        entries, has_more = parse_rg_files_or_counts(
            stdout,
            str(target),
            max_results=max_results,
            mode=output_mode,
        )
        visible_entries = [
            {
                "path": _grep_local_visible_path(e["file"], is_skill, target, visible_root, runtime),
                **({"match_count": e["match_count"]} if "match_count" in e else {}),
            }
            for e in entries
        ]
        return {"entries": visible_entries, "has_more": has_more, "skipped_binary": 0, "skipped_large": 0}

    raw_matches, has_more = parse_rg_output(
        stdout,
        str(target),
        max_results=max_results,
        max_chars=max_chars,
        context_lines=context_lines if output_mode == "content" else 0,
    )
    matches = [_build_local_grep_match(m, is_skill, target, visible_root, runtime) for m in raw_matches]
    return {"matches": matches, "has_more": has_more, "skipped_binary": 0, "skipped_large": 0}


def _resolve_local_skill_path(runtime: ConversationWorkspaceRuntime, path: str) -> tuple[Path, str]:
    """本地后端的技能路径解析；安全规则统一走 utils.skill_paths 的共享实现。"""
    return resolve_skill_snapshot_path(runtime.runtime_skills_dir, path)


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

    async def delete_internal_text_files(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
    ) -> dict[str, Any]:
        """Delete one system-owned semantic model file (residual cleanup path)."""
        return await asyncio.to_thread(
            _delete_local_path,
            runtime.workspace_root,
            path,
            False,
            "file",
            allow_managed_semantic_model_write=True,
        )

    async def write_tool_result(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        content: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(_write_local_tool_result, runtime.workspace_root, file_path, content)

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
        return await asyncio.to_thread(_list_local_workspace_dir, runtime, path, max_depth)

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
        use_regex: bool = False,
        ignore_case: bool = False,
        context_lines: int = 0,
        output_mode: str = "content",
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
            use_regex,
            ignore_case,
            context_lines,
            output_mode,
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
                raise ValueError("bash_run 只允许访问当前会话目录或当前用户有权访问的技能路径: " + token) from exc

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
            # 技能快照在工作区根之外，cwd 必须回到技能虚拟路径，不能交给
            # to_visible_path（它只认识 /mnt/user-data 根）。
            visible_cwd = raw_cwd
        else:
            local_cwd = resolve_workspace_path(runtime.workspace_root, raw_cwd)
            visible_cwd = to_visible_path(runtime.workspace_root, local_cwd)
        path_mapping = WorkspacePathMapping(
            workspace_root=runtime.workspace_root,
            runtime_skills_dir=runtime.runtime_skills_dir,
        )
        inner = f"cd {shlex.quote(str(local_cwd))} && {path_mapping.translate(command)}"
        # 间接写入归因：命令副作用不可预知（超时/失败也可能已写入），执行前后各采一次
        # 工作区 manifest 做对比，成功与超时两条路径都归因。
        manifest_before = await asyncio.to_thread(capture_mutable_manifest, runtime.workspace_root)
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-o",
            "pipefail",
            "-lc",
            inner,
            cwd=str(local_cwd),
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(local_cwd),
                "LANG": "C.UTF-8",
                **OFFICECLI_NO_AUTO_RESIDENT_ENV,
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
            return {
                "backend": self.name,
                "sandbox_id": "",
                "cwd": visible_cwd,
                "exit_code": int(process.returncode or 0),
                "stdout": path_mapping.mask(stdout_bytes.decode("utf-8", errors="replace")),
                "stderr": path_mapping.mask(stderr_bytes.decode("utf-8", errors="replace")),
                "timed_out": False,
                "changed_files": await self._observed_changes(runtime, manifest_before),
            }
        except TimeoutError:
            process.kill()
            await process.wait()
            return {
                "backend": self.name,
                "sandbox_id": "",
                "cwd": visible_cwd,
                "exit_code": 124,
                "stdout": "",
                "stderr": "command timed out",
                "timed_out": True,
                "changed_files": await self._observed_changes(runtime, manifest_before),
            }

    @staticmethod
    async def _observed_changes(
        runtime: ConversationWorkspaceRuntime,
        manifest_before: dict[str, tuple[int, int, str]],
    ) -> dict[str, Any]:
        """命令执行后采集 manifest 并与基线对比，产出间接写入变更归因。"""
        manifest_after = await asyncio.to_thread(capture_mutable_manifest, runtime.workspace_root)
        return diff_mutable_manifests(manifest_before, manifest_after)

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
        translated_args = translate_command_args(command_spec, visible_cwd, path_mapping.translate)

        # 间接写入归因：与沙箱后端同口径，只对写入类命令计算（成功/超时都归因——
        # 失败也可能已写入部分产物）；只读命令不产生归因（字段缺省）。
        attribute_writes = bool(command_spec.mutates or command_spec.write_indices)
        manifest_before = (
            await asyncio.to_thread(capture_mutable_manifest, runtime.workspace_root) if attribute_writes else None
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
                **OFFICECLI_NO_AUTO_RESIDENT_ENV,
            },
            stdin=asyncio.subprocess.DEVNULL,  # batch 等子命令会读 stdin，避免继承并卡到超时
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
            result = {
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
            result = {
                "backend": self.name,
                "sandbox_id": "",
                "cwd": to_visible_path(runtime.workspace_root, local_cwd),
                "args": args,
                "exit_code": 124,
                "stdout": "",
                "stderr": "officecli 执行超时",
                "timed_out": True,
            }
        if manifest_before is not None:
            result["changed_files"] = await self._observed_changes(runtime, manifest_before)
        return result
