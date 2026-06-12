from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把 JSONL 文件导出为 Excel。")
    parser.add_argument("--input-jsonl", required=True, help="输入 JSONL 文件路径")
    parser.add_argument("--output-xlsx", required=True, help="输出 Excel 文件路径")
    return parser


def load_jsonl_rows(input_path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in input_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def main() -> int:
    args = build_parser().parse_args()
    input_path = Path(args.input_jsonl).expanduser().resolve()
    output_path = Path(args.output_xlsx).expanduser().resolve()
    rows = load_jsonl_rows(input_path)
    df = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(output_path, index=False)
    print(f"输入文件: {input_path}")
    print(f"输出文件: {output_path}")
    print(f"记录数: {len(df)}")
    print(f"列数: {len(df.columns)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
