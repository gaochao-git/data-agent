"""
文件解析器。

支持的文件类型：
- PDF: 提取文本
- 图片: 生成原图引用
- Word: 提取文本和表格
- Excel: 转换为 Markdown 表格
- 文本: 直接读取
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

from ..daos.uploaded_file_dao import UploadedFileDao

logger = get_logger(__name__)

UPLOADS_ROOT = Path(__file__).resolve().parents[3] / "uploads"
IMAGE_TYPES = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}


async def parse_file(file_id: str, dao: Optional[UploadedFileDao] = None) -> bool:
    """解析文件并生成 Markdown。"""
    file_dao = dao or UploadedFileDao()
    row = await file_dao.get_file(file_id)
    if not row:
        logger.error("文件不存在: %s", file_id)
        return False

    file_dir = UPLOADS_ROOT / row.storage_dir
    original_path = file_dir / f"original.{row.file_type}"
    if not original_path.exists():
        logger.error("原始文件不存在: %s", original_path)
        return False

    try:
        parse_filename = await _parse_by_type(file_dir, original_path, row.file_type, row.original_filename)
        status = "completed" if parse_filename else "failed"
        await file_dao.update_parse_status(file_id=file_id, status=status, parse_filename=parse_filename)
        logger.info("文件解析完成: %s, status=%s", file_id, status)
        return parse_filename is not None
    except Exception as exc:
        logger.error("文件解析异常: %s, %s", file_id, exc, exc_info=True)
        await file_dao.update_parse_status(file_id=file_id, status="failed")
        return False


async def _parse_by_type(file_dir: Path, original_path: Path, file_type: str, original_filename: str) -> Optional[str]:
    if file_type in IMAGE_TYPES:
        return await _parse_image(file_dir, original_path, file_type, original_filename)
    if file_type == "pdf":
        return await _parse_pdf(file_dir, original_path, original_filename)
    if file_type in {"txt", "md"}:
        return await _parse_text(file_dir, original_path, original_filename)
    if file_type in {"xls", "xlsx"}:
        return await _parse_excel(file_dir, original_path, original_filename)
    if file_type in {"doc", "docx"}:
        return await _parse_word(file_dir, original_path, original_filename)
    logger.warning("不支持的文件类型: %s", file_type)
    return None


async def _parse_image(file_dir: Path, original_path: Path, file_type: str, original_filename: str) -> Optional[str]:
    try:
        md_content = f"""# {original_filename}

![{original_filename}](original.{file_type})

> 当前服务未启用图片 OCR，请直接查看原图。
"""
        return _write_markdown(file_dir, md_content)
    except Exception as exc:
        logger.error("图片解析失败: %s", exc, exc_info=True)
        return None


async def _parse_pdf(file_dir: Path, original_path: Path, original_filename: str) -> Optional[str]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF 未安装，请运行: pip install PyMuPDF")
        return None

    md_lines = [f"# {original_filename}\n"]
    try:
        doc = fitz.open(original_path)
        for index, page in enumerate(doc, 1):
            md_lines.append(f"\n## 第 {index} 页\n")
            text = page.get_text()
            if text:
                md_lines.extend([text.strip(), ""])
        doc.close()
    except Exception as exc:
        logger.error("PDF解析失败: %s", exc)
        md_lines.append(f"\n> PDF解析失败: {str(exc)}\n")
    return _write_markdown(file_dir, "\n".join(md_lines))


async def _parse_text(file_dir: Path, original_path: Path, original_filename: str) -> Optional[str]:
    try:
        content = original_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = original_path.read_text(encoding="gbk")
        except Exception:
            return None
    if original_path.suffix.lower() == ".md":
        return _write_markdown(file_dir, content)
    return _write_markdown(file_dir, f"# {original_filename}\n\n{content}")


async def _parse_excel(file_dir: Path, original_path: Path, original_filename: str) -> Optional[str]:
    try:
        import openpyxl
    except ImportError:
        logger.error("openpyxl 未安装，请运行: pip install openpyxl")
        return None

    try:
        md_lines = [f"# {original_filename}\n"]
        workbook = openpyxl.load_workbook(original_path, data_only=True)
        for sheet_name in workbook.sheetnames:
            worksheet = workbook[sheet_name]
            md_lines.append(f"\n## 工作表: {sheet_name}\n")
            rows = list(worksheet.iter_rows(values_only=True))
            non_empty_rows = [row for row in rows if any(cell is not None for cell in row)]
            if not non_empty_rows:
                md_lines.append("（空工作表）\n")
                continue

            max_cols = _max_non_empty_columns(non_empty_rows)
            if max_cols == 0:
                md_lines.append("（空工作表）\n")
                continue

            for row_index, row in enumerate(non_empty_rows):
                cells = [_markdown_cell(row[col_index] if col_index < len(row) else None) for col_index in range(max_cols)]
                md_lines.append("| " + " | ".join(cells) + " |")
                if row_index == 0:
                    md_lines.append("| " + " | ".join(["---"] * max_cols) + " |")
            md_lines.append("")
        workbook.close()
        return _write_markdown(file_dir, "\n".join(md_lines))
    except Exception as exc:
        logger.error("Excel解析失败: %s", exc, exc_info=True)
        return None


async def _parse_word(file_dir: Path, original_path: Path, original_filename: str) -> Optional[str]:
    try:
        from docx import Document
    except ImportError:
        logger.error("python-docx 未安装，请运行: pip install python-docx")
        return None

    try:
        document = Document(original_path)
        md_lines = [f"# {original_filename}\n"]
        for element in document.element.body:
            if element.tag.endswith("p"):
                _append_matching_paragraph(md_lines, document, element)
            elif element.tag.endswith("tbl"):
                _append_matching_table(md_lines, document, element)
        return _write_markdown(file_dir, "\n".join(md_lines))
    except Exception as exc:
        logger.error("Word解析失败: %s", exc, exc_info=True)
        return None


def _append_matching_paragraph(md_lines: list[str], document: object, element: object) -> None:
    for paragraph in document.paragraphs:
        if paragraph._element != element:
            continue
        text = paragraph.text.strip()
        if text:
            md_lines.append(f"{_format_paragraph_prefix(paragraph)}{text}\n")
        return


def _append_matching_table(md_lines: list[str], document: object, element: object) -> None:
    for table in document.tables:
        if table._element != element:
            continue
        md_lines.append("")
        for row_index, row in enumerate(table.rows):
            cells = [_markdown_text_cell(cell.text) for cell in row.cells]
            md_lines.append("| " + " | ".join(cells) + " |")
            if row_index == 0:
                md_lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
        md_lines.append("")
        return


def _format_paragraph_prefix(paragraph: object) -> str:
    style = getattr(paragraph, "style", None)
    style_name = str(getattr(style, "name", "") or "")
    if not style_name.startswith("Heading"):
        return ""
    level_text = style_name[-1] if style_name[-1:].isdigit() else "2"
    return f"{'#' * int(level_text)} "


def _max_non_empty_columns(rows: list[tuple]) -> int:
    max_cols = 0
    for row in rows:
        for index in range(len(row) - 1, -1, -1):
            if row[index] is not None:
                max_cols = max(max_cols, index + 1)
                break
    return max_cols


def _markdown_cell(value: object) -> str:
    return _markdown_text_cell(str(value) if value is not None else "")


def _markdown_text_cell(value: str) -> str:
    return str(value or "").strip().replace("|", "\\|").replace("\n", " ")


def get_image_base64(file_id: str, storage_dir: str, file_type: str) -> Optional[str]:
    """获取图片的 Base64 编码，用于发送给多模态 LLM。"""
    if file_type not in IMAGE_TYPES:
        return None

    file_path = UPLOADS_ROOT / storage_dir / f"original.{file_type}"
    if not file_path.exists():
        return None

    data = base64.b64encode(file_path.read_bytes()).decode("utf-8")
    mime_type = "image/jpeg" if file_type == "jpg" else f"image/{file_type}"
    return f"data:{mime_type};base64,{data}"


def _write_markdown(file_dir: Path, content: str) -> str:
    parse_filename = "parsed.md"
    (file_dir / parse_filename).write_text(content, encoding="utf-8")
    return parse_filename
