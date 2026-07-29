"""Context compression middleware for persisted agent state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

from langchain.agents.middleware import before_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.logger import get_logger
from ...services.context_budget import (
    ContextInputLimitExceededError,
    REQUEST_OVERHEAD_TOKENS,
    ToolResultBudget,
    build_runtime_context_summary_message,
    count_messages_tokens,
    get_model_context_budget,
)
from ...services.llm_config import (
    get_chat_data_llm,
    get_context_compression_summary_output_max_tokens,
)
from ...services.runtime_config import get_context_compression_config
from ..prompts.context_compression import build_context_compression_summary_prompt

logger = get_logger(__name__)

SUMMARY_TOOL_RESULT_COMPACT_TOKENS = 512
RECENT_COMPLETED_ROUNDS_TO_KEEP = 2


@dataclass(frozen=True)
class ContextCompressionConfig:
    enabled: bool
    trigger_ratio: float


async def compress_messages_for_state(
    messages: list[BaseMessage],
    *,
    static_input_tokens: int,
    compression_trigger_tokens: int,
    hard_input_limit_tokens: int,
    compression_keep_tokens: int,
    config: dict[str, Any] | None = None,
    previous_summary: str | None = None,
    summarizer: Callable[[list[BaseMessage]], Awaitable[str]] | None = None,
    summary_input_limit_tokens: int | None = None,
) -> tuple[list[BaseMessage], str | None, dict[str, Any]]:
    """压缩已完成旧历史，仅替换运行态消息并返回独立累计摘要。"""
    cfg = _config_from_mapping(config)
    original_messages, legacy_summary = _extract_legacy_context_summary(messages)
    effective_previous_summary = _merge_context_summaries(previous_summary, legacy_summary)
    static_tokens = max(int(static_input_tokens), 0)
    before_tokens = static_tokens + count_messages_tokens(original_messages) + _summary_tokens(effective_previous_summary)
    threshold_tokens = max(int(compression_trigger_tokens), 1)
    report = _build_report(
        cfg,
        before_tokens=before_tokens,
        after_tokens=before_tokens,
        threshold_tokens=threshold_tokens,
        hard_input_limit_tokens=hard_input_limit_tokens,
        compression_keep_tokens=compression_keep_tokens,
        static_input_tokens=static_tokens,
    )
    report["legacy_summary_migrated"] = bool(legacy_summary)
    if not cfg.enabled or before_tokens < threshold_tokens:
        return original_messages, effective_previous_summary, report

    compressible_end = _compressible_end_index(original_messages)
    if compressible_end <= 0:
        report["compression_skipped_reason"] = "no_completed_old_rounds"
        return original_messages, effective_previous_summary, report

    retain_start = _recent_completed_round_start(
        original_messages,
        compressible_end,
        static_input_tokens=static_tokens,
        compression_keep_tokens=compression_keep_tokens,
    )
    messages_to_summarize = original_messages[:retain_start]
    if not messages_to_summarize:
        report["compression_skipped_reason"] = "recent_rounds_exceed_keep_target"
        return original_messages, effective_previous_summary, report
    summary_messages, summary_input_report = _prepare_summary_input(
        messages_to_summarize,
        previous_summary=effective_previous_summary,
        summary_input_limit_tokens=summary_input_limit_tokens,
    )
    report.update(summary_input_report)
    if summarizer:
        summary_text = await summarizer(summary_messages)
    else:
        summary_text = await _summarize_messages_with_llm(
            summary_messages,
            previous_summary=effective_previous_summary,
        )
    if not summary_text:
        report["compression_skipped_reason"] = "empty_summary"
        return original_messages, effective_previous_summary, report

    compressed_messages = original_messages[retain_start:]
    after_tokens = static_tokens + count_messages_tokens(compressed_messages) + _summary_tokens(summary_text)
    cleared_tokens = max(before_tokens - after_tokens, 0)
    report.update({
        "triggered": True,
        "estimated_tokens_after": after_tokens,
        "estimated_cleared_tokens": cleared_tokens,
        "compressed_messages": retain_start,
        "summary_chars": len(summary_text),
    })
    logger.info(
        "[agent] 上下文压缩已执行: before_tokens=%s after_tokens=%s threshold_tokens=%s compressed_messages=%s",
        before_tokens,
        after_tokens,
        threshold_tokens,
        compressible_end,
    )
    return compressed_messages, summary_text, report


async def build_context_compression_update(
    messages: list[BaseMessage],
    *,
    static_input_tokens: int,
    compression_trigger_tokens: int,
    hard_input_limit_tokens: int,
    compression_keep_tokens: int,
    config: dict[str, Any] | None = None,
    previous_summary: str | None = None,
    summarizer: Callable[[list[BaseMessage]], Awaitable[str]] | None = None,
    summary_input_limit_tokens: int | None = None,
) -> tuple[list[BaseMessage] | None, str | None, dict[str, Any]]:
    """构造可写入 LangGraph state 的压缩消息更新。"""
    compressed_messages, summary_text, report = await compress_messages_for_state(
        messages,
        static_input_tokens=static_input_tokens,
        compression_trigger_tokens=compression_trigger_tokens,
        hard_input_limit_tokens=hard_input_limit_tokens,
        compression_keep_tokens=compression_keep_tokens,
        config=config,
        previous_summary=previous_summary,
        summarizer=summarizer,
        summary_input_limit_tokens=summary_input_limit_tokens,
    )
    if not report.get("triggered") and not report.get("legacy_summary_migrated"):
        return None, summary_text, report
    return [RemoveMessage(id=REMOVE_ALL_MESSAGES), *compressed_messages], summary_text, report


def create_context_compression_middleware(
    *,
    system_prompt: str,
    tools: list[Any] | None = None,
    model_id: str | None = None,
    tool_result_budget: ToolResultBudget | None = None,
    state_schema: Any | None = None,
) -> Any:
    """创建模型调用前的状态级上下文压缩 middleware。"""
    compression_budget = tool_result_budget or ToolResultBudget(model_id)
    request_tools = list(tools or [])

    @before_model(name="ContextCompressionMiddleware", state_schema=state_schema)
    async def context_compression_middleware(state, _runtime):
        messages = list((state or {}).get("messages") or [])
        _, legacy_summary = _extract_legacy_context_summary(messages)
        previous_summary = _merge_context_summaries(
            str((state or {}).get("context_summary") or "").strip() or None,
            legacy_summary,
        )
        budget = compression_budget.configure(system_prompt, request_tools, model_id=model_id)
        message_updates, summary_text, report = await build_context_compression_update(
            messages,
            static_input_tokens=compression_budget.static_input_tokens,
            compression_trigger_tokens=budget.compression_trigger_tokens,
            hard_input_limit_tokens=budget.hard_input_limit_tokens,
            compression_keep_tokens=budget.compression_keep_tokens,
            config=get_context_compression_config(),
            previous_summary=previous_summary,
            summary_input_limit_tokens=_get_summary_input_limit_tokens(model_id),
            summarizer=lambda compressible_messages: _summarize_messages_with_llm(
                compressible_messages,
                model_id=model_id,
                previous_summary=previous_summary,
            ),
        )
        if report["estimated_tokens_after"] > budget.hard_input_limit_tokens:
            raise ContextInputLimitExceededError(
                "当前输入内容过长，无法在模型上下文窗口内处理。请拆分问题、缩小上传内容或减少引用的技能后重试。"
            )
        compression_event = _build_context_compression_event(messages, report)
        if compression_event:
            _emit_context_compression_event(compression_event)
        if not message_updates:
            return {"context_compression": report}
        update = {
            "messages": message_updates,
            "context_summary": summary_text,
            "context_compression": report,
        }
        if compression_event:
            update["context_compression_events"] = [compression_event]
        return update

    return context_compression_middleware


def _config_from_mapping(config: dict[str, Any] | None) -> ContextCompressionConfig:
    data = config or {}
    return ContextCompressionConfig(
        enabled=bool(data.get("enabled", True)),
        trigger_ratio=float(data.get("trigger_ratio", 0.70)),
    )


def _build_report(
    cfg: ContextCompressionConfig,
    *,
    before_tokens: int,
    after_tokens: int,
    threshold_tokens: int,
    hard_input_limit_tokens: int,
    compression_keep_tokens: int,
    static_input_tokens: int,
) -> dict[str, Any]:
    return {
        "enabled": cfg.enabled,
        "triggered": False,
        "estimated_tokens_before": before_tokens,
        "estimated_tokens_after": after_tokens,
        "estimated_cleared_tokens": max(before_tokens - after_tokens, 0),
        "threshold_tokens": threshold_tokens,
        "trigger_ratio": cfg.trigger_ratio,
        "hard_input_limit_tokens": max(int(hard_input_limit_tokens), 1),
        "compression_keep_tokens": max(int(compression_keep_tokens), 1),
        "static_input_tokens": max(int(static_input_tokens), 0),
        "compressed_messages": 0,
        "summary_chars": 0,
        "summary_input_limit_tokens": None,
        "summary_input_tokens_before": 0,
        "summary_input_tokens_after": 0,
        "summary_tool_results_compacted": 0,
        "summary_input_pruned_messages": 0,
        "summary_input_pruned_rounds": 0,
        "compression_skipped_reason": "",
        "legacy_summary_migrated": False,
    }


def _build_context_compression_event(
    messages: list[BaseMessage],
    report: dict[str, Any],
) -> dict[str, Any] | None:
    """生成不含摘要正文的用户可见压缩分界事件。"""
    if not report.get("triggered"):
        return None
    turn_id = ""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            turn_id = _message_id(message)
            if turn_id:
                break
    anchor_message_id = _message_id(messages[-1]) if messages else ""
    return {
        "id": f"context-compression-{uuid4()}",
        "turn_id": turn_id,
        "anchor_message_id": anchor_message_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "estimated_tokens_before": _non_negative_int(report.get("estimated_tokens_before")),
        "estimated_tokens_after": _non_negative_int(report.get("estimated_tokens_after")),
        "estimated_cleared_tokens": _non_negative_int(report.get("estimated_cleared_tokens")),
        "compressed_messages": _non_negative_int(report.get("compressed_messages")),
    }


def _emit_context_compression_event(event: dict[str, Any]) -> None:
    """在 LangGraph 流可用时立即推送分界事件，不影响压缩主流程。"""
    try:
        get_stream_writer()({"type": "context_compression", "event": event})
    except Exception:
        logger.debug("当前执行上下文不支持推送 context_compression custom 事件", exc_info=True)


def _message_id(message: BaseMessage) -> str:
    return str(getattr(message, "id", "") or "").strip()


def _non_negative_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _extract_legacy_context_summary(messages: list[BaseMessage] | None) -> tuple[list[BaseMessage], str | None]:
    """将旧 checkpoint 中的摘要 SystemMessage 迁移到独立 state 字段。"""
    cleaned_messages: list[BaseMessage] = []
    summaries: list[str] = []
    for message in list(messages or []):
        additional_kwargs = getattr(message, "additional_kwargs", None) or {}
        if isinstance(message, SystemMessage) and additional_kwargs.get("context_compression"):
            content = _strip_legacy_summary_prefix(str(getattr(message, "content", "") or ""))
            if content:
                summaries.append(content)
            continue
        cleaned_messages.append(message)
    return cleaned_messages, "\n\n".join(summaries) or None


def _strip_legacy_summary_prefix(summary: str) -> str:
    prefix = "[历史对话摘要]"
    normalized = str(summary or "").strip()
    if normalized.startswith(prefix):
        return normalized[len(prefix):].lstrip("\n ")
    return normalized


def _merge_context_summaries(*summaries: str | None) -> str | None:
    unique_summaries: list[str] = []
    for raw_summary in summaries:
        summary = str(raw_summary or "").strip()
        if summary and summary not in unique_summaries:
            unique_summaries.append(summary)
    return "\n\n".join(unique_summaries) or None


def _compressible_end_index(messages: list[BaseMessage]) -> int:
    current_round_start = _last_human_index(messages)
    search_end = current_round_start if current_round_start < len(messages) else len(messages)
    if search_end <= 0:
        return 0
    return _last_complete_boundary(messages[:search_end])


def _recent_completed_round_start(
    messages: list[BaseMessage],
    completed_end: int,
    *,
    static_input_tokens: int,
    compression_keep_tokens: int,
) -> int:
    """保留不超过目标容量的最近完整轮次；工具调用与结果不会被拆开。"""
    message_keep_limit = max(int(compression_keep_tokens) - max(int(static_input_tokens), 0), 0)
    completed_round_starts = [
        index
        for index, message in enumerate(messages[:completed_end])
        if isinstance(message, HumanMessage)
    ]
    for start in completed_round_starts[-RECENT_COMPLETED_ROUNDS_TO_KEEP:]:
        if count_messages_tokens(messages[start:]) <= message_keep_limit:
            return start
    return completed_end


def _last_human_index(messages: list[BaseMessage]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return index
    return len(messages)


def _last_complete_boundary(messages: list[BaseMessage]) -> int:
    boundary = 0
    pending_tool_calls: set[str] = set()
    for index, message in enumerate(messages):
        if isinstance(message, AIMessage):
            for tool_call in getattr(message, "tool_calls", None) or []:
                call_id = str((tool_call or {}).get("id") or "").strip()
                if call_id:
                    pending_tool_calls.add(call_id)
        elif isinstance(message, ToolMessage):
            tool_call_id = str(getattr(message, "tool_call_id", "") or "").strip()
            if tool_call_id:
                pending_tool_calls.discard(tool_call_id)
        if isinstance(message, (AIMessage, ToolMessage)) and not pending_tool_calls:
            boundary = index + 1
    return boundary


def _summary_tokens(summary_text: str | None) -> int:
    summary_message = build_runtime_context_summary_message(summary_text)
    return count_messages_tokens([summary_message]) if summary_message else 0


def _get_summary_input_limit_tokens(model_id: str | None) -> int:
    """摘要调用使用独立的输出预留，避免摘要请求本身超出模型窗口。"""
    return get_model_context_budget(
        model_id,
        max_output_tokens=get_context_compression_summary_output_max_tokens(),
    ).hard_input_limit_tokens


def _prepare_summary_input(
    messages: list[BaseMessage],
    *,
    previous_summary: str | None,
    summary_input_limit_tokens: int | None,
) -> tuple[list[BaseMessage], dict[str, Any]]:
    """构造受预算约束的摘要输入，不改写 Agent 运行态消息。"""
    prepared_messages = list(messages)
    before_tokens = _count_summary_input_tokens(prepared_messages, previous_summary=previous_summary)
    report = {
        "summary_input_limit_tokens": None,
        "summary_input_tokens_before": before_tokens,
        "summary_input_tokens_after": before_tokens,
        "summary_tool_results_compacted": 0,
        "summary_input_pruned_messages": 0,
        "summary_input_pruned_rounds": 0,
    }
    if summary_input_limit_tokens is None:
        return prepared_messages, report

    input_limit = max(int(summary_input_limit_tokens), 1)
    report["summary_input_limit_tokens"] = input_limit
    if before_tokens <= input_limit:
        return prepared_messages, report

    # 先压缩最大的工具结果；一旦已满足预算便停止，不影响其它工具内容。
    candidates = sorted(
        (
            (index, count_messages_tokens([message]))
            for index, message in enumerate(prepared_messages)
            if isinstance(message, ToolMessage)
        ),
        key=lambda item: (-item[1], item[0]),
    )
    for index, _tokens in candidates:
        compacted = _compact_tool_result_for_summary(prepared_messages[index])
        if compacted is prepared_messages[index]:
            continue
        prepared_messages[index] = compacted
        report["summary_tool_results_compacted"] += 1
        if _count_summary_input_tokens(prepared_messages, previous_summary=previous_summary) <= input_limit:
            report["summary_input_tokens_after"] = _count_summary_input_tokens(
                prepared_messages,
                previous_summary=previous_summary,
            )
            return prepared_messages, report

    # 工具结果都已缩减仍超预算时，才从最早的完整对话轮次开始省略。
    dropped_messages = 0
    dropped_rounds = 0
    while prepared_messages:
        remaining_messages, removed_count = _drop_oldest_summary_round(prepared_messages)
        if removed_count <= 0:
            break
        dropped_messages += removed_count
        dropped_rounds += 1
        marker = _build_summary_omission_marker(dropped_messages, dropped_rounds)
        prepared_messages = [marker, *remaining_messages]
        if _count_summary_input_tokens(prepared_messages, previous_summary=previous_summary) <= input_limit:
            report.update({
                "summary_input_tokens_after": _count_summary_input_tokens(
                    prepared_messages,
                    previous_summary=previous_summary,
                ),
                "summary_input_pruned_messages": dropped_messages,
                "summary_input_pruned_rounds": dropped_rounds,
            })
            return prepared_messages, report
        prepared_messages = remaining_messages

    marker = _build_summary_omission_marker(dropped_messages, dropped_rounds)
    prepared_messages = [marker]
    after_tokens = _count_summary_input_tokens(prepared_messages, previous_summary=previous_summary)
    report.update({
        "summary_input_tokens_after": after_tokens,
        "summary_input_pruned_messages": dropped_messages,
        "summary_input_pruned_rounds": dropped_rounds,
    })
    if after_tokens > input_limit:
        raise ContextInputLimitExceededError(
            "当前模型上下文窗口不足以生成历史摘要，请缩小模型输出预留或切换更大上下文窗口的模型。"
        )
    return prepared_messages, report


def _count_summary_input_tokens(messages: list[BaseMessage], *, previous_summary: str | None) -> int:
    prompt = build_context_compression_summary_prompt(messages, previous_summary=previous_summary)
    return count_messages_tokens([HumanMessage(content=prompt)]) + REQUEST_OVERHEAD_TOKENS


def _compact_tool_result_for_summary(message: ToolMessage) -> ToolMessage:
    """以确定性首尾摘录压缩单个工具结果，保留其来源和调用关联。"""
    original_content = str(getattr(message, "content", "") or "").strip()
    if not original_content:
        return message
    header = (
        "[工具结果已为历史摘要压缩]\n"
        f"tool_name={getattr(message, 'name', None) or '-'}\n"
        f"tool_call_id={getattr(message, 'tool_call_id', None) or '-'}\n"
        f"original_chars={len(original_content)}\n"
        "保留首尾摘录：\n"
    )
    compact_content = _fit_text_to_token_limit(
        original_content,
        prefix=header,
        token_limit=SUMMARY_TOOL_RESULT_COMPACT_TOKENS,
    )
    if compact_content == original_content:
        return message
    metadata = dict(getattr(message, "additional_kwargs", None) or {})
    metadata["context_compression_summary_compacted"] = True
    return message.model_copy(update={
        "content": compact_content,
        "additional_kwargs": metadata,
    })


def _fit_text_to_token_limit(text: str, *, prefix: str, token_limit: int) -> str:
    """以统一 token 估算保留文本首尾，使单条工具结果可预测地缩小。"""
    original = str(text or "")
    if _text_tokens(prefix + original) <= token_limit:
        return original
    minimum = prefix + "[原始工具结果过长，未保留正文]"
    if _text_tokens(minimum) > token_limit:
        return "[工具结果已压缩]"

    left = 0
    right = len(original)
    best = minimum
    while left <= right:
        keep_chars = (left + right) // 2
        candidate = prefix + _build_edge_excerpt(original, keep_chars)
        if _text_tokens(candidate) <= token_limit:
            best = candidate
            left = keep_chars + 1
        else:
            right = keep_chars - 1
    return best


def _text_tokens(text: str) -> int:
    return count_messages_tokens([HumanMessage(content=text)])


def _build_edge_excerpt(text: str, keep_chars: int) -> str:
    if keep_chars >= len(text):
        return text
    if keep_chars <= 0:
        return "[正文已省略]"
    head_chars = (keep_chars + 1) // 2
    tail_chars = keep_chars // 2
    return f"{text[:head_chars]}\n... [中间内容已省略] ...\n{text[-tail_chars:] if tail_chars else ''}"


def _drop_oldest_summary_round(messages: list[BaseMessage]) -> tuple[list[BaseMessage], int]:
    """删除最早完整轮次，保证留下的工具调用与结果不被拆开。"""
    if not messages:
        return [], 0
    first_human_index = next(
        (index for index, message in enumerate(messages) if isinstance(message, HumanMessage)),
        None,
    )
    if first_human_index is None:
        return [], len(messages)
    next_human_index = next(
        (
            index
            for index, message in enumerate(messages[first_human_index + 1 :], start=first_human_index + 1)
            if isinstance(message, HumanMessage)
        ),
        len(messages),
    )
    return messages[next_human_index:], next_human_index


def _build_summary_omission_marker(dropped_messages: int, dropped_rounds: int) -> SystemMessage:
    return SystemMessage(
        content=(
            "[摘要输入已省略最早的完整历史]\n"
            f"已省略轮次={dropped_rounds}\n"
            f"已省略消息={dropped_messages}\n"
            "原因：摘要输入超过模型上下文预算。"
        ),
        additional_kwargs={"context_compression_summary_omission": True},
    )


async def _summarize_messages_with_llm(
    messages: list[BaseMessage],
    *,
    model_id: str | None = None,
    previous_summary: str | None = None,
) -> str:
    prompt = build_context_compression_summary_prompt(messages, previous_summary=previous_summary)
    llm = get_chat_data_llm(
        model=model_id,
        streaming=False,
        max_tokens=get_context_compression_summary_output_max_tokens(),
        enable_thinking=False,
    )
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:
        emit_llm_connection_alarm_if_needed(
            exc,
            ext1={"model_id": model_id or "", "purpose": "context_compression"},
        )
        raise
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(str(item.get("text") if isinstance(item, dict) else item) for item in content)
    return str(content or "").strip()
