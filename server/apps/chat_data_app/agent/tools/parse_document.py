"""Agent 工具：按需把用户上传的原始文件解析为 Markdown 写入 workspace。

上传与解析解耦：用户上传文件只落 ``uploads``（只读区），agent 在需要分析文件内容时
调用本工具，把文件解析为 ``<业务名>.md`` 写入 ``workspace``（可写区），随后用 ``file_read`` 读取。

安全边界：
- ``source_path`` 必须落在当前会话的 ``uploads`` 子目录下（只能解析本会话上传的文件，禁止越权读取他人/系统文件）。
- ``target_path`` 只允许写入 ``workspace`` 或 ``outputs``（沿用 ``resolve_workspace_path`` 的写约束）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from utils.logger import get_logger
from apps.chat_data_app.services.agent_run.conversation_workspace import (
    ConversationWorkspaceRuntime,
    resolve_workspace_path,
)
from apps.chat_data_app.services.agent_run.uploaded_file_parser import parse_uploaded_file_to_markdown

logger = get_logger(__name__)


def _json_result(tool_name: str, payload: dict[str, Any]) -> str:
    return f"[{tool_name}]\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def create_parse_document_tool(workspace_runtime: ConversationWorkspaceRuntime) -> list:
    """构建按需解析上传文件的单一工具。"""

    @tool("parse_document")
    async def parse_document(source_path: str, target_path: str) -> str:
        """把用户上传的原始文件解析为 Markdown 并写入 workspace。

        Args:
            source_path: 用户上传的原始文件路径，形如 /mnt/user-data/uploads/<原始文件名>。
                必须是本会话 uploads 下真实存在的文件。
            target_path: 解析后 Markdown 写入路径，形如 /mnt/user-data/workspace/<业务名>.md。
                文件名由你根据文件内容含义自行起一个清晰、可读的名字（不要带随机后缀）；
                只允许写入 workspace（或 outputs）。

        典型流程：先用 file_read 尝试直接读取用户上传的原始文件；若读到的内容不可读
        （xlsx/pptx/pdf/docx 等二进制），再对本文件调用本工具解析为 Markdown，最后用 file_read
        读取 target_path 的 Markdown 内容。
        """
        root = workspace_runtime.workspace_root
        try:
            src_physical = resolve_workspace_path(root, source_path, writable=False)
        except ValueError as exc:
            return _json_result("parse_document", {"success": False, "error": f"source_path 无效: {exc}"})

        uploads_physical_root = (Path(root) / "uploads").resolve()
        if uploads_physical_root not in src_physical.parents and src_physical != uploads_physical_root:
            return _json_result(
                "parse_document",
                {"success": False, "error": "source_path 必须是本会话 /mnt/user-data/uploads/ 下的文件"},
            )
        if not src_physical.exists() or not src_physical.is_file():
            return _json_result("parse_document", {"success": False, "error": "source_path 指向的文件不存在"})

        try:
            content = await parse_uploaded_file_to_markdown(src_physical)
        except Exception as exc:
            logger.error("parse_document 解析失败: %s", exc, exc_info=True)
            return _json_result("parse_document", {"success": False, "error": f"解析失败: {exc}"})

        try:
            # 走工作区运行时写入，与 file_write 等文件工具一致：
            # local 后端写本地、sandbox 后端同步到远程沙箱，否则 agent 后续 file_read 读不到。
            write_payload = await workspace_runtime.write_text_file(
                file_path=target_path,
                content=content,
                mode="overwrite",
            )
        except Exception as exc:
            logger.error("parse_document 写入失败: %s", exc, exc_info=True)
            return _json_result("parse_document", {"success": False, "error": f"写入失败: {exc}"})

        written_path = str(write_payload.get("file_path") or target_path)
        return _json_result(
            "parse_document",
            {
                "success": True,
                "target_path": written_path,
                "char_count": len(content),
            },
        )

    return [parse_document]
