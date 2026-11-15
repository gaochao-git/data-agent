"""子智能体沙箱续期测试。"""

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from apps.chat_data_app.agent.middleware.sandbox_renewal import create_sandbox_renewal_middleware
from apps.chat_data_app.agent.middleware.subagents import _TASK_TOOL_DESCRIPTION_TEMPLATE


def test_task_tool_description_is_compact_chinese():
    """task 是常驻注入工具：任务书要求保留，但描述模板不能无限膨胀。"""
    assert len(_TASK_TOOL_DESCRIPTION_TEMPLATE) <= 1200
    for keyword in ("任务书", "explore", "file_write", "present_artifacts"):
        assert keyword in _TASK_TOOL_DESCRIPTION_TEMPLATE


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
