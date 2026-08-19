"""Redis Streams transport for live Agent Run events."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from redis.asyncio import Redis
from redis.backoff import ExponentialBackoff
from redis.exceptions import ResponseError
from redis.retry import Retry


RUN_STREAM_PREFIX = "chatdata:run:"
RUN_STREAM_SUFFIX = ":events"
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
local current_owner = redis.call('GET', KEYS[4])
if not current_owner or current_owner ~= ARGV[8] then
  return redis.error_reply('RUN_OWNER_MISMATCH')
end
local existing_stream_id = redis.call('GET', KEYS[3])
if existing_stream_id then
  return {existing_stream_id, 'duplicate'}
end
local seq = redis.call('INCR', KEYS[2])
local seq_text = tostring(seq)
local stream_id = redis.call('XADD', KEYS[1], '*',
  'run_id', ARGV[1], 'conversation_id', ARGV[2], 'user_id', ARGV[3],
  'event_id', ARGV[4], 'event_type', ARGV[5], 'sequence_no', seq_text,
  'payload', ARGV[6])
redis.call('EXPIRE', KEYS[1], ARGV[7])
redis.call('EXPIRE', KEYS[2], ARGV[7])
redis.call('EXPIRE', KEYS[4], ARGV[7])
redis.call('SET', KEYS[3], stream_id, 'EX', ARGV[7])
return {stream_id, seq_text}
"""

_CLAIM_OWNER_SCRIPT = """
local incoming = ARGV[1]
local incoming_sep = string.find(incoming, ':')
local incoming_generation = tonumber(string.sub(incoming, 1, incoming_sep - 1))
local current = redis.call('GET', KEYS[1])
if current then
  if current == incoming then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
    return 1
  end
  local current_sep = string.find(current, ':')
  local current_generation = current_sep and tonumber(string.sub(current, 1, current_sep - 1)) or 0
  if current_generation >= incoming_generation then
    return 0
  end
end
redis.call('SET', KEYS[1], incoming, 'EX', ARGV[2])
return 1
"""

_RELEASE_OWNER_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

_RENEW_OWNER_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""


class RedisRunOwnerMismatchError(RuntimeError):
    """The Redis event publisher no longer owns the Run."""


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
        owner_token: str,
    ) -> str:
        try:
            result = await self.redis.eval(
                _PUBLISH_SCRIPT,
                4,
                self.run_stream_key(run_id),
                f"{self.run_stream_key(run_id)}:sequence",
                self.event_dedupe_key(run_id, event_id),
                self.run_owner_key(run_id),
                run_id,
                conversation_id,
                user_id,
                event_id,
                event_type,
                payload,
                str(STREAM_TTL_SECONDS),
                owner_token,
            )
        except ResponseError as exc:
            if "RUN_OWNER_MISMATCH" in str(exc):
                raise RedisRunOwnerMismatchError(run_id) from exc
            raise
        return str(result[0])

    async def claim_run_owner(self, run_id: str, owner_token: str) -> None:
        claimed = await self.redis.eval(
            _CLAIM_OWNER_SCRIPT,
            1,
            self.run_owner_key(run_id),
            owner_token,
            str(STREAM_TTL_SECONDS),
        )
        if int(claimed or 0) != 1:
            raise RedisRunOwnerMismatchError(run_id)

    async def release_run_owner(self, run_id: str, owner_token: str) -> None:
        await self.redis.eval(_RELEASE_OWNER_SCRIPT, 1, self.run_owner_key(run_id), owner_token)

    async def renew_run_owner(self, run_id: str, owner_token: str) -> None:
        renewed = await self.redis.eval(
            _RENEW_OWNER_SCRIPT,
            1,
            self.run_owner_key(run_id),
            owner_token,
            str(STREAM_TTL_SECONDS),
        )
        if int(renewed or 0) != 1:
            raise RedisRunOwnerMismatchError(run_id)

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

    async def expire_run_stream(self, run_id: str) -> None:
        await self.redis.expire(self.run_stream_key(run_id), STREAM_TTL_SECONDS)

    @staticmethod
    def run_stream_key(run_id: str) -> str:
        return f"{RUN_STREAM_PREFIX}{run_id}{RUN_STREAM_SUFFIX}"

    @staticmethod
    def event_dedupe_key(run_id: str, event_id: str) -> str:
        return f"{RUN_STREAM_PREFIX}{run_id}:event:{event_id}"

    @staticmethod
    def run_owner_key(run_id: str) -> str:
        return f"{RUN_STREAM_PREFIX}{run_id}:owner"

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
