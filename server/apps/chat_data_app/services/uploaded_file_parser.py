"""将受控的会话上传文件转换为供 Agent 读取的 Markdown。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from markitdown import MarkItDown

from utils.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_DOCUMENT_EXTENSIONS = frozenset({
    ".txt", ".md", ".sql", ".pdf", ".docx", ".xlsx", ".pptx",
})


async def parse_uploaded_file_to_markdown(source_path: Path) -> str:
    """Convert one already-authorized local upload without enabling plugins or network input."""
    return await asyncio.to_thread(_convert_local_upload, source_path)


def _convert_local_upload(source_path: Path) -> str:
    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise ValueError(f"不支持解析该文件类型: {suffix or '无扩展名'}")
    if not source_path.is_file():
        raise FileNotFoundError(f"文件不存在: {source_path.name}")

    try:
        result = MarkItDown(enable_plugins=False).convert_local(source_path)
    except Exception as exc:
        logger.error("MarkItDown 解析失败: path=%s error=%s", source_path, exc, exc_info=True)
        raise ValueError("文件解析失败") from exc

    content = str(result.text_content or "").strip()
    if not content:
        raise ValueError("文件未提取到可分析内容")
    return content
