"""子智能体生命周期事件 outbox 数据访问。

只负责任务事件行的写入、待投递扫描与投递状态更新；通知内容生成、
唤醒 Run 与重试编排在 ``agent/run/subagent_event_service.py``。
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from utils.models import ChatDataSubagentEvent

DELIVERY_PENDING = "pending"
DELIVERY_DELIVERED = "delivered"
DELIVERY_FAILED = "failed"
DELIVERY_CANCELLED = "cancelled"


class SubagentEventDao:
    """事件 outbox 的纯读写层。"""

    async def get_by_event_id(
        self,
        session: AsyncSession,
        event_id: str,
        *,
        for_update: bool = False,
    ) -> ChatDataSubagentEvent | None:
        statement = select(ChatDataSubagentEvent).where(
            ChatDataSubagentEvent.event_id == event_id,
            ChatDataSubagentEvent.delete_flag == 0,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return (await session.execute(statement)).scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        *,
        event_id: str,
        parent_run_id: str,
        task_id: str,
        session_id: str,
        conversation_id: str,
        user_id: str,
        event_type: str,
        task_status: str,
        wake_policy: str,
        content: str,
        meta: Mapping[str, Any] | None,
        now: datetime,
    ) -> ChatDataSubagentEvent:
        existing = await self.get_by_event_id(session, event_id)
        if existing is not None:
            return existing
        operator_name = str((meta or {}).get("operator_name") or user_id or "系统")
        row = ChatDataSubagentEvent(
            event_id=event_id,
            parent_run_id=parent_run_id,
            task_id=task_id,
            session_id=session_id,
            conversation_id=conversation_id,
            user_id=user_id,
            event_type=event_type,
            task_status=task_status,
            wake_policy=wake_policy,
            event_content=content,
            record_meta=json.dumps(dict(meta or {}), ensure_ascii=False),
            delivery_status=DELIVERY_PENDING,
            delivery_attempts=0,
            last_error="",
            create_time=now,
            update_time=now,
            create_by=user_id,
            create_by_name=operator_name,
            update_by=user_id,
            update_by_name=operator_name,
        )
        session.add(row)
        await session.flush()
        return row

    async def list_pending(self, session: AsyncSession, *, limit: int = 10) -> list[ChatDataSubagentEvent]:
        result = await session.execute(
            select(ChatDataSubagentEvent)
            .where(
                ChatDataSubagentEvent.delivery_status == DELIVERY_PENDING,
                ChatDataSubagentEvent.delete_flag == 0,
            )
            .order_by(ChatDataSubagentEvent.id.asc())
            .limit(max(int(limit or 0), 1))
        )
        return list(result.scalars().all())

    async def list_for_task(
        self,
        session: AsyncSession,
        task_id: str,
        event_types: Collection[str],
    ) -> list[ChatDataSubagentEvent]:
        if not event_types:
            return []
        result = await session.execute(
            select(ChatDataSubagentEvent)
            .where(
                ChatDataSubagentEvent.task_id == task_id,
                ChatDataSubagentEvent.event_type.in_(sorted(event_types)),
                ChatDataSubagentEvent.delete_flag == 0,
            )
            .order_by(ChatDataSubagentEvent.id.asc())
        )
        return list(result.scalars().all())

    async def mark_delivered(
        self,
        session: AsyncSession,
        event_id: str,
        now: datetime,
        *,
        operator_name: str = "system",
    ) -> None:
        await session.execute(
            update(ChatDataSubagentEvent)
            .where(
                ChatDataSubagentEvent.event_id == event_id,
                ChatDataSubagentEvent.delivery_status == DELIVERY_PENDING,
            )
            .values(
                delivery_status=DELIVERY_DELIVERED,
                delivered_time=now,
                last_error="",
                update_time=now,
                update_by=operator_name,
                update_by_name=operator_name,
            )
        )

    async def mark_failed(
        self,
        session: AsyncSession,
        event_id: str,
        error: str,
        now: datetime,
        *,
        max_attempts: int = 20,
    ) -> None:
        row = await self.get_by_event_id(session, event_id, for_update=True)
        if row is None or row.delivery_status != DELIVERY_PENDING:
            return
        attempts = int(row.delivery_attempts or 0) + 1
        row.delivery_attempts = attempts
        row.last_error = (error or "")[:500]
        row.delivery_status = DELIVERY_FAILED if attempts >= max_attempts else DELIVERY_PENDING
        row.update_time = now

    async def cancel_pending(
        self,
        session: AsyncSession,
        task_id: str,
        event_types: Collection[str],
        now: datetime,
    ) -> int:
        if not event_types:
            return 0
        result = await session.execute(
            update(ChatDataSubagentEvent)
            .where(
                ChatDataSubagentEvent.task_id == task_id,
                ChatDataSubagentEvent.event_type.in_(sorted(event_types)),
                ChatDataSubagentEvent.delivery_status == DELIVERY_PENDING,
                ChatDataSubagentEvent.delete_flag == 0,
            )
            .values(delivery_status=DELIVERY_CANCELLED, update_time=now)
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)


subagent_event_dao = SubagentEventDao()
