#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


ROUTE_PATH = "/api/complaint/complaint-analysis"


def ms_to_seconds(value: int) -> float:
    """毫秒转秒，保留 3 位小数。"""
    return round(max(0, int(value or 0)) / 1000.0, 3)


def build_seconds_view(stats: dict[str, Any]) -> dict[str, Any]:
    """把 *_ms 字段转换为 *_s 视图。"""
    result: dict[str, Any] = {}
    for key, value in stats.items():
        if str(key).endswith("_ms"):
            result[str(key)[:-3] + "_s"] = ms_to_seconds(int(value or 0))
        else:
            result[str(key)] = value
    return result


@dataclass
class RequestTask:
    run_index: int
    round_index: int
    item_index: int
    file_id: str


def load_file_ids(file_ids: list[str], file_id_file: str | None) -> list[str]:
    """读取压测使用的 file_id 列表。"""
    values = [str(item).strip() for item in file_ids if str(item).strip()]
    if file_id_file:
        path = Path(file_id_file).expanduser()
        if not path.is_file():
            raise ValueError(f"file_id 文件不存在: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            values.append(text)
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    if not deduped:
        raise ValueError("至少提供一个 file_id")
    return deduped


def build_tasks(file_ids: list[str], rounds: int) -> list[RequestTask]:
    """生成压测任务列表。"""
    tasks: list[RequestTask] = []
    run_index = 0
    for round_index in range(1, rounds + 1):
        for item_index, file_id in enumerate(file_ids, start=1):
            run_index += 1
            tasks.append(RequestTask(run_index=run_index, round_index=round_index, item_index=item_index, file_id=file_id))
    return tasks


def safe_event_name(payload: dict[str, Any]) -> str:
    """生成阶段事件名称。"""
    status = str(payload.get("status") or "").strip()
    if status == "started":
        return "started"
    if status == "completed":
        return "completed"
    if status == "error":
        return "error"
    progress = int(payload.get("progress") or 0)
    phase_node = str(payload.get("phase_node") or "").strip()
    if progress == 10:
        return "summary"
    if phase_node == "complaint_extract_items":
        return "extract_items_done" if isinstance(payload.get("phase_result"), dict) else "extract_items"
    if phase_node == "complaint_match_experience_stage1":
        return "match_stage1"
    if phase_node == "complaint_match_experience_stage2":
        return "match_stage2"
    if phase_node == "complaint_match_experience":
        return "match_done"
    if phase_node == "complaint_final_conclusion":
        return "final_conclusion_done" if isinstance(payload.get("phase_result"), dict) else "final_conclusion"
    return f"processing_{progress}"


def parse_sse_data_line(line: str) -> dict[str, Any] | None:
    """解析单条 SSE data 行。"""
    text = str(line or "").strip()
    if not text.startswith("data:"):
        return None
    body = text[5:].strip()
    if not body:
        return None
    return json.loads(body)


def request_payload(user_id: str, file_id: str) -> dict[str, Any]:
    """构造固定 force=true 的请求体。"""
    return {"user_id": user_id, "file_id": file_id, "force": True}


def build_request_output_dir(output_dir: Path, task: RequestTask) -> Path:
    """构造单请求输出目录。"""
    return output_dir / f"round_{task.round_index:02d}" / f"{task.item_index:02d}_{task.file_id}"


def write_json(path: Path, payload: Any) -> None:
    """写入 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """写入 JSONL 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def extract_stage_timestamps(events: list[dict[str, Any]]) -> dict[str, int]:
    """从事件列表提取阶段耗时。"""
    result: dict[str, int] = {}
    seen: set[str] = set()
    for row in events:
        event_name = str(row.get("event_name") or "")
        elapsed_ms = int(row.get("elapsed_ms") or 0)
        if not event_name or event_name in seen:
            continue
        seen.add(event_name)
        result[f"{event_name}_ms"] = elapsed_ms
    return result


def run_single_request(base_url: str, user_id: str, task: RequestTask, timeout_seconds: int, output_dir: Path) -> dict[str, Any]:
    """执行单个举报分析请求并记录时间。"""
    url = f"{base_url.rstrip('/')}{ROUTE_PATH}"
    request_started_at = time.monotonic()
    raw_events: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "run_index": task.run_index,
        "round_index": task.round_index,
        "item_index": task.item_index,
        "file_id": task.file_id,
        "url": url,
        "success": False,
        "status": "error",
        "http_status": 0,
        "base_id": "",
        "error": "",
        "response_headers_ms": 0,
        "first_event_ms": 0,
        "total_ms": 0,
        "event_count": 0,
    }
    per_request_dir = build_request_output_dir(output_dir, task)
    try:
        with requests.post(url, json=request_payload(user_id, task.file_id), stream=True, timeout=timeout_seconds) as response:
            result["http_status"] = int(response.status_code or 0)
            result["response_headers_ms"] = int((time.monotonic() - request_started_at) * 1000)
            response.raise_for_status()
            first_event_recorded = False
            last_payload: dict[str, Any] | None = None
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                parsed = parse_sse_data_line(raw_line)
                if not isinstance(parsed, dict):
                    continue
                now = time.monotonic()
                elapsed_ms = int((now - request_started_at) * 1000)
                if not first_event_recorded:
                    result["first_event_ms"] = elapsed_ms
                    first_event_recorded = True
                event_name = safe_event_name(parsed)
                raw_events.append({"elapsed_ms": elapsed_ms, "event_name": event_name, "payload": parsed})
                last_payload = parsed
            result["event_count"] = len(raw_events)
            result["total_ms"] = int((time.monotonic() - request_started_at) * 1000)
            if isinstance(last_payload, dict):
                result["base_id"] = str(last_payload.get("base_id") or "")
                result["status"] = str(last_payload.get("status") or "")
                if result["status"] == "completed":
                    result["success"] = True
                else:
                    result["error"] = str(last_payload.get("error") or last_payload.get("message") or "")
            result.update(extract_stage_timestamps(raw_events))
    except Exception as exc:
        result["total_ms"] = int((time.monotonic() - request_started_at) * 1000)
        result["error"] = str(exc)
    write_jsonl(per_request_dir / "events.jsonl", raw_events)
    write_json(per_request_dir / "summary.json", result)
    return result


def percentile(values: list[int], ratio: float) -> int:
    """计算简单分位数。"""
    if not values:
        return 0
    ordered = sorted(values)
    index = int((len(ordered) - 1) * ratio)
    return ordered[index]


def build_aggregate_report(rows: list[dict[str, Any]], concurrency: int, rounds: int, base_url: str) -> dict[str, Any]:
    """生成汇总报告。"""
    total_values = [int(row.get("total_ms") or 0) for row in rows if int(row.get("total_ms") or 0) > 0]
    success_rows = [row for row in rows if bool(row.get("success"))]
    return {
        "base_url": base_url,
        "route_path": ROUTE_PATH,
        "concurrency": concurrency,
        "rounds": rounds,
        "request_count": len(rows),
        "success_count": len(success_rows),
        "error_count": len(rows) - len(success_rows),
        "total_ms_avg": int(statistics.mean(total_values)) if total_values else 0,
        "total_ms_p50": int(statistics.median(total_values)) if total_values else 0,
        "total_ms_p95": percentile(total_values, 0.95),
        "total_ms_max": max(total_values) if total_values else 0,
    }


def build_phase_aggregate_report(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """生成按阶段聚合的耗时报告。"""
    metrics: dict[str, list[int]] = {}
    for row in rows:
        for key, value in row.items():
            if not str(key).endswith("_ms"):
                continue
            if key in {"total_ms"}:
                continue
            duration = int(value or 0)
            if duration <= 0:
                continue
            metrics.setdefault(str(key), []).append(duration)
    result: dict[str, dict[str, int]] = {}
    for key in sorted(metrics.keys()):
        values = metrics[key]
        result[key] = {
            "count": len(values),
            "avg": int(statistics.mean(values)),
            "p50": int(statistics.median(values)),
            "p95": percentile(values, 0.95),
            "max": max(values),
        }
    return result


def print_phase_aggregate_report(phase_report: dict[str, dict[str, int]]) -> None:
    """打印按阶段聚合的耗时结果。"""
    if not phase_report:
        return
    print("\n=== phase aggregate (s) ===")
    print(f"{'phase':32} {'count':>7} {'avg':>10} {'p50':>10} {'p95':>10} {'max':>10}")
    for phase, stats in phase_report.items():
        print(f"{phase:32} {int(stats.get('count') or 0):7d} {ms_to_seconds(int(stats.get('avg') or 0)):10.3f} {ms_to_seconds(int(stats.get('p50') or 0)):10.3f} {ms_to_seconds(int(stats.get('p95') or 0)):10.3f} {ms_to_seconds(int(stats.get('max') or 0)):10.3f}")


def write_phase_csv(path: Path, phase_report: dict[str, dict[str, int]]) -> None:
    """写入阶段聚合 CSV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["phase", "count", "avg_s", "p50_s", "p95_s", "max_s"])
        writer.writeheader()
        for phase, stats in phase_report.items():
            writer.writerow({
                "phase": phase,
                "count": int(stats.get("count") or 0),
                "avg_s": ms_to_seconds(int(stats.get("avg") or 0)),
                "p50_s": ms_to_seconds(int(stats.get("p50") or 0)),
                "p95_s": ms_to_seconds(int(stats.get("p95") or 0)),
                "max_s": ms_to_seconds(int(stats.get("max") or 0)),
            })


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """写入请求级汇总 CSV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数。"""
    parser = argparse.ArgumentParser(description="complaint-analysis SSE 并发压测脚本")
    parser.add_argument("--base-url", default="http://127.0.0.1:5001", help="接口服务地址，可配置 URL 前缀")
    parser.add_argument("--user-id", required=True, help="请求 user_id")
    parser.add_argument("--file-id", action="append", default=[], help="待压测的 file_id，可重复传入")
    parser.add_argument("--file-id-file", default=None, help="按行读取 file_id 的文本文件")
    parser.add_argument("--concurrency", type=int, default=10, help="并发数，默认 10")
    parser.add_argument("--rounds", type=int, default=1, help="轮数，默认 1")
    parser.add_argument("--timeout-seconds", type=int, default=900, help="单请求超时秒数")
    parser.add_argument("--output-dir", default="./tmp/complaint-analysis-benchmark", help="输出目录")
    return parser


def main() -> int:
    """脚本入口。"""
    args = build_parser().parse_args()
    file_ids = load_file_ids(args.file_id, args.file_id_file)
    tasks = build_tasks(file_ids, args.rounds)
    output_dir = Path(args.output_dir).expanduser() / time.strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(args.concurrency))) as executor:
        future_map = {
            executor.submit(run_single_request, args.base_url, args.user_id, task, int(args.timeout_seconds), output_dir): task
            for task in tasks
        }
        for future in concurrent.futures.as_completed(future_map):
            row = future.result()
            rows.append(row)
            status = "OK" if row.get("success") else "ERR"
            print(f"[{status}] file_id={row.get('file_id')} total_s={ms_to_seconds(int(row.get('total_ms') or 0)):.3f} status={row.get('status')} error={row.get('error')}", flush=True)
    rows.sort(key=lambda item: int(item.get("run_index") or 0))
    aggregate = build_aggregate_report(rows, int(args.concurrency), int(args.rounds), str(args.base_url))
    phase_aggregate = build_phase_aggregate_report(rows)
    write_csv(output_dir / "summary.csv", rows)
    write_phase_csv(output_dir / "phase_summary.csv", phase_aggregate)
    aggregate_seconds = build_seconds_view(aggregate)
    phase_aggregate_seconds = {phase: build_seconds_view(stats) for phase, stats in phase_aggregate.items()}
    write_json(output_dir / "summary.json", {"aggregate": aggregate_seconds, "phase_aggregate": phase_aggregate_seconds, "requests": rows})
    print("\n=== total aggregate ===")
    print(json.dumps(aggregate_seconds, ensure_ascii=False, indent=2))
    print_phase_aggregate_report(phase_aggregate)
    print(f"\noutput_dir={output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
