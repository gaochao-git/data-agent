"""Context compression middleware for persisted agent state."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import before_model
from langchain_core.messages import BaseMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.logger import get_logger
from ..context.context_budget import (
    ContextInputLimitExceededError,
    HARD_INPUT_GUARD_RATIO,
    REQUEST_OVERHEAD_TOKENS,
    ContextRequestBudget,
    build_runtime_context_summary_message,
    count_messages_tokens,
    extract_legacy_context_summary,
    get_model_context_budget,
    hard_input_guard_tokens,
    merge_context_summaries,
)
from apps.chat_data_app.services.llm_config import (
    get_chat_data_llm,
    get_context_compression_summary_output_max_tokens,
)
from apps.chat_data_app.services.runtime_config import get_context_compression_config
from ..prompts.context_compression import build_context_compression_summary_prompt
from ..tool_results import tool_result_recovery
from .message_integrity import complete_message_boundaries

logger = get_logger(__name__)

SUMMARY_TOOL_RESULT_COMPACT_TOKENS = 512
RECENT_COMPLETED_ROUNDS_TO_KEEP = 2
MAX_COMPRESSION_PASSES = 8


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
    compression_target_tokens: int,
    config: dict[str, Any] | None = None,
    previous_summary: str | None = None,
    summarizer: Callable[[list[BaseMessage]], Awaitable[str]] | None = None,
    summary_input_limit_tokens: int | None = None,
    summary_model_id: str | None = None,
) -> tuple[list[BaseMessage], str | None, dict[str, Any]]:
    """压缩旧历史和当前回合较早的完整工具链。"""
    cfg = _config_from_mapping(config)
    original_messages, legacy_summary = extract_legacy_context_summary(messages)
    effective_previous_summary = merge_context_summaries(previous_summary, legacy_summary)
    static_tokens = max(int(static_input_tokens), 0)
    before_tokens = static_tokens + count_messages_tokens(original_messages) + _summary_tokens(effective_previous_summary)
    threshold_tokens = max(int(compression_trigger_tokens), 1)
    report = _build_report(
        cfg,
        before_tokens=before_tokens,
        after_tokens=before_tokens,
        threshold_tokens=threshold_tokens,
        hard_input_limit_tokens=hard_input_limit_tokens,
        compression_target_tokens=compression_target_tokens,
        static_input_tokens=static_tokens,
    )
    report["legacy_summary_migrated"] = bool(legacy_summary)
    if not cfg.enabled or before_tokens < threshold_tokens:
        return _enforce_hard_input_limit(
            original_messages,
            effective_previous_summary,
            report,
            before_tokens=before_tokens,
            static_input_tokens=static_tokens,
            hard_input_limit_tokens=hard_input_limit_tokens,
        )

    target_tokens = max(int(compression_target_tokens), 1)
    working_messages, referenced_count = _reference_tool_results_to_target(
        original_messages,
        static_input_tokens=static_tokens,
        summary_text=effective_previous_summary,
        target_tokens=target_tokens,
    )
    working_tokens = static_tokens + count_messages_tokens(working_messages) + _summary_tokens(effective_previous_summary)
    report["referenced_tool_results"] = referenced_count
    if working_tokens <= target_tokens:
        _finalize_compression_report(
            report,
            before_tokens=before_tokens,
            after_tokens=working_tokens,
            compressed_messages=0,
            summary_text=effective_previous_summary,
        )
        return _enforce_hard_input_limit(
            working_messages,
            effective_previous_summary,
            report,
            before_tokens=before_tokens,
            static_input_tokens=static_tokens,
            hard_input_limit_tokens=hard_input_limit_tokens,
        )

    current_human_index = _last_human_index(working_messages)
    compressible_end = _last_complete_boundary(working_messages)
    if compressible_end <= 0:
        report["compression_skipped_reason"] = "no_completed_message_groups"
        report["estimated_tokens_after"] = working_tokens
        return _enforce_hard_input_limit(
            working_messages,
            effective_previous_summary,
            report,
            before_tokens=before_tokens,
            static_input_tokens=static_tokens,
            hard_input_limit_tokens=hard_input_limit_tokens,
        )

    messages_to_summarize, compressed_messages, compressed_count = _partition_messages_for_compression(
        working_messages,
        compressible_end,
        current_human_index=current_human_index,
        static_input_tokens=static_tokens,
        compression_target_tokens=target_tokens,
    )
    if not messages_to_summarize:
        report["compression_skipped_reason"] = "no_compressible_messages"
        report["estimated_tokens_after"] = working_tokens
        return _enforce_hard_input_limit(
            working_messages,
            effective_previous_summary,
            report,
            before_tokens=before_tokens,
            static_input_tokens=static_tokens,
            hard_input_limit_tokens=hard_input_limit_tokens,
        )
    summary_text, summary_input_report = await _summarize_for_compression(
        messages_to_summarize,
        previous_summary=effective_previous_summary,
        summarizer=summarizer,
        summary_input_limit_tokens=summary_input_limit_tokens,
        summary_model_id=summary_model_id,
    )
    _merge_summary_input_report(report, summary_input_report)
    if not summary_text:
        report["compression_skipped_reason"] = "empty_summary"
        report["estimated_tokens_after"] = working_tokens
        return _enforce_hard_input_limit(
            working_messages,
            effective_previous_summary,
            report,
            before_tokens=before_tokens,
            static_input_tokens=static_tokens,
            hard_input_limit_tokens=hard_input_limit_tokens,
        )

    after_tokens = static_tokens + count_messages_tokens(compressed_messages) + _summary_tokens(summary_text)
    compression_passes = 1
    while after_tokens > target_tokens and compression_passes < MAX_COMPRESSION_PASSES:
        effective_target = max(target_tokens - _summary_tokens(summary_text), 1)
        additional_messages, next_messages, additional_count = _partition_messages_for_compression(
            compressed_messages,
            _last_complete_boundary(compressed_messages),
            current_human_index=_last_human_index(compressed_messages),
            static_input_tokens=static_tokens,
            compression_target_tokens=effective_target,
        )
        if not additional_messages or additional_count <= 0:
            break
        merged_summary, next_input_report = await _summarize_for_compression(
            additional_messages,
            previous_summary=summary_text,
            summarizer=summarizer,
            summary_input_limit_tokens=summary_input_limit_tokens,
            summary_model_id=summary_model_id,
        )
        _merge_summary_input_report(report, next_input_report)
        if not merged_summary:
            break
        next_tokens = static_tokens + count_messages_tokens(next_messages) + _summary_tokens(merged_summary)
        if next_tokens >= after_tokens:
            break
        compressed_messages = next_messages
        summary_text = merged_summary
        compressed_count += additional_count
        after_tokens = next_tokens
        compression_passes += 1

    _finalize_compression_report(
        report,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        compressed_messages=compressed_count,
        summary_text=summary_text,
    )
    report["compression_passes"] = compression_passes
    report["target_reached"] = after_tokens <= target_tokens
    logger.info(
        "[agent] 上下文压缩已执行: before_tokens=%s after_tokens=%s threshold_tokens=%s compressed_messages=%s",
        before_tokens,
        after_tokens,
        threshold_tokens,
        compressed_count,
    )
    return _enforce_hard_input_limit(
        compressed_messages,
        summary_text,
        report,
        before_tokens=before_tokens,
        static_input_tokens=static_tokens,
        hard_input_limit_tokens=hard_input_limit_tokens,
    )


async def build_context_compression_update(
    messages: list[BaseMessage],
    *,
    static_input_tokens: int,
    compression_trigger_tokens: int,
    hard_input_limit_tokens: int,
    compression_target_tokens: int,
    config: dict[str, Any] | None = None,
    previous_summary: str | None = None,
    summarizer: Callable[[list[BaseMessage]], Awaitable[str]] | None = None,
    summary_input_limit_tokens: int | None = None,
    summary_model_id: str | None = None,
) -> tuple[list[BaseMessage] | None, str | None, dict[str, Any]]:
    """构造可写入 LangGraph state 的压缩消息更新。"""
    compressed_messages, summary_text, report = await compress_messages_for_state(
        messages,
        static_input_tokens=static_input_tokens,
        compression_trigger_tokens=compression_trigger_tokens,
        hard_input_limit_tokens=hard_input_limit_tokens,
        compression_target_tokens=compression_target_tokens,
        config=config,
        previous_summary=previous_summary,
        summarizer=summarizer,
        summary_input_limit_tokens=summary_input_limit_tokens,
        summary_model_id=summary_model_id,
    )
    if not report.get("triggered") and not report.get("legacy_summary_migrated"):
        return None, summary_text, report
    return [RemoveMessage(id=REMOVE_ALL_MESSAGES), *compressed_messages], summary_text, report


def create_context_compression_middleware(
    *,
    system_prompt: str,
    tools: list[Any] | None = None,
    model_id: str | None = None,
    context_request_budget: ContextRequestBudget | None = None,
    state_schema: Any | None = None,
) -> Any:
    """创建模型调用前的状态级上下文压缩 middleware。"""
    compression_budget = context_request_budget or ContextRequestBudget(model_id)
    request_tools = list(tools or [])

    @before_model(name="ContextCompressionMiddleware", state_schema=state_schema)
    async def context_compression_middleware(state, _runtime):
        messages = list((state or {}).get("messages") or [])
        _, legacy_summary = extract_legacy_context_summary(messages)
        previous_summary = merge_context_summaries(
            str((state or {}).get("context_summary") or "").strip() or None,
            legacy_summary,
        )
        budget = compression_budget.configure(system_prompt, request_tools, model_id=model_id)
        message_updates, summary_text, report = await build_context_compression_update(
            messages,
            static_input_tokens=compression_budget.static_input_tokens,
            compression_trigger_tokens=budget.compression_trigger_tokens,
            hard_input_limit_tokens=budget.hard_input_limit_tokens,
            compression_target_tokens=budget.compression_target_tokens,
            config=get_context_compression_config(),
            previous_summary=previous_summary,
            summary_input_limit_tokens=_get_summary_input_limit_tokens(model_id),
            summary_model_id=model_id,
        )
        if report["estimated_tokens_after"] > budget.hard_input_limit_tokens:
            raise ContextInputLimitExceededError("模型固定输入超过上下文硬上限，请检查系统提示词、工具描述和输出预留配置。")
        compression_event = _build_context_compression_event(messages, report)
        if compression_event:
            _emit_context_compression_event(
                compression_event,
                archived_turn_messages=_current_turn_messages(messages),
            )
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
    compression_target_tokens: int,
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
        "hard_input_guard_ratio": HARD_INPUT_GUARD_RATIO,
        "hard_input_guard_tokens": hard_input_guard_tokens(hard_input_limit_tokens),
        "hard_limit_enforced": False,
        "hard_limit_dropped_messages": 0,
        "hard_limit_summary_dropped": False,
        "hard_limit_user_message_truncated": False,
        "compression_target_tokens": max(int(compression_target_tokens), 1),
        "static_input_tokens": max(int(static_input_tokens), 0),
        "compressed_messages": 0,
        "summary_chars": 0,
        "summary_input_limit_tokens": None,
        "summary_input_tokens_before": 0,
        "summary_input_tokens_after": 0,
        "summary_tool_results_compacted": 0,
        "summary_input_pruned_messages": 0,
        "summary_input_pruned_rounds": 0,
        "summary_input_unusable": False,
        "summary_generation_failed": False,
        "referenced_tool_results": 0,
        "compression_passes": 0,
        "target_reached": before_tokens <= max(int(compression_target_tokens), 1),
        "compression_skipped_reason": "",
        "legacy_summary_migrated": False,
    }


def _reference_tool_results_to_target(
    messages: list[BaseMessage],
    *,
    static_input_tokens: int,
    summary_text: str | None,
    target_tokens: int,
) -> tuple[list[BaseMessage], int]:
    """Replace the largest recoverable tool payloads with deterministic references."""
    working = list(messages)
    candidates = sorted(
        (
            (index, count_messages_tokens([message]))
            for index, message in enumerate(working)
            if isinstance(message, ToolMessage) and tool_result_recovery(message.artifact)
        ),
        key=lambda item: (-item[1], item[0]),
    )
    referenced = 0
    for index, _tokens in candidates:
        current_tokens = static_input_tokens + count_messages_tokens(working) + _summary_tokens(summary_text)
        if current_tokens <= target_tokens:
            break
        referenced_message = _build_referenced_tool_message(working[index])
        if referenced_message is working[index]:
            continue
        working[index] = referenced_message
        referenced += 1
    return working, referenced


def _build_referenced_tool_message(message: ToolMessage) -> ToolMessage:
    recovery = tool_result_recovery(message.artifact)
    if recovery is None:
        return message
    result_path = str(recovery["result_path"])
    source_tool = str(recovery.get("source_tool") or message.name or "-")
    lines = [
        "[tool_result_referenced]",
        f"source_tool={source_tool}",
        f"result_path={result_path}",
        "content_in_context=false",
        "完整内容仍可通过 file_read 按最小必要范围读取。",
    ]
    for key in ("result_id", "result_size_chars", "offset", "limit"):
        if recovery.get(key) is not None:
            lines.insert(-1, f"{key}={recovery[key]}")
    referenced_content = "\n".join(lines)
    if str(message.content or "").strip() == referenced_content:
        return message
    metadata = dict(getattr(message, "additional_kwargs", None) or {})
    metadata["context_compression_referenced"] = True
    return message.model_copy(update={
        "content": referenced_content,
        "additional_kwargs": metadata,
    })


async def _summarize_for_compression(
    messages: list[BaseMessage],
    *,
    previous_summary: str | None,
    summarizer: Callable[[list[BaseMessage]], Awaitable[str]] | None,
    summary_input_limit_tokens: int | None,
    summary_model_id: str | None,
) -> tuple[str, dict[str, Any]]:
    summary_messages, input_report = _prepare_summary_input(
        messages,
        previous_summary=previous_summary,
        summary_input_limit_tokens=summary_input_limit_tokens,
    )
    if not summary_messages or input_report.get("summary_input_unusable"):
        return "", input_report
    try:
        if summarizer:
            return str(await summarizer(summary_messages) or "").strip(), input_report
        summary = await _summarize_messages_with_llm(
            summary_messages,
            model_id=summary_model_id,
            previous_summary=previous_summary,
        )
        return summary, input_report
    except Exception:
        logger.warning("生成上下文摘要失败，改用硬上限确定性收缩", exc_info=True)
        input_report["summary_generation_failed"] = True
        return "", input_report


def _merge_summary_input_report(report: dict[str, Any], current: dict[str, Any]) -> None:
    """Accumulate per-pass summary preparation metrics."""
    for key in (
        "summary_tool_results_compacted",
        "summary_input_pruned_messages",
        "summary_input_pruned_rounds",
    ):
        report[key] = _non_negative_int(report.get(key)) + _non_negative_int(current.get(key))
    for key in (
        "summary_input_limit_tokens",
        "summary_input_tokens_before",
        "summary_input_tokens_after",
    ):
        report[key] = current.get(key)
    report["summary_input_unusable"] = bool(
        report.get("summary_input_unusable") or current.get("summary_input_unusable")
    )
    report["summary_generation_failed"] = bool(
        report.get("summary_generation_failed") or current.get("summary_generation_failed")
    )


def _finalize_compression_report(
    report: dict[str, Any],
    *,
    before_tokens: int,
    after_tokens: int,
    compressed_messages: int,
    summary_text: str | None,
) -> None:
    report.update({
        "triggered": True,
        "estimated_tokens_after": after_tokens,
        "estimated_cleared_tokens": max(before_tokens - after_tokens, 0),
        "compressed_messages": compressed_messages,
        "summary_chars": len(str(summary_text or "")),
        "target_reached": after_tokens <= _non_negative_int(report.get("compression_target_tokens")),
    })


def _enforce_hard_input_limit(
    messages: list[BaseMessage],
    summary_text: str | None,
    report: dict[str, Any],
    *,
    before_tokens: int,
    static_input_tokens: int,
    hard_input_limit_tokens: int,
) -> tuple[list[BaseMessage], str | None, dict[str, Any]]:
    """Deterministically fit the final request below 95% of its hard limit."""
    guard_tokens = hard_input_guard_tokens(hard_input_limit_tokens)
    minimum_message_tokens = count_messages_tokens([HumanMessage(content="")])
    if static_input_tokens + minimum_message_tokens > guard_tokens:
        raise ContextInputLimitExceededError(
            "模型固定输入超过上下文安全水位，请缩短系统提示词或工具描述，并检查输出预留配置。"
        )

    working = list(messages)
    working_summary = summary_text
    current_tokens = _runtime_input_tokens(working, working_summary, static_input_tokens)
    if current_tokens <= guard_tokens:
        return working, working_summary, report

    original_count = len(working)
    working = _drop_rounds_before_current_turn(working, guard_tokens, working_summary, static_input_tokens)
    current_tokens = _runtime_input_tokens(working, working_summary, static_input_tokens)
    if current_tokens > guard_tokens and working_summary:
        working_summary = None
        report["hard_limit_summary_dropped"] = True

    working = _drop_current_turn_groups_to_limit(
        working,
        guard_tokens=guard_tokens,
        summary_text=working_summary,
        static_input_tokens=static_input_tokens,
    )
    current_tokens = _runtime_input_tokens(working, working_summary, static_input_tokens)
    if current_tokens > guard_tokens:
        working, truncated = _truncate_current_human_to_limit(
            working,
            guard_tokens=guard_tokens,
            summary_text=working_summary,
            static_input_tokens=static_input_tokens,
        )
        report["hard_limit_user_message_truncated"] = truncated
        current_tokens = _runtime_input_tokens(working, working_summary, static_input_tokens)

    report.update({
        "triggered": True,
        "hard_limit_enforced": True,
        "hard_limit_dropped_messages": max(original_count - len(working), 0),
        "estimated_tokens_after": current_tokens,
        "estimated_cleared_tokens": max(before_tokens - current_tokens, 0),
        "compressed_messages": max(
            _non_negative_int(report.get("compressed_messages")),
            max(original_count - len(working), 0),
        ),
        "target_reached": current_tokens <= _non_negative_int(report.get("compression_target_tokens")),
    })
    return working, working_summary, report


def _runtime_input_tokens(
    messages: list[BaseMessage],
    summary_text: str | None,
    static_input_tokens: int,
) -> int:
    return max(int(static_input_tokens), 0) + count_messages_tokens(messages) + _summary_tokens(summary_text)


def _drop_rounds_before_current_turn(
    messages: list[BaseMessage],
    guard_tokens: int,
    summary_text: str | None,
    static_input_tokens: int,
) -> list[BaseMessage]:
    working = list(messages)
    human_indexes = [index for index, message in enumerate(working) if isinstance(message, HumanMessage)]
    for start in human_indexes[1:]:
        candidate = working[start:]
        if _runtime_input_tokens(candidate, summary_text, static_input_tokens) <= guard_tokens:
            return candidate
    return working[human_indexes[-1]:] if human_indexes else working


def _drop_current_turn_groups_to_limit(
    messages: list[BaseMessage],
    *,
    guard_tokens: int,
    summary_text: str | None,
    static_input_tokens: int,
) -> list[BaseMessage]:
    working = list(messages)
    current_human_index = _last_human_index(working)
    if current_human_index >= len(working):
        return []
    current_human = working[current_human_index]
    tail = working[current_human_index + 1:]
    for boundary in _complete_boundaries(tail):
        candidate = [current_human, *tail[boundary:]]
        if _runtime_input_tokens(candidate, summary_text, static_input_tokens) <= guard_tokens:
            return candidate
    return [current_human]


def _truncate_current_human_to_limit(
    messages: list[BaseMessage],
    *,
    guard_tokens: int,
    summary_text: str | None,
    static_input_tokens: int,
) -> tuple[list[BaseMessage], bool]:
    human = next((message for message in reversed(messages) if isinstance(message, HumanMessage)), HumanMessage(content=""))
    message_limit = max(guard_tokens - static_input_tokens - _summary_tokens(summary_text), 1)
    if count_messages_tokens([human]) <= message_limit:
        return [human], False
    return [_truncate_human_message(human, message_limit)], True


def _truncate_human_message(message: HumanMessage, token_limit: int) -> HumanMessage:
    original = str(message.content or "")
    prefix = "[当前用户输入已按上下文安全水位截取，保留首尾内容]\n"
    left, right = 0, len(original)
    best = ""
    while left <= right:
        keep_chars = (left + right) // 2
        candidate = prefix + _build_edge_excerpt(original, keep_chars)
        patched = message.model_copy(update={"content": candidate})
        if count_messages_tokens([patched]) <= token_limit:
            best = candidate
            left = keep_chars + 1
        else:
            right = keep_chars - 1
    return message.model_copy(update={"content": best})


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
    event_fingerprint = "|".join([
        turn_id,
        anchor_message_id,
        str(_non_negative_int(report.get("estimated_tokens_before"))),
        str(_non_negative_int(report.get("estimated_tokens_after"))),
        str(_non_negative_int(report.get("compressed_messages"))),
    ])
    event_id = hashlib.sha256(event_fingerprint.encode("utf-8")).hexdigest()[:20]
    return {
        "id": f"context-compression-{event_id}",
        "turn_id": turn_id,
        "anchor_message_id": anchor_message_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "estimated_tokens_before": _non_negative_int(report.get("estimated_tokens_before")),
        "estimated_tokens_after": _non_negative_int(report.get("estimated_tokens_after")),
        "estimated_cleared_tokens": _non_negative_int(report.get("estimated_cleared_tokens")),
        "compressed_messages": _non_negative_int(report.get("compressed_messages")),
    }


def _emit_context_compression_event(
    event: dict[str, Any],
    *,
    archived_turn_messages: list[BaseMessage] | None = None,
) -> None:
    """在 LangGraph 流可用时立即推送分界事件，不影响压缩主流程。"""
    try:
        get_stream_writer()({
            "type": "context_compression",
            "event": event,
            "archived_turn_messages": list(archived_turn_messages or []),
        })
    except Exception:
        logger.debug("当前执行上下文不支持推送 context_compression custom 事件", exc_info=True)


def _message_id(message: BaseMessage) -> str:
    return str(getattr(message, "id", "") or "").strip()


def _current_turn_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Return the latest user turn before state compression mutates it."""
    current_human_index = _last_human_index(messages)
    if current_human_index >= len(messages):
        return []
    return list(messages[current_human_index:])


def _non_negative_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _partition_messages_for_compression(
    messages: list[BaseMessage],
    completed_end: int,
    *,
    current_human_index: int,
    static_input_tokens: int,
    compression_target_tokens: int,
) -> tuple[list[BaseMessage], list[BaseMessage], int]:
    """分离待摘要消息与保留消息，支持同一用户回合内的连续工具调用。

    当前回合的用户问题始终保留；当前回合较早的完整工具调用可以进入摘要，
    最新完整工具调用和尾部未完成消息保留，避免压缩时拆开 tool call/result。
    """
    bounded_end = min(max(int(completed_end), 0), len(messages))
    current_human_index = min(max(int(current_human_index), 0), len(messages))
    if current_human_index >= len(messages):
        retain_start = _recent_completed_round_start(
            messages[:bounded_end],
            bounded_end,
            static_input_tokens=static_input_tokens,
            compression_target_tokens=compression_target_tokens,
        )
        return (
            list(messages[:retain_start]),
            list(messages[retain_start:]),
            retain_start,
        )

    old_messages = list(messages[:current_human_index])
    current_completed_end = max(bounded_end, current_human_index + 1)
    current_messages = list(messages[current_human_index:current_completed_end])
    trailing_messages = list(messages[current_completed_end:])
    current_human = current_messages[0]
    current_tail_start = _select_current_turn_tail_start(
        current_messages,
        static_input_tokens=static_input_tokens,
        compression_target_tokens=compression_target_tokens,
    )
    retained_current = [current_human, *current_messages[current_tail_start:]]
    retained_current_tokens = count_messages_tokens(retained_current)
    remaining_keep_tokens = max(
        int(compression_target_tokens) - max(int(static_input_tokens), 0) - retained_current_tokens,
        0,
    )
    old_retain_start = _recent_completed_round_start(
        old_messages,
        len(old_messages),
        static_input_tokens=0,
        compression_target_tokens=remaining_keep_tokens,
    )
    retained_messages = [
        *old_messages[old_retain_start:],
        *retained_current,
        *trailing_messages,
    ]
    summarized_messages = [
        *old_messages[:old_retain_start],
        *current_messages[1:current_tail_start],
    ]
    return (
        summarized_messages,
        retained_messages,
        len(summarized_messages),
    )


def _select_current_turn_tail_start(
    current_messages: list[BaseMessage],
    *,
    static_input_tokens: int,
    compression_target_tokens: int,
) -> int:
    """选择当前回合要保留的最新完整消息边界。"""
    if len(current_messages) <= 1:
        return 1
    keep_limit = max(
        int(compression_target_tokens) - max(int(static_input_tokens), 0),
        0,
    )
    full_tokens = count_messages_tokens(current_messages)
    if full_tokens <= keep_limit:
        return 1
    boundaries = _complete_boundaries(current_messages[1:])
    if not boundaries:
        return 1
    for relative_end in boundaries[:-1]:
        candidate_start = 1 + relative_end
        candidate = [current_messages[0], *current_messages[candidate_start:]]
        if count_messages_tokens(candidate) <= keep_limit:
            return candidate_start
    # The newest completed model/tool group is required for the next model step.
    return 1 + (boundaries[-2] if len(boundaries) > 1 else 0)


def _complete_boundaries(messages: list[BaseMessage]) -> list[int]:
    """返回消息前缀中所有不含未完成工具调用的边界。"""
    return complete_message_boundaries(messages)


def _recent_completed_round_start(
    messages: list[BaseMessage],
    completed_end: int,
    *,
    static_input_tokens: int,
    compression_target_tokens: int,
) -> int:
    """保留不超过目标容量的最近完整轮次；工具调用与结果不会被拆开。"""
    message_keep_limit = max(int(compression_target_tokens) - max(int(static_input_tokens), 0), 0)
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
    for candidate in complete_message_boundaries(messages):
        boundary = max(boundary, candidate)
    return boundary


def _summary_tokens(summary_text: str | None) -> int:
    summary_message = build_runtime_context_summary_message(summary_text)
    return count_messages_tokens([summary_message]) if summary_message else 0


def _get_summary_input_limit_tokens(model_id: str | None) -> int:
    """摘要调用使用独立的输出预留，避免摘要请求本身超出模型窗口。"""
    budget = get_model_context_budget(
        model_id,
        max_output_tokens=get_context_compression_summary_output_max_tokens(),
    )
    return hard_input_guard_tokens(budget.hard_input_limit_tokens)


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
        "summary_input_unusable": False,
        "summary_generation_failed": False,
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
        report["summary_input_unusable"] = True
        return [], report
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
