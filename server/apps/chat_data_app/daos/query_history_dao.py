"""查询历史数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, func, or_, select

from utils.database import get_session as default_get_session
from utils.models import ChatDataQueryExecution, ChatDataQueryTask


class QueryHistoryDao:
    """封装查询历史相关 SQL/ORM 读写。"""

    def __init__(self, session_context: Optional[Callable] = None):
        self._session_context = session_context or default_get_session

    @staticmethod
    def _executed_time_expr():
        return func.coalesce(
            ChatDataQueryExecution.finished_time,
            ChatDataQueryExecution.started_time,
            ChatDataQueryExecution.create_time,
        )

    @classmethod
    def _append_time_conditions(
        cls,
        conditions: List[Any],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> None:
        executed_time_expr = cls._executed_time_expr()
        if start_time:
            conditions.append(executed_time_expr >= start_time)
        if end_time:
            conditions.append(executed_time_expr <= end_time)

    @staticmethod
    def _append_source_condition(conditions: List[Any], source_filter: Optional[List[str]]) -> None:
        if not source_filter:
            return
        source_values = [item for item in source_filter if item]
        if "" in source_filter:
            conditions.append(or_(ChatDataQueryTask.query_type.in_(source_values), ChatDataQueryTask.query_type.is_(None)))
            return
        conditions.append(ChatDataQueryTask.query_type.in_(source_filter))

    @staticmethod
    def _append_status_condition(conditions: List[Any], status_filter: Optional[str]) -> None:
        if not status_filter:
            return
        if status_filter == "cancelled":
            conditions.append(or_(
                ChatDataQueryExecution.sql_status == "cancelled",
                and_(ChatDataQueryTask.task_status == "cancelled", ChatDataQueryExecution.sql_status.in_(["pending", "running"])),
            ))
            return
        if status_filter in {"pending", "running"}:
            conditions.append(and_(
                ChatDataQueryExecution.sql_status == status_filter,
                or_(ChatDataQueryTask.task_status.is_(None), ChatDataQueryTask.task_status != "cancelled"),
            ))
            return
        conditions.append(ChatDataQueryExecution.sql_status == status_filter)

    @staticmethod
    def _append_admin_conditions(
        conditions: List[Any],
        *,
        datasource_id: str,
        semantic_config_id: str,
        user_id: str,
        username: str,
        keyword: str,
    ) -> None:
        if datasource_id:
            conditions.append(ChatDataQueryExecution.datasource_id == datasource_id)
        if user_id:
            conditions.append(ChatDataQueryExecution.user_id == user_id)
        if username:
            username_keyword = f"%{username}%"
            conditions.append(or_(
                ChatDataQueryExecution.create_by_name.like(username_keyword),
                ChatDataQueryExecution.update_by_name.like(username_keyword),
                ChatDataQueryTask.create_by_name.like(username_keyword),
                ChatDataQueryTask.update_by_name.like(username_keyword),
                ChatDataQueryTask.request_payload.like(username_keyword),
            ))
        if keyword:
            conditions.append(ChatDataQueryExecution.sql_text.like(f"%{keyword}%"))
        if semantic_config_id:
            conditions.append(ChatDataQueryTask.request_payload.like(f"%{semantic_config_id}%"))

    async def _list_by_conditions(
        self,
        conditions: Sequence[Any],
        *,
        page: int,
        page_size: int,
    ) -> Tuple[List[Tuple[ChatDataQueryExecution, Optional[ChatDataQueryTask]]], int]:
        async with self._session_context() as db:
            count_stmt = (
                select(func.count())
                .select_from(ChatDataQueryExecution)
                .outerjoin(ChatDataQueryTask, ChatDataQueryTask.task_id == ChatDataQueryExecution.task_id)
                .where(*conditions)
            )
            total_result = await db.execute(count_stmt)
            total = int(total_result.scalar() or 0)

            stmt = (
                select(ChatDataQueryExecution, ChatDataQueryTask)
                .outerjoin(ChatDataQueryTask, ChatDataQueryTask.task_id == ChatDataQueryExecution.task_id)
                .where(*conditions)
                .order_by(ChatDataQueryExecution.create_time.desc(), ChatDataQueryExecution.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await db.execute(stmt)
            return list(result.all()), total

    async def list_user_history(
        self,
        *,
        datasource_id: str,
        user_id: str,
        page: int,
        page_size: int,
        source_filter: Optional[List[str]],
        status_filter: Optional[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> Tuple[List[Tuple[ChatDataQueryExecution, Optional[ChatDataQueryTask]]], int]:
        conditions: List[Any] = [
            ChatDataQueryExecution.datasource_id == datasource_id,
            ChatDataQueryExecution.user_id == user_id,
            ChatDataQueryExecution.delete_flag == 0,
        ]
        self._append_source_condition(conditions, source_filter)
        self._append_status_condition(conditions, status_filter)
        self._append_time_conditions(conditions, start_time, end_time)
        return await self._list_by_conditions(conditions, page=page, page_size=page_size)

    async def list_admin_history(
        self,
        *,
        datasource_id: str,
        semantic_config_id: str,
        user_id: str,
        username: str,
        keyword: str,
        page: int,
        page_size: int,
        source_filter: Optional[List[str]],
        status_filter: Optional[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> Tuple[List[Tuple[ChatDataQueryExecution, Optional[ChatDataQueryTask]]], int]:
        conditions: List[Any] = [ChatDataQueryExecution.delete_flag == 0]
        self._append_admin_conditions(
            conditions,
            datasource_id=datasource_id,
            semantic_config_id=semantic_config_id,
            user_id=user_id,
            username=username,
            keyword=keyword,
        )
        self._append_source_condition(conditions, source_filter)
        self._append_status_condition(conditions, status_filter)
        self._append_time_conditions(conditions, start_time, end_time)
        return await self._list_by_conditions(conditions, page=page, page_size=page_size)

    async def get_user_history_execution(
        self,
        *,
        execution_id: str,
        user_id: str,
    ) -> Tuple[Optional[ChatDataQueryExecution], Optional[ChatDataQueryTask]]:
        async with self._session_context() as db:
            result = await db.execute(
                select(ChatDataQueryExecution, ChatDataQueryTask)
                .outerjoin(ChatDataQueryTask, ChatDataQueryTask.task_id == ChatDataQueryExecution.task_id)
                .where(
                    ChatDataQueryExecution.execution_id == execution_id,
                    ChatDataQueryExecution.user_id == user_id,
                    ChatDataQueryExecution.delete_flag == 0,
                )
            )
            row = result.first()
            return (row[0], row[1]) if row else (None, None)

    async def get_execution_models_by_task(self, task_id: str) -> List[ChatDataQueryExecution]:
        if not task_id:
            return []
        async with self._session_context() as db:
            result = await db.execute(
                select(ChatDataQueryExecution)
                .filter_by(task_id=task_id, delete_flag=0)
                .order_by(ChatDataQueryExecution.create_time.asc(), ChatDataQueryExecution.id.asc())
            )
            return list(result.scalars().all())

    async def get_execution_model(self, execution_id: str) -> Optional[ChatDataQueryExecution]:
        if not execution_id:
            return None
        async with self._session_context() as db:
            result = await db.execute(select(ChatDataQueryExecution).filter_by(execution_id=execution_id, delete_flag=0))
            return result.scalar_one_or_none()

    async def list_running_executions(
        self,
        *,
        running_statuses: Sequence[str],
        datasource_id: str = "",
        user_id: str = "",
    ) -> List[Tuple[ChatDataQueryExecution, Optional[ChatDataQueryTask]]]:
        conditions: List[Any] = [
            ChatDataQueryExecution.delete_flag == 0,
            ChatDataQueryExecution.sql_status.in_(sorted(running_statuses)),
        ]
        if datasource_id:
            conditions.append(ChatDataQueryExecution.datasource_id == datasource_id)
        if user_id:
            conditions.append(ChatDataQueryExecution.user_id == user_id)
        async with self._session_context() as db:
            result = await db.execute(
                select(ChatDataQueryExecution, ChatDataQueryTask)
                .outerjoin(ChatDataQueryTask, ChatDataQueryTask.task_id == ChatDataQueryExecution.task_id)
                .where(*conditions)
                .order_by(ChatDataQueryExecution.create_time.asc(), ChatDataQueryExecution.id.asc())
            )
            return list(result.all())
