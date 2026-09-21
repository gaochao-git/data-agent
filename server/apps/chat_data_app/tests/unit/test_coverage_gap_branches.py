"""热点模块缺失分支补齐（filter_normalization / tool_results / tool_call_normalizer / redis_event_bus）。"""

from types import SimpleNamespace

import pytest
from redis.exceptions import ResponseError

from apps.chat_data_app.agent import tool_results as tr
from apps.chat_data_app.agent.middleware import tool_call_normalizer as tcn
from apps.chat_data_app.agent.run import redis_event_bus as reb
from apps.chat_data_app.services.semantic.core import filter_normalization as fn


# ============ filter_normalization ============

def test_split_text_between_filter_keeps_node_without_two_sides():
    node = {"operator": "BETWEEN", "value": "only-one"}
    assert fn.split_text_between_filter(node) is node


def test_time_equal_value_to_range_branches():
    assert fn.time_equal_value_to_range(2024) == ("2024-01-01 00:00:00", "2025-01-01 00:00:00")
    assert fn.time_equal_value_to_range(99) is None
    assert fn.time_equal_value_to_range(["2024"]) is None
    assert fn.time_equal_value_to_range("abc") is None
    assert fn.time_equal_value_to_range("2024-13-99") is None
    assert fn.time_equal_value_to_range("2024-13") is None


def test_normalize_time_filter_leaf_non_time_value_keeps_node():
    node = {"operator": "=", "value": "not-a-date"}
    dim = {"logical_type": "date"}
    assert fn.normalize_time_filter_leaf(node, dim) == [node]


# ============ tool_results ============

def test_parse_internal_tool_result_requires_dict_payload_and_meta():
    content = tr.build_internal_query_result({"status": "succeeded"}, query_mode="standard")
    assert tr.parse_internal_tool_result(content) is not None
    broken = '{"type": "chatdata_internal_tool_result", "kind": "query_result", "payload": "not-dict", "meta": {}}'
    assert tr.parse_internal_tool_result(broken) is None
    broken = '{"type": "chatdata_internal_tool_result", "kind": "query_result", "payload": {}, "meta": []}'
    assert tr.parse_internal_tool_result(broken) is None


def test_build_tool_result_recovery_omits_optional_fields():
    recovery = tr.build_tool_result_recovery(source_tool="t", result_path="p", result_id=" id ")
    assert "result_size_chars" not in recovery
    assert recovery["result_id"] == "id"
    assert "offset" not in recovery
    assert "limit" not in recovery


def test_tool_result_recovery_rejects_invalid_shapes():
    version = tr.TOOL_RESULT_RECOVERY_VERSION
    assert tr.tool_result_recovery({"recovery": "nope"}) is None
    assert tr.tool_result_recovery({"recovery": {"version": version + 1, "result_path": "p"}}) is None
    assert tr.tool_result_recovery({"recovery": {"version": version, "result_path": "   "}}) is None


def test_merge_tool_result_recovery_keeps_existing_and_wraps_foreign_artifacts():
    assert tr.merge_tool_result_recovery_artifact({"a": 1}, {"version": 1})["a"] == 1
    merged = tr.merge_tool_result_recovery_artifact("raw-artifact", {"version": 1})
    assert merged["source_artifact"] == "raw-artifact"
    assert tr.merge_tool_result_recovery_artifact(None, {"version": 1}) == {"chatdata_tool_result_recovery": {"version": 1}}


# ============ tool_call_normalizer ============

def test_is_exact_repeat_of_rejects_empty_tool_name():
    assert tcn._is_exact_repeat_of("abcabc", "") is False


def test_normalize_response_keeps_non_dict_tool_calls_and_empty_candidates():
    message = SimpleNamespace(tool_calls=[{"name": "known_tool", "args": {"a": 1}}, "not-a-dict"])
    result = tcn.normalize_response_tool_call_names(message, [])
    assert result is message
    assert result.tool_calls[1] == "not-a-dict"


class _FrozenMessage:
    tool_calls = [{"name": "write_todoswrite_todos", "args": {"todos": []}}]

    def __setattr__(self, _key, _value):
        raise RuntimeError("frozen message")


class _CopyableFrozenMessage(_FrozenMessage):
    def model_copy(self, *, update=None):
        return ("copied", update)


@pytest.mark.asyncio
async def test_normalizer_falls_back_to_model_copy_when_assignment_fails():
    middleware = tcn.create_tool_call_name_normalizer_middleware([SimpleNamespace(name="write_todos")])
    request = SimpleNamespace(tools=[SimpleNamespace(name="write_todos")])

    async def handler(_request):
        return SimpleNamespace(result=[_CopyableFrozenMessage()])

    outcome = await middleware.awrap_model_call(request, handler)
    copied, update = outcome.result[0]
    assert copied == "copied"
    assert update["tool_calls"][0]["name"] == "write_todos"


@pytest.mark.asyncio
async def test_normalizer_returns_original_message_without_model_copy(monkeypatch):
    warnings = []
    monkeypatch.setattr(tcn.logger, "warning", lambda msg, *args, **kwargs: warnings.append(msg))
    middleware = tcn.create_tool_call_name_normalizer_middleware([SimpleNamespace(name="write_todos")])
    request = SimpleNamespace(tools=[SimpleNamespace(name="write_todos")])

    async def handler(_request):
        return SimpleNamespace(result=[_FrozenMessage()])

    response = await middleware.awrap_model_call(request, handler)
    message = response.result[0]
    assert message.tool_calls[0]["name"] == "write_todoswrite_todos"
    assert warnings


@pytest.mark.asyncio
async def test_normalizer_middleware_passthrough_when_result_empty():
    middleware = tcn.create_tool_call_name_normalizer_middleware([])

    async def handler(_request):
        return SimpleNamespace(result=[])

    response = await middleware.awrap_model_call(SimpleNamespace(tools=[]), handler)
    assert response.result == []


# ============ redis_event_bus ============

def _bus_with(fake_redis) -> reb.RedisEventBus:
    bus = reb.RedisEventBus.__new__(reb.RedisEventBus)
    bus._redis = fake_redis
    return bus


@pytest.mark.asyncio
async def test_redis_event_bus_close_is_idempotent():
    class FakeRedis:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    fake = FakeRedis()
    bus = _bus_with(fake)
    await bus.close()
    assert fake.closed is True
    assert bus._redis is None
    await bus.close()


@pytest.mark.asyncio
async def test_redis_event_bus_publish_reraises_non_owner_response_error():
    class FakeRedis:
        async def eval(self, *_args, **_kwargs):
            raise ResponseError("NOGROUP stream key")

    with pytest.raises(ResponseError, match="NOGROUP"):
        await _bus_with(FakeRedis()).publish(
            run_id="run-1",
            conversation_id="conversation-1",
            user_id="user-1",
            event_id="event-1",
            event_type="message",
            payload="event: message\ndata: {}\n\n",
            owner_token="1:token",
        )


@pytest.mark.asyncio
async def test_redis_event_bus_claim_owner_mismatch_raises():
    class FakeRedis:
        async def eval(self, *_args, **_kwargs):
            return 0

    with pytest.raises(reb.RedisRunOwnerMismatchError):
        await _bus_with(FakeRedis()).claim_run_owner("run-1", "1:stale")


@pytest.mark.asyncio
async def test_redis_event_bus_latest_run_event_handles_empty_and_present_streams():
    class EmptyRedis:
        async def xrevrange(self, *_args, **_kwargs):
            return []

    assert await _bus_with(EmptyRedis()).latest_run_event("run-1") is None

    class RowRedis:
        async def xrevrange(self, *_args, **_kwargs):
            return [("1740000000000-0", {"event_type": "done", "sequence_no": "9"})]

    event = await _bus_with(RowRedis()).latest_run_event("run-1")
    assert event["stream_id"] == "1740000000000-0"
    assert event["event_type"] == "done"
    assert event["sequence_no"] == "9"
