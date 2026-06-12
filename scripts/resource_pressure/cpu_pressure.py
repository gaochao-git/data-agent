#!/usr/bin/env python3
from __future__ import annotations

import argparse
import multiprocessing as mp
import signal
import time
from dataclasses import dataclass


@dataclass
class WorkerState:
    """记录单个 CPU 压力进程。"""
    process: mp.Process
    stop_event: mp.Event
    duty_cycle: float


def busy_loop(stop_event: mp.Event, duty_cycle: float) -> None:
    """按指定占空比制造单进程 CPU 压力。"""
    active_window = max(0.0, min(1.0, duty_cycle))
    period_seconds = 0.2
    active_seconds = period_seconds * active_window
    idle_seconds = max(0.0, period_seconds - active_seconds)
    while not stop_event.is_set():
        start = time.perf_counter()
        while not stop_event.is_set() and (time.perf_counter() - start) < active_seconds:
            pass
        if idle_seconds > 0:
            stop_event.wait(idle_seconds)


def build_worker_plan(cpu_count: int, target_percent: float) -> list[float]:
    """根据目标总 CPU 百分比生成 worker 占空比分配。"""
    if cpu_count <= 0:
        return [1.0]
    normalized_target = max(0.0, target_percent) / 100.0
    target_cores = cpu_count * normalized_target
    full_workers = int(target_cores)
    remainder = target_cores - full_workers
    plan = [1.0] * full_workers
    if remainder > 0:
        plan.append(remainder)
    return plan


def read_cpu_times() -> tuple[int, int]:
    """读取 Linux /proc/stat 中的总 CPU 时间与空闲时间。"""
    with open("/proc/stat", "r", encoding="utf-8") as handle:
        first_line = handle.readline().strip()
    parts = first_line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        raise RuntimeError("无法读取 /proc/stat")
    values = [int(item) for item in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return total, idle


def sample_cpu_percent(interval_seconds: float) -> float:
    """采样系统总 CPU 使用率。"""
    total1, idle1 = read_cpu_times()
    time.sleep(interval_seconds)
    total2, idle2 = read_cpu_times()
    total_delta = max(1, total2 - total1)
    idle_delta = max(0, idle2 - idle1)
    used_ratio = max(0.0, min(1.0, 1.0 - (idle_delta / total_delta)))
    return used_ratio * 100.0


def stop_worker(worker: WorkerState) -> None:
    """停止单个 worker。"""
    worker.stop_event.set()
    worker.process.join(timeout=2)
    if worker.process.is_alive():
        worker.process.terminate()


def reconcile_workers(workers: list[WorkerState], cpu_count: int, target_percent: float) -> list[WorkerState]:
    """按新的目标百分比调整 worker 数量。"""
    expected_plan = build_worker_plan(cpu_count, target_percent)
    current_count = len(workers)
    expected_count = len(expected_plan)

    if current_count > expected_count:
        redundant = workers[expected_count:]
        workers = workers[:expected_count]
        for worker in redundant:
            stop_worker(worker)

    for index, duty_cycle in enumerate(expected_plan):
        if index < len(workers):
            if abs(workers[index].duty_cycle - duty_cycle) < 0.01:
                continue
            stop_worker(workers[index])
            stop_event = mp.Event()
            process = mp.Process(target=busy_loop, args=(stop_event, duty_cycle), daemon=True)
            process.start()
            workers[index] = WorkerState(process=process, stop_event=stop_event, duty_cycle=duty_cycle)
            continue
        stop_event = mp.Event()
        process = mp.Process(target=busy_loop, args=(stop_event, duty_cycle), daemon=True)
        process.start()
        workers.append(WorkerState(process=process, stop_event=stop_event, duty_cycle=duty_cycle))
    return workers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将机器 CPU 使用率维持在目标区间内。")
    parser.add_argument("--min-percent", type=float, default=22.0, help="目标下限百分比，默认 22")
    parser.add_argument("--max-percent", type=float, default=28.0, help="目标上限百分比，默认 28")
    parser.add_argument("--check-interval", type=float, default=3.0, help="采样与调整间隔（秒），默认 3")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    min_percent = min(args.min_percent, args.max_percent)
    max_percent = max(args.min_percent, args.max_percent)
    target_percent = (min_percent + max_percent) / 2.0
    cpu_count = mp.cpu_count() or 1
    workers: list[WorkerState] = []
    stop = False

    def handle_stop(_signum, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    print(f"CPU 核心数: {cpu_count}")
    print(f"目标区间: {min_percent:.2f}% - {max_percent:.2f}%")
    print(f"目标中位值: {target_percent:.2f}%")

    try:
        while not stop:
            current_percent = sample_cpu_percent(max(0.5, args.check_interval))
            desired_extra = max(0.0, target_percent - current_percent)
            if current_percent > max_percent:
                desired_extra = 0.0
            workers = reconcile_workers(workers, cpu_count, desired_extra)
            print(f"当前 CPU 使用率: {current_percent:.2f}% | 追加压力目标: {desired_extra:.2f}% | worker 数: {len(workers)}")
    finally:
        for worker in workers:
            stop_worker(worker)
        print("CPU 压力脚本已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
