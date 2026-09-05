"""agent_runtime 运行期辅助函数测试——时间戳注入、模型构造与错误兜底。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from apps.chat_data_app.agent import agent_runtime as ar


def test_format_current_time_for_prompt_includes_weekday():
    # 2026-08-29 是星期六
    assert ar.format_current_time_for_prompt(datetime(2026, 8, 29, 10, 30, 5)) == (
        "2026-08-29 10:30:05（周六）"
    )


def test_timestamp_message_passthrough_for_empty_message():
    assert ar.timestamp_message(None) is None
    assert ar.timestamp_message("") == ""


def test_timestamp_message_sets_explicit_timestamp():
    message = ar.timestamp_message(AIMessage(content="hi"), timestamp="2026-01-01 00:00:00")
    assert message.additional_kwargs["timestamp"] == "2026-01-01 00:00:00"


def test_timestamp_message_falls_back_to_now():
    message = ar.timestamp_message(AIMessage(content="hi"))
    assert len(message.additional_kwargs["timestamp"]) == len("2026-01-01 00:00:00")


def test_timestamp_message_swallows_write_failure():
    """消息对象不接受属性写入时只告警，不影响主流程。"""

    class ReadOnlyMessage:
        @property
        def additional_kwargs(self):
            return {}

    message = ReadOnlyMessage()
    assert ar.timestamp_message(message) is message


def test_create_streaming_chat_model_builds_kwargs():
    captured: dict = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    ar.create_streaming_chat_model(
        model_id="qwen",
        enable_thinking=True,
        thinking_effort="high",
        stream_usage=True,
        llm_factory=_factory,
    )
    assert captured == {
        "model": "qwen",
        "streaming": True,
        "enable_thinking": True,
        "thinking_effort": "high",
        "stream_usage": True,
    }


def test_create_streaming_chat_model_omits_stream_usage_when_disabled():
    captured: dict = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    ar.create_streaming_chat_model(llm_factory=_factory)
    assert "stream_usage" not in captured
    assert captured["streaming"] is True


@pytest.mark.asyncio
async def test_response_timestamp_middleware_timestamps_ai_message():
    middleware = ar.create_response_timestamp_middleware()

    async def _handler(request):
        return AIMessage(content="hi")

    response = await middleware.awrap_model_call(SimpleNamespace(), _handler)
    assert response.additional_kwargs["timestamp"]


@pytest.mark.asyncio
async def test_response_timestamp_middleware_timestamps_result_list():
    middleware = ar.create_response_timestamp_middleware()
    holder = SimpleNamespace(result=[AIMessage(content="a"), AIMessage(content="b")])

    async def _handler(request):
        return holder

    response = await middleware.awrap_model_call(SimpleNamespace(), _handler)
    assert all(message.additional_kwargs["timestamp"] for message in response.result)


@pytest.mark.asyncio
async def test_response_timestamp_middleware_handles_empty_result():
    middleware = ar.create_response_timestamp_middleware()
    holder = SimpleNamespace(result=[])

    async def _handler(request):
        return holder

    response = await middleware.awrap_model_call(SimpleNamespace(), _handler)
    assert response.result == []


def test_tool_error_callback_returns_detail_message():
    assert ar._on_tool_error(RuntimeError("db down"), None) == "工具执行失败: db down"
    assert ar._format_tool_error(ValueError("boom")) == "工具执行失败: boom"
