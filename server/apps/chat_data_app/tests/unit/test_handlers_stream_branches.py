import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from apps.chat_data_app import handlers


async def collect_stream(generator):
    return [chunk async for chunk in generator]


class FakeChunk:
    def __init__(
        self,
        content="",
        *,
        msg_type="ai",
        tool_calls=None,
        tool_call_chunks=None,
        tool_call_id="",
        name="",
        additional_kwargs=None,
        response_metadata=None,
    ):
        self.content = content
        self.type = msg_type
        self.tool_calls = tool_calls or []
        self.tool_call_chunks = tool_call_chunks or []
        self.tool_call_id = tool_call_id
        self.name = name
        self.additional_kwargs = additional_kwargs or {}
        self.response_metadata = response_metadata or {}

    def model_dump(self):
        data = {
            "content": self.content,
            "type": self.type,
        }
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        if self.additional_kwargs:
            data["additional_kwargs"] = self.additional_kwargs
        if self.response_metadata:
            data["response_metadata"] = self.response_metadata
        return data


class FakeCheckpointer:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def test_handler_tool_state_helpers_cover_empty_and_fallback_branches():
    assert handlers._resolve_active_tool_call_id(None, "active", []) == "active"
    assert handlers._resolve_active_tool_call_id(None, None, ["older", "latest"]) == "latest"
    assert handlers._resolve_active_tool_call_id(None, None, []) is None

    running = ["a", "b"]
    assert handlers._finish_running_tool(running, "b", "b") == "a"
    assert running == ["a"]
    assert handlers._finish_running_tool(running, "a", "missing") == "a"

    assert handlers._parse_tool_args_buffer("", {"x": 1}) == {"x": 1}
    assert handlers._parse_tool_args_buffer("[bad", {"x": 1}) == {"x": 1}
    assert handlers._parse_tool_args_buffer("[1]", {"x": 1}) == {"x": 1}
    assert handlers._parse_tool_args_buffer('{"x": 2}', {"x": 1}) == {"x": 2}

    assert handlers._is_tool_result_error("") is False
    assert handlers._is_tool_result_error("[dashboard_write] bad json") is False
    assert handlers._is_tool_result_error('[dashboard_write] {"success": false}') is True
    assert handlers._is_tool_result_error("error: bad") is True
    assert handlers._is_tool_result_error("执行失败") is True

    assert handlers._visible_tool_count({}) == 0
    assert handlers._visible_tool_count({
        "hidden": "load_skill_tool",
        "sql": "semantic_query_tool",
        "dsl": "dsl2sql_tool",
    }) == 2

    reasoning_segments = []
    handlers._append_reasoning_segment(reasoning_segments, "工具前", 0)
    handlers._append_reasoning_segment(reasoning_segments, "继续", 0)
    handlers._append_reasoning_segment(reasoning_segments, "工具后", 2)
    assert reasoning_segments == [
        {"id": "reasoning-0", "content": "工具前继续", "tool_call_count": 0},
        {"id": "reasoning-1", "content": "工具后", "tool_call_count": 2},
    ]


@pytest.mark.asyncio
async def test_persist_reasoning_segments_only_writes_delta():
    class Graph:
        def __init__(self):
            self.updates = []

        async def aupdate_state(self, config, values, as_node=None):
            self.updates.append((config, values, as_node))

    graph = Graph()
    segments = [
        {"id": "reasoning-0", "content": "工具前", "tool_call_count": 0},
        {"id": "reasoning-1", "content": "工具后", "tool_call_count": 1},
    ]

    persisted_count = await handlers._persist_reasoning_segments(graph, {"thread_id": "t"}, segments, "agent")
    persisted_count = await handlers._persist_reasoning_segments(graph, {"thread_id": "t"}, segments, "agent", persisted_count)
    segments.append({"id": "reasoning-2", "content": "继续", "tool_call_count": 1})
    persisted_count = await handlers._persist_reasoning_segments(graph, {"thread_id": "t"}, segments, "agent", persisted_count)

    assert persisted_count == 3
    assert [update[1]["reasoning_segments"] for update in graph.updates] == [
        segments[:2],
        [segments[2]],
    ]


def test_message_end_payload_prefers_persisted_ai_timestamp():
    state = SimpleNamespace(values={"messages": [
        AIMessage(content="中间回复", id="ai_1", additional_kwargs={"timestamp": "2026-06-21 07:14:40"}),
        ToolMessage(content="工具", tool_call_id="tc", id="tool_1"),
        AIMessage(content="最终回复", id="ai_2", additional_kwargs={"timestamp": "2026-06-21 07:14:44"}),
    ]})

    payload = handlers._message_end_payload_from_state(state, {"total_tokens": 7})

    assert payload == {
        "timestamp": "2026-06-21 07:14:44",
        "usage": {"total_tokens": 7},
    }


@pytest.mark.asyncio
async def test_stream_sql_generation_tool_chunks_selection_and_interrupt(monkeypatch):
    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={"token_usage": {"total_tokens": 7}},
                tasks=[
                    SimpleNamespace(
                        interrupts=[
                            SimpleNamespace(
                                value={
                                    "args": {"sql": "select 1"},
                                    "message": "确认执行",
                                }
                            )
                        ]
                    )
                ],
            )

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(content="skip", msg_type="ai"), {"langgraph_node": "tools"}
            yield FakeChunk(), {"langgraph_node": "agent"}
            yield FakeChunk(tool_calls=[{"id": "tc1", "name": "select_semantic_config_tool"}]), {"langgraph_node": "agent"}
            yield FakeChunk(tool_call_chunks=[{"id": "tc1", "name": "select_semantic_config_tool", "args": '{"semantic_config_id":'}]), {"langgraph_node": "agent"}
            yield FakeChunk(tool_call_chunks=[{"id": None, "args": '"cfg"}'}]), {"langgraph_node": "agent"}
            yield FakeChunk(
                content='[select_semantic_config_tool] {"selected": true, "selected_config_id": "cfg", "selected_config_name": "指标集"}',
                msg_type="tool",
                tool_call_id="tc1",
            ), {"langgraph_node": "tools"}
            yield FakeChunk(content="answer", msg_type="ai"), {"langgraph_node": "agent"}

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: Graph())
    async def empty_execution_target_info(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(handlers, "_build_execution_target_info_async", empty_execution_target_info)

    chunks = await collect_stream(handlers._stream_sql_generation(
        thread_id="conv",
        user_query="查销售额",
        datasource_id="ds",
        user_id="u1",
        mode="query",
        semantic_config_selection={
            "semantic_config_id": "cfg",
            "matched_metrics": ["销售额", ""],
            "matched_dimensions": ["城市"],
            "auto_selected": True,
        },
    ))
    text = "".join(chunks)
    assert "semantic_config_selected" in text
    assert "accumulated_args" in text
    assert "event: message" in text
    assert "event: tool" in text
    assert "status" in text and "pending" in text
    assert "message_end" in text
    assert text.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_stream_sql_generation_emits_thinking_event(monkeypatch):
    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(
                msg_type="ai",
                additional_kwargs={"reasoning_content": "正在判断问题类型"},
            ), {"langgraph_node": "agent"}
            yield FakeChunk(content="你好", msg_type="ai"), {"langgraph_node": "agent"}

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: Graph())

    async def empty_execution_target_info(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(handlers, "_build_execution_target_info_async", empty_execution_target_info)

    chunks = await collect_stream(handlers._stream_sql_generation(
        thread_id="conv",
        user_query="你好",
        datasource_id="ds",
        user_id="u1",
        mode="query",
    ))

    text = "".join(chunks)
    assert "event: thinking" in text
    assert "正在判断问题类型" in text
    assert "event: message" in text


@pytest.mark.asyncio
async def test_stream_sql_generation_missing_semantic_config_and_graph_error(monkeypatch):
    async def no_semantic(*_args, **_kwargs):
        return None

    monkeypatch.setattr(handlers, "_get_semantic_config_async", no_semantic)
    chunks = await collect_stream(handlers._stream_sql_generation(
        thread_id="conv",
        user_query="查销售额",
        datasource_id="ds",
        semantic_config_id="missing",
        mode="query",
    ))
    assert "query 模式未找到可用语义配置" in "".join(chunks)

    class BadCheckpointer:
        async def __aenter__(self):
            raise RuntimeError("checkpoint down")

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: BadCheckpointer())
    chunks = await collect_stream(handlers._stream_sql_generation("conv", "hello"))
    assert "checkpoint down" in "".join(chunks)


@pytest.mark.asyncio
async def test_continue_execution_filters_disconnect_and_tool_args(monkeypatch):
    class Graph:
        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(content="skip", msg_type="ai"), {"langgraph_node": "tool_llm"}
            yield FakeChunk(), {"langgraph_node": "agent"}
            yield FakeChunk(tool_calls=[{"id": "tc1", "name": "semantic_query_tool"}]), {"langgraph_node": "agent"}
            yield FakeChunk(tool_call_chunks=[{"id": "tc1", "name": "semantic_query_tool", "args": "{bad"}]), {"langgraph_node": "agent"}
            yield FakeChunk(content="执行失败", msg_type="tool", tool_call_id="tc1", name="semantic_query_tool"), {"langgraph_node": "tools"}
            yield FakeChunk(content="done", msg_type="ai"), {"langgraph_node": "agent"}

        async def aget_state(self, _config):
            return SimpleNamespace(values={"token_usage": {"total_tokens": 3}}, tasks=[])

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: Graph())

    chunks = await collect_stream(handlers._continue_execution("conv", modified_sql="select 1", action="edit"))
    text = "".join(chunks)
    assert "semantic_query_tool" in text
    assert "accumulated_args" not in text
    assert "message_end" in text

    class DisconnectRequest:
        async def is_disconnected(self):
            return True

    chunks = await collect_stream(handlers._continue_execution("conv", request=DisconnectRequest()))
    assert chunks == []


@pytest.mark.asyncio
async def test_semantic_config_fallback_and_execution_target_name_branches(monkeypatch):
    class LegacyManager:
        async def get_datasource_async(self, datasource_id):
            return {"source_type": "", "id": datasource_id}

        async def get_semantic_config_async(self, datasource_id, semantic_config_id):
            return {"name": "未带 ID 指标集", "enabled": True}

    monkeypatch.setattr(handlers, "get_datasource_manager", lambda: LegacyManager())
    config = await handlers._get_semantic_config_async("ds_a", "cfg_a")

    assert config["id"] == "cfg_a"
    assert "source_type" not in config or config["source_type"] == ""

    class NamelessManager:
        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            return [
                {"member_datasource_id": ""},
                {"member_datasource_id": "m2"},
                {"member_name": "具名执行对象"},
            ]

    monkeypatch.setattr(handlers, "get_datasource_manager", lambda: NamelessManager())
    info = await handlers._build_execution_target_info_async("ds_a")

    assert "1. 执行对象1" in info
    assert "2. m2" in info
    assert "3. 具名执行对象" in info


@pytest.mark.asyncio
async def test_stream_sql_generation_non_query_reuses_loaded_tables_and_handles_disconnect(monkeypatch):
    class Graph:
        def __init__(self):
            self.initial_state = None

        async def aget_state(self, _config):
            return SimpleNamespace(values={"loaded_table_names": ["orders"], "token_usage": {"total_tokens": 5}}, tasks=[])

        async def astream(self, initial_state, *_args, **_kwargs):
            self.initial_state = initial_state
            yield FakeChunk(content="answer", msg_type="ai"), {"langgraph_node": "agent"}

    class DisconnectAfterFirst:
        def __init__(self):
            self.calls = 0

        async def is_disconnected(self):
            self.calls += 1
            return self.calls == 1

    graph = Graph()

    async def get_all_tables(_datasource_id):
        return [{"table_name": "orders"}, {"table_name": "users"}]

    async def select_tables(_query, _all_tables):
        return ["orders"]

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: graph)
    monkeypatch.setattr(handlers, "get_all_tables_async", get_all_tables)
    monkeypatch.setattr(handlers, "llm_select_tables", select_tables)
    monkeypatch.setattr(handlers, "get_tables_create_format", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("DDL should be reused")))
    monkeypatch.setattr(handlers, "_build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result="执行对象"))

    chunks = await collect_stream(handlers._stream_sql_generation(
        thread_id="conv",
        user_query="查订单",
        datasource_id="ds_a",
        mode="raw",
        request=DisconnectAfterFirst(),
    ))

    assert chunks == []
    assert graph.initial_state["tables_ddl_info"] == ""
    assert graph.initial_state["loaded_table_names"] == []


@pytest.mark.asyncio
async def test_stream_sql_generation_non_query_state_failure_loads_new_tables(monkeypatch):
    class Graph:
        def __init__(self):
            self.initial_state = None

        async def aget_state(self, _config):
            raise RuntimeError("state unavailable")

        async def astream(self, initial_state, *_args, **_kwargs):
            self.initial_state = initial_state
            yield FakeChunk(content="raw answer", msg_type="ai"), {"langgraph_node": "agent"}

    graph = Graph()
    ddl_calls = []

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: graph)
    monkeypatch.setattr(handlers, "get_all_tables_async", lambda _datasource_id: asyncio.sleep(0, result=[{"table_name": "orders"}]))
    monkeypatch.setattr(handlers, "llm_select_tables", lambda *_args, **_kwargs: asyncio.sleep(0, result=["orders"]))

    def fake_ddl(all_tables, table_names):
        ddl_calls.append((all_tables, table_names))
        return "create table orders(id int)"

    monkeypatch.setattr(handlers, "get_tables_create_format", fake_ddl)
    monkeypatch.setattr(handlers, "_build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    chunks = await collect_stream(handlers._stream_sql_generation(
        thread_id="conv",
        user_query="查订单",
        datasource_id="ds_a",
        mode="raw",
    ))

    assert "raw answer" in "".join(chunks)
    assert graph.initial_state["tables_ddl_info"] == "create table orders(id int)"
    assert graph.initial_state["loaded_table_names"] == ["orders"]
    assert ddl_calls[0][1] == ["orders"]


@pytest.mark.asyncio
async def test_continue_execution_error_and_pending_interrupt_paths(monkeypatch):
    class ErrorGraph:
        def astream(self, *_args, **_kwargs):
            raise RuntimeError("continue failed")

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: ErrorGraph())
    chunks = await collect_stream(handlers._continue_execution("conv"))
    assert "continue failed" in "".join(chunks)

    class PendingGraph:
        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(tool_calls=[{"id": "tc_pending", "name": "semantic_query_tool"}]), {"langgraph_node": "agent"}

        async def aget_state(self, _config):
            return SimpleNamespace(
                values={"token_usage": {"total_tokens": 9}, "messages": [SimpleNamespace(tool_calls=[{"id": "tc_pending", "name": "semantic_query_tool"}])]},
                tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={"args": {"sql": "select 1"}, "message": "确认"})])],
            )

    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: PendingGraph())
    chunks = await collect_stream(handlers._continue_execution("conv", modified_sql=None, action="approve"))
    text = "".join(chunks)
    assert "pending" in text
    assert "message_end" in text


@pytest.mark.asyncio
async def test_stream_sql_generation_tool_chunk_missing_id_and_bad_json(monkeypatch):
    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(tasks=[], values={})

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(tool_calls=[{"id": "tc_bad", "name": "semantic_query_tool"}]), {"langgraph_node": "agent"}
            yield FakeChunk(tool_call_chunks=[{"id": None, "name": "", "args": "{bad"}]), {"langgraph_node": "agent"}
            yield FakeChunk(content="tool done", msg_type="tool", tool_call_id="tc_bad", name="semantic_query_tool"), {"langgraph_node": "tools"}

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: Graph())
    monkeypatch.setattr(handlers, "_build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    chunks = await collect_stream(handlers._stream_sql_generation(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
        mode="query",
    ))
    text = "".join(chunks)
    assert "semantic_query_tool" in text
    assert "tool done" in text
    assert "accumulated_args" not in text


@pytest.mark.asyncio
async def test_stream_sql_generation_non_query_missing_semantic_config_and_no_selected_tables(monkeypatch):
    class Graph:
        def __init__(self):
            self.initial_state = None

        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

        async def astream(self, initial_state, *_args, **_kwargs):
            self.initial_state = initial_state
            yield FakeChunk(content="fallback answer", msg_type="ai"), {"langgraph_node": "agent"}

    graph = Graph()

    async def no_semantic(*_args, **_kwargs):
        return None

    monkeypatch.setattr(handlers, "_get_semantic_config_async", no_semantic)
    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: graph)
    monkeypatch.setattr(handlers, "get_all_tables_async", lambda _datasource_id: asyncio.sleep(0, result=[{"table_name": "orders"}]))
    monkeypatch.setattr(handlers, "llm_select_tables", lambda *_args, **_kwargs: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(handlers, "get_tables_create_format", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no tables selected")))
    monkeypatch.setattr(handlers, "_build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    chunks = await collect_stream(handlers._stream_sql_generation(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
        mode="generate",
        semantic_config_id="missing",
    ))

    assert "fallback answer" in "".join(chunks)
    assert graph.initial_state["tables_ddl_info"] == ""
    assert graph.initial_state["loaded_table_names"] == []


@pytest.mark.asyncio
async def test_stream_sql_generation_tool_call_duplicate_missing_name_and_empty_buffer(monkeypatch):
    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(tasks=[], values={"token_usage": {"total_tokens": 4}})

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(tool_calls=[
                {"id": "", "name": "skip_missing_id"},
                {"id": "tc_dup", "name": "semantic_query_tool"},
            ]), {"langgraph_node": "agent"}
            yield FakeChunk(tool_calls=[{"id": "tc_dup", "name": "semantic_query_tool"}]), {"langgraph_node": "agent"}
            yield FakeChunk(tool_call_chunks=[{"id": "tc_fresh", "name": "semantic_query_tool", "args": ""}]), {"langgraph_node": "agent"}
            yield FakeChunk(content="fresh done", msg_type="tool", tool_call_id="tc_fresh", name="semantic_query_tool"), {"langgraph_node": "tools"}

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: Graph())
    monkeypatch.setattr(handlers, "_build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    chunks = await collect_stream(handlers._stream_sql_generation(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
        mode="query",
    ))
    text = "".join(chunks)

    assert text.count('"tool_call_id": "tc_dup"') == 1
    assert "fresh done" in text
    assert "message_end" in text


@pytest.mark.asyncio
async def test_continue_execution_chunk_creates_buffer_and_finishes_without_token_usage(monkeypatch):
    class Graph:
        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(tool_call_chunks=[{"id": "tc_only_chunk", "name": "semantic_query_tool", "args": '{"dsl":{"metrics":["订单数"]}}'}]), {"langgraph_node": "agent"}
            yield FakeChunk(content="ok", msg_type="tool", tool_call_id="tc_only_chunk", name="semantic_query_tool"), {"langgraph_node": "tools"}

        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: Graph())

    chunks = await collect_stream(handlers._continue_execution("conv", action="approve"))
    text = "".join(chunks)

    assert '"accumulated_args": {"dsl": {"metrics": ["订单数"]}}' in text
    assert "message_end" in text
    assert '"timestamp":' in text
    assert '"usage"' not in text
    assert text.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_stream_sql_generation_forced_missing_buffer_and_empty_selection(monkeypatch):
    original_resolver = handlers._resolve_active_tool_call_id

    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(tasks=[], values={})

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(tool_call_chunks=[{"id": None, "args": '{"forced":true}'}]), {"langgraph_node": "agent"}
            yield FakeChunk(content="forced done", msg_type="tool", tool_call_id="forced"), {"langgraph_node": "tools"}
            yield FakeChunk(tool_calls=[{"id": "tc_select", "name": "select_semantic_config_tool"}]), {"langgraph_node": "agent"}
            yield FakeChunk(
                content='[select_semantic_config_tool] {"selected": false}',
                msg_type="tool",
                tool_call_id="tc_select",
            ), {"langgraph_node": "tools"}

    monkeypatch.setattr(handlers, "_resolve_active_tool_call_id", lambda *_args: "forced")
    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: Graph())
    monkeypatch.setattr(handlers, "_build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    chunks = await collect_stream(handlers._stream_sql_generation(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
        mode="query",
    ))
    text = "".join(chunks)

    assert '"accumulated_args": {"forced": true}' in text
    assert "semantic_config_selected" not in text
    assert "semantic_config_select_required" not in text
    assert original_resolver(None, None, []) is None


@pytest.mark.asyncio
async def test_continue_execution_forced_missing_buffer_interrupt_without_usage(monkeypatch):
    class Graph:
        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(tool_call_chunks=[{"id": None, "args": '{"forced":true}'}]), {"langgraph_node": "agent"}
            yield FakeChunk(content="forced done", msg_type="tool", tool_call_id="forced"), {"langgraph_node": "tools"}

        async def aget_state(self, _config):
            return SimpleNamespace(
                values={"messages": [SimpleNamespace(tool_calls=[{"id": "tc_pending", "name": "semantic_query_tool"}])]},
                tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={"args": {"sql": "select 1"}, "message": "确认"})])],
            )

    monkeypatch.setattr(handlers, "_resolve_active_tool_call_id", lambda *_args: "forced")
    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: Graph())

    chunks = await collect_stream(handlers._continue_execution("conv", action="approve"))
    text = "".join(chunks)

    assert '"accumulated_args": {"forced": true}' in text
    assert '"status": "pending"' in text
    assert "message_end" in text
    assert '"timestamp":' in text
    assert '"usage"' not in text


@pytest.mark.asyncio
async def test_data_analyze_stream_thinking_and_semantic_selection_branches(monkeypatch):
    class Agent:
        async def astream(self, initial_state, config=None, stream_mode=None, subgraphs=False):
            yield ("custom", {"type": "token_usage", "usage": {}})
            yield ("messages", (FakeChunk(additional_kwargs={"reasoning_content": "只思考"}), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (FakeChunk(content="思考后继续", additional_kwargs={"reasoning_content": "有内容思考"}), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (FakeChunk(content="", msg_type="ai"), {"langgraph_node": "thinking"}))
            yield ("messages", (FakeChunk(content="阶段内容", msg_type="ai"), {"langgraph_node": "plan"}))
            yield ("messages", (FakeChunk(tool_calls=[{"id": "tc_selected", "name": "select_semantic_config_tool"}]), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (
                FakeChunk(
                    content='[select_semantic_config_tool] {"selected": true, "selected_config_id": "cfg", "selected_config_name": "指标集"}',
                    msg_type="tool",
                    tool_call_id="tc_selected",
                    name="select_semantic_config_tool",
                ),
                {"langgraph_node": "tools"},
            ))
            yield ("messages", (FakeChunk(tool_calls=[{"id": "tc_candidates", "name": "select_semantic_config_tool"}]), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (
                FakeChunk(
                    content='[select_semantic_config_tool] {"selected": false, "candidates": [{"id": "cfg_a", "name": "指标集A"}]}',
                    msg_type="tool",
                    tool_call_id="tc_candidates",
                    name="select_semantic_config_tool",
                ),
                {"langgraph_node": "tools"},
            ))

        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers.conversation, "get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr(handlers.skill_service, "list_skills_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(handlers.data_analyze_graph, "build_data_analyze_system_prompt", lambda **_kwargs: "system")
    monkeypatch.setattr(handlers.data_analyze_graph, "create_data_analyze_flow_graph", lambda **_kwargs: Agent())
    monkeypatch.setattr(handlers, "build_data_analyze_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"datasource": {"source_type": "mysql"}}))
    monkeypatch.setattr(handlers, "prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))

    response = await handlers.data_analyze_completion_handler(
        conversation_id="conv_da",
        query="分析销售额",
        options={"datasource_id": "ds_a"},
        user_id="u1",
    )
    chunks = await collect_stream(response.body_iterator)
    text = "".join(chunks)

    assert "event: thinking" in text
    assert "思考后继续" in text
    assert "event: task_stage_delta" in text
    assert "semantic_config_selected" in text
    assert "semantic_config_select_required" in text


@pytest.mark.asyncio
async def test_data_analyze_stream_tool_selection_payload_empty_branch(monkeypatch):
    class Agent:
        async def astream(self, initial_state, config=None, stream_mode=None, subgraphs=False):
            yield ("messages", (FakeChunk(tool_calls=[{"id": "tc_empty", "name": "select_semantic_config_tool"}]), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (
                FakeChunk(
                    content='[select_semantic_config_tool] {"selected": false}',
                    msg_type="tool",
                    tool_call_id="tc_empty",
                    name="select_semantic_config_tool",
                ),
                {"langgraph_node": "tools"},
            ))

        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers.conversation, "get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr(handlers.skill_service, "list_skills_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(handlers.data_analyze_graph, "build_data_analyze_system_prompt", lambda **_kwargs: "system")
    monkeypatch.setattr(handlers.data_analyze_graph, "create_data_analyze_flow_graph", lambda **_kwargs: Agent())
    monkeypatch.setattr(handlers, "build_data_analyze_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"datasource": {"source_type": "mysql"}}))
    monkeypatch.setattr(handlers, "prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))

    response = await handlers.data_analyze_completion_handler(
        conversation_id="conv_da_empty",
        query="分析销售额",
        options={"datasource_id": "ds_a"},
        user_id="u1",
    )
    chunks = await collect_stream(response.body_iterator)
    text = "".join(chunks)

    assert "select_semantic_config_tool" in text
    assert "semantic_config_selected" not in text
    assert "semantic_config_select_required" not in text


@pytest.mark.asyncio
async def test_persist_reasoning_segments_keeps_count_on_update_error():
    class Graph:
        async def aupdate_state(self, *_args, **_kwargs):
            raise RuntimeError("state write failed")

    segments = [{"id": "reasoning-0", "content": "思考", "tool_call_count": 0}]

    assert await handlers._persist_reasoning_segments(Graph(), {"thread_id": "t"}, segments, "agent") == 0


@pytest.mark.asyncio
async def test_stream_sql_generation_thinking_with_content_and_untracked_tool(monkeypatch):
    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(
                content="带思考的回答",
                msg_type="ai",
                additional_kwargs={"reasoning_content": "先想一下"},
            ), {"langgraph_node": "agent"}
            yield FakeChunk(content="untracked tool", msg_type="tool", tool_call_id="missing"), {"langgraph_node": "tools"}
            yield FakeChunk(tool_calls=[{"id": "tc_select_bad", "name": "select_semantic_config_tool"}]), {"langgraph_node": "agent"}
            yield FakeChunk(
                content="[select_semantic_config_tool] not-json",
                msg_type="tool",
                tool_call_id="tc_select_bad",
            ), {"langgraph_node": "tools"}

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: Graph())
    monkeypatch.setattr(handlers, "_build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    chunks = await collect_stream(handlers._stream_sql_generation(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
        mode="query",
    ))
    text = "".join(chunks)

    assert "event: thinking" in text
    assert "带思考的回答" in text
    assert "untracked tool" in text
    assert "semantic_config_selected" not in text
    assert "semantic_config_select_required" not in text


@pytest.mark.asyncio
async def test_stream_sql_generation_interrupt_without_token_usage(monkeypatch):
    class Graph:
        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(tool_calls=[{"id": "tc_pending", "name": "semantic_query_tool"}]), {"langgraph_node": "agent"}

        async def aget_state(self, _config):
            return SimpleNamespace(
                values={},
                tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={"args": {"sql": "select 1"}, "message": "确认"})])],
            )

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: Graph())
    monkeypatch.setattr(handlers, "_build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    chunks = await collect_stream(handlers._stream_sql_generation(
        thread_id="conv",
        user_query="查",
        datasource_id="ds_a",
        mode="query",
    ))
    text = "".join(chunks)

    assert '"status": "pending"' in text
    assert "message_end" in text
    assert '"timestamp":' in text
    assert '"usage"' not in text


@pytest.mark.asyncio
async def test_continue_execution_thinking_duplicate_tool_and_empty_args(monkeypatch):
    class Graph:
        async def astream(self, *_args, **_kwargs):
            yield FakeChunk(additional_kwargs={"reasoning_content": "只思考"}), {"langgraph_node": "agent"}
            yield FakeChunk(content="思考后输出", additional_kwargs={"reasoning_content": "继续想"}), {"langgraph_node": "agent"}
            yield FakeChunk(tool_calls=[
                {"id": "tc_dup", "name": "semantic_query_tool"},
                {"id": "tc_dup", "name": "semantic_query_tool"},
            ]), {"langgraph_node": "agent"}
            yield FakeChunk(tool_call_chunks=[{"id": "tc_empty", "name": "semantic_query_tool", "args": ""}]), {"langgraph_node": "agent"}
            yield FakeChunk(content="empty args done", msg_type="tool", tool_call_id="tc_empty", name="semantic_query_tool"), {"langgraph_node": "tools"}
            yield FakeChunk(content="missing buffer done", msg_type="tool", tool_call_id="missing", name="semantic_query_tool"), {"langgraph_node": "tools"}

        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers, "get_chat_data_graph", lambda **_kwargs: Graph())

    chunks = await collect_stream(handlers._continue_execution("conv", action="approve"))
    text = "".join(chunks)

    assert "event: thinking" in text
    assert "思考后输出" in text
    assert text.count('"tool_call_id": "tc_dup"') == 1
    assert "empty args done" in text
    assert "missing buffer done" in text


@pytest.mark.asyncio
async def test_data_analyze_stream_bad_selection_payload_branch(monkeypatch):
    class Agent:
        async def astream(self, initial_state, config=None, stream_mode=None, subgraphs=False):
            yield ("messages", (FakeChunk(tool_calls=[{"id": "tc_bad", "name": "select_semantic_config_tool"}]), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (
                FakeChunk(
                    content="[select_semantic_config_tool] not-json",
                    msg_type="tool",
                    tool_call_id="tc_bad",
                    name="select_semantic_config_tool",
                ),
                {"langgraph_node": "tools"},
            ))

        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[])

    monkeypatch.setattr(handlers, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(handlers.conversation, "get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr(handlers.skill_service, "list_skills_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(handlers.data_analyze_graph, "build_data_analyze_system_prompt", lambda **_kwargs: "system")
    monkeypatch.setattr(handlers.data_analyze_graph, "create_data_analyze_flow_graph", lambda **_kwargs: Agent())
    monkeypatch.setattr(handlers, "build_data_analyze_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"datasource": {"source_type": "mysql"}}))
    monkeypatch.setattr(handlers, "prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))

    response = await handlers.data_analyze_completion_handler(
        conversation_id="conv_da_bad_selection",
        query="分析销售额",
        options={"datasource_id": "ds_a"},
        user_id="u1",
    )
    text = "".join(await collect_stream(response.body_iterator))

    assert "select_semantic_config_tool" in text
    assert "semantic_config_selected" not in text
    assert "semantic_config_select_required" not in text
