"""用户消息的多模态构造：附件占位文本与图片附件准入。

从 handlers 拆出的纯函数族——只依赖配置与文件服务元数据，不接触 SSE 编排、
会话生命周期等 handler 状态。两条输出：

- 附件占位 ``[attachment] <虚拟路径>``：随消息持久化，历史会话回放靠它
  合成附件卡片（前端识别后隐藏正文中的该行）。
- 图片准入：``select_vision_attachments`` 按类型/大小/数量规则选出可注入
  视觉的图片附件虚拟路径，调用方写入消息 additional_kwargs 标记；真正的
  base64 装载由 ``ImageInjectionMiddleware`` 在模型请求层按当轮模型能力位
  完成——state/checkpoint/事件表全程不出现 base64。
"""

from __future__ import annotations

from typing import Any

from utils.logger import get_logger
from .uploaded_file_service import (
    IMAGE_EXTENSIONS,
    MAX_IMAGES_PER_MESSAGE,
    MAX_IMAGE_SIZE,
)

logger = get_logger(__name__)


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


def select_vision_attachments(file_attachments: list[dict[str, Any]]) -> list[str]:
    """选出可注入视觉的图片附件虚拟路径（纯元数据判断，不读文件、不判 vlm）。

    准入规则：类型 ∈ IMAGE_EXTENSIONS、携带 file_id、大小 ≤ MAX_IMAGE_SIZE，
    数量上限 MAX_IMAGES_PER_MESSAGE。返回虚拟路径列表（可能为空）；调用方
    将其写入消息 additional_kwargs 的 ``vision_attachments`` 标记，由
    ImageInjectionMiddleware 在模型请求层按当轮 vlm 能力位装载图片。
    """
    paths: list[str] = []
    for item in file_attachments:
        if len(paths) >= MAX_IMAGES_PER_MESSAGE:
            break
        if not is_image_attachment(item):
            continue
        path = item.get("virtual_path") or item.get("parsed_path")
        if path:
            paths.append(str(path))
    return paths
