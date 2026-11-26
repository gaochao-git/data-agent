"""共享 runtime 写入的 Redis 租约降级行为。"""

from __future__ import annotations

import asyncio

import pytest

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


class _FakeRedis:
    def __init__(self, *, set_result: bool = True, set_error: Exception | None = None):
        self.set_result = set_result
        self.set_error = set_error
        self.set_calls: list[tuple[str, str, bool, int]] = []
        self.released = 0
        self.closed = False

    async def set(self, key: str, token: str, *, nx: bool, ex: int) -> bool:
        self.set_calls.append((key, token, nx, ex))
        if self.set_error is not None:
            raise self.set_error
        return self.set_result

    async def eval(self, *_args: object) -> int:
        self.released += 1
        return 1

    async def aclose(self) -> None:
        self.closed = True


def _enable_redis(monkeypatch, fake: _FakeRedis) -> None:
    monkeypatch.setenv("CHAT_DATA_REDIS_SENTINELS", "127.0.0.1:26379")
    monkeypatch.setattr(redis_lock, "_build_client", lambda: fake)


def test_lease_success_releases_token(monkeypatch):
    fake = _FakeRedis()
    _enable_redis(monkeypatch, fake)

    async def _run() -> bool:
        async with redis_lock.redis_lease("test:lock") as acquired:
            return acquired

    assert asyncio.run(_run()) is True
    assert fake.set_calls and fake.set_calls[0][0] == "test:lock"
    assert fake.set_calls[0][2] is True
    assert fake.released == 1
    assert fake.closed is True


def test_lease_busy_degrades_without_acquiring(monkeypatch):
    fake = _FakeRedis(set_result=False)
    _enable_redis(monkeypatch, fake)
    monkeypatch.setattr(redis_lock, "LEASE_WAIT_SECONDS", 0.02)
    monkeypatch.setattr(redis_lock, "LEASE_POLL_SECONDS", 0.001)

    async def _run() -> bool:
        async with redis_lock.redis_lease("test:lock") as acquired:
            return acquired

    assert asyncio.run(_run()) is False
    assert fake.released == 0
    assert fake.closed is True


def test_lease_error_degrades(monkeypatch):
    fake = _FakeRedis(set_error=ConnectionError("redis down"))
    _enable_redis(monkeypatch, fake)

    async def _run() -> bool:
        async with redis_lock.redis_lease("test:lock") as acquired:
            return acquired

    assert asyncio.run(_run()) is False
    assert fake.closed is True


def test_file_lock_timeout(monkeypatch, tmp_path):
    def _always_busy(_fd: int, _operation: int) -> None:
        raise BlockingIOError

    monkeypatch.setattr(redis_lock.fcntl, "flock", _always_busy)

    async def _run() -> None:
        await redis_lock._acquire_file_lock(tmp_path / "test.lock", timeout=0.01)

    with pytest.raises(redis_lock.FileLockTimeoutError):
        asyncio.run(_run())
