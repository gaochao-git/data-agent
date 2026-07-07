"""对话历史数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, List, Optional

from sqlalchemy import select, update as sa_update

from utils.database import get_session as default_get_session
from utils.models import UserThread


class ConversationDao:
    """封装 user_threads 表读写。"""

    def __init__(self, session_context: Optional[Callable] = None):
        self._session_context = session_context or default_get_session

    @staticmethod
    def _thread_conditions(conversation_id: str, user_id: Optional[str] = None) -> list[Any]:
        conditions = [UserThread.thread_id == conversation_id, UserThread.delete_flag == 0]
        if user_id:
            conditions.append(UserThread.user_id == user_id)
        return conditions

    async def get_thread(self, conversation_id: str, user_id: Optional[str] = None) -> Optional[UserThread]:
        async with self._session_context() as session:
            result = await session.execute(select(UserThread).where(*self._thread_conditions(conversation_id, user_id)))
            return result.scalar_one_or_none()

    async def get_thread_by_id_any_status(self, conversation_id: str) -> Optional[UserThread]:
        async with self._session_context() as session:
            result = await session.execute(select(UserThread).where(UserThread.thread_id == conversation_id))
            return result.scalar_one_or_none()

    async def create_thread(
        self,
        *,
        user_id: str,
        thread_id: str,
        agent_id: str,
        title: Optional[str] = None,
        now: datetime,
    ) -> UserThread:
        async with self._session_context() as session:
            thread = UserThread(
                user_id=user_id,
                thread_id=thread_id,
                thread_title=title or "未命名会话",
                agent_id=agent_id,
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=user_id,
            )
            session.add(thread)
            await session.flush()
            return thread

    async def list_threads(
        self,
        *,
        agent_id: str,
        user_id: Optional[str],
        limit: int,
        offset: int,
    ) -> List[UserThread]:
        async with self._session_context() as session:
            query = select(UserThread).where(UserThread.agent_id == agent_id, UserThread.delete_flag == 0)
            if user_id:
                query = query.where(UserThread.user_id == user_id)
            query = query.order_by(UserThread.update_time.desc(), UserThread.id.desc()).offset(max(offset, 0)).limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def list_threads_by_agent_ids(
        self,
        *,
        agent_ids: List[str],
        user_id: Optional[str],
        limit: int,
        offset: int,
    ) -> List[UserThread]:
        async with self._session_context() as session:
            query = select(UserThread).where(UserThread.agent_id.in_(agent_ids), UserThread.delete_flag == 0)
            if user_id:
                query = query.where(UserThread.user_id == user_id)
            query = query.order_by(UserThread.update_time.desc(), UserThread.id.desc()).offset(max(offset, 0)).limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def sync_thread(
        self,
        *,
        user_id: str,
        thread_id: str,
        agent_id: str,
        title: Optional[str],
        is_new: bool,
        now: datetime,
    ) -> None:
        async with self._session_context() as session:
            if is_new:
                result = await session.execute(select(UserThread).where(UserThread.thread_id == thread_id))
                if result.scalar_one_or_none():
                    return
                session.add(UserThread(
                    user_id=user_id or "system",
                    thread_id=thread_id,
                    thread_title=title or "新会话",
                    agent_id=agent_id,
                    delete_flag=0,
                    create_time=now,
                    update_time=now,
                    create_by=user_id or "system",
                ))
                return
            values = {"update_time": now, "update_by": user_id}
            if title:
                values["thread_title"] = title
            await session.execute(sa_update(UserThread).where(UserThread.thread_id == thread_id).values(**values))

    async def update_title(self, *, conversation_id: str, title: str, user_id: Optional[str]) -> bool:
        async with self._session_context() as session:
            result = await session.execute(select(UserThread).where(*self._thread_conditions(conversation_id, user_id)))
            if not result.scalar_one_or_none():
                return False
            update_conditions = [UserThread.thread_id == conversation_id]
            if user_id:
                update_conditions.append(UserThread.user_id == user_id)
            await session.execute(
                sa_update(UserThread)
                .where(*update_conditions)
                .values(thread_title=title, update_time=datetime.now(), update_by=user_id or "")
            )
            return True

    async def soft_delete(self, *, conversation_id: str, user_id: Optional[str]) -> bool:
        async with self._session_context() as session:
            result = await session.execute(select(UserThread).where(*self._thread_conditions(conversation_id, user_id)))
            if not result.scalar_one_or_none():
                return False
            update_conditions = [UserThread.thread_id == conversation_id]
            if user_id:
                update_conditions.append(UserThread.user_id == user_id)
            await session.execute(
                sa_update(UserThread)
                .where(*update_conditions)
                .values(delete_flag=1, update_time=datetime.now(), update_by=user_id or "")
            )
            return True

    async def toggle_favorite(self, *, conversation_id: str, user_id: Optional[str]) -> Optional[bool]:
        async with self._session_context() as session:
            result = await session.execute(select(UserThread).where(*self._thread_conditions(conversation_id, user_id)))
            thread = result.scalar_one_or_none()
            if not thread:
                return None
            new_favorited = 0 if (thread.is_favorited if thread.is_favorited is not None else 0) else 1
            update_conditions = [UserThread.thread_id == conversation_id]
            if user_id:
                update_conditions.append(UserThread.user_id == user_id)
            await session.execute(
                sa_update(UserThread)
                .where(*update_conditions)
                .values(is_favorited=new_favorited)
            )
            return bool(new_favorited)

    async def get_agent_id(self, *, conversation_id: str, user_id: Optional[str]) -> Optional[str]:
        async with self._session_context() as session:
            result = await session.execute(select(UserThread.agent_id).where(*self._thread_conditions(conversation_id, user_id)))
            return result.scalar_one_or_none()

    async def exists_user_agent_thread(self, *, conversation_id: str, user_id: str, agent_id: str) -> bool:
        async with self._session_context() as session:
            result = await session.execute(
                select(UserThread.id).where(
                    UserThread.thread_id == conversation_id,
                    UserThread.user_id == user_id,
                    UserThread.agent_id == agent_id,
                    UserThread.delete_flag == 0,
                )
            )
            return result.scalar_one_or_none() is not None
