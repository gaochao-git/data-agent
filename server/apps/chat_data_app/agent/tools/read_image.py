"""Agent 工具：按需读取会话内的图片文件。

与 ``parse_document``（文档转 Markdown）互补：图片无法转文本阅读，
本工具只做准入校验并返回文本回执；真正的图片内容由
``ImageInjectionMiddleware`` 在下一次模型调用前以 content block 注入，
base64 不进入工具结果，避免 checkpoint 膨胀。

安全边界：
- ``file_path`` 必须落在当前会话的 uploads / workspace / outputs 下
  （沿用 ``resolve_workspace_path`` 的读约束），禁止越权读取。
"""

from __future__ import annotations

from langchain_core.tools import tool

from utils.logger import get_logger
from apps.chat_data_app.agent.workspace.runtime import (
    ConversationWorkspaceRuntime,
    resolve_workspace_path,
    to_visible_path,
)
from apps.chat_data_app.agent.workspace.uploaded_file_parser import IMAGE_EXTENSIONS, get_image_size_limit
from .common import tool_result_payload

logger = get_logger(__name__)


def create_read_image_tool(workspace_runtime: ConversationWorkspaceRuntime) -> list:
    """构建按需读取图片的单一工具。"""

    @tool("read_image", parse_docstring=True)
    async def read_image(file_path: str, intent: str = "") -> str:
        """读取当前会话中的图片文件（png/jpg/jpeg/webp/gif），把图片内容加载进视觉上下文。单张不超过 5MB。

        Args:
            file_path: 图片路径，形如 /mnt/user-data/uploads/<文件名> 或 /mnt/user-data/workspace/<文件名>，必须是本会话目录内真实存在的文件。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        root = workspace_runtime.workspace_root
        try:
            physical = resolve_workspace_path(root, file_path, writable=False)
        except ValueError as exc:
            return tool_result_payload("read_image", {"success": False, "error": f"file_path 无效: {exc}"})

        suffix = physical.suffix.lower().lstrip(".")
        if suffix not in IMAGE_EXTENSIONS:
            return tool_result_payload(
                "read_image",
                {
                    "success": False,
                    "error": f"不支持的图片格式: .{suffix or '(无后缀)'}，仅支持 "
                    + "/".join(sorted(IMAGE_EXTENSIONS)),
                },
            )
        if not physical.exists() or not physical.is_file():
            return tool_result_payload("read_image", {"success": False, "error": "file_path 指向的文件不存在"})

        try:
            size_bytes = physical.stat().st_size
        except OSError as exc:
            logger.error("read_image 读取文件信息失败: %s", exc, exc_info=True)
            return tool_result_payload("read_image", {"success": False, "error": "读取文件信息失败"})
        _, max_image_size = await get_image_size_limit()
        if size_bytes > max_image_size:
            return tool_result_payload(
                "read_image",
                {
                    "success": False,
                    "error": f"图片超过大小上限（{size_bytes} > {max_image_size} 字节）",
                },
            )

        return tool_result_payload(
            "read_image",
            {
                "success": True,
                "file_path": to_visible_path(root, physical),
                "size_bytes": size_bytes,
                "message": "图片已加载，将在下一轮自动附带到视觉上下文",
            },
        )

    return [read_image]
