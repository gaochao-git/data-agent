#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import signal
import time
from pathlib import Path


def get_disk_usage(path: Path) -> tuple[int, int, int]:
    """获取目标路径所在文件系统的总量、已用、可用字节数。"""
    usage = shutil.disk_usage(path)
    return usage.total, usage.used, usage.free


def ensure_output_dir(path: Path) -> None:
    """确保写盘目录存在。"""
    path.mkdir(parents=True, exist_ok=True)


def write_chunk(file_path: Path, size_bytes: int) -> None:
    """写入固定大小的数据块。"""
    chunk = b"\0" * min(size_bytes, 4 * 1024 * 1024)
    remaining = size_bytes
    with open(file_path, "wb") as handle:
        while remaining > 0:
            current = chunk if remaining >= len(chunk) else chunk[:remaining]
            handle.write(current)
            remaining -= len(current)
        handle.flush()
        os.fsync(handle.fileno())


def cleanup_output_dir(path: Path) -> int:
    """清理脚本生成的压力文件。"""
    if not path.exists():
        return 0
    removed = 0
    for item in path.glob("disk-pressure-*.bin"):
        item.unlink(missing_ok=True)
        removed += 1
    return removed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="向指定目录写盘，将磁盘使用率维持在目标区间内。")
    parser.add_argument("--min-percent", type=float, default=22.0, help="目标下限百分比，默认 22")
    parser.add_argument("--max-percent", type=float, default=28.0, help="目标上限百分比，默认 28")
    parser.add_argument("--path", default="./tmp-disk-pressure", help="写盘目录，默认 ./tmp-disk-pressure")
    parser.add_argument("--chunk-mb", type=int, default=256, help="单个文件大小（MB），默认 256")
    parser.add_argument("--check-interval", type=float, default=10.0, help="巡检间隔（秒），默认 10")
    parser.add_argument("--min-free-percent", type=float, default=5.0, help="最小剩余空间百分比保护线，默认 5")
    parser.add_argument("--cleanup", action="store_true", help="清理脚本生成的压力文件后退出")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.path).expanduser().resolve()
    ensure_output_dir(output_dir)

    if args.cleanup:
        removed = cleanup_output_dir(output_dir)
        print(f"已清理文件数: {removed}")
        return 0

    min_percent = min(args.min_percent, args.max_percent)
    max_percent = max(args.min_percent, args.max_percent)
    target_percent = (min_percent + max_percent) / 2.0
    chunk_bytes = max(1, args.chunk_mb) * 1024 * 1024
    stop = False

    def handle_stop(_signum, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    print(f"目标目录: {output_dir}")
    print(f"目标区间: {min_percent:.2f}% - {max_percent:.2f}%")
    print(f"目标中位值: {target_percent:.2f}%")

    file_index = 1
    while not stop:
        total, used, free = get_disk_usage(output_dir)
        used_percent = (used / total) * 100 if total > 0 else 0.0
        free_percent = (free / total) * 100 if total > 0 else 0.0
        print(f"当前磁盘使用率: {used_percent:.2f}% | 剩余空间: {free_percent:.2f}%")

        if free_percent <= args.min_free_percent:
            print(f"剩余空间已低于保护线 {args.min_free_percent:.2f}%，停止写盘")
            return 1

        if used_percent < min_percent:
            target_used = int(total * target_percent / 100.0)
            file_size = min(chunk_bytes, max(1, target_used - used))
            file_path = output_dir / f"disk-pressure-{file_index:05d}.bin"
            print(f"写入文件: {file_path.name} 大小 {file_size / 1024 / 1024:.2f} MB")
            write_chunk(file_path, file_size)
            file_index += 1
            continue

        time.sleep(max(1.0, args.check_interval))

    print("收到停止信号，结束写盘")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
