import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

import pytest
from langchain_core.messages import AIMessage

from apps.chat_data_app.agent import data_agent
from apps.chat_data_app.agent.agent_state import ChatDataAgentState
from apps.chat_data_app.agent.data_agent import assemble_tools
from apps.chat_data_app.agent.middleware.tool_call_normalizer import create_tool_call_name_normalizer_middleware
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions
from apps.chat_data_app.services.conversation_workspace import ConversationWorkspaceRuntime


def _runtime(tmp_path, *, bash_enabled=False):
    return ConversationWorkspaceRuntime(
        workspace_root=str(tmp_path),
        user_id="u1",
        backend="local",
    ), AgentRuntimeOptions(workspace_backend="local", bash_enabled=bash_enabled)


def test_chat_data_agent_state_exposes_execution_and_workspace_context():
    annotations = ChatDataAgentState.__annotations__

    assert "execution_context" in annotations
    assert "workspace" in annotations
    assert "semantic_config_id" not in annotations
    assert "conversation_id" in annotations
    assert "reasoning_segments" in annotations
    assert "token_usage" in annotations
    assert "conversation_datasource_id" not in annotations


def test_assemble_tools_registers_all_tools_except_optional_bash(tmp_path):
    runtime, without_bash = _runtime(tmp_path, bash_enabled=False)
    _, with_bash = _runtime(tmp_path, bash_enabled=True)

    tool_names = [tool.name for tool in assemble_tools(
        without_bash, runtime, datasource_id="ds_a", user_id="u1", operator_name="测试用户",
    )]
    bash_tool_names = [tool.name for tool in assemble_tools(
        with_bash, runtime, datasource_id="ds_a", user_id="u1", operator_name="测试用户",
    )]

    assert tool_names == [
        "skill_load", "file_read", "file_edit", "file_write", "file_move", "file_delete", "file_glob", "file_grep", "dir_create", "dir_list", "dir_delete", "parse_document", "present_artifacts", "get_current_time",
        "search_dataset", "dsl2sql_tool", "semantic_query_tool", "calculator_tool",
    ]
    assert bash_tool_names[-1] == "bash_run"
    assert bash_tool_names[:-1] == tool_names


def test_create_data_agent_uses_standard_middleware_without_model_fallback(monkeypatch, tmp_path):
    captured = {}
    bound = []
    runtime, options = _runtime(tmp_path)

    class FakeLLM:
        pass

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(name="agent")

    monkeypatch.setattr(data_agent, "create_streaming_chat_model", lambda **_kwargs: FakeLLM())
    context_kwargs = {}

    def fake_context_usage_middleware(model_id, **kwargs):
        context_kwargs.update(kwargs)
        return {"context_model": model_id}

    monkeypatch.setattr(data_agent, "create_request_context_usage_middleware", fake_context_usage_middleware)
    monkeypatch.setattr(data_agent, "assemble_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(data_agent, "create_agent", fake_create_agent)
    monkeypatch.setattr(data_agent, "bind_context_usage_middleware", lambda agent, middleware: bound.append((agent, middleware)))

    agent = data_agent.create_data_agent(
        options,
        workspace_runtime=runtime,
        system_prompt="system",
        model_id="primary-model",
        datasource_id="ds_a",
    )

    assert agent.name == "agent"
    assert len(captured["middleware"]) == 6
    assert captured["middleware"][0].name == "ContextCompressionMiddleware"
    assert captured["middleware"][1] == {"context_model": "primary-model"}
    assert captured["middleware"][2].name == "ResponseTimestampMiddleware"
    assert captured["middleware"][3].name == "ToolCallNameNormalizerMiddleware"
    assert captured["middleware"][4].name == "ToolResultMiddleware"
    assert captured["middleware"][5].name == "ToolErrorMiddleware"
    assert context_kwargs["tools"] == []
    assert context_kwargs["state_schema"] is ChatDataAgentState
    assert bound == [(agent, {"context_model": "primary-model"})]


@pytest.mark.asyncio
async def test_tool_call_name_normalizer_middleware():
    middleware = create_tool_call_name_normalizer_middleware([SimpleNamespace(name="search_dataset")])

    async def handler(_request):
        return SimpleNamespace(result=[
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_select",
                    "name": "search_datasetsearch_dataset",
                    "args": {"query": "查询入金金额"},
                }],
            ),
        ])

    response = await middleware.awrap_model_call(SimpleNamespace(), handler)

    assert response.result[0].tool_calls[0]["name"] == "search_dataset"
    assert response.result[0].tool_calls[0]["args"] == {"query": "查询入金金额"}


def test_get_data_agent_graph_forwards_runtime_options_and_checkpointer(monkeypatch):
    captured = {}

    def fake_create_agent(runtime_options, **kwargs):
        captured["runtime_options"] = runtime_options
        captured["kwargs"] = kwargs
        return "graph"

    monkeypatch.setattr(data_agent, "create_data_agent", fake_create_agent)

    graph = data_agent.get_data_agent_graph(checkpointer="checkpoint")

    assert graph == "graph"
    assert captured["runtime_options"] is None
    assert captured["kwargs"]["checkpointer"] == "checkpoint"
