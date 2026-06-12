"""DataAnalyze file_read tool."""

from __future__ import annotations

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.workspace_execution import SandboxExecutionSessionRef, sandbox_read_text_file
from .common import DEFAULT_READ_LIMIT, MAX_READ_LIMIT, format_lines

logger = get_logger(__name__)


def create_file_read_tool(workspace_root: str, execution_session_ref: SandboxExecutionSessionRef | None = None):
    @tool("file_read")
    async def file_read(file_path: str, offset: int = 0, limit: int = DEFAULT_READ_LIMIT) -> str:
        """
        读取当前会话目录中的文本文件，支持 workspace/outputs/uploads/runtime-skills。

        读取 runtime-skills 下的 reference/asset 前，必须先通过 skill_load 成功加载对应技能。
        skill_load 返回内容已经包含 SKILL.md 正文，通常不要用 file_read 读取 SKILL.md 本身。
        """
        try:
            if offset < 0:
                return "offset 必须大于等于 0"
            if limit < 1 or limit > MAX_READ_LIMIT:
                return f"limit 必须在 1-{MAX_READ_LIMIT} 之间"
            _ = workspace_root
            result = await sandbox_read_text_file(session_ref=execution_session_ref, file_path=file_path, offset=offset, limit=limit)
            visible_path = str(result.get("path") or file_path)
            if not result.get("exists"):
                return f"文件不存在: {file_path}"
            if result.get("is_dir"):
                return f"目标是目录，不是文件: {file_path}"
            if result.get("is_binary"):
                return f"暂不支持以文本方式读取二进制文件: {visible_path}"
            lines = [str(item) for item in result.get("lines") or []]
            total = int(result.get("total_lines") or 0)
            if total == 0:
                return f"[file_read]\nfile_path={visible_path}\nstatus=empty\n"
            if offset >= total:
                return f"offset {offset} 超出文件总行数（共 {total} 行）"
            start_line = int(result.get("start_line") or offset + 1)
            end_line = int(result.get("end_line") or offset + len(lines))
            return (
                "[file_read]\n"
                f"file_path={visible_path}\n"
                f"offset={offset}\nlimit={limit}\nstart_line={start_line}\nend_line={end_line}\n"
                f"total_lines={total}\nhas_more={'true' if result.get('has_more') else 'false'}\n\n"
                f"{format_lines(lines, start_line)}"
            )
        except Exception as exc:
            logger.error("file_read 执行失败: %s", exc, exc_info=True)
            return f"file_read 执行失败: {exc}"

    return file_read
