#!/usr/bin/env python3
"""清理共享 CHAT_DATA_HOME/threads/*/runtime 派生数据。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from utils.logger import get_logger  # noqa: E402
from utils.redis_lock import file_lock, redis_lease, redis_lease_enabled  # noqa: E402
from utils.shared_runtime_cleanup import cleanup_shared_runtime  # noqa: E402
from utils.storage_paths import THREADS_DIR  # noqa: E402

logger = get_logger(__name__)
_RUNTIME_GC_LOCK_KEY = "chatdata:lock:runtime:gc"


async def _cleanup_in_thread(*, max_age_seconds: float, dry_run: bool) -> dict[str, list[str]]:
    return await asyncio.to_thread(
        cleanup_shared_runtime,
        max_age_seconds=max_age_seconds,
        dry_run=dry_run,
    )


async def _run_cleanup(*, max_age_seconds: float, dry_run: bool) -> dict[str, list[str]]:
    """GC 与物化写侧共享跨节点租约，避免删到正在构建的目录。

    Redis 已配置时只认 Redis 租约：拿不到说明别的节点正在 GC，直接跳过，
    绝不能再降级文件锁并发删除。Redis 未配置时才用同机文件锁兜底。
    """
    if redis_lease_enabled():
        async with redis_lease(_RUNTIME_GC_LOCK_KEY, ttl_seconds=1800) as acquired:
            if not acquired:
                logger.warning("另一个 GC 持有 runtime 租约，本节点跳过本轮清理")
                return {"semantic": [], "skill_tmp": []}
            return await _cleanup_in_thread(max_age_seconds=max_age_seconds, dry_run=dry_run)
    async with file_lock(THREADS_DIR.parent / ".runtime-gc.lock"):
        return await _cleanup_in_thread(max_age_seconds=max_age_seconds, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description="清理过期共享 runtime 派生数据")
    parser.add_argument("--max-age-days", type=float, default=7.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        _run_cleanup(
            max_age_seconds=args.max_age_days * 86400,
            dry_run=args.dry_run,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
