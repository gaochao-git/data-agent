"""跨进程/跨节点 Redis 租约（共享 runtime 写入保护）。

配置与 Run 事件总线一致：``CHAT_DATA_REDIS_SENTINELS`` / ``CHAT_DATA_REDIS_MASTER_NAME``
/ ``CHAT_DATA_REDIS_PASSWORD``。未配置时由调用方降级本地锁，保证单机/测试可用。
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import time
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from redis.asyncio import Redis
from redis.asyncio.sentinel import Sentinel

from utils.crypto import decrypt_env_value
from utils.logger import get_logger

logger = get_logger(__name__)

LEASE_WAIT_SECONDS = 30.0
LEASE_POLL_SECONDS = 0.1
_CONNECT_TIMEOUT = 5.0
_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def redis_lease_enabled() -> bool:
    return bool(str(os.getenv("CHAT_DATA_REDIS_SENTINELS") or "").strip())


def _parse_sentinels(raw: str) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        host, sep, port = part.rpartition(":")
        if not sep or not host:
            raise ValueError(f"CHAT_DATA_REDIS_SENTINELS 条目格式应为 host:port: {part!r}")
        result.append((host, int(port)))
    return result


def _build_client() -> Redis:
    master_name = str(os.getenv("CHAT_DATA_REDIS_MASTER_NAME") or "").strip()
    if not master_name:
        raise ValueError("CHAT_DATA_REDIS_MASTER_NAME 未配置")
    password = decrypt_env_value("CHAT_DATA_REDIS_PASSWORD") or None
    sentinel_password = (
        decrypt_env_value("CHAT_DATA_REDIS_SENTINEL_PASSWORD") or None
        if str(os.getenv("CHAT_DATA_REDIS_SENTINEL_PASSWORD") or "").strip()
        else None
    )
    sentinel_kwargs: dict[str, object] = {
        "socket_connect_timeout": _CONNECT_TIMEOUT,
        "socket_timeout": _CONNECT_TIMEOUT,
    }
    if sentinel_password:
        sentinel_kwargs["password"] = sentinel_password
    sentinel = Sentinel(
        _parse_sentinels(str(os.getenv("CHAT_DATA_REDIS_SENTINELS") or "")),
        sentinel_kwargs=sentinel_kwargs,
        password=password,
        socket_connect_timeout=_CONNECT_TIMEOUT,
    )
    return sentinel.master_for(
        master_name,
        db=max(int(os.getenv("CHAT_DATA_REDIS_DB", "0")), 0),
        decode_responses=True,
        socket_connect_timeout=_CONNECT_TIMEOUT,
        socket_timeout=_CONNECT_TIMEOUT,
    )


@asynccontextmanager
async def redis_lease(key: str, *, ttl_seconds: int = 300) -> AsyncIterator[bool]:
    """尽力获取跨节点租约；超时或 Redis 不可用时 yield False。"""
    if not redis_lease_enabled():
        yield False
        return
    client: Redis | None = None
    token = uuid4().hex
    acquired = False
    try:
        client = _build_client()
        deadline = time.monotonic() + LEASE_WAIT_SECONDS
        while time.monotonic() < deadline:
            try:
                acquired_result = await cast(
                    Awaitable[Any], client.set(key, token, nx=True, ex=max(int(ttl_seconds), 1))
                )
                if acquired_result:
                    acquired = True
                    break
            except Exception as exc:  # noqa: BLE001 - 降级本地锁
                logger.warning("获取 Redis 租约失败，降级本地锁: key=%s error=%s", key, exc)
                break
            await asyncio.sleep(LEASE_POLL_SECONDS)
        if not acquired:
            logger.warning("Redis 租约等待超时，降级本地锁: key=%s", key)
        yield acquired
    finally:
        if client is not None:
            if acquired:
                try:
                    await cast(Awaitable[Any], client.eval(_RELEASE_SCRIPT, 1, key, token))
                except Exception as exc:  # noqa: BLE001 - TTL 兜底
                    logger.warning("释放 Redis 租约失败: key=%s error=%s", key, exc)
            await client.aclose()


def runtime_skills_lease_key(user_id: str) -> str:
    return f"chatdata:lock:runtime:skills:{user_id}"


def semantic_lease_key(user_id: str, datasource_id: str) -> str:
    return f"chatdata:lock:runtime:semantic:{user_id}:{datasource_id}"


async def _acquire_file_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    await asyncio.to_thread(fcntl.flock, fd, fcntl.LOCK_EX)
    return fd


async def _release_file_lock(fd: int) -> None:
    try:
        await asyncio.to_thread(fcntl.flock, fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@asynccontextmanager
async def file_lock(path: Path) -> AsyncIterator[None]:
    """Redis 不可用时的同机文件锁兜底；跨机房不能依赖该锁。"""
    fd = await _acquire_file_lock(path)
    try:
        yield
    finally:
        await _release_file_lock(fd)
