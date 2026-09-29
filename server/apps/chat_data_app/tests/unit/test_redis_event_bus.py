import os
import sys
from unittest.mock import patch

import pytest
from redis.exceptions import ResponseError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.agent.run.redis_event_bus import RedisEventBus, RedisRunOwnerMismatchError
from utils.crypto import encrypt_password


class _FakeRedis:
    async def xread(self, streams, count, block):
        assert streams == {"chatdata:run:run-1:events": "0-0"}
        assert count == 100
        assert block == 6000
        return [
            (
                "chatdata:run:run-1:events",
                [
                    (
                        "1740000000000-0",
                        {
                            "run_id": "run-1",
                            "conversation_id": "conv-1",
                            "user_id": "user-1",
                            "event_id": "event-1",
                            "event_type": "message",
                            "sequence_no": "1",
                            "payload": "event: message\\ndata: {}\\n\\n",
                        },
                    ),
                ],
            ),
        ]


@pytest.mark.asyncio
async def test_read_run_preserves_redis_stream_id():
    bus = RedisEventBus()
    bus._read_redis = _FakeRedis()

    events = await bus.read_run("run-1", "0-0")

    assert events[0]["stream_id"] == "1740000000000-0"
    assert events[0]["sequence_no"] == "1"
    assert events[0]["payload"].startswith("event: message")


@pytest.mark.asyncio
async def test_publish_sets_stream_ttl_in_same_redis_script():
    class PublishRedis:
        def __init__(self):
            self.eval_calls = []

        async def eval(self, script, numkeys, *args):
            self.eval_calls.append((script, numkeys, args))
            return ["1740000000000-0", "1"]

    redis = PublishRedis()
    bus = RedisEventBus()
    bus._redis = redis

    stream_id = await bus.publish(
        run_id="run-1",
        conversation_id="conv-1",
        user_id="user-1",
        event_id="event-1",
        event_type="message",
        payload="event: message\ndata: {}\n\n",
        owner_token="1:owner-1",
    )

    assert stream_id == "1740000000000-0"
    assert len(redis.eval_calls) == 1
    script, numkeys, args = redis.eval_calls[0]
    assert numkeys == 4
    assert "XADD" in script
    assert script.count("XADD") == 1  # 只写实时 Stream，无持久化副本
    assert "redis.call('EXPIRE', KEYS[1], ARGV[7])" in script
    assert "redis.call('EXPIRE', KEYS[4], ARGV[7])" in script
    assert "redis.call('GET', KEYS[3])" in script
    assert "redis.call('SET', KEYS[3], stream_id, 'EX', ARGV[7])" in script
    assert args[2] == "chatdata:run:run-1:event:event-1"
    assert args[3] == "chatdata:run:run-1:owner"
    assert args[-2].isdigit()
    assert args[-1] == "1:owner-1"


@pytest.mark.asyncio
async def test_newer_owner_generation_fences_old_publisher():
    class OwnerRedis:
        def __init__(self):
            self.owner = None

        async def eval(self, script, _numkeys, *_args):
            if "incoming_generation" in script:
                incoming = _args[1]
                incoming_generation = int(incoming.split(":", 1)[0])
                current_generation = int(self.owner.split(":", 1)[0]) if self.owner else 0
                if self.owner and current_generation >= incoming_generation and self.owner != incoming:
                    return 0
                self.owner = incoming
                return 1
            if "RUN_OWNER_MISMATCH" in script:
                owner_token = _args[-1]
                if owner_token != self.owner:
                    raise ResponseError("RUN_OWNER_MISMATCH")
                return ["1740000000000-0", "1"]
            return 0

    redis = OwnerRedis()
    bus = RedisEventBus()
    bus._redis = redis

    await bus.claim_run_owner("run-1", "1:old")
    await bus.claim_run_owner("run-1", "2:new")

    with pytest.raises(RedisRunOwnerMismatchError):
        await bus.publish(
            run_id="run-1",
            conversation_id="conv-1",
            user_id="user-1",
            event_id="event-1",
            event_type="message",
            payload="event: message\ndata: {}\n\n",
            owner_token="1:old",
        )

    assert (
        await bus.publish(
            run_id="run-1",
            conversation_id="conv-1",
            user_id="user-1",
            event_id="event-2",
            event_type="message",
            payload="event: message\ndata: {}\n\n",
            owner_token="2:new",
        )
        == "1740000000000-0"
    )


@pytest.mark.asyncio
async def test_renew_run_owner_requires_exact_owner_token():
    class OwnerRedis:
        owner = "2:current"

        async def eval(self, script, _numkeys, *_args):
            assert "EXPIRE" in script
            return 1 if _args[1] == self.owner else 0

    bus = RedisEventBus()
    bus._redis = OwnerRedis()

    await bus.renew_run_owner("run-1", "2:current")
    with pytest.raises(RedisRunOwnerMismatchError):
        await bus.renew_run_owner("run-1", "1:stale")


# ---------------------------------------------------------------------------
# 哨兵模式连接
# ---------------------------------------------------------------------------


def test_sentinel_mode_resolves_master_via_sentinel(monkeypatch):
    monkeypatch.setenv("ENCRYPT_KEY", "00" * 16)
    monkeypatch.setenv("CHAT_DATA_REDIS_SENTINELS", "10.0.0.1:26379, 10.0.0.2:26379")
    monkeypatch.setenv("CHAT_DATA_REDIS_MASTER_NAME", "chatdata-master")
    monkeypatch.setenv("CHAT_DATA_REDIS_PASSWORD", encrypt_password("secret"))

    bus = RedisEventBus()
    with patch("apps.chat_data_app.agent.run.redis_event_bus.Sentinel") as sentinel_cls:
        sentinel_cls.return_value.master_for.return_value = object()
        assert bus.redis is not None

    addrs, kwargs = sentinel_cls.call_args.args[0], sentinel_cls.call_args.kwargs
    assert addrs == [("10.0.0.1", 26379), ("10.0.0.2", 26379)]
    assert kwargs["password"] == "secret"
    # sentinel 密码未单独配置时不发送 AUTH（不继承 master 密码）
    assert kwargs["sentinel_kwargs"]["password"] is None
    assert kwargs["socket_connect_timeout"] == 5.0

    master_kwargs = sentinel_cls.return_value.master_for.call_args.kwargs
    assert sentinel_cls.return_value.master_for.call_args.args == ("chatdata-master",)
    assert master_kwargs["decode_responses"] is True
    assert master_kwargs["db"] == 0  # 缺省逻辑库 0，等价于旧 URL 无 /N 后缀
    assert master_kwargs["health_check_interval"] == 30
    assert master_kwargs["socket_keepalive"] is True
    # 操作档：非阻塞命令短超时 + 单次重试，claim/publish 故障时秒级失败
    assert master_kwargs["socket_timeout"] == 4.0
    assert master_kwargs["retry"]._retries == 1
    # failover 窗口旧连接收到 READONLY 错误必须可重试自愈
    from redis.exceptions import ReadOnlyError

    assert ReadOnlyError in master_kwargs["retry"]._supported_errors


def test_sentinel_mode_read_client_uses_relaxed_blocking_profile(monkeypatch):
    """读档客户端（仅 XREAD）：socket 超时受 block 约束而非 20s 旧档，不重试。"""
    monkeypatch.setenv("ENCRYPT_KEY", "00" * 16)
    monkeypatch.setenv("CHAT_DATA_REDIS_SENTINELS", "10.0.0.1:26379")
    monkeypatch.setenv("CHAT_DATA_REDIS_MASTER_NAME", "chatdata-master")

    bus = RedisEventBus()
    with patch("apps.chat_data_app.agent.run.redis_event_bus.Sentinel") as sentinel_cls:
        sentinel_cls.return_value.master_for.return_value = object()
        _ = bus.redis
        _ = bus.read_redis

    assert sentinel_cls.return_value.master_for.call_count == 2
    read_kwargs = sentinel_cls.return_value.master_for.call_args_list[1].kwargs
    assert read_kwargs["db"] == 0
    assert read_kwargs["socket_timeout"] == 10.0
    assert read_kwargs["retry"]._retries == 0


def test_read_socket_timeout_always_exceeds_block_invariant():
    """读档 socket 超时下限恒大于 block，正常阻塞等待不会被误判超时。"""
    from apps.chat_data_app.agent.run.redis_event_bus import (
        REDIS_READ_BLOCK_SECONDS,
        REDIS_READ_SOCKET_TIMEOUT_SECONDS,
    )

    assert REDIS_READ_SOCKET_TIMEOUT_SECONDS >= REDIS_READ_BLOCK_SECONDS + 2.0


def test_sentinel_mode_supports_distinct_encrypted_sentinel_password(monkeypatch):
    monkeypatch.setenv("ENCRYPT_KEY", "00" * 16)
    monkeypatch.setenv("CHAT_DATA_REDIS_SENTINELS", "10.0.0.1:26379")
    monkeypatch.setenv("CHAT_DATA_REDIS_MASTER_NAME", "chatdata-master")
    monkeypatch.setenv("CHAT_DATA_REDIS_PASSWORD", encrypt_password("master-pwd"))
    monkeypatch.setenv("CHAT_DATA_REDIS_SENTINEL_PASSWORD", encrypt_password("sentinel-pwd"))

    bus = RedisEventBus()
    with patch("apps.chat_data_app.agent.run.redis_event_bus.Sentinel") as sentinel_cls:
        sentinel_cls.return_value.master_for.return_value = object()
        assert bus.redis is not None

    kwargs = sentinel_cls.call_args.kwargs
    assert kwargs["password"] == "master-pwd"
    assert kwargs["sentinel_kwargs"]["password"] == "sentinel-pwd"


def test_sentinel_mode_without_sentinel_password_skips_sentinel_auth(monkeypatch):
    """master 有密码而 sentinel 未单独配置：sentinel 连接不发送 AUTH，不继承 master 密码。"""
    monkeypatch.setenv("ENCRYPT_KEY", "00" * 16)
    monkeypatch.setenv("CHAT_DATA_REDIS_SENTINELS", "10.0.0.1:26379")
    monkeypatch.setenv("CHAT_DATA_REDIS_MASTER_NAME", "chatdata-master")
    monkeypatch.setenv("CHAT_DATA_REDIS_PASSWORD", encrypt_password("master-pwd"))
    monkeypatch.delenv("CHAT_DATA_REDIS_SENTINEL_PASSWORD", raising=False)

    bus = RedisEventBus()
    with patch("apps.chat_data_app.agent.run.redis_event_bus.Sentinel") as sentinel_cls:
        sentinel_cls.return_value.master_for.return_value = object()
        assert bus.redis is not None

    kwargs = sentinel_cls.call_args.kwargs
    assert kwargs["password"] == "master-pwd"
    assert kwargs["sentinel_kwargs"]["password"] is None


def test_sentinel_mode_supports_no_password(monkeypatch):
    """哨兵未开认证：两个密码变量都不配置即可，password 传 None。"""
    monkeypatch.setenv("CHAT_DATA_REDIS_SENTINELS", "10.0.0.1:26379")
    monkeypatch.setenv("CHAT_DATA_REDIS_MASTER_NAME", "chatdata-master")
    monkeypatch.delenv("CHAT_DATA_REDIS_PASSWORD", raising=False)
    monkeypatch.delenv("CHAT_DATA_REDIS_SENTINEL_PASSWORD", raising=False)

    bus = RedisEventBus()
    with patch("apps.chat_data_app.agent.run.redis_event_bus.Sentinel") as sentinel_cls:
        sentinel_cls.return_value.master_for.return_value = object()
        assert bus.redis is not None

    kwargs = sentinel_cls.call_args.kwargs
    assert kwargs["password"] is None
    assert kwargs["sentinel_kwargs"]["password"] is None


def test_sentinel_mode_selects_db(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_REDIS_SENTINELS", "10.0.0.1:26379")
    monkeypatch.setenv("CHAT_DATA_REDIS_MASTER_NAME", "chatdata-master")
    monkeypatch.setenv("CHAT_DATA_REDIS_DB", "3")

    bus = RedisEventBus()
    with patch("apps.chat_data_app.agent.run.redis_event_bus.Sentinel") as sentinel_cls:
        sentinel_cls.return_value.master_for.return_value = object()
        assert bus.redis is not None

    assert sentinel_cls.return_value.master_for.call_args.kwargs["db"] == 3


def test_sentinel_mode_requires_master_name(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_REDIS_SENTINELS", "10.0.0.1:26379")
    monkeypatch.delenv("CHAT_DATA_REDIS_MASTER_NAME", raising=False)
    bus = RedisEventBus()
    with pytest.raises(ValueError, match="CHAT_DATA_REDIS_MASTER_NAME"):
        _ = bus.redis


def test_missing_sentinels_raises(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_REDIS_SENTINELS", raising=False)
    bus = RedisEventBus()
    with pytest.raises(ValueError, match="CHAT_DATA_REDIS_SENTINELS"):
        _ = bus.redis


def test_invalid_sentinel_addr_raises(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_REDIS_SENTINELS", "no-port-here")
    with pytest.raises(ValueError, match="host:port"):
        RedisEventBus()
