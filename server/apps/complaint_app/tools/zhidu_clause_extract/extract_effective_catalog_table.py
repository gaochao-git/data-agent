from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from utils.logger import get_logger

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover
    fitz = None

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None


logger = get_logger(__name__)
EXPECTED_COLUMNS = ["序号", "主责部门", "制度名称", "制度文号", "发布时间", "制度级别"]
SPACE_RE = re.compile(r"[ \t\u3000\xa0]+")
HEADER_HINTS = ["序号", "主责部门", "制度名称", "发布时间", "制度级别"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从制度目录表 PDF 抽取有效目录清单。")
    parser.add_argument("--pdf-path", required=True, help="目录表 PDF 路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    return parser


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_excel(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_text(text: str) -> str:
    return SPACE_RE.sub(" ", str(text or "").replace("\r", "\n").replace("\ufeff", "")).strip()


def normalize_cell(value: str) -> str:
    return normalize_text(value).replace("\n", " ")


def is_header_row(row: list[str]) -> bool:
    text = " ".join(row)
    return all(hint in text for hint in HEADER_HINTS[:3])


def looks_like_title_row(row: list[str]) -> bool:
    if len(row) != 1:
        return False
    text = normalize_cell(row[0])
    return "制度目录" in text or "现行有效制度目录" in text


def normalize_row(row: list[str], page_no: int, row_no: int) -> dict | None:
    cleaned = [normalize_cell(cell) for cell in row]
    cleaned = [cell for cell in cleaned if cell != ""]
    if not cleaned:
        return None
    if looks_like_title_row(cleaned) or is_header_row(cleaned):
        return None
    values = cleaned[:]
    while len(values) < len(EXPECTED_COLUMNS):
        values.append("")
    values = values[: len(EXPECTED_COLUMNS)]
    record = {column: values[index] for index, column in enumerate(EXPECTED_COLUMNS)}
    record["page_no"] = page_no
    record["row_no"] = row_no
    return record


def extract_rows_with_fitz(pdf_path: Path) -> list[dict]:
    if fitz is None:
        return []
    rows: list[dict] = []
    document = fitz.open(pdf_path)
    try:
        for page_index, page in enumerate(document, start=1):
            finder = getattr(page, "find_tables", None)
            if not finder:
                continue
            tables = finder()
            extracted = getattr(tables, "tables", []) if tables else []
            for table in extracted:
                table_rows = table.extract() or []
                for row_index, row in enumerate(table_rows, start=1):
                    if not isinstance(row, list):
                        continue
                    normalized = normalize_row([str(cell or "") for cell in row], page_index, row_index)
                    if normalized:
                        rows.append(normalized)
    finally:
        document.close()
    return rows


def extract_rows_with_text(pdf_path: Path) -> list[dict]:
    if fitz is not None:
        return extract_rows_with_fitz_blocks(pdf_path)
    return extract_rows_with_pypdf_text(pdf_path)


def split_text_line(line: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"\s{2,}", normalize_text(line))]
    return [part for part in parts if part]


def extract_rows_with_fitz_blocks(pdf_path: Path) -> list[dict]:
    rows: list[dict] = []
    document = fitz.open(pdf_path)
    try:
        for page_index, page in enumerate(document, start=1):
            text = page.get_text("text")
            for row_index, line in enumerate(text.splitlines(), start=1):
                parts = split_text_line(line)
                if not parts:
                    continue
                normalized = normalize_row(parts, page_index, row_index)
                if normalized:
                    rows.append(normalized)
    finally:
        document.close()
    return rows


def extract_rows_with_pypdf_text(pdf_path: Path) -> list[dict]:
    if PdfReader is None:
        raise RuntimeError("未安装可用的 PDF 解析库，请安装 PyMuPDF 或 pypdf")
    rows: list[dict] = []
    reader = PdfReader(str(pdf_path))
    for page_index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for row_index, line in enumerate(text.splitlines(), start=1):
            parts = split_text_line(line)
            if not parts:
                continue
            normalized = normalize_row(parts, page_index, row_index)
            if normalized:
                rows.append(normalized)
    return rows


def deduplicate_rows(rows: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("序号", "")).strip(),
            str(row.get("制度名称", "")).strip(),
            str(row.get("发布时间", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def main() -> int:
    args = build_parser().parse_args()
    pdf_path = Path(args.pdf_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = extract_rows_with_fitz(pdf_path)
    extract_mode = "fitz_tables"
    if not rows:
        rows = extract_rows_with_text(pdf_path)
        extract_mode = "text_fallback"
    rows = deduplicate_rows(rows)

    jsonl_path = output_dir / "effective_catalog.jsonl"
    excel_path = output_dir / "effective_catalog.xlsx"
    summary_path = output_dir / "effective_catalog_summary.json"

    write_jsonl(jsonl_path, rows)
    write_excel(excel_path, rows)
    write_json(
        summary_path,
        {
            "row_count": len(rows),
            "extract_mode": extract_mode,
            "columns": EXPECTED_COLUMNS,
        },
    )

    print(f"jsonl 输出: {jsonl_path}")
    print(f"excel 输出: {excel_path}")
    print(f"summary 输出: {summary_path}")
    print(f"row_count: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
