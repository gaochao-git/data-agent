from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="根据原始 document_catlog.json 回填 documents/clauses 的分类信息。")
    parser.add_argument("--catalog-json", required=True, help="原始制度目录 JSON 路径")
    parser.add_argument("--documents-json", required=True, help="documents.jsonl 路径")
    parser.add_argument("--clauses-json", required=True, help="clauses.jsonl 路径")
    parser.add_argument("--output-dir", default="", help="输出目录；默认输出到输入文件同目录")
    parser.add_argument("--in-place", action="store_true", help="直接覆盖原 documents/clauses 文件，并生成 .bak 备份")
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
        catalog[category.strip()] = [str(file_name).strip() for file_name in file_names if str(file_name).strip()]
    return catalog


def build_catalog_name_index(catalog: dict[str, list[str]]) -> dict[str, list[str]]:
    name_index: dict[str, list[str]] = {}
    for category, file_names in catalog.items():
        for file_name in file_names:
            name_index.setdefault(file_name.lower(), []).append(category)
    return name_index


def load_jsonl_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def write_jsonl_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_category(file_name: str, file_path: str, catalog_name_index: dict[str, list[str]], current_category: str) -> tuple[str, str]:
    categories = catalog_name_index.get(file_name.lower(), [])
    if not categories:
        return current_category, "not_found"
    if len(categories) == 1:
        return categories[0], "catalog_exact"
    matched = [category for category in categories if category in str(file_path)]
    if len(matched) == 1:
        return matched[0], "catalog_path_match"
    return categories[0], "catalog_ambiguous_first"


def remap_documents(document_rows: list[dict], catalog_name_index: dict[str, list[str]]) -> tuple[list[dict], dict[str, dict]]:
    updated_rows: list[dict] = []
    document_meta: dict[str, dict] = {}
    for row in document_rows:
        file_name = str(row.get("file_name", "")).strip()
        file_path = str(row.get("file_path", "")).strip()
        current_category = str(row.get("category", "")).strip() or "未分类"
        mapped_category, mapping_source = resolve_category(file_name, file_path, catalog_name_index, current_category)
        updated_row = dict(row)
        updated_row["category"] = mapped_category
        updated_row["category_source"] = mapping_source
        updated_rows.append(updated_row)
        document_meta[str(updated_row.get("doc_id", "")).strip()] = {
            "category": mapped_category,
            "category_source": mapping_source,
            "file_name": file_name,
            "file_path": file_path,
        }
    return updated_rows, document_meta


def remap_clauses(clause_rows: list[dict], document_meta: dict[str, dict]) -> list[dict]:
    updated_rows: list[dict] = []
    for row in clause_rows:
        doc_id = str(row.get("doc_id", "")).strip()
        updated_row = dict(row)
        meta = document_meta.get(doc_id)
        if meta:
            updated_row["category"] = meta["category"]
            updated_row["category_source"] = meta["category_source"]
        updated_rows.append(updated_row)
    return updated_rows


def build_summary(document_rows: list[dict], updated_document_rows: list[dict]) -> dict:
    changed = 0
    not_found = 0
    source_counts: dict[str, int] = {}
    for before, after in zip(document_rows, updated_document_rows):
        if before.get("category") != after.get("category"):
            changed += 1
        source = str(after.get("category_source", "")).strip() or "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1
        if source == "not_found":
            not_found += 1
    return {
        "document_count": len(updated_document_rows),
        "changed_category_count": changed,
        "not_found_count": not_found,
        "category_source_counts": source_counts,
    }


def build_output_paths(documents_path: Path, clauses_path: Path, output_dir_arg: str, in_place: bool) -> tuple[Path, Path, Path]:
    if in_place:
        summary_path = documents_path.parent / "category_remap_summary.json"
        return documents_path, clauses_path, summary_path
    output_dir = Path(output_dir_arg).expanduser().resolve() if output_dir_arg.strip() else documents_path.parent
    return output_dir / "documents_mapped.jsonl", output_dir / "clauses_mapped.jsonl", output_dir / "category_remap_summary.json"


def backup_file(path: Path) -> None:
    backup_path = path.with_suffix(path.suffix + ".bak")
    shutil.copyfile(path, backup_path)


def main() -> int:
    args = build_parser().parse_args()
    catalog_path = Path(args.catalog_json).expanduser().resolve()
    documents_path = Path(args.documents_json).expanduser().resolve()
    clauses_path = Path(args.clauses_json).expanduser().resolve()

    catalog = load_catalog(catalog_path)
    catalog_name_index = build_catalog_name_index(catalog)
    document_rows = load_jsonl_rows(documents_path)
    clause_rows = load_jsonl_rows(clauses_path)

    updated_document_rows, document_meta = remap_documents(document_rows, catalog_name_index)
    updated_clause_rows = remap_clauses(clause_rows, document_meta)
    summary = build_summary(document_rows, updated_document_rows)

    output_documents_path, output_clauses_path, summary_path = build_output_paths(documents_path, clauses_path, args.output_dir, args.in_place)
    if args.in_place:
        backup_file(documents_path)
        backup_file(clauses_path)

    write_jsonl_rows(output_documents_path, updated_document_rows)
    write_jsonl_rows(output_clauses_path, updated_clause_rows)
    write_json(summary_path, summary)

    print(f"documents 输出: {output_documents_path}")
    print(f"clauses 输出: {output_clauses_path}")
    print(f"summary 输出: {summary_path}")
    print(f"文档总数: {summary['document_count']}")
    print(f"更新分类数: {summary['changed_category_count']}")
    print(f"未映射数: {summary['not_found_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
