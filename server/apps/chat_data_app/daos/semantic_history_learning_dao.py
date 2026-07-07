"""指标集历史学习数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, List, Sequence, Tuple

from sqlalchemy import select

from utils.database import get_session as default_get_session
from utils.models import ChatDataQueryExecution, ChatDataQueryTask


class SemanticHistoryLearningDao:
    """封装指标集历史学习需要的查询历史读取。"""

    def __init__(self, session_context: Callable | None = None):
        self._session_context = session_context or default_get_session

    async def list_learning_history(
        self,
        *,
        datasource_id: str,
        user_id: str,
        cutoff_time: datetime,
        status_values: Sequence[str],
        scan_limit: int,
    ) -> List[Tuple[ChatDataQueryExecution, ChatDataQueryTask | None]]:
        stmt = (
            select(ChatDataQueryExecution, ChatDataQueryTask)
            .outerjoin(ChatDataQueryTask, ChatDataQueryTask.task_id == ChatDataQueryExecution.task_id)
            .where(
                ChatDataQueryExecution.datasource_id == datasource_id,
                ChatDataQueryExecution.user_id == user_id,
                ChatDataQueryExecution.delete_flag == 0,
                ChatDataQueryExecution.create_time >= cutoff_time,
                ChatDataQueryExecution.sql_status.in_(list(status_values)),
                ChatDataQueryTask.query_type.in_(["agent", "builder", "sql"]),
            )
            .order_by(ChatDataQueryExecution.create_time.desc(), ChatDataQueryExecution.id.desc())
            .limit(scan_limit)
        )
        async with self._session_context() as db:
            return list((await db.execute(stmt)).all())
