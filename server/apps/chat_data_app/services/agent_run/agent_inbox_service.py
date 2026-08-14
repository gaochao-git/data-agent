"""跨进程 Agent 运行协调与运行中消息收件箱。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from langchain_core.messages import HumanMessage

from utils.database import get_session
from utils.models import ChatDataAgentInbox
from ...daos.agent_inbox_dao import AgentInboxDao, agent_inbox_dao

MESSAGE_LEASE_SECONDS = 60
RUN_LEASE_SECONDS = 120


class AgentRunConflictError(RuntimeError):
    pass


class AgentRunNotActiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaimedAgentMessages:
    run_id: str
    messages: list[HumanMessage]


class AgentInboxService:
    def __init__(self, dao: AgentInboxDao = agent_inbox_dao):
        self._dao = dao

    async def start_run(self, conversation_id: str, user_id: str, operator_name: str) -> str:
        now = datetime.now()
        async with get_session() as session:
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            active_run = await self._dao.get_active_run(session, conversation_id, user_id)
            if active_run and active_run.lease_expire_time and active_run.lease_expire_time <= now:
                await self._dao.requeue_messages(session, active_run.run_id, now)
                active_run.status = "failed"
                active_run.update_time = now
                active_run.update_by = user_id
                active_run.update_by_name = operator_name
                active_run = None
            if active_run:
                raise AgentRunConflictError("当前会话已有运行中的任务")
            run_id = uuid4().hex
            session.add(ChatDataAgentInbox(
                conversation_id=conversation_id,
                user_id=user_id,
                run_id=run_id,
                message_id=run_id,
                record_type="run",
                content="",
                status="active",
                lease_expire_time=now + timedelta(seconds=RUN_LEASE_SECONDS),
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=user_id,
                create_by_name=operator_name,
                update_by=user_id,
                update_by_name=operator_name,
            ))
            await self._dao.adopt_pending_messages(session, conversation_id, user_id, run_id, now)
            return run_id

    async def heartbeat_run(self, run_id: str) -> bool:
        now = datetime.now()
        async with get_session() as session:
            run = await self._dao.get_active_run_by_id(session, run_id)
            if run is None:
                return False
            run.lease_expire_time = now + timedelta(seconds=RUN_LEASE_SECONDS)
            run.update_time = now
            return True

    async def enqueue_message(
        self,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        content: str,
    ) -> str:
        now = datetime.now()
        async with get_session() as session:
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            run = await self._dao.get_active_run(session, conversation_id, user_id)
            if run is None:
                raise AgentRunNotActiveError("当前会话没有运行中的任务")
            message_id = uuid4().hex
            session.add(ChatDataAgentInbox(
                conversation_id=conversation_id,
                user_id=user_id,
                run_id=run.run_id,
                message_id=message_id,
                record_type="message",
                content=content,
                status="pending",
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=user_id,
                create_by_name=operator_name,
                update_by=user_id,
                update_by_name=operator_name,
            ))
            return message_id

    async def claim_pending_messages(self, run_id: str) -> ClaimedAgentMessages:
        now = datetime.now()
        async with get_session() as session:
            rows = await self._dao.claim_messages(
                session,
                run_id,
                now,
                now + timedelta(seconds=MESSAGE_LEASE_SECONDS),
            )
            return ClaimedAgentMessages(run_id, [self._to_human_message(row) for row in rows])

    async def mark_consumed(self, run_id: str, message_ids: list[str]) -> None:
        if not message_ids:
            return
        now = datetime.now()
        async with get_session() as session:
            rows = await self._dao.get_claimed_messages(session, run_id, message_ids)
            for row in rows:
                row.status = "consumed"
                row.consumed_time = now
                row.lease_expire_time = None
                row.update_time = now

    async def claim_pending_or_finish_run(
        self,
        conversation_id: str,
        user_id: str,
        run_id: str,
        operator_name: str,
    ) -> ClaimedAgentMessages:
        now = datetime.now()
        async with get_session() as session:
            await self._dao.lock_thread(session, conversation_id, user_id)
            run = await self._dao.get_active_run(session, conversation_id, user_id)
            if run is None or run.run_id != run_id:
                return ClaimedAgentMessages(run_id, [])
            rows = await self._dao.claim_messages(
                session,
                run_id,
                now,
                now + timedelta(seconds=MESSAGE_LEASE_SECONDS),
            )
            if rows:
                return ClaimedAgentMessages(run_id, [self._to_human_message(row) for row in rows])
            run.status = "finished"
            run.update_time = now
            run.update_by = user_id
            run.update_by_name = operator_name
            return ClaimedAgentMessages(run_id, [])

    async def finish_run(self, run_id: str, status: str, user_id: str, operator_name: str) -> None:
        now = datetime.now()
        async with get_session() as session:
            run = await self._dao.get_active_run_by_id(session, run_id)
            if run:
                if status in {"failed", "cancelled"}:
                    await self._dao.requeue_messages(session, run_id, now)
                run.status = status
                run.update_time = now
                run.update_by = user_id
                run.update_by_name = operator_name

    @staticmethod
    def _to_human_message(row: ChatDataAgentInbox) -> HumanMessage:
        return HumanMessage(
            id=row.message_id,
            content=row.content,
            additional_kwargs={"timestamp": row.create_time.strftime("%Y-%m-%d %H:%M:%S")},
        )


agent_inbox_service = AgentInboxService()
