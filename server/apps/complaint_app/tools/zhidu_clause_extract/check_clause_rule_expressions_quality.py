from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查 clause_rule_expressions.jsonl 的规则完整性。")
    parser.add_argument("--input-jsonl", required=True, help="clause_rule_expressions.jsonl 路径")
    return parser


def load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        source = str(line or "").strip()
        if not source:
            continue
        rows.append(json.loads(source))
    return rows


def has_non_empty_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return any(str(item or "").strip() for item in value)


def build_clause_key(doc_id: str, article_no: str) -> str:
    return f"{doc_id}::{article_no}"


def main() -> int:
    args = build_parser().parse_args()
    input_path = Path(args.input_jsonl).expanduser().resolve()
    rows = load_jsonl_rows(input_path)
    if not rows:
        raise ValueError(f"输入文件为空: {input_path}")

    empty_rule_count = 0
    missing_subject_count = 0
    missing_actions_count = 0
    missing_subject_and_actions_count = 0

    article_total_counts: dict[str, int] = defaultdict(int)
    article_missing_both_counts: dict[str, int] = defaultdict(int)

    for row in rows:
        doc_id = str(row.get("doc_id") or "").strip()
        article_no = str(row.get("article_no") or "").strip()
        clause_key = build_clause_key(doc_id, article_no)
        article_total_counts[clause_key] += 1

        has_subject = has_non_empty_list(row.get("subject"))
        has_actions = has_non_empty_list(row.get("actions"))
        has_object = has_non_empty_list(row.get("object"))
        has_condition = has_non_empty_list(row.get("condition"))
        has_constraint = has_non_empty_list(row.get("constraint"))
        has_business_context = bool(str(row.get("business_context") or "").strip())
        has_effect = bool(str(row.get("effect") or "").strip())

        if not any([has_subject, has_actions, has_object, has_condition, has_constraint, has_business_context, has_effect]):
            empty_rule_count += 1

        if not has_subject:
            missing_subject_count += 1

        if not has_actions:
            missing_actions_count += 1

        if not has_subject and not has_actions:
            missing_subject_and_actions_count += 1
            article_missing_both_counts[clause_key] += 1

    total_rules = len(rows)
    all_missing_both_article_count = sum(1 for key, total in article_total_counts.items() if article_missing_both_counts.get(key, 0) == total)

    print(f"输入文件: {input_path}")
    print(f"总规则数: {total_rules}")
    print(f"空规则数: {empty_rule_count} ({empty_rule_count / total_rules * 100:.2f}%)")
    print(f"缺subject规则数: {missing_subject_count} ({missing_subject_count / total_rules * 100:.2f}%)")
    print(f"缺actions规则数: {missing_actions_count} ({missing_actions_count / total_rules * 100:.2f}%)")
    print(f"subject/actions都缺规则数: {missing_subject_and_actions_count} ({missing_subject_and_actions_count / total_rules * 100:.2f}%)")
    print(f"整条款都无subject/actions的条款数: {all_missing_both_article_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
