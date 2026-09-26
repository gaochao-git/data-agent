"""SQL 助手流式思考透传的单元测试。

SQL 助手（nl2sql）开启深度思考时，思考增量必须以独立 thinking 事件产出、
正文走 chunk 事件、思考段随轮次持久化——不允许静默丢弃。
"""

from __future__ import annotations

import contextlib

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

from apps.chat_data_app.routers import query_execution


class _FakeAgent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def astream(self, _input, config=None, stream_mode=None):
        for chunk, metadata in self._chunks:
            yield chunk, metadata


@contextlib.asynccontextmanager
async def _fake_checkpointer():
    yield None


class _FakeAgentModule:
    get_checkpointer = staticmethod(_fake_checkpointer)


def _run_stream(chunks):
    async def _collect():
        fake_agent = _FakeAgent(chunks)
        return [
            (kind, text)
            async for kind, text in query_execution._stream_sql_assist_chunks(
                conversation_id="c1",
                user_message=HumanMessage(content="查订单数", id="t1"),
                source_type="mysql",
                tables_ddl_info="",
            )
        ]

    return _collect()


@pytest.mark.asyncio
async def test_stream_sql_assist_chunks_emits_thinking_before_chunk(monkeypatch):
    """模型节点的思考增量以 thinking 产出，正文以 chunk 产出，非模型节点被跳过。"""
    chunks = [
        (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "先看表结构"}), {"langgraph_node": "model"}),
        (AIMessageChunk(content="SELECT 1"), {"langgraph_node": "model"}),
        (AIMessageChunk(content="noise"), {"langgraph_node": "tools"}),
    ]
    monkeypatch.setattr(query_execution, "create_sql_assist_agent", lambda *_, **__: _FakeAgent(chunks))
    monkeypatch.setattr(query_execution, "agent_module", _FakeAgentModule)

    events = await _run_stream(chunks)

    assert events == [
        ("thinking", "先看表结构"),
        ("chunk", "SELECT 1"),
    ]


@pytest.mark.asyncio
async def test_stream_sql_assist_chunks_reasoning_key_fallback(monkeypatch):
    """reasoning / reasoning_text 兜底键同样被识别（与主链路三键口径一致）。"""
    chunks = [
        (AIMessageChunk(content="", additional_kwargs={"reasoning": "换个思路"}), {"langgraph_node": "model"}),
    ]
    monkeypatch.setattr(query_execution, "create_sql_assist_agent", lambda *_, **__: _FakeAgent(chunks))
    monkeypatch.setattr(query_execution, "agent_module", _FakeAgentModule)

    events = await _run_stream(chunks)

    assert events == [("thinking", "换个思路")]


@pytest.mark.asyncio
async def test_persist_quick_sql_assistant_turn_forwards_reasoning_segments(monkeypatch):
    """思考段随轮次透传到 persist_turn，正文为空但存在思考段时仍落历史。"""
    captured: dict = {}

    async def _fake_persist_turn(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        query_execution.conversation_event_service, "persist_turn", _fake_persist_turn
    )
    segments = [{"id": "reasoning-t1-0", "content": "先看表结构", "tool_call_count": 0}]
    await query_execution._persist_quick_sql_assistant_turn(
        conversation_id="c1",
        user_id="u1",
        username="op",
        user_message=HumanMessage(content="查订单数", id="t1"),
        content="",
        reasoning_segments=segments,
    )

    assert captured["reasoning_segments"] == segments
    assert captured["messages"][1].content == ""
