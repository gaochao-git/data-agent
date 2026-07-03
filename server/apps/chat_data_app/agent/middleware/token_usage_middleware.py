"""Token usage tracking for DataAnalyze agent calls."""

from __future__ import annotations

import json
import re
from typing import Any
import weakref

from langchain.agents.middleware import wrap_model_call
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from ...services.runtime_config import get_context_window

_AGENT_TOKEN_USAGE_MIDDLEWARE: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()


def count_messages_tokens(messages: list[BaseMessage]) -> int:
    """估算 LangChain messages 的 token 数。"""
    return sum(_message_tokens(message) for message in messages or [])


def estimate_tokens(text: str) -> int:
    """按当前项目约定估算 Qwen / DeepSeek token 数。"""
    text = str(text or "")
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    en_words = len(re.findall(r"[a-zA-Z0-9]+", re.sub(r"[\u4e00-\u9fff]", " ", text)))
    other_chars = len(re.findall(r"[^\w\s\u4e00-\u9fff]", text))
    return int(zh_chars * 0.7 + en_words * 1.3 + other_chars * 0.5)


def count_tools_tokens(tools: list[Any] | None) -> int:
    """估算工具 schema 的 token 数。"""
    total = 0
    for tool in tools or []:
        try:
            payload = tool if isinstance(tool, dict) else convert_to_openai_tool(tool)
        except Exception:
            payload = str(tool)
        total += estimate_tokens(json.dumps(payload, ensure_ascii=False, default=str))
    return int(total)


def estimate_text_tokens(text: str) -> int:
    """估算普通文本的 token 数。"""
    if not text:
        return 0
    return count_messages_tokens([HumanMessage(content=text)])


def build_token_usage_payload(*, used: int, total: int) -> dict[str, Any]:
    """构造前端展示用 usage payload。"""
    safe_total = max(int(total or 0), 1)
    safe_used = max(int(used or 0), 0)
    return {
        "used": safe_used,
        "total": safe_total,
        "percentage": round((safe_used / safe_total) * 100, 1),
        "remaining": max(safe_total - safe_used, 0),
    }


def create_request_token_usage_middleware(model_id: str | None = None):
    """创建每次模型请求前的当前上下文估算 middleware。"""
    records: list[dict[str, Any]] = []

    @wrap_model_call
    async def request_token_usage_middleware(request, handler):
        final_messages = _build_request_messages(request)
        tools = list(getattr(request, "tools", None) or [])
        message_tokens = count_messages_tokens(final_messages)
        tool_tokens = count_tools_tokens(tools)
        input_tokens = message_tokens + tool_tokens + 3
        record = {
            "index": len(records) + 1,
            "model": model_id or _request_model_name(request),
            "input_tokens": input_tokens,
            "message_tokens": message_tokens,
            "tool_schema_tokens": tool_tokens,
            "overhead_tokens": 3,
            "estimated": True,
            "message_count": len(final_messages),
            "breakdown": build_request_breakdown(
                messages=final_messages,
                message_tokens=message_tokens,
                tools=tools,
                tools_tokens=tool_tokens,
                overhead_tokens=3,
            ),
        }
        records.append(record)
        response = await handler(request)
        _merge_response_usage(record, response)
        return response

    def get_token_usage() -> dict[str, Any] | None:
        return build_usage_from_llm_calls(records, get_context_window(model_id))

    request_token_usage_middleware.get_token_usage = get_token_usage  # type: ignore[attr-defined]
    request_token_usage_middleware.token_usage_records = records  # type: ignore[attr-defined]
    return request_token_usage_middleware


def estimate_prompt_usage(*, system_prompt: str, messages: list[BaseMessage], model_id: str | None) -> dict[str, Any]:
    """估算主执行 agent 本轮请求的上下文占用。"""
    system_tokens = estimate_text_tokens(system_prompt)
    message_tokens = count_messages_tokens(messages)
    input_tokens = system_tokens + message_tokens
    context_window = get_context_window(model_id)
    payload = build_token_usage_payload(used=input_tokens, total=context_window)
    payload.update({
        "input_tokens": input_tokens,
        "output_tokens": 0,
        "total_tokens": input_tokens,
        "context_window": context_window,
        "estimated": True,
        "breakdown": build_prompt_breakdown(
            system_tokens=system_tokens,
            messages=messages,
            message_tokens=message_tokens,
            measured_input_tokens=input_tokens,
        ),
    })
    return payload


async def invoke_with_token_usage(*, agent: Any, state: dict[str, Any], system_prompt: str, model_id: str | None) -> tuple[Any, dict[str, Any]]:
    """执行主 agent，并返回结果和本次 token usage。"""
    result = await agent.ainvoke(state)
    transcript_messages = _extract_result_messages(result)
    runtime_usage = _get_agent_token_usage(agent)
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
            "children": build_message_breakdown(system_messages),
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
    _ = overhead_tokens
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
    _ = measured_input_tokens
    return [
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


def build_message_breakdown(messages: list[BaseMessage], *, scale: float = 1.0, start_index: int = 0) -> list[dict[str, Any]]:
    """按消息拆分估算 token。"""
    items = []
    round_index = 0
    for local_index, message in enumerate(messages or []):
        index = start_index + local_index
        content = _message_debug_content(message)
        message_type = str(getattr(message, "type", "") or "message")
        if message_type == "human":
            round_index += 1
        estimated_tokens = count_messages_tokens([message])
        items.append({
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
        })
    return items


def _scale_tokens(tokens: int, scale: float) -> int:
    return max(int(round(max(int(tokens or 0), 0) * scale)), 0)


def build_usage_from_llm_calls(calls: list[dict[str, Any]], context_window: int) -> dict[str, Any] | None:
    """从单次模型调用记录构造前端 usage payload。"""
    if not calls:
        return None
    current_call = calls[-1]
    current_input_tokens = int(current_call.get("input_tokens") or 0)
    max_input_tokens = max(int(call.get("input_tokens") or 0) for call in calls)
    cumulative_input_tokens = sum(int(call.get("input_tokens") or 0) for call in calls)
    output_tokens = sum(int(call.get("output_tokens") or 0) for call in calls)
    total_tokens = cumulative_input_tokens + output_tokens
    payload = build_token_usage_payload(used=current_input_tokens, total=context_window)
    payload.update({
        "input_tokens": current_input_tokens,
        "current_input_tokens": current_input_tokens,
        "max_input_tokens": max_input_tokens,
        "cumulative_input_tokens": cumulative_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "context_window": context_window,
        "estimated": True,
        "llm_calls": calls,
        "breakdown": list(current_call.get("breakdown") or []),
    })
    return payload


def _message_tokens(message: BaseMessage) -> int:
    total = 4
    content = getattr(message, "content", None)
    if content:
        if isinstance(content, str):
            total += estimate_tokens(content)
        else:
            total += estimate_tokens(json.dumps(content, ensure_ascii=False, default=str))
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        total += estimate_tokens(json.dumps(tool_calls, ensure_ascii=False, default=str))
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    raw_tool_calls = additional_kwargs.get("tool_calls")
    if raw_tool_calls:
        total += estimate_tokens(json.dumps(raw_tool_calls, ensure_ascii=False, default=str))
    return int(total)


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


def _get_agent_token_usage(agent: Any) -> dict[str, Any] | None:
    try:
        middleware = _AGENT_TOKEN_USAGE_MIDDLEWARE.get(agent)
    except TypeError:
        middleware = None
    if middleware is None:
        middleware = getattr(agent, "_chat_data_token_usage_middleware", None)
    getter = getattr(middleware, "get_token_usage", None)
    if callable(getter):
        return getter()
    return None


def bind_token_usage_middleware(agent: Any, middleware: Any) -> None:
    """关联 agent 与 token usage middleware。"""
    try:
        _AGENT_TOKEN_USAGE_MIDDLEWARE[agent] = middleware
        return
    except TypeError:
        pass
    try:
        setattr(agent, "_chat_data_token_usage_middleware", middleware)
    except Exception:
        return


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
