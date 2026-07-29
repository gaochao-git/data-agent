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
        char_offset: int = 0,
    ) -> str:
        """
        读取当前会话的 workspace、outputs、uploads 文本文件，或有权访问的技能只读 /mnt/skills 路径。

        每次最多读取 200 个源行，工具结果受服务端字符上限保护。正常阅读使用 offset 和
        limit；仅当上次结果的 truncated_lines 提示某行被截断时，使用该行的 offset、limit=1
        和 char_offset 继续读取该行剩余内容。

        技能路径（/mnt/skills/{system,user}/<技能名>/...）无需先经 skill_load 加载即可读取，
        解析按当前用户权限做磁盘/数据库校验（system=系统内置，user=该用户自己的技能）。
        skill_load 返回内容已包含 SKILL.md 正文，通常无需再用 file_read 读取 SKILL.md 本身；
        但读取技能的 references/scripts/assets 子文件时，直接用 skill_load 返回的 runtime_path 拼接即可。
        """
        try:
            if offset < 0:
                return "offset 必须大于等于 0"
            if limit < 1 or limit > MAX_READ_LIMIT:
                return f"limit 必须在 1-{MAX_READ_LIMIT} 之间"
            if char_offset < 0:
                return "char_offset 必须大于等于 0"
            if char_offset and limit != 1:
                return "使用 char_offset 续读时 limit 必须为 1"
            result = await workspace_read_text_file(
                workspace_runtime,
                file_path=file_path,
                offset=offset,
                limit=limit,
                char_offset=char_offset,
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
            if result.get("char_offset_out_of_range"):
                return (
                    f"char_offset {char_offset} 已到达或超出第 {result.get('line_number')} 行字符数"
                    f"（共 {result.get('line_chars')} 字符）"
                )
            start_line = int(result.get("start_line") or offset + 1)
            end_line = int(result.get("end_line") or offset + len(lines))
            truncated_lines = [item for item in result.get("truncated_lines") or [] if isinstance(item, dict)]
            truncated_text = ",".join(
                f"{item.get('line_number')}:{item.get('next_char_offset')}"
                for item in truncated_lines
            )
            next_offset = result.get("next_offset")
            continuation = f"\nchar_offset={char_offset}" if char_offset else ""
            next_offset_text = f"\nnext_offset={next_offset}" if next_offset is not None else ""
            truncated_text = f"\ntruncated=true\ntruncated_lines={truncated_text}" if truncated_text else ""
            return (
                "[file_read]\n"
                f"file_path={visible_path}\n"
                f"offset={offset}\nlimit={limit}{continuation}\nstart_line={start_line}\nend_line={end_line}\n"
                f"total_lines={total_lines if total_lines is not None else 'unknown'}\nhas_more={'true' if result.get('has_more') else 'false'}"
                f"{next_offset_text}{truncated_text}\n\n"
                f"{format_lines(lines, start_line)}"
            )
        except Exception as exc:
            logger.error("file_read 执行失败: %s", exc, exc_info=True)
            return f"file_read 执行失败: {exc}"

    return file_read
