"""共享 runtime 写入的 Redis 租约降级行为。"""

from __future__ import annotations

import asyncio

from utils import redis_lock


def test_lease_disabled_without_sentinel(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_REDIS_SENTINELS", raising=False)

    async def _run() -> bool:
        async with redis_lock.redis_lease("test:lock") as acquired:
            return acquired

    assert asyncio.run(_run()) is False


def test_lease_keys_are_namespaced():
    assert redis_lock.runtime_skills_lease_key("u1") == "chatdata:lock:runtime:skills:u1"
    assert redis_lock.semantic_lease_key("u1", "ds1") == "chatdata:lock:runtime:semantic:u1:ds1"
