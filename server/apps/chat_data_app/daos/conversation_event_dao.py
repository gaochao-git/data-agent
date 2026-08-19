"""ChatData 会话原始事件的数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from sqlalchemy import func, select

from utils.database import get_session as default_get_session
from utils.models import ChatDataConversationEvent, UserThread


class ConversationEventDao:
    """维护会话事件的顺序追加和读取。"""

    def __init__(self, session_context: Optional[Callable] = None):
        self._session_context = session_context or default_get_session

    async def append_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        events: Iterable[dict[str, Any]],
        now: datetime,
    ) -> list[ChatDataConversationEvent]:
        """原子追加未持久化事件，并为同一会话分配严格递增序号。"""
        candidates_by_key: dict[str, dict[str, Any]] = {}
        for raw_event in events:
            event = dict(raw_event)
            event_key = str(event.get("event_key") or "").strip()
            if event_key and event_key not in candidates_by_key:
                candidates_by_key[event_key] = event
        candidates = list(candidates_by_key.values())
        if not candidates:
            return []

        event_keys = list(dict.fromkeys(str(event["event_key"]).strip() for event in candidates))
        async with self._session_context() as session:
            # 锁住已有会话行，避免跨进程并发以 MAX(sequence_no) 分配同一序号。
            thread_id = await session.scalar(
                select(UserThread.id)
                .where(
                    UserThread.thread_id == conversation_id,
                    UserThread.user_id == user_id,
                    UserThread.delete_flag == 0,
                )
                .with_for_update()
            )
            if thread_id is None:
                raise LookupError(f"conversation not found: {conversation_id}")

            existing_rows = list((await session.scalars(
                select(ChatDataConversationEvent).where(
                    ChatDataConversationEvent.conversation_id == conversation_id,
                    ChatDataConversationEvent.event_key.in_(event_keys),
                )
            )).all())
            existing_by_key = {row.event_key: row for row in existing_rows}
            pending = [event for event in candidates if str(event["event_key"]).strip() not in existing_by_key]
            for event in candidates:
                event_key = str(event["event_key"]).strip()
                row = existing_by_key.get(event_key)
                if row is None:
                    continue
                row.turn_id = str(event.get("turn_id") or "")
                row.event_type = str(event.get("event_type") or "message")
                row.payload = str(event.get("payload") or "{}")
                row.update_by = user_id
                row.update_by_name = operator_name
                row.update_time = now

            if not pending:
                await session.flush()
                return existing_rows

            max_sequence = await session.scalar(
                select(func.max(ChatDataConversationEvent.sequence_no)).where(
                    ChatDataConversationEvent.conversation_id == conversation_id,
                )
            )
            sequence_no = int(max_sequence or 0)
            rows: list[ChatDataConversationEvent] = []
            for event in pending:
                sequence_no += 1
                row = ChatDataConversationEvent(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    turn_id=str(event.get("turn_id") or ""),
                    sequence_no=sequence_no,
                    event_key=str(event["event_key"]).strip(),
                    event_type=str(event.get("event_type") or "message"),
                    payload=str(event.get("payload") or "{}"),
                    delete_flag=0,
                    create_time=now,
                    update_time=now,
                    create_by=user_id,
                    create_by_name=operator_name,
                    update_by=user_id,
                    update_by_name=operator_name,
                )
                session.add(row)
                rows.append(row)
            await session.flush()
            return rows

    async def list_visible_events(self, *, conversation_id: str, user_id: str) -> list[ChatDataConversationEvent]:
        """按服务端分配的全局会话序号返回可见历史事件。"""
        async with self._session_context() as session:
            result = await session.execute(
                select(ChatDataConversationEvent)
                .where(
                    ChatDataConversationEvent.conversation_id == conversation_id,
                    ChatDataConversationEvent.user_id == user_id,
                    ChatDataConversationEvent.delete_flag == 0,
                    ChatDataConversationEvent.event_type != "token_usage",
                )
                .order_by(ChatDataConversationEvent.sequence_no.asc(), ChatDataConversationEvent.id.asc())
            )
            return list(result.scalars().all())

    async def get_latest_token_usage_payload(self, *, conversation_id: str, user_id: str) -> Optional[str]:
        """读取会话级 token_usage 快照（event_key 固定为 token_usage:latest 的单行 upsert）。"""
        async with self._session_context() as session:
            result = await session.execute(
                select(ChatDataConversationEvent.payload)
                .where(
                    ChatDataConversationEvent.conversation_id == conversation_id,
                    ChatDataConversationEvent.user_id == user_id,
                    ChatDataConversationEvent.event_type == "token_usage",
                    ChatDataConversationEvent.delete_flag == 0,
                )
                .order_by(ChatDataConversationEvent.sequence_no.desc(), ChatDataConversationEvent.id.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def get_first_user_message_payload(self, *, conversation_id: str, user_id: str) -> Optional[str]:
        """只读取首条用户消息，供标题生成使用。"""
        async with self._session_context() as session:
            result = await session.execute(
                select(ChatDataConversationEvent.payload)
                .where(
                    ChatDataConversationEvent.conversation_id == conversation_id,
                    ChatDataConversationEvent.user_id == user_id,
                    ChatDataConversationEvent.event_type == "user_message",
                    ChatDataConversationEvent.delete_flag == 0,
                )
                .order_by(ChatDataConversationEvent.sequence_no.asc(), ChatDataConversationEvent.id.asc())
                .limit(1)
            )
            return result.scalar_one_or_none()
