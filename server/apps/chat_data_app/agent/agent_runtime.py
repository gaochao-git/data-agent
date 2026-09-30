"""Shared runtime helpers for ChatData agents."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallResult,
    ModelRequest,
    TodoListMiddleware,
    ToolErrorMiddleware,
    wrap_model_call,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from utils.logger import get_logger

from ..services.llm_config import get_chat_data_llm
from .middleware.tool_call_normalizer import (
    create_tool_call_name_normalizer_middleware,
    normalize_response_tool_call_names,  # noqa: F401  向后兼容 re-export，原定义已下沉至 middleware
)
from .middleware.tool_call_rate_alert import create_tool_call_rate_alert_middleware
from .middleware.tool_timing import create_tool_timing_middleware

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
    thinking_effort: str | None = None,
    stream_usage: bool = True,
    llm_factory: Callable[..., BaseChatModel] = get_chat_data_llm,
) -> BaseChatModel:
    """创建智能体使用的流式聊天模型。

    stream_usage 默认 True：langchain-openai 的流式请求不显式声明就不返回
    token usage（静默丢失，极难排查），故除显式关闭外一律请求。
    """
    kwargs = {
        "model": model_id,
        "streaming": True,
        "enable_thinking": enable_thinking,
        "thinking_effort": thinking_effort,
    }
    if stream_usage:
        kwargs["stream_usage"] = True
    return llm_factory(**kwargs)


def create_response_timestamp_middleware() -> AgentMiddleware:
    """创建模型响应时间戳 middleware，确保 checkpoint 中助手消息有服务端时间。"""

    @wrap_model_call(name="ResponseTimestampMiddleware")
    async def response_timestamp_middleware(
        request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelCallResult]]
    ) -> ModelCallResult:
        response = await handler(request)
        if isinstance(response, AIMessage):
            return timestamp_message(response)
        result = list(getattr(response, "result", []) or [])
        if result:
            # ExtendedModelResponse 无 result 属性；能进入本分支说明 getattr 已取到
            # 非空 result，response 必为 ModelResponse。
            response.result = [timestamp_message(message) for message in result]  # type: ignore[union-attr]
        return response

    return response_timestamp_middleware


_PRE_VALIDATION_ERRORS = (ValueError, FileNotFoundError, IsADirectoryError, NotADirectoryError)


def _format_tool_error(exc: Exception, tool_name: str = "") -> str:
    """记录日志并向模型和前端返回带异常详情的错误提示。

    这里是工具失败日志的**唯一权威记录点**（工具层只 raise，不重复记日志），
    预期/非预期异常的分级判定也只在本处维护。异常详情对模型自愈（纠正参数后
    重试）必要；成败信号由 ToolMessage.status 显式承载，content 只负责让人和
    模型理解发生了什么。
    """
    label = f"[{tool_name}] " if tool_name else ""
    if isinstance(exc, _PRE_VALIDATION_ERRORS):
        logger.warning("[tools] %s工具执行失败(预期校验错误): %s", label, exc)
    else:
        logger.error("[tools] %s工具执行异常: %s", label, exc, exc_info=True)
    return f"工具执行失败: {exc}"


def on_tool_error(exc: Exception, request: Any) -> str:
    """官方 ``ToolErrorMiddleware`` 回调：把异常转为带详情的 error ``ToolMessage``。

    公开函数：子图中间件（middleware/subagents.py）与主图共用同一兜底。
    """
    tool_name = ""
    try:
        tool = getattr(request, "tool", None)
        tool_name = str(getattr(tool, "name", "") or "") or str(
            (getattr(request, "tool_call", None) or {}).get("name") or ""
        )
    except Exception as exc:
        # 工具名提取失败不能阻断错误兜底,降级为无名工具继续格式化错误消息。
        logger.warning("工具错误回调提取工具名失败,按无名工具兜底: error=%s", exc)
        tool_name = ""
    # 此处 exc 是本函数形参（except 块内同名绑定仅块内存活），无删除变量问题。
    return _format_tool_error(exc, tool_name)  # type: ignore[misc]


# 中间件规范顺序（外层 -> 内层，按注册名）。新增 middleware 必须先在此表登记
# 位置并写明原因；tests/unit/test_middleware_ordering.py 枚举全部启用组合，
# 校验实际组装结果与该表逐一致——插错位置会在 CI 直接失败，而不是靠 review 发现。
MIDDLEWARE_CANONICAL_ORDER: tuple[str, ...] = (
    "ToolExecutionFenceMiddleware",  # 租约围栏必须最外层：所有权丢失不能被内层转成普通工具失败
    "ImageInjectionMiddleware",  # 图片注入在压缩/预算外层：注入的图片要被正确计费与统计
    "MCPToolDiscoveryMiddleware",  # MCP 按需发现是唯一会过滤 request.tools 的中间件，必须外于压缩/用量统计，使其看到过滤后的真实工具集
    "ToolTimingMiddleware",  # 紧贴围栏内侧：计时覆盖卸载与错误转换在内的完整工具链路
    "ToolCallRateAlertMiddleware",  # 与计时同层：只统计通过围栏的真实调用，被拒绝的调用不计入频次
    "ToolCallHistoryRepairMiddleware",  # 历史修复先于 steering / 压缩消费消息
    "SteeringMiddleware",  # 在压缩之前注入插话，避免被摘要吞掉
    "SubagentMiddleware",  # 派发决策先于任务规划：task 工具注册 + 子智能体使用说明追加进系统提示
    "TodoListMiddleware",  # 复杂分析的任务分解规划（langchain 官方）：todos 存 state 随 checkpoint 持久，压缩不丢
    "AfterModelHistoryMiddleware",  # 当前唯一的 after_model；再加第二个时注意 after_model 按列表逆序执行
    "ContextCompressionMiddleware",
    "ContextUsageMiddleware",
    "ResponseTimestampMiddleware",
    "ToolCallNameNormalizerMiddleware",
    "ToolResultMiddleware",
    "ToolErrorMiddleware",  # 恒为最内层兜底
)


def build_standard_agent_middleware(
    tools: list[Any],
    *,
    context_compression_middleware: Any | None = None,
    context_usage_middleware: Any | None = None,
    tool_result_middleware: Any | None = None,
    tool_call_history_repair_middleware: Any | None = None,
    tool_execution_fence_middleware: Any | None = None,
    image_injection_middleware: Any | None = None,
    steering_middleware: Any | None = None,
    subagent_middleware: Any | None = None,
    after_model_history_middleware: Any | None = None,
    mcp_tool_discovery_middleware: Any | None = None,
    include_response_timestamp: bool = False,
    include_tool_call_normalizer: bool = False,
) -> list[Any]:
    """组装 create_agent 的公共 middleware 列表。

    顺序由 ``MIDDLEWARE_CANONICAL_ORDER`` 唯一决定：本函数只负责"按注册名
    建槽位、按权威顺序表出列"，不出现任何位置性的 if/append——新增中间件
    只需在顺序表登记并在下方槽位表提供实例，位置写错在类型上即不可能。

    注意各类 hook 的执行语义与列表位置的关系（LangChain create_agent）：
    wrap_tool_call / wrap_model_call 先进为最外层；before_model 按列表顺序执行；
    after_model 按列表**逆序**执行——将来若加入第二个 after_model middleware，
    列表顺序与执行顺序相反，不能按直觉排列。
    """
    # 计时与 ToolErrorMiddleware 是无条件必备项，由本组装点直接创建，
    # 不走调用方注入；其余 middleware 均由调用方按需注入（None = 不启用）。
    slots: dict[str, Any] = {
        "ToolExecutionFenceMiddleware": tool_execution_fence_middleware,
        "ImageInjectionMiddleware": image_injection_middleware,
        "MCPToolDiscoveryMiddleware": mcp_tool_discovery_middleware,
        "ToolTimingMiddleware": create_tool_timing_middleware(),
        "ToolCallRateAlertMiddleware": create_tool_call_rate_alert_middleware(),
        "ToolCallHistoryRepairMiddleware": tool_call_history_repair_middleware,
        "SteeringMiddleware": steering_middleware,
        "SubagentMiddleware": subagent_middleware,
        "TodoListMiddleware": TodoListMiddleware(),
        "AfterModelHistoryMiddleware": after_model_history_middleware,
        "ContextCompressionMiddleware": context_compression_middleware,
        "ContextUsageMiddleware": context_usage_middleware,
        "ResponseTimestampMiddleware": (create_response_timestamp_middleware() if include_response_timestamp else None),
        "ToolCallNameNormalizerMiddleware": (
            create_tool_call_name_normalizer_middleware(tools) if include_tool_call_normalizer else None
        ),
        "ToolResultMiddleware": tool_result_middleware,
        "ToolErrorMiddleware": ToolErrorMiddleware(on_tool_error),
    }
    return [slots[name] for name in MIDDLEWARE_CANONICAL_ORDER if slots[name] is not None]
