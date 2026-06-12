from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
from docx import Document
from docx.shared import Pt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="扫描制度正文中是否存在指定关键词，并导出 Excel 与 Word 汇总报告。")
    parser.add_argument("--documents-jsonl", required=True, help="documents.jsonl 或 documents_mapped.jsonl 路径")
    parser.add_argument("--raw-text-dir", required=True, help="raw_texts 目录路径")
    parser.add_argument("--keywords-txt", required=True, help="关键词文件，一行一个关键词")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--output-prefix", default="policy_keyword_report", help="输出文件名前缀")
    parser.add_argument("--context-chars", type=int, default=80, help="命中上下文前后字符数，默认 80")
    parser.add_argument("--ignore-case", action="store_true", help="英文关键词是否忽略大小写")
    return parser


def load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            rows.append(json.loads(text))
    return rows


def load_keywords(path: Path) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        keyword = line.strip()
        if not keyword or keyword.startswith("#") or keyword in seen:
            continue
        keywords.append(keyword)
        seen.add(keyword)
    return keywords


def resolve_raw_text_path(row: dict[str, Any], raw_text_dir: Path) -> Path:
    raw_text_path = str(row.get("raw_text_path") or "").strip()
    if raw_text_path and Path(raw_text_path).expanduser().is_file():
        return Path(raw_text_path).expanduser().resolve()
    doc_id = str(row.get("doc_id") or "").strip()
    return raw_text_dir / f"{doc_id}.txt"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def find_keyword_matches(text: str, keyword: str, context_chars: int, ignore_case: bool) -> list[dict[str, Any]]:
    flags = re.IGNORECASE if ignore_case else 0
    matches: list[dict[str, Any]] = []
    for match in re.finditer(re.escape(keyword), text, flags):
        start = max(0, match.start() - context_chars)
        end = min(len(text), match.end() + context_chars)
        matches.append(
            {
                "position": match.start(),
                "matched_text": text[match.start() : match.end()],
                "context": normalize_text(text[start:end]),
            }
        )
    return matches


def scan_documents(documents: list[dict[str, Any]], raw_text_dir: Path, keywords: list[str], context_chars: int, ignore_case: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    match_rows: list[dict[str, Any]] = []
    document_rows: list[dict[str, Any]] = []
    keyword_stats = {keyword: {"keyword": keyword, "hit_document_count": 0, "hit_count": 0} for keyword in keywords}
    for document in documents:
        doc_id = str(document.get("doc_id") or "").strip()
        raw_text_path = resolve_raw_text_path(document, raw_text_dir)
        text = raw_text_path.read_text(encoding="utf-8") if raw_text_path.is_file() else ""
        doc_hit_count = 0
        doc_hit_keywords: list[str] = []
        for keyword in keywords:
            matches = find_keyword_matches(text, keyword, context_chars, ignore_case)
            if not matches:
                continue
            doc_hit_keywords.append(keyword)
            doc_hit_count += len(matches)
            keyword_stats[keyword]["hit_document_count"] += 1
            keyword_stats[keyword]["hit_count"] += len(matches)
            for index, item in enumerate(matches, start=1):
                match_rows.append(
                    {
                        "keyword": keyword,
                        "match_index": index,
                        "position": item["position"],
                        "matched_text": item["matched_text"],
                        "context": item["context"],
                        "doc_id": doc_id,
                        "category": str(document.get("category") or "").strip(),
                        "title": str(document.get("title") or "").strip(),
                        "file_name": str(document.get("file_name") or "").strip(),
                        "file_path": str(document.get("file_path") or "").strip(),
                        "raw_text_path": str(raw_text_path),
                    }
                )
        document_rows.append(
            {
                "doc_id": doc_id,
                "category": str(document.get("category") or "").strip(),
                "title": str(document.get("title") or "").strip(),
                "file_name": str(document.get("file_name") or "").strip(),
                "raw_text_path": str(raw_text_path),
                "keyword_hit_count": doc_hit_count,
                "hit_keywords": "、".join(doc_hit_keywords),
                "status": "ok" if raw_text_path.is_file() else "raw_text_missing",
            }
        )
    return match_rows, document_rows, list(keyword_stats.values())


def write_excel(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_docx(path: Path, summary: dict[str, Any], keyword_rows: list[dict[str, Any]], match_rows: list[dict[str, Any]]) -> None:
    document = Document()
    title = document.add_heading("制度关键词命中报告", level=0)
    for run in title.runs:
        run.font.size = Pt(16)
    document.add_heading("一、汇总", level=1)
    document.add_paragraph(f"制度总数：{summary['document_count']}")
    document.add_paragraph(f"关键词总数：{summary['keyword_count']}")
    document.add_paragraph(f"命中制度数：{summary['hit_document_count']}")
    document.add_paragraph(f"命中关键词数：{summary['hit_keyword_count']}")
    document.add_paragraph(f"命中次数：{summary['hit_count']}")
    document.add_heading("二、关键词命中情况", level=1)
    for row in keyword_rows:
        if int(row.get("hit_count") or 0) <= 0:
            continue
        document.add_paragraph(f"{row.get('keyword', '')}：命中制度 {row.get('hit_document_count', 0)} 份，命中 {row.get('hit_count', 0)} 次。")
    document.add_heading("三、命中明细", level=1)
    for index, row in enumerate(match_rows, start=1):
        document.add_heading(f"{index}. {row.get('keyword', '')} - {row.get('title') or row.get('file_name', '')}", level=2)
        document.add_paragraph(f"版块：{row.get('category', '')}")
        document.add_paragraph(f"文件：{row.get('file_name', '')}")
        document.add_paragraph(f"上下文：{row.get('context', '')}")
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)


def build_summary(documents: list[dict[str, Any]], keywords: list[str], match_rows: list[dict[str, Any]], document_rows: list[dict[str, Any]], keyword_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "document_count": len(documents),
        "keyword_count": len(keywords),
        "hit_document_count": sum(1 for row in document_rows if int(row.get("keyword_hit_count") or 0) > 0),
        "hit_keyword_count": sum(1 for row in keyword_rows if int(row.get("hit_count") or 0) > 0),
        "hit_count": len(match_rows),
        "raw_text_missing_count": sum(1 for row in document_rows if row.get("status") == "raw_text_missing"),
    }


def main() -> int:
    args = build_parser().parse_args()
    documents_path = Path(args.documents_jsonl).expanduser().resolve()
    raw_text_dir = Path(args.raw_text_dir).expanduser().resolve()
    keywords_path = Path(args.keywords_txt).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    documents = load_jsonl_rows(documents_path)
    keywords = load_keywords(keywords_path)
    match_rows, document_rows, keyword_rows = scan_documents(documents, raw_text_dir, keywords, int(args.context_chars), bool(args.ignore_case))
    summary = build_summary(documents, keywords, match_rows, document_rows, keyword_rows)
    write_excel(output_dir / f"{args.output_prefix}_matches.xlsx", match_rows)
    write_excel(output_dir / f"{args.output_prefix}_documents.xlsx", document_rows)
    write_excel(output_dir / f"{args.output_prefix}_keywords.xlsx", keyword_rows)
    write_json(output_dir / f"{args.output_prefix}_summary.json", summary)
    write_docx(output_dir / f"{args.output_prefix}.docx", summary, keyword_rows, match_rows)
    print(f"summary: {output_dir / f'{args.output_prefix}_summary.json'}")
    print(f"matches excel: {output_dir / f'{args.output_prefix}_matches.xlsx'}")
    print(f"documents excel: {output_dir / f'{args.output_prefix}_documents.xlsx'}")
    print(f"keywords excel: {output_dir / f'{args.output_prefix}_keywords.xlsx'}")
    print(f"docx: {output_dir / f'{args.output_prefix}.docx'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
