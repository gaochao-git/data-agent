"""子智能体过程快照：把子图消息序列化成前端可回放的过程条目。"""

from __future__ import annotations

from itertools import count
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from apps.chat_data_app.services.runtime_config import (
    SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS,
    SUBAGENT_RESULT_PREVIEW_MAX_CHARS,
    SUBAGENT_THINKING_PREVIEW_MAX_CHARS,
)
from utils.tool_call_args import coerce_tool_call_args

from .subagent_async import _truncate_text
from .subagent_text import chunk_reasoning_text, plain_text, sanitize_report_text


def _read_tool_timing(message: ToolMessage) -> dict[str, int]:
    """读取 ToolTimingMiddleware 盖在 ToolMessage 上的起止时间（epoch 毫秒）。"""
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    timing: dict[str, int] = {}
    for key in ("started_at_ms", "ended_at_ms"):
        value = additional_kwargs.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            continue
        timing[key] = value
    return timing


def _collect_subagent_tool_results(messages: list) -> dict[str, dict[str, Any]]:
    tool_results: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        tool_call_id = str(getattr(message, "tool_call_id", "") or "")
        if not tool_call_id:
            continue
        raw = plain_text(getattr(message, "content", ""))
        tool_results[tool_call_id] = {
            "status": "error" if str(getattr(message, "status", "") or "") == "error" else "completed",
            "result": _truncate_text(raw, SUBAGENT_RESULT_PREVIEW_MAX_CHARS),
            **_read_tool_timing(message),
        }
    return tool_results


def _trail_message_entry(message: AIMessage, message_id: str) -> dict[str, Any]:
    text = sanitize_report_text(plain_text(getattr(message, "content", "")))
    thinking = chunk_reasoning_text(message)
    return {
        "kind": "message",
        "id": message_id,
        "content": _truncate_text(text, SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS, "内容") if text.strip() else "",
        "truncated": len(text) > SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS,
        "thinking": _truncate_text(thinking, SUBAGENT_THINKING_PREVIEW_MAX_CHARS, "思考") if thinking.strip() else "",
        "thinkingTruncated": len(thinking) > SUBAGENT_THINKING_PREVIEW_MAX_CHARS,
    }


def _trail_tool_entry(tool_call: dict[str, Any], tool_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tool_call_id = str(tool_call.get("id") or "")
    outcome = tool_results.get(tool_call_id) or {}
    entry = {
        "kind": "tool",
        "id": tool_call_id,
        "tool_name": str(tool_call.get("name") or ""),
        "args": coerce_tool_call_args(tool_call.get("args")) or {},
        "result": outcome.get("result", ""),
        "status": outcome.get("status", "completed"),
    }
    for key in ("started_at_ms", "ended_at_ms"):
        if isinstance(outcome.get(key), int):
            entry[key] = outcome[key]
    return entry


def _trail_orphan_tool_entry(
    message: ToolMessage,
    tool_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    tool_call_id = str(getattr(message, "tool_call_id", "") or "")
    outcome = tool_results[tool_call_id]
    entry = {
        "kind": "tool",
        "id": tool_call_id,
        "tool_name": str(getattr(message, "name", "") or ""),
        "args": {},
        "result": outcome["result"],
        "status": outcome["status"],
    }
    for key in ("started_at_ms", "ended_at_ms"):
        if isinstance(outcome.get(key), int):
            entry[key] = outcome[key]
    return entry


def _append_ai_message_trail(
    message: AIMessage,
    tool_results: dict[str, dict[str, Any]],
    trail: list[dict[str, Any]],
    seen_ids: set[str],
    anonymous_seq: count,
) -> None:
    message_id = str(getattr(message, "id", "") or "") or f"trail-anon-{next(anonymous_seq)}"
    text = plain_text(getattr(message, "content", ""))
    thinking = chunk_reasoning_text(message)
    if (text.strip() or thinking.strip()) and message_id not in seen_ids:
        seen_ids.add(message_id)
        trail.append(_trail_message_entry(message, message_id))
    for tool_call in getattr(message, "tool_calls", None) or []:
        if not isinstance(tool_call, dict):
            continue
        tool_call_id = str(tool_call.get("id") or "")
        if not tool_call_id or tool_call_id in seen_ids:
            continue
        seen_ids.add(tool_call_id)
        trail.append(_trail_tool_entry(tool_call, tool_results))


def _build_trail_snapshot(messages: list, compactions: Any = None) -> list[dict[str, Any]]:
    """把子图消息序列化成与前端 ``SubagentTrailItem`` 同构的过程快照。

    用于历史回放：直播时过程经 SSE custom 流逐条转发，刷新/看历史后由
    task ToolMessage 的 ``subagent_trail`` 还原。截断口径与 SSE 预览共用
    同一套常量，保证用户看到的直播内容与历史内容一致。

    - AIMessage → ``message`` 条目（content + reasoning 合并为一条，text 与
      tool_calls 按「先说后做」输出，顺序与 ``_iter_subagent_stream_events``
      一致）
    - ToolMessage → 回填对应内层工具的终态 status 与结果预览
    """
    tool_results = _collect_subagent_tool_results(messages)

    trail: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    anonymous_seq = count(1)
    for message in messages:
        if isinstance(message, AIMessage):
            _append_ai_message_trail(message, tool_results, trail, seen_ids, anonymous_seq)
        elif isinstance(message, ToolMessage):
            # 内层工具先产生 ToolMessage 后才出现对应 tool_calls 的情形不
            # 应发生（OpenAI 协议要求先声明后调用）；此处只为容忍乱序输入。
            tool_call_id = str(getattr(message, "tool_call_id", "") or "")
            if tool_call_id and tool_call_id not in seen_ids:
                seen_ids.add(tool_call_id)
                trail.append(_trail_orphan_tool_entry(message, tool_results))
    return _insert_trail_compactions(trail, compactions)


def _compaction_trail_entry(compaction: Any) -> dict[str, Any] | None:
    """压缩事件 → 过程快照里的分隔卡条目。"""
    if not isinstance(compaction, dict):
        return None
    return {
        "kind": "compaction",
        "id": str(compaction.get("id") or ""),
        "timestamp": str(compaction.get("timestamp") or ""),
        "estimated_tokens_before": int(compaction.get("estimated_tokens_before") or 0),
        "estimated_tokens_after": int(compaction.get("estimated_tokens_after") or 0),
        "estimated_cleared_tokens": int(compaction.get("estimated_cleared_tokens") or 0),
        "compressed_messages": int(compaction.get("compressed_messages") or 0),
        "anchor_message_id": str(compaction.get("anchor_message_id") or ""),
    }


def _trail_insert_index(trail: list[dict[str, Any]], anchor: str) -> int:
    """锚点消息之后插入；找不到锚点则放在末尾（压缩发生在最新内容附近）。"""
    if not anchor:
        return len(trail)
    for index, item in enumerate(trail):
        if str(item.get("id") or "") == anchor:
            return index + 1
    return len(trail)


def _insert_trail_compactions(trail: list[dict[str, Any]], compactions: Any) -> list[dict[str, Any]]:
    """把压缩事件按锚点消息位置插进过程快照，历史回放与主对话一样在流程中看到分隔卡。"""
    if not isinstance(compactions, list) or not compactions:
        return trail
    merged = list(trail)
    previous = -2
    for compaction in compactions:
        entry = _compaction_trail_entry(compaction)
        if entry is None:
            continue
        index = _trail_insert_index(merged, entry["anchor_message_id"])
        if index <= previous + 1:
            # 锚点缺失或重复（压缩后历史消息被删、多次压缩共享同一锚点）时，
            # 至少隔开一条真实过程条目，避免多张分隔卡堆在一起看不出发生位置。
            index = min(previous + 2, len(merged))
        merged.insert(index, entry)
        previous = index
    return merged
