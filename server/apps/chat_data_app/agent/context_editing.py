"""模型入口上下文编辑。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from .middleware.token_usage_middleware import count_messages_tokens, estimate_tokens


QUERY_RESULT_PATTERN = re.compile(r"<query_result>\s*(.*?)\s*</query_result>", re.DOTALL)


@dataclass(frozen=True)
class ContextEditConfig:
    enabled: bool
    trigger_ratio: float
    keep_recent_round_candidates: tuple[int, ...]


def _config_from_mapping(config: dict[str, Any] | None) -> ContextEditConfig:
    data = config or {}
    return ContextEditConfig(
        enabled=bool(data.get("enabled", True)),
        trigger_ratio=float(data.get("trigger_ratio", 0.70)),
        keep_recent_round_candidates=(10, 8, 5, 3, 1),
    )


def edit_messages_for_llm(
    messages: list[BaseMessage],
    *,
    context_window: int,
    config: dict[str, Any] | None = None,
) -> tuple[list[BaseMessage], dict[str, Any]]:
    """按上下文预算编辑发给模型的消息，不修改 checkpoint 中的原始消息。"""
    cfg = _config_from_mapping(config)
    original_messages = list(messages or [])
    before_tokens = count_messages_tokens(original_messages)
    trigger_tokens = max(int(max(context_window, 1) * cfg.trigger_ratio), 1)
    report = _build_report(cfg, before_tokens=before_tokens, after_tokens=before_tokens, triggered=False)
    if not cfg.enabled or before_tokens <= trigger_tokens:
        return original_messages, report

    edited_messages, cleared_tool_results = _summarize_old_tool_results_until_budget(
        original_messages,
        target_tokens=trigger_tokens,
    )
    after_tool_tokens = count_messages_tokens(edited_messages)
    history_trimmed = False
    kept_recent_rounds = None
    if after_tool_tokens > trigger_tokens:
        edited_messages, history_trimmed, kept_recent_rounds = _trim_old_rounds_until_budget(
            edited_messages,
            target_tokens=trigger_tokens,
            keep_round_candidates=cfg.keep_recent_round_candidates,
        )

    after_tokens = count_messages_tokens(edited_messages)
    report.update({
        "triggered": cleared_tool_results > 0 or history_trimmed,
        "cleared_tool_results": cleared_tool_results,
        "history_trimmed": history_trimmed,
        "kept_recent_rounds": kept_recent_rounds,
        "estimated_tokens_after": after_tokens,
        "estimated_cleared_tokens": max(before_tokens - after_tokens, 0),
    })
    return edited_messages, report


def _build_report(cfg: ContextEditConfig, *, before_tokens: int, after_tokens: int, triggered: bool) -> dict[str, Any]:
    return {
        "enabled": cfg.enabled,
        "triggered": triggered,
        "cleared_tool_results": 0,
        "history_trimmed": False,
        "estimated_tokens_before": before_tokens,
        "estimated_tokens_after": after_tokens,
        "estimated_cleared_tokens": max(before_tokens - after_tokens, 0),
        "trigger_ratio": cfg.trigger_ratio,
        "keep_recent_round_candidates": list(cfg.keep_recent_round_candidates),
        "kept_recent_rounds": None,
    }


def _current_turn_start_index(messages: list[BaseMessage]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return index
    return len(messages)


def _summarize_old_tool_results_until_budget(messages: list[BaseMessage], target_tokens: int) -> tuple[list[BaseMessage], int]:
    current_turn_start = _current_turn_start_index(messages)
    tool_indexes = [
        index
        for index, message in enumerate(messages[:current_turn_start])
        if isinstance(message, ToolMessage)
    ]
    if not tool_indexes:
        return list(messages), 0
    edited = list(messages)
    cleared = 0
    for index in tool_indexes:
        if count_messages_tokens(edited) <= target_tokens:
            break
        message = edited[index]
        if isinstance(message, ToolMessage):
            edited[index] = _tool_result_placeholder(message)
            cleared += 1
    return edited, cleared


def _trim_old_rounds_until_budget(
    messages: list[BaseMessage],
    *,
    target_tokens: int,
    keep_round_candidates: tuple[int, ...],
) -> tuple[list[BaseMessage], bool, int | None]:
    for keep_rounds in keep_round_candidates:
        trimmed, trimmed_history = _trim_old_rounds(messages, keep_rounds)
        if not trimmed_history:
            continue
        if count_messages_tokens(trimmed) <= target_tokens:
            return trimmed, True, keep_rounds
    trimmed, trimmed_history = _trim_old_rounds(messages, keep_round_candidates[-1])
    return trimmed, trimmed_history, keep_round_candidates[-1] if trimmed_history else None


def _trim_old_rounds(messages: list[BaseMessage], keep_recent_rounds: int) -> tuple[list[BaseMessage], bool]:
    current_turn_start = _current_turn_start_index(messages)
    historical_messages = messages[:current_turn_start]
    protected_start = _recent_round_start_index(historical_messages, keep_recent_rounds)
    if protected_start <= 0:
        return messages, False
    summary = _history_trim_placeholder(messages[:protected_start])
    return [summary, *messages[protected_start:]], True


def _recent_round_start_index(messages: list[BaseMessage], keep_recent_rounds: int) -> int:
    if keep_recent_rounds < 1:
        return len(messages)
    human_indexes = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, HumanMessage)
    ]
    if len(human_indexes) <= keep_recent_rounds:
        return 0
    return human_indexes[-keep_recent_rounds]


def _history_trim_placeholder(trimmed_messages: list[BaseMessage]) -> HumanMessage:
    human_count = sum(1 for message in trimmed_messages if isinstance(message, HumanMessage))
    assistant_count = sum(1 for message in trimmed_messages if isinstance(message, AIMessage))
    tool_count = sum(1 for message in trimmed_messages if isinstance(message, ToolMessage))
    return HumanMessage(
        content=(
            "[历史消息已移出模型上下文]\n"
            f"移出消息数={len(trimmed_messages)}；用户消息={human_count}；助手消息={assistant_count}；工具结果={tool_count}。\n"
            "完整历史仍保留在会话记录中；如需旧查询细节，请重新查询或打开历史结果。"
        ),
        additional_kwargs={"context_editing_placeholder": "trimmed_history"},
    )


def _tool_result_placeholder(message: ToolMessage) -> ToolMessage:
    content = _summarize_tool_result(message)
    try:
        return message.model_copy(update={"content": content})
    except Exception:
        return ToolMessage(
            content=content,
            tool_call_id=str(getattr(message, "tool_call_id", "") or ""),
            name=getattr(message, "name", None),
            id=getattr(message, "id", None),
            additional_kwargs=dict(getattr(message, "additional_kwargs", None) or {}),
        )


def _summarize_tool_result(message: ToolMessage) -> str:
    raw_content = str(getattr(message, "content", "") or "")
    payload = _parse_tool_payload(raw_content)
    parts = [
        "[工具结果已移出模型上下文]",
        f"tool_name={getattr(message, 'name', None) or '-'}",
        f"tool_call_id={getattr(message, 'tool_call_id', None) or '-'}",
        f"original_size_chars={len(raw_content)}",
    ]
    summary = _query_payload_summary(payload)
    if summary:
        parts.append(summary)
    else:
        preview = raw_content.replace("\r", "\\r").replace("\n", "\\n")[:300]
        if preview:
            parts.append(f"preview={preview}")
    parts.append("完整结果未发送给模型；如需细节，请重新查询或打开历史结果。")
    return "\n".join(parts)


def _parse_tool_payload(content: str) -> Any:
    text = str(content or "").strip()
    match = QUERY_RESULT_PATTERN.search(text)
    if match:
        text = match.group(1).strip()
    elif text.startswith("[semantic_query_tool]"):
        text = text[len("[semantic_query_tool]"):].strip()
    try:
        return json.loads(text)
    except Exception:
        return None


def _query_payload_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    fields: list[str] = []
    for key in ("success", "artifact_id", "total_count", "execution_time_ms", "query_mode", "confidence", "error"):
        value = payload.get(key)
        if value not in (None, ""):
            fields.append(f"{key}={value}")
    columns = payload.get("columns")
    if isinstance(columns, list) and columns:
        fields.append(f"columns={_format_columns(columns)}")
    sql = str(payload.get("sql") or "").strip()
    if sql:
        fields.append(f"sql={sql[:500]}")
    if not fields:
        return ""
    return "summary=" + "；".join(fields)


def _format_columns(columns: list[Any]) -> str:
    names: list[str] = []
    for column in columns[:20]:
        if isinstance(column, dict):
            name = column.get("name") or column.get("label") or column.get("field")
            if name:
                names.append(str(name))
        elif column not in (None, ""):
            names.append(str(column))
    suffix = "..." if len(columns) > 20 else ""
    return ",".join(names) + suffix
