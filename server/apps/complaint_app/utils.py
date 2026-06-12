import re
import os

# Markdown 表格相关正则
_MARKDOWN_TABLE_PATTERN = re.compile(r'(?:^|\n)(?:[ \t]*\|.*\|\s*(?:\n|$)){2,}')
_MARKDOWN_TABLE_SEPARATOR_PATTERN = re.compile(r'^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$')


def _split_markdown_cells(row: str) -> list:
    """Split a markdown table row into individual cell strings."""
    if row is None:
        return []
    return [cell.strip() for cell in row.strip().strip('|').split('|')]


def _markdown_table_to_rows(table_markdown: str) -> list:
    """Convert a markdown table into a 2D list of rows for CSV export."""
    if not table_markdown:
        return []

    lines = [line.rstrip() for line in table_markdown.strip().splitlines() if line.strip()]
    if not lines:
        return []

    rows = []
    for line in lines:
        if _MARKDOWN_TABLE_SEPARATOR_PATTERN.match(line):
            continue
        cells = _split_markdown_cells(line)
        if not cells:
            continue
        rows.append([
            cell.replace('\r', '').replace('\u3000', ' ').replace('\u00A0', ' ').strip()
            for cell in cells
        ])

    if not rows:
        return []

    column_count = max(len(row) for row in rows)
    normalized_rows = []
    for row in rows:
        padded = row[:column_count]
        if len(padded) < column_count:
            padded = padded + [''] * (column_count - len(padded))
        normalized_rows.append(padded)

    # 始终保留表头，同时过滤掉完全为空的数据行
    filtered_rows = [normalized_rows[0]]
    for row in normalized_rows[1:]:
        if any(cell for cell in row):
            filtered_rows.append(row)

    return filtered_rows if len(filtered_rows) > 0 else normalized_rows


def _strip_markdown_tables(text: str) -> str:
    """移除文本中的 Markdown 表格，以提升后续分析效果。"""
    if not text:
        return text

    cleaned = _MARKDOWN_TABLE_PATTERN.sub('\n', text)
    return re.sub(r'\n{3,}', '\n\n', cleaned)


def _extract_markdown_tables(text: str):
    """从文本中提取 Markdown 表格块，保留原始表格以便在摘要中附加展示。"""
    if not text:
        return []
    blocks = []
    for m in _MARKDOWN_TABLE_PATTERN.finditer(text):
        block = m.group(0).strip()
        if block:
            blocks.append(block)
    return blocks


def _ocr_file_max_size():
    _DEFAULT_MAX_UPLOAD_MB = 50
    _configured_upload_mb = os.getenv("MAX_UPLOAD_SIZE_MB") or os.getenv("MAX_UPLOAD_MB")
    try:
        _MAX_UPLOAD_SIZE_MB = int(_configured_upload_mb) if _configured_upload_mb else _DEFAULT_MAX_UPLOAD_MB
        if _MAX_UPLOAD_SIZE_MB <= 0:
            raise ValueError
    except (TypeError, ValueError):
        _MAX_UPLOAD_SIZE_MB = _DEFAULT_MAX_UPLOAD_MB
    return _MAX_UPLOAD_SIZE_MB


def _ocr_max_concurrency():
    _DEFAULT_OCR_MAX_CONCURRENCY = 10
    try:
        OCR_MAX_CONCURRENCY = int(os.getenv("OCR_MAX_CONCURRENCY", _DEFAULT_OCR_MAX_CONCURRENCY))
        if OCR_MAX_CONCURRENCY <= 0:
            raise ValueError
    except (TypeError, ValueError):
        OCR_MAX_CONCURRENCY = _DEFAULT_OCR_MAX_CONCURRENCY
    return OCR_MAX_CONCURRENCY


def _workflow_text_lenght_threshold():
    _DEFAULT_WORKFLOW_TEXT_LENGTH_THRESHOLD = 4096
    try:
        WORKFLOW_TEXT_LENGTH_THRESHOLD = int(
            os.getenv("WORKFLOW_TEXT_LENGTH_THRESHOLD", _DEFAULT_WORKFLOW_TEXT_LENGTH_THRESHOLD)
        )
        if WORKFLOW_TEXT_LENGTH_THRESHOLD <= 0:
            raise ValueError
    except (TypeError, ValueError):
        WORKFLOW_TEXT_LENGTH_THRESHOLD = _DEFAULT_WORKFLOW_TEXT_LENGTH_THRESHOLD
    return WORKFLOW_TEXT_LENGTH_THRESHOLD


def _task3_max_concurrency():
    _DEFAULT_TASK3_MAX_CONCURRENCY = 3
    try:
        TASK3_MAX_CONCURRENCY = int(os.getenv("TASK3_MAX_CONCURRENCY", _DEFAULT_TASK3_MAX_CONCURRENCY))
        if TASK3_MAX_CONCURRENCY <= 0:
            raise ValueError
    except (TypeError, ValueError):
        TASK3_MAX_CONCURRENCY = _DEFAULT_TASK3_MAX_CONCURRENCY
    return TASK3_MAX_CONCURRENCY
    
