import os
import sys
import json
from types import SimpleNamespace
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.handlers import (
    _build_execution_target_info_async,
    _build_semantic_selection_error,
    _build_semantic_select_required_event,
    _continue_execution,
    _extract_select_semantic_config_payload,
    _format_sse,
    _format_tool_event,
    _get_interrupt_info,
    _get_semantic_config_async,
    _is_tool_result_error,
    _normalize_semantic_selection_payload,
    _parse_tool_args_buffer,
    _prepare_data_analyze_workspace_sync,
    _stream_sql_generation,
    _finish_running_tool,
    _register_running_tool,
    _resolve_active_tool_call_id,
    _semantic_selection_event_from_tool_payload,
    _semantic_select_required_event_from_tool_payload,
    data_analyze_completion_handler,
    prepare_data_analyze_workspace,
    sql_completion_handler,
    sql_resume_handler,
)
from apps.chat_data_app.agent.tools import sql as agent_sql_tools


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
        tool_name="execute_sql_tool",
        tool_call_id="call_1",
        status="completed",
        args={"sql": "select 1"},
        result="执行完成",
        timestamp="2026-06-02 10:00:00",
    )

    assert "event: tool" in text
    assert '"tool_name": "execute_sql_tool"' in text
    assert '"content": "执行完成"' in text
    assert '"timestamp": "2026-06-02 10:00:00"' in text


def test_semantic_selection_helper_branches():
    assert _build_semantic_selection_error(None).startswith("当前数据源组没有")
    assert "跨指标集" in _build_semantic_selection_error({
        "cross_config_required": True,
        "cross_config_reason": "订单和库存分别在不同指标集",
    })
    assert _build_semantic_selection_error({
        "ambiguous": True,
        "candidates": [{"id": "cfg_a", "name": "订单分析"}, {"id": "cfg_b"}],
    }) == "该问题可由多个指标集回答，请明确选择：订单分析、cfg_b"
    assert _build_semantic_selection_error({"ambiguous": True, "reason": "请明确指标集"}) == "请明确指标集"
    assert "未能确定最适合" in _build_semantic_selection_error({
        "candidates": [
            {"id": "cfg_a", "name": "订单分析", "reason": "命中订单"},
            {"id": "cfg_b", "confidence": "low"},
            "skip",
        ]
    })
    assert _build_semantic_selection_error({"reason": ""}).startswith("当前可用指标集")

    required = _build_semantic_select_required_event(
        {
            "ambiguous": True,
            "ambiguity_reason": "多个候选",
            "candidates": [
                {"id": "", "name": "无效"},
                {"id": "cfg_a", "name": "订单分析", "coverage": "full", "confidence": "high", "reason": "命中"},
                "skip",
            ],
        },
        "查询金额",
    )
    assert required["query"] == "查询金额"
    assert required["ambiguous"] is True
    assert required["candidates"] == [{"id": "cfg_a", "name": "订单分析", "coverage": "full", "confidence": "high", "reason": "命中"}]


def test_select_semantic_config_payload_helpers():
    assert _extract_select_semantic_config_payload("plain") is None
    assert _extract_select_semantic_config_payload("[select_semantic_config_tool]\n{bad") is None
    assert _extract_select_semantic_config_payload("[select_semantic_config_tool]\n[]") is None
    payload = _extract_select_semantic_config_payload('[select_semantic_config_tool]\n{"selected": true, "selected_config_id": "cfg_a"}')
    assert payload == {"selected": True, "selected_config_id": "cfg_a"}

    selected_event = _semantic_selection_event_from_tool_payload({
        "selected_config_id": "cfg_a",
        "selected_config_name": "",
        "reason": "",
        "message": "已选择",
        "matched_metrics": ["订单数"],
    })
    required_event = _semantic_select_required_event_from_tool_payload({
        "message": "需要选择",
        "reason": "多个候选",
        "ambiguous": True,
        "candidates": [{"id": "cfg_a", "name": "订单分析"}],
    }, "查订单")

    assert selected_event["semantic_config_name"] == "cfg_a"
    assert selected_event["reason"] == "已选择"
    assert selected_event["matched_metrics"] == ["订单数"]
    assert required_event["query"] == "查订单"
    assert required_event["candidates"][0]["id"] == "cfg_a"


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


def test_is_tool_result_error_detects_plain_and_dashboard_errors():
    assert _is_tool_result_error("执行失败: 表不存在") is True
    assert _is_tool_result_error("error: syntax error") is True
    assert _is_tool_result_error("[dashboard_write] {\"success\": false}") is True
    assert _is_tool_result_error("[dashboard_write] {bad json}") is False
    assert _is_tool_result_error("执行完成") is False


def test_get_interrupt_info_reads_task_interrupt_and_tool_call():
    state = SimpleNamespace(
        tasks=[
            SimpleNamespace(interrupts=[
                SimpleNamespace(value={"args": {"sql": "select 1"}, "message": "请确认"})
            ])
        ],
        values={
            "messages": [
                SimpleNamespace(tool_calls=[{"id": "call_1", "name": "execute_sql_tool"}])
            ]
        },
    )

    info = _get_interrupt_info(state)

    assert info["args"] == {"sql": "select 1"}
    assert info["message"] == "请确认"
    assert info["tool_name"] == "execute_sql_tool"
    assert info["tool_call_id"] == "call_1"


def test_get_interrupt_info_returns_none_for_empty_or_invalid_state():
    assert _get_interrupt_info(None) is None
    assert _get_interrupt_info(SimpleNamespace(tasks=[], values={})) is None
    assert _get_interrupt_info(SimpleNamespace(tasks=[SimpleNamespace(interrupts=[])], values={})) is None


def test_build_execution_target_info_async_formats_single_and_multi_targets(monkeypatch):
    class FakeDatasourceManager:
        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            targets = [
                {"member_datasource_id": "m1", "member_name": "杭州库"},
                {"member_datasource_id": "m2", "member_name": "宁波库"},
            ]
            if member_datasource_ids == ["m1"]:
                return targets[:1]
            return targets

    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: FakeDatasourceManager())

    import asyncio

    single = asyncio.run(_build_execution_target_info_async("ds_a", ["m1"]))
    multi = asyncio.run(_build_execution_target_info_async("ds_a", None))

    assert "仅在 1 个执行对象" in single
    assert "1. 杭州库" in single
    assert "2 个执行对象" in multi
    assert "2. 宁波库" in multi


def test_build_execution_target_info_async_empty_and_error(monkeypatch):
    import asyncio

    assert asyncio.run(_build_execution_target_info_async(None, None)) == ""

    class EmptyManager:
        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            return []

    class ErrorManager:
        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            raise RuntimeError("manager failed")

    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: EmptyManager())
    assert asyncio.run(_build_execution_target_info_async("ds_a", None)) == ""

    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: ErrorManager())
    assert asyncio.run(_build_execution_target_info_async("ds_a", None)) == ""


def test_prepare_data_analyze_workspace_sync_and_async(monkeypatch):
    import asyncio
    calls = {}

    monkeypatch.setattr(
        "apps.chat_data_app.handlers.ensure_data_analyze_workspace",
        lambda conversation_id: calls.setdefault("workspace", {"root": f"/tmp/{conversation_id}"}),
    )
    def fake_write_workspace_json(root, path, payload):
        calls["write"] = (root, path, payload)
        return f"{root}/{path}"

    monkeypatch.setattr(
        "apps.chat_data_app.handlers.write_workspace_json",
        fake_write_workspace_json,
    )

    paths, context_file = _prepare_data_analyze_workspace_sync("conv_ws", {"a": 1})
    assert paths["root"] == "/tmp/conv_ws"
    assert calls["write"] == ("/tmp/conv_ws", "workspace/data_context.json", {"a": 1})
    assert context_file == "/tmp/conv_ws/workspace/data_context.json"

    async_paths, async_file = asyncio.run(prepare_data_analyze_workspace("conv_ws", {"a": 1}))
    assert async_paths == paths
    assert async_file == context_file


def test_get_interrupt_info_skips_invalid_interrupt_values():
    state = SimpleNamespace(
        tasks=[
            SimpleNamespace(interrupts=[SimpleNamespace()]),
            SimpleNamespace(interrupts=[SimpleNamespace(value="bad")]),
            SimpleNamespace(interrupts=[SimpleNamespace(value={"message": "无工具"})]),
        ],
        values={"messages": [SimpleNamespace()]},
    )

    info = _get_interrupt_info(state)

    assert info["message"] == "无工具"
    assert info["tool_name"] == "unknown_tool"
    assert info["tool_call_id"] is None


def test_get_semantic_config_async_branches(monkeypatch):
    import asyncio

    assert asyncio.run(_get_semantic_config_async(None, "cfg")) is None
    assert asyncio.run(_get_semantic_config_async("ds", None)) is None

    class FakeManager:
        def __init__(self, mode="ok"):
            self.mode = mode
            self.runtime_called = False

        async def get_datasource_async(self, datasource_id):
            if self.mode == "missing":
                return None
            if self.mode == "raise":
                raise RuntimeError("db failed")
            return {
                "source_type": "hive",
                "semantic_configs": [
                    {"id": "disabled", "enabled": False, "relationships": [{"left_table": "a"}]},
                    {"id": "cfg", "enabled": True, "relationships": [{"left_table": "orders"}]},
                ],
            }

        async def get_semantic_config_async(self, datasource_id, config_id):
            datasource = await self.get_datasource_async(datasource_id)
            for config in datasource.get("semantic_configs") or []:
                if config.get("id") == config_id:
                    return dict(config)
            return None

        async def get_runtime_semantic_config_async(self, datasource_id, config_id):
            self.runtime_called = True
            config = await self.get_semantic_config_async(datasource_id, config_id)
            if config and config_id == "cfg":
                config["dimensions"] = [{"name": "订单状态", "metadata_supplement": "枚举值：1=有效"}]
            return config

    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: FakeManager("missing"))
    assert asyncio.run(_get_semantic_config_async("ds", "cfg")) is None

    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: FakeManager("raise"))
    assert asyncio.run(_get_semantic_config_async("ds", "cfg")) is None

    manager = FakeManager("ok")
    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: manager)
    assert asyncio.run(_get_semantic_config_async("ds", "disabled")) is None
    assert asyncio.run(_get_semantic_config_async("ds", "missing")) is None
    config = asyncio.run(_get_semantic_config_async("ds", "cfg"))
    assert manager.runtime_called is True
    assert config["source_type"] == "hive"
    assert config["joins"] == [{"left_table": "orders"}]
    assert config["dimensions"][0]["metadata_supplement"] == "枚举值：1=有效"


def test_agent_sql_member_result_helpers():
    success = agent_sql_tools._build_member_query_result(
        {"success": True, "columns": ["id"], "data": [{"id": 1}], "total_count": 1, "execution_time_ms": 12},
        "select 1",
        "m1",
        "杭州库",
    )
    failed = agent_sql_tools._build_member_query_result(
        {"success": False, "columns": [], "data": [], "total_count": 0, "error": "表不存在"},
        "select * from missing",
        "m2",
        "宁波库",
    )
    merged = agent_sql_tools._build_multi_member_query_result("select 1", [failed, success])

    assert success["member_name"] == "杭州库"
    assert failed["error"] == "表不存在"
    assert merged["success"] is True
    assert merged["member_datasource_id"] == "m1"
    assert merged["results"] == [failed, success]


def test_agent_sql_normalize_member_datasource_ids_dedupes_and_strips():
    assert agent_sql_tools._normalize_member_datasource_ids([" m1 ", "", None, "m1", "m2"]) == ["m1", "m2"]


class _FakeMessageChunk:
    def __init__(
        self,
        *,
        content="",
        type_="ai",
        tool_calls=None,
        tool_call_chunks=None,
        tool_call_id="",
        name="",
    ):
        self.content = content
        self.type = type_
        self.tool_calls = tool_calls or []
        self.tool_call_chunks = tool_call_chunks or []
        self.tool_call_id = tool_call_id
        self.name = name

    def model_dump(self):
        payload = {"type": self.type, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        if self.tool_call_chunks:
            payload["tool_call_chunks"] = self.tool_call_chunks
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.name:
            payload["name"] = self.name
        return payload


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


def test_stream_sql_generation_enters_agent_when_semantic_config_not_selected(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="5+5=10"), {"langgraph_node": "agent"})],
        state=SimpleNamespace(tasks=[], values={}),
    )
    called = {"tables": False}

    async def fail_get_all_tables(*_args, **_kwargs):
        called["tables"] = True
        raise AssertionError("query 自动指标集模式不应预加载表结构")

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers.get_all_tables_async", fail_get_all_tables)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_auto_semantic_tool",
        user_query="5+5等于多少",
        datasource_id="ds_a",
        mode="query",
        semantic_config_id=None,
    )))

    joined = "".join(events)
    assert "5+5=10" in joined
    assert fake_graph.initial_state["semantic_config_id"] is None
    assert fake_graph.initial_state["tables_ddl_info"] == ""
    assert called["tables"] is False


def test_stream_sql_generation_emits_semantic_selection_event_from_tool(monkeypatch):
    import asyncio

    payload = {
        "selected": True,
        "selected_config_id": "cfg_pay",
        "selected_config_name": "支付汇总",
        "coverage": "full",
        "confidence": "high",
        "reason": "命中交易金额",
        "matched_metrics": ["交易金额"],
        "matched_dimensions": ["机构"],
    }
    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "select_semantic_config_tool"}]), {"langgraph_node": "agent"}),
            (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "call_select", "name": "select_semantic_config_tool", "args": "{\"query\":\"查交易金额\"}"}]), {"langgraph_node": "agent"}),
            (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="已查询支付数据"), {"langgraph_node": "agent"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_select_tool_event",
        user_query="按机构查询交易金额",
        datasource_id="ds_a",
        mode="query",
    )))

    joined = "".join(events)
    assert "event: semantic_config_selected" in joined
    assert '"semantic_config_id": "cfg_pay"' in joined
    assert '"tool_name": "select_semantic_config_tool"' in joined
    assert "已查询支付数据" in joined


def test_stream_sql_generation_keeps_user_selected_semantic_config_selection(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="商品数量为 10"), {"langgraph_node": "agent"})],
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

    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_test", "name": "测试指标集"}))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_user_selected_semantic",
        user_query="商品数量有多少",
        datasource_id="ds_a",
        mode="query",
        semantic_config_id="cfg_test",
        semantic_config_selection=selection,
    )))

    joined = "".join(events)
    assert "商品数量为 10" in joined
    assert fake_graph.initial_state["semantic_config_id"] == "cfg_test"
    human_message = fake_graph.initial_state["messages"][0]
    selection = human_message.additional_kwargs["semantic_config_selection"]
    assert selection["semantic_config_id"] == "cfg_test"
    assert selection["semantic_config_name"] == "测试指标集"
    assert selection["matched_metrics"] == ["商品数量"]
    assert selection["user_selected"] is True
    assert selection["auto_selected"] is False


def test_stream_sql_generation_emits_select_required_event_from_tool(monkeypatch):
    import asyncio

    payload = {
        "selected": False,
        "coverage": "full",
        "confidence": "none",
        "ambiguous": True,
        "reason": "多个指标集都能回答",
        "candidates": [
            {"id": "cfg_order", "name": "订单分析", "coverage": "full", "confidence": "high", "reason": "覆盖订单金额"},
            {"id": "cfg_pay", "name": "支付汇总", "coverage": "full", "confidence": "high", "reason": "覆盖支付金额"},
        ],
        "message": "存在多个可用指标集或需要跨指标集，请让用户从 candidates 中选择一个指标集后再查询",
    }
    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "select_semantic_config_tool"}]), {"langgraph_node": "agent"}),
            (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="请选择指标集"), {"langgraph_node": "agent"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_select_required_tool_event",
        user_query="查询金额",
        datasource_id="ds_a",
        mode="query",
        semantic_config_id=None,
    )))

    joined = "".join(events)
    assert "event: semantic_config_select_required" in joined
    assert '"query": "查询金额"' in joined
    assert "订单分析" in joined
    assert "支付汇总" in joined


def test_stream_sql_generation_uses_selected_config_without_precheck(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已进入当前指标集链路"), {"langgraph_node": "agent"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_order", "name": "订单分析"}))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_selected_semantic_no_precheck",
        user_query="查询明细",
        datasource_id="ds_a",
        mode="query",
        semantic_config_id="cfg_order",
    )))

    joined = "".join(events)
    assert "event: semantic_config_selected" not in joined
    assert "已进入当前指标集链路" in joined
    assert fake_graph.initial_state["semantic_config_id"] == "cfg_order"
    assert "semantic_config_selection" not in fake_graph.initial_state["messages"][0].additional_kwargs


def test_stream_sql_generation_keeps_supported_selected_config(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已查询订单数据"), {"langgraph_node": "agent"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_order", "name": "订单分析"}))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_keep_semantic",
        user_query="查询订单金额",
        datasource_id="ds_a",
        mode="query",
        semantic_config_id="cfg_order",
    )))

    joined = "".join(events)
    assert "event: semantic_config_selected" not in joined
    assert "已查询订单数据" in joined
    assert fake_graph.initial_state["semantic_config_id"] == "cfg_order"
    assert "semantic_config_selection" not in fake_graph.initial_state["messages"][0].additional_kwargs


def test_stream_sql_generation_runs_after_user_selects_ambiguous_candidate(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="商品数量为 10"), {"langgraph_node": "agent"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_order", "name": "订单分析"}))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_selected_ambiguous_semantic",
        user_query="商品数量是多少",
        datasource_id="ds_a",
        mode="query",
        semantic_config_id="cfg_order",
    )))

    joined = "".join(events)
    assert "event: semantic_config_select_required" not in joined
    assert "商品数量为 10" in joined
    assert fake_graph.initial_state["semantic_config_id"] == "cfg_order"


def test_stream_sql_generation_does_not_preblock_selected_config(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="原始链路返回当前指标集不支持"), {"langgraph_node": "agent"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_order", "name": "订单分析"}))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_no_preblock_semantic",
        user_query="查询退款金额",
        datasource_id="ds_a",
        mode="query",
        semantic_config_id="cfg_order",
    )))

    joined = "".join(events)
    assert "当前选择的指标集不支持该问题：缺少退款金额指标" not in joined
    assert "原始链路返回当前指标集不支持" in joined
    assert fake_graph.initial_state["semantic_config_id"] == "cfg_order"


def test_stream_sql_generation_blocks_ambiguous_semantic_configs(monkeypatch):
    import asyncio

    payload = {
        "selected": False,
        "coverage": "full",
        "confidence": "none",
        "ambiguous": True,
        "reason": "多个指标集都能回答",
        "candidates": [
            {"id": "cfg_order", "name": "订单分析", "coverage": "full", "confidence": "high", "reason": "覆盖订单金额"},
            {"id": "cfg_pay", "name": "支付汇总", "coverage": "full", "confidence": "high", "reason": "覆盖支付金额"},
        ],
        "message": "存在多个可用指标集或需要跨指标集，请让用户从 candidates 中选择一个指标集后再查询",
    }
    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "select_semantic_config_tool"}]), {"langgraph_node": "agent"}),
            (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_ambiguous_semantic",
        user_query="查询金额",
        datasource_id="ds_a",
        mode="query",
        semantic_config_id=None,
    )))

    joined = "".join(events)
    assert "event: semantic_config_select_required" in joined
    assert '"query": "查询金额"' in joined
    assert "订单分析" in joined
    assert "支付汇总" in joined
    assert fake_graph.initial_state["semantic_config_id"] is None


def test_stream_sql_generation_blocks_cross_config_required(monkeypatch):
    import asyncio

    payload = {
        "selected": False,
        "coverage": "partial",
        "confidence": "none",
        "cross_config_required": True,
        "reason": "订单金额和商品库存分属不同指标集",
        "candidates": [
            {"id": "cfg_order", "name": "订单分析", "coverage": "partial", "confidence": "medium", "reason": "覆盖订单金额"},
            {"id": "cfg_product", "name": "商品分析", "coverage": "partial", "confidence": "medium", "reason": "覆盖商品库存"},
        ],
        "message": "存在多个可用指标集或需要跨指标集，请让用户从 candidates 中选择一个指标集后再查询",
    }
    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "select_semantic_config_tool"}]), {"langgraph_node": "agent"}),
            (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_cross_semantic",
        user_query="查询订单金额和商品库存",
        datasource_id="ds_a",
        mode="query",
        semantic_config_id=None,
    )))

    joined = "".join(events)
    assert "event: semantic_config_select_required" in joined
    assert '"query": "查询订单金额和商品库存"' in joined
    assert "订单金额和商品库存分属不同指标集" in joined
    assert fake_graph.initial_state["semantic_config_id"] is None


def test_stream_sql_generation_emits_tool_message_usage_and_done(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_1", "name": "execute_sql_tool"}]), {"langgraph_node": "agent"}),
            (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "call_1", "name": "execute_sql_tool", "args": "{\"sql\":\"select 1\"}"}]), {"langgraph_node": "agent"}),
            (_FakeMessageChunk(content="执行完成", type_="tool", tool_call_id="call_1"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="已查询订单数据"), {"langgraph_node": "agent"}),
            (_FakeMessageChunk(content="内部工具模型输出"), {"langgraph_node": "tools"}),
        ],
        state=SimpleNamespace(tasks=[], values={"token_usage": {"total_tokens": 12}}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers.get_all_tables_async", lambda _datasource_id: asyncio.sleep(0, result=[{"table_name": "orders", "columns": []}]))
    monkeypatch.setattr("apps.chat_data_app.handlers.llm_select_tables", lambda _query, _tables: asyncio.sleep(0, result=["orders"]))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_tables_create_format", lambda _tables, _names: "CREATE TABLE orders (id int)")
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_a",
        user_query="查订单数",
        datasource_id="ds_a",
        user_id="user_a",
        username="测试用户",
        mode="generate",
    )))

    joined = "".join(events)
    assert '"status": "running"' in joined
    assert '"accumulated_args": {"sql": "select 1"}' in joined
    assert "已查询订单数据" in joined
    assert "内部工具模型输出" not in joined
    assert 'event: message_end\ndata: {"usage": {"total_tokens": 12}}' in joined
    assert events[-1] == "event: done\ndata: [DONE]\n\n"
    assert fake_graph.initial_state["datasource_id"] == "ds_a"
    assert fake_graph.initial_state["operator_name"] == "测试用户"


def test_stream_sql_generation_emits_pending_interrupt(monkeypatch):
    import asyncio

    interrupt_state = SimpleNamespace(
        tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={"args": {"sql": "select 1"}, "message": "确认执行"})])],
        values={
            "token_usage": {"total_tokens": 8},
            "messages": [SimpleNamespace(tool_calls=[{"id": "call_pending", "name": "execute_sql_tool"}])],
        },
    )
    fake_graph = _FakeGraph(events=[], state=interrupt_state)

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers.get_all_tables_async", lambda _datasource_id: asyncio.sleep(0, result=[{"table_name": "orders", "columns": []}]))
    monkeypatch.setattr("apps.chat_data_app.handlers.llm_select_tables", lambda _query, _tables: asyncio.sleep(0, result=["orders"]))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_tables_create_format", lambda _tables, _names: "CREATE TABLE orders (id int)")
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_interrupt",
        user_query="查订单数",
        datasource_id="ds_a",
        mode="generate",
    )))

    joined = "".join(events)
    assert '"status": "pending"' in joined
    assert '"tool_call_id": "call_pending"' in joined
    assert '"message": "确认执行"' in joined
    assert 'event: message_end\ndata: {"usage": {"total_tokens": 8}}' in joined


def test_stream_sql_generation_emits_error_when_graph_fails(monkeypatch):
    import asyncio

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: _FakeGraph(raise_on_stream=True))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_all_tables_async", lambda _datasource_id: asyncio.sleep(0, result=[{"table_name": "orders", "columns": []}]))
    monkeypatch.setattr("apps.chat_data_app.handlers.llm_select_tables", lambda _query, _tables: asyncio.sleep(0, result=["orders"]))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_tables_create_format", lambda _tables, _names: "CREATE TABLE orders (id int)")
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_error",
        user_query="查订单数",
        datasource_id="ds_a",
        mode="generate",
    )))

    joined = "".join(events)
    assert 'event: error\ndata: {"error": "graph failed"}' in joined
    assert events[-1] == "event: done\ndata: [DONE]\n\n"


def test_stream_sql_generation_semantic_config_missing_and_temp_mode(monkeypatch):
    import asyncio

    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=None))
    missing_events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_missing_semantic",
        user_query="查订单",
        datasource_id="ds_a",
        mode="query",
        semantic_config_id="cfg_missing",
    )))
    assert "query 模式未找到可用语义配置" in "".join(missing_events)

    fake_graph = _FakeGraph(events=[(_FakeMessageChunk(content="语义查询完成"), {"langgraph_node": "agent"})])
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result="对象"))
    temp_events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_temp",
        user_query="查订单",
        datasource_id="ds_a",
        mode="query",
        semantic_config_id="cfg_temp",
        temp_semantic_config={"id": "cfg_temp"},
    )))
    joined = "".join(temp_events)
    assert "语义查询完成" in joined
    assert fake_graph.initial_state["tables_ddl_info"] == ""
    assert fake_graph.initial_state["execution_target_info"] == "对象"


def test_stream_sql_generation_handles_disconnect_and_cached_tables(monkeypatch):
    import asyncio

    class DisconnectRequest:
        async def is_disconnected(self):
            return True

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="不应返回"), {"langgraph_node": "agent"})],
        state=SimpleNamespace(tasks=[], values={"loaded_table_names": ["orders"]}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers.get_all_tables_async", lambda _datasource_id: asyncio.sleep(0, result=[{"table_name": "orders", "columns": []}]))
    monkeypatch.setattr("apps.chat_data_app.handlers.llm_select_tables", lambda _query, _tables: asyncio.sleep(0, result=["orders"]))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_tables_create_format", lambda *_args: "should not build")
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_disconnect",
        user_query="查订单",
        datasource_id="ds_a",
        request=DisconnectRequest(),
    )))

    assert events == []
    assert fake_graph.initial_state["loaded_table_names"] == []


def test_stream_sql_generation_handles_state_lookup_failure_and_chunk_without_content(monkeypatch):
    import asyncio

    class StateFailGraph(_FakeGraph):
        async def aget_state(self, config):
            raise RuntimeError("state missing")

    fake_graph = StateFailGraph(events=[
        (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "call_1", "name": "execute_sql_tool", "args": ""}]), {"langgraph_node": "agent"}),
        (_FakeMessageChunk(content="完成"), {"langgraph_node": "agent"}),
    ])
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers.get_all_tables_async", lambda _datasource_id: asyncio.sleep(0, result=[{"table_name": "orders", "columns": []}]))
    monkeypatch.setattr("apps.chat_data_app.handlers.llm_select_tables", lambda _query, _tables: asyncio.sleep(0, result=["orders"]))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_tables_create_format", lambda *_args: "DDL")
    monkeypatch.setattr("apps.chat_data_app.handlers._build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=""))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_state_fail",
        user_query="查订单",
        datasource_id="ds_a",
    )))

    joined = "".join(events)
    assert "完成" in joined
    assert "event: done" in joined


def test_continue_execution_streams_resume_events(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_1", "name": "execute_sql_tool"}]), {"langgraph_node": "agent"}),
            (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "call_1", "name": "execute_sql_tool", "args": "{\"sql\":\"select 2\"}"}]), {"langgraph_node": "agent"}),
            (_FakeMessageChunk(content="执行完成", type_="tool", tool_call_id="call_1"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="继续完成"), {"langgraph_node": "agent"}),
        ],
        state=SimpleNamespace(tasks=[], values={"token_usage": {"total_tokens": 10}}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_continue_execution(
        thread_id="conv_resume",
        modified_sql="select 2",
        action="approve",
        semantic_config={"id": "cfg_a"},
    )))

    joined = "".join(events)
    assert '"status": "running"' in joined
    assert '"accumulated_args": {"sql": "select 2"}' in joined
    assert "继续完成" in joined
    assert events[-1] == "event: done\ndata: [DONE]\n\n"


def test_continue_execution_pending_interrupt_error_and_disconnect(monkeypatch):
    import asyncio

    interrupt_state = SimpleNamespace(
        tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={"args": {"sql": "select 3"}, "message": "继续确认"})])],
        values={"token_usage": {"total_tokens": 7}, "messages": [SimpleNamespace(tool_calls=[{"id": "call_pending", "name": "execute_sql_tool"}])]},
    )
    pending_graph = _FakeGraph(events=[], state=interrupt_state)
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: pending_graph)
    pending_events = asyncio.run(_collect_async_iter(_continue_execution("conv_resume_pending", modified_sql=None, action="approve")))
    assert '"status": "pending"' in "".join(pending_events)
    assert 'event: message_end\ndata: {"usage": {"total_tokens": 7}}' in "".join(pending_events)

    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: _FakeGraph(raise_on_stream=True))
    error_events = asyncio.run(_collect_async_iter(_continue_execution("conv_resume_error")))
    assert 'event: error\ndata: {"error": "graph failed"}' in "".join(error_events)

    class DisconnectRequest:
        async def is_disconnected(self):
            return True

    disconnect_graph = _FakeGraph(events=[(_FakeMessageChunk(content="不应返回"), {"langgraph_node": "agent"})])
    monkeypatch.setattr("apps.chat_data_app.handlers.get_chat_data_graph", lambda **_kwargs: disconnect_graph)
    disconnect_events = asyncio.run(_collect_async_iter(_continue_execution("conv_resume_disconnect", request=DisconnectRequest())))
    assert disconnect_events == []


def test_sql_completion_and_resume_handlers_wrap_streams(monkeypatch):
    import asyncio

    captured = {}

    async def fake_get_or_create(*args, **kwargs):
        captured["conversation"] = args
        return {}

    async def fake_stream(**kwargs):
        captured["stream_kwargs"] = kwargs
        yield _format_sse("done", "[DONE]")

    async def fake_continue(**kwargs):
        captured["continue_kwargs"] = kwargs
        yield _format_sse("done", "[DONE]")

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", fake_get_or_create)
    monkeypatch.setattr("apps.chat_data_app.handlers._stream_sql_generation", fake_stream)
    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg"}))
    monkeypatch.setattr("apps.chat_data_app.handlers._continue_execution", fake_continue)

    response = asyncio.run(sql_completion_handler(
        conversation_id="conv_sql",
        query="查订单",
        options={"datasource_id": "ds_a", "mode": "query", "semantic_config_id": "cfg", "model_id": "m1", "member_datasource_ids": ["m1"]},
        user_id="u1",
        username="测试用户",
    ))
    assert response.media_type == "text/event-stream"
    assert captured["conversation"] == ("", "chat_data", "conv_sql", "u1")
    chunks = asyncio.run(_collect_async_iter(response.body_iterator))
    assert chunks == ["event: done\ndata: [DONE]\n\n"]
    assert captured["stream_kwargs"]["username"] == "测试用户"
    assert captured["stream_kwargs"]["member_datasource_ids"] == ["m1"]

    resume = asyncio.run(sql_resume_handler("conv_sql", action="edit", data={"sql": "select 2", "datasource_id": "ds_a", "semantic_config_id": "cfg"}))
    chunks = asyncio.run(_collect_async_iter(resume.body_iterator))
    assert chunks == ["event: done\ndata: [DONE]\n\n"]
    assert captured["continue_kwargs"]["action"] == "approve"
    assert captured["continue_kwargs"]["modified_sql"] == "select 2"
    assert captured["continue_kwargs"]["semantic_config"] == {"id": "cfg"}


def test_data_analyze_completion_handler_streams_stage_tool_usage_and_done(monkeypatch):
    import asyncio

    class FakeDataAnalyzeGraph:
        async def astream(self, initial_state, config=None, stream_mode=None, subgraphs=False):
            assert initial_state["task_run_id"]
            assert config["configurable"]["thread_id"] == "conv_da"
            yield ("updates", {"thinking": {"task_stages": [{"stage": "thinking", "status": "completed", "content": "理解完成"}]}})
            yield ("messages", (_FakeMessageChunk(content="规划中"), {"langgraph_node": "plan"}))
            yield ("messages", (_FakeMessageChunk(content="", tool_calls=[{"id": "call_1", "name": "execute_sql_tool", "args": {"sql": "select 1"}}]), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "call_1", "name": "execute_sql_tool", "args": '{"sql":"select 1"}'}]), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (_FakeMessageChunk(content='{"success": true}', type_="tool", tool_call_id="call_1"), {"langgraph_node": "tools"}))
            yield ("custom", {"type": "token_usage", "usage": {"total_tokens": 9}})
            yield ("messages", (_FakeMessageChunk(content="分析完成"), {"langgraph_node": "data_analyze_agent"}))

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_data_analyze_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"datasource": {"source_type": "mysql"}}))
    monkeypatch.setattr("apps.chat_data_app.handlers.prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))
    monkeypatch.setattr("apps.chat_data_app.agent.data_analyze_graph.build_data_analyze_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr("apps.chat_data_app.agent.data_analyze_graph.create_data_analyze_flow_graph", lambda **_kwargs: FakeDataAnalyzeGraph())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())

    response = asyncio.run(data_analyze_completion_handler(
        conversation_id="conv_da",
        query="分析销售额",
        options={"datasource_id": "ds_a", "semantic_config_id": "cfg_a", "member_datasource_ids": ["m1"]},
        user_id="u1",
        username="测试用户",
    ))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    joined = "".join(item.decode("utf-8") if isinstance(item, bytes) else item for item in events)

    assert "event: task_stage" in joined
    assert "理解完成" in joined
    assert "event: task_stage_delta" in joined
    assert '"tool_name": "execute_sql_tool"' in joined
    assert '"accumulated_args": {"sql": "select 1"}' in joined
    assert "分析完成" in joined
    assert 'event: message_end\ndata: {"usage": {"total_tokens": 9}}' in joined
    assert joined.endswith("event: done\ndata: [DONE]\n\n")


def test_data_analyze_completion_handler_auto_selects_semantic_config(monkeypatch):
    import asyncio

    captured = {}
    payload = {
        "selected": True,
        "selected_config_id": "cfg_sales",
        "selected_config_name": "销售分析",
        "coverage": "full",
        "confidence": "high",
        "reason": "命中销售额",
        "matched_metrics": ["销售额"],
        "matched_dimensions": ["日期"],
    }

    class FakeDataAnalyzeGraph:
        async def astream(self, initial_state, config=None, stream_mode=None, subgraphs=False):
            captured["initial_state"] = initial_state
            yield ("messages", (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}))
            yield ("messages", (_FakeMessageChunk(content="分析完成"), {"langgraph_node": "data_analyze_agent"}))

    async def fake_build_context(datasource_id, semantic_config_id):
        captured["context_args"] = (datasource_id, semantic_config_id)
        return {"datasource": {"source_type": "mysql"}}

    def fake_create_graph(**kwargs):
        captured["graph_kwargs"] = kwargs
        return FakeDataAnalyzeGraph()

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_data_analyze_context", fake_build_context)
    monkeypatch.setattr("apps.chat_data_app.handlers.prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))
    monkeypatch.setattr("apps.chat_data_app.agent.data_analyze_graph.build_data_analyze_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr("apps.chat_data_app.agent.data_analyze_graph.create_data_analyze_flow_graph", fake_create_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())

    response = asyncio.run(data_analyze_completion_handler(
        conversation_id="conv_da_auto_semantic",
        query="分析销售额趋势",
        options={"datasource_id": "ds_a"},
        user_id="u1",
    ))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    joined = "".join(item.decode("utf-8") if isinstance(item, bytes) else item for item in events)

    assert "event: semantic_config_selected" in joined
    assert '"semantic_config_id": "cfg_sales"' in joined
    assert "分析完成" in joined
    assert captured["context_args"] == ("ds_a", None)
    assert captured["graph_kwargs"]["semantic_config_id"] is None
    assert captured["graph_kwargs"]["datasource_id"] == "ds_a"
    assert captured["initial_state"]["datasource_id"] == "ds_a"
    assert captured["initial_state"]["semantic_config_id"] is None
    assert captured["initial_state"]["member_datasource_ids"] == []
    assert captured["initial_state"]["user_id"] == "u1"
    assert captured["initial_state"]["operator_name"] == ""
    assert captured["initial_state"]["conversation_id"] == "conv_da_auto_semantic"


def test_data_analyze_completion_handler_requires_semantic_config_selection(monkeypatch):
    import asyncio

    payload = {
        "selected": False,
        "coverage": "full",
        "confidence": "none",
        "ambiguous": True,
        "reason": "多个指标集都能回答",
        "candidates": [
            {"id": "cfg_order", "name": "订单分析", "coverage": "full", "confidence": "high", "reason": "覆盖销售额"},
            {"id": "cfg_sales", "name": "销售分析", "coverage": "full", "confidence": "high", "reason": "覆盖销售额"},
        ],
        "message": "存在多个可用指标集或需要跨指标集，请让用户从 candidates 中选择一个指标集后再查询",
    }

    async def fake_build_context(*_args, **_kwargs):
        return {"datasource": {"source_type": "mysql"}}

    class FakeDataAnalyzeGraph:
        async def astream(self, initial_state, config=None, stream_mode=None, subgraphs=False):
            yield ("messages", (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}))

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_data_analyze_context", fake_build_context)
    monkeypatch.setattr("apps.chat_data_app.handlers.prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))
    monkeypatch.setattr("apps.chat_data_app.agent.data_analyze_graph.build_data_analyze_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr("apps.chat_data_app.agent.data_analyze_graph.create_data_analyze_flow_graph", lambda **_kwargs: FakeDataAnalyzeGraph())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())

    response = asyncio.run(data_analyze_completion_handler(
        conversation_id="conv_da_require_semantic",
        query="分析销售额",
        options={"datasource_id": "ds_a"},
        user_id="u1",
    ))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    joined = "".join(item.decode("utf-8") if isinstance(item, bytes) else item for item in events)

    assert "event: semantic_config_select_required" in joined
    assert "订单分析" in joined
    assert "销售分析" in joined
    assert '"query": "分析销售额"' in joined
    assert joined.endswith("event: done\ndata: [DONE]\n\n")


def test_data_analyze_completion_handler_covers_subgraph_chunks_and_disconnect(monkeypatch):
    import asyncio

    class FakeDataAnalyzeGraph:
        async def astream(self, initial_state, config=None, stream_mode=None, subgraphs=False):
            yield (("subgraph",), "messages", (_FakeMessageChunk(content="跳过内部"), {"langgraph_node": "__internal__"}))
            yield ("messages", (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "chunk_tool", "name": "execute_sql_tool", "args": '{"sql":', "index": 0}]), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (_FakeMessageChunk(content="", tool_call_chunks=[{"args": '"select 1"}', "index": 0}]), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (_FakeMessageChunk(content="执行失败: 表不存在", type_="tool", tool_call_id="chunk_tool"), {"langgraph_node": "tools"}))
            yield ("messages", (_FakeMessageChunk(content="", type_="tool", tool_call_id="missing_tool"), {"langgraph_node": "tools"}))
            yield ("messages", (_FakeMessageChunk(content="", type_="ai"), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (_FakeMessageChunk(content="完成", type_="ai"), {"langgraph_node": "data_analyze_agent"}))

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_data_analyze_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"datasource": {"source_type": "mysql"}}))
    monkeypatch.setattr("apps.chat_data_app.handlers.prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))
    monkeypatch.setattr("apps.chat_data_app.agent.data_analyze_graph.build_data_analyze_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr("apps.chat_data_app.agent.data_analyze_graph.create_data_analyze_flow_graph", lambda **_kwargs: FakeDataAnalyzeGraph())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())

    response = asyncio.run(data_analyze_completion_handler("conv_da_subgraph", "分析", user_id="u1"))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    joined = "".join(item.decode("utf-8") if isinstance(item, bytes) else item for item in events)

    assert "跳过内部" not in joined
    assert '"status": "running"' in joined
    assert '"status": "error"' in joined
    assert '"accumulated_args": {"sql": "select 1"}' in joined
    assert "完成" in joined

    class DisconnectRequest:
        async def is_disconnected(self):
            return True

    class DisconnectGraph:
        async def astream(self, *args, **kwargs):
            yield ("messages", (_FakeMessageChunk(content="不应返回"), {"langgraph_node": "data_analyze_agent"}))

    monkeypatch.setattr("apps.chat_data_app.agent.data_analyze_graph.create_data_analyze_flow_graph", lambda **_kwargs: DisconnectGraph())
    response = asyncio.run(data_analyze_completion_handler("conv_da_disconnect", "分析", user_id="u1", request=DisconnectRequest()))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    assert events == []


def test_data_analyze_completion_handler_emits_error_stage(monkeypatch):
    import asyncio

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_data_analyze_context", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("context failed")))

    response = asyncio.run(data_analyze_completion_handler("conv_da_error", "分析销售额", user_id="u1"))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    joined = "".join(item.decode("utf-8") if isinstance(item, bytes) else item for item in events)

    assert "任务执行失败：context failed" in joined
    assert 'event: error\ndata: {"error": "context failed"}' in joined
    assert joined.endswith("event: done\ndata: [DONE]\n\n")
