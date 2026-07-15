"""Context usage helpers and middleware for create_agent calls."""

from __future__ import annotations

import json
from typing import Any
import weakref

from langchain.agents.middleware import wrap_model_call
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.config import get_stream_writer
from langgraph.types import Command

from utils.logger import get_logger
from ...services.context_budget import (
    ContextBudget,
    REQUEST_OVERHEAD_TOKENS,
    SYSTEM_PROMPT_PLACEHOLDER,
    ToolResultBudget,
    build_context_usage_payload,
    count_messages_tokens,
    count_tools_tokens,
    estimate_text_tokens,
    estimate_tokens,
    get_model_context_budget,
    sanitize_context_usage_for_response,
)
from ...services.llm_config import get_chat_data_llm, get_default_chat_data_model
from ...services.runtime_config import get_context_edit_config
from ..agent_runtime import normalize_response_tool_call_names, timestamp_message
from .context_editing import edit_messages_for_llm

logger = get_logger(__name__)

_AGENT_CONTEXT_USAGE_MIDDLEWARE: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()


def create_request_context_usage_middleware(
    model_id: str | None = None,
    *,
    tool_result_budget: Any | None = None,
):
    """创建每次模型请求前的当前上下文估算 middleware。"""
    records: list[dict[str, Any]] = []

    @wrap_model_call
    async def request_context_usage_middleware(request, handler):
        final_messages = _build_request_messages(request)
        tools = list(getattr(request, "tools", None) or [])
        effective_model_id = _request_model_name(request) or model_id
        if tool_result_budget is not None:
            system_message = getattr(request, "system_message", None)
            system_prompt = str(getattr(system_message, "content", "") or "")
            tool_result_budget.configure(system_prompt, tools, model_id=effective_model_id)
        message_tokens = count_messages_tokens(final_messages)
        tool_tokens = count_tools_tokens(tools)
        input_tokens = message_tokens + tool_tokens + REQUEST_OVERHEAD_TOKENS
        record = {
            "index": len(records) + 1,
            "model": effective_model_id,
            "input_tokens": input_tokens,
            "message_tokens": message_tokens,
            "tool_schema_tokens": tool_tokens,
            "overhead_tokens": REQUEST_OVERHEAD_TOKENS,
            "estimated": True,
            "message_count": len(final_messages),
            "breakdown": build_request_breakdown(
                messages=final_messages,
                message_tokens=message_tokens,
                tools=tools,
                tools_tokens=tool_tokens,
                overhead_tokens=REQUEST_OVERHEAD_TOKENS,
            ),
        }
        records.append(record)
        response = await handler(request)
        _merge_response_usage(record, response)
        _emit_context_usage(get_context_usage())
        return response

    def get_context_usage() -> dict[str, Any] | None:
        effective_model_id = str(records[-1].get("model") or model_id or "") if records else model_id
        budget = tool_result_budget.budget if tool_result_budget is not None else get_model_context_budget(effective_model_id)
        return build_usage_from_llm_calls(records, budget)

    request_context_usage_middleware.get_context_usage = get_context_usage  # type: ignore[attr-defined]
    request_context_usage_middleware.context_usage_records = records  # type: ignore[attr-defined]
    return request_context_usage_middleware


def create_chat_data_context_usage_middleware(
    tools: list,
    model_id: str | None = None,
    enable_thinking: bool | None = None,
    tool_result_budget: ToolResultBudget | None = None,
    state_schema: Any | None = None,
) -> Any:
    """创建智能问数上下文裁剪和 token usage middleware。"""
    request_budget = tool_result_budget or ToolResultBudget(model_id)

    @wrap_model_call(name="ChatDataContextUsageMiddleware", state_schema=state_schema)
    async def chat_data_context_usage_middleware(request, handler):
        logger.info("Agent 处理用户请求")
        system_message = getattr(request, "system_message", None)
        system_prompt = str(getattr(system_message, "content", "") or "")
        logger.info("[agent] system_prompt 长度: %s 字符", len(system_prompt))

        async def invoke_with_model(effective_model_id: str | None, model_override: Any | None = None):
            request_tools = list(request.tools or tools or [])
            edited_messages, token_usage = _build_chat_data_context_usage(
                system_prompt=system_prompt,
                messages=list(request.messages),
                tools=request_tools,
                model_id=effective_model_id,
                request_budget=request_budget,
            )
            _emit_context_usage(token_usage)
            effective_request = request.override(
                model=model_override or request.model,
                system_message=SystemMessage(content=system_prompt),
                messages=edited_messages,
            )
            response = await handler(effective_request)
            return _timestamp_model_response(response, request_tools), token_usage

        try:
            response, token_usage = await invoke_with_model(model_id)
        except Exception as err:
            if not model_id:
                raise
            fallback_model = get_default_chat_data_model()
            logger.warning("[agent] 指定模型调用失败，回退默认模型: model_id=%s, fallback=%s, err=%s", model_id, fallback_model, err)
            response, token_usage = await invoke_with_model(
                fallback_model,
                get_chat_data_llm(model=fallback_model, streaming=True, enable_thinking=enable_thinking),
            )

        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={"token_usage": token_usage}),
        )

    return chat_data_context_usage_middleware


def estimate_prompt_usage(*, system_prompt: str, messages: list[BaseMessage], model_id: str | None) -> dict[str, Any]:
    """估算主执行 agent 本轮请求的上下文占用。"""
    system_tokens = estimate_text_tokens(system_prompt)
    message_tokens = count_messages_tokens(messages)
    input_tokens = system_tokens + message_tokens + REQUEST_OVERHEAD_TOKENS
    budget = get_model_context_budget(model_id)
    payload = build_context_usage_payload(budget, input_tokens)
    payload.update({
        "input_tokens": input_tokens,
        "output_tokens": 0,
        "total_tokens": input_tokens,
        "estimated": True,
        "breakdown": build_prompt_breakdown(
            system_tokens=system_tokens,
            messages=messages,
            message_tokens=message_tokens,
            measured_input_tokens=input_tokens,
        ),
    })
    return payload


async def invoke_with_context_usage(*, agent: Any, state: dict[str, Any], system_prompt: str, model_id: str | None) -> tuple[Any, dict[str, Any]]:
    """执行主 agent，并返回结果和本次上下文占用。"""
    result = await agent.ainvoke(state)
    transcript_messages = _extract_result_messages(result)
    runtime_usage = _get_agent_context_usage(agent)
    if runtime_usage:
        return result, runtime_usage
    return result, estimate_prompt_usage(
        system_prompt=system_prompt,
        messages=transcript_messages or list(state.get("messages", [])),
        model_id=model_id,
    )


def build_request_breakdown(*, messages: list[BaseMessage], message_tokens: int, tools: list[Any] | None = None, tools_tokens: int, overhead_tokens: int) -> list[dict[str, Any]]:
    """构造当前模型请求上下文拆分。"""
    system_messages = [message for message in messages if getattr(message, "type", "") == "system"]
    non_system_messages = [message for message in messages if getattr(message, "type", "") != "system"]
    system_tokens = count_messages_tokens(system_messages)
    non_system_tokens = max(int(message_tokens or 0) - system_tokens, 0)
    items: list[dict[str, Any]] = []
    if system_messages:
        items.append({
            "key": "system_prompt",
            "label": "系统提示词与数据上下文",
            "tokens": system_tokens,
            "estimated_tokens": system_tokens,
            "estimated": True,
            "description": "按当前项目估算公式统计 system message。",
            "children": build_message_breakdown(system_messages, hide_content=True),
        })
    if tools_tokens:
        items.append({
            "key": "tool_schema",
            "label": "工具定义",
            "tokens": tools_tokens,
            "estimated_tokens": tools_tokens,
            "estimated": True,
            "description": "按当前项目估算公式统计本次请求传给模型的工具 schema。",
            "children": build_tools_breakdown(tools or []),
        })
    if non_system_messages:
        items.append({
            "key": "messages",
            "label": "会话消息与工具上下文",
            "tokens": non_system_tokens,
            "estimated_tokens": non_system_tokens,
            "estimated": True,
            "description": "按当前项目估算公式统计 history、当前输入、AI tool_calls 和工具结果。",
            "children": build_message_breakdown(non_system_messages, start_index=len(system_messages)),
        })
    if overhead_tokens:
        items.append({
            "key": "request_overhead",
            "label": "请求协议开销",
            "tokens": max(int(overhead_tokens), 0),
            "estimated_tokens": max(int(overhead_tokens), 0),
            "estimated": True,
            "description": "模型协议固定开销，已计入本次输入预算。",
        })
    return items


def build_tools_breakdown(tools: list[Any]) -> list[dict[str, Any]]:
    """按工具拆分 schema 估算 token。"""
    items = []
    for index, tool in enumerate(tools or []):
        try:
            payload = tool if isinstance(tool, dict) else convert_to_openai_tool(tool)
        except Exception:
            payload = str(tool)
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        name = ""
        if isinstance(payload, dict):
            function = payload.get("function") if isinstance(payload.get("function"), dict) else {}
            name = str(function.get("name") or payload.get("name") or "")
        items.append({
            "key": f"tool_schema_{index}",
            "label": f"工具定义 · {name or index + 1}",
            "role": "tool_schema",
            "message_index": index,
            "tokens": estimate_tokens(content),
            "estimated_tokens": estimate_tokens(content),
            "estimated": True,
            "content": content,
            "description": content[:120],
        })
    return items


def build_prompt_breakdown(*, system_tokens: int, messages: list[BaseMessage], message_tokens: int, measured_input_tokens: int) -> list[dict[str, Any]]:
    """构造无 request middleware 时的上下文占用拆分。"""
    items = [
        {
            "key": "system_prompt",
            "label": "系统提示词与数据上下文",
            "tokens": max(int(system_tokens or 0), 0),
            "estimated_tokens": max(int(system_tokens or 0), 0),
            "estimated": True,
            "description": "仅统计服务端显式构造的 system prompt 文本，包含角色设定、工具说明、技能说明、数据源/语义层上下文。",
        },
        {
            "key": "messages",
            "label": "会话消息",
            "tokens": max(int(message_tokens or 0), 0),
            "estimated_tokens": max(int(message_tokens or 0), 0),
            "estimated": True,
            "description": "包含传入主执行 agent 的用户、AI、工具等消息。",
            "children": build_message_breakdown(messages, scale=1.0),
        },
    ]
    overhead_tokens = max(int(measured_input_tokens or 0) - max(int(system_tokens or 0), 0) - max(int(message_tokens or 0), 0), 0)
    if overhead_tokens:
        items.append({
            "key": "request_overhead",
            "label": "请求协议开销",
            "tokens": overhead_tokens,
            "estimated_tokens": overhead_tokens,
            "estimated": True,
            "description": "模型协议固定开销，已计入本次输入预算。",
        })
    return items


def build_message_breakdown(
    messages: list[BaseMessage],
    *,
    scale: float = 1.0,
    start_index: int = 0,
    hide_content: bool = False,
) -> list[dict[str, Any]]:
    """按消息拆分估算 token。"""
    items = []
    round_index = 0
    for local_index, message in enumerate(messages or []):
        index = start_index + local_index
        message_type = str(getattr(message, "type", "") or "message")
        redacted = hide_content or message_type == "system"
        content = SYSTEM_PROMPT_PLACEHOLDER if redacted else _message_debug_content(message)
        if message_type == "human":
            round_index += 1
        estimated_tokens = count_messages_tokens([message])
        item = {
            "key": f"message_{index}",
            "label": _message_label(message_type, round_index or 1),
            "role": message_type,
            "round": round_index or 1,
            "message_index": index,
            "tokens": _scale_tokens(estimated_tokens, scale),
            "estimated_tokens": estimated_tokens,
            "estimated": True,
            "content": content,
            "description": content[:120],
        }
        if redacted:
            item["redacted"] = True
        items.append(item)
    return items


def build_usage_from_llm_calls(calls: list[dict[str, Any]], budget: ContextBudget | int) -> dict[str, Any] | None:
    """从单次模型调用记录构造前端 usage payload。"""
    if not calls:
        return None
    current_call = calls[-1]
    current_input_tokens = int(current_call.get("input_tokens") or 0)
    max_input_tokens = max(int(call.get("input_tokens") or 0) for call in calls)
    cumulative_input_tokens = sum(int(call.get("input_tokens") or 0) for call in calls)
    output_tokens = sum(int(call.get("output_tokens") or 0) for call in calls)
    total_tokens = cumulative_input_tokens + output_tokens
    context_budget = budget if isinstance(budget, ContextBudget) else ContextBudget(
        context_window=max(int(budget), 1),
        max_output_tokens=0,
        trigger_ratio=1.0,
        input_budget_tokens=max(int(budget), 1),
    )
    payload = build_context_usage_payload(context_budget, current_input_tokens)
    payload.update({
        "input_tokens": current_input_tokens,
        "current_input_tokens": current_input_tokens,
        "max_input_tokens": max_input_tokens,
        "cumulative_input_tokens": cumulative_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated": True,
        "llm_calls": calls,
        "breakdown": list(current_call.get("breakdown") or []),
    })
    return payload


def bind_context_usage_middleware(agent: Any, middleware: Any) -> None:
    """关联 agent 与 context usage middleware。"""
    try:
        _AGENT_CONTEXT_USAGE_MIDDLEWARE[agent] = middleware
        return
    except TypeError:
        pass
    try:
        setattr(agent, "_chat_data_context_usage_middleware", middleware)
    except Exception:
        return


def _build_chat_data_context_usage(
    *,
    system_prompt: str,
    messages: list[Any],
    tools: list[Any],
    model_id: str | None,
    request_budget: ToolResultBudget,
) -> tuple[list[Any], dict[str, Any]]:
    """裁剪消息并构造当前模型入口 token usage。"""
    budget = request_budget.configure(system_prompt, tools, model_id=model_id)
    edited_messages, context_edit = edit_messages_for_llm(
        messages,
        context_window=budget.context_window,
        config=get_context_edit_config(),
        message_budget_tokens=request_budget.remaining_for_messages(),
    )
    _log_context_editing_if_triggered(context_edit, context_window=budget.context_window, model_id=model_id)
    llm_messages = [SystemMessage(content=system_prompt), *edited_messages]
    message_tokens = count_messages_tokens(llm_messages)
    tool_tokens = count_tools_tokens(tools)
    input_tokens = message_tokens + tool_tokens + REQUEST_OVERHEAD_TOKENS
    token_usage = build_context_usage_payload(budget, input_tokens)
    token_usage.update({
        "input_tokens": input_tokens,
        "current_input_tokens": input_tokens,
        "output_tokens": 0,
        "total_tokens": input_tokens,
        "estimated": True,
        "context_editing": context_edit,
        "breakdown": build_request_breakdown(
            messages=llm_messages,
            message_tokens=message_tokens,
            tools=tools,
            tools_tokens=tool_tokens,
            overhead_tokens=REQUEST_OVERHEAD_TOKENS,
        ),
    })
    return edited_messages, token_usage


def _log_context_editing_if_triggered(context_edit: dict[str, Any], *, context_window: int, model_id: str | None) -> None:
    """上下文编辑实际触发时打印一条摘要日志。"""
    if not context_edit.get("triggered"):
        return
    logger.info(
        "[agent] 上下文编辑已触发: model=%s context_window=%s before=%s after=%s cleared=%s tool_results=%s history_trimmed=%s",
        model_id or "-",
        context_window,
        context_edit.get("estimated_tokens_before"),
        context_edit.get("estimated_tokens_after"),
        context_edit.get("estimated_cleared_tokens"),
        context_edit.get("cleared_tool_results"),
        context_edit.get("history_trimmed"),
    )


def _emit_context_usage(usage: dict[str, Any] | None) -> None:
    """通过 LangGraph custom stream 输出模型入口上下文占用。"""
    if not usage:
        return
    try:
        get_stream_writer()({"type": "token_usage", "usage": sanitize_context_usage_for_response(usage)})
    except Exception:
        logger.debug("当前执行上下文不支持推送 token_usage custom 事件", exc_info=True)


def _timestamp_model_response(response: ModelResponse, tools: list[Any]) -> ModelResponse:
    """归一化模型响应并写入服务端时间戳。"""
    normalized_result = [
        timestamp_message(normalize_response_tool_call_names(message, tools))
        for message in list(getattr(response, "result", []) or [])
    ]
    return ModelResponse(
        result=normalized_result,
        structured_response=response.structured_response,
    )


def _build_request_messages(request: Any) -> list[BaseMessage]:
    messages = []
    system_message = getattr(request, "system_message", None)
    if isinstance(system_message, BaseMessage):
        messages.append(system_message)
    messages.extend(message for message in list(getattr(request, "messages", None) or []) if isinstance(message, BaseMessage))
    return messages


def _request_model_name(request: Any) -> str | None:
    model = getattr(request, "model", None)
    for attr in ("model_name", "model", "model_id"):
        value = getattr(model, attr, None)
        if value:
            return str(value)
    return None


def _merge_response_usage(record: dict[str, Any], response: Any) -> None:
    message = None
    if isinstance(response, AIMessage):
        message = response
    else:
        result = getattr(response, "result", None)
        if isinstance(result, list) and result and isinstance(result[0], AIMessage):
            message = result[0]
    usage = getattr(message, "usage_metadata", None) if message is not None else None
    if isinstance(usage, dict):
        record["actual_input_tokens"] = int(usage.get("input_tokens") or 0)
        record["output_tokens"] = int(usage.get("output_tokens") or 0)
        record["actual_total_tokens"] = int(usage.get("total_tokens") or (record["actual_input_tokens"] + record["output_tokens"]))


def _get_agent_context_usage(agent: Any) -> dict[str, Any] | None:
    try:
        middleware = _AGENT_CONTEXT_USAGE_MIDDLEWARE.get(agent)
    except TypeError:
        middleware = None
    if middleware is None:
        middleware = getattr(agent, "_chat_data_context_usage_middleware", None)
    getter = getattr(middleware, "get_context_usage", None)
    if callable(getter):
        return getter()
    return None


def _extract_result_messages(result: Any) -> list[BaseMessage]:
    if isinstance(result, dict) and isinstance(result.get("messages"), list):
        return [message for message in result.get("messages") or [] if isinstance(message, BaseMessage)]
    return []


def _message_debug_content(message: BaseMessage) -> str:
    parts = []
    content = getattr(message, "content", "") or ""
    if content:
        parts.append(str(content))
    if isinstance(message, AIMessage):
        tool_calls = getattr(message, "tool_calls", None) or []
        if tool_calls:
            parts.append("tool_calls:\n" + json.dumps(tool_calls, ensure_ascii=False, indent=2, default=str))
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    raw_tool_calls = additional_kwargs.get("tool_calls")
    if raw_tool_calls:
        parts.append("raw_tool_calls:\n" + json.dumps(raw_tool_calls, ensure_ascii=False, indent=2, default=str))
    return "\n\n".join(parts)


def _message_label(message_type: str, round_index: int) -> str:
    labels = {
        "human": "用户消息",
        "ai": "AI消息",
        "system": "系统消息",
        "tool": "工具结果",
    }
    return f"第 {round_index} 轮 · {labels.get(message_type, message_type or '消息')}"


def _scale_tokens(tokens: int, scale: float) -> int:
    return max(int(round(max(int(tokens or 0), 0) * scale)), 0)
