from __future__ import annotations

import argparse
import json
from typing import Any

from outline_data_store import get_loaded_merged_outline_path
from outline_data_store import load_merged_outline_index_by_title
from outline_data_store import load_merged_outline_rows


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="加载 merged_outline.json 到内存并输出摘要")
    parser.add_argument("--force-reload", action="store_true", help="忽略进程内缓存，重新从文件加载")
    parser.add_argument("--title", default="", help="按制度名称查看一条已加载记录")
    parser.add_argument("--sample-size", type=int, default=5, help="默认输出前 N 条制度名称预览")
    return parser


# 打印已加载数据的摘要信息。
def print_outline_summary(rows: list[dict[str, Any]], sample_size: int) -> None:
    titles = [str(row.get("制度名称") or "").strip() for row in rows if str(row.get("制度名称") or "").strip()]
    preview = titles[: max(sample_size, 0)]
    print(f"merged_outline_path={get_loaded_merged_outline_path()}")
    print(f"loaded_row_count={len(rows)}")
    print(f"title_preview={json.dumps(preview, ensure_ascii=False)}")


# 按制度名称打印单条已加载记录。
def print_outline_record_by_title(index_by_title: dict[str, dict[str, Any]], title: str) -> None:
    record = index_by_title.get(title)
    if record is None:
        raise ValueError(f"未找到指定制度名称: {title}")
    print(json.dumps(record, ensure_ascii=False, indent=2))


def main() -> None:
    parser = build_argument_parser()
    args = parser.parse_args()
    rows = load_merged_outline_rows(force_reload=args.force_reload)
    print_outline_summary(rows, args.sample_size)
    if str(args.title or "").strip():
        index_by_title = load_merged_outline_index_by_title(force_reload=False)
        print_outline_record_by_title(index_by_title, str(args.title).strip())


if __name__ == "__main__":
    main()
