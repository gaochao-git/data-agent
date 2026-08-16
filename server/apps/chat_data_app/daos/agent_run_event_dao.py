"""ChatData Agent Run 事件的数据访问层。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.models import ChatDataAgentRunEvent


class AgentRunEventDao:
    """只负责运行事件的顺序追加和游标读取。"""

    async def append(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        sequence_no: int,
        event_id: str,
        event_type: str,
        payload: str,
        now: datetime | None,
    ) -> ChatDataAgentRunEvent | None:
        existing = await session.scalar(
            select(ChatDataAgentRunEvent).where(
                ChatDataAgentRunEvent.run_id == run_id,
                ChatDataAgentRunEvent.event_id == event_id,
            )
        )
        if existing is not None:
            return None

        timestamp = now or datetime.now()
        row = ChatDataAgentRunEvent(
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            sequence_no=sequence_no,
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            delete_flag=0,
            create_time=timestamp,
            update_time=timestamp,
            create_by=user_id,
            create_by_name=user_id,
            update_by=user_id,
            update_by_name=user_id,
        )
        session.add(row)
        await session.flush()
        return row

    async def list_after(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        after_seq: int,
        limit: int = 200,
    ) -> list[ChatDataAgentRunEvent]:
        result = await session.execute(
            select(ChatDataAgentRunEvent)
            .where(
                ChatDataAgentRunEvent.run_id == run_id,
                ChatDataAgentRunEvent.sequence_no > after_seq,
                ChatDataAgentRunEvent.delete_flag == 0,
            )
            .order_by(ChatDataAgentRunEvent.sequence_no.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_sequence_by_event_id(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        event_id: str,
    ) -> int | None:
        result = await session.scalar(
            select(ChatDataAgentRunEvent.sequence_no).where(
                ChatDataAgentRunEvent.run_id == run_id,
                ChatDataAgentRunEvent.event_id == event_id,
                ChatDataAgentRunEvent.delete_flag == 0,
            )
        )
        return int(result) if result is not None else None

    async def get_max_sequence(
        self,
        session: AsyncSession,
        *,
        run_id: str,
    ) -> int:
        result = await session.scalar(
            select(func.max(ChatDataAgentRunEvent.sequence_no)).where(
                ChatDataAgentRunEvent.run_id == run_id,
                ChatDataAgentRunEvent.delete_flag == 0,
            )
        )
        return int(result or 0)

    async def get_latest_event_id(
        self,
        session: AsyncSession,
        *,
        run_id: str,
    ) -> str | None:
        result = await session.scalar(
            select(ChatDataAgentRunEvent.event_id)
            .where(
                ChatDataAgentRunEvent.run_id == run_id,
                ChatDataAgentRunEvent.delete_flag == 0,
            )
            .order_by(ChatDataAgentRunEvent.sequence_no.desc())
            .limit(1)
        )
        return str(result) if result else None


agent_run_event_dao = AgentRunEventDao()
