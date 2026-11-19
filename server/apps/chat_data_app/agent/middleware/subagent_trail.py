"""子智能体过程快照：把子图消息序列化成前端可回放的过程条目。"""

from __future__ import annotations

import hashlib
import json
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


def anonymous_message_id(message: Any) -> str:
    """生成匿名消息的内容指纹；重复消息由分配器追加出现序号区分。"""
    tool_calls: list[dict[str, Any]] = []
    for call in getattr(message, "tool_calls", None) or []:
        if isinstance(call, dict):
            tool_calls.append(
                {
                    "id": str(call.get("id") or ""),
                    "name": str(call.get("name") or ""),
                    "args": call.get("args") or {},
                }
            )
    payload = json.dumps(
        {
            "type": str(getattr(message, "type", "") or ""),
            "content": plain_text(getattr(message, "content", "")),
            "thinking": chunk_reasoning_text(message),
            "tool_calls": tool_calls,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return f"anon-{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]}"


class AnonymousMessageIdAllocator:
    """按同内容出现次序分配稳定 ID，并回写消息供后续检查点复用。"""

    def __init__(self) -> None:
        self._occurrences: dict[str, int] = {}
        self._used_ids: set[str] = set()
        self._by_object: dict[int, str] = {}

    def prime(self, messages: list[Any]) -> None:
        """预占历史中无 id 消息的出现序号，保证追问新消息与最终快照对齐。"""
        self._occurrences.clear()
        self._used_ids.clear()
        self._by_object.clear()
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            existing = str(getattr(message, "id", "") or "")
            if existing:
                self._used_ids.add(existing)
                self._by_object[id(message)] = existing
                continue
            fingerprint = anonymous_message_id(message)
            occurrence = self._occurrences.get(fingerprint, 0) + 1
            self._occurrences[fingerprint] = occurrence
            message_id = fingerprint if occurrence == 1 else f"{fingerprint}-{occurrence}"
            self._used_ids.add(message_id)
            self._by_object[id(message)] = message_id

    def resolve(self, message: AIMessage) -> str:
        primed = self._by_object.get(id(message))
        if primed:
            if not str(getattr(message, "id", "") or ""):
                message.id = primed
            return primed
        existing = str(getattr(message, "id", "") or "")
        if existing:
            self._used_ids.add(existing)
            self._by_object[id(message)] = existing
            return existing
        fingerprint = anonymous_message_id(message)
        occurrence = self._occurrences.get(fingerprint, 0)
        while True:
            occurrence += 1
            message_id = fingerprint if occurrence == 1 else f"{fingerprint}-{occurrence}"
            if message_id not in self._used_ids:
                break
        self._occurrences[fingerprint] = occurrence
        self._used_ids.add(message_id)
        self._by_object[id(message)] = message_id
        message.id = message_id
        return message_id


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


def _trail_tool_entry(
    tool_call: dict[str, Any],
    tool_results: dict[str, dict[str, Any]],
    pending_status: str = "completed",
) -> dict[str, Any]:
    tool_call_id = str(tool_call.get("id") or "")
    outcome = tool_results.get(tool_call_id) or {}
    entry = {
        "kind": "tool",
        "id": tool_call_id,
        "tool_name": str(tool_call.get("name") or ""),
        "args": coerce_tool_call_args(tool_call.get("args")) or {},
        "result": outcome.get("result", ""),
        "status": outcome.get("status", pending_status),
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
    anonymous_ids: AnonymousMessageIdAllocator,
    pending_tool_status: str = "completed",
) -> None:
    message_id = anonymous_ids.resolve(message)
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
        trail.append(_trail_tool_entry(tool_call, tool_results, pending_tool_status))


def _build_trail_snapshot(
    messages: list,
    compactions: Any = None,
    *,
    pending_tool_status: str = "completed",
) -> list[dict[str, Any]]:
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
    anonymous_ids = AnonymousMessageIdAllocator()
    for message in messages:
        if isinstance(message, AIMessage):
            _append_ai_message_trail(message, tool_results, trail, seen_ids, anonymous_ids, pending_tool_status)
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


def _trail_anchor_index(trail: list[dict[str, Any]], anchor: str) -> int:
    """返回锚点条目之后的位置；锚点已被后续压缩清掉时返回 -1。"""
    if not anchor:
        return -1
    for index, item in enumerate(trail):
        if str(item.get("id") or "") == anchor:
            return index + 1
    return -1


def _compaction_targets(trail: list[dict[str, Any]], compactions: list[Any]) -> list[tuple[int, dict[str, Any]]]:
    """按发生顺序算出每张压缩卡的目标位置，再按位置升序返回。

    快照是事后从最终消息重建的，压缩会删掉旧消息，所以早期压缩的锚点在
    最终 trail 里经常已不存在。锚点失效时借用它之后最早的一个已知位置，
    保持压缩卡的发生顺序；全部失效则均匀铺在过程尾部。注意不能拿绝对下标
    跟上一张卡比较来判断「堆叠」——真实锚点本来就可能落在上一张卡之前，
    那样会把所有卡推到末尾挤成一团。
    """
    raw: list[tuple[int, dict[str, Any]]] = []
    for compaction in compactions:
        entry = _compaction_trail_entry(compaction)
        if entry is None:
            continue
        raw.append((_trail_anchor_index(trail, entry["anchor_message_id"]), entry))
    found = [target for target, _ in raw if target >= 0]
    fallback = min(found) if found else max(len(trail) - len(raw), 0)
    positioned: list[tuple[int, int, dict[str, Any]]] = []
    for order, (target, entry) in enumerate(raw):
        if target >= 0:
            positioned.append((target, order, entry))
        elif found:
            positioned.append((fallback, order, entry))
        else:
            positioned.append((min(fallback + order, len(trail)), order, entry))
    positioned.sort(key=lambda item: (item[0], item[1]))
    return [(target, entry) for target, _, entry in positioned]


def _insert_trail_compactions(trail: list[dict[str, Any]], compactions: Any) -> list[dict[str, Any]]:
    """把压缩事件按锚点消息位置插进过程快照，历史回放与主对话一样在流程中看到分隔卡。"""
    if not isinstance(compactions, list) or not compactions:
        return trail
    base = list(trail)
    merged = list(base)
    previous = -2
    previous_target = -1
    for offset, (target, entry) in enumerate(_compaction_targets(base, compactions)):
        index = min(target + offset, len(merged))
        if target == previous_target and index <= previous + 1:
            # 目标位置相同（锚点重复或同时失效）时至少隔开一条真实过程条目。
            index = min(previous + 2, len(merged))
        merged.insert(index, entry)
        previous = index
        previous_target = target
    return merged
