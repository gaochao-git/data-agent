"""子智能体生命周期事件 outbox 的统一投递。

所有需要主智能体感知的事件（审批、终态、审批恢复、未来的用户直聊）
都写入 ``chat_data_subagent_event``，由本模块统一负责：

- ``wake``：会话空闲时创建隐藏通知 Run 自动唤醒，忙时挂当前 Run；
- ``attach``：只挂当前活跃 Run，不唤醒空闲会话；
- ``silent``：仅保留审计，不投递。

投递状态按事件记录，失败留在 pending 由调度轮询重试，因此一个任务可以
先后产生多条通知，不再受「一任务一通知」限制。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from apps.chat_data_app.daos.agent_inbox_dao import agent_inbox_dao
from apps.chat_data_app.daos.subagent_event_dao import (
    DELIVERY_PENDING,
    subagent_event_dao,
)
from apps.chat_data_app.daos.subagent_task_dao import subagent_task_dao
from utils.database import get_session
from utils.logger import get_logger

from .agent_inbox_service import (
    AgentRunConflictError,
    AgentRunNotActiveError,
    agent_inbox_service,
    load_run_content,
)

logger = get_logger(__name__)

EVENT_WAITING_APPROVAL = "waiting_approval"
EVENT_TERMINAL = "terminal"
EVENT_RESUMED = "resumed"
EVENT_USER_MESSAGE = "user_message"

WAKE_POLICY_WAKE = "wake"
WAKE_POLICY_ATTACH = "attach"
WAKE_POLICY_SILENT = "silent"

TERMINAL_NOTIFICATION_EVENTS = frozenset({EVENT_TERMINAL})
APPROVAL_NOTIFICATION_EVENTS = frozenset({EVENT_WAITING_APPROVAL})


def terminal_event_id(task_id: str) -> str:
    """同一任务只保留一条终态事件；最终状态记录在 task_status 列。"""
    return f"terminal:{task_id}"


def lifecycle_event_id(event_type: str, task_id: str, now: datetime) -> str:
    """同一任务可能发生多次生命周期事件，用毫秒时间戳区分同一类型事件。"""
    return f"{event_type}:{task_id}:{int(now.timestamp() * 1000)}"


def event_message_id(event_id: str) -> str:
    """通知消息 ID 必须不超过 inbox.message_id 的 64 字符。"""
    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:40]
    return f"evt-{digest}"


class SubagentEventService:
    """事件写入、投递与重试；不直接理解任务业务状态。"""

    async def emit_in_session(self, session: Any, **kwargs: Any) -> Any:
        return await subagent_event_dao.create(session, **kwargs)

    async def emit(self, **kwargs: Any) -> Any:
        async with get_session() as session:
            row = await subagent_event_dao.create(session, **kwargs)
        return row

    async def dispatch_pending(self, *, limit: int = 10) -> int:
        async with get_session() as session:
            events = await subagent_event_dao.list_pending(session, limit=limit)
        delivered = 0
        for event in events:
            if await self.dispatch_event(event.event_id):
                delivered += 1
        return delivered

    async def dispatch_event(self, event_id: str) -> bool:
        async with get_session() as session:
            event = await subagent_event_dao.get_by_event_id(session, event_id)
        if event is None or event.delivery_status != DELIVERY_PENDING:
            return event is not None
        try:
            await self._deliver(event)
        except Exception as exc:
            logger.warning("子智能体事件投递失败，等待重试: event_id=%s", event_id, exc_info=True)
            async with get_session() as session:
                now = await subagent_task_dao.now(session)
                await subagent_event_dao.mark_failed(session, event_id, str(exc), now)
            return False
        async with get_session() as session:
            now = await subagent_task_dao.now(session)
            await subagent_event_dao.mark_delivered(session, event_id, now)
            if event.event_type == EVENT_TERMINAL:
                # 维持任务 notified_at 的既有口径；事件投递状态仍是权威。
                await subagent_task_dao.mark_consumed(session, event.task_id, now)
        return True

    async def cancel_pending_in_session(
        self,
        session: Any,
        task_id: str,
        event_types: frozenset[str],
        now: datetime,
    ) -> int:
        """取消未投递事件，并撤回已生成但尚未消费的隐藏通知消息。"""
        events = await subagent_event_dao.list_for_task(session, task_id, event_types)
        cancelled = await subagent_event_dao.cancel_pending(session, task_id, event_types, now)
        for event in events:
            message = await agent_inbox_dao.get_message_by_id(
                session,
                event.conversation_id,
                event.user_id,
                event_message_id(event.event_id),
            )
            self._cancel_pending_message(message, now)
        return cancelled

    async def cancel_pending(self, task_id: str, event_types: frozenset[str]) -> int:
        async with get_session() as session:
            now = await subagent_task_dao.now(session)
            return await self.cancel_pending_in_session(session, task_id, event_types, now)

    async def _deliver(self, event: Any) -> None:
        if event.wake_policy == WAKE_POLICY_SILENT:
            return
        meta = self._load_meta(event)
        message_id = event_message_id(event.event_id)
        operator_name = str(meta.get("operator_name") or event.user_id or "系统")
        async with get_session() as session:
            now = await subagent_task_dao.now(session)
            existing = await agent_inbox_dao.get_message_by_id(
                session,
                event.conversation_id,
                event.user_id,
                message_id,
            )
            if existing is not None and existing.record_status != "pending":
                return
            active = await agent_inbox_dao.get_active_run_snapshot(
                session,
                event.conversation_id,
                event.user_id,
            )
            if active is not None:
                await self._attach_or_create(session, event, existing, active.run_id, operator_name, now)
                return
            if event.wake_policy == WAKE_POLICY_ATTACH:
                # 不唤醒空闲会话；已挂到旧 Run 但未消费的消息也要撤回，避免串到下一轮。
                self._cancel_pending_message(existing, now)
                return
            if existing is None:
                await self._create_message(session, event, event.parent_run_id, operator_name, now)
        await self._start_wake_run(event, message_id, operator_name)

    async def _attach_or_create(
        self,
        session: Any,
        event: Any,
        existing: Any,
        run_id: str,
        operator_name: str,
        now: datetime,
    ) -> None:
        if existing is None:
            await self._create_message(session, event, run_id, operator_name, now)
        else:
            await subagent_task_dao.attach_pending_message(session, existing, run_id, now)

    async def _create_message(
        self,
        session: Any,
        event: Any,
        run_id: str,
        operator_name: str,
        now: datetime,
    ) -> None:
        await subagent_task_dao.create_notification_message(
            session,
            conversation_id=event.conversation_id,
            user_id=event.user_id,
            run_id=run_id,
            message_id=event_message_id(event.event_id),
            content=event.event_content,
            record_meta=self._notification_meta(event),
            now=now,
            operator_name=operator_name,
        )

    async def _start_wake_run(self, event: Any, message_id: str, operator_name: str) -> None:
        runtime_context = await self._runtime_context(event)
        try:
            await agent_inbox_service.start_run(
                event.conversation_id,
                event.user_id,
                operator_name,
                runtime_context,
                adopt_pending_messages=False,
                selected_message_id=message_id,
            )
        except AgentRunConflictError:
            # 启动瞬间有别的 Run 抢到会话：挂到活跃 Run，不丢事件。
            if not await self._attach_to_active(event, message_id):
                raise RuntimeError("通知 Run 启动冲突且未找到可挂载的活跃 Run") from None
        except AgentRunNotActiveError:
            logger.warning("子智能体事件无法创建通知 Run：会话不存在 event_id=%s", event.event_id)

    async def _attach_to_active(self, event: Any, message_id: str) -> bool:
        async with get_session() as session:
            now = await subagent_task_dao.now(session)
            active = await agent_inbox_dao.get_active_run_snapshot(
                session,
                event.conversation_id,
                event.user_id,
            )
            message = await agent_inbox_dao.get_message_by_id(
                session,
                event.conversation_id,
                event.user_id,
                message_id,
            )
            if message is not None and message.record_status != "pending":
                return True
            if active is not None and message is not None:
                await subagent_task_dao.attach_pending_message(session, message, active.run_id, now)
                return True
            return False

    @staticmethod
    def _cancel_pending_message(message: Any, now: datetime) -> None:
        if message is not None and message.record_status == "pending":
            message.record_status = "cancelled"
            message.delete_flag = 1
            message.update_time = now

    async def _runtime_context(self, event: Any) -> dict[str, Any]:
        event_meta = self._load_meta(event)
        parent_context: dict[str, Any] = {}
        if event.parent_run_id:
            async with get_session() as session:
                parent = await agent_inbox_dao.get_run_by_id(session, event.parent_run_id)
                if parent is not None:
                    loaded = load_run_content(parent.record_content).get("runtime_context")
                    if isinstance(loaded, dict):
                        parent_context = dict(loaded)
        return {
            **parent_context,
            "query": "",
            "disable_user_interaction": True,
            "subagent_notification_event_id": event.event_id,
            "subagent_notification_event_type": event.event_type,
            "subagent_notification_task_id": event.task_id,
            "subagent_notification_status": event.task_status,
            "subagent_notification_cancel_origin": event_meta.get("cancel_origin") or "",
        }

    @staticmethod
    def _load_meta(event: Any) -> dict[str, Any]:
        try:
            loaded = json.loads(event.record_meta or "{}")
        except (TypeError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _notification_meta(event: Any) -> dict[str, Any]:
        return {
            "hide_from_ui": True,
            "subagent_notification": {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "task_id": event.task_id,
                "status": event.task_status,
            },
        }


subagent_event_service = SubagentEventService()


def build_event_meta(**values: Any) -> dict[str, Any]:
    """忽略空值，保持 record_meta 精简。"""
    return {key: value for key, value in values.items() if value not in (None, "")}


__all__ = [
    "APPROVAL_NOTIFICATION_EVENTS",
    "EVENT_RESUMED",
    "EVENT_TERMINAL",
    "EVENT_USER_MESSAGE",
    "EVENT_WAITING_APPROVAL",
    "TERMINAL_NOTIFICATION_EVENTS",
    "WAKE_POLICY_ATTACH",
    "WAKE_POLICY_SILENT",
    "WAKE_POLICY_WAKE",
    "build_event_meta",
    "event_message_id",
    "lifecycle_event_id",
    "subagent_event_service",
    "terminal_event_id",
]
