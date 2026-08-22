import os
import sys
import json
from dataclasses import replace
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessageChunk, ToolMessageChunk

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.handlers import (
    _extract_reasoning_content,
    _format_reasoning_for_display,
    _format_sse,
    _format_thinking_event,
    _format_tool_event,
    _is_tool_result_error,
    _normalize_enable_thinking,
    _normalize_semantic_selection_payload,
    _append_reasoning_segment,
    _parse_tool_args_buffer,
    _prepare_conversation_workspace_sync,
    _generate_chat_data_stream,
    _finish_running_tool,
    _register_running_tool,
    _resolve_active_tool_call_id,
    prepare_conversation_workspace,
    sql_completion_handler,
)
from apps.chat_data_app.agent.tools import semantic_query as agent_sql_tools
from apps.chat_data_app.services.agent_run.context_budget import ContextInputLimitExceededError
from apps.chat_data_app.services.datasource.target_context import append_selected_execution_targets_to_context
from apps.chat_data_app.services.semantic.run.semantic_conversation_context import SemanticConversationContext


def _semantic_context_from_request(**kwargs) -> SemanticConversationContext:
    datasource_id = str(kwargs.get("datasource_id") or "").strip() or None
    semantic_config_id = str(kwargs.get("semantic_config_id") or "").strip() or None
    selected_ids = list(dict.fromkeys(
        str(member_id).strip()
        for member_id in kwargs.get("member_datasource_ids") or []
        if str(member_id).strip()
    ))
    semantic_config = (
        {"id": semantic_config_id, "name": semantic_config_id, "metrics": [], "dimensions": []}
        if semantic_config_id else None
    )
    targets = [
        {"member_datasource_id": member_id, "member_name": member_id, "org_code": member_id}
        for member_id in selected_ids
    ]
    query = str(kwargs.get("user_query") or "")
    matched_ids = selected_ids if "所有机构" in query or "全部机构" in query else []
    datasource_group_context = append_selected_execution_targets_to_context("", targets, selected_ids)
    return SemanticConversationContext(
        datasource_id=datasource_id,
        datasource_meta={"query_context": ""} if datasource_id else None,
        semantic_config_id=semantic_config_id,
        semantic_config=semantic_config,
        semantic_config_directory_info=("" if semantic_config else "## 当前可见指标集目录\n1. 名称：测试指标集"),
        execution_targets=targets,
        available_member_datasource_ids=selected_ids,
        matched_member_datasource_ids=matched_ids,
        selected_member_datasource_ids=selected_ids,
        datasource_group_context=datasource_group_context,
    )


def _override_semantic_context(monkeypatch, **overrides):
    async def prepare_context(**kwargs):
        context = _semantic_context_from_request(**kwargs)
        values = {
            key: value(context, kwargs) if callable(value) else value
            for key, value in overrides.items()
        }
        return replace(context, **values)

    monkeypatch.setattr(
        "apps.chat_data_app.handlers.prepare_semantic_conversation_context",
        prepare_context,
    )


@pytest.fixture(autouse=True)
def _stub_semantic_conversation_context(monkeypatch):
    async def noop_event_write(**_kwargs):
        return None

    async def prepare_context(**kwargs):
        return _semantic_context_from_request(**kwargs)

    monkeypatch.setattr(
        "apps.chat_data_app.handlers.prepare_semantic_conversation_context",
        prepare_context,
    )

    async def resolve_datasource_binding(_conversation_id, _user_id, requested_datasource_id):
        return requested_datasource_id

    monkeypatch.setattr(
        "apps.chat_data_app.handlers.conversation.resolve_datasource_binding",
        resolve_datasource_binding,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.handlers.conversation_event_service.persist_user_message",
        noop_event_write,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.handlers.conversation_event_service.persist_turn",
        noop_event_write,
    )


def _tool_event_contents(events: list[str]) -> list[str]:
    """解析 SSE 工具事件中的原始工具结果。"""
    contents = []
    for event in events:
        if not event.startswith("event: tool\n"):
            continue
        payload = json.loads(event.split("data: ", 1)[1].strip())
        contents.append(str(payload.get("content") or ""))
    return contents


def test_resolve_active_tool_call_id_prefers_chunk_id():
    running = ["tool_a", "tool_b"]
    assert _resolve_active_tool_call_id("tool_c", "tool_b", running) == "tool_c"


def test_resolve_active_tool_call_id_uses_current_active_when_chunk_id_missing():
    running = ["tool_a", "tool_b"]
    assert _resolve_active_tool_call_id(None, "tool_b", running) == "tool_b"


def test_finish_running_tool_falls_back_to_previous_running_tool():
    running = []
    _register_running_tool(running, "tool_a")
    _register_running_tool(running, "tool_b")

    next_active = _finish_running_tool(running, "tool_b", "tool_b")

    assert running == ["tool_a"]
    assert next_active == "tool_a"


def test_finish_running_tool_clears_active_when_last_tool_finishes():
    running = []
    _register_running_tool(running, "tool_a")

    next_active = _finish_running_tool(running, "tool_a", "tool_a")

    assert running == []
    assert next_active is None


def test_format_sse_serializes_dict_as_event_payload():
    text = _format_sse("message", {"content": "你好"})

    assert text.startswith("event: message\n")
    assert 'data: {"content": "你好"}' in text
    assert text.endswith("\n\n")


def test_format_tool_event_uses_result_as_content():
    text = _format_tool_event(
        tool_name="semantic_query_tool",
        tool_call_id="call_1",
        status="completed",
        args={"dsl": {"metrics": ["订单数"]}},
        result="执行完成",
        timestamp="2026-06-02 10:00:00",
    )

    assert "event: tool" in text
    assert '"tool_name": "semantic_query_tool"' in text
    assert '"content": "执行完成"' in text
    assert '"timestamp": "2026-06-02 10:00:00"' in text


def test_reasoning_and_thinking_helper_branches():
    assert _extract_reasoning_content(SimpleNamespace()) == ""
    assert _extract_reasoning_content(SimpleNamespace(additional_kwargs={"reasoning_content": "思考A"})) == "思考A"
    assert _extract_reasoning_content(SimpleNamespace(response_metadata={"reasoning": "思考B"})) == "思考B"
    assert _extract_reasoning_content(
        SimpleNamespace(additional_kwargs={}),
        {"response_metadata": {"reasoning_text": "思考C"}},
    ) == "思考C"
    assert _extract_reasoning_content(
        SimpleNamespace(additional_kwargs={}),
        {"reasoning_content": "思考D"},
    ) == "思考D"
    assert _format_thinking_event(SimpleNamespace(additional_kwargs={})) is None
    thinking_event = _format_thinking_event(SimpleNamespace(additional_kwargs={"reasoning_content": "正在分析"}))
    assert thinking_event.startswith("event: thinking")
    assert "正在分析" in thinking_event
    tool_thinking_event = _format_thinking_event(SimpleNamespace(additional_kwargs={"reasoning_content": "调用 semantic_search"}))
    assert "语义检索" in tool_thinking_event
    assert "semantic_search" not in tool_thinking_event
    assert _format_reasoning_for_display("调用 semantic_query_tool") == "调用 查询数据"
    assert _format_reasoning_for_display("调用 get_current_time") == "调用 获取当前时间"

    segments = []
    _append_reasoning_segment(segments, "", 0)
    assert segments == []
    _append_reasoning_segment(segments, "a", 0)
    _append_reasoning_segment(segments, "b", 0)
    _append_reasoning_segment(segments, "c", 1)
    _append_reasoning_segment(segments, "调用 semantic_search", 1)
    assert segments == [
        {"id": "reasoning-0", "content": "ab", "tool_call_count": 0},
        {"id": "reasoning-1", "content": "c调用 语义检索", "tool_call_count": 1},
    ]


def test_normalize_enable_thinking_branches():
    assert _normalize_enable_thinking(None) is None
    assert _normalize_enable_thinking(True) is True
    assert _normalize_enable_thinking(False) is False
    assert _normalize_enable_thinking(1) is True
    assert _normalize_enable_thinking(0) is False
    assert _normalize_enable_thinking(" true ") is True
    assert _normalize_enable_thinking("YES") is True
    assert _normalize_enable_thinking("off") is False
    assert _normalize_enable_thinking("no") is False
    assert _normalize_enable_thinking("unknown") is None
    assert _normalize_enable_thinking(object()) is None


def test_normalize_semantic_selection_payload_branches():
    assert _normalize_semantic_selection_payload(None) is None
    assert _normalize_semantic_selection_payload({"semantic_config_id": ""}) is None
    normalized = _normalize_semantic_selection_payload({
        "semantic_config_id": " cfg_a ",
        "semantic_config_name": "",
        "confidence": " high ",
        "reason": " 命中 ",
        "matched_metrics": [" 订单数 ", "", None],
        "matched_dimensions": "bad",
        "auto_selected": 1,
        "user_selected": "",
    })

    assert normalized == {
        "semantic_config_id": "cfg_a",
        "semantic_config_name": "cfg_a",
        "confidence": "high",
        "reason": "命中",
        "matched_metrics": ["订单数", "None"],
        "matched_dimensions": [],
        "auto_selected": True,
        "user_selected": False,
    }


def test_parse_tool_args_buffer_falls_back_on_invalid_json():
    fallback = {"sql": "select 1"}

    assert _parse_tool_args_buffer("", fallback) == fallback
    assert _parse_tool_args_buffer('{"sql":"select 2"}', fallback) == {"sql": "select 2"}
    assert _parse_tool_args_buffer('["not_dict"]', fallback) == fallback
    assert _parse_tool_args_buffer("{bad json", fallback) == fallback


def test_is_tool_result_error_detects_plain_errors():
    assert _is_tool_result_error("执行失败: 表不存在") is True
    assert _is_tool_result_error("error: syntax error") is True
    assert _is_tool_result_error('{"type":"query_result","status":"failed"}') is True
    assert _is_tool_result_error('{"type":"query_result","status":"cancelled"}') is True
    assert _is_tool_result_error('{"type":"query_result","status":"succeeded"}') is False
    assert _is_tool_result_error("[dashboard_write] {\"success\": false}") is False
    assert _is_tool_result_error("执行完成") is False


def test_prepare_conversation_workspace_sync_and_async(monkeypatch):
    import asyncio
    calls = {}

    monkeypatch.setattr(
        "apps.chat_data_app.handlers.ensure_conversation_workspace",
        lambda conversation_id, user_id: calls.setdefault(
            "workspace", {"root": f"/tmp/{user_id}/{conversation_id}"}
        ),
    )

    paths = _prepare_conversation_workspace_sync("conv_ws", "user_a")
    assert paths["root"] == "/tmp/user_a/conv_ws"

    async_paths = asyncio.run(prepare_conversation_workspace("conv_ws", "user_a"))
    assert async_paths == paths


def test_agent_sql_normalize_member_datasource_ids_dedupes_and_strips():
    assert agent_sql_tools._normalize_member_datasource_ids([" m1 ", "", None, "m1", "m2"]) == ["m1", "m2"]


def _FakeMessageChunk(
    *,
    content="",
    type_="ai",
    tool_calls=None,
    tool_call_chunks=None,
    tool_call_id="",
    name="",
):
    """测试工厂：返回真实 LangChain 消息 Chunk，不伪造 type 字段。"""
    if type_ == "human":
        return HumanMessageChunk(content=content)
    if type_ == "tool":
        return ToolMessageChunk(content=content, tool_call_id=tool_call_id, name=name or None)
    normalized_tool_calls = [{**tool_call, "args": tool_call.get("args") or {}} for tool_call in tool_calls or []]
    return AIMessageChunk(
        content=content,
        tool_calls=normalized_tool_calls,
        tool_call_chunks=tool_call_chunks or [],
    )


class _FakeGraph:
    def __init__(self, events=None, state=None, raise_on_stream=False):
        self.events = events or []
        self.state = state or SimpleNamespace(tasks=[], values={})
        self.raise_on_stream = raise_on_stream
        self.initial_state = None

    async def aget_state(self, config):
        return self.state

    async def astream(self, initial_state, config=None, stream_mode=None):
        self.initial_state = initial_state
        if self.raise_on_stream:
            raise RuntimeError("graph failed")
        for event in self.events:
            yield event


@asynccontextmanager
async def _fake_checkpointer():
    yield object()


async def _collect_async_iter(async_iterable):
    return [item async for item in async_iterable]


def test_generate_chat_data_stream_enters_agent_when_semantic_config_not_selected(monkeypatch):
    import asyncio

    captured_agent_kwargs = {}
    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="5+5=10"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())

    def create_agent(_runtime_options, **kwargs):
        captured_agent_kwargs.update(kwargs)
        return fake_graph

    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", create_agent)
    _override_semantic_context(
        monkeypatch,
        semantic_config_directory_info="## 当前可见指标集目录\n1. 名称：订单分析",
    )

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_auto_semantic_search",
        user_query="5+5等于多少",
        datasource_id="ds_a",
        semantic_config_id=None,
    )))

    joined = "".join(events)
    assert "5+5=10" in joined
    assert "prompt_context" not in fake_graph.initial_state
    assert "## 当前可见指标集目录\n1. 名称：订单分析" in captured_agent_kwargs["model_invocation"].system_prompt
    human_message = fake_graph.initial_state["messages"][0]
    assert "skill_names" not in human_message.additional_kwargs


def test_generate_chat_data_stream_uses_runtime_snapshot_for_system_prompt(monkeypatch, tmp_path):
    import asyncio

    captured_agent_kwargs = {}
    snapshot_dir = tmp_path / "runtime-skills"
    runtime_skill = snapshot_dir / "runtime-skill"
    runtime_skill.mkdir(parents=True)
    (runtime_skill / "SKILL.md").write_text(
        "---\nname: stale-management-name\ntitle: Runtime skill\ndescription: Only in this snapshot\n---\n# skill\n",
        encoding="utf-8",
    )
    workspace_root = tmp_path / "threads" / "u1" / "conv_snapshot" / "user-data"

    async def fake_prepare_workspace(*_args, **_kwargs):
        workspace_root.mkdir(parents=True)
        return {"root": str(workspace_root)}

    async def fake_build_snapshot(user_id, conversation_id):
        assert (user_id, conversation_id) == ("u1", "conv_snapshot")
        return snapshot_dir

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.prepare_conversation_workspace", fake_prepare_workspace)
    monkeypatch.setattr("apps.chat_data_app.handlers.build_runtime_skill_snapshot_async", fake_build_snapshot)

    def create_agent(_runtime_options, **kwargs):
        captured_agent_kwargs.update(kwargs)
        return fake_graph

    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", create_agent)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_snapshot",
        user_query="查询订单",
        datasource_id="ds_a",
        user_id="u1",
    )))

    assert "完成" in "".join(events)
    system_prompt = captured_agent_kwargs["model_invocation"].system_prompt
    assert "<name>runtime-skill</name>" in system_prompt
    assert "<title>Runtime skill</title>" in system_prompt
    assert "stale-management-name" not in system_prompt
    assert captured_agent_kwargs["workspace_runtime"].available_skill_names == ["runtime-skill"]


def test_generate_chat_data_stream_streams_semantic_retrieval_tool_result(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_pay"],
        "semantic_configs": {
            "cfg_pay": {
                "id": "cfg_pay",
                "name": "支付汇总",
                "metrics": [{"name": "交易金额", "expression": "SUM(pay.amount)"}],
            },
        },
    }
    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "semantic_search"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "call_select", "name": "semantic_search", "args": "{\"query\":\"查交易金额\"}"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="[semantic_search]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="semantic_search"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="已查询支付数据"), {"langgraph_node": "model"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_select_tool_event",
        user_query="按机构查询交易金额",
        datasource_id="ds_a",
    )))

    joined = "".join(events)
    assert "event: tool" in joined
    assert "[semantic_search]" in _tool_event_contents(events)[-1]
    assert '"semantic_config_ids": ["cfg_pay"]' in _tool_event_contents(events)[-1]
    assert '"expression": "SUM(pay.amount)"' in _tool_event_contents(events)[-1]
    assert '"tool_name": "semantic_search"' in joined
    assert "event: semantic_config_selected" not in joined
    assert "event: semantic_config_select_required" not in joined
    assert "已查询支付数据" in joined


def test_generate_chat_data_stream_keeps_user_selected_semantic_config_selection(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="商品数量为 10"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )
    selection = {
        "semantic_config_id": "cfg_test",
        "semantic_config_name": "测试指标集",
        "confidence": "low",
        "reason": "用户从候选指标集中选择",
        "matched_metrics": ["商品数量", ""],
        "matched_dimensions": [],
        "auto_selected": False,
        "user_selected": True,
    }

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_user_selected_semantic",
        user_query="商品数量有多少",
        datasource_id="ds_a",
        semantic_config_id="cfg_test",
        semantic_config_selection=selection,
    )))

    joined = "".join(events)
    assert "商品数量为 10" in joined
    human_message = fake_graph.initial_state["messages"][0]
    selection = human_message.additional_kwargs["semantic_config_selection"]
    assert selection["semantic_config_id"] == "cfg_test"
    assert selection["semantic_config_name"] == "测试指标集"
    assert selection["matched_metrics"] == ["商品数量"]
    assert selection["user_selected"] is True
    assert selection["auto_selected"] is False
    assert "skill_names" not in human_message.additional_kwargs


def test_generate_chat_data_stream_keeps_explicit_query_skill(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_query_skill_names",
        user_query="查询金额",
        datasource_id="ds_a",
        skill_names=["custom-query-skill", "risk-query-skill"],
    )))

    assert "完成" in "".join(events)
    human_message = fake_graph.initial_state["messages"][0]
    assert human_message.additional_kwargs["skill_names"] == ["custom-query-skill", "risk-query-skill"]


def test_generate_chat_data_stream_streams_multiple_semantic_configs_as_tool_result(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order", "cfg_pay"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析"},
            "cfg_pay": {"id": "cfg_pay", "name": "支付汇总"},
        },
    }
    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "semantic_search"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="[semantic_search]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="semantic_search"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="请选择指标集"), {"langgraph_node": "model"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_multiple_semantic_search_event",
        user_query="查询金额",
        datasource_id="ds_a",
        semantic_config_id=None,
    )))

    joined = "".join(events)
    assert "event: tool" in joined
    assert '"semantic_config_ids": ["cfg_order", "cfg_pay"]' in _tool_event_contents(events)[-1]
    assert "订单分析" in joined
    assert "支付汇总" in joined
    assert "event: semantic_config_selected" not in joined
    assert "event: semantic_config_select_required" not in joined


def test_generate_chat_data_stream_streams_retrieval_without_query_plan(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order", "cfg_user"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析"},
            "cfg_user": {"id": "cfg_user", "name": "用户分析"},
        },
    }
    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "semantic_search"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="[semantic_search]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="semantic_search"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="已按多个指标集完成查询"), {"langgraph_node": "model"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_retrieval_tool_event",
        user_query="查询订单金额和用户数",
        datasource_id="ds_a",
        semantic_config_id=None,
    )))

    joined = "".join(events)
    assert "event: tool" in joined
    assert "event: semantic_config_select_required" not in joined
    assert "query_plan" not in joined
    assert "已按多个指标集完成查询" in joined


def test_generate_chat_data_stream_streams_retrieval_without_cross_config_decision(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order", "cfg_user"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析"},
            "cfg_user": {"id": "cfg_user", "name": "用户分析"},
        },
    }
    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "semantic_search"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="[semantic_search]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="semantic_search"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="这个问题需要跨指标集合并，当前不支持。"), {"langgraph_node": "model"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_cross_config_retrieval",
        user_query="按用户等级关联订单金额",
        datasource_id="ds_a",
        semantic_config_id=None,
    )))

    joined = "".join(events)
    assert "event: tool" in joined
    assert "event: semantic_config_select_required" not in joined
    assert "cross_config_required" not in joined
    assert "这个问题需要跨指标集合并，当前不支持。" in joined


def test_generate_chat_data_stream_uses_selected_config_without_precheck(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已进入当前指标集链路"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_selected_semantic_no_precheck",
        user_query="查询明细",
        datasource_id="ds_a",
        semantic_config_id="cfg_order",
    )))

    joined = "".join(events)
    assert "event: semantic_config_selected" not in joined
    assert "已进入当前指标集链路" in joined
    assert "semantic_config_selection" not in fake_graph.initial_state["messages"][0].additional_kwargs


def test_generate_chat_data_stream_keeps_supported_selected_config(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已查询订单数据"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_keep_semantic",
        user_query="查询订单金额",
        datasource_id="ds_a",
        semantic_config_id="cfg_order",
    )))

    joined = "".join(events)
    assert "event: semantic_config_selected" not in joined
    assert "已查询订单数据" in joined
    assert "semantic_config_selection" not in fake_graph.initial_state["messages"][0].additional_kwargs


def test_generate_chat_data_stream_runs_after_user_selects_ambiguous_candidate(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="商品数量为 10"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_selected_ambiguous_semantic",
        user_query="商品数量是多少",
        datasource_id="ds_a",
        semantic_config_id="cfg_order",
    )))

    joined = "".join(events)
    assert "event: semantic_config_select_required" not in joined
    assert "商品数量为 10" in joined


def test_generate_chat_data_stream_does_not_preblock_selected_config(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="原始链路返回当前指标集不支持"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_no_preblock_semantic",
        user_query="查询退款金额",
        datasource_id="ds_a",
        semantic_config_id="cfg_order",
    )))

    joined = "".join(events)
    assert "当前选择的指标集不支持该问题：缺少退款金额指标" not in joined
    assert "原始链路返回当前指标集不支持" in joined


def test_generate_chat_data_stream_does_not_block_multiple_semantic_configs(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order", "cfg_pay"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析"},
            "cfg_pay": {"id": "cfg_pay", "name": "支付汇总"},
        },
    }
    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "semantic_search"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="[semantic_search]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="semantic_search"), {"langgraph_node": "tools"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_multiple_semantic",
        user_query="查询金额",
        datasource_id="ds_a",
        semantic_config_id=None,
    )))

    joined = "".join(events)
    assert "event: tool" in joined
    assert "event: semantic_config_select_required" not in joined
    assert "订单分析" in joined
    assert "支付汇总" in joined


def test_generate_chat_data_stream_does_not_block_cross_config_retrieval(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order", "cfg_product"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析"},
            "cfg_product": {"id": "cfg_product", "name": "商品分析"},
        },
    }
    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "semantic_search"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="[semantic_search]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="semantic_search"), {"langgraph_node": "tools"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_cross_semantic_retrieval",
        user_query="查询订单金额和商品库存",
        datasource_id="ds_a",
        semantic_config_id=None,
    )))

    joined = "".join(events)
    assert "event: tool" in joined
    assert "event: semantic_config_select_required" not in joined
    assert "订单分析" in joined
    assert "商品分析" in joined


def test_generate_chat_data_stream_emits_tool_message_usage_and_done(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_1", "name": "semantic_query_tool"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "call_1", "name": "semantic_query_tool", "args": "{\"dsl\":{\"metrics\":[\"订单数\"]}}"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="执行完成", type_="tool", tool_call_id="call_1"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="已查询订单数据"), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="内部工具模型输出"), {"langgraph_node": "tools"}),
        ],
        state=SimpleNamespace(tasks=[], values={"token_usage": {"total_tokens": 12}}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_a",
        user_query="查订单数",
        datasource_id="ds_a",
        user_id="user_a",
        username="测试用户",
    )))

    joined = "".join(events)
    assert '"status": "running"' in joined
    assert '"accumulated_args": {"dsl": {"metrics": ["订单数"]}}' in joined
    assert "已查询订单数据" in joined
    assert "内部工具模型输出" not in joined
    assert 'event: message_end' in joined
    assert '"usage": {"total_tokens": 12}' in joined
    assert '"timestamp":' in joined
    assert events[-1] == "event: done\ndata: [DONE]\n\n"
    assert fake_graph.initial_state["execution_context"]["datasource_id"] == "ds_a"
    assert fake_graph.initial_state["operator_name"] == "测试用户"


def test_generate_chat_data_stream_projects_visible_members_from_shared_context(monkeypatch):
    import asyncio

    captured_agent_kwargs = {}
    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())

    def create_agent(_runtime_options, **kwargs):
        captured_agent_kwargs.update(kwargs)
        return fake_graph

    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", create_agent)

    asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_query_members",
        user_query="查询所有机构订单数",
        datasource_id="ds_a",
        user_id="user_a",
        member_datasource_ids=["m1"],
        semantic_config_id="cfg_a",
    )))

    assert fake_graph.initial_state["execution_context"]["available_member_datasource_ids"] == ["m1"]
    assert fake_graph.initial_state["execution_context"]["matched_member_datasource_ids"] == ["m1"]
    datasource_group_context = captured_agent_kwargs["datasource_context"].datasource_group_context
    assert "当前用户已选择的执行对象数据源" in datasource_group_context
    assert "member_datasource_id: m1" in datasource_group_context
    assert "m2" not in datasource_group_context


def test_generate_chat_data_stream_keeps_empty_member_scope_from_shared_context(monkeypatch):
    import asyncio

    captured_agent_kwargs = {}
    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())

    def create_agent(_runtime_options, **kwargs):
        captured_agent_kwargs.update(kwargs)
        return fake_graph

    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", create_agent)
    _override_semantic_context(
        monkeypatch,
        datasource_group_context="当前数据源组按执行对象数据源分库",
    )

    asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_query_members",
        user_query="查询所有机构订单数",
        datasource_id="ds_a",
        user_id="user_a",
        member_datasource_ids=[],
        semantic_config_id="cfg_a",
    )))

    assert fake_graph.initial_state["execution_context"]["available_member_datasource_ids"] == []
    assert fake_graph.initial_state["execution_context"]["matched_member_datasource_ids"] == []
    assert "prompt_context" not in fake_graph.initial_state
    assert captured_agent_kwargs["datasource_context"].datasource_group_context == "当前数据源组按执行对象数据源分库"


def test_generate_chat_data_stream_projects_selected_member_scope_from_shared_context(monkeypatch):
    import asyncio

    captured_agent_kwargs = {}
    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())

    def create_agent(_runtime_options, **kwargs):
        captured_agent_kwargs.update(kwargs)
        return fake_graph

    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", create_agent)

    asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_query_member_selected",
        user_query="查询订单数",
        datasource_id="ds_a",
        user_id="user_a",
        member_datasource_ids=["m1"],
        semantic_config_id="cfg_a",
    )))

    assert fake_graph.initial_state["execution_context"]["available_member_datasource_ids"] == ["m1"]
    datasource_group_context = captured_agent_kwargs["datasource_context"].datasource_group_context
    assert "当前用户已选择的执行对象数据源" in datasource_group_context
    assert "member_datasource_id: m1" in datasource_group_context
    assert "m2" not in datasource_group_context


def test_generate_chat_data_stream_emits_error_when_graph_fails(monkeypatch):
    import asyncio

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: _FakeGraph(raise_on_stream=True))

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_error",
        user_query="查订单数",
        datasource_id="ds_a",
    )))

    joined = "".join(events)
    assert 'event: error\ndata: {"error": "智能问数执行失败，请稍后重试。"}' in joined
    assert "graph failed" not in joined
    assert events[-1] == "event: done\ndata: [DONE]\n\n"


def test_generate_chat_data_stream_keeps_context_limit_error_actionable(monkeypatch):
    import asyncio

    class ContextLimitGraph(_FakeGraph):
        async def astream(self, initial_state, config=None, stream_mode=None):
            _ = initial_state, config, stream_mode
            raise ContextInputLimitExceededError("当前输入内容过长，请拆分问题后重试。")
            yield  # pragma: no cover - keeps this an async generator for the stream adapter

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: ContextLimitGraph())

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_context_limit",
        user_query="查订单数",
        datasource_id="ds_a",
    )))

    joined = "".join(events)
    assert "当前输入内容过长，请拆分问题后重试。" in joined
    assert "智能问数执行失败，请稍后重试。" not in joined
    assert events[-1] == "event: done\ndata: [DONE]\n\n"


def test_generate_chat_data_stream_semantic_config_missing(monkeypatch):
    import asyncio

    _override_semantic_context(
        monkeypatch,
        semantic_config=None,
        semantic_config_error="未找到可用指标集: cfg_missing",
    )
    missing_events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_missing_semantic",
        user_query="查订单",
        datasource_id="ds_a",
        semantic_config_id="cfg_missing",
    )))
    assert "未找到可用指标集: cfg_missing" in "".join(missing_events)



def test_generate_chat_data_stream_handles_disconnect_and_cached_tables(monkeypatch):
    import asyncio

    class DisconnectRequest:
        async def is_disconnected(self):
            return True

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="不应返回"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_disconnect",
        user_query="查订单",
        datasource_id="ds_a",
        request=DisconnectRequest(),
    )))

    assert events == []
    assert fake_graph.initial_state is None


def test_generate_chat_data_stream_handles_state_lookup_failure_and_chunk_without_content(monkeypatch):
    import asyncio

    class StateFailGraph(_FakeGraph):
        async def aget_state(self, config):
            raise RuntimeError("state missing")

    fake_graph = StateFailGraph(events=[
        (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "call_1", "name": "semantic_query_tool", "args": ""}]), {"langgraph_node": "model"}),
        (_FakeMessageChunk(content="完成"), {"langgraph_node": "model"}),
    ])
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_generate_chat_data_stream(
        thread_id="conv_state_fail",
        user_query="查订单",
        datasource_id="ds_a",
    )))

    joined = "".join(events)
    assert "完成" in joined
    assert "event: done" in joined


def test_sql_completion_handler_wraps_stream(monkeypatch):
    import asyncio

    captured = {}

    async def fake_get_or_create(*args, **kwargs):
        captured["conversation"] = args
        return {}

    async def fake_start_run(*args, **kwargs):
        captured["start_run_args"] = args
        captured["start_run_kwargs"] = kwargs
        return "run-test", []

    def fake_submit(**kwargs):
        captured["submit_kwargs"] = kwargs

    async def fake_event_stream(**kwargs):
        captured["event_stream_kwargs"] = kwargs
        yield {
            "stream_id": "test-1-0",
            "payload": _format_sse("done", "[DONE]"),
        }

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", fake_get_or_create)
    monkeypatch.setattr(
        "apps.chat_data_app.handlers.agent_inbox_service.start_run",
        fake_start_run,
    )
    monkeypatch.setattr("apps.chat_data_app.handlers.agent_run_runtime.submit", fake_submit)
    monkeypatch.setattr("apps.chat_data_app.handlers.agent_run_event_service.stream", fake_event_stream)

    response = asyncio.run(sql_completion_handler(
        conversation_id="conv_sql",
        query="查订单",
        options={"datasource_id": "ds_a", "mode": "query", "semantic_config_id": "cfg", "model_id": "m1", "member_datasource_ids": ["m1"]},
        user_id="u1",
        username="测试用户",
    ))
    assert response.media_type == "text/event-stream"
    assert response.headers["X-ChatData-Run-Id"] == "run-test"
    assert response.headers["X-Accel-Buffering"] == "no"
    assert captured["conversation"] == ("", "chat_data", "conv_sql", "u1")
    chunks = asyncio.run(_collect_async_iter(response.body_iterator))
    assert chunks == ["id: test-1-0\nevent: done\ndata: [DONE]\n\n"]
    runtime_context = captured["start_run_args"][3]
    assert captured["start_run_args"][:3] == ("conv_sql", "u1", "测试用户")
    assert runtime_context["member_datasource_ids"] == ["m1"]
    assert runtime_context["semantic_config_id"] == "cfg"
    assert runtime_context["model_id"] == "m1"
    assert captured["submit_kwargs"] == {
        "run_id": "run-test",
        "conversation_id": "conv_sql",
        "user_id": "u1",
        "operator_name": "测试用户",
    }
    assert captured["event_stream_kwargs"]["run_id"] == "run-test"
    assert captured["event_stream_kwargs"]["last_event_id"] == "0-0"


# ---- 图片附件准入（vision_attachments 路径标记，base64 由 middleware 请求层装载） ----

from apps.chat_data_app.services.agent_run.message_compose import (
    build_file_attachment_text as _build_file_attachment_text,
    is_image_attachment as _is_image_attachment,
    select_vision_attachments as _select_vision_attachments,
)
from apps.chat_data_app.services.agent_run.uploaded_file_service import MAX_IMAGES_PER_MESSAGE
from apps.chat_data_app.services.agent_run.conversation_event_service import _extract_text_content


def test_is_image_attachment_checks_type_and_size():
    ok = {"file_id": "f1", "file_type": "png", "file_size": 1024}
    assert _is_image_attachment(ok)
    assert not _is_image_attachment({"file_id": "f2", "file_type": "pdf", "file_size": 1024})
    assert not _is_image_attachment({"file_type": "png", "file_size": 1024})  # 无 file_id
    assert not _is_image_attachment({**ok, "file_size": 99 * 1024 * 1024})  # 超大小上限
    # 输入卸载附件（无 file_id 的 parsed_path 条目）
    assert not _is_image_attachment({"original_filename": "input.txt", "parsed_path": "/mnt/x"})


def test_select_vision_attachments_returns_image_paths_only():
    atts = [
        {"file_id": "img1", "original_filename": "a.png", "file_type": "png",
         "file_size": 10, "virtual_path": "/mnt/user-data/uploads/a.png"},
        {"file_id": "doc1", "original_filename": "b.pdf", "file_type": "pdf",
         "file_size": 10, "virtual_path": "/mnt/user-data/uploads/b.pdf"},
        {"file_id": "img2", "original_filename": "c.jpg", "file_type": "jpg",
         "file_size": 10, "virtual_path": "/mnt/user-data/uploads/c.jpg"},
    ]
    paths = _select_vision_attachments(atts)
    assert paths == ["/mnt/user-data/uploads/a.png", "/mnt/user-data/uploads/c.jpg"]


def test_select_vision_attachments_caps_at_limit_and_skips_invalid():
    images = [
        {"file_id": f"img{i}", "original_filename": f"{i}.png", "file_type": "png",
         "file_size": 10, "virtual_path": f"/mnt/user-data/uploads/{i}.png"}
        for i in range(MAX_IMAGES_PER_MESSAGE + 2)
    ]
    oversized = [{"file_id": "big", "original_filename": "big.png", "file_type": "png",
                  "file_size": 99 * 1024 * 1024, "virtual_path": "/mnt/user-data/uploads/big.png"}]
    paths = _select_vision_attachments(oversized + images)
    assert len(paths) == MAX_IMAGES_PER_MESSAGE
    assert all(path.startswith("/mnt/user-data/uploads/") for path in paths)


def test_select_vision_attachments_empty_without_images():
    atts = [
        {"file_id": "doc1", "original_filename": "b.pdf", "file_type": "pdf",
         "file_size": 10, "virtual_path": "/mnt/user-data/uploads/b.pdf"},
        {"original_filename": "input.txt", "parsed_path": "/mnt/x"},
    ]
    assert _select_vision_attachments([]) == []
    assert _select_vision_attachments(atts) == []


def test_extract_text_content_strips_attachment_lines():
    blocks = [
        {"type": "text", "text": "你好\n[attachment] /mnt/user-data/uploads/a.png"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
    ]
    assert _extract_text_content(blocks) == "你好"
    # 纯字符串历史消息同样剔除占位行
    assert _extract_text_content("查订单\n[attachment] /mnt/x.xlsx") == "查订单"
    # 只发文件无文字 → 空字符串（标题生成走兜底）
    assert _extract_text_content([{"type": "text", "text": "[attachment] /x/a.png"}]) == ""
