from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any


def collect_doc_ids_from_member_mentions(rows: Iterable[dict[str, Any]]) -> set[str]:
    doc_ids: set[str] = set()
    for row in rows:
        for member in list(row.get("member_mentions") or []):
            if not isinstance(member, dict):
                continue
            doc_id = str(member.get("doc_id") or "").strip()
            if doc_id:
                doc_ids.add(doc_id)
    return doc_ids


def collect_doc_ids_from_rows(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {str(row.get("doc_id") or "").strip() for row in rows if str(row.get("doc_id") or "").strip()}


def print_doc_progress(stage: str, done_doc_ids: set[str], total_doc_ids: set[str], current_doc_id: str = "") -> None:
    total = len(total_doc_ids)
    done = len(done_doc_ids)
    suffix = f" current_doc_id={current_doc_id}" if current_doc_id else ""
    print(f"[{stage}] document progress {done}/{total}{suffix}", flush=True)


async def gather_with_doc_progress(stage: str, tasks: list[tuple[asyncio.Task, set[str], str]], total_doc_ids: set[str]) -> list[Any]:
    pending = {task for task, _doc_ids, _label in tasks}
    task_meta = {task: (index, doc_ids, label) for index, (task, doc_ids, label) in enumerate(tasks)}
    done_doc_ids: set[str] = set()
    results: list[Any] = [None] * len(tasks)
    print_doc_progress(stage, done_doc_ids, total_doc_ids)
    while pending:
        done_tasks, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done_tasks:
            index, doc_ids, label = task_meta[task]
            result = await task
            results[index] = result
            done_doc_ids.update(doc_ids)
            print_doc_progress(stage, done_doc_ids, total_doc_ids, label)
    return results


async def gather_with_item_progress(stage: str, tasks: list[tuple[asyncio.Task, str]], total_count: int) -> list[Any]:
    pending = {task for task, _label in tasks}
    task_meta = {task: (index, label) for index, (task, label) in enumerate(tasks)}
    done_count = 0
    results: list[Any] = [None] * len(tasks)
    print(f"[{stage}] progress {done_count}/{total_count}", flush=True)
    while pending:
        done_tasks, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done_tasks:
            result = await task
            index, label = task_meta[task]
            results[index] = result
            done_count += 1
            suffix = f" current={label}" if label else ""
            print(f"[{stage}] progress {done_count}/{total_count}{suffix}", flush=True)
    return results
