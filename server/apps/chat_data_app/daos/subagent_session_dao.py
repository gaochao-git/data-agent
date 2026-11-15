"""后台子智能体多轮会话快照数据访问。

一个 ``session_id`` 只保留一行当前完整消息历史；任务按轮次落在
``chat_data_agent_inbox``，本表只负责“续聊所需的会话状态”。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.models import ChatDataSubagentSession


class SubagentSessionDao:
    """只做会话快照的读取与整体覆盖，不编排任务状态。"""

    async def get(
        self,
        session: AsyncSession,
        session_id: str,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
        for_update: bool = False,
    ) -> ChatDataSubagentSession | None:
        conditions = [
            ChatDataSubagentSession.session_id == session_id,
            ChatDataSubagentSession.delete_flag == 0,
        ]
        if conversation_id is not None:
            conditions.append(ChatDataSubagentSession.conversation_id == conversation_id)
        if user_id is not None:
            conditions.append(ChatDataSubagentSession.user_id == user_id)
        statement = select(ChatDataSubagentSession).where(*conditions)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return (await session.execute(statement)).scalar_one_or_none()

    async def upsert(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        conversation_id: str,
        user_id: str,
        latest_task_id: str,
        turn_count: int,
        messages_json: str,
        now: datetime,
        operator_name: str,
    ) -> ChatDataSubagentSession:
        row = await self.get(
            session,
            session_id,
            conversation_id=conversation_id,
            user_id=user_id,
            for_update=True,
        )
        if row is None:
            row = ChatDataSubagentSession(
                session_id=session_id,
                conversation_id=conversation_id,
                user_id=user_id,
                latest_task_id=latest_task_id,
                turn_count=max(int(turn_count or 1), 1),
                messages_json=messages_json,
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=user_id,
                create_by_name=operator_name,
                update_by=user_id,
                update_by_name=operator_name,
            )
            session.add(row)
        else:
            row.latest_task_id = latest_task_id
            row.turn_count = max(int(turn_count or 1), 1)
            row.messages_json = messages_json
            row.update_time = now
            row.update_by = user_id
            row.update_by_name = operator_name
        await session.flush()
        return row


subagent_session_dao = SubagentSessionDao()
