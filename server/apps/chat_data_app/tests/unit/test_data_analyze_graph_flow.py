import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from apps.chat_data_app.agent import data_analyze_graph


def test_data_analyze_tool_metadata_and_dialect_sections():
    assert [item["name"] for item in data_analyze_graph.get_data_analyze_tool_chain()] == [
        "skill_load",
        "file_read",
        "file_edit",
        "present_artifacts",
        "sandbox_run",
        "select_semantic_config_tool",
        "dsl2sql_tool",
        "semantic_query_tool",
    ]
    assert "`skill_load`" in data_analyze_graph.get_data_analyze_allowed_tool_names_text()
    assert "Hive" in data_analyze_graph.build_dialect_prompt_sections({"datasource": {"source_type": "hive"}})[0]
    assert "MySQL" in data_analyze_graph.build_dialect_prompt_sections({"semantic_configs": [{"source_type": "mysql"}]})[0]
    assert "Hive" in data_analyze_graph.build_dialect_prompt_sections({"semantic_configs": ["bad", {"source_type": "hive"}]})[0]
    assert "当前数据库方言未明确" in data_analyze_graph.build_dialect_prompt_sections({})[0]


def test_data_analyze_agent_state_exposes_datasource_context():
    annotations = data_analyze_graph.DataAnalyzeAgentState.__annotations__

    assert "messages" in annotations
    assert "datasource_id" in annotations
    assert "semantic_config_id" in annotations
    assert "conversation_id" in annotations


def test_merge_task_stages_updates_matching_run_id():
    merged = data_analyze_graph.merge_task_stages(
        [{"stage": "thinking", "run_id": "r1", "status": "running"}],
        [{"stage": "thinking", "run_id": "r1", "status": "completed"}, {"stage": "", "status": "free"}],
    )

    assert merged[0]["status"] == "completed"
    assert merged[1]["status"] == "free"


def test_data_analyze_reasoning_merge_and_default_skill_prompt(monkeypatch, tmp_path):
    assert data_analyze_graph.merge_reasoning_segments([{"id": "old"}], [{"id": "new"}]) == [{"id": "old"}, {"id": "new"}]
    assert data_analyze_graph.keep_conversation_datasource_id(None, "ds_a") == "ds_a"
    assert data_analyze_graph.keep_conversation_datasource_id("ds_a", "ds_b") == "ds_a"

    monkeypatch.setattr(data_analyze_graph, "build_builtin_skills_prompt", lambda: "<skills>默认</skills>")
    prompt = data_analyze_graph.build_data_analyze_system_prompt(
        skill={},
        data_context={},
        workspace_root=str(tmp_path),
        available_skills_xml=None,
    )

    assert "<skills>默认</skills>" in prompt


def test_data_analyze_prompt_helper_remaining_edges(monkeypatch, tmp_path):
    assert data_analyze_graph._extract_source_type({"semantic_configs": ["bad", {}]}) == ""

    monkeypatch.setattr(data_analyze_graph, "build_builtin_skills_prompt", lambda: "<skills />")
    prompt = data_analyze_graph.build_data_analyze_system_prompt(
        skill=None,
        data_context={},
        workspace_root=str(tmp_path),
        available_skills_xml=None,
    )
    assert "<skills />" in prompt
    assert str(tmp_path) in prompt


def test_create_data_analyze_tools_wraps_expected_tool_names(monkeypatch, tmp_path):
    monkeypatch.setattr(data_analyze_graph, "create_tool_retry_guard", lambda tools: tools)
    tools = data_analyze_graph.create_data_analyze_tools(
        str(tmp_path),
        datasource_id="ds_a",
        semantic_config_id="cfg_a",
        conversation_id="conv_a",
        member_datasource_ids=["m1"],
        user_id="u1",
        operator_name="测试用户",
    )

    assert [tool.name for tool in tools] == [
        "skill_load",
        "file_read",
        "file_edit",
        "present_artifacts",
        "sandbox_run",
        "select_semantic_config_tool",
        "dsl2sql_tool",
        "semantic_query_tool",
    ]


def test_create_data_analyze_agent_adds_fallback_middleware(monkeypatch, tmp_path):
    captured = {}
    bound = []

    class FakeLLM:
        pass

    class FakeFallbackMiddleware:
        def __init__(self, model):
            self.model = model

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(name="agent")

    monkeypatch.setattr(data_analyze_graph, "get_chat_data_llm", lambda **_kwargs: FakeLLM())
    monkeypatch.setattr(data_analyze_graph, "get_default_chat_data_model", lambda: "fallback-model")
    monkeypatch.setattr(data_analyze_graph, "ModelFallbackMiddleware", FakeFallbackMiddleware)
    monkeypatch.setattr(data_analyze_graph, "create_request_token_usage_middleware", lambda model_id, **_kwargs: {"token_model": model_id})
    monkeypatch.setattr(data_analyze_graph, "create_data_analyze_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(data_analyze_graph, "create_agent", fake_create_agent)
    monkeypatch.setattr(data_analyze_graph, "bind_token_usage_middleware", lambda agent, middleware: bound.append((agent, middleware)))

    agent = data_analyze_graph.create_data_analyze_agent(
        workspace_root=str(tmp_path),
        system_prompt="system",
        model_id="primary-model",
        datasource_id="ds_a",
        semantic_config_id="sc_a",
    )

    assert agent.name == "agent"
    assert len(captured["middleware"]) == 3
    assert isinstance(captured["middleware"][0], FakeFallbackMiddleware)
    assert captured["middleware"][2].name == "ToolCallNameNormalizerMiddleware"
    assert bound == [(agent, {"token_model": "primary-model"})]


@pytest.mark.asyncio
async def test_data_analyze_tool_call_name_normalizer_middleware():
    middleware = data_analyze_graph.create_tool_call_name_normalizer_middleware([
        SimpleNamespace(name="select_semantic_config_tool"),
    ])

    async def handler(_request):
        return SimpleNamespace(result=[
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_select",
                    "name": "select_semantic_config_toolselect_semantic_config_tool",
                    "args": {"query": "查询入金金额"},
                }],
            ),
        ])

    response = await middleware.awrap_model_call(SimpleNamespace(), handler)

    assert response.result[0].tool_calls[0]["name"] == "select_semantic_config_tool"
    assert response.result[0].tool_calls[0]["args"] == {"query": "查询入金金额"}


def test_create_data_analyze_agent_skips_fallback_when_same_model(monkeypatch, tmp_path):
    captured = {}

    monkeypatch.setattr(data_analyze_graph, "get_chat_data_llm", lambda **_kwargs: object())
    monkeypatch.setattr(data_analyze_graph, "get_default_chat_data_model", lambda: "same-model")
    monkeypatch.setattr(data_analyze_graph, "create_request_token_usage_middleware", lambda model_id, **_kwargs: {"token_model": model_id})
    monkeypatch.setattr(data_analyze_graph, "create_data_analyze_tools", lambda *_args, **_kwargs: [])

    def fake_create_agent(**kwargs):
        captured["agent_kwargs"] = kwargs
        return SimpleNamespace(name="agent")

    monkeypatch.setattr(data_analyze_graph, "create_agent", fake_create_agent)
    monkeypatch.setattr(data_analyze_graph, "bind_token_usage_middleware", lambda *_args, **_kwargs: None)

    data_analyze_graph.create_data_analyze_agent(
        workspace_root=str(tmp_path),
        system_prompt="system",
        model_id="same-model",
    )

    assert len(captured["agent_kwargs"]["middleware"]) == 2
    assert captured["agent_kwargs"]["middleware"][1].name == "ToolCallNameNormalizerMiddleware"


@pytest.mark.asyncio
async def test_create_data_analyze_flow_graph_runs_single_agent_node(monkeypatch, tmp_path):
    class FakeAgent:
        async def ainvoke(self, state, config=None):
            return {"messages": [AIMessage(content="执行完成")]}

    monkeypatch.setattr(data_analyze_graph, "create_data_analyze_agent", lambda **_kwargs: FakeAgent())
    monkeypatch.setattr(data_analyze_graph, "invoke_with_token_usage", lambda **_kwargs: asyncio.sleep(0, result=({"messages": [AIMessage(content="执行完成")]}, {"total_tokens": 12})))
    monkeypatch.setattr(data_analyze_graph, "get_stream_writer", lambda: lambda payload: None)
    monkeypatch.setattr(data_analyze_graph, "get_workspace_execution_session", lambda workspace_root, user_id: asyncio.sleep(0, result=SimpleNamespace(sandbox_id="sbx_1")))
    monkeypatch.setattr(data_analyze_graph, "attach_workspace_execution_session", lambda session, user_id: asyncio.sleep(0, result=None))
    monkeypatch.setattr(data_analyze_graph, "cleanup_workspace_execution_session", lambda session: asyncio.sleep(0, result=None))

    graph = data_analyze_graph.create_data_analyze_flow_graph(
        workspace_root=str(tmp_path),
        system_prompt="system",
        user_id="u1",
    )
    result = await graph.ainvoke({
        "messages": [HumanMessage(content="分析销售额")],
        "task_stages": [],
        "task_run_id": "run_1",
    }, config={"configurable": {"thread_id": "conv_graph"}})

    assert result["task_stages"] == []
    assert result["messages"][-1].content == "执行完成"
    assert result["token_usage"] == {"total_tokens": 12}


@pytest.mark.asyncio
async def test_flow_graph_reuses_existing_session(monkeypatch, tmp_path):
    existing_session = SimpleNamespace(sandbox_id="existing")
    attach_calls = []

    monkeypatch.setattr(data_analyze_graph, "create_data_analyze_agent", lambda **kwargs: SimpleNamespace(ref=kwargs["execution_session_ref"]))
    monkeypatch.setattr(data_analyze_graph, "invoke_with_token_usage", lambda **_kwargs: asyncio.sleep(0, result=({"messages": [AIMessage(content="执行")]}, {})))
    monkeypatch.setattr(data_analyze_graph, "get_stream_writer", lambda: lambda payload: None)
    monkeypatch.setattr(data_analyze_graph, "get_workspace_execution_session", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cached session should be reused")))
    monkeypatch.setattr(data_analyze_graph, "attach_workspace_execution_session", lambda session, user_id: asyncio.sleep(0, result=attach_calls.append((session, user_id))))
    monkeypatch.setattr(data_analyze_graph, "cleanup_workspace_execution_session", lambda session: asyncio.sleep(0, result=None))

    original_ref = data_analyze_graph.SandboxExecutionSessionRef

    class SeededRef(original_ref):
        def __init__(self):
            super().__init__()
            self.current = existing_session

    monkeypatch.setattr(data_analyze_graph, "SandboxExecutionSessionRef", SeededRef)

    graph = data_analyze_graph.create_data_analyze_flow_graph(workspace_root=str(tmp_path), system_prompt="system", user_id="u1")
    await graph.ainvoke({
        "messages": [HumanMessage(content="分析")],
        "task_stages": [],
        "task_run_id": "run-default",
    }, config={"configurable": {"thread_id": "conv_default"}})

    assert attach_calls == [(existing_session, "u1")]


@pytest.mark.asyncio
async def test_flow_graph_execute_exception_cleans_session(monkeypatch, tmp_path):
    session = SimpleNamespace(sandbox_id="sbx")
    cleaned = []

    monkeypatch.setattr(data_analyze_graph, "create_data_analyze_agent", lambda **_kwargs: object())
    monkeypatch.setattr(data_analyze_graph, "invoke_with_token_usage", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("agent failed")))
    monkeypatch.setattr(data_analyze_graph, "get_stream_writer", lambda: lambda payload: None)
    monkeypatch.setattr(data_analyze_graph, "get_workspace_execution_session", lambda *_args, **_kwargs: asyncio.sleep(0, result=session))
    monkeypatch.setattr(data_analyze_graph, "cleanup_workspace_execution_session", lambda session: asyncio.sleep(0, result=cleaned.append(session)))
    monkeypatch.setattr(data_analyze_graph, "attach_workspace_execution_session", lambda session, user_id: asyncio.sleep(0, result=None))

    graph = data_analyze_graph.create_data_analyze_flow_graph(workspace_root=str(tmp_path), system_prompt="system", user_id="u1")

    with pytest.raises(RuntimeError, match="agent failed"):
        await graph.ainvoke({
            "messages": [HumanMessage(content="分析")],
            "task_stages": [],
            "task_run_id": "run-error",
        }, config={"configurable": {"thread_id": "conv_error"}})

    assert cleaned == [session]


@pytest.mark.asyncio
async def test_execute_data_analyze_agent_returns_message_for_non_dict(monkeypatch):
    monkeypatch.setattr(data_analyze_graph, "invoke_with_token_usage", lambda **_kwargs: asyncio.sleep(0, result=(AIMessage(content="完成"), {"total_tokens": 3})))
    emitted = []
    monkeypatch.setattr(data_analyze_graph, "get_stream_writer", lambda: emitted.append)

    result = await data_analyze_graph.execute_data_analyze_agent(agent=object(), state={"messages": []}, system_prompt="system", model_id=None)

    assert result["messages"][0].content == "完成"
    assert result["messages"][0].additional_kwargs["timestamp"]
    assert emitted == [{"type": "token_usage", "usage": {"total_tokens": 3}}]


def test_get_data_analyze_graph_builds_history_reader(monkeypatch):
    captured = {}

    monkeypatch.setattr(data_analyze_graph, "ensure_data_analyze_workspace", lambda conversation_id: {"root": f"/tmp/{conversation_id}"})
    monkeypatch.setattr(data_analyze_graph, "build_builtin_skills_prompt", lambda: "<skills />")

    def fake_create_flow(**kwargs):
        captured["kwargs"] = kwargs
        return "graph"

    monkeypatch.setattr(data_analyze_graph, "create_data_analyze_flow_graph", fake_create_flow)

    graph = data_analyze_graph.get_data_analyze_graph(checkpointer="checkpoint")

    assert graph == "graph"
    assert captured["kwargs"]["workspace_root"] == "/tmp/_history_reader"
    assert captured["kwargs"]["checkpointer"] == "checkpoint"
