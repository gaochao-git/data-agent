"""context_compression 残余分支补测：更新构造、todo 快照、分界事件与摘要预算裁剪。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from apps.chat_data_app.agent.context.context_budget import ContextBudget
from apps.chat_data_app.agent.middleware import context_compression as cc
from apps.chat_data_app.agent.middleware.context_compression import (
    ContextInputLimitExceededError,
    build_context_compression_update,
    create_context_compression_middleware,
    format_todo_snapshot_lines,
    neutralize_stale_todo_tool_messages,
)


@pytest.mark.asyncio
async def test_build_update_skips_state_write_when_summary_retry_scheduled(monkeypatch):
    report = {"triggered": True, "summary_retry_next_turn": True}

    async def fake_compress(*_args, **_kwargs):
        return [], None, report

    monkeypatch.setattr(cc, "compress_messages_for_state", fake_compress)

    updates, summary, result_report = await build_context_compression_update(
        [],
        static_input_tokens=0,
        compression_trigger_tokens=1,
        hard_input_limit_tokens=1000,
        compression_target_tokens=100,
    )

    assert updates is None
    assert summary is None
    assert result_report is report


def test_format_todo_snapshot_lines_skips_non_dict_and_empty_content():
    lines, unfinished = format_todo_snapshot_lines(
        [None, "junk", {"content": "   ", "status": "pending"}, {"content": "补数据", "status": "pending"}]
    )

    assert lines == ["1. [待办] 补数据"]
    assert unfinished == 1


def test_emit_compression_started_event_skips_when_builder_returns_none(monkeypatch):
    monkeypatch.setattr(cc, "_build_context_compression_started_event", lambda messages: None)
    emitted = []
    monkeypatch.setattr(cc, "_emit_context_compression_event", lambda *a, **k: emitted.append(a))

    cc._emit_compression_started_event([])

    assert emitted == []


def test_started_event_without_messages_has_empty_turn_id():
    event = cc._build_context_compression_started_event([])

    assert event is not None
    assert event["phase"] == "started"
    assert event["turn_id"] == ""


def test_started_event_skips_non_human_messages():
    event = cc._build_context_compression_started_event([AIMessage(content="回答")])

    assert event is not None
    assert event["turn_id"] == ""


def test_started_event_continues_past_human_without_id():
    event = cc._build_context_compression_started_event(
        [HumanMessage(content="问题", id="turn-1"), HumanMessage(content="无 id 追问")]
    )

    assert event is not None
    assert event["turn_id"] == "turn-1"


@pytest.mark.asyncio
async def test_middleware_raises_when_estimated_after_exceeds_hard_limit(monkeypatch):
    budget = ContextBudget(
        context_window=1000,
        max_output_tokens=10,
        compression_trigger_ratio=0.7,
        hard_input_limit_tokens=10,
        compression_trigger_tokens=5,
        compression_target_tokens=1,
    )
    monkeypatch.setattr(
        cc.ContextRequestBudget,
        "configure",
        lambda self, system_prompt, tools, model_id=None: budget,
    )
    monkeypatch.setattr(cc, "get_context_compression_config", lambda: {"enabled": True})
    monkeypatch.setattr(cc, "_get_summary_input_limit_tokens", lambda _model_id: 1000)

    async def fake_update(*_args, **_kwargs):
        return None, None, {"estimated_tokens_after": 11, "triggered": False}

    monkeypatch.setattr(cc, "build_context_compression_update", fake_update)
    middleware = create_context_compression_middleware(system_prompt="系统提示词", state_schema=dict)

    with pytest.raises(ContextInputLimitExceededError):
        await middleware.abefore_model({"messages": []}, SimpleNamespace())


def test_prepare_summary_input_breaks_when_nothing_removable(monkeypatch):
    monkeypatch.setattr(cc, "_drop_oldest_summary_round", lambda messages: ([], 0))
    messages = [HumanMessage(content="历史问题" * 500)]
    marker = cc._build_summary_omission_marker(1, 1)
    limit = cc._count_summary_input_tokens([marker], previous_summary=None)
    before = cc._count_summary_input_tokens(messages, previous_summary=None)
    assert before > limit

    prepared, report = cc._prepare_summary_input(
        messages,
        previous_summary=None,
        summary_input_limit_tokens=limit,
    )

    assert len(prepared) == 1
    assert prepared[0].additional_kwargs.get("context_compression_summary_omission") is True
    assert report["summary_input_pruned_messages"] == 0
    assert report["summary_input_unusable"] is False


def test_neutralize_stale_todo_messages_covers_repeated_hits():
    first = ToolMessage(content="旧快照1", name="write_todos", tool_call_id="c1")
    second = ToolMessage(content="旧快照2", name="write_todos", tool_call_id="c2")
    messages = [first, second]

    result = neutralize_stale_todo_tool_messages(messages)

    assert result is not messages
    assert all("已由实时 state 快照" in message.content for message in result)
    assert first.content == "旧快照1"
    assert second.content == "旧快照2"
