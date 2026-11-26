"""脚本化流式模型（scripted_llm）自测与真图链路验证。

覆盖：
- 工具参数按 JSON 片段流式产出，合并后得到完整 tool_calls
- 真 ``create_agent`` 图执行工具时拿到完整参数（不是 mock 掉图）
- 按请求内容路由到不同脚本（正常回答 / 压缩摘要）
- 异常注入按轮次抛出
- ``_AgentSseStreamAdapter`` 从真实模型分片产出 ``tool_args_delta``，增量可拼回完整参数
"""

import json

import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from apps.chat_data_app import agent_stream
from apps.chat_data_app.tests.unit.scripted_llm import (
    ScriptedChatModel,
    chunks_turn,
    error_turn,
    text_turn,
    tool_call_turn,
)


def _parse_sse(frame: str) -> tuple[str, dict]:
    event = frame.split("\n", 1)[0].removeprefix("event: ")
    payload = json.loads(frame.split("\ndata: ", 1)[1].rstrip("\n"))
    return event, payload


@pytest.mark.asyncio
async def test_streams_tool_call_args_in_partial_json_pieces():
    model = ScriptedChatModel(turns=[tool_call_turn("file_read", {"path": "/tmp/a.md", "limit": 50}, arg_pieces=3)])

    chunks = [chunk async for chunk in model.astream([HumanMessage(content="读文件")])]

    arg_chunks = [chunk for chunk in chunks if chunk.tool_call_chunks]
    assert len(arg_chunks) == 3
    merged = arg_chunks[0]
    for chunk in arg_chunks[1:]:
        merged = merged + chunk
    assert merged.tool_calls == [
        {"name": "file_read", "args": {"path": "/tmp/a.md", "limit": 50}, "id": "call-1", "type": "tool_call"}
    ]


def test_sync_stream_also_yields_partial_args():
    model = ScriptedChatModel(turns=[tool_call_turn("file_read", {"path": "/tmp/s.md"}, arg_pieces=3)])

    arg_chunks = [chunk for chunk in model.stream([HumanMessage(content="读文件")]) if chunk.tool_call_chunks]

    assert len(arg_chunks) == 3
    merged = arg_chunks[0]
    for chunk in arg_chunks[1:]:
        merged = merged + chunk
    assert merged.tool_calls == [
        {"name": "file_read", "args": {"path": "/tmp/s.md"}, "id": "call-1", "type": "tool_call"}
    ]


@pytest.mark.asyncio
async def test_real_graph_runs_tool_with_streamed_args():
    seen: list[str] = []

    @tool
    def record_path(path: str) -> str:
        """记录收到的路径。"""
        seen.append(path)
        return "ok"

    model = ScriptedChatModel(
        turns=[tool_call_turn("record_path", {"path": "/tmp/real.md"}, arg_pieces=4), text_turn("完成")]
    )
    graph = create_agent(model=model, tools=[record_path])

    async for _event in graph.astream({"messages": [HumanMessage(content="读文件")]}, stream_mode="messages"):
        pass

    assert model.bound_tool_names == ["record_path"]
    assert seen == ["/tmp/real.md"]


def test_router_selects_turn_by_request_content():
    model = ScriptedChatModel(
        turns=[text_turn("正常回答"), text_turn("压缩摘要")],
        router=lambda messages: 1 if "压缩" in str(messages[-1].content) else 0,
    )

    assert model.invoke([HumanMessage(content="你好")]).content == "正常回答"
    assert model.invoke([HumanMessage(content="请压缩这段对话")]).content == "压缩摘要"
    assert len(model.calls) == 2


def test_error_turn_raises_on_call():
    model = ScriptedChatModel(turns=[error_turn(TimeoutError("upstream timeout"))])

    with pytest.raises(TimeoutError, match="upstream timeout"):
        model.invoke([HumanMessage(content="hi")])


@pytest.mark.asyncio
async def test_error_turn_raises_while_streaming():
    model = ScriptedChatModel(turns=[error_turn(ConnectionError("stream broke"))])

    with pytest.raises(ConnectionError, match="stream broke"):
        async for _chunk in model.astream([HumanMessage(content="hi")]):
            pass


@pytest.mark.asyncio
async def test_stream_interrupts_after_partial_tool_args():
    args = {"path": "/tmp/partial.md", "limit": 50}
    turn = tool_call_turn("file_read", args, arg_pieces=3).interrupt_after(2, ConnectionError("upstream closed"))
    model = ScriptedChatModel(turns=[turn])

    received = []
    with pytest.raises(ConnectionError, match="upstream closed"):
        async for chunk in model.astream([HumanMessage(content="读文件")]):
            received.append(chunk)

    arg_chunks = [chunk for chunk in received if chunk.tool_call_chunks]
    assert len(arg_chunks) == 2
    partial = "".join(chunk.tool_call_chunks[0]["args"] for chunk in arg_chunks)
    assert json.dumps(args, ensure_ascii=False, separators=(",", ":")).startswith(partial)
    with pytest.raises(json.JSONDecodeError):
        json.loads(partial)


@pytest.mark.asyncio
async def test_adapter_keeps_partial_args_and_propagates_stream_interrupt():
    @tool
    def record_path(path: str) -> str:
        """记录收到的路径。"""
        return "ok"

    args = {"path": "/tmp/broken.md"}
    turn = tool_call_turn("record_path", args, arg_pieces=3).interrupt_after(2, ConnectionError("upstream closed"))
    model = ScriptedChatModel(turns=[turn])
    graph = create_agent(model=model, tools=[record_path])
    adapter = agent_stream._AgentSseStreamAdapter(
        agent=graph,
        initial_state={"messages": [HumanMessage(content="读文件")]},
        config={"recursion_limit": 1000},
        visible_message_filter=agent_stream._is_chat_data_visible_stream_message,
    )

    frames = []
    with pytest.raises(ConnectionError, match="upstream closed"):
        async for frame in adapter.stream_events():
            frames.append(frame)

    parsed = [_parse_sse(frame) for frame in frames if isinstance(frame, str) and frame.startswith("event: ")]
    deltas = [payload for event, payload in parsed if event == "tool_args_delta"]
    assert deltas
    partial = "".join(payload["delta"] for payload in deltas)
    assert json.dumps(args, ensure_ascii=False, separators=(",", ":")).startswith(partial)
    # 异常收口要补发窗口内尾片：收到的是「已到达适配器的全部分片」，不是少一片的残段。
    assert partial == "".join(chunk["tool_call_chunks"][0]["args"] for chunk in turn.chunks[:2])
    assert all(payload["tool_call_id"] == "call-1" for payload in deltas)
    assert not [payload for event, payload in parsed if event == "tool_progress"]


def test_custom_chunk_without_content_defaults_to_empty_string():
    model = ScriptedChatModel(
        turns=[chunks_turn([{"tool_call_chunks": [{"name": "ping", "args": "{}", "id": "call-2", "index": 0}]}])]
    )

    assert model.invoke([HumanMessage(content="hi")]).tool_calls == [
        {"name": "ping", "args": {}, "id": "call-2", "type": "tool_call"}
    ]


@pytest.mark.asyncio
async def test_usage_metadata_reaches_last_chunk():
    usage = {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    model = ScriptedChatModel(turns=[text_turn("好的", pieces=3, usage=usage)])

    chunks = [chunk async for chunk in model.astream([HumanMessage(content="hi")])]
    assert [chunk.usage_metadata for chunk in chunks if chunk.usage_metadata] == [usage]


@pytest.mark.asyncio
async def test_adapter_emits_tool_args_delta_from_scripted_stream():
    @tool
    def record_path(path: str) -> str:
        """记录收到的路径。"""
        return "ok"

    model = ScriptedChatModel(
        turns=[tool_call_turn("record_path", {"path": "/tmp/sse.md"}, arg_pieces=3), text_turn("完成")]
    )
    graph = create_agent(model=model, tools=[record_path])
    adapter = agent_stream._AgentSseStreamAdapter(
        agent=graph,
        initial_state={"messages": [HumanMessage(content="读文件")]},
        config={"recursion_limit": 1000},
        visible_message_filter=agent_stream._is_chat_data_visible_stream_message,
    )

    frames = [frame async for frame in adapter.stream_events()]
    parsed = [_parse_sse(frame) for frame in frames if isinstance(frame, str) and frame.startswith("event: ")]
    deltas = [item for item in parsed if item[0] == "tool_args_delta"]

    assert deltas
    assert all(payload["tool_name"] == "record_path" for _event, payload in deltas)
    assert all(payload["tool_call_id"] == "call-1" for _event, payload in deltas)
    streamed_args = "".join(payload["delta"] for _event, payload in deltas)
    assert json.loads(streamed_args) == {"path": "/tmp/sse.md"}

    progress = [payload for event, payload in parsed if event == "tool_progress"]
    assert len(progress) == 1
    assert progress[0]["tool_call_id"] == "call-1"
    assert progress[0]["replace_args"] is True
    assert progress[0]["args"] == {"path": "/tmp/sse.md"}
