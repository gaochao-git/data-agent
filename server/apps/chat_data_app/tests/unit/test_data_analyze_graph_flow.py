import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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
        "execute_sql_tool",
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


def test_stage_context_truncates_and_merges_stages():
    messages = [HumanMessage(content="用户问题" * 300), AIMessage(content="助手回答")]
    prompt = data_analyze_graph._build_stage_user_prompt(messages, "当前问题", "补充信息")
    merged = data_analyze_graph.merge_task_stages(
        [{"stage": "thinking", "run_id": "r1", "status": "running"}],
        [{"stage": "thinking", "run_id": "r1", "status": "completed"}, {"stage": "", "status": "free"}],
    )

    assert len(prompt) < 4600
    assert "当前用户目标：\n当前问题" in prompt
    assert "补充信息" in prompt
    assert merged[0]["status"] == "completed"
    assert merged[1]["status"] == "free"


def test_stage_context_empty_and_multimodal_message_edges():
    messages = [
        SystemMessage(content="system ignored"),
        HumanMessage(content=[{"text": "用户文本"}, "补充文本", {"content": "内容字段"}, {"image": "ignored"}]),
        AIMessage(content=[{"content": "助手内容"}]),
    ]
    context = data_analyze_graph._format_stage_conversation_context(messages)
    no_history_prompt = data_analyze_graph._build_stage_user_prompt([], "当前问题")

    assert data_analyze_graph._latest_user_query([AIMessage(content="无用户")]) == ""
    assert data_analyze_graph._message_text(["a", {"text": "b"}, {"content": "c"}, {"bad": "d"}]) == "a\nb\nc"
    assert data_analyze_graph._format_stage_conversation_context([]) == ""
    assert "用户文本" in context
    assert "助手内容" in context
    assert no_history_prompt == "当前用户目标：\n当前问题"


def test_data_analyze_prompt_helper_remaining_edges(monkeypatch, tmp_path):
    assert data_analyze_graph._message_text([{"text": ""}, {"content": None}]) == ""
    assert data_analyze_graph._message_text({"unexpected": "value"}) == "{'unexpected': 'value'}"
    assert data_analyze_graph._format_stage_conversation_context([HumanMessage(content=""), SystemMessage(content="ignored")]) == ""
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
        "execute_sql_tool",
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
    monkeypatch.setattr(data_analyze_graph, "create_request_token_usage_middleware", lambda model_id: {"token_model": model_id})
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
    assert len(captured["middleware"]) == 2
    assert isinstance(captured["middleware"][1], FakeFallbackMiddleware)
    assert bound == [(agent, {"token_model": "primary-model"})]


def test_create_data_analyze_agent_skips_fallback_when_same_model(monkeypatch, tmp_path):
    captured = {}

    monkeypatch.setattr(data_analyze_graph, "get_chat_data_llm", lambda **_kwargs: object())
    monkeypatch.setattr(data_analyze_graph, "get_default_chat_data_model", lambda: "same-model")
    monkeypatch.setattr(data_analyze_graph, "create_request_token_usage_middleware", lambda model_id: {"token_model": model_id})
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

    assert len(captured["agent_kwargs"]["middleware"]) == 1


@pytest.mark.asyncio
async def test_create_data_analyze_flow_graph_runs_all_stages(monkeypatch, tmp_path):
    class FakeLLM:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content="理解用户要分析销售额")
            if self.calls == 2:
                return SimpleNamespace(content="1. 查询数据\n2. 生成图表")
            return SimpleNamespace(content="已完成分析")

    class FakeAgent:
        async def ainvoke(self, state, config=None):
            return {"messages": [AIMessage(content="执行完成")]}

    fake_llm = FakeLLM()
    monkeypatch.setattr(data_analyze_graph, "get_chat_data_llm", lambda **_kwargs: fake_llm)
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

    stages = result["task_stages"]
    assert [stage["stage"] for stage in stages] == ["thinking", "plan", "execute", "done"]
    assert stages[0]["content"] == "理解用户要分析销售额"
    assert stages[1]["steps"] == ["查询数据", "生成图表"]
    assert stages[-1]["content"] == "已完成分析"


@pytest.mark.asyncio
async def test_flow_graph_reuses_session_defaults_plan_and_done_text(monkeypatch, tmp_path):
    class FakeLLM:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content="理解")
            if self.calls == 2:
                return SimpleNamespace(content="  \n")
            return SimpleNamespace(content="")

    existing_session = SimpleNamespace(sandbox_id="existing")
    attach_calls = []
    fake_llm = FakeLLM()

    monkeypatch.setattr(data_analyze_graph, "get_chat_data_llm", lambda **_kwargs: fake_llm)
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
    result = await graph.ainvoke({
        "messages": [HumanMessage(content="分析")],
        "task_stages": [],
        "task_run_id": "run-default",
    }, config={"configurable": {"thread_id": "conv_default"}})

    plan_stage = next(stage for stage in result["task_stages"] if stage["stage"] == "plan")
    done_stage = next(stage for stage in result["task_stages"] if stage["stage"] == "done")
    assert plan_stage["steps"] == data_analyze_graph.DATA_ANALYZE_DEFAULT_PLAN_STEPS
    assert done_stage["content"] == "已完成本次智能分析任务。"
    assert attach_calls == [(existing_session, "u1")]


@pytest.mark.asyncio
async def test_flow_graph_execute_exception_cleans_session(monkeypatch, tmp_path):
    class FakeLLM:
        async def ainvoke(self, messages):
            return SimpleNamespace(content="理解")

    session = SimpleNamespace(sandbox_id="sbx")
    cleaned = []

    monkeypatch.setattr(data_analyze_graph, "get_chat_data_llm", lambda **_kwargs: FakeLLM())
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
