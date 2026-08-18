import os
import sys
from unittest.mock import patch

import pytest
from redis.exceptions import ResponseError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.services.agent_run.redis_event_bus import RedisEventBus, RedisRunOwnerMismatchError


class _FakeRedis:
    async def xread(self, streams, count, block):
        assert streams == {"chatdata:run:run-1:events": "0-0"}
        assert count == 100
        assert block == 15000
        return [
            ("chatdata:run:run-1:events", [
                ("1740000000000-0", {
                    "run_id": "run-1",
                    "conversation_id": "conv-1",
                    "user_id": "user-1",
                    "event_id": "event-1",
                    "event_type": "message",
                    "sequence_no": "1",
                    "payload": "event: message\\ndata: {}\\n\\n",
                }),
            ]),
        ]


@pytest.mark.asyncio
async def test_read_run_preserves_redis_stream_id():
    bus = RedisEventBus("redis://unused")
    bus._redis = _FakeRedis()

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
    bus = RedisEventBus("redis://unused")
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
    assert numkeys == 5
    assert "redis.call('EXPIRE', KEYS[1], ARGV[8])" in script
    assert "redis.call('EXPIRE', KEYS[5], ARGV[8])" in script
    assert "redis.call('GET', KEYS[4])" in script
    assert "redis.call('SET', KEYS[4], stream_id, 'EX', ARGV[8])" in script
    assert args[3] == "chatdata:run:run-1:event:event-1"
    assert args[4] == "chatdata:run:run-1:owner"
    assert args[-2].isdigit()
    assert args[-1] == "1:owner-1"


def test_redis_client_uses_health_check_keepalive_and_retry_settings():
    bus = RedisEventBus("redis://unused")
    with patch(
        "apps.chat_data_app.services.agent_run.redis_event_bus.Redis.from_url",
        return_value=object(),
    ) as from_url:
        assert bus.redis is not None

    kwargs = from_url.call_args.kwargs
    assert kwargs["decode_responses"] is True
    assert kwargs["health_check_interval"] == 30
    assert kwargs["socket_keepalive"] is True
    assert kwargs["socket_connect_timeout"] == 5.0
    assert kwargs["retry"]._retries == 2


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
    bus = RedisEventBus("redis://unused")
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

    assert await bus.publish(
        run_id="run-1",
        conversation_id="conv-1",
        user_id="user-1",
        event_id="event-2",
        event_type="message",
        payload="event: message\ndata: {}\n\n",
        owner_token="2:new",
    ) == "1740000000000-0"


@pytest.mark.asyncio
async def test_renew_run_owner_requires_exact_owner_token():
    class OwnerRedis:
        owner = "2:current"

        async def eval(self, script, _numkeys, *_args):
            assert "EXPIRE" in script
            return 1 if _args[1] == self.owner else 0

    bus = RedisEventBus("redis://unused")
    bus._redis = OwnerRedis()

    await bus.renew_run_owner("run-1", "2:current")
    with pytest.raises(RedisRunOwnerMismatchError):
        await bus.renew_run_owner("run-1", "1:stale")
