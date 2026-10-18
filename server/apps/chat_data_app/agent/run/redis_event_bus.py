"""Redis Streams transport for live Agent Run events."""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.sentinel import Sentinel
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ReadOnlyError, ResponseError, TimeoutError
from redis.retry import Retry

from utils.crypto import decrypt_env_value

RUN_STREAM_PREFIX = "chatdata:run:"
RUN_STREAM_SUFFIX = ":events"
STREAM_TTL_SECONDS = max(int(os.getenv("CHAT_DATA_REDIS_STREAM_TTL_SECONDS", "3600")), 60)
REDIS_HEALTH_CHECK_INTERVAL_SECONDS = max(
    int(os.getenv("CHAT_DATA_REDIS_HEALTH_CHECK_INTERVAL_SECONDS", "30")),
    0,
)
REDIS_CONNECT_TIMEOUT_SECONDS = max(
    float(os.getenv("CHAT_DATA_REDIS_CONNECT_TIMEOUT_SECONDS", "5")),
    1.0,
)
# 连接档位按命令类别拆成两个客户端（同一 Sentinel）：失败探测成本与
# 「XREAD 需要长时间阻塞」解耦，这是把 Redis 故障下的失败反馈压到秒级的关键。
# —— 读档（仅 XREAD 阻塞命令）——
# socket 超时只需略大于 block；健康时空转一个 block 周期返回空，
# 连接被静默掐断（NAT/黑洞）时最多 socket_timeout 秒解卡，
# 之后由 SSE 订阅循环降级到 MySQL 快照兜底。不重试：订阅循环天然按周期重读。
REDIS_READ_BLOCK_SECONDS = max(float(os.getenv("CHAT_DATA_REDIS_READ_BLOCK_SECONDS", "6")), 1.0)
REDIS_READ_SOCKET_TIMEOUT_SECONDS = max(
    float(os.getenv("CHAT_DATA_REDIS_READ_SOCKET_TIMEOUT_SECONDS", "10")),
    REDIS_READ_BLOCK_SECONDS + 2.0,
)
REDIS_READ_RETRY = Retry(
    ExponentialBackoff(base=0.1, cap=1.0),
    retries=0,
    supported_errors=(RedisConnectionError, TimeoutError, ReadOnlyError, socket.timeout),
)
# —— 操作档（eval/exists/xrevrange 等非阻塞命令）——
# 健康链路毫秒级完成，短超时仍有两个数量级余量；claim/publish 走此档，
# Redis 故障时秒级失败让 Run 快速收口，而不是按旧档 20s×2 重试拖到 40s+。
REDIS_OPS_SOCKET_TIMEOUT_SECONDS = max(
    float(os.getenv("CHAT_DATA_REDIS_OPS_SOCKET_TIMEOUT_SECONDS", "4")),
    1.0,
)
REDIS_OPS_COMMAND_RETRIES = max(int(os.getenv("CHAT_DATA_REDIS_OPS_COMMAND_RETRIES", "1")), 0)
REDIS_OPS_RETRY = Retry(
    ExponentialBackoff(base=0.1, cap=1.0),
    retries=REDIS_OPS_COMMAND_RETRIES,
    supported_errors=(RedisConnectionError, TimeoutError, ReadOnlyError, socket.timeout),
)


def _socket_keepalive_options() -> dict[int, int]:
    """跨平台 keepalive 探测参数：空闲 30s 即探测，按 10s 间隔连探 3 次判死。

    ``socket_keepalive=True`` 不带 options 时用 OS 默认间隔（Linux tcp_keepalive_time
    默认 7200s），对公网链路 NAT 空闲回收（通常 300s 内静默掐断半开连接）形同虚设，
    操作档间歇使用的连接池连接会在下次取用时 PING 挂满 socket_timeout。显式缩短
    探测间隔，让死连接在一分钟内被主动清理。Linux/macOS 的 setsockopt 键名不同，
    逐键探测构建，未知平台退化为空表（等价于系统默认行为）。
    """
    options: dict[int, int] = {}
    idle = getattr(socket, "TCP_KEEPIDLE", None)
    if idle is not None:
        options[idle] = 30
    else:
        mac_idle = getattr(socket, "TCP_KEEPALIVE", None)
        if mac_idle is not None:
            options[mac_idle] = 30
    interval = getattr(socket, "TCP_KEEPINTVL", None)
    if interval is not None:
        options[interval] = 10
    count = getattr(socket, "TCP_KEEPCNT", None)
    if count is not None:
        options[count] = 3
    return options


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


def _parse_sentinel_addrs(raw: str) -> list[tuple[str, int]]:
    """解析 ``host:port[,host:port...]``；空串返回空列表，格式错误抛 ValueError。"""
    addrs: list[tuple[str, int]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        host, sep, port = part.rpartition(":")
        if not sep or not host:
            raise ValueError(f"CHAT_DATA_REDIS_SENTINELS 条目格式应为 host:port: {part!r}")
        try:
            addrs.append((host, int(port)))
        except ValueError as exc:
            raise ValueError(f"CHAT_DATA_REDIS_SENTINELS 端口无效: {part!r}") from exc
    return addrs


class RedisEventBus:
    """Publish and consume Run events using Redis Streams.

    仅支持哨兵模式：``CHAT_DATA_REDIS_SENTINELS=host:port[,host:port...]`` +
    ``CHAT_DATA_REDIS_MASTER_NAME``，master 密码经 ``CHAT_DATA_REDIS_PASSWORD``
    （SM4 密文，与数据库密码同规范）。sentinel 未单独配置密码时不发送 AUTH
    （sentinel 未开认证是常见形态，继承 master 密码反而会 AUTH 报错）；
    sentinel 单独开了认证时用 ``CHAT_DATA_REDIS_SENTINEL_PASSWORD`` 指定（SM4 密文）。
    逻辑库经 ``CHAT_DATA_REDIS_DB`` 选择（缺省 0）。
    master 地址在连接建立时向 sentinel 解析；failover 后旧连接上的命令会
    收到 ConnectionError/ReadOnlyError：操作档由命令级重试重建连接跟随新主；
    读档不重试（见 REDIS_READ_RETRY 注释），由 SSE 订阅循环按周期重读自愈。
    """

    def __init__(self) -> None:
        self._sentinel_addrs = _parse_sentinel_addrs(os.getenv("CHAT_DATA_REDIS_SENTINELS", ""))
        self._master_name = os.getenv("CHAT_DATA_REDIS_MASTER_NAME", "").strip()
        # 逻辑库编号：等价于旧 CHAT_DATA_REDIS_URL 的 /N 后缀，缺省 0 行为不变
        self._db = max(int(os.getenv("CHAT_DATA_REDIS_DB", "0")), 0)
        self._password = decrypt_env_value("CHAT_DATA_REDIS_PASSWORD") or None
        if str(os.getenv("CHAT_DATA_REDIS_SENTINEL_PASSWORD") or "").strip():
            self._sentinel_password = decrypt_env_value("CHAT_DATA_REDIS_SENTINEL_PASSWORD") or None
        else:
            self._sentinel_password = None
        self._sentinel: Sentinel | None = None
        self._redis: Redis | None = None
        self._read_redis: Redis | None = None

    def _build_sentinel(self) -> Sentinel:
        if self._sentinel is None:
            if not self._sentinel_addrs:
                raise ValueError("CHAT_DATA_REDIS_SENTINELS 未配置")
            if not self._master_name:
                raise ValueError("CHAT_DATA_REDIS_MASTER_NAME 未配置")
            self._sentinel = Sentinel(
                self._sentinel_addrs,
                sentinel_kwargs={
                    "password": self._sentinel_password,
                    "socket_connect_timeout": REDIS_CONNECT_TIMEOUT_SECONDS,
                    "socket_timeout": REDIS_CONNECT_TIMEOUT_SECONDS,
                },
                password=self._password,
                socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
            )
        return self._sentinel

    @property
    def redis(self) -> Redis:
        """操作档客户端：除 XREAD 外的全部命令（claim/publish/exists 等）。"""
        if self._redis is None:
            self._redis = self._build_sentinel().master_for(
                self._master_name,
                db=self._db,
                decode_responses=True,
                health_check_interval=REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
                socket_keepalive=True,
                socket_keepalive_options=_socket_keepalive_options(),
                socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
                socket_timeout=REDIS_OPS_SOCKET_TIMEOUT_SECONDS,
                retry=REDIS_OPS_RETRY,
            )
        return self._redis

    @property
    def read_redis(self) -> Redis:
        """读档客户端：仅承载 XREAD 阻塞读，超时档位与操作档解耦。"""
        if self._read_redis is None:
            self._read_redis = self._build_sentinel().master_for(
                self._master_name,
                db=self._db,
                decode_responses=True,
                health_check_interval=REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
                socket_keepalive=True,
                socket_keepalive_options=_socket_keepalive_options(),
                socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
                socket_timeout=REDIS_READ_SOCKET_TIMEOUT_SECONDS,
                retry=REDIS_READ_RETRY,
            )
        return self._read_redis

    async def start(self) -> None:
        # redis-py 桩对 sync/async 双客户端返回联合类型，asyncio 客户端实际返回 Awaitable
        await self.redis.ping()  # type: ignore[misc]

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        if self._read_redis is not None:
            await self._read_redis.aclose()
            self._read_redis = None

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
            )  # type: ignore[misc]
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
        )  # type: ignore[misc]
        if int(claimed or 0) != 1:
            raise RedisRunOwnerMismatchError(run_id)

    async def release_run_owner(self, run_id: str, owner_token: str) -> None:
        await self.redis.eval(_RELEASE_OWNER_SCRIPT, 1, self.run_owner_key(run_id), owner_token)  # type: ignore[misc]

    async def renew_run_owner(self, run_id: str, owner_token: str) -> None:
        renewed = await self.redis.eval(
            _RENEW_OWNER_SCRIPT,
            1,
            self.run_owner_key(run_id),
            owner_token,
            str(STREAM_TTL_SECONDS),
        )  # type: ignore[misc]
        if int(renewed or 0) != 1:
            raise RedisRunOwnerMismatchError(run_id)

    async def read_run(
        self, run_id: str, cursor: str, *, block_ms: int = int(REDIS_READ_BLOCK_SECONDS * 1000), count: int = 100
    ) -> list[dict[str, str]]:
        result = await self.read_redis.xread(
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
