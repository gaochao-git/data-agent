"""将受控的会话上传文件转换为供 Agent 读取的 Markdown。"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from docx import Document as _load_docx_document
from markitdown import MarkItDown

from utils.logger import get_logger

from apps.chat_data_app.services.runtime_config import read_positive_int_env

logger = get_logger(__name__)

# 图片读取/注入的扩展名与单张大小上限（agent 视觉链路唯一口径）。
# 配置用 MB（CHAT_DATA_MAX_IMAGE_SIZE_MB，正整数），字节值由它派生；
# 模块级求值，改 env 后需重启进程。
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_IMAGE_SIZE_MB = read_positive_int_env("CHAT_DATA_MAX_IMAGE_SIZE_MB", 5)
MAX_IMAGE_SIZE = MAX_IMAGE_SIZE_MB * 1024 * 1024

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

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            convert_path = _resolve_markitdown_source(source_path, suffix, Path(tmp_dir))
            result = MarkItDown(enable_plugins=False).convert_local(convert_path)
        except Exception as exc:
            logger.error("MarkItDown 解析失败: path=%s error=%s", source_path, exc, exc_info=True)
            raise ValueError("文件解析失败") from exc

    content = str(result.text_content or "").strip()
    if not content:
        raise ValueError("文件未提取到可分析内容")
    return content


def _resolve_markitdown_source(source_path: Path, suffix: str, tmp_dir: Path) -> Path:
    """docx 含合并单元格时生成矩形化副本供解析，其余情况原样返回。

    GFM 表格语法无法表达 gridSpan/vMerge，markitdown(mammoth) 会丢弃合并延续格，
    导致行内格子数不足、后续列整体左移（串行）。展开策略与 pandoc 一致：
    锚点内容复制到每个被覆盖坐标，数据保真。
    """
    if suffix != ".docx":
        return source_path
    try:
        document = _load_docx_document(str(source_path))
        merged_tables = [table for table in document.tables if _table_has_merge(table)]
    except Exception as exc:
        logger.warning(
            "docx 表格预处理跳过（交由 markitdown 原样解析）: path=%s error=%s",
            source_path.name,
            exc,
        )
        return source_path
    if not merged_tables:
        return source_path
    try:
        for table in merged_tables:
            _rebuild_table_rectangular(document, table)
        normalized_path = tmp_dir / f"{source_path.stem or 'docx'}.docx"
        document.save(str(normalized_path))
        return normalized_path
    except Exception as exc:
        # 不规则网格（某行缺 tc）在 table.cell 读取时会 IndexError：矩形化
        # 失败必须回退原文件交给 markitdown 原样解析（串列也比整份失败好），
        # 不能让预处理异常冒泡成"文件解析失败"
        logger.warning(
            "docx 表格矩形化重建失败（回退原文件）: path=%s error=%s",
            source_path.name,
            exc,
        )
        return source_path


def _table_has_merge(table) -> bool:
    """python-docx 对合并坐标返回同一底层 tc，元素重复出现即存在合并。"""
    seen_tc_ids: set[int] = set()
    for row in table.rows:
        for cell in row.cells:
            tc_id = id(cell._tc)
            if tc_id in seen_tc_ids:
                return True
            seen_tc_ids.add(tc_id)
    return False


def _rebuild_table_rectangular(document, table) -> None:
    """将合并表重建为等尺寸普通表，合并内容按锚点重复填充。"""
    row_count = len(table.rows)
    col_count = len(table.columns)
    grid = [[table.cell(row, col).text for col in range(col_count)] for row in range(row_count)]

    rebuilt = document.add_table(rows=row_count, cols=col_count)
    rebuilt.style = table.style
    for row, values in enumerate(grid):
        for col, text in enumerate(values):
            _set_cell_paragraphs(rebuilt.cell(row, col), text)

    old_tbl = table._tbl
    old_tbl.addprevious(rebuilt._tbl)
    old_tbl.getparent().remove(old_tbl)


def _set_cell_paragraphs(cell, text: str) -> None:
    """按 cell.text 的换行语义回填（\\n 对应独立段落），保持段落结构与原表一致。"""
    lines = text.split("\n")
    cell.paragraphs[0].text = lines[0]
    for line in lines[1:]:
        cell.add_paragraph(line)
