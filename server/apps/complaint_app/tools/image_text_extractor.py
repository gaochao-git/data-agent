"""OCR provider dispatcher for complaint_app tools (LLM-only)."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, TypedDict

from dotenv import load_dotenv

load_dotenv()

class ImageTextResult(TypedDict, total=False):
    """Standardized result schema for image→text extraction."""

    text_content: str
    cache_hit: bool
    sha256: Optional[str]
    upload_file_id: Optional[str]
    provider: str


from .image_text_extractor_llm import recognize_image as _provider_recognize_image
from .image_text_extractor_llm import recognize_image_async as _provider_recognize_image_async


# 统一的OCR提取提示词，可通过环境变量 `OCR_PROMPT` 覆盖
_DEFAULT_OCR_PROMPT = (
    "请从我给你的这张图片中提取所有的文字内容，保持按照原有的段落顺序和换行来组织文本即可；不要补充、修改或省略文字内容；如果是交易流水表格，需要使用markdown的方式表达。（不识别条形码）"
)
OCR_PROMPT = os.getenv("OCR_PROMPT") or _DEFAULT_OCR_PROMPT


def get_ocr_prompt() -> str:
    return OCR_PROMPT


def current_provider() -> str:
    """Return the name of the active OCR provider."""

    return "llm"


def recognize_image(image_path: str, *, force: bool = True, model: Optional[str] = None) -> ImageTextResult:
    """Extract text from an image via the configured provider.

    Args:
        image_path: Absolute or relative path to a local image file.
        force: If True, bypass provider-level cache when supported.

    Returns:
        ImageTextResult with unified fields:
        - text_content: str (may contain an error message on failure)
        - cache_hit: bool (False when provider does not support caching)
        - sha256: optional str digest (LLM provider)
        - upload_file_id: optional str (legacy field, usually empty)
        - provider: str active provider name ("llm")
    """

    raw = _provider_recognize_image(image_path, force=force, prompt=get_ocr_prompt(), model=model) or {}
    return ImageTextResult(
        text_content=str(raw.get("text_content") or ""),
        cache_hit=bool(raw.get("cache_hit")) if "cache_hit" in raw else False,
        sha256=raw.get("sha256"),
        upload_file_id=raw.get("upload_file_id"),
        provider="llm",
    )


async def recognize_image_async(image_path: str, *, force: bool = True, model: Optional[str] = None) -> ImageTextResult:
    """异步提取图片文本（OCR）。"""
    raw = await _provider_recognize_image_async(image_path, force=force, prompt=get_ocr_prompt(), model=model) or {}
    return ImageTextResult(
        text_content=str(raw.get("text_content") or ""),
        cache_hit=bool(raw.get("cache_hit")) if "cache_hit" in raw else False,
        sha256=raw.get("sha256"),
        upload_file_id=raw.get("upload_file_id"),
        provider="llm",
    )
