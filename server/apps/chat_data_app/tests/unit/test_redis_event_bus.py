import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.services.agent_run.redis_event_bus import RedisEventBus


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
    )

    assert stream_id == "1740000000000-0"
    assert len(redis.eval_calls) == 1
    script, numkeys, args = redis.eval_calls[0]
    assert numkeys == 3
    assert "redis.call('EXPIRE', KEYS[1], ARGV[8])" in script
    assert args[-1].isdigit()
