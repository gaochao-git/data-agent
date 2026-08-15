"""跨进程 Agent 运行协调与运行中消息收件箱。"""

from __future__ import annotations

import json
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


class AgentQuestionNotFoundError(RuntimeError):
    pass


class AgentQuestionNotWaitingError(RuntimeError):
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
        runtime_context: dict[str, Any] | None = None,
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
            active_run = await self._reap_expired_run(
                session,
                active_run,
                now,
                user_id,
                operator_name,
            )
            if active_run:
                raise AgentRunConflictError("当前会话已有运行中的任务")
            run_id = uuid4().hex
            session.add(ChatDataAgentInbox(
                conversation_id=conversation_id,
                user_id=user_id,
                run_id=run_id,
                message_id=run_id,
                record_type="run",
                content=json.dumps({"runtime_context": runtime_context or {}}, ensure_ascii=False),
                status="pending",
                lease_expire_time=None,
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

    async def prepare_pending_run(self, run_id: str) -> dict[str, Any] | None:
        """领取一个 pending Run，并返回重启后重建执行所需的上下文。"""
        now = datetime.now()
        async with get_session() as session:
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None or run.status != "pending":
                return None
            thread = await self._dao.lock_thread(session, run.conversation_id, run.user_id)
            if thread is None:
                return None
            active = await self._dao.get_active_run(session, run.conversation_id, run.user_id)
            if active is not None and active.run_id != run_id:
                return None
            run.status = "active"
            run.lease_expire_time = now + timedelta(seconds=RUN_LEASE_SECONDS)
            run.update_time = now
            content = self._load_run_content(run.content)
            rows = await self._dao.list_claimed_messages(session, run_id)
            resume_question = content.get("resuming_question")
            pending_answer = content.get("pending_answer")
            return {
                "run_id": run_id,
                "conversation_id": run.conversation_id,
                "user_id": run.user_id,
                "operator_name": content.get("runtime_context", {}).get("operator_name") or run.user_id,
                "runtime_context": content.get("runtime_context") or {},
                "messages": [self._to_human_message(row) for row in rows],
                "resume_answer": pending_answer if isinstance(pending_answer, dict) else None,
                "resume_turn_id": (
                    str(resume_question.get("turn_id") or "")
                    if isinstance(resume_question, dict)
                    else None
                ),
            }

    async def heartbeat_run(self, run_id: str) -> str:
        now = datetime.now()
        async with get_session() as session:
            run = await self._dao.get_active_run_by_id(session, run_id)
            if run is None:
                return "inactive"
            if run.status == "cancelling":
                return "cancelling"
            run.lease_expire_time = now + timedelta(seconds=RUN_LEASE_SECONDS)
            run.update_time = now
            return "active"

    async def mark_waiting_for_user(
        self,
        run_id: str,
        question: dict[str, Any],
        user_id: str,
        operator_name: str,
    ) -> None:
        now = datetime.now()
        async with get_session() as session:
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None or run.status != "active":
                raise AgentRunNotActiveError("当前运行已结束")
            content = self._load_run_content(run.content)
            content["pending_question"] = question
            content.pop("resuming_question", None)
            content.pop("pending_answer", None)
            run.content = json.dumps(content, ensure_ascii=False)
            run.status = "waiting_user"
            run.lease_expire_time = None
            run.update_time = now
            run.update_by = user_id
            run.update_by_name = operator_name

    async def get_pending_question(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        now = datetime.now()
        async with get_session() as session:
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            run = await self._dao.get_active_run(session, conversation_id, user_id)
            run = await self._reap_expired_run(session, run, now, user_id, user_id)
            if run is None or run.status != "waiting_user":
                return None
            return self._question_response(run)

    async def claim_question_answer(
        self,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        question_id: str,
        answer: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        now = datetime.now()
        async with get_session() as session:
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentQuestionNotFoundError("待回答问题不存在")
            run = await self._dao.get_active_run(session, conversation_id, user_id)
            if run is None:
                raise AgentQuestionNotFoundError("待回答问题不存在")
            if run.status != "waiting_user":
                raise AgentQuestionNotWaitingError("问题已回答或运行已恢复")
            content = self._load_run_content(run.content)
            question = content.get("pending_question") or {}
            if str(question.get("question_id") or "") != question_id:
                raise AgentQuestionNotFoundError("待回答问题不存在")
            self._validate_question_answer(question, answer)
            # Keep the question separately while the checkpoint is being
            # resumed. It must not be exposed as pending after the answer has
            # been claimed, otherwise a later stream failure can resurrect an
            # interrupt that LangGraph has already consumed.
            content["resuming_question"] = content.pop("pending_question")
            content["pending_answer"] = answer
            content["resume_started"] = False
            run.content = json.dumps(content, ensure_ascii=False)
            # The answer is durable before dispatch. Runtime will atomically
            # claim the pending run, including after a worker restart.
            run.status = "pending"
            run.lease_expire_time = None
            run.update_time = now
            run.update_by = user_id
            run.update_by_name = operator_name
            return run.run_id, content.get("runtime_context") or {}

    async def mark_question_resume_started(self, run_id: str) -> None:
        """Record that LangGraph has started processing the claimed answer."""
        now = datetime.now()
        async with get_session() as session:
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None or run.status not in {"active", "pending"}:
                return
            content = self._load_run_content(run.content)
            if not isinstance(content.get("resuming_question"), dict):
                return
            content["resume_started"] = True
            run.content = json.dumps(content, ensure_ascii=False)
            run.update_time = now

    async def restore_question_answer(
        self,
        run_id: str,
        user_id: str,
        operator_name: str,
    ) -> None:
        """Roll back a claim when the resume stream could not be created."""
        now = datetime.now()
        async with get_session() as session:
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None or run.status not in {"active", "pending"}:
                return
            content = self._load_run_content(run.content)
            question = content.pop("resuming_question", None)
            if not isinstance(question, dict) or "pending_answer" not in content:
                return
            content.pop("pending_answer", None)
            content.pop("resume_started", None)
            content["pending_question"] = question
            run.content = json.dumps(content, ensure_ascii=False)
            run.status = "waiting_user"
            run.lease_expire_time = None
            run.update_time = now
            run.update_by = user_id
            run.update_by_name = operator_name

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
            run = await self._reap_expired_run(session, run, now, user_id, operator_name)
            if run is None or run.status not in {"pending", "active"}:
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
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None or run.status != "active":
                return ClaimedAgentMessages(run_id, [])
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
            if run.status == "cancelling":
                await self._cancel_run(session, run, now, user_id, operator_name)
                return ClaimedAgentMessages(run_id, [])
            if run.status != "active":
                return ClaimedAgentMessages(run_id, [])
            rows = await self._claim_step_and_turn(session, run_id, now)
            if rows:
                return ClaimedAgentMessages(run_id, [self._to_human_message(row) for row in rows])
            content = self._load_run_content(run.content)
            content.pop("pending_question", None)
            content.pop("resuming_question", None)
            content.pop("pending_answer", None)
            content.pop("resume_started", None)
            run.content = json.dumps(content, ensure_ascii=False)
            run.status = "finished"
            run.update_time = now
            run.update_by = user_id
            run.update_by_name = operator_name
            return ClaimedAgentMessages(run_id, [])

    async def finish_run(self, run_id: str, status: str, user_id: str, operator_name: str) -> None:
        now = datetime.now()
        async with get_session() as session:
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run and run.status in {"pending", "active", "cancelling"}:
                if status in {"failed", "cancelled"}:
                    await self._dao.requeue_messages(session, run_id, now)
                content = self._load_run_content(run.content)
                self._restore_unstarted_question(content)
                run.content = json.dumps(content, ensure_ascii=False)
                # A claimed answer is already in the resume lifecycle. If the
                # resumed stream fails, do not expose the old question again;
                # the checkpoint may already have consumed its interrupt.
                run.status = "waiting_user" if content.get("pending_question") else status
                run.lease_expire_time = None
                run.update_time = now
                run.update_by = user_id
                run.update_by_name = operator_name

    async def cancel_active_run(
        self,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        run_id: str,
    ) -> bool:
        """显式停止当前执行；保留 pending，并把 claimed 恢复为 pending 供下一轮接管。"""
        now = datetime.now()
        async with get_session() as session:
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            run = await self._dao.get_active_run(session, conversation_id, user_id)
            if run is None or run.run_id != run_id or run.status not in {"pending", "active"}:
                return False
            if run.status == "pending":
                await self._cancel_run(session, run, now, user_id, operator_name)
                return True
            run.status = "cancelling"
            run.lease_expire_time = now + timedelta(seconds=5)
            run.update_time = now
            run.update_by = user_id
            run.update_by_name = operator_name
            return True

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
        now = datetime.now()
        async with get_session() as session:
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            active_run = await self._dao.get_active_run(session, conversation_id, user_id)
            active_run = await self._reap_expired_run(
                session,
                active_run,
                now,
                user_id,
                user_id,
            )
            latest_run = active_run or await self._dao.get_latest_run(session, conversation_id, user_id)
            rows = await self._dao.list_run_messages(session, latest_run.run_id) if latest_run else []
            return {
                "has_active_run": active_run is not None,
                "run_id": latest_run.run_id if latest_run else None,
                "run_status": latest_run.status if latest_run else None,
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

    async def get_run_snapshot(
        self,
        conversation_id: str,
        user_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        """读取指定 Run，并校验它属于当前用户和会话。"""
        async with get_session() as session:
            run = await self._dao.get_run_by_id(session, run_id)
            if run is None or run.conversation_id != conversation_id or run.user_id != user_id:
                return None
            return {
                "run_id": run.run_id,
                "conversation_id": run.conversation_id,
                "user_id": run.user_id,
                "status": run.status,
            }

    async def _cancel_run(
        self,
        session,
        run: ChatDataAgentInbox,
        now: datetime,
        user_id: str,
        operator_name: str,
    ) -> None:
        """在持有 Run 行锁时完成取消收口。"""
        await self._dao.requeue_messages(session, run.run_id, now)
        content = self._load_run_content(run.content)
        self._restore_unstarted_question(content)
        run.content = json.dumps(content, ensure_ascii=False)
        run.status = "waiting_user" if content.get("pending_question") else "cancelled"
        run.lease_expire_time = None
        run.update_time = now
        run.update_by = user_id
        run.update_by_name = operator_name

    async def _reap_expired_run(
        self,
        session,
        run: ChatDataAgentInbox | None,
        now: datetime,
        user_id: str,
        operator_name: str,
    ) -> ChatDataAgentInbox | None:
        """原子收口失去心跳的运行；未消费消息留给下一次正常 completion。"""
        if run is None or run.status not in {"active", "cancelling"}:
            return run
        if run.lease_expire_time is None or run.lease_expire_time > now:
            return run
        await self._dao.requeue_messages(session, run.run_id, now)
        content = self._load_run_content(run.content)
        self._restore_unstarted_question(content)
        run.content = json.dumps(content, ensure_ascii=False)
        run.status = "waiting_user" if content.get("pending_question") else (
            "cancelled" if run.status == "cancelling" else "failed"
        )
        run.lease_expire_time = None
        run.update_time = now
        run.update_by = user_id
        run.update_by_name = operator_name
        return run if run.status == "waiting_user" else None

    @staticmethod
    def _load_run_content(content: str) -> dict[str, Any]:
        try:
            value = json.loads(content or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _question_response(self, run: ChatDataAgentInbox) -> dict[str, Any] | None:
        question = self._load_run_content(run.content).get("pending_question")
        if not isinstance(question, dict):
            return None
        return {"run_id": run.run_id, **question}

    @staticmethod
    def _restore_unstarted_question(content: dict[str, Any]) -> None:
        question = content.get("resuming_question")
        if not isinstance(question, dict) or content.get("resume_started") is True:
            return
        content["pending_question"] = content.pop("resuming_question")
        content.pop("pending_answer", None)
        content.pop("resume_started", None)

    @staticmethod
    def _validate_question_answer(question: dict[str, Any], answer: dict[str, Any]) -> None:
        if answer.get("cancelled") is True:
            return
        questions = question.get("questions") or []
        submitted = answer.get("answers") if isinstance(answer, dict) else None
        if not isinstance(submitted, list):
            raise ValueError("answers 必须是数组")
        submitted_by_id = {str(item.get("id") or ""): item for item in submitted if isinstance(item, dict)}
        if len(submitted_by_id) != len(submitted):
            raise ValueError("回答的问题 ID 不能重复且必须是对象")
        if set(submitted_by_id) != {str(item.get("id") or "") for item in questions}:
            raise ValueError("回答的问题 ID 与待回答问题不一致")
        for item in questions:
            current = submitted_by_id[str(item.get("id") or "")]
            if current.get("skipped") is True:
                continue
            selected = current.get("selected") or []
            custom = str(current.get("custom") or "").strip()
            if not isinstance(selected, list) or not all(isinstance(value, str) for value in selected):
                raise ValueError("selected 必须是字符串数组")
            allowed = {str(option.get("label") or "") for option in item.get("options") or []}
            if any(value not in allowed for value in selected):
                raise ValueError("回答包含无效选项")
            if not item.get("multi_select") and len(selected) > 1:
                raise ValueError("单选问题只能选择一个选项")
            if not selected and not custom:
                raise ValueError("每个问题都必须选择选项或填写自定义回答")

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
