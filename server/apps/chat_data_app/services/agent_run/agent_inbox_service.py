"""跨进程 Agent 运行协调与运行中消息收件箱。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage

from utils.database import get_session
from utils.models import ChatDataAgentInbox
from ...daos.agent_inbox_dao import AgentInboxDao, agent_inbox_dao

MESSAGE_LEASE_SECONDS = 60
RUN_LEASE_SECONDS = 120

# next-step 在下一次模型调用前注入；next-turn 每个新轮次按 FIFO 领取一条。
INBOX_QUEUE_TYPES = {"next-step", "next-turn"}


class AgentRunConflictError(RuntimeError):
    pass


class AgentRunNotActiveError(RuntimeError):
    pass


class SteerMessageNotFoundError(RuntimeError):
    pass


class SteerMessageNotPendingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaimedAgentMessages:
    run_id: str
    messages: list[HumanMessage]


class AgentInboxService:
    def __init__(self, dao: AgentInboxDao = agent_inbox_dao):
        self._dao = dao

    async def start_run(
        self,
        conversation_id: str,
        user_id: str,
        operator_name: str,
    ) -> tuple[str, list[HumanMessage]]:
        """开启新运行，返回 (run_id, 遗留排队消息)。

        上一次运行遗留的排队消息（未消费的立即插入/下一轮消息）由新运行接管，
        并作为新一轮对话的首批输入；它们在本事务内被领取，避免被中间件重复注入。
        """
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
            rows = await self._claim_step_and_turn(session, run_id, now)
            return run_id, [self._to_human_message(row) for row in rows]

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
        queue_type: str = "next-turn",
    ) -> str:
        if queue_type not in INBOX_QUEUE_TYPES:
            raise ValueError(f"非法队列类型: {queue_type}")
        normalized_content = (content or "").strip()
        if not normalized_content:
            raise ValueError("消息内容不能为空")
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
                queue_type=queue_type,
                content=normalized_content,
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

    async def claim_pending_messages(
        self,
        run_id: str,
        *,
        queue_type: str | None = "next-step",
    ) -> ClaimedAgentMessages:
        now = datetime.now()
        async with get_session() as session:
            rows = await self._dao.claim_messages(
                session,
                run_id,
                now,
                now + timedelta(seconds=MESSAGE_LEASE_SECONDS),
                queue_type=queue_type,
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
        """轮次收尾：领取全部 next-step 和一条 next-turn；无消息时结束运行。

        next-step 是赶不上上一模型边界的即时引导；next-turn 严格按 FIFO 每轮一条，
        避免多条普通用户消息被合并成同一次模型调用。
        """
        now = datetime.now()
        async with get_session() as session:
            await self._dao.lock_thread(session, conversation_id, user_id)
            run = await self._dao.get_active_run(session, conversation_id, user_id)
            if run is None or run.run_id != run_id:
                return ClaimedAgentMessages(run_id, [])
            rows = await self._claim_step_and_turn(session, run_id, now)
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

    async def update_pending_message(
        self,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        message_id: str,
        *,
        content: str | None = None,
        queue_type: str | None = None,
    ) -> dict[str, str]:
        """编辑仍在等待注入的消息内容或切换队列类型；已被领取的消息不可修改。"""
        if content is not None and not content.strip():
            raise ValueError("消息内容不能为空")
        if queue_type is not None and queue_type not in INBOX_QUEUE_TYPES:
            raise ValueError(f"非法队列类型: {queue_type}")
        now = datetime.now()
        async with get_session() as session:
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            run = await self._dao.get_active_run(session, conversation_id, user_id)
            if run is None:
                raise AgentRunNotActiveError("当前会话没有运行中的任务")
            message = await self._dao.get_message_by_id(session, run.run_id, message_id)
            if message is None:
                raise SteerMessageNotFoundError("队列消息不存在")
            if message.status != "pending":
                raise SteerMessageNotPendingError("消息已进入注入流程，无法修改")
            if content is not None:
                message.content = content.strip()
            if queue_type is not None:
                message.queue_type = queue_type
            message.update_time = now
            message.update_by = user_id
            message.update_by_name = operator_name
            return {
                "message_id": message.message_id,
                "content": message.content,
                "queue_type": message.queue_type,
            }

    async def cancel_pending_message(
        self,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        message_id: str,
    ) -> None:
        """删除仍在等待注入的消息；已被领取的消息不可删除。"""
        now = datetime.now()
        async with get_session() as session:
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            run = await self._dao.get_active_run(session, conversation_id, user_id)
            if run is None:
                raise AgentRunNotActiveError("当前会话没有运行中的任务")
            message = await self._dao.get_message_by_id(session, run.run_id, message_id)
            if message is None:
                raise SteerMessageNotFoundError("队列消息不存在")
            if message.status != "pending":
                raise SteerMessageNotPendingError("消息已进入注入流程，无法删除")
            message.status = "cancelled"
            message.delete_flag = 1
            message.update_time = now
            message.update_by = user_id
            message.update_by_name = operator_name

    async def list_message_states(
        self,
        conversation_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """读取当前 active run 的完整队列状态，供刷新和断线重连恢复。"""
        async with get_session() as session:
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            active_run = await self._dao.get_active_run(session, conversation_id, user_id)
            latest_run = active_run or await self._dao.get_latest_run(session, conversation_id, user_id)
            rows = await self._dao.list_run_messages(session, latest_run.run_id) if latest_run else []
            return {
                "has_active_run": active_run is not None,
                "messages": [
                    {
                        "message_id": row.message_id,
                        "content": row.content,
                        "queue_type": row.queue_type,
                        "status": row.status,
                        # ISO 格式：前端 new Date() 跨浏览器（含 Safari）均可解析
                        "create_time": row.create_time.isoformat(),
                    }
                    for row in rows
                ],
            }

    async def _claim_step_and_turn(self, session, run_id: str, now: datetime) -> list[ChatDataAgentInbox]:
        """领取一个模型轮次的输入：全部 next-step，加一条 FIFO next-turn。"""
        lease_expire_time = now + timedelta(seconds=MESSAGE_LEASE_SECONDS)
        step_rows = await self._dao.claim_messages(
            session,
            run_id,
            now,
            lease_expire_time,
            queue_type="next-step",
        )
        turn_rows = await self._dao.claim_messages(
            session,
            run_id,
            now,
            lease_expire_time,
            queue_type="next-turn",
            limit=1,
        )
        return [*step_rows, *turn_rows]

    @staticmethod
    def _to_human_message(row: ChatDataAgentInbox) -> HumanMessage:
        return HumanMessage(
            id=row.message_id,
            content=row.content,
            additional_kwargs={"timestamp": row.create_time.strftime("%Y-%m-%d %H:%M:%S")},
        )


agent_inbox_service = AgentInboxService()
