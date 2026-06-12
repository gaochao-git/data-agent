from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="对账制度目录 JSON 与 PDF 目录差异。")
    parser.add_argument("--catalog-json", required=True, help="制度目录 JSON 路径")
    parser.add_argument("--pdf-dir", required=True, help="PDF 根目录，支持递归查找")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    return parser


# 读取制度目录 JSON。
def load_catalog(catalog_path: Path) -> dict[str, list[str]]:
    raw_data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(raw_data, dict):
        raise ValueError("catalog json 顶层必须是对象")
    catalog: dict[str, list[str]] = {}
    for category, file_names in raw_data.items():
        if not isinstance(category, str) or not isinstance(file_names, list):
            continue
        cleaned_names = [str(file_name).strip() for file_name in file_names if str(file_name).strip()]
        catalog[category.strip()] = cleaned_names
    return catalog


# 扫描 PDF 目录中的全部文件。
def scan_pdf_paths(pdf_dir: Path) -> list[Path]:
    return sorted(pdf_dir.rglob("*.pdf"), key=lambda path: str(path.relative_to(pdf_dir)))


def collect_catalog_names(catalog: dict[str, list[str]]) -> list[str]:
    names: list[str] = []
    for file_names in catalog.values():
        names.extend(file_names)
    return names


def build_duplicate_names(names: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return sorted([name for name, count in counts.items() if count > 1])


def write_json(output_path: Path, payload: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_lines(output_path: Path, lines: list[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines).strip()
    if content:
        content += "\n"
    output_path.write_text(content, encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    catalog_path = Path(args.catalog_json).expanduser().resolve()
    pdf_dir = Path(args.pdf_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    catalog = load_catalog(catalog_path)
    catalog_names = collect_catalog_names(catalog)
    catalog_name_set = set(catalog_names)
    pdf_paths = scan_pdf_paths(pdf_dir)
    pdf_names = [path.name for path in pdf_paths]
    pdf_name_set = set(pdf_names)

    json_only_names = sorted(catalog_name_set - pdf_name_set)
    pdf_only_names = sorted(pdf_name_set - catalog_name_set)
    duplicate_json_names = build_duplicate_names(catalog_names)
    duplicate_pdf_names = build_duplicate_names(pdf_names)

    summary = {
        "catalog_document_count": len(catalog_names),
        "catalog_unique_name_count": len(catalog_name_set),
        "pdf_document_count": len(pdf_paths),
        "pdf_unique_name_count": len(pdf_name_set),
        "json_only_count": len(json_only_names),
        "pdf_only_count": len(pdf_only_names),
        "duplicate_json_name_count": len(duplicate_json_names),
        "duplicate_pdf_name_count": len(duplicate_pdf_names),
        "json_only_names": json_only_names,
        "pdf_only_names": pdf_only_names,
        "duplicate_json_names": duplicate_json_names,
        "duplicate_pdf_names": duplicate_pdf_names,
    }

    write_json(output_dir / "catalog_diff.json", summary)
    write_lines(output_dir / "json_only.txt", json_only_names)
    write_lines(output_dir / "pdf_only.txt", pdf_only_names)
    write_lines(output_dir / "duplicate_json_names.txt", duplicate_json_names)
    write_lines(output_dir / "duplicate_pdf_names.txt", duplicate_pdf_names)

    print(f"输出目录: {output_dir}")
    print(f"json 文件数: {len(catalog_names)}")
    print(f"pdf 文件数: {len(pdf_paths)}")
    print(f"json有但pdf没有: {len(json_only_names)}")
    print(f"pdf有但json没有: {len(pdf_only_names)}")
    print(f"json重名数: {len(duplicate_json_names)}")
    print(f"pdf重名数: {len(duplicate_pdf_names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
