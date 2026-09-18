"""Conversation filesystem tools backed by one local or sandbox runtime."""

from __future__ import annotations

import difflib
from pathlib import PurePosixPath
from typing import Any

from langchain_core.tools import tool

from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime
from apps.chat_data_app.services.runtime_config import FILE_READ_MAX_CHARS
from .common import (
    DEFAULT_READ_LIMIT,
    MAX_GLOB_RESULTS,
    MAX_GREP_RESULTS,
    MAX_READ_LIMIT,
    format_lines,
    json_dumps,
    tool_result_payload,
)


_DEFAULT_GLOB_RESULTS = 100
_DEFAULT_GREP_RESULTS = 100

# 整体读取上限（字符）：与 file_grep 的 2 MiB 跳过口径一致
FULL_READ_MAX_CHARS = 2 * 1024 * 1024
# read_text_file 是分页接口（offset 0 起始、返回 lines 数组、has_more/next_offset 翻页），
# 不存在一次性全文读取；需要全文的场景必须经 read_text_file_full 翻页拼接。
_FULL_READ_PAGE_LINES = 2_000
_FULL_READ_MAX_PAGES = 50


async def read_text_file_full(
    workspace_runtime: ConversationWorkspaceRuntime,
    file_path: str,
    *,
    max_chars: int = FULL_READ_MAX_CHARS,
) -> str:
    """按 read_text_file 分页契约拉取工作区文本文件全文。

    任一页出现 truncated_lines（行级预算截断）或累计字符超上限，都视为文件超过
    max_chars，直接报错；二进制、目录、不存在、空文件同样报错。
    """
    raw_path = str(file_path or "").strip()
    if not raw_path:
        raise ValueError("file_path 不能为空")
    chunks: list[str] = []
    collected = 0
    offset = 0
    for _ in range(_FULL_READ_MAX_PAGES):
        page = await workspace_runtime.read_text_file(
            file_path=raw_path,
            offset=offset,
            limit=_FULL_READ_PAGE_LINES,
            max_chars=max_chars + 1,
        )
        if not page.get("exists"):
            raise ValueError(f"文件不存在，请确认工作区路径: {file_path}")
        if page.get("is_dir"):
            raise ValueError(f"目标是目录而非文件: {file_path}")
        if page.get("is_binary"):
            raise ValueError(f"不支持按文本读取二进制文件: {file_path}")
        if page.get("offset_out_of_range"):
            break
        if page.get("truncated_lines"):
            raise ValueError(f"文件超过 {max_chars} 字符上限，无法整体读取: {file_path}")
        lines = [str(item) for item in page.get("lines") or []]
        if not lines and offset == 0:
            raise ValueError(f"文件为空: {file_path}")
        page_text = "\n".join(lines)
        chunks.append(page_text)
        collected += len(page_text) + 1
        if collected > max_chars:
            raise ValueError(f"文件超过 {max_chars} 字符上限，无法整体读取: {file_path}")
        if not page.get("has_more"):
            break
        next_offset = page.get("next_offset")
        if next_offset is None:
            break
        offset = int(next_offset)
    text = "\n".join(chunks)
    if not text.strip():
        raise ValueError(f"文件为空: {file_path}")
    return text


def _validate_relative_glob(pattern: str, *, field_name: str) -> str:
    normalized = str(pattern or "").strip().replace("\\", "/")
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise ValueError(f"{field_name} 只能在指定目录内匹配，不能包含绝对路径或 ..")
    return normalized


def _validate_filename_pattern(pattern: str, *, field_name: str) -> str:
    """Validate the filename-only pattern used by OpenSandbox Files search."""
    normalized = str(pattern or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    if "/" in normalized or "\\" in normalized or normalized in {".", ".."}:
        raise ValueError(f"{field_name} 只能匹配文件名，不能包含路径、绝对路径或 ..")
    return normalized


def create_filesystem_tools(workspace_runtime: ConversationWorkspaceRuntime) -> list:
    """Build the single model-visible filesystem toolset for one conversation.

    All filesystem access is delegated to ``ConversationWorkspaceRuntime``. The
    runtime selects the local or OpenSandbox backend, while this module keeps
    names, argument validation, output format, and user-facing policy uniform.
    """

    @tool("file_read", parse_docstring=True)
    async def file_read(
        file_path: str,
        offset: int = 0,
        limit: int = DEFAULT_READ_LIMIT,
        intent: str = "",
    ) -> str:
        """
        读取当前会话的 /mnt/user-data/{workspace,outputs,uploads} 文本文件，或有权访问的技能只读 /mnt/skills 路径。

        返回头中的 total_lines 是文件真实总行数：大文件先按 total_lines 评估规模，优先用
        file_grep 定位或按 offset 精确分页，不要从文件头开始逐页翻完整个大文件。
        outputs/query_result 与 outputs/.tool-offload 是系统写入的结果文件，不会作为用户产物展示，不能通过其它文件工具修改、复制、移动或删除。

        Args:
            file_path: 要读取的文件路径。
            offset: 起始行偏移，从 0 开始，默认从头读取。
            limit: 本次最多读取的源行数，默认 200、最大 500。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        if offset < 0:
            raise ValueError("offset 必须大于等于 0")
        if limit < 1 or limit > MAX_READ_LIMIT:
            raise ValueError(f"limit 必须在 1-{MAX_READ_LIMIT} 之间")
        result = await workspace_runtime.read_text_file(
            file_path=file_path,
            offset=offset,
            limit=limit,
            max_chars=FILE_READ_MAX_CHARS,
        )
        visible_path = str(result.get("path") or file_path)
        if not result.get("exists"):
            return f"文件不存在: {file_path}"
        if result.get("is_dir"):
            return f"目标是目录，不是文件: {file_path}"
        if result.get("is_binary"):
            return f"暂不支持以文本方式读取二进制文件: {visible_path}"
        lines = [str(item) for item in result.get("lines") or []]
        total_lines = result.get("total_lines")
        if result.get("empty"):
            total_lines_text = total_lines if total_lines is not None else "unknown"
            return f"[file_read]\nfile_path={visible_path}\nstatus=empty\ntotal_lines={total_lines_text}\n"
        if result.get("offset_out_of_range"):
            if total_lines is None:
                return f"offset {offset} 超出文件可读范围（总行数统计失败）"
            return f"offset {offset} 超出文件总行数（共 {total_lines} 行）"
        start_line = int(result.get("start_line") or offset + 1)
        end_line = int(result.get("end_line") or offset + len(lines))
        truncated_lines = [
            item for item in result.get("truncated_lines") or [] if isinstance(item, dict)
        ]
        truncated_text = ",".join(str(item.get("line_number")) for item in truncated_lines)
        next_offset = result.get("next_offset")
        next_offset_text = f"\nnext_offset={next_offset}" if next_offset is not None else ""
        truncated_text = f"\ntruncated=true\ntruncated_lines={truncated_text}" if truncated_text else ""
        return (
            "[file_read]\n"
            f"file_path={visible_path}\n"
            f"offset={offset}\nlimit={limit}\nstart_line={start_line}\nend_line={end_line}\n"
            f"total_lines={total_lines if total_lines is not None else 'unknown'}\n"
            f"has_more={'true' if result.get('has_more') else 'false'}"
            f"{next_offset_text}{truncated_text}\n\n"
            f"{format_lines(lines, start_line)}"
        )

    @tool("file_edit", parse_docstring=True)
    async def file_edit(
        file_path: str,
        old_content: str,
        new_content: Any,
        intent: str = "",
    ) -> str:
        """
        精确修改 /mnt/user-data/workspace/ 或 /mnt/user-data/outputs/ 下已有文本文件的一处内容。

        修改前必须先用 file_read 获取最新内容。old_content 必须是文件中完整、非空且唯一的一段原文；
        找不到或匹配多处时会失败，不会修改文件。new_content 与 old_content 相同时同样会失败。
        新建文件或整体覆盖请使用 file_write。
        追加内容时同样用本工具：取文件末尾一段唯一原文作 old_content，new_content 为这段原文加换行再加新增内容。

        Args:
            file_path: 要修改的文件路径。
            old_content: 文件中完整、非空且唯一的一段待替换原文。
            new_content: 替换后的新内容；非字符串值会序列化为 JSON 文本写入。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        if not isinstance(old_content, str) or not old_content:
            raise ValueError("old_content 必须是非空字符串；请先用 file_read 读取文件后传入待替换的完整原文")
        text = new_content if isinstance(new_content, str) else json_dumps(new_content)
        if text == old_content:
            raise ValueError("old_content 与 new_content 相同，不会产生任何修改；请检查 new_content 是否为期望的新内容")
        payload = await workspace_runtime.replace_text_file(
            file_path=file_path,
            old_content=old_content,
            new_content=text,
        )
        return tool_result_payload("file_edit", payload)

    @tool("file_write", parse_docstring=True)
    async def file_write(
        file_path: str,
        content: Any,
        intent: str = "",
    ) -> str:
        """
        在 /mnt/user-data/workspace/ 或 /mnt/user-data/outputs/ 新建或整体覆盖文本文件。

        修改已有局部内容请使用 file_edit，避免无意覆盖文件。
        注意：单次 content 受模型单轮输出上限约束，过长会在生成阶段被截断成半截文件；
        超过约 8000 字符的文本必须分块写入——先用本工具写前段，再用 file_edit 逐段追加
        （以文件当前末尾的唯一内容作 old_content，new_content 为该段原文接续新增内容）。

        Args:
            file_path: 新建或覆盖的目标文件路径。
            content: 文件完整内容；非字符串值会序列化为 JSON 文本写入。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        text = content if isinstance(content, str) else json_dumps(content)
        payload = await workspace_runtime.write_text_file(
            file_path=file_path,
            content=text,
            mode="overwrite",
        )
        return tool_result_payload("file_write", payload)

    @tool("file_move", parse_docstring=True)
    async def file_move(source_path: str, destination_path: str, intent: str = "") -> str:
        """移动或重命名 /mnt/user-data/workspace、/mnt/user-data/outputs 下的文件或目录。目标路径不得存在，且其父目录必须已存在。

        Args:
            source_path: 源文件或目录路径。
            destination_path: 目标路径，不得已存在，父目录必须已存在。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        payload = await workspace_runtime.move_path(
            source_path=source_path,
            destination_path=destination_path,
        )
        return tool_result_payload("file_move", payload)

    @tool("file_copy", parse_docstring=True)
    async def file_copy(source_path: str, destination_path: str, intent: str = "") -> str:
        """复制 /mnt/user-data/{uploads,workspace,outputs} 或只读 /mnt/skills 技能路径下的单个文件到 /mnt/user-data/{workspace,outputs}。目标路径不得存在，且其父目录必须已存在。

        Args:
            source_path: 源文件路径，仅支持单个文件。
            destination_path: 目标文件路径，不得已存在，父目录必须已存在。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        payload = await workspace_runtime.copy_path(
            source_path=source_path,
            destination_path=destination_path,
        )
        return tool_result_payload("file_copy", payload)

    @tool("file_delete", parse_docstring=True)
    async def file_delete(file_path: str, intent: str = "") -> str:
        """删除 /mnt/user-data/workspace 或 /mnt/user-data/outputs 下的单个文件。不能删除目录、根目录或隐藏工具结果缓存。

        Args:
            file_path: 要删除的文件路径。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        payload = await workspace_runtime.delete_path(
            path=file_path,
            recursive=False,
            expected_type="file",
        )
        return tool_result_payload("file_delete", payload)

    @tool("file_glob", parse_docstring=True)
    async def file_glob(
        pattern: str,
        path: str = "/mnt/user-data/workspace",
        max_results: int = _DEFAULT_GLOB_RESULTS,
        intent: str = "",
    ) -> str:
        """在指定目录内递归按文件名匹配，例如 `*.json` 或 `README.*`。默认搜索 /mnt/user-data/workspace，最多返回 500 项。

        Args:
            pattern: 文件名匹配模式，如 `*.json`。
            path: 搜索起始目录，默认 /mnt/user-data/workspace。
            max_results: 最多返回的匹配项数，范围 1-500。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        normalized_pattern = _validate_filename_pattern(pattern, field_name="pattern")
        if max_results < 1 or max_results > MAX_GLOB_RESULTS:
            raise ValueError(f"max_results 必须在 1-{MAX_GLOB_RESULTS} 之间")
        entries = await workspace_runtime.search_files(
            path=str(path or "/mnt/user-data/workspace").strip(),
            pattern=normalized_pattern,
            max_results=max_results,
        )
        payload = {
            "path": str(path or "/mnt/user-data/workspace").strip(),
            "pattern": normalized_pattern,
            "count": len(entries),
            "entries": entries,
        }
        return tool_result_payload("file_glob", payload)

    @tool("file_grep", parse_docstring=True)
    async def file_grep(
        pattern: str,
        path: str = "/mnt/user-data/workspace",
        glob: str = "**/*",
        max_results: int = _DEFAULT_GREP_RESULTS,
        intent: str = "",
    ) -> str:
        """
        在文本文件中按字面内容查找，默认递归搜索 /mnt/user-data/workspace。pattern 可用 | 分隔多个关键词做 OR 匹配（如 error|warn|fail），不支持正则；glob 可限定文件范围，例如 `**/*.md`。

        最多返回 200 条匹配，每条最多 500 字符，整体正文受 CHAT_DATA_FILE_READ_MAX_CHARS 限制；二进制文件和超过 2 MiB 的文件会跳过。

        Args:
            pattern: 要查找的字面内容，可用 | 分隔多个关键词做 OR 匹配，不支持正则。
            path: 搜索起始目录，默认 /mnt/user-data/workspace。
            glob: 文件范围过滤模式，例如 `**/*.md`，默认匹配全部文件。
            max_results: 最多返回的匹配条数，范围 1-200。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        patterns = [p for p in str(pattern or "").split("|") if p]
        if not patterns:
            raise ValueError("pattern 不能为空")
        normalized_glob = _validate_relative_glob(glob, field_name="glob")
        if max_results < 1 or max_results > MAX_GREP_RESULTS:
            raise ValueError(f"max_results 必须在 1-{MAX_GREP_RESULTS} 之间")
        result = await workspace_runtime.grep_text(
            path=str(path or "/mnt/user-data/workspace").strip(),
            patterns=patterns,
            glob=normalized_glob,
            max_results=max_results,
            max_chars=FILE_READ_MAX_CHARS,
        )
        payload = {
            "path": str(path or "/mnt/user-data/workspace").strip(),
            "pattern": pattern,
            "glob": normalized_glob,
            "count": len(result.get("matches") or []),
            "has_more": bool(result.get("has_more")),
            "skipped_binary": int(result.get("skipped_binary") or 0),
            "skipped_large": int(result.get("skipped_large") or 0),
            "matches": result.get("matches") or [],
        }
        return tool_result_payload("file_grep", payload)

    @tool("file_diff", parse_docstring=True)
    async def file_diff(file_a: str, file_b: str, intent: str = "") -> str:
        """
        对比当前会话内两个文本文件的差异（unified diff），修改文件后自查改动范围用。

        两个文件整体读取（单文件上限 2 MiB，二进制不支持），输出 added_lines /
        removed_lines 统计与完整 unified diff；内容完全一致时 status=identical。
        file_edit/file_write 之后应先对比确认改动范围与预期一致，再继续下一步。

        Args:
            file_a: 原文件路径（diff 的 from 侧）。
            file_b: 对比文件路径（diff 的 to 侧）。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        text_a = await read_text_file_full(workspace_runtime, file_a)
        text_b = await read_text_file_full(workspace_runtime, file_b)
        if text_a == text_b:
            return tool_result_payload("file_diff", {
                "file_a": str(file_a),
                "file_b": str(file_b),
                "status": "identical",
                "added_lines": 0,
                "removed_lines": 0,
                "message": "两个文件内容完全一致",
            })
        lines_a = text_a.splitlines()
        lines_b = text_b.splitlines()
        diff_lines = list(difflib.unified_diff(
            lines_a, lines_b,
            fromfile=str(file_a), tofile=str(file_b), lineterm="",
        ))
        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        return tool_result_payload("file_diff", {
            "file_a": str(file_a),
            "file_b": str(file_b),
            "status": "changed",
            "added_lines": added,
            "removed_lines": removed,
            "diff": "\n".join(diff_lines),
        })

    @tool("dir_create", parse_docstring=True)
    async def dir_create(path: str, intent: str = "") -> str:
        """在 /mnt/user-data/workspace 或 /mnt/user-data/outputs 下创建目录。不能创建根目录或操作隐藏工具结果缓存。

        Args:
            path: 要创建的目录路径。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        return tool_result_payload("dir_create", await workspace_runtime.create_dir(path=path))

    @tool("dir_list", parse_docstring=True)
    async def dir_list(path: str = "/mnt/user-data/workspace", max_depth: int = 2, intent: str = "") -> str:
        """
        列出当前会话目录内容。path 必须是规范形式：/mnt/user-data/workspace、/mnt/user-data/outputs、/mnt/user-data/uploads 之一，或只读技能路径 /mnt/skills/<技能名>/...。

        max_depth 默认 2，范围 1-5；系统管理的 outputs/query_result 和 outputs/.tool-offload 不会出现在结果中。

        Args:
            path: 要列出的目录路径，必须使用规范形式。
            max_depth: 递归列出的最大深度，范围 1-5，默认 2。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        if max_depth < 1 or max_depth > 5:
            raise ValueError("max_depth 必须在 1-5 之间")
        raw = str(path or "/mnt/user-data/workspace").strip().replace("\\", "/")
        entries = await workspace_runtime.list_dir(path=raw, max_depth=max_depth)
        if not entries:
            return f"[dir_list]\npath={raw}\nis_dir=true\ncount=0\n(空目录)"
        lines = ["[dir_list]", f"path={raw}", "is_dir=true", f"count={len(entries)}", ""]
        for item in entries:
            lines.append(
                f"type={item['type']}  path={item['path']}  "
                f"size={item['size']}  mtime={item['mtime']}"
            )
        return "\n".join(lines)

    @tool("dir_delete", parse_docstring=True)
    async def dir_delete(path: str, recursive: bool = False, intent: str = "") -> str:
        """删除 /mnt/user-data/workspace 或 /mnt/user-data/outputs 下的目录。非空目录必须显式设置 recursive=true，不能删除根目录。

        Args:
            path: 要删除的目录路径。
            recursive: 是否递归删除非空目录，默认 false。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        payload = await workspace_runtime.delete_path(
            path=path,
            recursive=recursive,
            expected_type="dir",
        )
        return tool_result_payload("dir_delete", payload)

    return [
        file_read,
        file_edit,
        file_write,
        file_move,
        file_copy,
        file_delete,
        file_glob,
        file_grep,
        file_diff,
        dir_create,
        dir_list,
        dir_delete,
    ]


__all__ = ["create_filesystem_tools", "read_text_file_full"]
