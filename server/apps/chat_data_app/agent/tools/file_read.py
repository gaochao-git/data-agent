"""ChatData file_read tool."""

from __future__ import annotations

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.conversation_workspace import ConversationWorkspaceRuntime, workspace_read_text_file
from ...services.runtime_config import FILE_READ_MAX_CHARS
from .common import DEFAULT_READ_LIMIT, MAX_READ_LIMIT, format_lines

logger = get_logger(__name__)


def create_file_read_tool(workspace_runtime: ConversationWorkspaceRuntime):
    @tool("file_read")
    async def file_read(
        file_path: str,
        offset: int = 0,
        limit: int = DEFAULT_READ_LIMIT,
    ) -> str:
        """
        读取当前会话的 workspace、outputs、uploads 文本文件，或有权访问的技能只读 /mnt/skills 路径。

        默认读取 200 个源行，最多读取 500 行。每行正文最多返回 500 个字符，整页正文受
        CHAT_DATA_FILE_READ_MAX_CHARS 限制；超出的行内容直接截断，不支持续读。

        技能路径（/mnt/skills/<技能名>/...）无需先经 skill_load 加载即可读取，解析只认当前
        会话的只读技能快照。
        skill_load 返回内容已包含 SKILL.md 正文，通常无需再用 file_read 读取 SKILL.md 本身；
        但读取技能的 references/scripts/assets 子文件时，直接用 skill_load 返回的 runtime_path 拼接即可。

        outputs/.tool-results/<id>.json 或 .txt 是当前会话已卸载的工具结果缓存；它不会出现在工作区文件列表中，也不可通过 file_edit 或 present_artifacts 修改、呈现。
        """
        try:
            if offset < 0:
                return "offset 必须大于等于 0"
            if limit < 1 or limit > MAX_READ_LIMIT:
                return f"limit 必须在 1-{MAX_READ_LIMIT} 之间"
            result = await workspace_read_text_file(
                workspace_runtime,
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
                return f"[file_read]\nfile_path={visible_path}\nstatus=empty\n"
            if result.get("offset_out_of_range"):
                return f"offset {offset} 超出文件总行数（共 {total_lines} 行）"
            start_line = int(result.get("start_line") or offset + 1)
            end_line = int(result.get("end_line") or offset + len(lines))
            truncated_lines = [item for item in result.get("truncated_lines") or [] if isinstance(item, dict)]
            truncated_text = ",".join(
                str(item.get("line_number"))
                for item in truncated_lines
            )
            next_offset = result.get("next_offset")
            next_offset_text = f"\nnext_offset={next_offset}" if next_offset is not None else ""
            truncated_text = f"\ntruncated=true\ntruncated_lines={truncated_text}" if truncated_text else ""
            return (
                "[file_read]\n"
                f"file_path={visible_path}\n"
                f"offset={offset}\nlimit={limit}\nstart_line={start_line}\nend_line={end_line}\n"
                f"total_lines={total_lines if total_lines is not None else 'unknown'}\nhas_more={'true' if result.get('has_more') else 'false'}"
                f"{next_offset_text}{truncated_text}\n\n"
                f"{format_lines(lines, start_line)}"
            )
        except Exception as exc:
            logger.error("file_read 执行失败: %s", exc, exc_info=True)
            return f"file_read 执行失败: {exc}"

    return file_read
