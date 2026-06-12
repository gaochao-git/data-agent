from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

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
LEVEL_RE = re.compile(r"^\s*([一二三四五六七八九十1234])级\s*[：: ]?\s*(.+?)\s*$")
BULLET_RE = re.compile(r"^[\-•·●◦○]\s*")
SPACE_RE = re.compile(r"[ \t\u3000\xa0]+")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从制度体系图 PDF 抽取层级 JSON。")
    parser.add_argument("--pdf-path", required=True, help="制度体系图 PDF 路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    return parser


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_text(text: str) -> str:
    text = str(text or "").replace("\r", "\n").replace("\ufeff", "")
    text = SPACE_RE.sub(" ", text)
    return text.strip()


def level_to_int(level_text: str) -> int:
    mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}
    return mapping.get(str(level_text).strip(), 0)


def parse_level_line(line: str) -> tuple[int, str] | None:
    match = LEVEL_RE.match(normalize_text(line))
    if match:
        return level_to_int(match.group(1)), match.group(2).strip()
    return None


def clean_node_text(text: str) -> str:
    cleaned = BULLET_RE.sub("", normalize_text(text))
    return cleaned


def extract_lines_with_fitz(pdf_path: Path) -> list[str]:
    if fitz is None:
        return []
    lines: list[tuple[int, float, str]] = []
    document = fitz.open(pdf_path)
    try:
        for page_index, page in enumerate(document):
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    spans = [span.get("text", "") for span in line.get("spans", []) if str(span.get("text", "")).strip()]
                    text = clean_node_text("".join(spans))
                    if not text:
                        continue
                    bbox = line.get("bbox") or [0, 0, 0, 0]
                    lines.append((page_index + 1, float(bbox[1]), text))
    finally:
        document.close()
    lines.sort(key=lambda item: (item[0], item[1], item[2]))
    return [text for _, _, text in lines]


def extract_lines_with_pypdf(pdf_path: Path) -> list[str]:
    if PdfReader is None:
        raise RuntimeError("未安装可用的 PDF 解析库，请安装 PyMuPDF 或 pypdf")
    reader = PdfReader(str(pdf_path))
    lines: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for raw_line in text.splitlines():
            cleaned = clean_node_text(raw_line)
            if cleaned:
                lines.append(cleaned)
    return lines


def extract_lines(pdf_path: Path) -> list[str]:
    lines = extract_lines_with_fitz(pdf_path)
    if lines:
        return lines
    return extract_lines_with_pypdf(pdf_path)


def insert_path(tree: dict, path_nodes: list[str]) -> None:
    current = tree
    for index, node in enumerate(path_nodes):
        if index == len(path_nodes) - 1:
            current.setdefault(node, {})
        else:
            current = current.setdefault(node, {})


def parse_tree(lines: list[str]) -> tuple[dict, list[dict]]:
    tree: dict = {}
    unresolved: list[dict] = []
    level_nodes: dict[int, str] = {}
    current_level = 0
    for line_no, line in enumerate(lines, start=1):
        level_info = parse_level_line(line)
        if level_info:
            level, text = level_info
            if not text:
                unresolved.append({"line_no": line_no, "line": line, "reason": "empty_level_text"})
                continue
            level_nodes[level] = text
            for stale_level in list(level_nodes.keys()):
                if stale_level > level:
                    level_nodes.pop(stale_level, None)
            current_level = level
            insert_path(tree, [level_nodes[idx] for idx in sorted(level_nodes.keys())])
            continue

        cleaned = clean_node_text(line)
        if not cleaned:
            continue
        if current_level <= 0:
            unresolved.append({"line_no": line_no, "line": line, "reason": "no_level_context"})
            continue
        inferred_level = min(current_level + 1, 4)
        level_nodes[inferred_level] = cleaned
        for stale_level in list(level_nodes.keys()):
            if stale_level > inferred_level:
                level_nodes.pop(stale_level, None)
        insert_path(tree, [level_nodes[idx] for idx in sorted(level_nodes.keys())])
    return tree, unresolved


def main() -> int:
    args = build_parser().parse_args()
    pdf_path = Path(args.pdf_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    lines = extract_lines(pdf_path)
    tree, unresolved = parse_tree(lines)

    tree_path = output_dir / "hierarchy_catalog.json"
    summary_path = output_dir / "hierarchy_catalog_summary.json"
    unresolved_path = output_dir / "hierarchy_catalog_unresolved.json"

    write_json(tree_path, tree)
    write_json(
        summary_path,
        {
            "line_count": len(lines),
            "unresolved_count": len(unresolved),
            "root_keys": list(tree.keys()),
        },
    )
    write_json(unresolved_path, {"items": unresolved})

    print(f"tree 输出: {tree_path}")
    print(f"summary 输出: {summary_path}")
    print(f"unresolved 输出: {unresolved_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
