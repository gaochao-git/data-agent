from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _to_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


# 读取CSV并统一列名与空值。
def _load_csv(input_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(input_csv, dtype=str, encoding="utf-8-sig").fillna("")
    df.columns = [_to_text(c) for c in df.columns]
    return df


# 按举报事项聚合“任一候选命中”结果。
def _build_item_level_view(df: pd.DataFrame, item_col: str, second_correct_col: str, hit_value: str) -> pd.DataFrame:
    if item_col not in df.columns:
        raise ValueError(f"未找到列: {item_col}")
    if second_correct_col not in df.columns:
        raise ValueError(f"未找到列: {second_correct_col}")
    df2 = df.copy()
    df2[item_col] = df2[item_col].map(_to_text)
    df2[second_correct_col] = df2[second_correct_col].map(_to_text)
    item_hit = df2[second_correct_col].eq(hit_value).groupby(df2[item_col], dropna=False).transform("any")
    df2["事项命中_任一候选"] = item_hit.map({True: "是", False: "否"})
    df2 = df2[~df2[item_col].duplicated(keep="first")].copy()
    return df2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把平铺CSV聚合成举报事项级视图（每事项1行，任一候选命中即命中）。")
    parser.add_argument("--input-csv", required=True, help="输入平铺CSV路径")
    parser.add_argument("--output-csv", required=True, help="输出事项级CSV路径")
    parser.add_argument("--item-col", default="举报事项", help="举报事项列名")
    parser.add_argument("--second-correct-col", default="二级是否正确", help="二级是否正确列名")
    parser.add_argument("--hit-value", default="是", help="命中值，默认是")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    input_csv = Path(args.input_csv).expanduser().resolve()
    output_csv = Path(args.output_csv).expanduser().resolve()
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df = _load_csv(input_csv)
    out = _build_item_level_view(df, _to_text(args.item_col), _to_text(args.second_correct_col), _to_text(args.hit_value))
    out.to_csv(output_csv, index=False, encoding="utf-8-sig")
    print(f"已输出: {output_csv}")
    print(f"事项数: {len(out)}")
    if "事项命中_任一候选" in out.columns:
        print(out["事项命中_任一候选"].value_counts(dropna=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
