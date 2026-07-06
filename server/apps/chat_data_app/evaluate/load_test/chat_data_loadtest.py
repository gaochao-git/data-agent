#!/usr/bin/env python3
"""Lightweight concurrent load test for ChatData SSE completion.

Examples:
  python server/apps/chat_data_app/evaluate/load_test/chat_data_loadtest.py --requests 10 --concurrency 2 --mode generate
  python server/apps/chat_data_app/evaluate/load_test/chat_data_loadtest.py --requests 20 --concurrency 4 --datasource-id ds_xxx --semantic-config-id sem_xxx
  python server/apps/chat_data_app/evaluate/load_test/chat_data_loadtest.py --agent-id chat_data_data_analyze --requests 5 --concurrency 1 --datasource-id ds_xxx
本地开发电脑：
/Users/gaochao/work/miniconda3/envs/fxcjg/bin/python server/apps/chat_data_app/evaluate/load_test/chat_data_loadtest.py \
--base-url http://127.0.0.1:5001 \
--agent-id chat_data \
--requests 4 \
--concurrency 2 \
--datasource-id ds_20260116115851_95505 \
--semantic-config-id sc_order_operating_v2 \
--model-id qwen3.6-35b-a3b \
--mode query \
--request-timeout 180
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import httpx

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from utils.crypto import decrypt_env_value


DEFAULT_QUERIES = [
    "查询订单总销量",
    "查询2024年每月销量",
    "查询2025年每月销量",
    "查询不同订单状态的订单数量",
    "查询销售额最高的10条记录",
]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def load_default_env() -> None:
    load_env_file(SERVER_ROOT / ".env")


def _default_model_id() -> str | None:
    """从 CHAT_DATA_MODEL 统一配置中提取默认模型名。"""
    first_item = str(os.getenv("CHAT_DATA_MODEL") or "").split(",", 1)[0].strip()
    if not first_item:
        return None
    return first_item.split(";", 1)[0].strip().strip("\"'") or None


def _default_service_token() -> str:
    return decrypt_env_value("SERVICE_AUTH_TOKEN") or "loadtest-service-token"


@dataclass
class RunResult:
    index: int
    ok: bool
    status_code: int
    first_event_ms: float | None
    total_ms: float
    events: int
    bytes_read: int
    error: str = ""
    conversation_id: str = ""


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def fmt_ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}ms"


def fmt_seconds(value: float | None) -> str:
    return "-" if value is None else f"{value / 1000:.2f}s"


def print_kv_table(title: str, rows: list[tuple[str, str]]) -> None:
    print(f"\n=== {title} ===")
    width = max((len(key) for key, _ in rows), default=0)
    for key, value in rows:
        print(f"{key:<{width}}  {value}")


def parse_sse_data(data: str) -> Any:
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data


def extract_error_from_event(event_name: str, data: str) -> str:
    if event_name != "error":
        return ""
    payload = parse_sse_data(data)
    if isinstance(payload, dict):
        return str(payload.get("error") or payload.get("message") or payload)[:1000]
    return str(payload)[:1000]


def load_queries(path: str | None) -> list[str]:
    if not path:
        return DEFAULT_QUERIES
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        queries = [str(item).strip() for item in payload if str(item).strip()]
    elif isinstance(payload, dict):
        queries = [str(item).strip() for item in payload.get("queries") or [] if str(item).strip()]
    else:
        queries = []
    if not queries:
        raise ValueError(f"query 文件为空或格式不正确: {path}")
    return queries


async def create_conversation(client: httpx.AsyncClient, base_url: str, agent_id: str) -> str:
    response = await client.post(f"{base_url}/api/chatdata/conversations", json={"agent_id": agent_id})
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"创建会话失败: {payload}")
    conversation_id = str((payload.get("data") or {}).get("id") or "").strip()
    if not conversation_id:
        raise RuntimeError(f"创建会话未返回 id: {payload}")
    return conversation_id


async def consume_completion(
    client: httpx.AsyncClient,
    base_url: str,
    conversation_id: str,
    agent_id: str,
    query: str,
    options: dict[str, Any],
    request_timeout: float,
    index: int,
) -> RunResult:
    started_at = time.perf_counter()
    first_event_at: float | None = None
    event_count = 0
    bytes_read = 0
    status_code = 0
    error = ""
    ok = False
    url = f"{base_url}/api/chatdata/conversations/{conversation_id}/completion"
    body = {"query": query, "agent_id": agent_id, "options": options}
    try:
        async with client.stream("POST", url, json=body, timeout=request_timeout) as response:
            status_code = response.status_code
            if response.status_code >= 400:
                text = await response.aread()
                bytes_read += len(text)
                error = text.decode("utf-8", errors="replace")[:500]
            else:
                current_event = "message"
                data_lines: list[str] = []

                def flush_event() -> None:
                    nonlocal current_event, data_lines, event_count, error, ok
                    if not data_lines and not current_event:
                        return
                    data = "\n".join(data_lines)
                    event_count += 1
                    event_error = extract_error_from_event(current_event, data)
                    if event_error:
                        error = event_error
                    if current_event in {"done", "message_end"} or data.strip().lower() == "[done]":
                        ok = not error
                    current_event = "message"
                    data_lines = []

                async for line in response.aiter_lines():
                    if first_event_at is None:
                        first_event_at = time.perf_counter()
                    if line == "":
                        flush_event()
                        continue
                    bytes_read += len(line.encode("utf-8", errors="replace"))
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip() or "message"
                    elif line.startswith("data:"):
                        data_lines.append(line.split(":", 1)[1].lstrip())
                flush_event()
    except Exception as exc:
        error = repr(exc)
    total_ms = (time.perf_counter() - started_at) * 1000
    first_event_ms = None if first_event_at is None else (first_event_at - started_at) * 1000
    return RunResult(
        index=index,
        ok=ok,
        status_code=status_code,
        first_event_ms=first_event_ms,
        total_ms=total_ms,
        events=event_count,
        bytes_read=bytes_read,
        error=error.strip(),
        conversation_id=conversation_id,
    )


async def worker(
    *,
    worker_id: int,
    queue: asyncio.Queue[tuple[int, str]],
    client: httpx.AsyncClient,
    base_url: str,
    agent_id: str,
    options: dict[str, Any],
    request_timeout: float,
    reuse_conversation: bool,
) -> list[RunResult]:
    results: list[RunResult] = []
    shared_conversation_id = ""
    if reuse_conversation:
        shared_conversation_id = await create_conversation(client, base_url, agent_id)
    while True:
        try:
            index, query = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        conversation_id = shared_conversation_id or await create_conversation(client, base_url, agent_id)
        result = await consume_completion(
            client=client,
            base_url=base_url,
            conversation_id=conversation_id,
            agent_id=agent_id,
            query=query,
            options=options,
            request_timeout=request_timeout,
            index=index,
        )
        results.append(result)
        marker = "OK" if result.ok else "FAIL"
        print(
            f"[worker-{worker_id}] #{index} {marker} status={result.status_code} "
            f"first={fmt_ms(result.first_event_ms)} total={fmt_ms(result.total_ms)} "
            f"events={result.events} conv={result.conversation_id}"
        )
        if result.error:
            print(f"[worker-{worker_id}] #{index} error={result.error[:300]}")
        queue.task_done()
    return results


def build_options(args: argparse.Namespace) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for key in ("datasource_id", "semantic_config_id", "model_id", "mode"):
        value = getattr(args, key)
        if value:
            options[key] = value
    if args.member_datasource_id:
        options["member_datasource_ids"] = args.member_datasource_id
    if args.option:
        for item in args.option:
            if "=" not in item:
                raise ValueError(f"--option 必须是 key=value 格式: {item}")
            key, value = item.split("=", 1)
            options[key.strip()] = value.strip()
    return options


def print_markdown_summary(
    *,
    total: int,
    concurrency: int,
    succeeded: int,
    failed: int,
    success_rate: float,
    elapsed: float,
    throughput: float,
    first_p95: float | None,
    total_p95: float | None,
    first_avg: float | None,
    first_p50: float | None,
    first_p90: float | None,
    first_p99: float | None,
    total_avg: float | None,
    total_p50: float | None,
    total_p99: float | None,
    verbose: bool,
) -> None:
    print("\n=== Markdown Summary ===")
    headers = [
        "requests",
        "concurrency",
        "success",
        "failed",
        "success_rate",
        "elapsed",
        "first_p95",
        "total_p95",
    ]
    values = [
        str(total),
        str(concurrency),
        str(succeeded),
        str(failed),
        f"{success_rate:.1f}%",
        f"{elapsed:.2f}s",
        fmt_seconds(first_p95),
        fmt_seconds(total_p95),
    ]
    if verbose:
        headers.extend([
            "throughput",
            "first_avg",
            "first_p50",
            "first_p90",
            "first_p99",
            "total_avg",
            "total_p50",
            "total_p99",
        ])
        values.extend([
            f"{throughput:.2f} req/s",
            fmt_seconds(first_avg),
            fmt_seconds(first_p50),
            fmt_seconds(first_p90),
            fmt_seconds(first_p99),
            fmt_seconds(total_avg),
            fmt_seconds(total_p50),
            fmt_seconds(total_p99),
        ])
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---:" for _ in headers]) + "|")
    print("| " + " | ".join(values) + " |")


def print_summary(
    results: list[RunResult],
    started_at: float,
    concurrency: int,
    *,
    verbose: bool,
    markdown: bool,
) -> None:
    total = len(results)
    succeeded = sum(1 for item in results if item.ok)
    failed = total - succeeded
    elapsed = time.perf_counter() - started_at
    first_events = [item.first_event_ms for item in results if item.first_event_ms is not None]
    totals = [item.total_ms for item in results]
    success_rate = succeeded / total * 100 if total else 0
    throughput = total / elapsed if elapsed > 0 else 0
    first_avg = statistics.mean(first_events) if first_events else None
    first_p50 = percentile(first_events, 0.50)
    first_p90 = percentile(first_events, 0.90)
    first_p95 = percentile(first_events, 0.95)
    first_p99 = percentile(first_events, 0.99)
    total_avg = statistics.mean(totals) if totals else None
    total_p50 = percentile(totals, 0.50)
    total_p95 = percentile(totals, 0.95)
    total_p99 = percentile(totals, 0.99)
    print_kv_table("ChatData Load Test Summary", [
        ("requests", str(total)),
        ("concurrency", str(concurrency)),
        ("success", str(succeeded)),
        ("failed", str(failed)),
        ("success_rate", f"{success_rate:.1f}%"),
        ("elapsed", f"{elapsed:.2f}s"),
        ("first_p95", fmt_seconds(first_p95)),
        ("total_p95", fmt_seconds(total_p95)),
    ])
    if verbose:
        print_kv_table("Run Detail", [
            ("throughput", f"{throughput:.2f} req/s"),
        ])
        print_kv_table("Latency Detail", [
            (
                "first_event",
                " ".join([
                    f"avg={fmt_seconds(first_avg)}",
                    f"p50={fmt_seconds(first_p50)}",
                    f"p90={fmt_seconds(first_p90)}",
                    f"p95={fmt_seconds(first_p95)}",
                    f"p99={fmt_seconds(first_p99)}",
                ]),
            ),
            (
                "total_time",
                " ".join([
                    f"avg={fmt_seconds(total_avg)}",
                    f"p50={fmt_seconds(total_p50)}",
                    f"p95={fmt_seconds(total_p95)}",
                    f"p99={fmt_seconds(total_p99)}",
                ]),
            ),
        ])
    if markdown:
        print_markdown_summary(
            total=total,
            concurrency=concurrency,
            succeeded=succeeded,
            failed=failed,
            success_rate=success_rate,
            elapsed=elapsed,
            throughput=throughput,
            first_p95=first_p95,
            total_p95=total_p95,
            first_avg=first_avg,
            first_p50=first_p50,
            first_p90=first_p90,
            first_p99=first_p99,
            total_avg=total_avg,
            total_p50=total_p50,
            total_p99=total_p99,
            verbose=verbose,
        )
    errors: dict[str, int] = {}
    for item in results:
        if item.error:
            key = item.error.splitlines()[0][:160]
            errors[key] = errors.get(key, 0) + 1
    if errors:
        print("\nerrors:")
        for message, count in sorted(errors.items(), key=lambda item: item[1], reverse=True)[:10]:
            print(f"- {count}x {message}")


async def main_async(args: argparse.Namespace) -> int:
    queries = load_queries(args.queries_file)
    requests = [queries[index % len(queries)] for index in range(args.requests)]
    options = build_options(args)
    limits = httpx.Limits(max_connections=max(args.concurrency * 2, 10), max_keepalive_connections=max(args.concurrency, 5))
    timeout = httpx.Timeout(args.http_timeout, connect=args.connect_timeout)
    headers = {
        "X-Service-Token": args.service_token,
        "X-User-Id": args.user_id,
        "X-Username": args.username,
    }
    started_at = time.perf_counter()
    async with httpx.AsyncClient(limits=limits, timeout=timeout, trust_env=False, headers=headers) as client:
        queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        for index, query in enumerate(requests, start=1):
            queue.put_nowait((index, query))
        workers = [
            asyncio.create_task(
                worker(
                    worker_id=worker_id,
                    queue=queue,
                    client=client,
                    base_url=args.base_url.rstrip("/"),
                    agent_id=args.agent_id,
                    options=options,
                    request_timeout=args.request_timeout,
                    reuse_conversation=args.reuse_conversation,
                )
            )
            for worker_id in range(1, args.concurrency + 1)
        ]
        grouped = await asyncio.gather(*workers)
    results = [item for group in grouped for item in group]
    results.sort(key=lambda item: item.index)
    print_summary(
        results,
        started_at,
        args.concurrency,
        verbose=args.verbose,
        markdown=args.markdown,
    )
    return 0 if all(item.ok for item in results) else 1


def parse_args() -> argparse.Namespace:
    load_default_env()
    parser = argparse.ArgumentParser(description="ChatData 智能问数 SSE 并发压测脚本")
    parser.add_argument("--base-url", default="http://127.0.0.1:5001", help="后端地址，不带 /api")
    parser.add_argument("--agent-id", default="chat_data", choices=["chat_data", "chat_data_data_analyze", "chat_data_sql_assist"])
    parser.add_argument("--requests", type=int, default=10, help="总请求数")
    parser.add_argument("--concurrency", type=int, default=2, help="并发 worker 数")
    parser.add_argument("--queries-file", help="JSON 文件，格式为字符串数组或 {\"queries\": [...]}")
    parser.add_argument("--service-token", default=_default_service_token(), help="X-Service-Token，默认读取当前进程环境变量 SERVICE_AUTH_TOKEN 密文并解密")
    parser.add_argument("--user-id", default="10001")
    parser.add_argument("--username", default="5Y6L5rWL55So5oi3", help="X-Username，按网关口径传 UTF-8 Base64 后的用户名")
    parser.add_argument("--datasource-id", help="数据源 ID")
    parser.add_argument("--semantic-config-id", help="语义配置 ID")
    parser.add_argument("--member-datasource-id", action="append", help="成员数据源 ID，可传多次")
    parser.add_argument("--model-id", default=_default_model_id(), help="模型 ID，默认读取当前进程环境变量 CHAT_DATA_MODEL 的第一项模型名")
    parser.add_argument("--mode", default="query", help="智能问数模式，常用 generate/query")
    parser.add_argument("--option", action="append", help="额外 options，格式 key=value，可传多次")
    parser.add_argument("--reuse-conversation", action="store_true", help="每个 worker 复用一个会话；默认每个请求新建会话")
    parser.add_argument("--verbose", action="store_true", help="输出 avg/p50/p90/p99 等详细延迟指标")
    parser.add_argument("--markdown", action="store_true", help="额外输出 Markdown 汇总表")
    parser.add_argument("--http-timeout", type=float, default=180.0, help="httpx 全局超时")
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--request-timeout", type=float, default=180.0, help="单个 SSE 请求超时")
    args = parser.parse_args()
    if args.requests <= 0:
        parser.error("--requests 必须大于 0")
    if args.concurrency <= 0:
        parser.error("--concurrency 必须大于 0")
    args.concurrency = min(args.concurrency, args.requests)
    return args


def main() -> int:
    try:
        return asyncio.run(main_async(parse_args()))
    except KeyboardInterrupt:
        print("Interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
