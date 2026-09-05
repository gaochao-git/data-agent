"""context_usage 中间件辅助函数的分支覆盖补测。"""
import os
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from apps.chat_data_app.agent.middleware import context_usage as cu
from apps.chat_data_app.agent.middleware.context_usage import (
    ModelResponse,
    _add_cache_hit_rate,
    _add_cached_input_usage,
    _cached_tokens_from_details,
    _cached_tokens_from_openai_usage,
    _emit_context_usage,
    _ensure_model_response,
    _extract_cached_input_tokens,
    _response_metadata_usage,
    _stamp_response_timing,
    _usage_from_response_metadata,
    compact_token_usage_for_state,
)


def test_ensure_model_response_wraps_plain_objects():
    """非 ModelResponse/AIMessage 的对象按 result 字段包装（148->150 / 151->152 / 151->153）。"""
    message = AIMessage("ok")
    wrapped = _ensure_model_response(SimpleNamespace(result=[message]))
    assert isinstance(wrapped, ModelResponse)
    assert wrapped.result == [message]

    empty = _ensure_model_response(SimpleNamespace())
    assert isinstance(empty, ModelResponse)
    assert empty.result == []

    non_list = _ensure_model_response(SimpleNamespace(result="oops"))
    assert isinstance(non_list, ModelResponse)
    assert non_list.result == []


def test_compact_token_usage_keeps_known_fields_and_compression():
    usage = {
        "used": 100,
        "total": 1000,
        "extra_dropped": "x",
        "context_compression": {"triggered": True, "unknown_field": 1},
    }
    compact = compact_token_usage_for_state(usage)
    assert compact["used"] == 100 and compact["total"] == 1000
    assert "extra_dropped" not in compact
    # compression 只保留白名单字段（221->222）
    assert compact["context_compression"] == {"triggered": True}


def test_compact_token_usage_empty_compression_and_empty_usage():
    # compression 是空 dict → 不写入（227->230）
    compact = compact_token_usage_for_state({"used": 1, "context_compression": {}})
    assert "context_compression" not in compact
    # usage 全是未知字段 → compact 为空 → None
    assert compact_token_usage_for_state({"unknown": 1}) is None


def test_emit_context_usage_none_is_noop(monkeypatch):
    """usage 为空时不触发 stream writer（575->576）。"""
    calls = []
    monkeypatch.setattr(cu, "get_stream_writer", lambda: calls.append)
    _emit_context_usage(None)
    assert calls == []

    _emit_context_usage({"used": 1})
    assert len(calls) == 1


def test_stamp_response_timing_without_message_returns_early():
    """响应里提不出消息时直接返回（618->619）。"""
    _stamp_response_timing(SimpleNamespace(), started_at_ms=1, ended_at_ms=2)  # 不抛错


def test_add_cached_input_usage_without_current_cached_tokens():
    """当前调用缺 cached_input_tokens 时跳过 current 字段，仍汇总累计值（657->665）。"""
    payload = {}
    _add_cached_input_usage(
        payload,
        [{"cached_input_tokens": 5, "actual_input_tokens": 10}],
        {},
    )
    assert "current_cached_input_tokens" not in payload
    assert payload["cumulative_cached_input_tokens"] == 5
    assert payload["cumulative_cache_hit_rate"] == 50.0


def test_add_cache_hit_rate_writes_percentage():
    payload = {}
    _add_cache_hit_rate(payload, "current_cache_hit_rate", 3, 10)
    assert payload["current_cache_hit_rate"] == 30.0


def test_extract_cached_input_tokens_requires_dict_metadata():
    """response_metadata 非 dict → None（718->719）。"""
    message = AIMessage.model_construct(content="ok", response_metadata="not-a-dict")
    assert _extract_cached_input_tokens(message) is None


def test_usage_from_response_metadata_none_payload():
    """response_metadata 非 dict → 提不出三元组（754->755）。"""
    message = AIMessage.model_construct(content="ok", response_metadata="bad")
    assert _usage_from_response_metadata(message) is None


def test_response_metadata_usage_rejects_non_dict():
    message = AIMessage.model_construct(content="ok", response_metadata="bad")
    assert _response_metadata_usage(message) is None


def test_cached_tokens_from_details_all_invalid_returns_none():
    """details 是 dict 但没有任何有效缓存数（787->791）。"""
    assert _cached_tokens_from_details({"cache_read": "x", "cached_tokens": -1}) is None
    assert _cached_tokens_from_details("not-dict") is None


def test_cached_tokens_from_openai_usage_falls_back_to_prompt_details():
    """input_token_details 无命中时回退 prompt_tokens_details（798->799）。"""
    usage = {"prompt_tokens_details": {"cached_tokens": 7}}
    assert _cached_tokens_from_openai_usage(usage) == 7
    assert _cached_tokens_from_openai_usage("bad") is None
