"""子智能体 run context 透传：身份走 context，不依赖 state 白名单。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from apps.chat_data_app.agent.middleware import subagents as subagents_module
from apps.chat_data_app.agent.middleware.subagents import SubagentSpec
from apps.chat_data_app.tests.unit.subagent_test_support import build_subagent_tool, invoke_subagent_tool


def _spec() -> SubagentSpec:
    return SubagentSpec(
        name="explore",
        description="深度分析子智能体",
        system_prompt="你是分析子智能体",
        tools=[],
        model=object(),
    )


@pytest.mark.asyncio
async def test_subagent_run_passes_run_context_to_subgraph(monkeypatch):
    """身份与主图同源：派发时把 ToolRuntime.context 透传给子图 invoke。"""
    captured = {}

    class FakeRunnable:
        async def astream(self, _state, _config, stream_mode, context=None):
            captured["context"] = context
            yield "updates", {"model": {"messages": [AIMessage(content="ok")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_subagent_tool([_spec()])
    run_context = {"user_id": "u1", "operator_name": "张三"}
    runtime = SimpleNamespace(state={"user_id": "u1"}, tool_call_id="call_1", context=run_context)

    await invoke_subagent_tool(
        tool,
        "dispatch",
        background=False,
        description="分析入金趋势",
        subagent_type="explore",
        runtime=runtime,
    )

    assert captured["context"] == run_context
