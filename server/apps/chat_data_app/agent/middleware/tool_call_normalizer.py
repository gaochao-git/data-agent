"""Tool call name normalization middleware."""

from typing import Any

from langchain.agents.middleware import wrap_model_call
from langchain_core.messages import AIMessage

from ..agent_runtime import normalize_response_tool_call_names


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
