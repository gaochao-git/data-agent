#!/usr/bin/env python3
"""Concurrent benchmark client for the unified /api/complaint/init_analysis API."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

console = Console()
SSE_HEADERS = {
    "Accept": "text/event-stream",
    "Cache-Control": "no-cache",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}


def _print_stage_result(task_label: str, stage_name: str, payload: Dict[str, Any]) -> None:
    pretty = json.dumps(payload, ensure_ascii=False, indent=2)
    # console.print(f"[magenta]{task_label} {stage_name}完成结果：[/magenta]\n{pretty}")


async def read_sse(response: httpx.Response):
    """Yield JSON payloads from an SSE httpx response."""
    buffer: List[str] = []
    try:
        async for line in response.aiter_lines():
            # console.print(f"[blue]SSE 行：{line}[/blue]")
            if not line:
                if buffer:
                    data_str = "\n".join(buffer).strip()
                    buffer.clear()
                    if not data_str:
                        continue
                    try:
                        yield json.loads(data_str)
                    except json.JSONDecodeError:
                        console.log(f"[yellow]无法解析SSE数据: {data_str[:200]}...[/yellow]")
                continue
            if line.startswith("data: "):
                buffer.append(line[6:])
            elif line.startswith("data:"):
                buffer.append(line[5:].lstrip())
            else:
                # Ignore other SSE control fields (event:, id:, etc.)
                continue
    finally:
        if buffer:
            data_str = "\n".join(buffer).strip()
            if data_str:
                try:
                    yield json.loads(data_str)
                except json.JSONDecodeError:
                    console.log(f"[yellow]无法解析SSE数据: {data_str[:200]}...[/yellow]")
        await response.aclose()


async def upload_and_process(
    client: httpx.AsyncClient,
    pdf_bytes: bytes,
    pdf_name: str,
    task_id: int,
) -> Dict[str, Optional[float]]:
    task_label = f"请求#{task_id}"
    request_start = time.time()
    task1_done: Optional[float] = None
    task2_done: Optional[float] = None
    task3_done: Optional[float] = None
    task4_done: Optional[float] = None

    def _fmt_ts(ts: Optional[float]) -> str:
        if ts is None:
            return "-"
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

    console.print(f"[cyan]{task_label} 发起时间：{_fmt_ts(request_start)}[/cyan]")

    files = {"file": (pdf_name, pdf_bytes, "application/pdf")}

    async with client.stream("POST", "/api/complaint/init_analysis", files=files, headers=SSE_HEADERS, timeout=3600) as response:
        response.raise_for_status()
        async for data in read_sse(response):
            task = data.get("task")
            status = data.get("status")

            if task == 0 and status == "completed" and task1_done is None:
                task1_done = time.time()
                console.print(f"[cyan]{task_label} 任务1完成：{_fmt_ts(task1_done)}[/cyan]")
                _print_stage_result(task_label, "任务1", data)
                continue

            if task == 1 and status == "completed" and task2_done is None:
                task2_done = time.time()
                console.print(f"[cyan]{task_label} 任务2完成：{_fmt_ts(task2_done)}[/cyan]")
                _print_stage_result(task_label, "任务2", data)
                continue

            if task == 2 and status == "completed":
                if task3_done is None:
                    task3_done = time.time()
                    console.print(f"[cyan]{task_label} 任务3完成：{_fmt_ts(task3_done)}[/cyan]")
                    _print_stage_result(task_label, "任务3", data)
                continue

            if task == "task2_final" and status == "completed":
                if task3_done is None:
                    task3_done = time.time()
                    console.print(f"[cyan]{task_label} 任务3完成（final）：{_fmt_ts(task3_done)}[/cyan]")
                    _print_stage_result(task_label, "任务3", data)

            if task == 3 and status == "completed":
                if task4_done is None:
                    task4_done = time.time()
                    console.print(f"[cyan]{task_label} 任务4完成：{_fmt_ts(task4_done)}[/cyan]")
                    _print_stage_result(task_label, "任务4", data)

    def _duration(end: Optional[float], start: Optional[float]) -> Optional[float]:
        if end is None or start is None:
            return None
        return max(0.0, end - start)

    upload_duration = _duration(task1_done, request_start)
    pdf_duration = _duration(task2_done, task1_done or request_start)
    summary_duration = _duration(task3_done, task2_done or task1_done or request_start)
    items_duration = _duration(task4_done, task3_done or task2_done or task1_done or request_start)

    console.print(
        f"[green]{task_label} 耗时统计：上传 {upload_duration or 0:.2f}s | "
        f"PDF {pdf_duration or 0:.2f}s | 摘要/举报 {summary_duration or 0:.2f}s | "
        f"事项分析 {items_duration or 0:.2f}s[/green]"
    )

    return {
        "upload": upload_duration,
        "pdf": pdf_duration,
        "summary": summary_duration,
        "items": items_duration,
        "request_start": request_start,
        "task1_done": task1_done,
        "task2_done": task2_done,
        "task3_done": task3_done,
        "task4_done": task4_done,
    }
async def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark /api/complaint/init_analysis with concurrent requests.")
    parser.add_argument("pdf_path", type=Path, help="Path to the PDF file to upload (should contain 20 pages).")
    parser.add_argument("--base-url", default="http://127.0.0.1:5001", help="Base URL of the API (default: http://127.0.0.1:5001)")
    parser.add_argument("--requests", type=int, default=5, help="Total number of concurrent requests to send (default: 5)")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrency level (default: 5)")
    parser.add_argument("--timeout", type=float, default=60.0, help="Request timeout in seconds (default: 60)")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification")
    args = parser.parse_args()

    pdf_path: Path = args.pdf_path.expanduser().resolve()
    if not pdf_path.exists():
        console.print(f"[red]PDF 文件不存在: {pdf_path}[/red]")
        raise SystemExit(1)

    pdf_bytes = pdf_path.read_bytes()
    pdf_name = pdf_path.name

    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    timeout = httpx.Timeout(args.timeout)

    base_url = args.base_url.rstrip("/")

    async with httpx.AsyncClient(
        base_url=base_url,
        http2=False,
        trust_env=False,
        timeout=timeout,
        limits=limits,
        verify=not args.insecure,
    ) as client:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            TextColumn("[progress.percentage]{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        )
        tasks: List[asyncio.Task[None]] = []
        results: List[Optional[Dict[str, Optional[float]]]] = [None] * args.requests

        semaphore = asyncio.Semaphore(args.concurrency)

        async def runner(task_id: int) -> None:
            async with semaphore:
                description = f"请求#{task_id}"
                task = progress.add_task(description, total=None)
                try:
                    result = await upload_and_process(client, pdf_bytes, pdf_name, task_id)
                    results[task_id - 1] = result
                finally:
                    progress.update(task, completed=1, total=1)
                    progress.remove_task(task)

        with progress:
            for idx in range(1, args.requests + 1):
                tasks.append(asyncio.create_task(runner(idx)))
            await asyncio.gather(*tasks)

    console.print("[bold green]所有请求已完成。[/bold green]")

    table = Table(title="各请求阶段耗时", show_lines=True)
    table.add_column("请求")
    table.add_column("上传")
    table.add_column("PDF文字提取")
    table.add_column("摘要/举报")
    table.add_column("事项分析")

    def _fmt(value: Optional[float]) -> str:
        return "-" if value is None else f"{value:.2f}s"

    for idx, result in enumerate(results, start=1):
        if not result:
            table.add_row(f"#{idx}", "-", "-", "-", "-")
            continue
        table.add_row(
            f"#{idx}",
            _fmt(result["upload"]),
            _fmt(result["pdf"]),
            _fmt(result["summary"]),
            _fmt(result["items"]),
        )

    console.print(table)

    agg_table = Table(title="阶段统计", show_header=True)
    agg_table.add_column("阶段")
    agg_table.add_column("平均值")
    agg_table.add_column("最小值")
    agg_table.add_column("最大值")

    for phase, key in (
        ("上传", "upload"),
        ("PDF文字提取", "pdf"),
        ("摘要/举报", "summary"),
        ("事项分析", "items"),
    ):
        values = [r[key] for r in results if r and r.get(key) is not None]
        if not values:
            avg = min_v = max_v = None
        else:
            avg = sum(values) / len(values)
            min_v = min(values)
            max_v = max(values)
        agg_table.add_row(phase, _fmt(avg), _fmt(min_v), _fmt(max_v))

    console.print(agg_table)


if __name__ == "__main__":
    asyncio.run(main())
