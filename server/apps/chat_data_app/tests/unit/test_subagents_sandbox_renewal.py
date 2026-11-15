"""子智能体沙箱续期测试。"""

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from apps.chat_data_app.agent.middleware.sandbox_renewal import create_sandbox_renewal_middleware
from apps.chat_data_app.agent.middleware.subagents import (
    _TASK_TOOL_DESCRIPTION_TEMPLATE,
    TaskToolArgs,
    _default_system_prompt,
)


def test_subagent_tool_description_is_compact_chinese():
    """subagent 是常驻注入工具：任务书要求保留，但描述模板不能无限膨胀。"""
    assert len(_TASK_TOOL_DESCRIPTION_TEMPLATE) <= 1200
    for keyword in ("任务书", "explore", "file_write", "present_artifacts"):
        assert keyword in _TASK_TOOL_DESCRIPTION_TEMPLATE


def test_subagent_tool_defaults_to_background_execution():
    """默认异步：模型不显式传 background 时走后台任务，同步需显式关闭。"""
    args = TaskToolArgs(description="盘点全部模型", subagent_type="explore")
    assert args.background is True
    assert "默认异步（background=true）" in _TASK_TOOL_DESCRIPTION_TEMPLATE
    assert "同步（background=false）" in _TASK_TOOL_DESCRIPTION_TEMPLATE


def test_subagent_reuse_guidance_prefers_follow_up_over_new_session():
    """相似任务不做代码硬匹配，由工具描述与系统提示约束主智能体复用子会话。"""
    prompt = _default_system_prompt().format(available_agents="- explore: 只读")
    for text in (prompt, _TASK_TOOL_DESCRIPTION_TEMPLATE):
        assert 'subagent(action="list")' in text
        assert "follow_up" in text
    assert "运行中用 steer" in _TASK_TOOL_DESCRIPTION_TEMPLATE
    assert '仍在执行时用 subagent(action="steer")' in prompt
    assert "达到上限后 start 会被拒绝" in prompt
    assert "达到上限后 start 会被拒绝" in _TASK_TOOL_DESCRIPTION_TEMPLATE


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
