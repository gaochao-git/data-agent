"""agent_state 压缩事件合并测试——幂等去重与脏数据防御。"""

from __future__ import annotations

from apps.chat_data_app.agent.agent_state import merge_context_compression_events


def test_merges_events_in_order():
    existing = [{"id": "e1", "round": 1}]
    new = [{"id": "e2", "round": 2}]
    assert merge_context_compression_events(existing, new) == [
        {"id": "e1", "round": 1},
        {"id": "e2", "round": 2},
    ]


def test_dedups_by_event_id_to_support_stream_retry():
    existing = [{"id": "e1", "round": 1}]
    new = [{"id": "e1", "round": 1}, {"id": "e2", "round": 2}]
    assert merge_context_compression_events(existing, new) == [
        {"id": "e1", "round": 1},
        {"id": "e2", "round": 2},
    ]


def test_skips_non_dict_events():
    assert merge_context_compression_events(["bad", 3, None], [{"id": "e1"}]) == [{"id": "e1"}]


def test_skips_events_without_usable_id():
    events = [{"id": "  "}, {"no_id": True}, {"id": "e1"}]
    assert merge_context_compression_events(events, None) == [{"id": "e1"}]


def test_handles_none_inputs():
    assert merge_context_compression_events(None, None) == []
    assert merge_context_compression_events(None, [{"id": "e1"}]) == [{"id": "e1"}]
