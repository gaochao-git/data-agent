import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

import pytest
from langchain_core.messages import AIMessage

from apps.chat_data_app.agent import data_agent
from apps.chat_data_app.agent.agent_state import ChatDataAgentState
from apps.chat_data_app.agent.data_agent import (
    DatasourceContext,
    IdentityContext,
    ModelInvocationConfig,
    assemble_tools,
)
from apps.chat_data_app.agent.middleware.tool_call_normalizer import create_tool_call_name_normalizer_middleware
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions
from apps.chat_data_app.services.agent_run.conversation_workspace import ConversationWorkspaceRuntime


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
    assert "reasoning_segments" not in annotations
    assert "token_usage" in annotations
    assert "conversation_datasource_id" not in annotations


def test_assemble_tools_registers_all_tools_except_optional_bash(tmp_path):
    runtime, without_bash = _runtime(tmp_path, bash_enabled=False)
    _, with_bash = _runtime(tmp_path, bash_enabled=True)

    tool_names = [tool.name for tool in assemble_tools(
        without_bash, runtime,
        identity_context=IdentityContext(user_id="u1", operator_name="测试用户"),
        datasource_context=DatasourceContext(datasource_id="ds_a"),
    )]
    bash_tool_names = [tool.name for tool in assemble_tools(
        with_bash, runtime,
        identity_context=IdentityContext(user_id="u1", operator_name="测试用户"),
        datasource_context=DatasourceContext(datasource_id="ds_a"),
    )]

    assert tool_names == [
        "file_read", "file_edit", "file_write", "file_move", "file_copy", "file_delete", "file_glob", "file_grep", "file_diff", "dir_create", "dir_list", "dir_delete", "parse_document", "read_image", "present_artifacts", "get_current_time",
        "data_analyzing_tool", "calculator_tool", "ask_user_question", "officecli_run",
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
        identity_context=IdentityContext(),
        datasource_context=DatasourceContext(datasource_id="ds_a"),
        model_invocation=ModelInvocationConfig(system_prompt="system", model_id="primary-model"),
    )

    assert agent.name == "agent"
    assert len(captured["middleware"]) == 11
    assert captured["middleware"][0].name == "ImageInjectionMiddleware"
    assert captured["middleware"][1].name == "ToolTimingMiddleware"
    assert captured["middleware"][2].name == "ToolCallRateAlertMiddleware"
    assert captured["middleware"][3].name == "ToolCallHistoryRepairMiddleware"
    assert captured["middleware"][4].name == "TodoListMiddleware"
    assert captured["middleware"][5].name == "ContextCompressionMiddleware"
    assert captured["middleware"][6] == {"context_model": "primary-model"}
    assert captured["middleware"][7].name == "ResponseTimestampMiddleware"
    assert captured["middleware"][8].name == "ToolCallNameNormalizerMiddleware"
    assert captured["middleware"][9].name == "ToolResultMiddleware"
    assert captured["middleware"][10].name == "ToolErrorMiddleware"
    assert context_kwargs["tools"] == []
    assert context_kwargs["state_schema"] is ChatDataAgentState
    assert bound == [(agent, {"context_model": "primary-model"})]


def test_create_data_agent_registers_after_model_history_middleware(monkeypatch, tmp_path):
    captured = {}
    runtime, options = _runtime(tmp_path)

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(name="agent")

    monkeypatch.setattr(data_agent, "create_streaming_chat_model", lambda **_kwargs: object())
    monkeypatch.setattr(data_agent, "assemble_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(data_agent, "create_agent", fake_create_agent)

    async def persist_snapshot(_state):
        return None

    data_agent.create_data_agent(
        options,
        workspace_runtime=runtime,
        identity_context=IdentityContext(),
        datasource_context=DatasourceContext(datasource_id="ds_a"),
        model_invocation=ModelInvocationConfig(system_prompt="system", model_id="primary-model"),
        persist_model_snapshot=persist_snapshot,
    )

    assert [middleware.name for middleware in captured["middleware"]] == [
        "ImageInjectionMiddleware",
        "ToolTimingMiddleware",
        "ToolCallRateAlertMiddleware",
        "ToolCallHistoryRepairMiddleware",
        "TodoListMiddleware",
        "AfterModelHistoryMiddleware",
        "ContextCompressionMiddleware",
        "ContextUsageMiddleware",
        "ResponseTimestampMiddleware",
        "ToolCallNameNormalizerMiddleware",
        "ToolResultMiddleware",
        "ToolErrorMiddleware",
    ]


def test_create_data_agent_registers_subagent_middleware_when_enabled(monkeypatch, tmp_path):
    captured = {}
    runtime, base_options = _runtime(tmp_path)
    options = AgentRuntimeOptions(
        workspace_backend="local", bash_enabled=False, explore_subagent_enabled=True
    )

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(name="agent")

    monkeypatch.setattr(data_agent, "create_streaming_chat_model", lambda **_kwargs: object())
    monkeypatch.setattr(data_agent, "assemble_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(data_agent, "create_agent", fake_create_agent)

    data_agent.create_data_agent(
        options,
        workspace_runtime=runtime,
        identity_context=IdentityContext(),
        datasource_context=DatasourceContext(datasource_id="ds_a"),
        model_invocation=ModelInvocationConfig(system_prompt="system", model_id="primary-model"),
    )

    names = [middleware.name for middleware in captured["middleware"]]
    assert "SubagentMiddleware" in names
    assert names.index("SubagentMiddleware") == names.index("TodoListMiddleware") - 1


def test_build_subagent_middleware_disabled_by_default(tmp_path):
    from apps.chat_data_app.agent.data_agent import build_subagent_middleware

    runtime, options = _runtime(tmp_path)
    assert build_subagent_middleware(options, [], model=object()) is None


def _subagent_spec_names(middleware) -> list[str]:
    return [spec.name for spec in middleware.specs]


def test_build_subagent_middleware_explore_excludes_write_tools(monkeypatch, tmp_path):
    from apps.chat_data_app.agent.data_agent import build_subagent_middleware
    from apps.chat_data_app.agent.middleware import subagents as subagents_module

    monkeypatch.setattr(
        data_agent, "create_streaming_chat_model", lambda **_kwargs: object()
    )
    # 伪工具对象无法走真实 create_agent 编译，桩掉子图编译步骤
    monkeypatch.setattr(
        subagents_module, "_compile_subagent", lambda spec, run_controls=None: SimpleNamespace()
    )
    options = AgentRuntimeOptions(
        workspace_backend="local", bash_enabled=False, explore_subagent_enabled=True
    )
    tools = [
        SimpleNamespace(name="file_read"),
        SimpleNamespace(name="file_grep"),
        SimpleNamespace(name="file_write"),
        SimpleNamespace(name="file_edit"),
        SimpleNamespace(name="bash_run"),
        SimpleNamespace(name="officecli_run"),
        SimpleNamespace(name="ask_user_question"),
        SimpleNamespace(name="present_artifacts"),
        SimpleNamespace(name="calculator_tool"),
    ]
    model = object()

    middleware = build_subagent_middleware(options, tools, model=model)

    assert middleware is not None
    assert _subagent_spec_names(middleware) == ["data-explore"]
    spec = middleware.specs[0]
    assert [tool.name for tool in spec.tools] == ["file_read", "file_grep", "calculator_tool"]
    assert spec.model is model


def test_build_subagent_middleware_plan_keeps_only_inventory_tools(monkeypatch, tmp_path):
    from apps.chat_data_app.agent.data_agent import build_subagent_middleware
    from apps.chat_data_app.agent.middleware import subagents as subagents_module

    monkeypatch.setattr(
        data_agent, "create_streaming_chat_model", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        subagents_module, "_compile_subagent", lambda spec, run_controls=None: SimpleNamespace()
    )
    options = AgentRuntimeOptions(
        workspace_backend="local", bash_enabled=False, plan_subagent_enabled=True
    )
    tools = [
        SimpleNamespace(name="file_read"),
        SimpleNamespace(name="file_write"),
        SimpleNamespace(name="bash_run"),
        SimpleNamespace(name="officecli_run"),
        SimpleNamespace(name="ask_user_question"),
        SimpleNamespace(name="present_artifacts"),
    ]
    model = object()

    middleware = build_subagent_middleware(options, tools, model=model)

    assert middleware is not None
    assert _subagent_spec_names(middleware) == ["plan"]
    spec = middleware.specs[0]
    # plan 只保留盘点与计划落盘工具：执行类工具（bash/officecli）与人工应答/产物呈现全部扣除
    assert [tool.name for tool in spec.tools] == ["file_read", "file_write"]
    assert spec.model is model


def test_build_subagent_middleware_supports_all_specs(monkeypatch, tmp_path):
    from apps.chat_data_app.agent.data_agent import build_subagent_middleware
    from apps.chat_data_app.agent.middleware import subagents as subagents_module

    monkeypatch.setattr(
        data_agent, "create_streaming_chat_model", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        subagents_module, "_compile_subagent", lambda spec, run_controls=None: SimpleNamespace()
    )
    options = AgentRuntimeOptions(
        workspace_backend="local", bash_enabled=False,
        explore_subagent_enabled=True, plan_subagent_enabled=True,
        general_purpose_subagent_enabled=True,
    )
    tools = [
        SimpleNamespace(name="file_read"),
        SimpleNamespace(name="file_write"),
        SimpleNamespace(name="bash_run"),
    ]
    model = object()

    middleware = build_subagent_middleware(options, tools, model=model)

    assert middleware is not None
    assert _subagent_spec_names(middleware) == ["data-explore", "plan", "general-purpose"]
    # 同一工具列表按各自排除集独立过滤：explore 不含写工具，plan 只留盘点与计划落盘，
    # general-purpose 保留写工具（执行交付物需要）但 bash_run 仍排除
    explore_tools = [tool.name for tool in middleware.specs[0].tools]
    plan_tools = [tool.name for tool in middleware.specs[1].tools]
    gp_tools = [tool.name for tool in middleware.specs[2].tools]
    assert explore_tools == ["file_read"]
    assert plan_tools == ["file_read", "file_write"]
    assert gp_tools == ["file_read", "file_write"]
    # task 工具描述中列出全部类型
    assert "data-explore" in middleware.tools[0].description
    assert "plan" in middleware.tools[0].description
    assert "general-purpose" in middleware.tools[0].description


@pytest.mark.asyncio
async def test_tool_call_name_normalizer_middleware():
    middleware = create_tool_call_name_normalizer_middleware([SimpleNamespace(name="semantic_search")])

    async def handler(_request):
        return SimpleNamespace(result=[
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_select",
                    "name": "semantic_searchsemantic_search",
                    "args": {"query": "查询入金金额"},
                }],
            ),
        ])

    response = await middleware.awrap_model_call(SimpleNamespace(), handler)

    assert response.result[0].tool_calls[0]["name"] == "semantic_search"
    assert response.result[0].tool_calls[0]["args"] == {"query": "查询入金金额"}


@pytest.mark.asyncio
@pytest.mark.parametrize("repeated_name", ["write_todoswrite_todos", "write_todoswrite_todoswrite_todos"])
async def test_tool_call_name_normalizer_covers_middleware_injected_tools(repeated_name):
    """候选名必须取本轮 request.tools。

    重复名的成因是流式协议里多段 ``tool_call delta`` 的 ``function.name`` 被逐段拼接
    （恒为名称的整数倍，不带分隔符）。``write_todos`` 由 TodoListMiddleware 经
    ``middleware.tools`` 注册，不在业务 tools 静态列表里；以静态列表当候选名时这类名字
    修不回来（内网 vLLM/GLM 实测）。同时确认：不在候选集内的名称仍原样返回，不做猜测。
    """
    middleware = create_tool_call_name_normalizer_middleware([SimpleNamespace(name="semantic_search")])
    request = SimpleNamespace(tools=[
        SimpleNamespace(name="write_todos"),
        SimpleNamespace(name="semantic_search"),
    ])

    async def handler(_request):
        return AIMessage(content="", tool_calls=[{
            "id": "call_todos", "name": repeated_name, "args": {"todos": []},
        }])

    response = await middleware.awrap_model_call(request, handler)

    assert response.tool_calls[0]["name"] == "write_todos"
    assert response.tool_calls[0]["args"] == {"todos": []}


@pytest.mark.asyncio
async def test_tool_call_name_normalizer_leaves_unmatched_names_untouched():
    middleware = create_tool_call_name_normalizer_middleware([])
    request = SimpleNamespace(tools=[SimpleNamespace(name="write_todos")])

    async def handler(_request):
        return AIMessage(content="", tool_calls=[{
            "id": "call_x", "name": "write_todos_extra", "args": {},
        }])

    response = await middleware.awrap_model_call(request, handler)

    assert response.tool_calls[0]["name"] == "write_todos_extra"


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
