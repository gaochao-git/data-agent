"""Utilities for handling complaint chat attachments."""
from __future__ import annotations

import os
import uuid
from typing import Tuple


CHAT_ALLOWED_EXTENSIONS = {"doc", "docx"}


def ensure_chat_attachment_dir(base_dir: str, conversation_id: str, filename: str) -> Tuple[str, str]:
    """Create the directory structure for storing a chat attachment.

    Returns a tuple of (attachment_dir, saved_filename).
    """
    upload_dir = os.path.join(base_dir, conversation_id, uuid.uuid4().hex)
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir, filename


def build_structured_query(filename: str, markdown_text: str, question: str) -> str:
    sections = [
        "【上传文件名称】",
        filename,
        "",
        "【附件内容（含 OCR 结果）】",
        markdown_text.strip(),
        "",
        "【原始提问】",
        question.strip(),
    ]
    return "\n".join(sections).strip()


def is_allowed_chat_attachment(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in CHAT_ALLOWED_EXTENSIONS
