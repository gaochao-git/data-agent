"""Conversation filesystem tools backed by one local or sandbox runtime."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, NoReturn

from langchain_core.tools import tool

from utils.logger import get_logger
from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime
from apps.chat_data_app.services.runtime_config import FILE_READ_MAX_CHARS
from .common import (
    DEFAULT_READ_LIMIT,
    MAX_GLOB_RESULTS,
    MAX_GREP_RESULTS,
    MAX_READ_LIMIT,
    format_lines,
    json_dumps,
)

logger = get_logger(__name__)

_DEFAULT_GLOB_RESULTS = 100
_DEFAULT_GREP_RESULTS = 100


def _json_result(tool_name: str, payload: dict[str, Any]) -> str:
    return f"[{tool_name}]\n" + json_dumps(payload)


def _tool_error(tool_name: str, exc: Exception) -> NoReturn:
    """按预期/非预期分级记日志后重新抛出。

    异常由 ToolErrorMiddleware 统一转成 ``status="error"`` 的 ToolMessage，
    content 带异常详情；工具失败从此只走显式协议位，不再以普通文本返回。
    """
    message = str(exc) or exc.__class__.__name__
    if isinstance(exc, (ValueError, FileNotFoundError, IsADirectoryError, NotADirectoryError)):
        logger.warning("%s 工具错误: %s", tool_name, message)
    else:
        logger.error("%s 执行失败: %s", tool_name, message, exc_info=True)
    raise exc


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

    @tool("file_read")
    async def file_read(
        file_path: str,
        offset: int = 0,
        limit: int = DEFAULT_READ_LIMIT,
    ) -> str:
        """
        读取当前会话的 /mnt/user-data/{workspace,outputs,uploads} 文本文件，或有权访问的技能只读 /mnt/skills 路径。

        默认读取 200 个源行，最多读取 500 行。每行正文最多返回 500 个字符，整页正文受
        CHAT_DATA_FILE_READ_MAX_CHARS 限制；超出的行内容直接截断，不支持续读。
        返回头中的 total_lines 是文件真实总行数：大文件先按 total_lines 评估规模，优先用
        file_grep 定位或按 offset 精确分页，不要从文件头开始逐页翻完整个大文件。

        /mnt/user-data/outputs/query_result/<id>.json 是查询工具统一保存的结果文件；后续分析应按查询工具返回的 result_path 分页读取。
        /mnt/user-data/outputs/.tool-offload/<id>.json 或 .txt 是普通工具的大结果缓存。两类系统结果都不会作为用户产物展示，不能通过其它文件工具修改、复制、移动或删除。
        """
        try:
            if offset < 0:
                return "offset 必须大于等于 0"
            if limit < 1 or limit > MAX_READ_LIMIT:
                return f"limit 必须在 1-{MAX_READ_LIMIT} 之间"
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
        except Exception as exc:
            return _tool_error("file_read", exc)

    @tool("file_edit")
    async def file_edit(
        file_path: str,
        old_content: str,
        new_content: Any,
    ) -> str:
        """
        精确修改 /mnt/user-data/workspace/ 或 /mnt/user-data/outputs/ 下已有文本文件的一处内容。

        修改前必须先用 file_read 获取最新内容。old_content 必须是文件中完整、非空且唯一的一段原文；
        找不到或匹配多处时会失败，不会修改文件。新建文件或整体覆盖请使用 file_write。
        """
        try:
            if not isinstance(old_content, str) or not old_content:
                return "old_content 必须是非空字符串；请先用 file_read 读取文件后传入待替换的完整原文"
            text = new_content if isinstance(new_content, str) else json_dumps(new_content)
            payload = await workspace_runtime.replace_text_file(
                file_path=file_path,
                old_content=old_content,
                new_content=text,
            )
            return _json_result("file_edit", payload)
        except Exception as exc:
            return _tool_error("file_edit", exc)

    @tool("file_write")
    async def file_write(
        file_path: str,
        content: Any,
    ) -> str:
        """
        在 /mnt/user-data/workspace/ 或 /mnt/user-data/outputs/ 新建或整体覆盖文本文件。

        修改已有局部内容请使用 file_edit，避免无意覆盖文件。最终用户产物应写入 outputs/。
        """
        try:
            text = content if isinstance(content, str) else json_dumps(content)
            payload = await workspace_runtime.write_text_file(
                file_path=file_path,
                content=text,
                mode="overwrite",
            )
            return _json_result("file_write", payload)
        except Exception as exc:
            return _tool_error("file_write", exc)

    @tool("file_move")
    async def file_move(source_path: str, destination_path: str) -> str:
        """移动或重命名 /mnt/user-data/workspace、/mnt/user-data/outputs 下的文件或目录。目标路径不得存在，且其父目录必须已存在。"""
        try:
            payload = await workspace_runtime.move_path(
                source_path=source_path,
                destination_path=destination_path,
            )
            return _json_result("file_move", payload)
        except Exception as exc:
            return _tool_error("file_move", exc)

    @tool("file_copy")
    async def file_copy(source_path: str, destination_path: str) -> str:
        """复制 /mnt/user-data/{uploads,workspace,outputs} 或只读 /mnt/skills 技能路径下的单个文件到 /mnt/user-data/{workspace,outputs}。目标路径不得存在，且其父目录必须已存在。"""
        try:
            payload = await workspace_runtime.copy_path(
                source_path=source_path,
                destination_path=destination_path,
            )
            return _json_result("file_copy", payload)
        except Exception as exc:
            return _tool_error("file_copy", exc)

    @tool("file_delete")
    async def file_delete(file_path: str) -> str:
        """删除 /mnt/user-data/workspace 或 /mnt/user-data/outputs 下的单个文件。不能删除目录、根目录或隐藏工具结果缓存。"""
        try:
            payload = await workspace_runtime.delete_path(
                path=file_path,
                recursive=False,
                expected_type="file",
            )
            return _json_result("file_delete", payload)
        except Exception as exc:
            return _tool_error("file_delete", exc)

    @tool("file_glob")
    async def file_glob(
        pattern: str,
        path: str = "/mnt/user-data/workspace",
        max_results: int = _DEFAULT_GLOB_RESULTS,
    ) -> str:
        """在指定目录内递归按文件名匹配，例如 `*.json` 或 `README.*`。默认搜索 /mnt/user-data/workspace，最多返回 500 项。"""
        try:
            normalized_pattern = _validate_filename_pattern(pattern, field_name="pattern")
            if max_results < 1 or max_results > MAX_GLOB_RESULTS:
                return f"max_results 必须在 1-{MAX_GLOB_RESULTS} 之间"
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
            return _json_result("file_glob", payload)
        except Exception as exc:
            return _tool_error("file_glob", exc)

    @tool("file_grep")
    async def file_grep(
        pattern: str,
        path: str = "/mnt/user-data/workspace",
        glob: str = "**/*",
        max_results: int = _DEFAULT_GREP_RESULTS,
    ) -> str:
        """
        在文本文件中按字面内容查找，默认递归搜索 /mnt/user-data/workspace。pattern 可用 | 分隔多个关键词做 OR 匹配（如 error|warn|fail），不支持正则；glob 可限定文件范围，例如 `**/*.md`。

        最多返回 200 条匹配，每条最多 500 字符，整体正文受 CHAT_DATA_FILE_READ_MAX_CHARS 限制；二进制文件和超过 2 MiB 的文件会跳过。
        """
        try:
            patterns = [p for p in str(pattern or "").split("|") if p]
            if not patterns:
                return "pattern 不能为空"
            normalized_glob = _validate_relative_glob(glob, field_name="glob")
            if max_results < 1 or max_results > MAX_GREP_RESULTS:
                return f"max_results 必须在 1-{MAX_GREP_RESULTS} 之间"
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
            return _json_result("file_grep", payload)
        except Exception as exc:
            return _tool_error("file_grep", exc)

    @tool("dir_create")
    async def dir_create(path: str) -> str:
        """在 /mnt/user-data/workspace 或 /mnt/user-data/outputs 下创建目录。不能创建根目录或操作隐藏工具结果缓存。"""
        try:
            return _json_result("dir_create", await workspace_runtime.create_dir(path=path))
        except Exception as exc:
            return _tool_error("dir_create", exc)

    @tool("dir_list")
    async def dir_list(path: str = "workspace", max_depth: int = 2) -> str:
        """
        列出当前会话目录内容。path 必须是规范形式：/mnt/user-data/workspace、/mnt/user-data/outputs、/mnt/user-data/uploads 之一，或只读技能路径 /mnt/skills/<技能名>/...。

        max_depth 默认 2，范围 1-5；系统管理的 outputs/query_result 和 outputs/.tool-offload 不会出现在结果中。
        """
        try:
            if max_depth < 1 or max_depth > 5:
                return "max_depth 必须在 1-5 之间"
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
        except Exception as exc:
            return _tool_error("dir_list", exc)

    @tool("dir_delete")
    async def dir_delete(path: str, recursive: bool = False) -> str:
        """删除 /mnt/user-data/workspace 或 /mnt/user-data/outputs 下的目录。非空目录必须显式设置 recursive=true，不能删除根目录。"""
        try:
            payload = await workspace_runtime.delete_path(
                path=path,
                recursive=recursive,
                expected_type="dir",
            )
            return _json_result("dir_delete", payload)
        except Exception as exc:
            return _tool_error("dir_delete", exc)

    return [
        file_read,
        file_edit,
        file_write,
        file_move,
        file_copy,
        file_delete,
        file_glob,
        file_grep,
        dir_create,
        dir_list,
        dir_delete,
    ]


__all__ = ["create_filesystem_tools"]
