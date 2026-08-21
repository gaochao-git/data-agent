"""Tool call name normalization middleware."""

from typing import Any

from langchain.agents.middleware import wrap_model_call
from langchain_core.messages import AIMessage

from utils.logger import get_logger

logger = get_logger(__name__)


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


def create_tool_call_name_normalizer_middleware(tools: list[Any]):
    """创建模型工具调用名称归一化 middleware。"""
    @wrap_model_call(name="ToolCallNameNormalizerMiddleware")
    async def normalize_tool_call_name_middleware(request, handler):
        response = await handler(request)
        if isinstance(response, AIMessage):
            return normalize_response_tool_call_names(response, tools)
        result = list(getattr(response, "result", []) or [])
        if not result:
            return response
        normalized_result = [
            normalize_response_tool_call_names(message, tools)
            for message in result
        ]
        response.result = normalized_result
        return response

    return normalize_tool_call_name_middleware
