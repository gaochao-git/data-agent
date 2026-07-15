import os
import sys
import json
from types import SimpleNamespace
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.handlers import (
    _append_user_selected_execution_targets_to_context,
    _build_query_skills_context,
    _build_semantic_config_directory_info_async,
    _extract_reasoning_content,
    _format_reasoning_for_display,
    _format_sse,
    _format_thinking_event,
    _format_tool_event,
    _get_execution_targets_async,
    _get_semantic_config_async,
    _is_tool_result_error,
    _match_execution_targets_from_query_only,
    _normalize_enable_thinking,
    _normalize_semantic_selection_payload,
    _append_reasoning_segment,
    _parse_tool_args_buffer,
    _prepare_data_analyze_workspace_sync,
    _stream_sql_generation,
    _finish_running_tool,
    _register_running_tool,
    _resolve_active_tool_call_id,
    data_analyze_completion_handler,
    prepare_data_analyze_workspace,
    sql_completion_handler,
)
from apps.chat_data_app.agent.tools import sql as agent_sql_tools


def _tool_event_contents(events: list[str]) -> list[str]:
    """解析 SSE 工具事件中的原始工具结果。"""
    contents = []
    for event in events:
        if not event.startswith("event: tool\n"):
            continue
        payload = json.loads(event.split("data: ", 1)[1].strip())
        contents.append(str(payload.get("content") or ""))
    return contents


def _patch_data_analyze_workspace_session(monkeypatch):
    session = SimpleNamespace(sandbox_id="sbx_test")
    calls = {"created": [], "attached": [], "cleaned": []}

    async def fake_get_workspace_execution_session(workspace_root, user_id):
        calls["created"].append((workspace_root, user_id))
        return session

    async def fake_attach_workspace_execution_session(attached_session, user_id):
        calls["attached"].append((attached_session, user_id))

    async def fake_cleanup_workspace_execution_session(cleaned_session):
        calls["cleaned"].append(cleaned_session)

    monkeypatch.setattr("apps.chat_data_app.handlers.get_workspace_execution_session", fake_get_workspace_execution_session)
    monkeypatch.setattr("apps.chat_data_app.handlers.attach_workspace_execution_session", fake_attach_workspace_execution_session)
    monkeypatch.setattr("apps.chat_data_app.handlers.cleanup_workspace_execution_session", fake_cleanup_workspace_execution_session)
    return calls


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
    tool_thinking_event = _format_thinking_event(SimpleNamespace(additional_kwargs={"reasoning_content": "调用 select_semantic_config_tool"}))
    assert "选择指标集" in tool_thinking_event
    assert "select_semantic_config_tool" not in tool_thinking_event
    assert _format_reasoning_for_display("调用 semantic_query_tool") == "调用 查询数据"

    segments = []
    _append_reasoning_segment(segments, "", 0)
    assert segments == []
    _append_reasoning_segment(segments, "a", 0)
    _append_reasoning_segment(segments, "b", 0)
    _append_reasoning_segment(segments, "c", 1)
    _append_reasoning_segment(segments, "调用 select_semantic_config_tool", 1)
    assert segments == [
        {"id": "reasoning-0", "content": "ab", "tool_call_count": 0},
        {"id": "reasoning-1", "content": "c调用 选择指标集", "tool_call_count": 1},
    ]


def test_build_query_skills_context_appends_requested_skills(monkeypatch):
    import asyncio

    context = asyncio.run(_build_query_skills_context("u1", ["custom-query-skill", "custom-query-skill", "risk-query-skill"]))

    assert "## 本轮指定业务知识技能" in context
    assert "`custom-query-skill`、`risk-query-skill`" in context
    assert "不要读取未指定技能" in context


def test_build_query_skills_context_empty_without_requested_skills():
    import asyncio

    assert asyncio.run(_build_query_skills_context("u1", [])) == ""


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


def test_is_tool_result_error_detects_plain_and_dashboard_errors():
    assert _is_tool_result_error("执行失败: 表不存在") is True
    assert _is_tool_result_error("error: syntax error") is True
    assert _is_tool_result_error("[dashboard_write] {\"success\": false}") is True
    assert _is_tool_result_error("[dashboard_write] {bad json}") is False
    assert _is_tool_result_error("执行完成") is False


def test_append_user_selected_execution_targets_to_context_formats_single_and_multi_targets():
    targets = [
        {"member_datasource_id": "m1", "member_name": "杭州库", "org_code": "ORG_HZ"},
        {"member_datasource_id": "m2", "member_name": "宁波库", "source_config": {"org_code": "ORG_NB"}},
    ]

    single = _append_user_selected_execution_targets_to_context("", targets, ["m1"])
    multi = _append_user_selected_execution_targets_to_context("", targets, ["m1", "m2"])
    empty = _append_user_selected_execution_targets_to_context("已有说明", targets, [])

    assert "当前用户已选择的执行对象数据源" in single
    assert "member_datasource_id: m1" in single
    assert "当前用户已选择的执行对象数据源" in multi
    assert "宁波库" in multi
    assert "编码：ORG_NB" in multi
    assert empty == "已有说明"


def test_execution_target_context_uses_only_requested_targets(monkeypatch):
    import asyncio

    calls = []

    class FakeDatasourceManager:
        async def get_datasource_async(self, datasource_id):
            return {
                "query_context": "当前数据源组按执行对象数据源分库",
                "source_type": "hive",
                "query_executor_type": "direct",
                "member_sources": [
                    {"member_datasource_id": "m1", "org_name": "杭州库", "org_code": "ORG_HZ"},
                    {"member_datasource_id": "m2", "org_name": "宁波库", "org_code": "ORG_NB"},
                ],
            }

        def _build_execution_datasource_targets(self, datasource, member_datasource_ids=None):
            raise AssertionError("should not expand all datasource group members")

        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            calls.append(list(member_datasource_ids or []))
            member_name_map = {"m1": "杭州库", "m2": "宁波库"}
            return [
                {"member_datasource_id": member_id, "member_name": member_name_map.get(member_id, member_id), "org_code": member_id}
                for member_id in member_datasource_ids or []
            ]

    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: FakeDatasourceManager())

    selected_targets = asyncio.run(_get_execution_targets_async("ds_a", ["m1"]))
    empty_targets = asyncio.run(_get_execution_targets_async("ds_a", None))
    selected_only = _append_user_selected_execution_targets_to_context("", selected_targets, ["m1"])

    assert calls == [["m1"]]
    assert "当前用户已选择的执行对象数据源" in selected_only
    assert "member_datasource_id: m1" in selected_only
    assert "m2" not in selected_only
    assert empty_targets == []


def test_get_execution_targets_async_does_not_expand_all_members(monkeypatch):
    import asyncio

    class FakeDatasourceManager:
        async def get_datasource_async(self, datasource_id):
            raise AssertionError("should not read datasource group members")

        def _build_execution_datasource_targets(self, datasource, member_datasource_ids=None):
            raise AssertionError("should not expand all datasource group members")

        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            return [
                {"member_datasource_id": member_id, "member_name": member_id, "org_code": member_id}
                for member_id in member_datasource_ids or []
            ]

    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: FakeDatasourceManager())

    empty_targets = asyncio.run(_get_execution_targets_async("ds_a", []))
    selected_targets = asyncio.run(_get_execution_targets_async("ds_a", ["m1"]))

    assert empty_targets == []
    assert [target["member_datasource_id"] for target in selected_targets] == ["m1"]


def test_match_execution_targets_prefers_long_exact_member_name():
    targets = [
        {"member_datasource_id": "ORG_CODE1", "member_name": "运维平台", "org_code": "ORG_CODE1"},
        {"member_datasource_id": "ORG_CODE2", "member_name": "运维平台1", "org_code": "ORG_CODE2"},
        {"member_datasource_id": "ORG_CODE3", "member_name": "运维平台2", "org_code": "ORG_CODE3"},
    ]

    matched_member_ids = _match_execution_targets_from_query_only(
        targets,
        "查询运维平台1在2024年的订单数量是多少",
    )

    assert matched_member_ids == ["ORG_CODE2"]


def test_match_execution_targets_matches_all_execution_targets():
    targets = [
        {"member_datasource_id": "ORG_CODE1", "member_name": "运维平台", "org_code": "ORG_CODE1"},
        {"member_datasource_id": "ORG_CODE2", "member_name": "运维平台1", "org_code": "ORG_CODE2"},
    ]

    matched_member_ids = _match_execution_targets_from_query_only(
        targets,
        "查询所有机构2024年的订单数量",
    )

    assert matched_member_ids == ["ORG_CODE1", "ORG_CODE2"]


def test_get_execution_targets_async_empty_and_error(monkeypatch):
    import asyncio

    assert asyncio.run(_get_execution_targets_async(None, None)) == []

    class EmptyManager:
        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            return []

    class ErrorManager:
        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            raise RuntimeError("manager failed")

    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: EmptyManager())
    assert asyncio.run(_get_execution_targets_async("ds_a", None)) == []

    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: ErrorManager())
    assert asyncio.run(_get_execution_targets_async("ds_a", ["m1"])) == []


def test_build_semantic_config_directory_info_async(monkeypatch):
    import asyncio
    captured = {}

    async def fake_directory(datasource_id, user_id=None):
        assert datasource_id == "ds_a"
        captured["user_id"] = user_id
        return [{
            "id": "sc_order",
            "name": "订单分析",
            "description": "订单经营分析",
            "metrics_total": 2,
            "dimensions_total": 4,
        }]

    async def failing_directory(_datasource_id, _user_id=None):
        raise RuntimeError("directory failed")

    monkeypatch.setattr("apps.chat_data_app.handlers.list_runtime_semantic_config_directory", fake_directory)

    prompt = asyncio.run(_build_semantic_config_directory_info_async("ds_a", user_id="u1"))

    assert captured["user_id"] == "u1"
    assert "当前可见指标集目录" in prompt
    assert "名称：订单分析" in prompt
    assert "描述：订单经营分析" in prompt
    assert asyncio.run(_build_semantic_config_directory_info_async("ds_a", enabled=False)) == ""
    assert asyncio.run(_build_semantic_config_directory_info_async(None)) == ""

    monkeypatch.setattr("apps.chat_data_app.handlers.list_runtime_semantic_config_directory", failing_directory)
    assert asyncio.run(_build_semantic_config_directory_info_async("ds_a")) == ""


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
        events=[(_FakeMessageChunk(content="5+5=10"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )
    called = {"tables": False}

    async def fail_get_all_tables(*_args, **_kwargs):
        called["tables"] = True
        raise AssertionError("query 自动指标集模式不应预加载表结构")

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_semantic_config_directory_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result="## 当前可见指标集目录\n1. 名称：订单分析"))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_auto_semantic_tool",
        user_query="5+5等于多少",
        datasource_id="ds_a",
        semantic_config_id=None,
    )))

    joined = "".join(events)
    assert "5+5=10" in joined
    assert fake_graph.initial_state["semantic_config_id"] is None
    assert fake_graph.initial_state["prompt_context"]["semantic_config_directory_info"] == "## 当前可见指标集目录\n1. 名称：订单分析"
    human_message = fake_graph.initial_state["messages"][0]
    assert "skill_names" not in human_message.additional_kwargs
    assert called["tables"] is False


def test_stream_sql_generation_streams_semantic_retrieval_tool_result(monkeypatch):
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
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "select_semantic_config_tool"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "call_select", "name": "select_semantic_config_tool", "args": "{\"query\":\"查交易金额\"}"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="已查询支付数据"), {"langgraph_node": "model"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_select_tool_event",
        user_query="按机构查询交易金额",
        datasource_id="ds_a",
    )))

    joined = "".join(events)
    assert "event: tool" in joined
    assert "[select_semantic_config_tool]" in _tool_event_contents(events)[-1]
    assert '"semantic_config_ids": ["cfg_pay"]' in _tool_event_contents(events)[-1]
    assert '"expression": "SUM(pay.amount)"' in _tool_event_contents(events)[-1]
    assert '"tool_name": "select_semantic_config_tool"' in joined
    assert "event: semantic_config_selected" not in joined
    assert "event: semantic_config_select_required" not in joined
    assert "已查询支付数据" in joined


def test_stream_sql_generation_keeps_user_selected_semantic_config_selection(monkeypatch):
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

    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_test", "name": "测试指标集"}))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_user_selected_semantic",
        user_query="商品数量有多少",
        datasource_id="ds_a",
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
    assert "skill_names" not in human_message.additional_kwargs


def test_stream_sql_generation_keeps_explicit_query_skill(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_query_skill_names",
        user_query="查询金额",
        datasource_id="ds_a",
        skill_names=["custom-query-skill", "risk-query-skill"],
    )))

    assert "完成" in "".join(events)
    human_message = fake_graph.initial_state["messages"][0]
    assert human_message.additional_kwargs["skill_names"] == ["custom-query-skill", "risk-query-skill"]


def test_stream_sql_generation_streams_multiple_semantic_configs_as_tool_result(monkeypatch):
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
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "select_semantic_config_tool"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="请选择指标集"), {"langgraph_node": "model"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_multiple_semantic_tool_event",
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


def test_stream_sql_generation_streams_retrieval_without_query_plan(monkeypatch):
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
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "select_semantic_config_tool"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="已按多个指标集完成查询"), {"langgraph_node": "model"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
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


def test_stream_sql_generation_streams_retrieval_without_cross_config_decision(monkeypatch):
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
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "select_semantic_config_tool"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="这个问题需要跨指标集合并，当前不支持。"), {"langgraph_node": "model"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
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


def test_stream_sql_generation_uses_selected_config_without_precheck(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已进入当前指标集链路"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_order", "name": "订单分析"}))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_selected_semantic_no_precheck",
        user_query="查询明细",
        datasource_id="ds_a",
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
        events=[(_FakeMessageChunk(content="已查询订单数据"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_order", "name": "订单分析"}))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_keep_semantic",
        user_query="查询订单金额",
        datasource_id="ds_a",
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
        events=[(_FakeMessageChunk(content="商品数量为 10"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_order", "name": "订单分析"}))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_selected_ambiguous_semantic",
        user_query="商品数量是多少",
        datasource_id="ds_a",
        semantic_config_id="cfg_order",
    )))

    joined = "".join(events)
    assert "event: semantic_config_select_required" not in joined
    assert "商品数量为 10" in joined
    assert fake_graph.initial_state["semantic_config_id"] == "cfg_order"


def test_stream_sql_generation_does_not_preblock_selected_config(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="原始链路返回当前指标集不支持"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_order", "name": "订单分析"}))
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_no_preblock_semantic",
        user_query="查询退款金额",
        datasource_id="ds_a",
        semantic_config_id="cfg_order",
    )))

    joined = "".join(events)
    assert "当前选择的指标集不支持该问题：缺少退款金额指标" not in joined
    assert "原始链路返回当前指标集不支持" in joined
    assert fake_graph.initial_state["semantic_config_id"] == "cfg_order"


def test_stream_sql_generation_does_not_block_multiple_semantic_configs(monkeypatch):
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
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "select_semantic_config_tool"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
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
    assert fake_graph.initial_state["semantic_config_id"] is None


def test_stream_sql_generation_does_not_block_cross_config_retrieval(monkeypatch):
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
            (_FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "select_semantic_config_tool"}]), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
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
    assert fake_graph.initial_state["semantic_config_id"] is None


def test_stream_sql_generation_emits_tool_message_usage_and_done(monkeypatch):
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
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
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


def test_stream_sql_generation_query_mode_uses_only_request_visible_members(monkeypatch):
    import asyncio

    class FakeDatasourceManager:
        async def get_datasource_async(self, datasource_id):
            return {
                "query_context": "当前数据源组按执行对象数据源分库",
                "source_type": "hive",
                "query_executor_type": "direct",
                "member_sources": [
                    {"member_datasource_id": "m1", "org_name": "杭州库", "org_code": "ORG_HZ"},
                    {"member_datasource_id": "m2", "org_name": "宁波库", "org_code": "ORG_NB"},
                ],
            }

        def _build_execution_datasource_targets(self, datasource, member_datasource_ids=None):
            return [
                {"member_datasource_id": member_id, "member_name": member_id, "org_code": member_id}
                for member_id in member_datasource_ids or []
            ]

        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            target_map = {
                "m1": {"member_datasource_id": "m1", "member_name": "杭州库", "org_code": "ORG_HZ"},
                "m2": {"member_datasource_id": "m2", "member_name": "宁波库", "org_code": "ORG_NB"},
            }
            return [
                target_map[member_id]
                for member_id in member_datasource_ids or []
                if member_id in target_map
            ]

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: FakeDatasourceManager())
    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_a", "metrics": [], "dimensions": []}))

    asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_query_members",
        user_query="查询所有机构订单数",
        datasource_id="ds_a",
        user_id="user_a",
        member_datasource_ids=["m1"],
        semantic_config_id="cfg_a",
    )))

    assert fake_graph.initial_state["execution_context"]["available_member_datasource_ids"] == ["m1"]
    assert fake_graph.initial_state["execution_context"]["matched_member_datasource_ids"] == ["m1"]
    datasource_group_context = fake_graph.initial_state["prompt_context"]["datasource_group_context"]
    assert "当前用户已选择的执行对象数据源" in datasource_group_context
    assert "member_datasource_id: m1" in datasource_group_context
    assert "m2" not in datasource_group_context


def test_stream_sql_generation_query_mode_does_not_expand_empty_member_scope(monkeypatch):
    import asyncio

    class FakeDatasourceManager:
        async def get_datasource_async(self, datasource_id):
            return {
                "query_context": "当前数据源组按执行对象数据源分库",
                "source_type": "hive",
                "query_executor_type": "direct",
                "member_sources": [
                    {"member_datasource_id": "m1", "org_name": "杭州库", "org_code": "ORG_HZ"},
                    {"member_datasource_id": "m2", "org_name": "宁波库", "org_code": "ORG_NB"},
                ],
            }

        def _build_execution_datasource_targets(self, datasource, member_datasource_ids=None):
            raise AssertionError("should not expand all datasource group members")

        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            return [
                {"member_datasource_id": "m1", "member_name": "杭州库", "org_code": "ORG_HZ"},
                {"member_datasource_id": "m2", "member_name": "宁波库", "org_code": "ORG_NB"},
            ][:len(member_datasource_ids or [])]

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: FakeDatasourceManager())
    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_a", "metrics": [], "dimensions": []}))

    asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_query_members",
        user_query="查询所有机构订单数",
        datasource_id="ds_a",
        user_id="user_a",
        member_datasource_ids=[],
        semantic_config_id="cfg_a",
    )))

    assert fake_graph.initial_state["execution_context"]["available_member_datasource_ids"] == []
    assert fake_graph.initial_state["execution_context"]["matched_member_datasource_ids"] == []
    assert fake_graph.initial_state["prompt_context"]["datasource_group_context"] == "当前数据源组按执行对象数据源分库"


def test_stream_sql_generation_query_mode_keeps_selected_member_scope(monkeypatch):
    import asyncio

    class FakeDatasourceManager:
        async def get_datasource_async(self, datasource_id):
            return {
                "query_context": "",
                "source_type": "hive",
                "query_executor_type": "direct",
                "member_sources": [
                    {"member_datasource_id": "m1", "org_name": "杭州库", "org_code": "ORG_HZ"},
                    {"member_datasource_id": "m2", "org_name": "宁波库", "org_code": "ORG_NB"},
                ],
            }

        def _build_execution_datasource_targets(self, datasource, member_datasource_ids=None):
            return [
                {"member_datasource_id": member_id, "member_name": member_id, "org_code": member_id}
                for member_id in member_datasource_ids or []
            ]

        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            target_map = {
                "m1": {"member_datasource_id": "m1", "member_name": "杭州库", "org_code": "ORG_HZ"},
                "m2": {"member_datasource_id": "m2", "member_name": "宁波库", "org_code": "ORG_NB"},
            }
            return [
                target_map[member_id]
                for member_id in member_datasource_ids or []
                if member_id in target_map
            ]

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers.get_datasource_manager", lambda: FakeDatasourceManager())
    monkeypatch.setattr("apps.chat_data_app.handlers._get_semantic_config_async", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "cfg_a", "metrics": [], "dimensions": []}))

    asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_query_member_selected",
        user_query="查询订单数",
        datasource_id="ds_a",
        user_id="user_a",
        member_datasource_ids=["m1"],
        semantic_config_id="cfg_a",
    )))

    assert fake_graph.initial_state["execution_context"]["available_member_datasource_ids"] == ["m1"]
    datasource_group_context = fake_graph.initial_state["prompt_context"]["datasource_group_context"]
    assert "当前用户已选择的执行对象数据源" in datasource_group_context
    assert "member_datasource_id: m1" in datasource_group_context
    assert "m2" not in datasource_group_context


def test_stream_sql_generation_emits_error_when_graph_fails(monkeypatch):
    import asyncio

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: _FakeGraph(raise_on_stream=True))

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_error",
        user_query="查订单数",
        datasource_id="ds_a",
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
        semantic_config_id="cfg_missing",
    )))
    assert "智能问数未找到可用指标集" in "".join(missing_events)

    fake_graph = _FakeGraph(events=[(_FakeMessageChunk(content="语义查询完成"), {"langgraph_node": "model"})])
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)
    async def fake_get_execution_targets(_datasource_id, member_datasource_ids=None):
        if not member_datasource_ids:
            return []
        return [
            {"member_datasource_id": "m1", "member_name": "对象", "org_code": "ORG_1"},
        ]

    monkeypatch.setattr("apps.chat_data_app.handlers._get_execution_targets_async", fake_get_execution_targets)
    monkeypatch.setattr("apps.chat_data_app.handlers._build_semantic_config_directory_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result="不应注入目录"))
    temp_events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_temp",
        user_query="查订单",
        datasource_id="ds_a",
        semantic_config_id="cfg_temp",
        temp_semantic_config={"id": "cfg_temp"},
    )))
    joined = "".join(temp_events)
    assert "语义查询完成" in joined
    assert fake_graph.initial_state["prompt_context"]["datasource_group_context"] == ""
    assert fake_graph.initial_state["prompt_context"]["semantic_config_directory_info"] == ""


def test_stream_sql_generation_handles_disconnect_and_cached_tables(monkeypatch):
    import asyncio

    class DisconnectRequest:
        async def is_disconnected(self):
            return True

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="不应返回"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
        thread_id="conv_disconnect",
        user_query="查订单",
        datasource_id="ds_a",
        request=DisconnectRequest(),
    )))

    assert events == []
    assert "loaded_table_names" not in fake_graph.initial_state


def test_stream_sql_generation_handles_state_lookup_failure_and_chunk_without_content(monkeypatch):
    import asyncio

    class StateFailGraph(_FakeGraph):
        async def aget_state(self, config):
            raise RuntimeError("state missing")

    fake_graph = StateFailGraph(events=[
        (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "call_1", "name": "semantic_query_tool", "args": ""}]), {"langgraph_node": "model"}),
        (_FakeMessageChunk(content="完成"), {"langgraph_node": "model"}),
    ])
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.handlers.create_chat_data_agent", lambda **_kwargs: fake_graph)

    events = asyncio.run(_collect_async_iter(_stream_sql_generation(
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

    async def fake_stream(**kwargs):
        captured["stream_kwargs"] = kwargs
        yield _format_sse("done", "[DONE]")

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", fake_get_or_create)
    monkeypatch.setattr("apps.chat_data_app.handlers._stream_sql_generation", fake_stream)

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


def test_data_analyze_completion_handler_streams_stage_tool_usage_and_done(monkeypatch):
    import asyncio

    class FakeDataAnalyzeGraph:
        async def astream(self, initial_state, config=None, stream_mode=None):
            assert "task_run_id" not in initial_state
            assert config["configurable"]["thread_id"] == "conv_da"
            assert stream_mode == ["messages", "custom"]
            yield ("messages", (_FakeMessageChunk(content="", tool_calls=[{"id": "call_1", "name": "semantic_query_tool", "args": {"dsl": {"metrics": ["销售额"]}}}]), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "call_1", "name": "semantic_query_tool", "args": '{"dsl":{"metrics":["销售额"]}}'}]), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (_FakeMessageChunk(content='{"success": true}', type_="tool", tool_call_id="call_1"), {"langgraph_node": "tools"}))
            yield ("custom", {"type": "token_usage", "usage": {"total_tokens": 9}})
            yield ("messages", (_FakeMessageChunk(content="分析完成"), {"langgraph_node": "data_analyze_agent"}))

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_data_analyze_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"datasource": {"source_type": "mysql"}}))
    monkeypatch.setattr("apps.chat_data_app.handlers.prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.build_data_analyze_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.create_data_analyze_agent", lambda **_kwargs: FakeDataAnalyzeGraph())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    session_calls = _patch_data_analyze_workspace_session(monkeypatch)

    response = asyncio.run(data_analyze_completion_handler(
        conversation_id="conv_da",
        query="分析销售额",
        options={"datasource_id": "ds_a", "semantic_config_id": "cfg_a", "member_datasource_ids": ["m1"]},
        user_id="u1",
        username="测试用户",
    ))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    joined = "".join(item.decode("utf-8") if isinstance(item, bytes) else item for item in events)

    assert "event: task_stage" not in joined
    assert "event: task_stage_delta" not in joined
    assert '"tool_name": "semantic_query_tool"' in joined
    assert '"status": "completed"' in joined
    assert '"accumulated_args": {"dsl": {"metrics": ["销售额"]}}' in joined
    assert "分析完成" in joined
    assert 'event: message_end' in joined
    assert '"usage": {"total_tokens": 9}' in joined
    assert '"timestamp":' in joined
    assert joined.endswith("event: done\ndata: [DONE]\n\n")
    assert session_calls["created"] == [("/tmp/workspace", "u1")]
    assert [(item[0].sandbox_id, item[1]) for item in session_calls["attached"]] == [("sbx_test", "u1")]
    assert session_calls["cleaned"] == []


def test_data_analyze_completion_handler_streams_semantic_retrieval(monkeypatch):
    import asyncio

    captured = {}
    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_sales"],
        "semantic_configs": {"cfg_sales": {"id": "cfg_sales", "name": "销售分析"}},
    }

    class FakeDataAnalyzeGraph:
        async def astream(self, initial_state, config=None, stream_mode=None):
            captured["initial_state"] = initial_state
            yield ("messages", (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}))
            yield ("messages", (_FakeMessageChunk(content="分析完成"), {"langgraph_node": "data_analyze_agent"}))

    async def fake_build_context(datasource_id, semantic_config_id, user_id=None):
        captured["context_args"] = (datasource_id, semantic_config_id, user_id)
        return {"datasource": {"source_type": "mysql"}}

    def fake_create_graph(**kwargs):
        captured["graph_kwargs"] = kwargs
        return FakeDataAnalyzeGraph()

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_data_analyze_context", fake_build_context)
    monkeypatch.setattr("apps.chat_data_app.handlers.prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.build_data_analyze_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.create_data_analyze_agent", fake_create_graph)
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    session_calls = _patch_data_analyze_workspace_session(monkeypatch)

    response = asyncio.run(data_analyze_completion_handler(
        conversation_id="conv_da_auto_semantic",
        query="分析销售额趋势",
        options={"datasource_id": "ds_a"},
        user_id="u1",
    ))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    joined = "".join(item.decode("utf-8") if isinstance(item, bytes) else item for item in events)

    assert "event: tool" in joined
    decoded_events = [item.decode("utf-8") if isinstance(item, bytes) else item for item in events]
    assert '"semantic_config_ids": ["cfg_sales"]' in _tool_event_contents(decoded_events)[-1]
    assert "event: semantic_config_selected" not in joined
    assert "分析完成" in joined
    assert captured["context_args"] == ("ds_a", None, "u1")
    assert captured["graph_kwargs"]["semantic_config_id"] is None
    assert captured["graph_kwargs"]["datasource_id"] == "ds_a"
    assert captured["initial_state"]["datasource_id"] == "ds_a"
    assert captured["initial_state"]["semantic_config_id"] is None
    assert captured["initial_state"]["member_datasource_ids"] == []
    assert captured["initial_state"]["user_id"] == "u1"
    assert captured["initial_state"]["operator_name"] == ""
    assert captured["initial_state"]["conversation_id"] == "conv_da_auto_semantic"


def test_data_analyze_completion_handler_streams_multiple_semantic_configs(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order", "cfg_sales"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析"},
            "cfg_sales": {"id": "cfg_sales", "name": "销售分析"},
        },
    }

    async def fake_build_context(*_args, **_kwargs):
        return {"datasource": {"source_type": "mysql"}}

    class FakeDataAnalyzeGraph:
        async def astream(self, initial_state, config=None, stream_mode=None):
            yield ("messages", (_FakeMessageChunk(content="[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False), type_="tool", tool_call_id="call_select", name="select_semantic_config_tool"), {"langgraph_node": "tools"}))

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_data_analyze_context", fake_build_context)
    monkeypatch.setattr("apps.chat_data_app.handlers.prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.build_data_analyze_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.create_data_analyze_agent", lambda **_kwargs: FakeDataAnalyzeGraph())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    session_calls = _patch_data_analyze_workspace_session(monkeypatch)

    response = asyncio.run(data_analyze_completion_handler(
        conversation_id="conv_da_require_semantic",
        query="分析销售额",
        options={"datasource_id": "ds_a"},
        user_id="u1",
    ))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    joined = "".join(item.decode("utf-8") if isinstance(item, bytes) else item for item in events)

    assert "event: tool" in joined
    assert "event: semantic_config_select_required" not in joined
    assert "订单分析" in joined
    assert "销售分析" in joined
    assert joined.endswith("event: done\ndata: [DONE]\n\n")


def test_data_analyze_completion_handler_covers_tool_chunks_and_disconnect(monkeypatch):
    import asyncio

    class FakeDataAnalyzeGraph:
        async def astream(self, initial_state, config=None, stream_mode=None):
            assert stream_mode == ["messages", "custom"]
            yield ("messages", (_FakeMessageChunk(content="跳过内部"), {"langgraph_node": "__internal__"}))
            yield ("messages", (_FakeMessageChunk(content="", tool_call_chunks=[{"id": "chunk_tool", "name": "semantic_query_tool", "args": '{"dsl":', "index": 0}]), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (_FakeMessageChunk(content="", tool_call_chunks=[{"args": '{"metrics":["订单数"]}}', "index": 0}]), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (_FakeMessageChunk(content="执行失败: 表不存在", type_="tool", tool_call_id="chunk_tool"), {"langgraph_node": "tools"}))
            yield ("messages", (_FakeMessageChunk(content="", type_="tool", tool_call_id="missing_tool"), {"langgraph_node": "tools"}))
            yield ("messages", (_FakeMessageChunk(content="", type_="ai"), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (_FakeMessageChunk(content="完成", type_="ai"), {"langgraph_node": "data_analyze_agent"}))

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_data_analyze_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"datasource": {"source_type": "mysql"}}))
    monkeypatch.setattr("apps.chat_data_app.handlers.prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.build_data_analyze_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.create_data_analyze_agent", lambda **_kwargs: FakeDataAnalyzeGraph())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    session_calls = _patch_data_analyze_workspace_session(monkeypatch)

    response = asyncio.run(data_analyze_completion_handler("conv_da_subgraph", "分析", user_id="u1"))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    joined = "".join(item.decode("utf-8") if isinstance(item, bytes) else item for item in events)

    assert "跳过内部" not in joined
    assert '"status": "running"' in joined
    assert '"status": "error"' in joined
    assert '"accumulated_args": {"dsl": {"metrics": ["订单数"]}}' in joined
    assert "完成" in joined

    class DisconnectRequest:
        async def is_disconnected(self):
            return True

    class DisconnectGraph:
        async def astream(self, *args, **kwargs):
            yield ("messages", (_FakeMessageChunk(content="不应返回"), {"langgraph_node": "data_analyze_agent"}))

    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.create_data_analyze_agent", lambda **_kwargs: DisconnectGraph())
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

    assert "任务执行失败：context failed" not in joined
    assert 'event: error\ndata: {"error": "context failed"}' in joined
    assert joined.endswith("event: done\ndata: [DONE]\n\n")


def test_data_analyze_completion_handler_falls_back_when_skill_list_fails(monkeypatch):
    import asyncio
    from apps.chat_data_app import handlers as handlers_module

    captured = {}

    class FakeDataAnalyzeGraph:
        async def astream(self, initial_state, config=None, stream_mode=None):
            captured["initial_state"] = initial_state
            yield ("custom", {"type": "ignored", "usage": {"total_tokens": 0}})
            yield ("messages", (_FakeMessageChunk(content="fallback ok"), {"langgraph_node": "data_analyze_agent"}))

    class BrokenSkillService:
        async def list_skills_async(self, **_kwargs):
            raise RuntimeError("skill down")

    def fake_build_system_prompt(**kwargs):
        captured["available_skills_xml"] = kwargs["available_skills_xml"]
        return "system prompt"

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_data_analyze_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"datasource": {"source_type": "mysql"}}))
    monkeypatch.setattr("apps.chat_data_app.handlers.prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_builtin_skills_prompt", lambda: "<builtin-skills />")
    monkeypatch.setattr("apps.chat_data_app.handlers.skill_service", BrokenSkillService())
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.build_data_analyze_system_prompt", fake_build_system_prompt)
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.create_data_analyze_agent", lambda **_kwargs: FakeDataAnalyzeGraph())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    session_calls = _patch_data_analyze_workspace_session(monkeypatch)

    response = asyncio.run(data_analyze_completion_handler(
        conversation_id="conv_da_skill_fallback",
        query="分析",
        options={
            "datasource_id": "ds_a",
            "semantic_config_selection": {
                "semantic_config_id": "cfg_a",
                "matched_metrics": ["销售额"],
                "user_selected": True,
            },
        },
        user_id="u1",
    ))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    joined = "".join(item.decode("utf-8") if isinstance(item, bytes) else item for item in events)

    assert captured["available_skills_xml"] == "<builtin-skills />"
    human_message = captured["initial_state"]["messages"][0]
    assert human_message.additional_kwargs["semantic_config_selection"]["semantic_config_id"] == "cfg_a"
    assert "fallback ok" in joined
    assert "message_end" in joined
    assert '"timestamp":' in joined


def test_data_analyze_completion_handler_message_end_uses_state_token_usage(monkeypatch):
    import asyncio

    class FakeDataAnalyzeGraph:
        async def astream(self, initial_state, config=None, stream_mode=None):
            yield ("messages", (_FakeMessageChunk(content="完成"), {"langgraph_node": "data_analyze_agent"}))

        async def aget_state(self, _config):
            return SimpleNamespace(values={"token_usage": {"total_tokens": 18}}, tasks=[])

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_data_analyze_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"datasource": {"source_type": "mysql"}}))
    monkeypatch.setattr("apps.chat_data_app.handlers.prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))
    monkeypatch.setattr("apps.chat_data_app.handlers.skill_service.list_skills_async", lambda **_kwargs: asyncio.sleep(0, result=[]))
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.build_data_analyze_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.create_data_analyze_agent", lambda **_kwargs: FakeDataAnalyzeGraph())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    session_calls = _patch_data_analyze_workspace_session(monkeypatch)

    response = asyncio.run(data_analyze_completion_handler("conv_da_state_usage", "分析", user_id="u1"))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    joined = "".join(item.decode("utf-8") if isinstance(item, bytes) else item for item in events)

    assert 'event: token_usage' not in joined
    assert 'event: message_end' in joined
    assert '"usage": {"total_tokens": 18}' in joined


def test_data_analyze_completion_handler_tool_call_index_and_fallback_args(monkeypatch):
    import asyncio

    class FakeDataAnalyzeGraph:
        async def astream(self, initial_state, config=None, stream_mode=None):
            yield ("messages", (_FakeMessageChunk(
                content="",
                tool_calls=[
                    {"id": "", "name": "skip_missing_id", "index": 99},
                    {"id": "call_indexed", "name": "semantic_query_tool", "index": 0, "args": {"fallback": True}},
                    {"id": "call_indexed", "name": "semantic_query_tool", "index": 0},
                ],
            ), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (_FakeMessageChunk(
                content="",
                tool_call_chunks=[{"args": "", "index": 0}],
            ), {"langgraph_node": "data_analyze_agent"}))
            yield ("messages", (_FakeMessageChunk(
                content="完成",
                type_="tool",
                tool_call_id="call_indexed",
            ), {"langgraph_node": "tools"}))

    monkeypatch.setattr("apps.chat_data_app.handlers.conversation.get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr("apps.chat_data_app.handlers.build_data_analyze_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"datasource": {"source_type": "mysql"}}))
    monkeypatch.setattr("apps.chat_data_app.handlers.prepare_data_analyze_workspace", lambda *_args, **_kwargs: asyncio.sleep(0, result=({"root": "/tmp/workspace"}, "/tmp/workspace/data_context.json")))
    monkeypatch.setattr("apps.chat_data_app.handlers.skill_service.list_skills_async", lambda **_kwargs: asyncio.sleep(0, result=[]))
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.build_data_analyze_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr("apps.chat_data_app.agent.analyze_data_agent.create_data_analyze_agent", lambda **_kwargs: FakeDataAnalyzeGraph())
    monkeypatch.setattr("apps.chat_data_app.handlers.get_checkpointer", lambda: _fake_checkpointer())
    session_calls = _patch_data_analyze_workspace_session(monkeypatch)

    response = asyncio.run(data_analyze_completion_handler("conv_da_index", "分析", user_id="u1"))
    events = asyncio.run(_collect_async_iter(response.body_iterator))
    joined = "".join(item.decode("utf-8") if isinstance(item, bytes) else item for item in events)

    assert joined.count('"tool_call_id": "call_indexed"') >= 2
    assert '"accumulated_args": {"fallback": true}' in joined
    assert '"status": "completed"' in joined
