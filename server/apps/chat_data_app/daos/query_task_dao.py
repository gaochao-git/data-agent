"""查询任务数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from sqlalchemy import select

from utils.database import get_session as default_get_session
from utils.models import ChatDataQueryTask


class QueryTaskDao:
    """封装 chat_data_query_task 表读写，含行锁状态流转。"""

    def __init__(self, session_context: Optional[Callable] = None):
        self._session_context = session_context or default_get_session

    async def get_task(self, task_id: str) -> Optional[ChatDataQueryTask]:
        """按任务 ID 读取未删除任务。"""
        async with self._session_context() as db:
            result = await db.execute(select(ChatDataQueryTask).filter_by(task_id=task_id, delete_flag=0))
            return result.scalar_one_or_none()

    async def create_task(
        self,
        *,
        task_id: str,
        task_type: str,
        datasource_id: str,
        query_type: str,
        request_payload_json: str,
        progress_text: str,
        operator_user_id: str,
        operator_name: str,
    ) -> ChatDataQueryTask:
        """创建 pending 状态的查询任务，flush 后回读库端默认值并返回。"""
        task = ChatDataQueryTask(
            task_id=task_id,
            task_type=task_type,
            datasource_id=datasource_id,
            query_type=query_type,
            task_status="pending",
            progress_text=progress_text,
            request_payload=request_payload_json,
            delete_flag=0,
            create_time=datetime.now(),
            create_by=operator_user_id,
            create_by_name=operator_name,
            update_by=operator_user_id,
            update_by_name=operator_name,
        )
        async with self._session_context() as db:
            db.add(task)
            await db.flush()
            await db.refresh(task)
            return task

    async def update_task_locked(
        self,
        task_id: str,
        apply_updates: Callable[[ChatDataQueryTask], bool],
    ) -> Optional[ChatDataQueryTask]:
        """行锁读取任务并应用变更；apply_updates 返回 False 表示保持原状不落库。

        行锁读取：与取消操作互斥，防止快照读守卫失效导致终态被回写覆盖。
        变更生效时统一刷新 update_time；原实现为提交后 refresh，此处保持
        相同语句分组，先 flush 写入再 refresh 回读（refresh 不会自动 flush），
        提交由 get_session 退出自动完成。
        """
        async with self._session_context() as db:
            result = await db.execute(
                select(ChatDataQueryTask)
                .filter_by(task_id=task_id, delete_flag=0)
                .with_for_update()
            )
            task = result.scalar_one_or_none()
            if not task:
                return None
            if not apply_updates(task):
                return task
            task.update_time = datetime.now()
            await db.flush()
            await db.refresh(task)
            return task
