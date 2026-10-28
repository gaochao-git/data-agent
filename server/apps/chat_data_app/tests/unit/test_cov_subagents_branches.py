"""subagents 残余分支补测：流式过滤 flush、摘要注入兜底、编译围栏与消息记账。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from apps.chat_data_app.agent.context import context_budget as cb
from apps.chat_data_app.agent.middleware import subagents as sm
from apps.chat_data_app.agent.middleware.subagents import (
    SubagentSpec,
    _StreamingToolCallMarkupFilter,
    _subagent_report_token_limit,
    _SubagentStreamState,
    build_task_tool,
)


def _spec(name="data-explore", **overrides):
    kwargs = {
        "name": name,
        "description": "深度分析子智能体",
        "system_prompt": "你是分析子智能体",
        "tools": [],
        "model": object(),
    }
    kwargs.update(overrides)
    return SubagentSpec(**kwargs)


def test_stream_filter_flush_drops_exact_opener_pending():
    markup_filter = _StreamingToolCallMarkupFilter()

    assert markup_filter.feed("<tool_call") == ""
    assert markup_filter.flush() == ""


def test_budget_alert_keeps_messages_when_summary_builder_returns_none(monkeypatch):
    monkeypatch.setattr(
        sm,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(compression_trigger_tokens=10**9),
    )
    monkeypatch.setattr(cb, "build_runtime_context_summary_message", lambda _summary: None)
    middleware = sm.create_subagent_budget_alert_middleware(_spec(), [])
    seen = {}

    def handler(request):
        seen["request"] = request
        return "done"

    request = SimpleNamespace(
        messages=[AIMessage(content="短任务")],
        state={"context_summary": "上一轮摘要"},
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    assert middleware.wrap_model_call(request, handler) == "done"
    assert seen["request"] is request


def test_compile_subagent_appends_fence_when_run_controls_complete(monkeypatch):
    captured = {}
    monkeypatch.setattr(sm, "_subagent_system_prompt", lambda spec: "系统提示词")
    monkeypatch.setattr(sm, "create_context_compression_middleware", lambda **kwargs: "compression")
    monkeypatch.setattr(sm, "create_subagent_budget_alert_middleware", lambda *a, **k: "budget")
    monkeypatch.setattr(sm, "SkillPrerequisiteMiddleware", lambda *_a: "skill")
    monkeypatch.setattr(sm, "create_tool_call_name_normalizer_middleware", lambda _tools: "normalizer")
    monkeypatch.setattr(sm, "create_tool_result_middleware", lambda: "result")
    monkeypatch.setattr(sm, "ToolErrorMiddleware", lambda *_a: "error")
    monkeypatch.setattr(
        sm, "create_tool_execution_fence_middleware", lambda **kwargs: captured.setdefault("fence", kwargs)
    )
    monkeypatch.setattr(sm, "create_agent", lambda model, **kwargs: captured.setdefault("agent", kwargs))
    run_controls = SimpleNamespace(
        is_complete=lambda: True,
        run_id="run-1",
        owner_token="token",
        inbox_service="inbox",
    )

    sm._compile_subagent(_spec(), run_controls=run_controls)

    assert captured["fence"] == {"run_id": "run-1", "owner_token": "token", "inbox_service": "inbox"}
    assert captured["fence"] in captured["agent"]["middleware"]


def test_collect_update_skips_duplicate_message_in_same_removal_update():
    state = _SubagentStreamState("task-1")
    first = AIMessage(content="第一条", id="a1")
    duplicate = AIMessage(content="重复", id="a2")
    state.collect_update_messages({"model": {"messages": [first]}})

    new_messages = state.collect_update_messages(
        {
            "model": {
                "messages": [
                    sm.RemoveMessage(id="a-missing"),
                    duplicate,
                    duplicate,
                ]
            }
        }
    )

    assert new_messages == [duplicate]
    assert state.collected == [first, duplicate]


def test_collect_update_full_reset_emits_only_new_message():
    state = _SubagentStreamState("task-1")
    kept = AIMessage(content="保留", id="a1")
    fresh = AIMessage(content="新增", id="a2")
    state.collect_update_messages({"model": {"messages": [kept]}})

    new_messages = state.collect_update_messages(
        {"compression": {"messages": [sm.RemoveMessage(id=REMOVE_ALL_MESSAGES), kept, fresh]}}
    )

    assert new_messages == [fresh]
    assert state.collected == [kept, fresh]


def test_subagent_report_token_limit_returns_none_for_zero_hard_limit():
    runtime = SimpleNamespace(state={"token_usage": {"hard_input_limit_tokens": 0}})

    assert _subagent_report_token_limit(runtime) is None


@pytest.mark.asyncio
async def test_task_tool_without_usage_meta_omits_context_usage_snapshot(monkeypatch):
    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="分析完成")]}}

    monkeypatch.setattr(sm, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    monkeypatch.setattr(sm, "_safe_subagent_budget_meta", lambda *a, **k: None)
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        description="分析",
        subagent_type="data-explore",
        runtime=SimpleNamespace(state={"user_id": "u1"}, tool_call_id="call-1"),
    )

    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.content == "分析完成"
    assert "subagent_context_usage" not in message.additional_kwargs
