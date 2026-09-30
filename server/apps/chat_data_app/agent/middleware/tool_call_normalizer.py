"""Tool call name/args normalization middleware."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import ModelCallResult, ModelRequest, wrap_model_call
from langchain_core.messages import AIMessage

from utils.logger import get_logger
from utils.tool_call_args import coerce_tool_call_args

logger = get_logger(__name__)


def _get_available_tool_names(tools: list) -> set[str]:
    """提取当前可用工具名称。"""
    return {
        str(name)
        for item in tools or []
        if (name := getattr(item, "name", None) or (item if isinstance(item, str) else None))
    }


def _is_exact_repeat_of(candidate_name: str, tool_name: str) -> bool:
    """判断候选名是否为某工具名的整数倍（>1）精确重复。"""
    if not tool_name:
        return False
    repeat_count, remainder = divmod(len(candidate_name), len(tool_name))
    return repeat_count > 1 and remainder == 0 and candidate_name == tool_name * repeat_count


def _normalize_repeated_tool_name(tool_name: Any, available_tool_names: set[str]) -> str:
    """修复 OpenAI-compatible 流式工具调用中偶发的重复工具名。

    成因不是模型输出了带分隔符的名字，而是流式协议里同一次工具调用的多段
    ``tool_call delta`` 各自带了 ``function.name``，聚合时被逐段拼接，所以待修形态
    恒为该工具名的整数倍重复（``write_todos`` -> ``write_todoswrite_todos``）。

    因此只接受「候选名整数倍精确重复」这一种形态，不做前缀或相似度猜测：猜错会把
    调用打到错误的工具上，比报错更难排查。多个候选同时命中时取最长者，避免候选互为
    前缀时结果随 set 迭代顺序漂移。
    """
    raw_name = str(tool_name or "").strip()
    if not raw_name or raw_name in available_tool_names:
        return raw_name

    matched = [name for name in available_tool_names if _is_exact_repeat_of(raw_name, name)]
    if matched:
        return max(matched, key=len)
    return raw_name


def normalize_response_tool_call_names(message: Any, tools: list) -> Any:
    """在 tools 节点执行前归一化工具调用：重复工具名 + 字符串形态的 args。

    args 规整逻辑统一收口在 ``utils.tool_call_args.coerce_tool_call_args``
    （handlers 直播侧与回放侧共用同一实现）。
    """
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return message

    available_tool_names = _get_available_tool_names(tools)
    changed = False
    normalized_tool_calls = []
    for tool_call in tool_calls:
        normalized_tool_call, call_changed = _normalize_single_tool_call(tool_call, available_tool_names)
        if call_changed:
            changed = True
        normalized_tool_calls.append(normalized_tool_call)

    if not changed:
        return message

    logger.warning("已归一化模型返回的工具调用（重复名称/字符串参数），避免下游解析与执行异常")
    try:
        message.tool_calls = normalized_tool_calls
        return message
    except Exception:
        if hasattr(message, "model_copy"):
            return message.model_copy(update={"tool_calls": normalized_tool_calls})
        logger.warning("工具调用归一化写入失败: message=%r", message, exc_info=True)
        return message


def _normalize_single_tool_call(tool_call: Any, available_tool_names: set[str]) -> tuple[Any, bool]:
    """归一化单条工具调用（重复名 + 字符串 args），返回 (调用, 是否变更)。"""
    if not isinstance(tool_call, dict):
        return tool_call, False
    raw_name = tool_call.get("name")
    changed = False
    if available_tool_names:
        normalized_name = _normalize_repeated_tool_name(raw_name, available_tool_names)
        if normalized_name != raw_name:
            tool_call = {**tool_call, "name": normalized_name}
            changed = True
    raw_args = tool_call.get("args")
    if not isinstance(raw_args, dict):
        coerced_args = coerce_tool_call_args(raw_args)
        if coerced_args is not None:
            tool_call = {**tool_call, "args": coerced_args}
            changed = True
    return tool_call, changed


def create_tool_call_name_normalizer_middleware(
    tools: list[Any],
) -> Callable[[ModelRequest], Awaitable[ModelCallResult]]:
    """创建模型工具调用名称归一化 middleware。

    Args:
        tools: 装配期已知的工具列表，仅作为 ``request.tools`` 缺失时的兜底。
    """

    def _candidate_tools(request: ModelRequest) -> list[Any]:
        """本轮模型真正可见的工具集。

        必须取 ``request.tools``：``write_todos``、``task`` 等由 ``middleware.tools``
        注册，不在业务 ``tools`` 静态列表里；拿静态列表当候选名，这些工具的重复名就
        修不回来（内网 vLLM/GLM 实际踩过）。``request.tools`` 含全部已注册工具，也会
        随中间件的动态增删同步变化，才是模型输出名称唯一对应的集合。
        """
        return list(getattr(request, "tools", None) or []) or tools

    @wrap_model_call(name="ToolCallNameNormalizerMiddleware")
    async def normalize_tool_call_name_middleware(
        request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelCallResult]]
    ) -> ModelCallResult:
        candidate_tools = _candidate_tools(request)
        response = await handler(request)
        if isinstance(response, AIMessage):
            return normalize_response_tool_call_names(response, candidate_tools)
        result = list(getattr(response, "result", []) or [])
        if not result:
            return response
        normalized_result = [normalize_response_tool_call_names(message, candidate_tools) for message in result]
        response.result = normalized_result
        return response

    return normalize_tool_call_name_middleware
