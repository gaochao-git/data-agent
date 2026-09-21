import os
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from apps.chat_data_app.agent.middleware.tool_timing import create_tool_timing_middleware


def _request():
    return SimpleNamespace(tool_call={"id": "call-1", "name": "run_sql", "args": {}})


@pytest.mark.asyncio
async def test_successful_tool_message_is_stamped_with_execution_timing():
    middleware = create_tool_timing_middleware()

    async def handler(_request):
        return ToolMessage(content="结果", name="run_sql", tool_call_id="call-1")

    result = await middleware.awrap_tool_call(_request(), handler)

    started_at_ms = result.additional_kwargs["started_at_ms"]
    ended_at_ms = result.additional_kwargs["ended_at_ms"]
    assert isinstance(started_at_ms, int)
    assert isinstance(ended_at_ms, int)
    assert ended_at_ms >= started_at_ms


@pytest.mark.asyncio
async def test_error_tool_message_is_stamped_with_execution_timing():
    """经 ToolErrorMiddleware 转换后的失败 ToolMessage 同样盖上起止时间。"""
    middleware = create_tool_timing_middleware()

    async def handler(_request):
        return ToolMessage(content="工具执行失败，请稍后重试", name="run_sql", tool_call_id="call-1", status="error")

    result = await middleware.awrap_tool_call(_request(), handler)

    assert result.status == "error"
    assert "started_at_ms" in result.additional_kwargs
    assert "ended_at_ms" in result.additional_kwargs


@pytest.mark.asyncio
async def test_non_tool_message_result_passes_through_unchanged():
    middleware = create_tool_timing_middleware()
    sentinel = object()

    async def handler(_request):
        return sentinel

    assert await middleware.awrap_tool_call(_request(), handler) is sentinel
