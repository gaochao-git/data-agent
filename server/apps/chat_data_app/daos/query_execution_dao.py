"""查询执行明细数据访问层。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from sqlalchemy import select

from utils.database import get_session as default_get_session
from utils.models import ChatDataQueryExecution, ChatDataQueryTask


class QueryExecutionDao:
    """封装 chat_data_query_execution 表读写。"""

    def __init__(self, session_context: Callable | None = None):
        self._session_context = session_context or default_get_session

    async def create_pending_executions(self, specs: Sequence[dict[str, str]]) -> list[str]:
        """批量预创建 pending 状态执行明细，同一事务提交，返回按传入顺序的 execution_id。

        每个 spec 需要 execution_id、task_id、user_id、datasource_id、
        member_datasource_id、member_name、executor_type、sql_text、operator_name；
        user_id 同时填充 create_by/update_by，operator_name 同时填充
        create_by_name/update_by_name。
        """
        now = datetime.now()
        async with self._session_context() as db:
            for spec in specs:
                db.add(
                    ChatDataQueryExecution(
                        execution_id=spec["execution_id"],
                        task_id=spec["task_id"],
                        user_id=spec["user_id"],
                        datasource_id=spec["datasource_id"],
                        member_datasource_id=spec["member_datasource_id"],
                        member_name=spec["member_name"],
                        executor_type=spec["executor_type"],
                        sql_text=spec["sql_text"],
                        sql_status="pending",
                        delete_flag=0,
                        create_time=now,
                        update_time=now,
                        create_by=spec["user_id"],
                        create_by_name=spec["operator_name"],
                        update_by=spec["user_id"],
                        update_by_name=spec["operator_name"],
                    )
                )
        return [spec["execution_id"] for spec in specs]

    async def update_execution_locked(
        self,
        execution_id: str,
        apply_updates: Callable[[ChatDataQueryExecution], bool],
    ) -> ChatDataQueryExecution | None:
        """行锁读取执行明细并应用变更；apply_updates 返回 False 表示保持原状。

        行锁读取：取消操作与执行线程并发写时，先锁定再由 apply_updates 校验
        终态守卫，避免 REPEATABLE READ 快照读读到旧的 cancelled 前状态、回写
        覆盖终态。变更生效时统一刷新 update_time，提交由 get_session 退出自动完成。
        """
        async with self._session_context() as db:
            result = await db.execute(
                select(ChatDataQueryExecution).filter_by(execution_id=execution_id, delete_flag=0).with_for_update()
            )
            execution = result.scalar_one_or_none()
            if not execution:
                return None
            if not apply_updates(execution):
                return execution
            execution.update_time = datetime.now()
            return execution

    async def get_execution_status(self, execution_id: str) -> str:
        """读取执行明细当前状态（小写归一，缺失返回空串）。"""
        async with self._session_context() as db:
            result = await db.execute(
                select(ChatDataQueryExecution.sql_status).filter_by(execution_id=execution_id, delete_flag=0)
            )
            return str(result.scalar_one_or_none() or "").strip().lower()

    async def list_executions_with_task(
        self,
        task_id: str,
    ) -> tuple[list[ChatDataQueryExecution], ChatDataQueryTask | None]:
        """读取任务下全部执行明细；存在明细时附带读取任务主记录。"""
        async with self._session_context() as db:
            result = await db.execute(
                select(ChatDataQueryExecution)
                .filter_by(task_id=task_id, delete_flag=0)
                .order_by(ChatDataQueryExecution.create_time.asc(), ChatDataQueryExecution.id.asc())
            )
            executions = list(result.scalars().all())
            if not executions:
                return executions, None
            task_result = await db.execute(select(ChatDataQueryTask).filter_by(task_id=task_id, delete_flag=0))
            return executions, task_result.scalar_one_or_none()
