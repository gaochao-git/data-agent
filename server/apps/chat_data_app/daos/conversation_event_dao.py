"""ChatData 会话原始事件的数据访问层。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from utils.database import get_session as default_get_session
from utils.models import ChatDataConversationEvent, UserThread


class ConversationEventDao:
    """维护会话事件的顺序追加和读取。"""

    # 轮次闭包迭代上限：每轮至少吞并一个更早轮次，真实数据 2 轮内收敛，这里只作防御性停止条件。
    _TURN_CLOSURE_MAX_ROUNDS = 16

    def __init__(self, session_context: Callable | None = None) -> None:
        self._session_context = session_context or default_get_session

    @property
    def session_context(self) -> Callable:
        """返回 DAO 使用的事务上下文，供 service 组合多个 DAO 操作。"""
        return self._session_context

    @staticmethod
    def _dedupe_event_candidates(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """按 event_key 去重并保留首个出现顺序。"""
        candidates_by_key: dict[str, dict[str, Any]] = {}
        for raw_event in events:
            event = dict(raw_event)
            event_key = str(event.get("event_key") or "").strip()
            if event_key and event_key not in candidates_by_key:
                candidates_by_key[event_key] = event
        return list(candidates_by_key.values())

    @staticmethod
    def _update_existing_event_rows(
        existing_by_key: dict[str, ChatDataConversationEvent],
        candidates: list[dict[str, Any]],
        *,
        user_id: str,
        operator_name: str,
        now: datetime,
    ) -> None:
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

    async def _insert_pending_event_rows(
        self,
        session: AsyncSession,
        *,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        pending: list[dict[str, Any]],
        now: datetime,
    ) -> list[ChatDataConversationEvent]:
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

    async def append_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        events: Iterable[dict[str, Any]],
        now: datetime,
        touch_thread: bool = False,
    ) -> list[ChatDataConversationEvent]:
        """原子追加未持久化事件，并为同一会话分配严格递增序号。"""
        candidates = self._dedupe_event_candidates(events)
        if not candidates:
            return []

        async with self._session_context() as session:
            return await self.append_events_in_session(
                session,
                conversation_id=conversation_id,
                user_id=user_id,
                operator_name=operator_name,
                events=candidates,
                now=now,
                touch_thread=touch_thread,
            )

    async def append_events_in_session(
        self,
        session: AsyncSession,
        *,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        events: Iterable[dict[str, Any]],
        now: datetime,
        touch_thread: bool = False,
    ) -> list[ChatDataConversationEvent]:
        """在调用方事务内追加事件，供历史与 Run 水位原子提交。"""
        candidates = self._dedupe_event_candidates(events)
        if not candidates:
            return []

        event_keys = list(dict.fromkeys(str(event["event_key"]).strip() for event in candidates))
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

        if touch_thread:
            # 用户提问视为会话活跃：在同一事务内刷新会话行 update_time（行已被 FOR UPDATE 锁住），
            # 使侧栏「最近更新」排序与后端分组游标跟随真实活跃时间演进。
            await session.execute(
                sa_update(UserThread).where(UserThread.id == thread_id).values(update_time=now, update_by=user_id or "")
            )

        existing_rows = list(
            (
                await session.scalars(
                    select(ChatDataConversationEvent).where(
                        ChatDataConversationEvent.conversation_id == conversation_id,
                        ChatDataConversationEvent.event_key.in_(event_keys),
                    )
                )
            ).all()
        )
        existing_by_key = {row.event_key: row for row in existing_rows}
        pending = [event for event in candidates if str(event["event_key"]).strip() not in existing_by_key]
        self._update_existing_event_rows(
            existing_by_key,
            candidates,
            user_id=user_id,
            operator_name=operator_name,
            now=now,
        )

        if not pending:
            await session.flush()
            return existing_rows

        return await self._insert_pending_event_rows(
            session,
            conversation_id=conversation_id,
            user_id=user_id,
            operator_name=operator_name,
            pending=pending,
            now=now,
        )

    async def get_max_sequence_in_session(self, session: AsyncSession, conversation_id: str) -> int:
        """读取当前事务内已可见的会话历史最大序号。"""
        value = await session.scalar(
            select(func.max(ChatDataConversationEvent.sequence_no)).where(
                ChatDataConversationEvent.conversation_id == conversation_id,
            )
        )
        return int(value or 0)

    async def list_visible_events(self, *, conversation_id: str, user_id: str) -> list[ChatDataConversationEvent]:
        """按服务端分配的全局会话序号返回可见历史事件。"""
        async with self._session_context() as session:
            return await self.list_visible_events_in_session(
                session,
                conversation_id=conversation_id,
                user_id=user_id,
            )

    async def list_visible_events_in_session(
        self,
        session: AsyncSession,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ChatDataConversationEvent]:
        """在调用方事务内读取可见历史事件。"""
        rows, _ = await self.list_visible_event_window_in_session(
            session,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        return rows

    @staticmethod
    def _visible_event_filters(*, conversation_id: str, user_id: str) -> list[Any]:
        """可见历史事件共用的过滤条件；每次返回新列表，调用方可安全追加条件。"""
        return [
            ChatDataConversationEvent.conversation_id == conversation_id,
            ChatDataConversationEvent.user_id == user_id,
            ChatDataConversationEvent.delete_flag == 0,
            ChatDataConversationEvent.event_type != "token_usage",
        ]

    async def list_visible_event_window_in_session(
        self,
        session: AsyncSession,
        *,
        conversation_id: str,
        user_id: str,
        limit: int | None = None,
        before_sequence: int | None = None,
    ) -> tuple[list[ChatDataConversationEvent], bool]:
        """读取可见历史事件窗口，返回 (按序号升序的事件, 是否还有更早)。

        limit 为空时返回全量且 has_more 恒为 False，与旧行为一致。
        指定 limit 时先按 sequence_no 倒序取尾部 limit 条，再把窗口起点收敛到「轮次闭包」起点
        （见 _expand_cutoff_to_turn_closure），返回 [cutoff, before_sequence) 内的全部事件。

        窗口必须是一段连续的 sequence_no 区间，且页内不能有被切断的轮次。并发 run 会让同一
        轮次的事件在序号上不连续：用户在上一条 run 收尾前又发消息时，提问先落库，回答要等 run
        结束才以更大的序号追加，中间夹进其它轮次的事件。此时若只把「尾部 limit 条」与「最早
        一条所属轮次的更早事件」两段并集返回，夹在两段之间、属于其它轮次的事件既不在本页、
        又落在下一页游标之下，会被永久跳过（真实库最长会话 503 条事件中漏掉 48 条，含 2 条
        提问）；若窗口起点只取「尾部最早一条所属轮次的起点」，则另有轮次的回答会落在该起点
        之上、其提问却在其下（真实库 3/67 个会话首屏因此渲染出没有提问的回答）。
        """
        conditions = self._visible_event_filters(conversation_id=conversation_id, user_id=user_id)
        if before_sequence is not None:
            conditions.append(ChatDataConversationEvent.sequence_no < before_sequence)
        stmt = select(ChatDataConversationEvent).where(*conditions)
        if limit is None:
            result = await session.execute(
                stmt.order_by(ChatDataConversationEvent.sequence_no.asc(), ChatDataConversationEvent.id.asc())
            )
            return list(result.scalars().all()), False

        tail_result = await session.execute(
            stmt.order_by(ChatDataConversationEvent.sequence_no.desc(), ChatDataConversationEvent.id.desc()).limit(
                limit
            )
        )
        tail = list(reversed(list(tail_result.scalars().all())))
        if not tail:
            return [], False
        cutoff = await self._expand_cutoff_to_turn_closure(
            session,
            conditions=conditions,
            cutoff=int(tail[0].sequence_no),
        )
        window_result = await session.execute(
            stmt.where(ChatDataConversationEvent.sequence_no >= cutoff).order_by(
                ChatDataConversationEvent.sequence_no.asc(), ChatDataConversationEvent.id.asc()
            )
        )
        rows = list(window_result.scalars().all())
        has_more = await self._has_earlier_events_in_session(
            session,
            conditions=conditions,
            earlier_than=cutoff,
        )
        return rows, has_more

    async def _expand_cutoff_to_turn_closure(
        self,
        session: AsyncSession,
        *,
        conditions: list[Any],
        cutoff: int,
    ) -> int:
        """把窗口起点向下收敛到「轮次闭包」起点，保证页内没有被切断的轮次。

        并发 run 会让同一轮次的事件在序号上不连续（提问先落库、回答等 run 收尾才追加），
        因此仅取「尾部最早一条所属轮次的起点」并不够：另一个轮次的回答可能落在该起点之上，
        而它的提问在起点之下，首屏就会渲染出没有提问的回答。这里反复把「有事件位于起点之上
        的轮次」的起点并入起点，直到不动点——此时窗口内每个轮次的起点都在窗口内。

        迭代次数由「每轮至少并入一个更早轮次、起点单调下降且有下界」保证收敛；真实数据 2 轮
        内收敛，_TURN_CLOSURE_MAX_ROUNDS 只是防御性上限。万一触及上限（数据交织极端异常），
        结果退化为「起点停在某个轮次起点」的弱保证：仍然是一段连续区间、不会漏事件，只是可能
        还有孤立的回答留在首屏。
        """
        # 每个轮次在本窗口范围内的序号跨度；空 turn_id 的行不构成轮次，无法被并入，保持单行独立。
        turn_spans = (
            select(
                func.min(ChatDataConversationEvent.sequence_no).label("min_seq"),
                func.max(ChatDataConversationEvent.sequence_no).label("max_seq"),
            )
            .where(*conditions, ChatDataConversationEvent.turn_id != "")
            .group_by(ChatDataConversationEvent.turn_id)
            .subquery()
        )
        for _ in range(self._TURN_CLOSURE_MAX_ROUNDS):
            earliest = await session.scalar(
                select(func.min(turn_spans.c.min_seq)).where(turn_spans.c.max_seq >= cutoff)
            )
            if earliest is None or int(earliest) >= cutoff:
                break
            cutoff = int(earliest)
        return cutoff

    @staticmethod
    async def _has_earlier_events_in_session(
        session: AsyncSession,
        *,
        conditions: list[Any],
        earlier_than: int,
    ) -> bool:
        """只探测是否还存在更早事件，不读取 payload。"""
        probe = await session.scalar(
            select(ChatDataConversationEvent.id)
            .where(*conditions, ChatDataConversationEvent.sequence_no < earlier_than)
            .limit(1)
        )
        return probe is not None

    async def get_latest_token_usage_payload(self, *, conversation_id: str, user_id: str) -> str | None:
        """读取会话级 token_usage 快照（event_key 固定为 token_usage:latest 的单行 upsert）。"""
        async with self._session_context() as session:
            return await self.get_latest_token_usage_payload_in_session(
                session,
                conversation_id=conversation_id,
                user_id=user_id,
            )

    async def get_latest_token_usage_payload_in_session(
        self,
        session: AsyncSession,
        *,
        conversation_id: str,
        user_id: str,
    ) -> str | None:
        """在调用方事务内读取会话级 token_usage 快照。"""
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

    async def get_first_user_message_payload(self, *, conversation_id: str, user_id: str) -> str | None:
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
