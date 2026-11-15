"""后台子智能体任务账本数据访问。

任务与 Run / inbox 消息共用 ``chat_data_agent_inbox`` 表，用
``record_type='task'`` 区分：Run 与消息的既有查询都显式带 record_type，
不会互相污染；任务复用表上的会话索引、唯一键与租约列，无需额外建表。
"""

from __future__ import annotations

import json
from collections.abc import Collection
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from utils.models import ChatDataAgentInbox

TASK_RECORD_TYPE = "task"
TASK_QUEUE_TYPE = "background"


def load_task_meta(meta_text: str | None) -> dict[str, Any]:
    """解析任务元数据 JSON；损坏数据降级为空字典。"""
    try:
        loaded = json.loads(meta_text or "{}")
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


class SubagentTaskDao:
    """只负责任务行的读写，不编排通知与业务状态语义。"""

    async def now(self, session: AsyncSession) -> datetime:
        bind = session.get_bind()
        if bind is None or bind.dialect.name != "mysql":
            return datetime.now()
        value = (await session.execute(select(func.now()))).scalar()
        return value if isinstance(value, datetime) else datetime.now()

    async def create(
        self,
        session: AsyncSession,
        *,
        task_id: str,
        conversation_id: str,
        user_id: str,
        parent_run_id: str,
        owner_token: str,
        record_status: str,
        record_meta: dict[str, Any],
        lease_expire_time: datetime,
        now: datetime,
        operator_name: str,
    ) -> ChatDataAgentInbox:
        row = ChatDataAgentInbox(
            conversation_id=conversation_id,
            user_id=user_id,
            run_id=parent_run_id,
            owner_token=owner_token,
            message_id=task_id,
            record_type=TASK_RECORD_TYPE,
            queue_type=TASK_QUEUE_TYPE,
            record_content="",
            record_meta=json.dumps(record_meta, ensure_ascii=False),
            record_status=record_status,
            lease_expire_time=lease_expire_time,
            delete_flag=0,
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

    async def get(
        self,
        session: AsyncSession,
        task_id: str,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
        for_update: bool = False,
    ) -> ChatDataAgentInbox | None:
        conditions = [
            ChatDataAgentInbox.message_id == task_id,
            ChatDataAgentInbox.record_type == TASK_RECORD_TYPE,
            ChatDataAgentInbox.delete_flag == 0,
        ]
        if conversation_id is not None:
            conditions.append(ChatDataAgentInbox.conversation_id == conversation_id)
        if user_id is not None:
            conditions.append(ChatDataAgentInbox.user_id == user_id)
        statement = select(ChatDataAgentInbox).where(*conditions)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return (await session.execute(statement)).scalar_one_or_none()

    async def list_for_conversation(
        self,
        session: AsyncSession,
        *,
        conversation_id: str,
        user_id: str,
        statuses: Collection[str] | None = None,
        limit: int = 50,
    ) -> list[ChatDataAgentInbox]:
        conditions = [
            ChatDataAgentInbox.conversation_id == conversation_id,
            ChatDataAgentInbox.user_id == user_id,
            ChatDataAgentInbox.record_type == TASK_RECORD_TYPE,
            ChatDataAgentInbox.delete_flag == 0,
        ]
        if statuses:
            conditions.append(ChatDataAgentInbox.record_status.in_(sorted(statuses)))
        result = await session.execute(
            select(ChatDataAgentInbox)
            # 列表接口只需要状态/标签，不拉报告与过程快照（可能是上百 KB）。
            .options(defer(ChatDataAgentInbox.record_content))
            .where(*conditions)
            .order_by(ChatDataAgentInbox.id.desc())
            .limit(max(int(limit or 0), 1))
        )
        return list(result.scalars().all())

    async def list_by_statuses(
        self,
        session: AsyncSession,
        statuses: Collection[str],
        *,
        limit: int = 20,
    ) -> list[ChatDataAgentInbox]:
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.record_type == TASK_RECORD_TYPE,
                ChatDataAgentInbox.record_status.in_(sorted(statuses)),
                ChatDataAgentInbox.delete_flag == 0,
            )
            .order_by(ChatDataAgentInbox.id.asc())
            .limit(max(int(limit or 0), 1))
        )
        return list(result.scalars().all())

    async def list_pending_notifications(
        self,
        session: AsyncSession,
        terminal_statuses: Collection[str],
        *,
        limit: int = 20,
    ) -> list[ChatDataAgentInbox]:
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.record_type == TASK_RECORD_TYPE,
                ChatDataAgentInbox.record_status.in_(sorted(terminal_statuses)),
                ChatDataAgentInbox.consumed_time.is_(None),
                ChatDataAgentInbox.delete_flag == 0,
            )
            .order_by(ChatDataAgentInbox.id.asc())
            .limit(max(int(limit or 0), 1))
        )
        return list(result.scalars().all())

    async def mark_consumed(self, session: AsyncSession, task_id: str, now: datetime) -> None:
        """标记任务已通知/已读；事件投递成功后用于维持 ``notified_at`` 口径。"""
        await session.execute(
            update(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.message_id == task_id,
                ChatDataAgentInbox.record_type == TASK_RECORD_TYPE,
                ChatDataAgentInbox.consumed_time.is_(None),
                ChatDataAgentInbox.delete_flag == 0,
            )
            .values(consumed_time=now, update_time=now)
        )

    async def list_expired(
        self,
        session: AsyncSession,
        active_statuses: Collection[str],
        now: datetime,
        *,
        limit: int = 20,
    ) -> list[ChatDataAgentInbox]:
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.record_type == TASK_RECORD_TYPE,
                ChatDataAgentInbox.record_status.in_(sorted(active_statuses)),
                ChatDataAgentInbox.lease_expire_time.is_not(None),
                ChatDataAgentInbox.lease_expire_time < now,
                ChatDataAgentInbox.delete_flag == 0,
            )
            .order_by(ChatDataAgentInbox.id.asc())
            .limit(max(int(limit or 0), 1))
        )
        return list(result.scalars().all())

    async def count_active(
        self,
        session: AsyncSession,
        conversation_id: str,
        user_id: str,
        active_statuses: Collection[str],
    ) -> int:
        result = await session.execute(
            select(func.count(ChatDataAgentInbox.id)).where(
                ChatDataAgentInbox.conversation_id == conversation_id,
                ChatDataAgentInbox.user_id == user_id,
                ChatDataAgentInbox.record_type == TASK_RECORD_TYPE,
                ChatDataAgentInbox.record_status.in_(sorted(active_statuses)),
                ChatDataAgentInbox.delete_flag == 0,
            )
        )
        return int(result.scalar() or 0)

    async def count_subagents(
        self,
        session: AsyncSession,
        conversation_id: str,
        user_id: str,
    ) -> int:
        """按 agent_session_id 去重统计主对话已创建的子智能体数量。

        ``agent_session_id`` 位于 JSON 文本里；这里只拉 message_id + meta，
        不加载报告正文。任务行数量受会话内派发频率约束，按会话逐行解析足以
        覆盖旧数据兼容（旧行没有 agent_session_id 时回退 task_id）。
        """
        result = await session.execute(
            select(ChatDataAgentInbox.message_id, ChatDataAgentInbox.record_meta).where(
                ChatDataAgentInbox.conversation_id == conversation_id,
                ChatDataAgentInbox.user_id == user_id,
                ChatDataAgentInbox.record_type == TASK_RECORD_TYPE,
                ChatDataAgentInbox.delete_flag == 0,
            )
        )
        session_ids: set[str] = set()
        for task_id, meta_text in result.all():
            meta = load_task_meta(meta_text)
            session_ids.add(str(meta.get("agent_session_id") or meta.get("root_task_id") or task_id))
        return len(session_ids)

    async def list_active_for_conversation(
        self,
        session: AsyncSession,
        conversation_id: str,
        user_id: str,
        active_statuses: Collection[str],
    ) -> list[ChatDataAgentInbox]:
        """列出会话内活跃任务；活跃任务数量受上限约束，调用方可在内存里按子会话过滤。"""
        result = await session.execute(
            select(ChatDataAgentInbox)
            .options(defer(ChatDataAgentInbox.record_content))
            .where(
                ChatDataAgentInbox.conversation_id == conversation_id,
                ChatDataAgentInbox.user_id == user_id,
                ChatDataAgentInbox.record_type == TASK_RECORD_TYPE,
                ChatDataAgentInbox.record_status.in_(sorted(active_statuses)),
                ChatDataAgentInbox.delete_flag == 0,
            )
            .order_by(ChatDataAgentInbox.id.desc())
        )
        return list(result.scalars().all())

    async def create_notification_message(
        self,
        session: AsyncSession,
        *,
        conversation_id: str,
        user_id: str,
        run_id: str,
        message_id: str,
        content: str,
        record_meta: dict[str, Any],
        now: datetime,
        operator_name: str,
    ) -> ChatDataAgentInbox:
        """把任务完成通知写成 inbox 的 next-step 消息，随 Run 生命周期消费。"""
        row = ChatDataAgentInbox(
            conversation_id=conversation_id,
            user_id=user_id,
            run_id=run_id,
            message_id=message_id,
            record_type="message",
            queue_type="next-step",
            record_content=content,
            record_meta=json.dumps(record_meta, ensure_ascii=False),
            record_status="pending",
            lease_expire_time=None,
            delete_flag=0,
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

    async def attach_pending_message(
        self,
        session: AsyncSession,
        message: ChatDataAgentInbox,
        run_id: str,
        now: datetime,
    ) -> None:
        if message.record_status == "pending":
            message.run_id = run_id
            message.update_time = now

    async def update_owned(
        self,
        session: AsyncSession,
        task_id: str,
        owner_token: str,
        *,
        allowed_statuses: Collection[str],
        values: dict[str, Any],
    ) -> int:
        """按 owner + 当前状态原子更新任务，返回命中行数。"""
        result = await session.execute(
            update(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.message_id == task_id,
                ChatDataAgentInbox.record_type == TASK_RECORD_TYPE,
                ChatDataAgentInbox.owner_token == owner_token,
                ChatDataAgentInbox.record_status.in_(sorted(allowed_statuses)),
                ChatDataAgentInbox.delete_flag == 0,
            )
            .values(**values)
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def update_any(
        self,
        session: AsyncSession,
        task_id: str,
        *,
        allowed_statuses: Collection[str],
        values: dict[str, Any],
    ) -> int:
        """用于回收/用户取消等无需 owner 的状态迁移。"""
        result = await session.execute(
            update(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.message_id == task_id,
                ChatDataAgentInbox.record_type == TASK_RECORD_TYPE,
                ChatDataAgentInbox.record_status.in_(sorted(allowed_statuses)),
                ChatDataAgentInbox.delete_flag == 0,
            )
            .values(**values)
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)


subagent_task_dao = SubagentTaskDao()
