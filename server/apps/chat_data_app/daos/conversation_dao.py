"""对话历史数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, List, Optional

from sqlalchemy import and_, case, func, literal, or_, select, update as sa_update
from sqlalchemy.orm import aliased

from utils.database import get_session as default_get_session
from utils.models import ChatDataAgentInbox, UserThread


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
            query = query.order_by(UserThread.is_favorited.desc(), UserThread.update_time.desc(), UserThread.id.desc()).offset(max(offset, 0)).limit(limit)
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
            query = query.order_by(UserThread.is_favorited.desc(), UserThread.update_time.desc(), UserThread.id.desc()).offset(max(offset, 0)).limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def list_active_run_statuses(
        self,
        conversation_ids: list[str],
        user_id: Optional[str],
    ) -> dict[str, str]:
        """批量读取会话的有效运行状态，避免历史列表产生 N+1 查询。"""
        if not conversation_ids:
            return {}
        now = datetime.now()
        conditions = [
            ChatDataAgentInbox.conversation_id.in_(conversation_ids),
            ChatDataAgentInbox.record_type == "run",
            ChatDataAgentInbox.delete_flag == 0,
            or_(
                ChatDataAgentInbox.status.in_(("pending", "waiting_user")),
                and_(
                    ChatDataAgentInbox.status.in_(("active", "cancelling")),
                    ChatDataAgentInbox.lease_expire_time > now,
                ),
            ),
        ]
        if user_id:
            conditions.append(ChatDataAgentInbox.user_id == user_id)
        async with self._session_context() as session:
            result = await session.execute(
                select(ChatDataAgentInbox.conversation_id, ChatDataAgentInbox.status)
                .where(*conditions)
            )
            statuses: dict[str, str] = {}
            priority = {"cancelling": 4, "waiting_user": 3, "active": 2, "pending": 1}
            for conversation_id, status in result.all():
                current = statuses.get(conversation_id)
                if current is None or priority.get(status, 0) > priority.get(current, 0):
                    statuses[conversation_id] = status
            return statuses

    @staticmethod
    def _list_conditions(agent_id: str, user_id: Optional[str]) -> list[Any]:
        conditions = [UserThread.agent_id == agent_id, UserThread.delete_flag == 0]
        if user_id:
            conditions.append(UserThread.user_id == user_id)
        return conditions

    async def list_thread_group_first_pages(
        self,
        *,
        agent_id: str,
        user_id: Optional[str],
        limit_per_group: int,
    ) -> List[UserThread]:
        """一次查询返回每个历史会话分组的首屏，额外一条用于判断 has_more。"""
        datasource_group = func.trim(func.coalesce(UserThread.datasource_id, ""))
        group_type = case(
            (UserThread.is_favorited == 1, literal("pinned")),
            (datasource_group == "", literal("unclassified")),
            else_=literal("datasource"),
        )
        group_datasource = case(
            (UserThread.is_favorited == 1, literal("")),
            else_=datasource_group,
        )
        group_rank = func.row_number().over(
            partition_by=(group_type, group_datasource),
            order_by=(UserThread.update_time.desc(), UserThread.id.desc()),
        ).label("group_rank")
        ranked = select(UserThread, group_rank).where(
            *self._list_conditions(agent_id, user_id)
        ).subquery()
        ranked_thread = aliased(UserThread, ranked)
        async with self._session_context() as session:
            result = await session.execute(
                select(ranked_thread)
                .where(ranked.c.group_rank <= limit_per_group)
                .order_by(
                    ranked_thread.is_favorited.desc(),
                    ranked_thread.update_time.desc(),
                    ranked_thread.id.desc(),
                )
            )
            return list(result.scalars().all())

    async def list_threads_in_group(
        self,
        *,
        agent_id: str,
        user_id: Optional[str],
        group_type: str,
        datasource_id: Optional[str],
        limit: int,
        cursor_update_time: Optional[datetime] = None,
        cursor_id: Optional[int] = None,
    ) -> List[UserThread]:
        """按稳定分组筛选会话；收藏会话只属于 pinned 分组。"""
        conditions = self._list_conditions(agent_id, user_id)
        normalized_datasource = func.trim(func.coalesce(UserThread.datasource_id, ""))
        if group_type == "pinned":
            conditions.append(UserThread.is_favorited == 1)
        else:
            conditions.append(func.coalesce(UserThread.is_favorited, 0) != 1)
            if group_type == "unclassified":
                conditions.append(normalized_datasource == "")
            else:
                conditions.append(normalized_datasource == datasource_id)
        if cursor_update_time is not None and cursor_id is not None:
            conditions.append(or_(
                UserThread.update_time < cursor_update_time,
                and_(UserThread.update_time == cursor_update_time, UserThread.id < cursor_id),
            ))
        async with self._session_context() as session:
            result = await session.execute(
                select(UserThread)
                .where(*conditions)
                .order_by(UserThread.update_time.desc(), UserThread.id.desc())
                .limit(limit)
            )
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

    async def get_datasource_id(self, *, conversation_id: str, user_id: str) -> Optional[str]:
        """读取会话绑定的数据源组。"""
        async with self._session_context() as session:
            result = await session.execute(
                select(UserThread.datasource_id).where(
                    *self._thread_conditions(conversation_id, user_id)
                )
            )
            datasource_id = result.scalar_one_or_none()
            return str(datasource_id or "").strip() or None

    async def list_unbound_threads(self, *, limit: int) -> List[UserThread]:
        """列出仍需从旧 checkpoint 回填数据源绑定的有效会话。"""
        async with self._session_context() as session:
            result = await session.execute(
                select(UserThread)
                .where(
                    UserThread.datasource_id == "",
                    UserThread.delete_flag == 0,
                )
                .order_by(UserThread.id.asc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def backfill_datasource_binding(
        self,
        *,
        conversation_id: str,
        user_id: str,
        datasource_id: str,
    ) -> bool:
        """仅在绑定仍为空时回填，避免覆盖迁移期间产生的新绑定。"""
        async with self._session_context() as session:
            result = await session.execute(
                sa_update(UserThread)
                .where(
                    UserThread.thread_id == conversation_id,
                    UserThread.user_id == user_id,
                    UserThread.datasource_id == "",
                    UserThread.delete_flag == 0,
                )
                .values(
                    datasource_id=datasource_id,
                    update_time=datetime.now(),
                    update_by=user_id,
                )
            )
            return bool(result.rowcount)

    async def resolve_datasource_binding(
        self,
        *,
        conversation_id: str,
        user_id: str,
        requested_datasource_id: str,
    ) -> Optional[str]:
        """在行锁内读取绑定，未绑定时写入请求值。"""
        async with self._session_context() as session:
            result = await session.execute(
                select(UserThread)
                .where(*self._thread_conditions(conversation_id, user_id))
                .with_for_update()
            )
            thread = result.scalar_one_or_none()
            if thread is None:
                return None
            bound_datasource_id = str(thread.datasource_id or "").strip()
            if not bound_datasource_id and requested_datasource_id:
                bound_datasource_id = requested_datasource_id
                await session.execute(
                    sa_update(UserThread)
                    .where(UserThread.id == thread.id)
                    .values(
                        datasource_id=bound_datasource_id,
                        update_time=datetime.now(),
                        update_by=user_id,
                    )
                )
            return bound_datasource_id or None

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
