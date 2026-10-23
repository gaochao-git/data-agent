"""Context usage helpers and middleware for create_agent calls."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware import ModelCallResult, ModelRequest, wrap_model_call
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.config import get_stream_writer
from langgraph.types import Command

from apps.chat_data_app.services.runtime_config import get_context_compression_config
from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.logger import get_logger

from ..context.context_budget import (
    HARD_INPUT_GUARD_RATIO,
    REQUEST_OVERHEAD_TOKENS,
    SYSTEM_PROMPT_PLACEHOLDER,
    ContextBudget,
    ContextRequestBudget,
    build_context_usage_payload,
    build_runtime_context_summary_message,
    count_messages_tokens,
    count_tools_tokens,
    estimate_tokens,
    extract_legacy_context_summary,
    hard_input_guard_tokens,
    latest_model_name_from_usage,
    merge_context_summaries,
    sanitize_context_usage_for_response,
)
from .context_compression import format_todo_snapshot_lines, neutralize_stale_todo_tool_messages

logger = get_logger(__name__)


def create_context_usage_middleware(
    model_id: str | None = None,
    *,
    tools: list[Any] | None = None,
    context_request_budget: ContextRequestBudget | None = None,
    state_schema: Any | None = None,
    name: str = "ContextUsageMiddleware",
) -> Any:
    """创建统一的上下文计量与 token usage middleware。"""
    records: list[dict[str, Any]] = []
    request_budget = context_request_budget or ContextRequestBudget(model_id)

    @wrap_model_call(name=name, state_schema=state_schema)
    async def context_usage_middleware(
        request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelCallResult]]
    ) -> ModelCallResult:
        logger.info("Agent 处理用户请求")
        system_message = getattr(request, "system_message", None)
        system_prompt = str(getattr(system_message, "content", "") or "")
        logger.info("[agent] system_prompt 长度: %s 字符", len(system_prompt))

        request_tools = list(getattr(request, "tools", None) or tools or [])
        effective_model_id = _request_model_name(request) or model_id
        effective_system_prompt, edited_messages, token_usage = _build_agent_context_usage(
            system_prompt=system_prompt,
            messages=list(getattr(request, "messages", None) or []),
            tools=request_tools,
            model_id=effective_model_id,
            request_budget=request_budget,
            context_compression=_request_context_compression(request),
            context_summary=_request_context_summary(request),
            todo_status=_format_todo_status(_request_todos(request)),
        )
        record = _build_llm_call_record(token_usage, model_id=effective_model_id, index=len(records) + 1)
        records.append(record)
        _emit_context_usage(get_context_usage())

        started_at_ms = _now_epoch_ms()
        try:
            response = await handler(
                _override_request(
                    request,
                    system_message=SystemMessage(content=effective_system_prompt),
                    messages=edited_messages,
                )
            )
        except Exception as exc:
            logger.warning(
                "[agent] 模型调用失败: index=%s model=%s started_at_ms=%s duration_ms=%s error=%s",
                record["index"],
                effective_model_id,
                started_at_ms,
                _now_epoch_ms() - started_at_ms,
                exc,
            )
            emit_llm_connection_alarm_if_needed(
                exc,
                ext1={"model_id": effective_model_id, "middleware": name},
            )
            raise
        ended_at_ms = _now_epoch_ms()
        record["started_at_ms"] = started_at_ms
        record["ended_at_ms"] = ended_at_ms
        _merge_response_usage(record, response)
        _stamp_response_timing(response, started_at_ms=started_at_ms, ended_at_ms=ended_at_ms)
        estimated_input_tokens = int(record.get("input_tokens") or 0)
        actual_input_tokens = record.get("actual_input_tokens")
        deviation_pct: float | None = None
        if actual_input_tokens is not None and estimated_input_tokens > 0:
            deviation_pct = round(
                (float(actual_input_tokens) - estimated_input_tokens) * 100 / estimated_input_tokens,
                1,
            )
        logger.info(
            "[agent] 模型调用完成: index=%s model=%s started_at_ms=%s ended_at_ms=%s duration_ms=%s "
            "estimated_input_tokens=%s actual_input_tokens=%s estimation_deviation_pct=%s output_tokens=%s",
            record["index"],
            effective_model_id,
            started_at_ms,
            ended_at_ms,
            ended_at_ms - started_at_ms,
            estimated_input_tokens,
            "-" if actual_input_tokens is None else actual_input_tokens,
            "-" if deviation_pct is None else deviation_pct,
            record.get("output_tokens"),
        )
        usage = get_context_usage()
        _emit_context_usage(usage)
        return ExtendedModelResponse(
            model_response=_ensure_model_response(response),
            command=Command(update={"token_usage": compact_token_usage_for_state(usage)}),
        )

    def get_context_usage() -> dict[str, Any] | None:
        return build_usage_from_llm_calls(records, request_budget.budget)

    context_usage_middleware.get_context_usage = get_context_usage  # type: ignore[attr-defined]
    context_usage_middleware.context_usage_records = records  # type: ignore[attr-defined]
    return context_usage_middleware


def create_request_context_usage_middleware(
    model_id: str | None = None,
    *,
    tools: list[Any] | None = None,
    context_request_budget: ContextRequestBudget | None = None,
    state_schema: Any | None = None,
) -> Callable[[ModelRequest], Awaitable[ModelCallResult]]:
    """创建每次模型请求前的上下文计量与 token usage middleware。"""
    return create_context_usage_middleware(
        model_id=model_id,
        tools=tools,
        context_request_budget=context_request_budget,
        state_schema=state_schema,
    )


def _ensure_model_response(response: Any) -> ModelResponse:
    if isinstance(response, ModelResponse):
        return response
    if isinstance(response, AIMessage):
        return ModelResponse(result=[response])
    result = getattr(response, "result", None)
    if isinstance(result, list):
        return ModelResponse(result=result)
    return ModelResponse(result=[])


def _build_llm_call_record(token_usage: dict[str, Any], *, model_id: str | None, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "model": model_id,
        "input_tokens": int(token_usage.get("input_tokens") or 0),
        "message_tokens": int(token_usage.get("message_tokens") or 0),
        "tool_schema_tokens": int(token_usage.get("tool_schema_tokens") or 0),
        "overhead_tokens": int(token_usage.get("overhead_tokens") or REQUEST_OVERHEAD_TOKENS),
        "estimated": True,
        "message_count": int(token_usage.get("message_count") or 0),
        "context_compression": token_usage.get("context_compression"),
        "breakdown": list(token_usage.get("breakdown") or []),
    }


_STATE_USAGE_FIELDS = (
    "used",
    "total",
    "percentage",
    "remaining",
    "context_window",
    "max_output_tokens",
    "hard_input_limit_tokens",
    "hard_input_guard_ratio",
    "hard_input_guard_tokens",
    "compression_trigger_tokens",
    "compression_trigger_ratio",
    "compression_target_tokens",
    "input_tokens",
    "current_input_tokens",
    "max_input_tokens",
    "cumulative_input_tokens",
    "output_tokens",
    "total_tokens",
    "estimated",
    "current_cached_input_tokens",
    "cumulative_cached_input_tokens",
    "current_cache_hit_rate",
    "cumulative_cache_hit_rate",
)

_STATE_COMPRESSION_FIELDS = (
    "enabled",
    "triggered",
    "estimated_tokens_before",
    "estimated_tokens_after",
    "estimated_cleared_tokens",
    "threshold_tokens",
    "trigger_ratio",
    "hard_input_limit_tokens",
    "hard_input_guard_ratio",
    "hard_input_guard_tokens",
    "compression_target_tokens",
    "static_input_tokens",
    "compressed_messages",
    "summary_chars",
    "compression_skipped_reason",
)


def compact_token_usage_for_state(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """只保留 checkpoint 所需的统计值，避免保存 prompt、工具和消息明细。"""
    if not isinstance(usage, dict):
        return None
    compact = {key: usage[key] for key in _STATE_USAGE_FIELDS if key in usage}
    compression = usage.get("context_compression")
    if isinstance(compression, dict):
        compact_compression = {key: compression[key] for key in _STATE_COMPRESSION_FIELDS if key in compression}
        if compact_compression:
            compact["context_compression"] = compact_compression
    return compact or None


def build_request_breakdown(
    *,
    messages: list[BaseMessage],
    message_tokens: int,
    tools: list[Any] | None = None,
    tools_tokens: int,
    overhead_tokens: int,
) -> list[dict[str, Any]]:
    """构造当前模型请求上下文拆分。"""
    system_messages = [message for message in messages if isinstance(message, SystemMessage)]
    non_system_messages = [message for message in messages if not isinstance(message, SystemMessage)]
    system_tokens = count_messages_tokens(system_messages)
    non_system_tokens = max(int(message_tokens or 0) - system_tokens, 0)
    items: list[dict[str, Any]] = []
    if system_messages:
        items.append(
            {
                "key": "system_prompt",
                "label": "系统提示词与数据上下文",
                "tokens": system_tokens,
                "estimated_tokens": system_tokens,
                "estimated": True,
                "description": "按当前项目估算公式统计 system message。",
                "children": build_message_breakdown(system_messages),
            }
        )
    if tools_tokens:
        items.append(
            {
                "key": "tool_schema",
                "label": "工具定义",
                "tokens": tools_tokens,
                "estimated_tokens": tools_tokens,
                "estimated": True,
                "description": "按当前项目估算公式统计本次请求传给模型的工具 schema。",
                "children": build_tools_breakdown(tools or []),
            }
        )
    if non_system_messages:
        items.append(
            {
                "key": "messages",
                "label": "会话消息与工具上下文",
                "tokens": non_system_tokens,
                "estimated_tokens": non_system_tokens,
                "estimated": True,
                "description": "按当前项目估算公式统计 history、当前输入、AI tool_calls 和工具结果。",
                "children": build_message_breakdown(non_system_messages, start_index=len(system_messages)),
            }
        )
    if overhead_tokens:
        items.append(
            {
                "key": "request_overhead",
                "label": "请求协议开销",
                "tokens": max(int(overhead_tokens), 0),
                "estimated_tokens": max(int(overhead_tokens), 0),
                "estimated": True,
                "description": "模型协议固定开销，已计入本次模型输入。",
            }
        )
    return items


def build_tools_breakdown(tools: list[Any]) -> list[dict[str, Any]]:
    """按工具拆分 schema 估算 token。

    工具定义无法像消息正文一样从会话历史反查，因此保留完整 schema 正文；
    工具数量有界（不随轮数增长），且 token_usage 每会话仅一行，体积可控。
    """
    items: list[dict[str, Any]] = []
    for index, tool in enumerate(tools or []):
        payload: dict[str, Any] | str
        try:
            payload = tool if isinstance(tool, dict) else convert_to_openai_tool(tool)
        except Exception:
            payload = str(tool)
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        name = ""
        if isinstance(payload, dict):
            raw_function = payload.get("function")
            function = raw_function if isinstance(raw_function, dict) else {}
            name = str(function.get("name") or payload.get("name") or "")
        items.append(
            {
                "key": f"tool_schema_{index}",
                "label": f"工具定义 · {name or index + 1}",
                "role": "tool_schema",
                "message_index": index,
                "tokens": estimate_tokens(content),
                "estimated_tokens": estimate_tokens(content),
                "estimated": True,
                "content": content,
                "description": content[:120],
            }
        )
    return items


def _resolve_breakdown_content(redacted: bool, message: BaseMessage) -> Any:
    """redacted 时回退占位文案，否则返回消息调试正文。"""
    if redacted:
        return SYSTEM_PROMPT_PLACEHOLDER
    return _message_debug_content(message)


def _is_round_boundary(message: BaseMessage, is_context_summary: bool) -> bool:
    return isinstance(message, HumanMessage) and not is_context_summary


def _apply_breakdown_item_flags(item: dict[str, Any], is_context_summary: bool, redacted: bool) -> None:
    """按 redacted / 上下文摘要标记补充 item 的 description 与 redacted 字段。"""
    if is_context_summary:
        item["description"] = "运行态上下文摘要，内容不对用户展示。"
    elif redacted:
        item["description"] = SYSTEM_PROMPT_PLACEHOLDER
    if redacted:
        item["redacted"] = True


def _build_message_breakdown_item(
    message: BaseMessage,
    index: int,
    round_index: int,
    hide_content: bool,
) -> tuple[dict[str, Any], int]:
    """构造单条消息的 token 用量明细，返回 (item, 更新后的 round_index)。"""
    message_type = _message_role(message)
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    is_context_summary = bool(additional_kwargs.get("context_summary"))
    redacted = hide_content or is_context_summary
    content = _resolve_breakdown_content(redacted, message)
    if _is_round_boundary(message, is_context_summary):
        round_index += 1
    estimated_tokens = count_messages_tokens([message])
    item = {
        "key": f"message_{index}",
        "label": "历史摘要（运行态）" if is_context_summary else _message_label(message_type, round_index or 1),
        "role": "context_summary" if is_context_summary else message_type,
        "round": round_index or 1,
        "message_index": index,
        # 前端凭 message_id 从已加载的会话消息中取正文，payload 本身不落正文。
        "message_id": getattr(message, "id", None),
        "tool_call_id": getattr(message, "tool_call_id", None),
        # AI 消息为模型调用、工具消息为工具执行的起止时间（epoch 毫秒）；
        # 其余消息无此字段，耗时由前端相减得出。
        "started_at_ms": _optional_non_negative_int(additional_kwargs.get("started_at_ms")),
        "ended_at_ms": _optional_non_negative_int(additional_kwargs.get("ended_at_ms")),
        "tokens": estimated_tokens,
        "estimated_tokens": estimated_tokens,
        "estimated": True,
        "content": content,
    }
    _apply_breakdown_item_flags(item, is_context_summary, redacted)
    return item, round_index


def build_message_breakdown(
    messages: Sequence[BaseMessage],
    *,
    start_index: int = 0,
    hide_content: bool = False,
) -> list[dict[str, Any]]:
    """按消息拆分估算 token。"""
    items = []
    round_index = 0
    for local_index, message in enumerate(messages or []):
        index = start_index + local_index
        item, round_index = _build_message_breakdown_item(message, index, round_index, hide_content)
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
    context_budget = (
        budget
        if isinstance(budget, ContextBudget)
        else ContextBudget(
            context_window=max(int(budget), 1),
            max_output_tokens=0,
            compression_trigger_ratio=1.0,
            hard_input_limit_tokens=max(int(budget), 1),
            compression_trigger_tokens=max(int(budget), 1),
            compression_target_tokens=max(int(budget), 1),
        )
    )
    payload = build_context_usage_payload(context_budget, current_input_tokens)
    payload.update(
        {
            "input_tokens": current_input_tokens,
            "current_input_tokens": current_input_tokens,
            "max_input_tokens": max_input_tokens,
            "cumulative_input_tokens": cumulative_input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated": True,
            "llm_calls": calls,
            "context_compression": current_call.get("context_compression"),
            "breakdown": list(current_call.get("breakdown") or []),
        }
    )
    _add_cached_input_usage(payload, calls, current_call)
    return payload


def _build_agent_context_usage(
    *,
    system_prompt: str,
    messages: list[Any],
    tools: list[Any],
    model_id: str | None,
    request_budget: ContextRequestBudget,
    context_compression: dict[str, Any] | None = None,
    context_summary: str | None = None,
    todo_status: str | None = None,
) -> tuple[str, list[Any], dict[str, Any]]:
    """构造当前模型入口 token usage。"""
    budget = request_budget.configure(system_prompt, tools, model_id=model_id)
    effective_system_prompt, request_messages = _merge_compression_summary_into_system_prompt(
        system_prompt,
        messages,
        context_summary=context_summary,
    )
    # 陈旧的 write_todos 快照在出站视图上替换，配合下方 fresh 块的权威声明消除新旧状态矛盾；
    # 只改本轮请求视图，不改写 Agent 运行态消息（checkpoint 保持原样）。
    request_messages = neutralize_stale_todo_tool_messages(request_messages)
    if todo_status:
        # 实时待办状态在压缩之后的出站视图上拼接（不会被摘要吞掉），并参与本轮计量。
        effective_system_prompt = f"{effective_system_prompt}\n\n{todo_status}"
    llm_messages = [SystemMessage(content=effective_system_prompt), *request_messages]
    message_tokens = count_messages_tokens(llm_messages)
    tool_tokens = count_tools_tokens(tools)
    input_tokens = message_tokens + tool_tokens + REQUEST_OVERHEAD_TOKENS
    token_usage = build_context_usage_payload(budget, input_tokens)
    token_usage.update(
        {
            "input_tokens": input_tokens,
            "current_input_tokens": input_tokens,
            "message_tokens": message_tokens,
            "tool_schema_tokens": tool_tokens,
            "overhead_tokens": REQUEST_OVERHEAD_TOKENS,
            "message_count": len(llm_messages),
            "output_tokens": 0,
            "total_tokens": input_tokens,
            "estimated": True,
            "context_compression": context_compression
            or _empty_context_compression_report(
                budget=budget,
                static_input_tokens=request_budget.static_input_tokens,
            ),
            "breakdown": build_request_breakdown(
                messages=llm_messages,
                message_tokens=message_tokens,
                tools=tools,
                tools_tokens=tool_tokens,
                overhead_tokens=REQUEST_OVERHEAD_TOKENS,
            ),
        }
    )
    return effective_system_prompt, request_messages, token_usage


def _empty_context_compression_report(*, budget: ContextBudget, static_input_tokens: int) -> dict[str, Any]:
    config = get_context_compression_config()
    return {
        "enabled": bool(config.get("enabled", True)),
        "triggered": False,
        "estimated_tokens_before": 0,
        "estimated_tokens_after": 0,
        "estimated_cleared_tokens": 0,
        "threshold_tokens": budget.compression_trigger_tokens,
        "trigger_ratio": budget.compression_trigger_ratio,
        "hard_input_limit_tokens": budget.hard_input_limit_tokens,
        "hard_input_guard_ratio": HARD_INPUT_GUARD_RATIO,
        "hard_input_guard_tokens": hard_input_guard_tokens(budget.hard_input_limit_tokens),
        "compression_target_tokens": budget.compression_target_tokens,
        "static_input_tokens": max(int(static_input_tokens), 0),
        "compressed_messages": 0,
        "summary_chars": 0,
        "compression_skipped_reason": "",
    }


def _override_request(request: Any, **kwargs: Any) -> ModelRequest:
    """兼容 LangChain ModelRequest 和单测中的轻量对象。"""
    if hasattr(request, "override"):
        return request.override(**kwargs)
    data = dict(getattr(request, "__dict__", {}) or {})
    data.update(kwargs)
    return type(request)(**data)


def _merge_compression_summary_into_system_prompt(
    system_prompt: str,
    messages: list[Any],
    *,
    context_summary: str | None = None,
) -> tuple[str, list[Any]]:
    """将摘要作为隐藏 human 数据上下文注入，保持唯一 system message。"""
    cleaned_messages, legacy_summary = extract_legacy_context_summary(messages)
    summary = merge_context_summaries(context_summary, legacy_summary)
    if not summary:
        return system_prompt, cleaned_messages
    summary_message = build_runtime_context_summary_message(summary)
    if summary_message is None:
        return system_prompt, cleaned_messages
    return system_prompt, [summary_message, *cleaned_messages]


def _emit_context_usage(usage: dict[str, Any] | None) -> None:
    """通过 LangGraph custom stream 输出模型入口上下文占用。"""
    if not usage:
        return
    try:
        payload = sanitize_context_usage_for_response(usage)
        # sanitize 会剥掉 llm_calls，模型名必须在其前提取并单独透出：
        # model 挂在 custom 事件外层（adapter 的 _handle_token_usage_payload
        # 从 payload["model"] 读取，放 usage 内层会永远取不到）。
        model_name = latest_model_name_from_usage(usage)
        event: dict[str, Any] = {"type": "token_usage", "usage": payload}
        if model_name:
            event["model"] = model_name
        get_stream_writer()(event)
    except Exception:
        logger.debug("当前执行上下文不支持推送 token_usage custom 事件", exc_info=True)


def _request_model_name(request: Any) -> str | None:
    model = getattr(request, "model", None)
    for attr in ("model_name", "model", "model_id"):
        value = getattr(model, attr, None)
        if value:
            return str(value)
    return None


def _request_context_compression(request: Any) -> dict[str, Any] | None:
    state = getattr(request, "state", None)
    if not isinstance(state, dict):
        return None
    value = state.get("context_compression")
    return value if isinstance(value, dict) else None


def _request_context_summary(request: Any) -> str | None:
    state = getattr(request, "state", None)
    if not isinstance(state, dict):
        return None
    summary = str(state.get("context_summary") or "").strip()
    return summary or None


def _request_todos(request: Any) -> list[dict[str, Any]]:
    """读取 state 中的 todos（官方 TodoListMiddleware 将其 OmitFromInput，不会自动进模型输入）。"""
    state = getattr(request, "state", None)
    if not isinstance(state, dict):
        return []
    todos = state.get("todos")
    if not isinstance(todos, list):
        return []
    return [item for item in todos if isinstance(item, dict) and str(item.get("content") or "").strip()]


def _format_todo_status(todos: list[dict[str, Any]]) -> str | None:
    """把 state 中的 todos 格式化为每轮注入 system 的实时状态块。

    无有效条目时返回 None（不注入）；注入的是"从 state 重新生成的易失视图"，
    替代历史里一次性 write_todos ToolMessage 的陈旧快照。
    """
    lines, unfinished = format_todo_snapshot_lines(todos)
    if not lines:
        return None
    reminder = (
        f"未完成任务 {unfinished} 个。"
        "若本轮执行推进或完成了其中任一项，必须先调用 write_todos 更新状态，再继续后续步骤。"
        if unfinished
        else "全部任务已完成。若本轮产生新的待办事项，用 write_todos 记录。"
    )
    todo_block = "\n".join(lines)
    return (
        '<todo_status source="state" fresh="true">\n'
        f"{todo_block}\n"
        "历史 write_todos 工具结果中的旧快照均已失效，任务状态一律以本块为准。\n"
        f"{reminder}\n"
        "</todo_status>"
    )


def _now_epoch_ms() -> int:
    return int(time.time() * 1000)


def _stamp_response_timing(response: Any, *, started_at_ms: int, ended_at_ms: int) -> None:
    """把模型调用起止时间（epoch 毫秒）盖进响应消息，随 checkpoint 持久化。

    耗时由展示端相减得出；保留起止时间比只存时长信息更全（时间轴定位、间隔分析）。
    """
    message = _extract_response_message(response)
    if message is None:
        return
    additional_kwargs = dict(getattr(message, "additional_kwargs", None) or {})
    additional_kwargs["started_at_ms"] = started_at_ms
    additional_kwargs["ended_at_ms"] = ended_at_ms
    try:
        message.additional_kwargs = additional_kwargs
    except Exception:
        logger.warning(
            "模型调用起止时间写入响应消息失败: started_at_ms=%s ended_at_ms=%s",
            started_at_ms,
            ended_at_ms,
            exc_info=True,
        )


def _merge_response_usage(record: dict[str, Any], response: Any) -> None:
    message = _extract_response_message(response)
    response_usage = _extract_response_usage(message)
    if response_usage is not None:
        input_tokens, output_tokens, total_tokens = response_usage
        record["actual_input_tokens"] = input_tokens
        record["output_tokens"] = output_tokens
        record["actual_total_tokens"] = total_tokens
    cached_input_tokens = _extract_cached_input_tokens(message)
    if cached_input_tokens is not None:
        record["cached_input_tokens"] = cached_input_tokens


def _add_cached_input_usage(
    payload: dict[str, Any],
    calls: list[dict[str, Any]],
    current_call: dict[str, Any],
) -> None:
    """聚合模型实际返回的缓存命中数，缺失时不改变既有 payload。"""
    cache_calls = [call for call in calls if _optional_non_negative_int(call.get("cached_input_tokens")) is not None]
    if not cache_calls:
        return
    current_cached_tokens = _optional_non_negative_int(current_call.get("cached_input_tokens"))
    if current_cached_tokens is not None:
        payload["current_cached_input_tokens"] = current_cached_tokens
        _add_cache_hit_rate(
            payload,
            "current_cache_hit_rate",
            current_cached_tokens,
            _optional_non_negative_int(current_call.get("actual_input_tokens")),
        )
    calls_with_reported_input = [
        call for call in cache_calls if _optional_non_negative_int(call.get("actual_input_tokens")) is not None
    ]
    cumulative_cached_tokens = sum(
        _optional_non_negative_int(call.get("cached_input_tokens")) or 0 for call in calls_with_reported_input
    )
    payload["cumulative_cached_input_tokens"] = cumulative_cached_tokens
    reported_input_tokens = [
        _optional_non_negative_int(call.get("actual_input_tokens")) for call in calls_with_reported_input
    ]
    _add_cache_hit_rate(
        payload,
        "cumulative_cache_hit_rate",
        cumulative_cached_tokens,
        sum(tokens for tokens in reported_input_tokens if tokens is not None),
        has_reported_input=bool(calls_with_reported_input),
    )


def _add_cache_hit_rate(
    payload: dict[str, Any],
    key: str,
    cached_tokens: int,
    input_tokens: int | None,
    *,
    has_reported_input: bool = True,
) -> None:
    if not has_reported_input or input_tokens is None or input_tokens <= 0:
        return
    payload[key] = round((cached_tokens / input_tokens) * 100, 1)


def _extract_response_message(response: Any) -> AIMessage | None:
    if isinstance(response, AIMessage):
        return response
    result = getattr(response, "result", None)
    if isinstance(result, list) and result and isinstance(result[0], AIMessage):
        return result[0]
    return None


def _extract_cached_input_tokens(message: AIMessage | None) -> int | None:
    if message is None:
        return None
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        cached_tokens = _cached_tokens_from_details(usage.get("input_token_details"))
        if cached_tokens is not None:
            return cached_tokens
    response_metadata = getattr(message, "response_metadata", None)
    if not isinstance(response_metadata, dict):
        return None
    for payload in (
        response_metadata.get("token_usage"),
        response_metadata.get("usage"),
        response_metadata,
    ):
        cached_tokens = _cached_tokens_from_openai_usage(payload)
        if cached_tokens is not None:
            return cached_tokens
    return None


def _extract_response_usage(message: AIMessage | None) -> tuple[int, int, int] | None:
    if message is None:
        return None
    usage = getattr(message, "usage_metadata", None)
    usage_from_metadata = _usage_from_usage_metadata(usage)
    if usage_from_metadata is not None:
        return usage_from_metadata
    return _usage_from_response_metadata(message)


def _usage_from_usage_metadata(usage: Any) -> tuple[int, int, int] | None:
    """从 usage_metadata dict 提取三元组；缺关键字段返回 None 交给 response_metadata 兜底。"""
    if not (isinstance(usage, dict) and any(key in usage for key in ("input_tokens", "output_tokens", "total_tokens"))):
        return None
    input_tokens = _optional_non_negative_int(usage.get("input_tokens")) or 0
    output_tokens = _optional_non_negative_int(usage.get("output_tokens")) or 0
    total_tokens = _optional_non_negative_int(usage.get("total_tokens"))
    return input_tokens, output_tokens, total_tokens if total_tokens is not None else input_tokens + output_tokens


def _usage_from_response_metadata(message: AIMessage) -> tuple[int, int, int] | None:
    """从 response_metadata 的 openai 风格 payload 提取三元组；全空返回 None。"""
    raw_usage = _response_metadata_usage(message)
    if raw_usage is None:
        return None
    input_tokens = _optional_non_negative_int(raw_usage.get("prompt_tokens"))
    if input_tokens is None:
        input_tokens = _optional_non_negative_int(raw_usage.get("input_tokens"))
    output_tokens = _optional_non_negative_int(raw_usage.get("completion_tokens"))
    if output_tokens is None:
        output_tokens = _optional_non_negative_int(raw_usage.get("output_tokens"))
    total_tokens = _optional_non_negative_int(raw_usage.get("total_tokens"))
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    safe_input_tokens = input_tokens or 0
    safe_output_tokens = output_tokens or 0
    return (
        safe_input_tokens,
        safe_output_tokens,
        total_tokens if total_tokens is not None else safe_input_tokens + safe_output_tokens,
    )


def _response_metadata_usage(message: AIMessage) -> dict[str, Any] | None:
    response_metadata = getattr(message, "response_metadata", None)
    if not isinstance(response_metadata, dict):
        return None
    for payload in (
        response_metadata.get("token_usage"),
        response_metadata.get("usage"),
        response_metadata,
    ):
        if isinstance(payload, dict):
            return payload
    return None


def _cached_tokens_from_details(details: Any) -> int | None:
    if not isinstance(details, dict):
        return None
    for key in ("cache_read", "priority_cache_read", "flex_cache_read", "cached_tokens"):
        cached_tokens = _optional_non_negative_int(details.get(key))
        if cached_tokens is not None:
            return cached_tokens
    return None


def _cached_tokens_from_openai_usage(usage: Any) -> int | None:
    if not isinstance(usage, dict):
        return None
    cached_tokens = _cached_tokens_from_details(usage.get("input_token_details"))
    if cached_tokens is not None:
        return cached_tokens
    details = usage.get("prompt_tokens_details")
    return _cached_tokens_from_details(details)


def _optional_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        token_count = int(value)
    except (TypeError, ValueError):
        return None
    return token_count if token_count >= 0 else None


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


def _message_role(message: BaseMessage) -> str:
    """将 LangChain 标准消息类映射为内部 token 明细角色。"""
    if isinstance(message, SystemMessage):
        return "system"
    if isinstance(message, HumanMessage):
        return "human"
    if isinstance(message, AIMessage):
        return "ai"
    if isinstance(message, ToolMessage):
        return "tool"
    return "message"
