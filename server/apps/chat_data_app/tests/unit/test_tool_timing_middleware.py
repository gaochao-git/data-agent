import os
import sys
import time
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from apps.chat_data_app.agent.middleware.tool_timing import create_tool_timing_middleware
from apps.chat_data_app.agent.tool_execution_timing import (
    begin_tool_execution_clock,
    mark_tool_execution_started,
    reset_tool_execution_clock,
)
from apps.chat_data_app.agent.workspace.workspace_sandbox_api import InstrumentedOperationLock


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


@pytest.mark.asyncio
async def test_command_result_tool_message_is_stamped_with_execution_timing():
    """返回 Command 的工具（task/子智能体）同样盖上起止时间，历史回放才有总耗时。"""
    middleware = create_tool_timing_middleware()
    message = ToolMessage(content="子智能体报告", name="task", tool_call_id="call-1")

    async def handler(_request):
        return Command(update={"messages": [message]})

    result = await middleware.awrap_tool_call(_request(), handler)

    assert isinstance(result, Command)
    stamped = result.update["messages"][0]
    assert isinstance(stamped.additional_kwargs["started_at_ms"], int)
    assert stamped.additional_kwargs["ended_at_ms"] >= stamped.additional_kwargs["started_at_ms"]


@pytest.mark.asyncio
async def test_middleware_prefers_real_execution_start_over_wrapper_start():
    """排队等待后的真实执行起点覆盖 wrapper 开始时间。"""
    middleware = create_tool_timing_middleware()
    marked_at_ms = 0

    async def handler(_request):
        nonlocal marked_at_ms
        marked_at_ms = int(time.time() * 1000)
        mark_tool_execution_started(marked_at_ms)
        return ToolMessage(content="结果", name="file_read", tool_call_id="call-1")

    result = await middleware.awrap_tool_call(_request(), handler)

    assert result.additional_kwargs["started_at_ms"] == marked_at_ms


@pytest.mark.asyncio
async def test_operation_lock_marks_execution_clock_after_acquiring():
    clock, token = begin_tool_execution_clock()
    try:
        lock = InstrumentedOperationLock()
        assert clock.execution_started_at_ms is None
        async with lock:
            assert isinstance(clock.execution_started_at_ms, int)
    finally:
        reset_tool_execution_clock(token)
