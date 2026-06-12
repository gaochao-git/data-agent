from __future__ import annotations

import argparse
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd


EXPECTED_COLUMNS = ["序号", "名称", "起草部门", "生效日期", "时效性", "附件数", "上传者"]
SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx", ".wps"}
VERSION_SUFFIX_RE = re.compile(r"(v|V)\s*\d+(\.\d+)*$")
MULTI_SPACE_RE = re.compile(r"\s{2,}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将制度清单 TXT 结构化，并与本地文档目录做对账。")
    parser.add_argument("--catalog-txt", required=True, help="制度清单 txt 路径，一行一条记录")
    parser.add_argument("--local-dir", required=True, help="本地文档目录，支持递归扫描子目录")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--fuzzy-threshold", type=float, default=0.88, help="模糊匹配阈值，默认 0.88")
    return parser


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_excel(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False)


# 统一归一化制度名称，降低名称差异造成的误判。
def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "").strip())
    text = Path(text).stem
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("【", "[").replace("】", "]")
    text = text.replace("_", "").replace("-", "").replace("—", "")
    text = text.replace(" ", "").replace("\u3000", "")
    text = VERSION_SUFFIX_RE.sub("", text)
    return text.strip()


# 尝试按 tab、多个空格或竖线切分一行。
def split_catalog_line(line: str) -> list[str]:
    source = str(line or "").strip()
    if not source:
        return []
    if "\t" in source:
        parts = [part.strip() for part in source.split("\t")]
    elif "|" in source:
        parts = [part.strip() for part in source.split("|")]
    else:
        parts = [part.strip() for part in MULTI_SPACE_RE.split(source)]
    return [part for part in parts if part != ""]


def is_header_line(parts: list[str]) -> bool:
    if len(parts) < 2:
        return False
    joined = "".join(parts)
    return "名称" in joined and "起草部门" in joined


# 将一行清单解析成结构化记录；不足 7 列时保底入 raw_line。
def parse_catalog_line(line: str, line_no: int) -> dict:
    parts = split_catalog_line(line)
    if not parts:
        return {"line_no": line_no, "parse_status": "empty", "raw_line": line}
    if is_header_line(parts):
        return {"line_no": line_no, "parse_status": "header", "raw_line": line}
    row = {"line_no": line_no, "raw_line": line}
    for idx, column in enumerate(EXPECTED_COLUMNS):
        row[column] = parts[idx] if idx < len(parts) else ""
    row["extra_fields"] = parts[len(EXPECTED_COLUMNS):] if len(parts) > len(EXPECTED_COLUMNS) else []
    row["parse_status"] = "ok" if len(parts) >= 2 else "invalid"
    row["normalized_name"] = normalize_name(row.get("名称", ""))
    return row


def load_catalog_rows(path: Path) -> tuple[list[dict], list[dict]]:
    parsed_rows: list[dict] = []
    invalid_rows: list[dict] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        row = parse_catalog_line(line, index)
        status = str(row.get("parse_status", "")).strip()
        if status == "ok":
            parsed_rows.append(row)
        elif status not in {"empty", "header"}:
            invalid_rows.append(row)
    return parsed_rows, invalid_rows


def scan_local_files(local_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(local_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        rows.append(
            {
                "file_name": path.name,
                "relative_path": str(path.relative_to(local_dir)),
                "ext": path.suffix.lower(),
                "stem": path.stem,
                "normalized_name": normalize_name(path.name),
            }
        )
    return rows


def build_exact_index(local_rows: list[dict]) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for row in local_rows:
        key = str(row.get("normalized_name", "")).strip()
        index.setdefault(key, []).append(row)
    return index


def build_match_row(catalog_row: dict, local_row: dict, match_type: str, score: float) -> dict:
    return {
        "序号": str(catalog_row.get("序号", "")).strip(),
        "名称": str(catalog_row.get("名称", "")).strip(),
        "normalized_name": str(catalog_row.get("normalized_name", "")).strip(),
        "起草部门": str(catalog_row.get("起草部门", "")).strip(),
        "生效日期": str(catalog_row.get("生效日期", "")).strip(),
        "时效性": str(catalog_row.get("时效性", "")).strip(),
        "附件数": str(catalog_row.get("附件数", "")).strip(),
        "上传者": str(catalog_row.get("上传者", "")).strip(),
        "matched_file_name": str(local_row.get("file_name", "")).strip(),
        "matched_relative_path": str(local_row.get("relative_path", "")).strip(),
        "match_type": match_type,
        "match_score": round(score, 4),
    }


def build_missing_row(catalog_row: dict) -> dict:
    return {
        "序号": str(catalog_row.get("序号", "")).strip(),
        "名称": str(catalog_row.get("名称", "")).strip(),
        "normalized_name": str(catalog_row.get("normalized_name", "")).strip(),
        "起草部门": str(catalog_row.get("起草部门", "")).strip(),
        "生效日期": str(catalog_row.get("生效日期", "")).strip(),
        "时效性": str(catalog_row.get("时效性", "")).strip(),
        "附件数": str(catalog_row.get("附件数", "")).strip(),
        "上传者": str(catalog_row.get("上传者", "")).strip(),
    }


def build_local_only_row(local_row: dict) -> dict:
    return {
        "file_name": str(local_row.get("file_name", "")).strip(),
        "relative_path": str(local_row.get("relative_path", "")).strip(),
        "ext": str(local_row.get("ext", "")).strip(),
        "normalized_name": str(local_row.get("normalized_name", "")).strip(),
    }


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, str(left or ""), str(right or "")).ratio()


def find_best_fuzzy_match(catalog_row: dict, local_rows: list[dict], used_paths: set[str], threshold: float) -> tuple[dict | None, float]:
    target = str(catalog_row.get("normalized_name", "")).strip()
    best_row = None
    best_score = 0.0
    for local_row in local_rows:
        relative_path = str(local_row.get("relative_path", "")).strip()
        if relative_path in used_paths:
            continue
        score = similarity(target, str(local_row.get("normalized_name", "")).strip())
        if score > best_score:
            best_row = local_row
            best_score = score
    if best_row is None or best_score < threshold:
        return None, 0.0
    return best_row, best_score


def reconcile_catalog(catalog_rows: list[dict], local_rows: list[dict], fuzzy_threshold: float) -> tuple[list[dict], list[dict], list[dict]]:
    matched_rows: list[dict] = []
    missing_rows: list[dict] = []
    used_paths: set[str] = set()
    exact_index = build_exact_index(local_rows)

    for catalog_row in catalog_rows:
        normalized_name = str(catalog_row.get("normalized_name", "")).strip()
        candidates = [row for row in exact_index.get(normalized_name, []) if str(row.get("relative_path", "")).strip() not in used_paths]
        if candidates:
            local_row = candidates[0]
            used_paths.add(str(local_row.get("relative_path", "")).strip())
            matched_rows.append(build_match_row(catalog_row, local_row, "exact", 1.0))
            continue
        local_row, score = find_best_fuzzy_match(catalog_row, local_rows, used_paths, fuzzy_threshold)
        if local_row:
            used_paths.add(str(local_row.get("relative_path", "")).strip())
            matched_rows.append(build_match_row(catalog_row, local_row, "fuzzy", score))
            continue
        missing_rows.append(build_missing_row(catalog_row))

    local_only_rows = [build_local_only_row(row) for row in local_rows if str(row.get("relative_path", "")).strip() not in used_paths]
    return matched_rows, missing_rows, local_only_rows


def main() -> int:
    args = build_parser().parse_args()
    catalog_txt = Path(args.catalog_txt).expanduser().resolve()
    local_dir = Path(args.local_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    catalog_rows, invalid_rows = load_catalog_rows(catalog_txt)
    local_rows = scan_local_files(local_dir)
    matched_rows, missing_rows, local_only_rows = reconcile_catalog(catalog_rows, local_rows, float(args.fuzzy_threshold))

    write_jsonl(output_dir / "catalog_structured.jsonl", catalog_rows)
    write_excel(output_dir / "catalog_structured.xlsx", catalog_rows)
    write_excel(output_dir / "matched.xlsx", matched_rows)
    write_excel(output_dir / "missing_local.xlsx", missing_rows)
    write_excel(output_dir / "local_only.xlsx", local_only_rows)
    write_excel(output_dir / "catalog_invalid_lines.xlsx", invalid_rows)

    summary = {
        "catalog_count": len(catalog_rows),
        "catalog_invalid_count": len(invalid_rows),
        "local_file_count": len(local_rows),
        "matched_count": len(matched_rows),
        "exact_match_count": sum(1 for row in matched_rows if str(row.get("match_type", "")).strip() == "exact"),
        "fuzzy_match_count": sum(1 for row in matched_rows if str(row.get("match_type", "")).strip() == "fuzzy"),
        "missing_local_count": len(missing_rows),
        "local_only_count": len(local_only_rows),
        "fuzzy_threshold": float(args.fuzzy_threshold),
    }
    write_json(output_dir / "summary.json", summary)

    print(f"catalog structured: {output_dir / 'catalog_structured.xlsx'}")
    print(f"matched: {output_dir / 'matched.xlsx'}")
    print(f"missing_local: {output_dir / 'missing_local.xlsx'}")
    print(f"local_only: {output_dir / 'local_only.xlsx'}")
    print(f"invalid_lines: {output_dir / 'catalog_invalid_lines.xlsx'}")
    print(f"summary: {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
