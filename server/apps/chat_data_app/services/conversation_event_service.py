"""会话原始事件的持久化与历史恢复服务。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from utils.database import get_session
from utils.logger import get_logger
from ..daos.conversation_event_dao import ConversationEventDao

logger = get_logger(__name__)

EVENT_USER_MESSAGE = "user_message"
EVENT_REASONING = "reasoning"
EVENT_ASSISTANT_MESSAGE = "assistant_message"
EVENT_TOOL_RESULT = "tool_result"
EVENT_CONTEXT_COMPRESSION = "context_compression"

_HIDDEN_TOOLS = {"load_skill_tool"}


class ConversationEventService:
    """将 Agent 完整消息转换为追加式历史事件。"""

    def __init__(self, dao: ConversationEventDao | None = None):
        self._dao = dao or ConversationEventDao(session_context=lambda: get_session())

    async def persist_user_message(
        self,
        *,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        message: HumanMessage,
    ) -> None:
        turn_id = _message_id(message) or ""
        if not turn_id:
            logger.warning("跳过无 ID 的用户历史消息: conversation_id=%s", conversation_id)
            return
        await self._append_best_effort(
            conversation_id=conversation_id,
            user_id=user_id,
            operator_name=operator_name,
            events=[_event(
                turn_id=turn_id,
                event_key=f"user:{turn_id}",
                event_type=EVENT_USER_MESSAGE,
                payload=_serialize_human_message(message),
            )],
        )

    async def persist_turn(
        self,
        *,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        turn_id: str,
        messages: Iterable[BaseMessage],
        reasoning_segments: Iterable[dict[str, Any]],
        context_compression_events: Iterable[dict[str, Any]] = (),
    ) -> None:
        events = _build_turn_events(
            turn_id=turn_id,
            messages=list(messages or []),
            reasoning_segments=list(reasoning_segments or []),
            context_compression_events=list(context_compression_events or []),
        )
        if not events:
            return
        await self._append_best_effort(
            conversation_id=conversation_id,
            user_id=user_id,
            operator_name=operator_name,
            events=events,
        )

    async def backfill_checkpoint_history(
        self,
        *,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        messages: Iterable[BaseMessage],
        reasoning_segments: Iterable[dict[str, Any]],
        context_compression_events: Iterable[dict[str, Any]] = (),
    ) -> None:
        """将旧 checkpoint 中仍可读取的原始消息幂等回填到事件表。"""
        raw_messages = list(messages or [])
        raw_segments = list(reasoning_segments or [])
        turn_ids = [
            turn_id
            for message in raw_messages
            if isinstance(message, HumanMessage) and (turn_id := _message_id(message))
        ]
        scoped_segments = [
            segment for segment in raw_segments
            if str((segment or {}).get("reasoning_turn_id") or "").strip()
        ]
        legacy_groups = _split_legacy_reasoning_segments(raw_segments)
        events: list[dict[str, Any]] = []
        for turn_index, turn_id in enumerate(turn_ids):
            # 旧数据没有轮次 ID 时，沿用历史页面按 tool_call_count 重置分组的规则。
            turn_segments = [
                *scoped_segments,
                *(legacy_groups[turn_index] if turn_index < len(legacy_groups) else []),
            ]
            events.extend(_build_turn_events(
                turn_id=turn_id,
                messages=raw_messages,
                reasoning_segments=turn_segments,
                context_compression_events=list(context_compression_events or []),
            ))
        if events:
            await self._append_best_effort(
                conversation_id=conversation_id,
                user_id=user_id,
                operator_name=operator_name,
                events=events,
            )

    async def load_history(self, *, conversation_id: str, user_id: str) -> dict[str, list[dict[str, Any]]]:
        try:
            rows = await self._dao.list_events(conversation_id=conversation_id, user_id=user_id)
        except Exception:
            # 新表未迁移或短暂数据库错误时，调用方继续兼容读取 checkpoint 历史。
            logger.exception("读取会话原始事件失败: conversation_id=%s", conversation_id)
            return {"messages": [], "reasoning_segments": [], "history_events": []}
        messages: list[dict[str, Any]] = []
        reasoning_segments: list[dict[str, Any]] = []
        history_events: list[dict[str, Any]] = []
        for row in rows:
            payload = _load_payload(row.payload)
            if not payload:
                continue
            history_events.append({
                "sequence_no": int(row.sequence_no),
                "turn_id": row.turn_id,
                "event_type": row.event_type,
                "payload": payload,
            })
            if row.event_type == EVENT_REASONING:
                reasoning_segments.append(payload)
            elif row.event_type in {EVENT_USER_MESSAGE, EVENT_ASSISTANT_MESSAGE, EVENT_TOOL_RESULT}:
                messages.append(payload)
        return {
            "messages": messages,
            "reasoning_segments": reasoning_segments,
            "history_events": history_events,
        }

    async def _append_best_effort(
        self,
        *,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        events: list[dict[str, Any]],
    ) -> None:
        try:
            await self._dao.append_events(
                conversation_id=conversation_id,
                user_id=user_id,
                operator_name=operator_name,
                events=events,
                now=datetime.now(),
            )
        except Exception:
            # 事件历史不能使已开始的 Agent 对话失败；下次完整持久化会按 event_key 补齐。
            logger.exception("追加会话原始事件失败: conversation_id=%s", conversation_id)


def _build_turn_events(
    *,
    turn_id: str,
    messages: list[BaseMessage],
    reasoning_segments: list[dict[str, Any]],
    context_compression_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    start_index = _turn_start_index(messages, turn_id)
    if start_index is None:
        return []
    turn_end = len(messages)
    for index, message in enumerate(messages[start_index + 1 :], start=start_index + 1):
        if isinstance(message, HumanMessage):
            turn_end = index
            break
    turn_messages = messages[start_index:turn_end]
    pending_reasoning = _normalize_reasoning_segments(reasoning_segments, turn_id)
    compression_events = _normalize_context_compression_events(context_compression_events or [], turn_id)
    compression_events_by_anchor: dict[str, list[dict[str, Any]]] = {}
    unanchored_compression_events: list[dict[str, Any]] = []
    for event in compression_events:
        anchor_message_id = str(event.get("anchor_message_id") or "").strip()
        if anchor_message_id:
            compression_events_by_anchor.setdefault(anchor_message_id, []).append(event)
        else:
            unanchored_compression_events.append(event)
    events: list[dict[str, Any]] = []
    next_reasoning_index = 0
    visible_tool_count = 0

    def append_ready_reasoning() -> None:
        nonlocal next_reasoning_index
        while (
            next_reasoning_index < len(pending_reasoning)
            and int(pending_reasoning[next_reasoning_index].get("tool_call_count") or 0) <= visible_tool_count
        ):
            segment = pending_reasoning[next_reasoning_index]
            segment_id = str(segment["id"])
            events.append(_event(
                turn_id=turn_id,
                event_key=f"reasoning:{segment_id}",
                event_type=EVENT_REASONING,
                payload=segment,
            ))
            next_reasoning_index += 1

    def append_context_compression_events(anchor_message_id: str) -> None:
        for event in compression_events_by_anchor.pop(anchor_message_id, []):
            event_id = str(event["id"])
            events.append(_event(
                turn_id=turn_id,
                event_key=f"context_compression:{event_id}",
                event_type=EVENT_CONTEXT_COMPRESSION,
                payload=event,
            ))

    for message_index, message in enumerate(turn_messages):
        if isinstance(message, HumanMessage):
            message_id = _message_id(message)
            if message_id:
                events.append(_event(
                    turn_id=turn_id,
                    event_key=f"user:{message_id}",
                    event_type=EVENT_USER_MESSAGE,
                    payload=_serialize_human_message(message),
                ))
                append_context_compression_events(message_id)
                if unanchored_compression_events:
                    for event in unanchored_compression_events:
                        event_id = str(event["id"])
                        events.append(_event(
                            turn_id=turn_id,
                            event_key=f"context_compression:{event_id}",
                            event_type=EVENT_CONTEXT_COMPRESSION,
                            payload=event,
                        ))
                    unanchored_compression_events = []
            continue
        if isinstance(message, AIMessage):
            append_ready_reasoning()
            message_id = _message_id(message) or f"{turn_id}:ai:{message_index}"
            events.append(_event(
                turn_id=turn_id,
                event_key=f"assistant:{message_id}",
                event_type=EVENT_ASSISTANT_MESSAGE,
                payload=_serialize_ai_message(message),
            ))
            visible_tool_count += sum(
                1 for tool_call in getattr(message, "tool_calls", None) or []
                if str((tool_call or {}).get("name") or "") not in _HIDDEN_TOOLS
            )
            append_context_compression_events(message_id)
            continue
        if isinstance(message, ToolMessage):
            message_id = _message_id(message) or f"{turn_id}:tool:{message_index}"
            events.append(_event(
                turn_id=turn_id,
                event_key=f"tool:{message_id}",
                event_type=EVENT_TOOL_RESULT,
                payload=_serialize_tool_message(message),
            ))
            append_context_compression_events(message_id)

    append_ready_reasoning()
    for pending_events in compression_events_by_anchor.values():
        unanchored_compression_events.extend(pending_events)
    for event in unanchored_compression_events:
        event_id = str(event["id"])
        events.append(_event(
            turn_id=turn_id,
            event_key=f"context_compression:{event_id}",
            event_type=EVENT_CONTEXT_COMPRESSION,
            payload=event,
        ))
    return events


def _turn_start_index(messages: list[BaseMessage], turn_id: str) -> int | None:
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage) and _message_id(message) == turn_id:
            return index
    return None


def _normalize_reasoning_segments(segments: list[dict[str, Any]], turn_id: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(segments):
        if not isinstance(raw, dict):
            continue
        segment_turn_id = str(raw.get("reasoning_turn_id") or "").strip()
        if segment_turn_id and segment_turn_id != turn_id:
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        normalized.append({
            "id": str(raw.get("id") or f"reasoning-{turn_id}-{index}"),
            "content": content,
            "tool_call_count": _normalize_tool_call_count(raw.get("tool_call_count")),
            "reasoning_turn_id": turn_id,
        })
    return normalized


def _normalize_context_compression_events(
    events: list[dict[str, Any]],
    turn_id: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_event in events:
        if not isinstance(raw_event, dict):
            continue
        event_id = str(raw_event.get("id") or "").strip()
        event_turn_id = str(raw_event.get("turn_id") or "").strip()
        if not event_id or event_id in seen_ids or event_turn_id != turn_id:
            continue
        seen_ids.add(event_id)
        normalized.append({
            "id": event_id,
            "turn_id": turn_id,
            "anchor_message_id": str(raw_event.get("anchor_message_id") or "").strip(),
            "timestamp": str(raw_event.get("timestamp") or "").strip(),
            "estimated_tokens_before": _non_negative_int(raw_event.get("estimated_tokens_before")),
            "estimated_tokens_after": _non_negative_int(raw_event.get("estimated_tokens_after")),
            "estimated_cleared_tokens": _non_negative_int(raw_event.get("estimated_cleared_tokens")),
            "compressed_messages": _non_negative_int(raw_event.get("compressed_messages")),
        })
    return normalized


def _split_legacy_reasoning_segments(segments: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """按历史 UI 的规则分组没有 ``reasoning_turn_id`` 的旧思考分段。"""
    groups: list[list[dict[str, Any]]] = []
    for raw in segments:
        if not isinstance(raw, dict) or str(raw.get("reasoning_turn_id") or "").strip():
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        normalized = dict(raw)
        normalized["content"] = content
        normalized["tool_call_count"] = _normalize_tool_call_count(raw.get("tool_call_count"))
        current_group = groups[-1] if groups else None
        previous = current_group[-1] if current_group else None
        if current_group is None or normalized["tool_call_count"] <= int(previous.get("tool_call_count") or 0):
            groups.append([normalized])
        else:
            current_group.append(normalized)
    return groups


def _event(*, turn_id: str, event_key: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "turn_id": turn_id,
        "event_key": event_key,
        "event_type": event_type,
        "payload": json.dumps(payload, ensure_ascii=False, default=str),
    }


def _normalize_tool_call_count(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _non_negative_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _message_id(message: BaseMessage) -> str:
    return str(getattr(message, "id", "") or "").strip()


def _timestamp(message: BaseMessage) -> str:
    return str((getattr(message, "additional_kwargs", None) or {}).get("timestamp") or "")


def _serialize_human_message(message: HumanMessage) -> dict[str, Any]:
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    payload: dict[str, Any] = {
        "id": _message_id(message),
        "role": "user",
        "content": message.content,
        "timestamp": _timestamp(message),
    }
    for key in ("file_ids", "semantic_config_selection", "reasoning_turn_id"):
        if additional_kwargs.get(key):
            payload[key] = additional_kwargs[key]
    return payload


def _serialize_ai_message(message: AIMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": _message_id(message),
        "role": "assistant",
        "content": message.content,
        "timestamp": _timestamp(message),
    }
    tool_calls = []
    for tool_call in getattr(message, "tool_calls", None) or []:
        if not isinstance(tool_call, dict):
            continue
        tool_calls.append({
            "id": tool_call.get("id"),
            "name": tool_call.get("name"),
            "args": tool_call.get("args") or {},
        })
    if tool_calls:
        payload["tool_calls"] = tool_calls
    reasoning_content = _extract_reasoning_content(message)
    if reasoning_content:
        payload["reasoning_content"] = reasoning_content
    return payload


def _serialize_tool_message(message: ToolMessage) -> dict[str, Any]:
    return {
        "id": _message_id(message),
        "role": "tool",
        "content": message.content,
        "tool_call_id": str(getattr(message, "tool_call_id", "") or ""),
        "tool_name": str(getattr(message, "name", "") or ""),
        "timestamp": _timestamp(message),
    }


def _extract_reasoning_content(message: AIMessage) -> str:
    for source in (getattr(message, "additional_kwargs", None), getattr(message, "response_metadata", None)):
        if not isinstance(source, dict):
            continue
        for key in ("reasoning_content", "reasoning", "reasoning_text"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _load_payload(raw_payload: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw_payload)
    except (TypeError, ValueError):
        logger.warning("会话历史事件 payload 非法，已跳过")
        return None
    return payload if isinstance(payload, dict) else None


conversation_event_service = ConversationEventService()
