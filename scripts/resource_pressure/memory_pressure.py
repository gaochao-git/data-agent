#!/usr/bin/env python3
from __future__ import annotations

import argparse
import mmap
import os
import signal
import sys
import time


def get_total_memory_bytes() -> int:
    """获取机器总内存字节数。"""
    if hasattr(os, "sysconf") and "SC_PAGE_SIZE" in os.sysconf_names and "SC_PHYS_PAGES" in os.sysconf_names:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        if page_size > 0 and pages > 0:
            return page_size * pages
    raise RuntimeError("无法获取总内存大小")


def get_used_memory_bytes(total_memory: int) -> int:
    """获取当前已使用内存字节数。"""
    if sys.platform.startswith("linux") and os.path.isfile("/proc/meminfo"):
        values: dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split(":", 1)
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                value_text = parts[1].strip().split()[0]
                if value_text.isdigit():
                    values[key] = int(value_text) * 1024
        available = values.get("MemAvailable")
        if isinstance(available, int) and available >= 0:
            return max(0, total_memory - available)
    return 0


def allocate_block(size_bytes: int) -> mmap.mmap:
    """分配并触碰一块匿名内存，确保进入 RSS。"""
    block = mmap.mmap(-1, size_bytes)
    page_size = 4096
    for index in range(0, size_bytes, page_size):
        block[index:index + 1] = b"\1"
    return block


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将机器内存使用率维持在目标区间内。")
    parser.add_argument("--min-percent", type=float, default=22.0, help="目标下限百分比，默认 22")
    parser.add_argument("--max-percent", type=float, default=28.0, help="目标上限百分比，默认 28")
    parser.add_argument("--check-interval", type=float, default=5.0, help="采样与调整间隔（秒），默认 5")
    parser.add_argument("--chunk-mb", type=int, default=128, help="单次增减块大小（MB），默认 128")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    min_percent = min(args.min_percent, args.max_percent)
    max_percent = max(args.min_percent, args.max_percent)
    target_percent = (min_percent + max_percent) / 2.0
    total_memory = get_total_memory_bytes()
    chunk_bytes = max(1, args.chunk_mb) * 1024 * 1024
    allocated_blocks: list[mmap.mmap] = []
    stop = False

    def handle_stop(_signum, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    print(f"总内存: {total_memory / 1024 / 1024:.2f} MB")
    print(f"目标区间: {min_percent:.2f}% - {max_percent:.2f}%")
    print(f"目标中位值: {target_percent:.2f}%")
    print(f"分配块大小: {args.chunk_mb} MB")

    try:
        while not stop:
            used_memory = get_used_memory_bytes(total_memory)
            used_percent = (used_memory / total_memory) * 100 if total_memory > 0 else 0.0
            target_used = int(total_memory * target_percent / 100.0)
            if used_percent < min_percent:
                need_allocate = max(0, target_used - used_memory)
                allocate_bytes = min(chunk_bytes, need_allocate) or chunk_bytes
                allocated_blocks.append(allocate_block(allocate_bytes))
                print(f"当前内存使用率: {used_percent:.2f}% | 新增分配: {allocate_bytes / 1024 / 1024:.2f} MB | 块数: {len(allocated_blocks)}")
            elif used_percent > max_percent and allocated_blocks:
                block = allocated_blocks.pop()
                released_bytes = block.size()
                block.close()
                print(f"当前内存使用率: {used_percent:.2f}% | 释放分配: {released_bytes / 1024 / 1024:.2f} MB | 块数: {len(allocated_blocks)}")
            else:
                print(f"当前内存使用率: {used_percent:.2f}% | 保持不变 | 块数: {len(allocated_blocks)}")
            time.sleep(max(1.0, args.check_interval))
    finally:
        for block in allocated_blocks:
            try:
                block.close()
            except Exception:
                pass
        print("内存压力脚本已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
