"""用户消息的多模态构造：附件占位文本与图片 content blocks 升级。

从 handlers 拆出的纯函数族——只依赖配置与文件服务，不接触 SSE 编排、
会话生命周期等 handler 状态。两条输出路径：

- 附件占位 ``[attachment] <虚拟路径>``：随消息持久化，历史会话回放靠它
  合成附件卡片（前端识别后隐藏正文中的该行）。
- 图片升级：当前模型能力位为 vlm 时，把合规图片转为 ``image_url``
  content block；不合规或读取失败的一律退回附件占位文本引用。
"""

from __future__ import annotations

import base64
from typing import Any

from utils.logger import get_logger
from .uploaded_file_service import (
    IMAGE_EXTENSIONS,
    MAX_IMAGES_PER_MESSAGE,
    MAX_IMAGE_SIZE,
    get_file_content,
)
from ..llm_config import get_chat_data_model_config

logger = get_logger(__name__)

IMAGE_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}


def build_file_attachment_text(file_attachments: list[dict[str, Any]]) -> str:
    """构造追加到用户消息末尾的附件路径占位。

    仅输出短占位 ``[attachment] /mnt/user-data/uploads/<原始文件>`` 或
    ``[attachment] /mnt/user-data/outputs/<已转文件>``，不再附加大段说明；
    agent 处理用户附件的策略（先 file_read，读不了再 parse_document）由
    ``agent/prompts/chat_data.py`` 的工作区流程块统一提供，重复说明反而冗余。
    前端识别该占位并在消息气泡中隐藏，避免查看历史会话时再次渲染冗长文本。
    """
    if not file_attachments:
        return ""
    lines = [""]
    for item in file_attachments:
        path = item.get("virtual_path") or item.get("parsed_path")
        if path:
            lines.append(f"[attachment] {path}")
    return "\n".join(lines)


def is_image_attachment(item: dict[str, Any]) -> bool:
    """判断附件是否为可作为 vision 输入的图片类型。"""
    file_type = str(item.get("file_type") or "").lower()
    if file_type not in IMAGE_EXTENSIONS or item.get("file_id") is None:
        return False
    try:
        return int(item.get("file_size") or 0) <= MAX_IMAGE_SIZE
    except (TypeError, ValueError):
        return False


async def build_multimodal_message_content(
    text_content: str,
    file_attachments: list[dict[str, Any]],
    *,
    user_id: str,
    thread_id: str,
    model_id: str | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """按本轮模型能力位把合规图片附件升级为 content blocks。

    返回 ``(消息内容, 未消化的附件列表)``：消息内容为纯字符串（无图片，
    或本轮模型未开启 vlm 时保持旧行为）或 ``[text_block, image_block...]``
    列表；超出数量/大小限制或读取失败的图片退回附件文本引用路径。
    """
    vlm_enabled = bool(get_chat_data_model_config(model_id).get("vlm"))
    if not vlm_enabled:
        return text_content, file_attachments

    image_items = [item for item in file_attachments if is_image_attachment(item)]
    leftover = [item for item in file_attachments if item not in image_items]
    admitted = image_items[:MAX_IMAGES_PER_MESSAGE]
    leftover.extend(image_items[MAX_IMAGES_PER_MESSAGE:])
    if not admitted:
        return text_content, leftover

    blocks: list[dict[str, Any]] = [{"type": "text", "text": text_content}] if text_content else []
    for item in admitted:
        data = await get_file_content(
            str(item["file_id"]), user_id=user_id, thread_id=thread_id
        )
        if not data:
            logger.warning("图片附件读取失败，退回文本引用: file_id=%s", item.get("file_id"))
            leftover.append(item)
            continue
        mime = IMAGE_MIME_TYPES.get(str(item.get("file_type") or "").lower(), "application/octet-stream")
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"},
        })
    if not any(block.get("type") == "image_url" for block in blocks):
        # 没有任何图片成功升级（如全部读取失败），保持纯字符串旧行为
        return text_content, leftover
    return blocks, leftover
