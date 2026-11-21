"""本轮图推进次数（supersteps）计数与持久化单测。

口径：values 流每条 = 一次图推进；首条是输入回显，``__interrupt__`` 帧不算；
每次 invoke 从 0 重新计，与 recursion_limit 的额度口径一致。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from apps.chat_data_app import agent_run_support
from apps.chat_data_app import agent_stream as agent_stream_module
from apps.chat_data_app.agent.middleware import subagent_stream
from apps.chat_data_app.agent.middleware.subagents import _build_task_result_artifacts
from apps.chat_data_app.agent.run.conversation_event_service import _serialize_subagent_artifacts


def _adapter(config=None):
    return agent_stream_module._AgentSseStreamAdapter(
        agent=SimpleNamespace(),
        initial_state={},
        config=config if config is not None else {"recursion_limit": 1000},
    )


async def _collect(adapter, event):
    return [item async for item in adapter._process_stream_event(event, started_at=0.0)]


@pytest.mark.asyncio
async def test_run_steps_counts_values_excluding_echo_and_interrupt():
    adapter = _adapter()

    assert await _collect(adapter, ("values", {"messages": []})) == []
    assert adapter.supersteps == 0

    first = await _collect(adapter, ("values", {"messages": []}))
    assert len(first) == 1
    assert "event: run_steps" in first[0]
    assert '"supersteps": 1' in first[0]
    assert '"recursion_limit": 1000' in first[0]

    assert await _collect(adapter, ("values", {"__interrupt__": [{"value": "ok"}]})) == []
    assert adapter.supersteps == 1

    second = await _collect(adapter, ("values", {"messages": []}))
    assert '"supersteps": 2' in second[0]


@pytest.mark.asyncio
async def test_finish_persists_supersteps_without_token_usage():
    class _StateAgent:
        async def aget_state(self, config):
            return SimpleNamespace(values={"messages": []}, interrupts=(), next=())

    adapter = agent_stream_module._AgentSseStreamAdapter(
        agent=_StateAgent(),
        initial_state={},
        config={"recursion_limit": 1000},
    )
    adapter.supersteps = 3

    payload = await adapter.finish()

    assert payload["generation_stats"]["supersteps"] == 3
    assert payload["generation_stats"]["recursion_limit"] == 1000


def test_subagent_steps_event_and_snapshot_fields():
    state = subagent_stream._SubagentStreamState("call-1")
    state.superstep_limit = 1000
    events: list[dict] = []
    writer = events.append

    subagent_stream._process_subagent_chunk(("values", {"messages": []}), state, writer, "call-1")
    assert events == []

    subagent_stream._process_subagent_chunk(("values", {"messages": []}), state, writer, "call-1")
    assert state.supersteps == 1
    assert events[-1]["type"] == "subagent_tool"
    event = events[-1]["event"]
    assert event["event_type"] == "subagent_steps"
    assert event["parent_tool_call_id"] == "call-1"
    assert event["supersteps"] == 1
    assert event["recursion_limit"] == 1000

    subagent_stream._process_subagent_chunk(("values", {"__interrupt__": [{"value": "ok"}]}), state, writer, "call-1")
    assert state.supersteps == 1

    snapshot = subagent_stream._subagent_process_snapshot([], None, supersteps=4, recursion_limit=1000)
    assert snapshot["subagent_supersteps"] == 4
    assert snapshot["subagent_recursion_limit"] == 1000

    progress = subagent_stream._subagent_progress_snapshot(state)
    assert progress["supersteps"] == 1
    assert progress["recursion_limit"] == 1000


@pytest.mark.asyncio
async def test_subagent_stream_wires_values_counting_into_captured():
    class _FakeRunnable:
        async def astream(self, state, config, stream_mode):
            assert stream_mode == ["values", "updates", "messages", "custom"]
            assert config["recursion_limit"] == 1000
            yield ("values", {"messages": []})
            yield ("values", {"messages": []})
            yield ("values", {"messages": []})

    events: list[dict] = []
    captured: dict = {}
    await subagent_stream._run_subagent_with_events(
        _FakeRunnable(),
        {"messages": []},
        {"recursion_limit": 1000},
        events.append,
        parent_tool_call_id="call-1",
        captured=captured,
    )

    assert [item["event"]["supersteps"] for item in events] == [1, 2]
    assert captured["supersteps"] == 2
    assert captured["recursion_limit"] == 1000


def test_subagent_artifacts_survive_tool_message_roundtrip():
    captured = {"supersteps": 4, "recursion_limit": 1000}
    artifacts = _build_task_result_artifacts([], None, captured)
    message = ToolMessage(content="report", tool_call_id="call-1", additional_kwargs=artifacts)

    serialized = _serialize_subagent_artifacts(message)

    assert serialized["subagent_supersteps"] == 4
    assert serialized["subagent_recursion_limit"] == 1000


@pytest.mark.asyncio
async def test_partial_history_keeps_failure_supersteps(monkeypatch):
    class _StateAgent:
        async def aget_state(self, config):
            assert config == {"recursion_limit": 1000}
            return SimpleNamespace(values={"messages": [AIMessage(content="部分回答", id="assistant-1")]})

    adapter = SimpleNamespace(
        agent=_StateAgent(),
        config={"recursion_limit": 1000},
        state=None,
        supersteps=2,
        reasoning_segments=[],
    )
    persisted = []

    async def persist_turn(**kwargs):
        persisted.append(kwargs)

    monkeypatch.setattr(agent_stream_module.conversation_event_service, "persist_turn", persist_turn)

    await agent_run_support._persist_partial_agent_turn_history(
        adapter,
        conversation_id="conv-failed",
        user_id="user-1",
        username="用户一",
        turn_id="turn-1",
    )

    generation_stats = persisted[0]["messages"][0].additional_kwargs["generation_stats"]
    assert generation_stats == {"supersteps": 2, "recursion_limit": 1000}
