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
from langchain_core.messages import AIMessage

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

    assert "datasource_id" in annotations
    assert "semantic_config_id" in annotations
    assert "conversation_id" in annotations
    assert "reasoning_segments" in annotations
    assert "token_usage" in annotations
    assert "conversation_datasource_id" in annotations


def test_data_analyze_default_skill_prompt(monkeypatch, tmp_path):
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


def test_get_data_analyze_graph_builds_history_reader(monkeypatch):
    captured = {}

    monkeypatch.setattr(data_analyze_graph, "ensure_data_analyze_workspace", lambda conversation_id: {"root": f"/tmp/{conversation_id}"})
    monkeypatch.setattr(data_analyze_graph, "build_builtin_skills_prompt", lambda: "<skills />")

    def fake_create_agent(**kwargs):
        captured["kwargs"] = kwargs
        return "graph"

    monkeypatch.setattr(data_analyze_graph, "create_data_analyze_agent", fake_create_agent)

    graph = data_analyze_graph.get_data_analyze_graph(checkpointer="checkpoint")

    assert graph == "graph"
    assert captured["kwargs"]["workspace_root"] == "/tmp/_history_reader"
    assert captured["kwargs"]["checkpointer"] == "checkpoint"
