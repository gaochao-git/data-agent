"""文件文本抽取工具。"""

from __future__ import annotations

import io
from pathlib import Path

SUPPORTED_TEXT_EXTENSIONS = {".txt", ".docx", ".pdf"}


def decode_text_bytes(data: bytes) -> str:
    """解析纯文本字节。"""
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_docx_text(data: bytes) -> str:
    """解析 DOCX 文本。"""
    from docx import Document

    document = Document(io.BytesIO(data))
    lines = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells if cell.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def extract_pdf_text(data: bytes) -> str:
    """解析 PDF 文本。"""
    import fitz

    lines = []
    document = fitz.open(stream=data, filetype="pdf")
    try:
        for page in document:
            text = page.get_text()
            if text:
                lines.append(text.strip())
    finally:
        document.close()
    return "\n\n".join(lines)


def extract_text_from_file_bytes(filename: str, data: bytes) -> str:
    """按文件扩展名抽取文本内容。"""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_TEXT_EXTENSIONS:
        raise ValueError("仅支持 txt、docx、pdf 文件")
    if suffix == ".txt":
        return decode_text_bytes(data).strip()
    if suffix == ".docx":
        return extract_docx_text(data).strip()
    return extract_pdf_text(data).strip()
