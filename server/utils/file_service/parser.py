"""
文件解析器

支持的文件类型:
- PDF: 提取文本
- 图片: OCR 文字识别
- Word: 提取文本和表格
- Excel: 转换为Markdown表格
- 文本: 直接读取
"""

import os
import base64
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select, update as sa_update

from utils.logger import get_logger
from utils.lang_util.database import get_session
from utils.lang_util.models import UploadedFile

logger = get_logger(__name__)

# 上传文件根目录
UPLOADS_ROOT = Path(__file__).resolve().parent.parent.parent / "uploads"

# 支持的图片类型
IMAGE_TYPES = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}

# 支持的文档类型
DOC_TYPES = {"pdf", "doc", "docx", "xls", "xlsx", "txt", "md"}


async def parse_file(file_id: str) -> bool:
    """
    解析文件并生成Markdown

    Args:
        file_id: 文件ID

    Returns:
        是否成功
    """
    # 获取文件信息
    async with get_session() as session:
        result = await session.execute(
            select(UploadedFile).where(UploadedFile.file_id == file_id)
        )
        row = result.scalar_one_or_none()

    if not row:
        logger.error(f"文件不存在: {file_id}")
        return False

    storage_dir = row.storage_dir
    file_type = row.file_type
    original_filename = row.original_filename

    file_dir = UPLOADS_ROOT / storage_dir
    original_path = file_dir / f"original.{file_type}"

    if not original_path.exists():
        logger.error(f"原始文件不存在: {original_path}")
        return False

    # 根据文件类型选择解析方式
    parse_filename = None
    try:
        if file_type in IMAGE_TYPES:
            parse_filename = await _parse_image(file_dir, original_path, file_type, original_filename)
        elif file_type == "pdf":
            parse_filename = await _parse_pdf(file_dir, original_path, original_filename)
        elif file_type in {"txt", "md"}:
            parse_filename = await _parse_text(file_dir, original_path, original_filename)
        elif file_type in {"xls", "xlsx"}:
            parse_filename = await _parse_excel(file_dir, original_path, original_filename)
        elif file_type in {"doc", "docx"}:
            parse_filename = await _parse_word(file_dir, original_path, original_filename)
        else:
            # 不支持的类型，标记为失败
            logger.warning(f"不支持的文件类型: {file_type}")
            parse_filename = None

        # 更新数据库
        status = "completed" if parse_filename else "failed"
        async with get_session() as session:
            await session.execute(
                sa_update(UploadedFile)
                .where(UploadedFile.file_id == file_id)
                .values(parse_status=status, parse_filename=parse_filename, update_time=datetime.now())
            )

        logger.info(f"文件解析完成: {file_id}, status={status}")
        return parse_filename is not None

    except Exception as e:
        logger.error(f"文件解析异常: {file_id}, {e}", exc_info=True)
        async with get_session() as session:
            await session.execute(
                sa_update(UploadedFile)
                .where(UploadedFile.file_id == file_id)
                .values(parse_status="failed", update_time=datetime.now())
            )
        return False


async def _parse_image(file_dir: Path, original_path: Path, file_type: str, original_filename: str) -> Optional[str]:
    """
    解析图片文件 - 使用 LLM OCR 识别文字
    """
    try:
        from apps.complaint_app.tools.image_text_extractor_llm import recognize_image
    except ImportError:
        logger.error("OCR 模块导入失败")
        return None

    try:
        # 调用 LLM OCR 识别
        result = recognize_image(str(original_path), prompt="请识别图片中的所有文字内容")
        ocr_text = result.get("text_content", "")

        if not ocr_text or ocr_text.startswith("错误") or ocr_text.startswith("OCR识别失败"):
            # OCR 失败，生成简单引用
            md_content = f"""# {original_filename}

![{original_filename}](original.{file_type})

> 图片文字识别失败，请直接查看原图。
"""
        else:
            # OCR 成功
            md_content = f"""# {original_filename}

## 识别内容

{ocr_text}
"""

        # 保存Markdown
        parse_filename = "parsed.md"
        md_path = file_dir / parse_filename
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        return parse_filename

    except Exception as e:
        logger.error(f"图片OCR解析失败: {e}", exc_info=True)
        return None


async def _parse_pdf(file_dir: Path, original_path: Path, original_filename: str) -> Optional[str]:
    """
    解析PDF文件

    使用 PyMuPDF (fitz) 提取文本
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF 未安装，请运行: pip install PyMuPDF")
        return None

    md_lines = [f"# {original_filename}\n"]

    try:
        doc = fitz.open(original_path)
        for i, page in enumerate(doc, 1):
            md_lines.append(f"\n## 第 {i} 页\n")

            # 提取文本
            text = page.get_text()
            if text:
                md_lines.append(text.strip())
                md_lines.append("")

        doc.close()

    except Exception as e:
        logger.error(f"PDF解析失败: {e}")
        md_lines.append(f"\n> PDF解析失败: {str(e)}\n")

    # 保存Markdown
    parse_filename = "parsed.md"
    md_path = file_dir / parse_filename
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    return parse_filename


async def _parse_text(file_dir: Path, original_path: Path, original_filename: str) -> Optional[str]:
    """
    解析纯文本文件
    """
    try:
        with open(original_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        # 尝试其他编码
        try:
            with open(original_path, "r", encoding="gbk") as f:
                content = f.read()
        except Exception:
            return None

    # 如果是md文件，直接复制
    if original_path.suffix.lower() == ".md":
        parse_filename = "parsed.md"
        md_path = file_dir / parse_filename
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)
        return parse_filename

    # txt文件，添加标题
    md_content = f"# {original_filename}\n\n{content}"

    parse_filename = "parsed.md"
    md_path = file_dir / parse_filename
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    return parse_filename


async def _parse_excel(file_dir: Path, original_path: Path, original_filename: str) -> Optional[str]:
    """
    解析 Excel 文件 - 转换为 Markdown 表格
    """
    try:
        import openpyxl
    except ImportError:
        logger.error("openpyxl 未安装，请运行: pip install openpyxl")
        return None

    try:
        md_lines = [f"# {original_filename}\n"]

        wb = openpyxl.load_workbook(original_path, data_only=True)

        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            md_lines.append(f"\n## 工作表: {sheet_name}\n")

            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                md_lines.append("（空工作表）\n")
                continue

            # 过滤全空行
            non_empty_rows = [row for row in rows if any(cell is not None for cell in row)]
            if not non_empty_rows:
                md_lines.append("（空工作表）\n")
                continue

            # 确定实际列数（去除尾部空列）
            max_cols = 0
            for row in non_empty_rows:
                for i in range(len(row) - 1, -1, -1):
                    if row[i] is not None:
                        max_cols = max(max_cols, i + 1)
                        break

            if max_cols == 0:
                md_lines.append("（空工作表）\n")
                continue

            # 生成 Markdown 表格
            for row_idx, row in enumerate(non_empty_rows):
                cells = []
                for col_idx in range(max_cols):
                    cell_value = row[col_idx] if col_idx < len(row) else None
                    cell_str = str(cell_value) if cell_value is not None else ""
                    # 转义管道符
                    cell_str = cell_str.replace("|", "\\|")
                    cells.append(cell_str)

                md_lines.append("| " + " | ".join(cells) + " |")

                # 第一行后添加分隔符
                if row_idx == 0:
                    md_lines.append("| " + " | ".join(["---"] * max_cols) + " |")

            md_lines.append("")

        wb.close()

        # 保存 Markdown
        parse_filename = "parsed.md"
        md_path = file_dir / parse_filename
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))

        return parse_filename

    except Exception as e:
        logger.error(f"Excel 解析失败: {e}", exc_info=True)
        return None


async def _parse_word(file_dir: Path, original_path: Path, original_filename: str) -> Optional[str]:
    """
    解析 Word 文件 - 提取文本和表格
    """
    try:
        from docx import Document
    except ImportError:
        logger.error("python-docx 未安装，请运行: pip install python-docx")
        return None

    try:
        md_lines = [f"# {original_filename}\n"]

        doc = Document(original_path)

        for element in doc.element.body:
            # 处理段落
            if element.tag.endswith('p'):
                for para in doc.paragraphs:
                    if para._element == element:
                        text = para.text.strip()
                        if text:
                            # 检查是否是标题样式
                            if para.style and para.style.name.startswith('Heading'):
                                level = para.style.name[-1] if para.style.name[-1].isdigit() else '2'
                                md_lines.append(f"{'#' * int(level)} {text}\n")
                            else:
                                md_lines.append(f"{text}\n")
                        break

            # 处理表格
            elif element.tag.endswith('tbl'):
                for table in doc.tables:
                    if table._element == element:
                        md_lines.append("")
                        for row_idx, row in enumerate(table.rows):
                            cells = []
                            for cell in row.cells:
                                cell_text = cell.text.strip().replace("|", "\\|").replace("\n", " ")
                                cells.append(cell_text)
                            md_lines.append("| " + " | ".join(cells) + " |")

                            # 第一行后添加分隔符
                            if row_idx == 0:
                                md_lines.append("| " + " | ".join(["---"] * len(cells)) + " |")

                        md_lines.append("")
                        break

        # 保存 Markdown
        parse_filename = "parsed.md"
        md_path = file_dir / parse_filename
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))

        return parse_filename

    except Exception as e:
        logger.error(f"Word 解析失败: {e}", exc_info=True)
        return None


def get_image_base64(file_id: str, storage_dir: str, file_type: str) -> Optional[str]:
    """
    获取图片的Base64编码（用于发送给多模态LLM）

    Args:
        file_id: 文件ID
        storage_dir: 存储目录
        file_type: 文件类型

    Returns:
        Base64编码的图片，格式: data:image/{type};base64,{data}
    """
    if file_type not in IMAGE_TYPES:
        return None

    file_path = UPLOADS_ROOT / storage_dir / f"original.{file_type}"
    if not file_path.exists():
        return None

    with open(file_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")

    mime_type = f"image/{file_type}"
    if file_type == "jpg":
        mime_type = "image/jpeg"

    return f"data:{mime_type};base64,{data}"
