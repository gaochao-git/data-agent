#!/usr/bin/env python3
"""Summarize LLM prompt-size JSONL metrics."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any


DETAIL_FIELDS = [
    "request_id",
    "timestamp",
    "business_stage",
    "stage",
    "model",
    "call_index",
    "message_count",
    "system_chars",
    "user_chars",
    "assistant_chars",
    "tool_chars",
    "prompt_chars",
    "output_chars",
    "first_token_ms",
    "llm_duration_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "base_id",
    "file_id",
    "session_id",
    "skill_name",
    "match_strategy",
    "complaint_id",
]


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def load_rows(metrics_dir: Path, request_id: str = "") -> list[dict[str, Any]]:
    files = [metrics_dir / f"{request_id}.jsonl"] if request_id else sorted(metrics_dir.glob("*.jsonl"))
    prompt_rows: dict[tuple[str, str], dict[str, Any]] = {}
    output_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for path in files:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as file_obj:
            for line in file_obj:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                row.setdefault("request_id", path.stem)
                key = (str(row.get("request_id") or path.stem), str(row.get("call_id") or ""))
                if row.get("type") == "llm_prompt":
                    prompt_rows[key] = row
                elif row.get("type") == "llm_output":
                    output_rows[key] = row
    rows: list[dict[str, Any]] = []
    for key, prompt in prompt_rows.items():
        merged = dict(prompt)
        output = output_rows.get(key) or {}
        merged["output_chars"] = _int(output.get("output_chars"))
        merged["first_token_ms"] = _int(output.get("first_token_ms"))
        merged["llm_duration_ms"] = _int(output.get("llm_duration_ms"))
        merged["prompt_tokens"] = _int(output.get("prompt_tokens"))
        merged["completion_tokens"] = _int(output.get("completion_tokens"))
        merged["total_tokens"] = _int(output.get("total_tokens"))
        rows.append(merged)
    rows.sort(key=lambda item: (str(item.get("request_id") or ""), _int(item.get("call_index")), str(item.get("stage") or "")))
    return rows


def write_detail_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=DETAIL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("business_stage") or ""), str(row.get("stage") or ""))
        groups.setdefault(key, []).append(row)
    summary: list[dict[str, Any]] = []
    for (business_stage, stage), items in sorted(groups.items()):
        prompt_chars = [_int(item.get("prompt_chars")) for item in items]
        output_chars = [_int(item.get("output_chars")) for item in items]
        first_token_ms = [_int(item.get("first_token_ms")) for item in items if _int(item.get("first_token_ms")) > 0]
        duration_ms = [_int(item.get("llm_duration_ms")) for item in items if _int(item.get("llm_duration_ms")) > 0]
        prompt_tokens = [_int(item.get("prompt_tokens")) for item in items]
        completion_tokens = [_int(item.get("completion_tokens")) for item in items]
        total_tokens = [_int(item.get("total_tokens")) for item in items]
        system_chars = [_int(item.get("system_chars")) for item in items]
        user_chars = [_int(item.get("user_chars")) for item in items]
        tool_chars = [_int(item.get("tool_chars")) for item in items]
        summary.append(
            {
                "business_stage": business_stage,
                "stage": stage,
                "call_count": len(items),
                "prompt_chars_total": sum(prompt_chars),
                "prompt_chars_avg": round(statistics.mean(prompt_chars), 2) if prompt_chars else 0,
                "prompt_chars_max": max(prompt_chars) if prompt_chars else 0,
                "output_chars_total": sum(output_chars),
                "output_chars_avg": round(statistics.mean(output_chars), 2) if output_chars else 0,
                "output_chars_max": max(output_chars) if output_chars else 0,
                "first_token_avg_ms": round(statistics.mean(first_token_ms), 2) if first_token_ms else 0,
                "duration_avg_ms": round(statistics.mean(duration_ms), 2) if duration_ms else 0,
                "prompt_tokens_total": sum(prompt_tokens),
                "completion_tokens_total": sum(completion_tokens),
                "total_tokens_total": sum(total_tokens),
                "system_chars_total": sum(system_chars),
                "user_chars_total": sum(user_chars),
                "tool_chars_total": sum(tool_chars),
            }
        )
    return summary


def write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["business_stage", "stage", "call_count", "prompt_chars_total", "prompt_chars_avg", "prompt_chars_max", "output_chars_total", "output_chars_avg", "output_chars_max", "first_token_avg_ms", "duration_avg_ms", "prompt_tokens_total", "completion_tokens_total", "total_tokens_total", "system_chars_total", "user_chars_total", "tool_chars_total"]
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_report(path: Path, summary_rows: list[dict[str, Any]], detail_count: int) -> None:
    lines = ["# LLM Prompt Metrics Report", "", f"- 明细请求数：{detail_count}", ""]
    lines.append("## 汇总")
    lines.append("")
    lines.append("| 业务阶段 | 调用点 | 调用次数 | Prompt总字数 | 输出总字数 | 首token均值ms | 总耗时均值ms | Prompt tokens | Output tokens | Total tokens |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in summary_rows:
        lines.append(
            "| {business_stage} | {stage} | {call_count} | {prompt_chars_total} | {output_chars_total} | {first_token_avg_ms} | {duration_avg_ms} | {prompt_tokens_total} | {completion_tokens_total} | {total_tokens_total} |".format(**row)
        )
    lines.append("")
    lines.append("## 产物")
    lines.append("")
    lines.append("- `llm_prompt_detail.csv`：每次真实大模型请求一行，包含输入和输出")
    lines.append("- `llm_prompt_summary.csv`：按业务阶段和调用点汇总")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize LLM prompt metrics")
    parser.add_argument("--metrics-dir", required=True, help="LLM_PROMPT_METRICS_DIR 或 DATA_ROOT/llm_prompt_metrics")
    parser.add_argument("--request-id", default="", help="可选：只汇总单个 request_id/base_id")
    parser.add_argument("--output-dir", required=True, help="报告输出目录")
    args = parser.parse_args()

    metrics_dir = Path(args.metrics_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_rows(metrics_dir, args.request_id)
    summary_rows = summarize(rows)
    write_detail_csv(output_dir / "llm_prompt_detail.csv", rows)
    write_summary_csv(output_dir / "llm_prompt_summary.csv", summary_rows)
    write_report(output_dir / "report.md", summary_rows, len(rows))
    print(f"report written: {output_dir / 'report.md'}")


if __name__ == "__main__":
    main()
