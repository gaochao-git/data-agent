"""Context editing strategies for model requests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage

from ...services.context_budget import count_messages_tokens, estimate_tokens


QUERY_RESULT_PATTERN = re.compile(r"<query_result>\s*(.*?)\s*</query_result>", re.DOTALL)


@dataclass(frozen=True)
class ContextEditConfig:
    enabled: bool
    trigger_ratio: float
    tool_result_offload_size: int


def _config_from_mapping(config: dict[str, Any] | None) -> ContextEditConfig:
    data = config or {}
    return ContextEditConfig(
        enabled=bool(data.get("enabled", True)),
        trigger_ratio=float(data.get("trigger_ratio", 0.70)),
        tool_result_offload_size=max(int(data.get("tool_result_offload_size") or 32000), 1),
    )


def edit_messages_for_llm(
    messages: list[BaseMessage],
    *,
    context_window: int,
    config: dict[str, Any] | None = None,
    message_budget_tokens: int | None = None,
) -> tuple[list[BaseMessage], dict[str, Any]]:
    """裁剪单条大工具结果，不修改 checkpoint 原始消息。"""
    cfg = _config_from_mapping(config)
    original_messages = list(messages or [])
    before_tokens = count_messages_tokens(original_messages)
    trigger_tokens = max(
        int(message_budget_tokens)
        if message_budget_tokens is not None
        else int(max(context_window, 1) * cfg.trigger_ratio),
        1,
    )
    report = _build_report(
        cfg,
        before_tokens=before_tokens,
        after_tokens=before_tokens,
        triggered=False,
        message_budget_tokens=trigger_tokens,
    )
    if not cfg.enabled:
        return original_messages, report

    edited_messages, cleared_tool_results = _summarize_large_tool_results(
        original_messages,
        max_content_chars=cfg.tool_result_offload_size,
    )
    after_tokens = count_messages_tokens(edited_messages)
    report.update({
        "triggered": cleared_tool_results > 0,
        "cleared_tool_results": cleared_tool_results,
        "estimated_tokens_after": after_tokens,
        "estimated_cleared_tokens": max(before_tokens - after_tokens, 0),
    })
    return edited_messages, report


def _build_report(
    cfg: ContextEditConfig,
    *,
    before_tokens: int,
    after_tokens: int,
    triggered: bool,
    message_budget_tokens: int,
) -> dict[str, Any]:
    return {
        "enabled": cfg.enabled,
        "triggered": triggered,
        "cleared_tool_results": 0,
        "estimated_tokens_before": before_tokens,
        "estimated_tokens_after": after_tokens,
        "estimated_cleared_tokens": max(before_tokens - after_tokens, 0),
        "message_budget_tokens": message_budget_tokens,
        "trigger_ratio": cfg.trigger_ratio,
        "tool_result_offload_size": cfg.tool_result_offload_size,
    }


def _summarize_large_tool_results(messages: list[BaseMessage], max_content_chars: int) -> tuple[list[BaseMessage], int]:
    tool_indexes = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, ToolMessage)
        and len(str(getattr(message, "content", "") or "")) > max_content_chars
    ]
    if not tool_indexes:
        return list(messages), 0
    edited = list(messages)
    for index in tool_indexes:
        message = edited[index]
        if isinstance(message, ToolMessage):
            edited[index] = _tool_result_placeholder(message)
    return edited, len(tool_indexes)


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
