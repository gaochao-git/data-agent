"""Utilities for converting document attachments to Markdown with OCR text."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Dict, Tuple
from utils.logger import get_logger
logger = get_logger(__name__)

from .image_text_extractor import recognize_image


class DocumentConversionError(RuntimeError):
    """Raised when a document cannot be converted to Markdown."""


def _run_pandoc(input_path: str, output_markdown: str, media_dir: str) -> None:
    """Run pandoc to convert the document and extract media."""

    if os.path.exists(media_dir):
        shutil.rmtree(media_dir)
    os.makedirs(media_dir, exist_ok=True)

    cmd = [
        "pandoc",
        input_path,
        "--to",
        "gfm",
        "--extract-media",
        media_dir,
        "-o",
        output_markdown,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.error(
            f"pandoc 转换失败 {exc}",
            exc_info=True
        )
        raise DocumentConversionError("文档读取失败：暂无法解析该附件，请确认文件为未加密的 doc/docx 并重试") from exc


def convert_doc_to_markdown(input_path: str, output_dir: str) -> Tuple[str, str]:
    """Convert the document to Markdown using pandoc.

    Returns (markdown_path, media_dir).
    """

    markdown_path = os.path.join(output_dir, "content.md")
    media_dir = os.path.join(output_dir, "media")

    _run_pandoc(input_path, markdown_path, media_dir)

    if not os.path.exists(markdown_path):
        raise DocumentConversionError("未生成 Markdown 文件")

    return markdown_path, media_dir


def _resolve_image_path(markdown_dir: str, media_dir: str, image_ref: str) -> str | None:
    cleaned_ref = image_ref.split()[0].strip()
    candidate = os.path.normpath(os.path.join(markdown_dir, cleaned_ref))
    if os.path.exists(candidate):
        return candidate
    if media_dir:
        candidate = os.path.normpath(os.path.join(media_dir, os.path.basename(cleaned_ref)))
        if os.path.exists(candidate):
            return candidate
    return None


def replace_images_with_ocr(markdown_path: str, media_dir: str) -> str:
    """Replace image references in Markdown with OCR text."""

    with open(markdown_path, "r", encoding="utf-8") as md_file:
        content = md_file.read()

    markdown_dir = os.path.dirname(markdown_path)
    image_pattern = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
    cache: Dict[str, str] = {}

    def replace_match(match: re.Match[str]) -> str:
        ref = match.group(1)
        image_path = _resolve_image_path(markdown_dir, media_dir, ref)
        if not image_path or not os.path.exists(image_path):
            return ""

        cached_text = cache.get(image_path)
        if cached_text is None:
            try:
                result = recognize_image(image_path)
            except Exception as exc:  # pylint: disable=broad-except
                cached_text = f"[图片OCR失败: {exc}]"
            else:
                text = (result or {}).get("text_content") or ""
                cached_text = text.strip() or "[图片OCR无结果]"
            cache[image_path] = cached_text

        return cached_text

    updated_content = image_pattern.sub(replace_match, content)

    with open(markdown_path, "w", encoding="utf-8") as md_file:
        md_file.write(updated_content)

    return updated_content
