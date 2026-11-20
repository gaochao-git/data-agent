"""子智能体沙箱续期测试。"""

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from apps.chat_data_app.agent.middleware.sandbox_renewal import create_sandbox_renewal_middleware
from apps.chat_data_app.agent.middleware.subagents import (
    _SUBAGENT_TOOL_DESCRIPTION_TEMPLATE,
    DispatchArgs,
    _default_system_prompt,
)


def _rendered_tool_description() -> str:
    """按生产装配的真实类型描述渲染，门禁对齐实际注入体积。"""
    from apps.chat_data_app.agent.data_agent import (
        EXPLORE_SUBAGENT_DESCRIPTION,
        GENERAL_SUBAGENT_DESCRIPTION,
    )

    agents = f"- explore: {EXPLORE_SUBAGENT_DESCRIPTION}\n- general: {GENERAL_SUBAGENT_DESCRIPTION}"
    return _SUBAGENT_TOOL_DESCRIPTION_TEMPLATE.replace("{available_agents}", agents)


def test_subagent_description_is_compact_chinese():
    """subagent 是常驻注入工具：按真实类型渲染后也必须收敛，避免挤占上下文。"""
    rendered = _rendered_tool_description()
    assert len(rendered) <= 1400
    for keyword in ("explore", "general", "background", "write_scopes", "dispatch", "send"):
        assert keyword in rendered


def test_subagent_tool_defaults_to_background_execution():
    """默认异步：模型不显式传 background 时走后台任务，同步需显式关闭。"""
    args = DispatchArgs(description="盘点全部模型", subagent_type="explore")
    assert args.background is True
    rendered = _rendered_tool_description()
    assert "background 默认 true" in rendered
    assert "false 同步等待" in rendered


def test_subagent_reuse_guidance_prefers_follow_up_over_new_session():
    """复用策略写在系统提示，工具描述只留参数语法，两处不重复。"""
    prompt = _default_system_prompt().replace("{available_agents}", "- explore: 只读")
    assert 'subagent({"send":' in prompt
    assert 'subagent({"manage":{"action":"list"}})' in prompt
    assert "审批用 decide" in prompt
    assert "达到上限后 dispatch 会被拒绝" in prompt


def test_subagent_tool_description_does_not_repeat_system_prompt_policy():
    """工具描述只讲参数机制；任务书、复用与额度策略只写在系统提示里。"""
    prompt = _default_system_prompt().replace("{available_agents}", "- explore: 只读")
    for phrase in ("达到上限后 dispatch 会被拒绝", "present_artifacts", "file_write"):
        assert phrase not in _SUBAGENT_TOOL_DESCRIPTION_TEMPLATE
        assert phrase in prompt


def test_sandbox_renewal_middleware_renews_before_and_after_model():
    """子图 model 调用同样续期：前/后各一次，覆盖子智能体长任务。"""
    calls: list[str] = []

    async def renewal():
        calls.append("renew")

    middleware = create_sandbox_renewal_middleware(renewal)
    request = SimpleNamespace(system_message=None, messages=[], tools=[])

    async def handler(_request):
        return AIMessage(content="ok")

    asyncio.run(middleware.awrap_model_call(request, handler))
    assert len(calls) == 2
