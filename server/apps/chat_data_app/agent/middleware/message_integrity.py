"""Helpers for validating persisted model/tool message sequences."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


def message_tool_call_ids(message: BaseMessage) -> tuple[str, ...]:
    """Return distinct, non-empty tool call IDs declared by an AI message."""
    ids: list[str] = []
    seen: set[str] = set()
    for tool_call in getattr(message, "tool_calls", None) or []:
        if not isinstance(tool_call, dict):
            continue
        call_id = str(tool_call.get("id") or "").strip()
        if call_id and call_id not in seen:
            ids.append(call_id)
            seen.add(call_id)
    return tuple(ids)


def _collect_ai_group_result_ids(
    messages: list[BaseMessage],
    index: int,
    call_ids: tuple[str, ...],
) -> tuple[set[str], int]:
    """扫描 AI 工具调用组的连续 ToolMessage，返回 (命中的 result_ids, 越过后的索引)。"""
    result_ids: set[str] = set()
    next_index = index + 1
    while next_index < len(messages) and isinstance(messages[next_index], ToolMessage):
        tool_call_id = str(getattr(messages[next_index], "tool_call_id", "") or "").strip()
        if tool_call_id in call_ids:
            result_ids.add(tool_call_id)
        next_index += 1
    return result_ids, next_index


def complete_message_boundaries(messages: list[BaseMessage]) -> list[int]:
    """Return prefixes that do not end inside a tool-call group.

    A tool result only closes the immediately preceding AI tool-call group.
    Results found after another message are not allowed to repair that group.
    """
    boundaries: list[int] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            call_ids = message_tool_call_ids(message)
            if not call_ids:
                index += 1
                continue
            result_ids, next_index = _collect_ai_group_result_ids(messages, index, call_ids)
            if result_ids == set(call_ids):
                boundaries.append(next_index)
                index = next_index
                continue
            index += 1
            continue
        if isinstance(message, ToolMessage):
            index += 1
            continue
        boundaries.append(index + 1)
        index += 1
    return boundaries


def repair_tool_call_messages(messages: Sequence[BaseMessage]) -> tuple[list[BaseMessage], dict[str, Any]]:
    """Remove incomplete tool-call groups without fabricating tool results."""
    repaired: list[BaseMessage] = []
    removed_call_ids: list[str] = []
    removed_message_count = 0
    index = 0
    while index < len(messages):
        message = messages[index]
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            call_ids = message_tool_call_ids(message)
            if call_ids and _has_contiguous_tool_results(messages, index, call_ids):
                index, duplicate_removed = _consume_contiguous_tool_results(messages, index, repaired, call_ids)
                removed_message_count += duplicate_removed
                continue

            removed_call_ids.extend(call_ids or ("<missing-id>",))
            removed_message_count += 1
            skipped_index, orphan_removed = _skip_orphan_tool_results(messages, index + 1, call_ids)
            removed_message_count += orphan_removed
            index = skipped_index
            continue

        if isinstance(message, ToolMessage):
            removed_message_count += 1
            index += 1
            continue
        repaired.append(message)
        index += 1

    return repaired, {
        "repaired": removed_message_count > 0,
        "removed_call_ids": tuple(dict.fromkeys(removed_call_ids)),
        "removed_message_count": removed_message_count,
    }


def _consume_contiguous_tool_results(
    messages: Sequence[BaseMessage],
    index: int,
    repaired: list[BaseMessage],
    call_ids: tuple[str, ...],
) -> tuple[int, int]:
    """保留完整的 tool-call 组：AI 消息与连续且相关的 ToolMessage；返回 (新索引, 移除数)。"""
    repaired.append(messages[index])
    result_index = index + 1
    seen_results: set[str] = set()
    removed = 0
    while result_index < len(messages) and isinstance(messages[result_index], ToolMessage):
        tool_call_id = str(getattr(messages[result_index], "tool_call_id", "") or "").strip()
        if tool_call_id in call_ids and tool_call_id not in seen_results:
            repaired.append(messages[result_index])
            seen_results.add(tool_call_id)
        else:
            removed += 1
        result_index += 1
    return result_index, removed


def _skip_orphan_tool_results(
    messages: Sequence[BaseMessage],
    index: int,
    call_ids: tuple[str, ...],
) -> tuple[int, int]:
    """丢弃残缺 tool-call 组后续的孤儿 ToolMessage；返回 (新索引, 移除数)。"""
    removed = 0
    while index < len(messages) and isinstance(messages[index], ToolMessage):
        tool_call_id = str(getattr(messages[index], "tool_call_id", "") or "").strip()
        if not tool_call_id or tool_call_id in call_ids:
            removed += 1
            index += 1
            continue
        break
    return index, removed


def _has_contiguous_tool_results(
    messages: Sequence[BaseMessage],
    ai_index: int,
    call_ids: tuple[str, ...],
) -> bool:
    result_ids: set[str] = set()
    index = ai_index + 1
    while index < len(messages) and isinstance(messages[index], ToolMessage):
        tool_call_id = str(getattr(messages[index], "tool_call_id", "") or "").strip()
        if tool_call_id in call_ids:
            result_ids.add(tool_call_id)
        index += 1
    return result_ids == set(call_ids)
