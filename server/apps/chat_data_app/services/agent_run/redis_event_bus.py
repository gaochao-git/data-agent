"""Redis Streams transport for live Agent Run events."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from redis.asyncio import Redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry


RUN_STREAM_PREFIX = "chatdata:run:"
RUN_STREAM_SUFFIX = ":events"
PERSIST_STREAM_KEY = "chatdata:run-events:persist"
PERSIST_GROUP = "chatdata-run-event-persistence"
STREAM_TTL_SECONDS = max(int(os.getenv("CHAT_DATA_REDIS_STREAM_TTL_SECONDS", "86400")), 60)
REDIS_HEALTH_CHECK_INTERVAL_SECONDS = max(
    int(os.getenv("CHAT_DATA_REDIS_HEALTH_CHECK_INTERVAL_SECONDS", "30")),
    0,
)
REDIS_CONNECT_TIMEOUT_SECONDS = max(
    float(os.getenv("CHAT_DATA_REDIS_CONNECT_TIMEOUT_SECONDS", "5")),
    1.0,
)
REDIS_COMMAND_RETRIES = max(int(os.getenv("CHAT_DATA_REDIS_COMMAND_RETRIES", "2")), 0)
REDIS_COMMAND_RETRY = Retry(
    ExponentialBackoff(base=0.1, cap=1.0),
    retries=REDIS_COMMAND_RETRIES,
)

_PUBLISH_SCRIPT = """
local existing_stream_id = redis.call('GET', KEYS[4])
if existing_stream_id then
  return {existing_stream_id, 'duplicate'}
end
if redis.call('EXISTS', KEYS[3]) == 0 and tonumber(ARGV[7]) > 0 then
  redis.call('SET', KEYS[3], ARGV[7])
end
local seq = redis.call('INCR', KEYS[3])
local seq_text = tostring(seq)
local stream_id = redis.call('XADD', KEYS[1], '*',
  'run_id', ARGV[1], 'conversation_id', ARGV[2], 'user_id', ARGV[3],
  'event_id', ARGV[4], 'event_type', ARGV[5], 'sequence_no', seq_text,
  'payload', ARGV[6])
redis.call('XADD', KEYS[2], '*',
  'run_id', ARGV[1], 'conversation_id', ARGV[2], 'user_id', ARGV[3],
  'event_id', ARGV[4], 'event_type', ARGV[5], 'sequence_no', seq_text,
  'payload', ARGV[6], 'stream_id', stream_id)
redis.call('EXPIRE', KEYS[1], ARGV[8])
redis.call('SET', KEYS[4], stream_id, 'EX', ARGV[8])
return {stream_id, seq_text}
"""


class RedisEventBus:
    """Publish and consume Run events using Redis Streams."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or os.getenv("CHAT_DATA_REDIS_URL", "").strip()
        self._redis: Redis | None = None

    @property
    def redis(self) -> Redis:
        if self._redis is None:
            if not self._url:
                raise ValueError("CHAT_DATA_REDIS_URL 未配置")
            self._redis = Redis.from_url(
                self._url,
                decode_responses=True,
                health_check_interval=REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
                socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
                socket_keepalive=True,
                retry=REDIS_COMMAND_RETRY,
            )
        return self._redis

    async def start(self) -> None:
        await self.redis.ping()
        await self._ensure_persistence_group()

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def publish(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        event_id: str,
        event_type: str,
        payload: str,
        initial_sequence: int = 0,
    ) -> str:
        result = await self.redis.eval(
            _PUBLISH_SCRIPT,
            4,
            self.run_stream_key(run_id),
            PERSIST_STREAM_KEY,
            f"{self.run_stream_key(run_id)}:sequence",
            self.event_dedupe_key(run_id, event_id),
            run_id,
            conversation_id,
            user_id,
            event_id,
            event_type,
            payload,
            str(initial_sequence),
            str(STREAM_TTL_SECONDS),
        )
        return str(result[0])

    async def sequence_exists(self, run_id: str) -> bool:
        return bool(await self.redis.exists(f"{self.run_stream_key(run_id)}:sequence"))

    async def read_run(self, run_id: str, cursor: str, *, block_ms: int = 15000, count: int = 100) -> list[dict[str, str]]:
        result = await self.redis.xread(
            {self.run_stream_key(run_id): cursor},
            count=count,
            block=block_ms,
        )
        return [self._decode_event(stream_id, fields) for _key, rows in result for stream_id, fields in rows]

    async def run_stream_exists(self, run_id: str) -> bool:
        return bool(await self.redis.exists(self.run_stream_key(run_id)))

    async def latest_run_event_id(self, run_id: str) -> str | None:
        """Return the current tail cursor without consuming the stream."""
        event = await self.latest_run_event(run_id)
        return event["stream_id"] if event else None

    async def latest_run_event(self, run_id: str) -> dict[str, str] | None:
        rows = await self.redis.xrevrange(self.run_stream_key(run_id), count=1)
        if not rows:
            return None
        stream_id, fields = rows[0]
        return self._decode_event(stream_id, fields)

    async def run_event_sequence(self, run_id: str, event_id: str) -> int | None:
        """Resolve a live Redis cursor to its Run-local sequence number."""
        rows = await self.redis.xrange(
            self.run_stream_key(run_id),
            min=event_id,
            max=event_id,
            count=1,
        )
        if not rows:
            return None
        return int(rows[0][1].get("sequence_no") or 0)

    async def read_persistence_batch(
        self,
        *,
        consumer: str,
        count: int = 100,
        block_ms: int = 5000,
    ) -> list[tuple[str, dict[str, str]]]:
        result = await self.redis.xreadgroup(
            groupname=PERSIST_GROUP,
            consumername=consumer,
            streams={PERSIST_STREAM_KEY: ">"},
            count=count,
            block=block_ms,
        )
        return [(stream_id, fields) for _key, rows in result for stream_id, fields in rows]

    async def reclaim_persistence_batch(
        self,
        *,
        consumer: str,
        min_idle_ms: int = 60000,
        count: int = 100,
    ) -> list[tuple[str, dict[str, str]]]:
        result = await self.redis.xautoclaim(
            PERSIST_STREAM_KEY,
            PERSIST_GROUP,
            consumer,
            min_idle_ms,
            start_id="0-0",
            count=count,
        )
        rows = result[1] if len(result) > 1 else []
        return [(stream_id, fields) for stream_id, fields in rows]

    async def ack_persistence(self, stream_ids: list[str]) -> None:
        if stream_ids:
            await self.redis.xack(PERSIST_STREAM_KEY, PERSIST_GROUP, *stream_ids)

    async def expire_run_stream(self, run_id: str) -> None:
        await self.redis.expire(self.run_stream_key(run_id), STREAM_TTL_SECONDS)

    async def _ensure_persistence_group(self) -> None:
        try:
            await self.redis.xgroup_create(PERSIST_STREAM_KEY, PERSIST_GROUP, id="0-0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    @staticmethod
    def run_stream_key(run_id: str) -> str:
        return f"{RUN_STREAM_PREFIX}{run_id}{RUN_STREAM_SUFFIX}"

    @staticmethod
    def event_dedupe_key(run_id: str, event_id: str) -> str:
        return f"{RUN_STREAM_PREFIX}{run_id}:event:{event_id}"

    @staticmethod
    def _decode_event(stream_id: str, fields: Mapping[str, Any]) -> dict[str, str]:
        return {
            "stream_id": stream_id,
            "run_id": str(fields.get("run_id") or ""),
            "conversation_id": str(fields.get("conversation_id") or ""),
            "user_id": str(fields.get("user_id") or ""),
            "event_id": str(fields.get("event_id") or ""),
            "event_type": str(fields.get("event_type") or "message"),
            "sequence_no": str(fields.get("sequence_no") or "0"),
            "payload": str(fields.get("payload") or ""),
        }


def build_redis_event_bus() -> RedisEventBus:
    return RedisEventBus()
