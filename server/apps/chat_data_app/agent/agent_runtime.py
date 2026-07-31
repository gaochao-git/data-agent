"""Shared runtime helpers for ChatData agents."""

from datetime import datetime
from typing import Any, Callable

from langchain.agents.middleware import wrap_model_call
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from utils.logger import get_logger
from ..services.context_budget import ToolResultBudget
from ..services.llm_config import get_chat_data_llm
from .middleware.tool_error import create_tool_error_middleware
from .middleware.tool_result import create_tool_result_middleware

logger = get_logger(__name__)


def format_current_time_for_prompt(current_time: datetime | None = None) -> str:
    """格式化提示词中的当前时间，附带周几信息。"""
    current = current_time or datetime.now()
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekdays[current.weekday()]
    return f"{current.strftime('%Y-%m-%d %H:%M:%S')}（{weekday}）"


def timestamp_message(message: Any, timestamp: str | None = None) -> Any:
    """给 LangChain 消息补充服务端时间戳，随 checkpoint 一起持久化。"""
    if not message:
        return message
    additional_kwargs = dict(getattr(message, "additional_kwargs", None) or {})
    additional_kwargs["timestamp"] = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        message.additional_kwargs = additional_kwargs
    except Exception:
        logger.warning("消息时间戳写入失败: message=%r", message, exc_info=True)
    return message


def create_streaming_chat_model(
    *,
    model_id: str | None = None,
    enable_thinking: bool | None = None,
    stream_usage: bool = False,
    llm_factory: Callable[..., BaseChatModel] = get_chat_data_llm,
) -> BaseChatModel:
    """创建智能体使用的流式聊天模型。"""
    kwargs = {
        "model": model_id,
        "streaming": True,
        "enable_thinking": enable_thinking,
    }
    if stream_usage:
        kwargs["stream_usage"] = True
    return llm_factory(**kwargs)


def create_response_timestamp_middleware():
    """创建模型响应时间戳 middleware，确保 checkpoint 中助手消息有服务端时间。"""
    @wrap_model_call(name="ResponseTimestampMiddleware")
    async def response_timestamp_middleware(request, handler):
        response = await handler(request)
        if isinstance(response, AIMessage):
            return timestamp_message(response)
        result = list(getattr(response, "result", []) or [])
        if result:
            response.result = [timestamp_message(message) for message in result]
        return response

    return response_timestamp_middleware


def build_standard_agent_middleware(
    tools: list[Any],
    *,
    context_compression_middleware: Any | None = None,
    context_usage_middleware: Any | None = None,
    tool_result_middleware: Any | None = None,
    include_response_timestamp: bool = False,
    include_tool_call_normalizer: bool = False,
) -> list[Any]:
    """组装 create_agent 的公共 middleware 列表。"""
    middleware: list[Any] = []
    if context_compression_middleware is not None:
        middleware.append(context_compression_middleware)
    if context_usage_middleware is not None:
        middleware.append(context_usage_middleware)
    if include_response_timestamp:
        middleware.append(create_response_timestamp_middleware())
    if include_tool_call_normalizer:
        from .middleware.tool_call_normalizer import create_tool_call_name_normalizer_middleware
        middleware.append(create_tool_call_name_normalizer_middleware(tools))
    if tool_result_middleware is not None:
        middleware.append(tool_result_middleware)
    middleware.append(create_tool_error_middleware())
    return middleware


def _get_available_tool_names(tools: list) -> set[str]:
    """提取当前可用工具名称。"""
    return {
        str(name)
        for item in tools or []
        if (name := getattr(item, "name", None) or (item if isinstance(item, str) else None))
    }


def _normalize_repeated_tool_name(tool_name: Any, available_tool_names: set[str]) -> str:
    """修复 OpenAI-compatible 流式工具调用中偶发的重复工具名。"""
    raw_name = str(tool_name or "").strip()
    if not raw_name or raw_name in available_tool_names:
        return raw_name

    for name in available_tool_names:
        repeat_count, remainder = divmod(len(raw_name), len(name))
        if repeat_count > 1 and remainder == 0 and raw_name == name * repeat_count:
            return name

    return raw_name


def normalize_response_tool_call_names(message: Any, tools: list) -> Any:
    """在 tools 节点执行前归一化明显重复的已注册工具名。"""
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return message

    available_tool_names = _get_available_tool_names(tools)
    if not available_tool_names:
        return message

    changed = False
    normalized_tool_calls = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            normalized_tool_calls.append(tool_call)
            continue
        raw_name = tool_call.get("name")
        normalized_name = _normalize_repeated_tool_name(raw_name, available_tool_names)
        if normalized_name != raw_name:
            tool_call = {**tool_call, "name": normalized_name}
            changed = True
        normalized_tool_calls.append(tool_call)

    if not changed:
        return message

    logger.warning("已归一化重复工具调用名称，避免 OpenAI-compatible 流式 chunk 合并异常")
    try:
        message.tool_calls = normalized_tool_calls
        return message
    except Exception:
        if hasattr(message, "model_copy"):
            return message.model_copy(update={"tool_calls": normalized_tool_calls})
        logger.warning("工具调用名称归一化写入失败: message=%r", message, exc_info=True)
        return message
