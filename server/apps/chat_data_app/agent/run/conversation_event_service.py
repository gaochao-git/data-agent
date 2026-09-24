"""会话原始事件的持久化与历史恢复服务。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Iterable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from utils.database import get_session
from utils.db_failover import is_mysql_connection_error
from utils.logger import get_logger
from apps.chat_data_app.daos.conversation_event_dao import ConversationEventDao
from apps.chat_data_app.daos.agent_inbox_dao import AgentInboxDao, agent_inbox_dao
from apps.chat_data_app.agent.run.agent_inbox_service import AgentRunLeaseLostError, is_valid_redis_stream_id, load_run_content as _load_run_content, normalize_history_checkpoint

logger = get_logger(__name__)

EVENT_USER_MESSAGE = "user_message"
EVENT_REASONING = "reasoning"
EVENT_ASSISTANT_MESSAGE = "assistant_message"
EVENT_TOOL_RESULT = "tool_result"
EVENT_CONTEXT_COMPRESSION = "context_compression"
EVENT_TOKEN_USAGE = "token_usage"
EVENT_APPEND_MAX_ATTEMPTS = 3
EVENT_APPEND_RETRY_DELAY_SECONDS = 0.05



def _compare_stream_ids(left: str, right: str) -> int:
    """按 Redis Stream 的时间和序号比较游标。"""
    def key(value: str) -> tuple[int, int, str]:
        parts = value.split("-", 1)
        try:
            return int(parts[0]), int(parts[1]), ""
        except (IndexError, TypeError, ValueError):
            return 0, 0, value

    left_key = key(left)
    right_key = key(right)
    return (left_key > right_key) - (left_key < right_key)

class ConversationEventService:
    """将 Agent 完整消息转换为追加式历史事件。"""

    def __init__(
        self,
        dao: ConversationEventDao | None = None,
        inbox_dao: AgentInboxDao = agent_inbox_dao,
    ):
        self._dao = dao or ConversationEventDao(session_context=lambda: get_session())
        self._inbox_dao = inbox_dao

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
        await self._append_events(
            conversation_id=conversation_id,
            user_id=user_id,
            operator_name=operator_name,
            events=[_event(
                turn_id=turn_id,
                event_key=f"user:{turn_id}",
                event_type=EVENT_USER_MESSAGE,
                payload=_serialize_human_message(message),
            )],
            touch_thread=True,
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
        token_usage: dict[str, Any] | None = None,
    ) -> None:
        events = _build_turn_events(
            turn_id=turn_id,
            messages=list(messages or []),
            reasoning_segments=list(reasoning_segments or []),
            context_compression_events=list(context_compression_events or []),
            token_usage=token_usage,
        )
        if not events:
            return
        await self._append_events(
            conversation_id=conversation_id,
            user_id=user_id,
            operator_name=operator_name,
            events=events,
        )

    async def persist_turn_with_checkpoint(
        self,
        *,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        turn_id: str,
        messages: Iterable[BaseMessage],
        reasoning_segments: Iterable[dict[str, Any]],
        context_compression_events: Iterable[dict[str, Any]] = (),
        token_usage: dict[str, Any] | None = None,
        run_id: str,
        stream_id: str | None,
        owner_token: str | None = None,
    ) -> dict[str, Any] | None:
        """原子提交历史快照与 Run 的 Redis 恢复水位。

        Redis 发布和 MySQL 不在同一事务中，因此调用方必须先发布事件，再调用本方法。
        事务内先写会话事件，再推进 checkpoint；失败时两者一起回滚，恢复最多重复读取，
        不会因为游标提前推进而丢失历史事件。
        """
        events = _build_turn_events(
            turn_id=turn_id,
            messages=list(messages or []),
            reasoning_segments=list(reasoning_segments or []),
            context_compression_events=list(context_compression_events or []),
            token_usage=token_usage,
        )
        if not events:
            return None
        normalized_stream_id = self._normalize_stream_id(stream_id)
        for attempt in range(1, EVENT_APPEND_MAX_ATTEMPTS + 1):
            try:
                async with self._dao.session_context() as session:
                    await self._dao.append_events_in_session(
                        session,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        operator_name=operator_name,
                        events=events,
                        now=datetime.now(),
                    )
                    # append_events_in_session locks the conversation row first.
                    # Keep the same conversation -> Run lock order as start_run
                    # and complete_run to avoid cross-process deadlocks. If the
                    # owner check fails, the surrounding transaction rolls back
                    # the provisional history append.
                    run = await self._inbox_dao.get_run_by_id_for_update(session, run_id)
                    self._validate_run_for_checkpoint(
                        run,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        run_id=run_id,
                        owner_token=owner_token,
                    )
                    history_sequence_no = await self._dao.get_max_sequence_in_session(session, conversation_id)
                    checkpoint = await self._update_history_checkpoint(
                        run_id=run_id,
                        turn_id=turn_id,
                        history_sequence_no=history_sequence_no,
                        stream_id=normalized_stream_id,
                        run=run,
                    )
                    return checkpoint
            except AgentRunLeaseLostError:
                raise
            except Exception as exc:
                should_retry = is_mysql_connection_error(exc) and attempt < EVENT_APPEND_MAX_ATTEMPTS
                if not should_retry:
                    logger.exception(
                        "追加会话历史并更新恢复水位失败: conversation_id=%s run_id=%s attempt=%s",
                        conversation_id,
                        run_id,
                        attempt,
                    )
                    raise
                logger.warning(
                    "追加会话历史遇到连接错误，准备重试: conversation_id=%s run_id=%s attempt=%s",
                    conversation_id,
                    run_id,
                    attempt,
                    exc_info=True,
                )
                await asyncio.sleep(EVENT_APPEND_RETRY_DELAY_SECONDS)

        return None

    async def load_history(
        self,
        *,
        conversation_id: str,
        user_id: str,
        include_derived: bool = True,
    ) -> dict[str, Any]:
        rows = await self._dao.list_visible_events(conversation_id=conversation_id, user_id=user_id)
        token_usage_payload = None
        try:
            token_usage_payload = await self._dao.get_latest_token_usage_payload(
                conversation_id=conversation_id,
                user_id=user_id,
            )
        except Exception:
            logger.warning(
                "读取会话 token 占用失败，历史消息继续返回: conversation_id=%s",
                conversation_id,
                exc_info=True,
            )
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
            if include_derived and row.event_type == EVENT_REASONING:
                reasoning_segments.append(payload)
            elif include_derived and row.event_type in {EVENT_USER_MESSAGE, EVENT_ASSISTANT_MESSAGE, EVENT_TOOL_RESULT}:
                messages.append(payload)
        return {
            "messages": messages,
            "reasoning_segments": reasoning_segments,
            "history_events": history_events,
            "token_usage": _load_payload(token_usage_payload),
        }

    async def load_history_with_recovery(
        self,
        *,
        conversation_id: str,
        user_id: str,
        include_derived: bool = True,
        limit: int | None = None,
        before_sequence: int | None = None,
    ) -> dict[str, Any]:
        """在同一事务读取历史和活跃 Run 的恢复水位。

        limit 为空时返回全量会话历史；指定 limit 时只返回尾部窗口，并通过
        has_more / earliest_sequence_no 支持向上翻页。长会话全量拉取会让
        payload 字节数随会话线性增长，是历史加载变慢的主因。
        """
        async with self._dao.session_context() as session:
            rows, has_more = await self._dao.list_visible_event_window_in_session(
                session,
                conversation_id=conversation_id,
                user_id=user_id,
                limit=limit,
                before_sequence=before_sequence,
            )
            try:
                token_usage_payload = await self._dao.get_latest_token_usage_payload_in_session(
                    session,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
            except Exception:
                logger.warning(
                    "读取会话 token 占用失败，历史消息继续返回: conversation_id=%s",
                    conversation_id,
                    exc_info=True,
                )
                token_usage_payload = None
            active_run = await self._inbox_dao.get_active_run_snapshot(
                session,
                conversation_id,
                user_id,
            )
            return {
                **self._build_history_payload(rows, token_usage_payload, include_derived),
                "active_run": self._active_run_payload(active_run),
                "has_more": has_more,
                "earliest_sequence_no": int(rows[0].sequence_no) if rows else None,
            }

    @staticmethod
    def _build_history_payload(
        rows: list[Any],
        token_usage_payload: str | None,
        include_derived: bool,
    ) -> dict[str, Any]:
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
            if include_derived and row.event_type == EVENT_REASONING:
                reasoning_segments.append(payload)
            elif include_derived and row.event_type in {EVENT_USER_MESSAGE, EVENT_ASSISTANT_MESSAGE, EVENT_TOOL_RESULT}:
                messages.append(payload)
        return {
            "messages": messages,
            "reasoning_segments": reasoning_segments,
            "history_events": history_events,
            "token_usage": _load_payload(token_usage_payload),
        }

    @staticmethod
    def _active_run_payload(run: Any | None) -> dict[str, Any] | None:
        if run is None:
            return None
        content = _load_run_content(run.record_content)
        return {
            "run_id": run.run_id,
            "status": run.record_status,
            "turn_id": str(content.get("turn_id") or "") or None,
            "is_continuation": content.get("run_kind") == "continuation",
            "history_checkpoint": normalize_history_checkpoint(content, run.run_id),
        }

    async def get_first_user_message(self, *, conversation_id: str, user_id: str) -> str | None:
        """读取首条有效用户消息正文，不加载完整会话历史。"""
        try:
            raw_payload = await self._dao.get_first_user_message_payload(
                conversation_id=conversation_id,
                user_id=user_id,
            )
        except Exception:
            logger.exception("读取会话首条用户消息失败: conversation_id=%s", conversation_id)
            return None
        payload = _load_payload(raw_payload)
        content = _extract_text_content((payload or {}).get("content"))
        return content or None

    async def _append_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        events: list[dict[str, Any]],
        touch_thread: bool = False,
    ) -> None:
        for attempt in range(1, EVENT_APPEND_MAX_ATTEMPTS + 1):
            try:
                await self._dao.append_events(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    operator_name=operator_name,
                    events=events,
                    now=datetime.now(),
                    touch_thread=touch_thread,
                )
                return
            except Exception as exc:
                should_retry = is_mysql_connection_error(exc) and attempt < EVENT_APPEND_MAX_ATTEMPTS
                if not should_retry:
                    logger.exception(
                        "追加会话原始事件失败: conversation_id=%s attempt=%s",
                        conversation_id,
                        attempt,
                    )
                    raise
                logger.warning(
                    "追加会话原始事件遇到连接错误，准备重试: conversation_id=%s attempt=%s",
                    conversation_id,
                    attempt,
                    exc_info=True,
                )
                await asyncio.sleep(EVENT_APPEND_RETRY_DELAY_SECONDS)

    async def _update_history_checkpoint(
        self,
        *,
        run_id: str,
        turn_id: str,
        history_sequence_no: int,
        stream_id: str | None,
        run: Any,
    ) -> dict[str, Any] | None:
        """在同一事务内推进当前 Run 的历史水位。"""
        if not stream_id:
            return None
        content = _load_run_content(run.record_content)
        previous = content.get("history_checkpoint")
        if isinstance(previous, dict) and str(previous.get("run_id") or "") not in {"", run_id}:
            raise AgentRunLeaseLostError(f"当前运行恢复水位归属已变化: {run_id}")
        previous_stream_id = str((previous or {}).get("stream_id") or "")
        if previous_stream_id and _compare_stream_ids(stream_id, previous_stream_id) < 0:
            return previous
        checkpoint = {
            "run_id": run_id,
            "turn_id": turn_id,
            "history_sequence_no": history_sequence_no,
            "stream_id": stream_id,
        }
        content["history_checkpoint"] = checkpoint
        run.record_content = json.dumps(content, ensure_ascii=False)
        return checkpoint

    @staticmethod
    def _normalize_stream_id(stream_id: str | None) -> str | None:
        normalized = str(stream_id or "").strip()
        return normalized if is_valid_redis_stream_id(normalized) else None

    @staticmethod
    def _validate_run_for_checkpoint(
        run: Any | None,
        *,
        conversation_id: str,
        user_id: str,
        run_id: str,
        owner_token: str | None,
    ) -> None:
        if run is None or run.conversation_id != conversation_id or run.user_id != user_id:
            raise AgentRunLeaseLostError(f"当前运行不存在或归属已变化: {run_id}")
        if run.record_status not in {"pending", "active"}:
            raise AgentRunLeaseLostError(f"当前运行已结束，禁止追加历史: {run_id}")
        # An active Run must still be owned by the Worker writing its snapshot.
        # A missing owner is only tolerated for a pending compatibility path;
        # the runtime claims the Run before it starts producing events.
        if run.record_status == "active" and (not owner_token or run.owner_token != owner_token):
            raise AgentRunLeaseLostError(f"当前运行已由其他 Worker 接管: {run_id}")
        content = _load_run_content(run.record_content)
        previous = content.get("history_checkpoint")
        if isinstance(previous, dict) and str(previous.get("run_id") or "") not in {"", run_id}:
            raise AgentRunLeaseLostError(f"当前运行恢复水位归属已变化: {run_id}")


def _turn_message_range(messages: list[BaseMessage], turn_id: str) -> tuple[int, int] | None:
    """定位本轮消息区间：起点为该 turn 的用户消息，终点为下一条用户消息前。"""
    start_index = _turn_start_index(messages, turn_id)
    if start_index is None:
        return None
    turn_end = len(messages)
    for index, message in enumerate(messages[start_index + 1 :], start=start_index + 1):
        if isinstance(message, HumanMessage):
            turn_end = index
            break
    return start_index, turn_end


def _partition_compression_events(
    compression_events: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """按锚点消息分组压缩事件，未锚定事件单独收集。"""
    compression_events_by_anchor: dict[str, list[dict[str, Any]]] = {}
    unanchored_compression_events: list[dict[str, Any]] = []
    for event in compression_events:
        anchor_message_id = str(event.get("anchor_message_id") or "").strip()
        if anchor_message_id:
            compression_events_by_anchor.setdefault(anchor_message_id, []).append(event)
        else:
            unanchored_compression_events.append(event)
    return compression_events_by_anchor, unanchored_compression_events


def _build_turn_events_ready_reasoning(
    events: list[dict[str, Any]],
    state: dict[str, Any],
    pending_reasoning: list[dict[str, Any]],
    turn_id: str,
) -> None:
    while (
        state["next_reasoning_index"] < len(pending_reasoning)
        and int(pending_reasoning[state["next_reasoning_index"]].get("tool_call_count") or 0) <= state["visible_tool_count"]
    ):
        segment = pending_reasoning[state["next_reasoning_index"]]
        segment_id = str(segment["id"])
        events.append(_event(
            turn_id=turn_id,
            event_key=f"reasoning:{segment_id}",
            event_type=EVENT_REASONING,
            payload=segment,
        ))
        state["next_reasoning_index"] += 1


def _build_turn_events_anchor_events(
    events: list[dict[str, Any]],
    compression_events_by_anchor: dict[str, list[dict[str, Any]]],
    anchor_message_id: str,
    turn_id: str,
) -> None:
    for event in compression_events_by_anchor.pop(anchor_message_id, []):
        event_id = str(event["id"])
        events.append(_event(
            turn_id=turn_id,
            event_key=f"context_compression:{event_id}",
            event_type=EVENT_CONTEXT_COMPRESSION,
            payload=event,
        ))


def _build_turn_events_flush_unanchored(
    events: list[dict[str, Any]],
    unanchored_compression_events: list[dict[str, Any]],
    turn_id: str,
) -> None:
    for event in unanchored_compression_events:
        event_id = str(event["id"])
        events.append(_event(
            turn_id=turn_id,
            event_key=f"context_compression:{event_id}",
            event_type=EVENT_CONTEXT_COMPRESSION,
            payload=event,
        ))
    unanchored_compression_events.clear()


def _build_turn_events_human_message(
    message: BaseMessage,
    events: list[dict[str, Any]],
    compression_events_by_anchor: dict[str, list[dict[str, Any]]],
    unanchored_compression_events: list[dict[str, Any]],
    turn_id: str,
) -> None:
    message_id = _message_id(message)
    if not message_id:
        return
    events.append(_event(
        turn_id=turn_id,
        event_key=f"user:{message_id}",
        event_type=EVENT_USER_MESSAGE,
        payload=_serialize_human_message(message),
    ))
    _build_turn_events_anchor_events(events, compression_events_by_anchor, message_id, turn_id)
    if unanchored_compression_events:
        _build_turn_events_flush_unanchored(events, unanchored_compression_events, turn_id)


def _build_turn_events_ai_message(
    message: BaseMessage,
    message_index: int,
    events: list[dict[str, Any]],
    state: dict[str, Any],
    pending_reasoning: list[dict[str, Any]],
    compression_events_by_anchor: dict[str, list[dict[str, Any]]],
    turn_id: str,
) -> None:
    _build_turn_events_ready_reasoning(events, state, pending_reasoning, turn_id)
    message_id = _message_id(message) or f"{turn_id}:ai:{message_index}"
    events.append(_event(
        turn_id=turn_id,
        event_key=f"assistant:{message_id}",
        event_type=EVENT_ASSISTANT_MESSAGE,
        payload=_serialize_ai_message(message),
    ))
    state["visible_tool_count"] += len(getattr(message, "tool_calls", None) or [])
    _build_turn_events_anchor_events(events, compression_events_by_anchor, message_id, turn_id)


def _build_turn_events_tool_message(
    message: BaseMessage,
    message_index: int,
    events: list[dict[str, Any]],
    compression_events_by_anchor: dict[str, list[dict[str, Any]]],
    turn_id: str,
) -> None:
    message_id = _message_id(message) or f"{turn_id}:tool:{message_index}"
    events.append(_event(
        turn_id=turn_id,
        event_key=f"tool:{message_id}",
        event_type=EVENT_TOOL_RESULT,
        payload=_serialize_tool_message(message),
    ))
    _build_turn_events_anchor_events(events, compression_events_by_anchor, message_id, turn_id)


def _build_turn_events(
    *,
    turn_id: str,
    messages: list[BaseMessage],
    reasoning_segments: list[dict[str, Any]],
    context_compression_events: list[dict[str, Any]] | None = None,
    token_usage: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    turn_range = _turn_message_range(messages, turn_id)
    if turn_range is None:
        return []
    start_index, turn_end = turn_range
    turn_messages = messages[start_index:turn_end]
    pending_reasoning = _normalize_reasoning_segments(reasoning_segments, turn_id)
    compression_events = _normalize_context_compression_events(context_compression_events or [], turn_id)
    compression_events_by_anchor, unanchored_compression_events = _partition_compression_events(compression_events)
    events: list[dict[str, Any]] = []
    state = {"next_reasoning_index": 0, "visible_tool_count": 0}

    for message_index, message in enumerate(turn_messages):
        if isinstance(message, HumanMessage):
            _build_turn_events_human_message(
                message,
                events=events,
                compression_events_by_anchor=compression_events_by_anchor,
                unanchored_compression_events=unanchored_compression_events,
                turn_id=turn_id,
            )
            continue
        if isinstance(message, AIMessage):
            _build_turn_events_ai_message(
                message,
                message_index=message_index,
                events=events,
                state=state,
                pending_reasoning=pending_reasoning,
                compression_events_by_anchor=compression_events_by_anchor,
                turn_id=turn_id,
            )
            continue
        if isinstance(message, ToolMessage):
            _build_turn_events_tool_message(
                message,
                message_index=message_index,
                events=events,
                compression_events_by_anchor=compression_events_by_anchor,
                turn_id=turn_id,
            )

    _build_turn_events_ready_reasoning(events, state, pending_reasoning, turn_id)
    for pending_events in compression_events_by_anchor.values():
        unanchored_compression_events.extend(pending_events)
    _build_turn_events_flush_unanchored(events, unanchored_compression_events, turn_id)
    if isinstance(token_usage, dict) and token_usage:
        events.append(_event(
            turn_id=turn_id,
            # 固定 event_key：借助 append_events 的按 key 原地更新语义，
            # 每会话仅保留一条最新 token_usage 快照，避免逐轮追加大 payload。
            event_key="token_usage:latest",
            event_type=EVENT_TOKEN_USAGE,
            payload=token_usage,
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


def _execution_timing(message: BaseMessage) -> dict[str, int]:
    """读取消息上盖章的执行起止时间（AI 为模型调用、tool 为工具执行，epoch 毫秒）。"""
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    timing: dict[str, int] = {}
    for key in ("started_at_ms", "ended_at_ms"):
        value = additional_kwargs.get(key)
        if isinstance(value, bool):
            continue
        try:
            timestamp_ms = int(value)
        except (TypeError, ValueError):
            continue
        if timestamp_ms >= 0:
            timing[key] = timestamp_ms
    return timing


def _serialize_human_message(message: HumanMessage) -> dict[str, Any]:
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    payload: dict[str, Any] = {
        "id": _message_id(message),
        "role": "user",
        "content": _sanitize_user_message_content(message.content),
        "timestamp": _timestamp(message),
    }
    # vision_attachments 与 message_compose.select_vision_attachments 对应；
    # 不直接 import 是为了避免 services 反向依赖 agent.middleware。
    for key in ("file_ids", "file_selections", "reasoning_turn_id", "vision_attachments"):
        if additional_kwargs.get(key):
            payload[key] = additional_kwargs[key]
    return payload


def _sanitize_user_message_content(content: Any) -> Any:
    """保险丝：用户消息若为多模态 blocks，剥掉非文本块、降级为占位说明。

    正常链路下用户消息恒为纯字符串（图片附件走 ``vision_attachments``
    路径标记 + ImageInjectionMiddleware 请求层装载）。此防御保证未来任何
    代码路径再把大 blob 塞进用户消息时，事件表也不会落 base64。
    """
    if not isinstance(content, list):
        return content
    texts: list[str] = []
    image_count = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "image_url":
            image_count += 1
        elif block.get("type") == "text" and str(block.get("text") or "").strip():
            texts.append(str(block["text"]))
    text = "\n".join(texts)
    if image_count:
        note = f"[图片内容已省略，共 {image_count} 张]"
        text = f"{text}\n{note}" if text else note
    return text or "[非文本内容已省略]"


def _serialize_ai_message(message: AIMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": _message_id(message),
        "role": "assistant",
        "content": message.content,
        "timestamp": _timestamp(message),
    }
    payload.update(_execution_timing(message))
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
    payload = {
        "id": _message_id(message),
        "role": "tool",
        "content": message.content,
        "tool_call_id": str(getattr(message, "tool_call_id", "") or ""),
        "tool_name": str(getattr(message, "name", "") or ""),
        "timestamp": _timestamp(message),
    }
    payload.update(_execution_timing(message))
    # task 工具的子智能体过程快照（subagents.py 盖在 additional_kwargs 上）：
    # 随 tool_result 事件持久化，历史回放时前端据此还原「子智能体内部逻辑」。
    trail = (getattr(message, "additional_kwargs", None) or {}).get("subagent_trail")
    if isinstance(trail, list) and trail:
        payload["subagent_trail"] = trail
    # 子图上下文用量快照（最后一次估算）：历史回放时 task 卡片标题行还原用量徽标。
    context_usage = (getattr(message, "additional_kwargs", None) or {}).get("subagent_context_usage")
    if isinstance(context_usage, dict) and context_usage:
        payload["subagent_context_usage"] = context_usage
    return payload


def _extract_reasoning_content(message: AIMessage) -> str:
    for source in (getattr(message, "additional_kwargs", None), getattr(message, "response_metadata", None)):
        if not isinstance(source, dict):
            continue
        for key in ("reasoning_content", "reasoning", "reasoning_text"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _load_payload(raw_payload: str | None) -> dict[str, Any] | None:
    if raw_payload is None:
        return None
    try:
        payload = json.loads(raw_payload)
    except (TypeError, ValueError):
        logger.warning("会话历史事件 payload 非法，已跳过")
        return None
    return payload if isinstance(payload, dict) else None


def _extract_text_content(content: Any) -> str:
    """提取消息正文文本：兼容纯字符串与多模态 content blocks（只取 text 块）。

    剔除 ``[attachment]`` 附件占位行——那是给历史会话回放合成附件卡片用的，
    不属于用户正文（会话标题等场景不应出现）。
    """
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        texts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                block_text = str(block.get("text") or "").strip()
                if block_text:
                    texts.append(block_text)
        text = "\n".join(texts).strip()
    else:
        return ""
    if not text:
        return ""
    lines = [line for line in text.splitlines() if not line.strip().startswith("[attachment]")]
    return "\n".join(lines).strip()


conversation_event_service = ConversationEventService()
