"""ChatData Agent 运行收件箱数据访问。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.models import ChatDataAgentInbox, UserThread


class AgentInboxDao:
    """只负责事务内的运行记录和追加消息读写。"""

    async def now(self, session: AsyncSession) -> datetime:
        """租约统一使用数据库时钟，避免多进程 / 多机时钟漂移造成误判。

        非 MySQL（单测 SQLite）回退应用时钟：SQLite CURRENT_TIMESTAMP 为 UTC，
        与测试内 datetime.now() 基准不一致。
        """
        bind = session.get_bind()
        if bind is None or bind.dialect.name != "mysql":
            return datetime.now()
        value = (await session.execute(select(func.now()))).scalar()
        return value if isinstance(value, datetime) else datetime.now()

    async def lock_thread(self, session: AsyncSession, conversation_id: str, user_id: str) -> UserThread | None:
        result = await session.execute(
            select(UserThread)
            .where(
                UserThread.thread_id == conversation_id,
                UserThread.user_id == user_id,
                UserThread.delete_flag == 0,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_active_run(
        self,
        session: AsyncSession,
        conversation_id: str,
        user_id: str,
    ) -> ChatDataAgentInbox | None:
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.conversation_id == conversation_id,
                ChatDataAgentInbox.user_id == user_id,
                ChatDataAgentInbox.record_type == "run",
                ChatDataAgentInbox.status.in_(("pending", "active", "cancelling", "waiting_user")),
                ChatDataAgentInbox.delete_flag == 0,
            )
            .order_by(ChatDataAgentInbox.id.desc())
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_active_run_snapshot(
        self,
        session: AsyncSession,
        conversation_id: str,
        user_id: str,
    ) -> ChatDataAgentInbox | None:
        """读取活跃 Run，不加锁，供会话详情返回恢复信息。"""
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.conversation_id == conversation_id,
                ChatDataAgentInbox.user_id == user_id,
                ChatDataAgentInbox.record_type == "run",
                ChatDataAgentInbox.status.in_(("pending", "active", "cancelling", "waiting_user")),
                ChatDataAgentInbox.delete_flag == 0,
            )
            .order_by(ChatDataAgentInbox.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_run(
        self,
        session: AsyncSession,
        conversation_id: str,
        user_id: str,
    ) -> ChatDataAgentInbox | None:
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.conversation_id == conversation_id,
                ChatDataAgentInbox.user_id == user_id,
                ChatDataAgentInbox.record_type == "run",
                ChatDataAgentInbox.delete_flag == 0,
            )
            .order_by(ChatDataAgentInbox.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def claim_messages(
        self,
        session: AsyncSession,
        run_id: str,
        now: datetime,
        lease_expire_time: datetime,
        queue_type: str | None = None,
        limit: int | None = None,
    ) -> list[ChatDataAgentInbox]:
        conditions = [
            ChatDataAgentInbox.run_id == run_id,
            ChatDataAgentInbox.record_type == "message",
            ChatDataAgentInbox.delete_flag == 0,
            or_(
                ChatDataAgentInbox.status == "pending",
                (ChatDataAgentInbox.status == "claimed")
                & (ChatDataAgentInbox.lease_expire_time < now),
            ),
        ]
        if queue_type:
            conditions.append(ChatDataAgentInbox.queue_type == queue_type)
        statement = (
            select(ChatDataAgentInbox)
            .where(*conditions)
            .order_by(ChatDataAgentInbox.id.asc())
            .with_for_update()
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await session.execute(statement)
        messages = list(result.scalars().all())
        for message in messages:
            message.status = "claimed"
            message.claimed_time = now
            message.lease_expire_time = lease_expire_time
            message.update_time = now
        return messages

    async def get_message_by_id(
        self,
        session: AsyncSession,
        conversation_id: str,
        user_id: str,
        message_id: str,
    ) -> ChatDataAgentInbox | None:
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.conversation_id == conversation_id,
                ChatDataAgentInbox.user_id == user_id,
                ChatDataAgentInbox.message_id == message_id,
                ChatDataAgentInbox.record_type == "message",
                ChatDataAgentInbox.delete_flag == 0,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_run_messages(
        self,
        session: AsyncSession,
        run_id: str,
    ) -> list[ChatDataAgentInbox]:
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.run_id == run_id,
                ChatDataAgentInbox.record_type == "message",
            )
            .order_by(ChatDataAgentInbox.id.asc())
        )
        return list(result.scalars().all())

    async def get_claimed_messages(
        self,
        session: AsyncSession,
        run_id: str,
        message_ids: list[str],
    ) -> list[ChatDataAgentInbox]:
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.run_id == run_id,
                ChatDataAgentInbox.message_id.in_(message_ids),
                ChatDataAgentInbox.record_type == "message",
                ChatDataAgentInbox.status == "claimed",
                ChatDataAgentInbox.delete_flag == 0,
            )
            .with_for_update()
        )
        return list(result.scalars().all())

    async def requeue_messages(self, session: AsyncSession, run_id: str, now: datetime) -> None:
        """将终止运行尚未确认消费的消息恢复为待处理。"""
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.run_id == run_id,
                ChatDataAgentInbox.record_type == "message",
                ChatDataAgentInbox.status == "claimed",
                ChatDataAgentInbox.delete_flag == 0,
            )
            .with_for_update()
        )
        for message in result.scalars().all():
            message.status = "pending"
            message.lease_expire_time = None
            message.update_time = now

    async def renew_claimed_message_leases(
        self,
        session: AsyncSession,
        run_id: str,
        lease_expire_time: datetime,
        now: datetime,
    ) -> None:
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.run_id == run_id,
                ChatDataAgentInbox.record_type == "message",
                ChatDataAgentInbox.status == "claimed",
                ChatDataAgentInbox.delete_flag == 0,
            )
            .with_for_update()
        )
        for message in result.scalars().all():
            message.lease_expire_time = lease_expire_time
            message.update_time = now

    async def adopt_pending_messages(
        self,
        session: AsyncSession,
        conversation_id: str,
        user_id: str,
        run_id: str,
        now: datetime,
    ) -> None:
        """让新运行接管该会话此前运行遗留的待处理消息。"""
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.conversation_id == conversation_id,
                ChatDataAgentInbox.user_id == user_id,
                ChatDataAgentInbox.record_type == "message",
                ChatDataAgentInbox.status == "pending",
                ChatDataAgentInbox.delete_flag == 0,
            )
            .with_for_update()
        )
        for message in result.scalars().all():
            message.run_id = run_id
            message.update_time = now

    async def has_pending_messages(
        self,
        session: AsyncSession,
        conversation_id: str,
        user_id: str,
    ) -> bool:
        result = await session.execute(
            select(ChatDataAgentInbox.id)
            .where(
                ChatDataAgentInbox.conversation_id == conversation_id,
                ChatDataAgentInbox.user_id == user_id,
                ChatDataAgentInbox.record_type == "message",
                ChatDataAgentInbox.status == "pending",
                ChatDataAgentInbox.delete_flag == 0,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_active_run_by_id(
        self,
        session: AsyncSession,
        run_id: str,
    ) -> ChatDataAgentInbox | None:
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.run_id == run_id,
                ChatDataAgentInbox.record_type == "run",
                ChatDataAgentInbox.status.in_(("active", "cancelling")),
                ChatDataAgentInbox.delete_flag == 0,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_run_by_id(
        self,
        session: AsyncSession,
        run_id: str,
    ) -> ChatDataAgentInbox | None:
        result = await session.execute(
            select(ChatDataAgentInbox).where(
                ChatDataAgentInbox.run_id == run_id,
                ChatDataAgentInbox.record_type == "run",
                ChatDataAgentInbox.delete_flag == 0,
            )
        )
        return result.scalar_one_or_none()

    async def list_runnable_runs(
        self,
        session: AsyncSession,
        now: datetime,
        limit: int = 20,
    ) -> list[ChatDataAgentInbox]:
        # 这里只返回 pending；过期 active/cancelling 由 list_expired_runs
        # 提供给后台回收器收口，不自动续跑。
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.record_type == "run",
                ChatDataAgentInbox.status == "pending",
                ChatDataAgentInbox.delete_flag == 0,
            )
            .order_by(ChatDataAgentInbox.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_expired_runs(
        self,
        session: AsyncSession,
        now: datetime,
        limit: int = 20,
    ) -> list[ChatDataAgentInbox]:
        """锁定租约已过期的执行 Run，供后台回收器收口。"""
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.record_type == "run",
                ChatDataAgentInbox.status.in_(("active", "cancelling")),
                ChatDataAgentInbox.lease_expire_time.is_not(None),
                ChatDataAgentInbox.lease_expire_time < now,
                ChatDataAgentInbox.delete_flag == 0,
            )
            .order_by(ChatDataAgentInbox.id.asc())
            .limit(limit)
            .with_for_update()
        )
        return list(result.scalars().all())

    async def list_claimed_messages(
        self,
        session: AsyncSession,
        run_id: str,
    ) -> list[ChatDataAgentInbox]:
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.run_id == run_id,
                ChatDataAgentInbox.record_type == "message",
                ChatDataAgentInbox.status == "claimed",
                ChatDataAgentInbox.delete_flag == 0,
            )
            .order_by(ChatDataAgentInbox.id.asc())
            .with_for_update()
        )
        return list(result.scalars().all())

    async def get_run_by_id_for_update(
        self,
        session: AsyncSession,
        run_id: str,
    ) -> ChatDataAgentInbox | None:
        result = await session.execute(
            select(ChatDataAgentInbox)
            .where(
                ChatDataAgentInbox.run_id == run_id,
                ChatDataAgentInbox.record_type == "run",
                ChatDataAgentInbox.delete_flag == 0,
            )
            # populate_existing：同事务内此前可能已普通读过该行（身份映射缓存了
            # 旧属性），锁定读必须强制刷新，否则锁内状态校验读到的是加锁前的
            # 过期值，多进程并发 prepare 会全部放行（REPEATABLE READ 下同样
            # 看不到他事务已提交的 status=active）。
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()


agent_inbox_dao = AgentInboxDao()
