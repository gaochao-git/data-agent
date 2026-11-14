"""后台子智能体任务账本、租约与完成通知。

任务行持久化在 ``chat_data_agent_inbox``（``record_type='task'``）：
- 进程内只保留 asyncio 执行句柄，任务状态以 MySQL 为唯一真值；
- worker 每 5 秒续租并写进度，取消请求先落 ``cancelling``，执行 worker 通过
  心跳观察后停止本地协程，因而跨 worker 也能取消；
- 任务终态后写一条隐藏的 ``next-step`` inbox 消息。父 Run 仍活跃时按现有
  steering 通道注入；会话空闲时创建一个 pending notification Run，由现有
  Run 调度器领取。通知消息 ID 由 task_id 派生，重复派发不会重复注入。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from apps.chat_data_app.daos.agent_inbox_dao import agent_inbox_dao
from apps.chat_data_app.daos.subagent_task_dao import (
    SubagentTaskDao,
    load_task_meta,
    subagent_task_dao,
)
from apps.chat_data_app.services.setting_service import get_runtime_setting
from apps.chat_data_app.sse_protocol import SSE_EVENT_SUBAGENT_TASK_UPDATED, format_sse
from utils.database import get_session
from utils.logger import get_logger
from utils.models import ChatDataAgentInbox

from ..contracts.subagent_task import (
    ACTIVE_TASK_STATUSES,
    TASK_HEARTBEAT_SECONDS,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_CANCELLING,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_INTERRUPTED,
    TASK_STATUS_LABELS,
    TASK_STATUS_LOST,
    TASK_STATUS_PENDING,
    TASK_STATUS_RUNNING,
    TASK_STATUS_TIMEOUT,
    TERMINAL_TASK_STATUSES,
    SubagentTaskCapacityError,
    SubagentTaskView,
    parse_task_status_filter,
    register_subagent_task_service,
)
from .agent_inbox_service import (
    AgentRunConflictError,
    AgentRunNotActiveError,
    agent_inbox_service,
    load_run_content,
)
from .agent_run_event_service import agent_run_event_service
from .subagent_task_event_service import (
    SubagentTaskStreamWriter,
    subagent_task_event_service,
)

logger = get_logger(__name__)

__all__ = [
    "ACTIVE_TASK_STATUSES",
    "NOTIFICATION_REPORT_PREVIEW_CHARS",
    "SubagentTaskCapacityError",
    "SubagentTaskRecord",
    "SubagentTaskService",
    "TASK_HEARTBEAT_SECONDS",
    "TASK_STATUS_CANCELLED",
    "TASK_STATUS_CANCELLING",
    "TASK_STATUS_COMPLETED",
    "TASK_STATUS_FAILED",
    "TASK_STATUS_INTERRUPTED",
    "TASK_STATUS_LABELS",
    "TASK_STATUS_LOST",
    "TASK_STATUS_PENDING",
    "TASK_STATUS_RUNNING",
    "TASK_STATUS_TIMEOUT",
    "TERMINAL_TASK_STATUSES",
    "notification_message_id",
    "parse_task_status_filter",
    "subagent_task_service",
]

NOTIFICATION_REPORT_PREVIEW_CHARS = 1600


@dataclass(frozen=True)
class SubagentTaskRecord:
    """任务账本行的只读视图。"""

    task_id: str
    conversation_id: str
    user_id: str
    parent_run_id: str
    owner_token: str | None
    status: str
    report: str
    result_meta: dict[str, Any]
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    notified_at: datetime | None
    lease_expire_time: datetime | None

    @classmethod
    def from_row(cls, row: ChatDataAgentInbox, *, include_content: bool = True) -> SubagentTaskRecord:
        meta = load_task_meta(row.record_meta)
        finished_at = _parse_datetime(meta.get("finished_at"))
        report = ""
        result_meta: dict[str, Any] = {}
        if include_content:
            report, result_meta = _decode_task_content(row.record_content)
        if not result_meta:
            # 兼容改造前的任务行：结果快照曾直接写在 record_meta 里。
            legacy = meta.get("result_meta")
            result_meta = dict(legacy) if isinstance(legacy, dict) else {}
        return cls(
            task_id=row.message_id,
            conversation_id=row.conversation_id,
            user_id=row.user_id,
            parent_run_id=row.run_id,
            owner_token=row.owner_token,
            status=row.record_status,
            report=report,
            result_meta=result_meta,
            meta=meta,
            created_at=row.create_time,
            updated_at=row.update_time,
            started_at=row.claimed_time,
            finished_at=finished_at,
            notified_at=row.consumed_time,
            lease_expire_time=row.lease_expire_time,
        )

    @property
    def subagent_type(self) -> str:
        return str(self.meta.get("subagent_type") or "")

    @property
    def description(self) -> str:
        return str(self.meta.get("description") or "")

    @property
    def label(self) -> str:
        return str(self.meta.get("label") or "") or self.description.splitlines()[0][:80]

    @property
    def parent_tool_call_id(self) -> str:
        return str(self.meta.get("parent_tool_call_id") or "")

    @property
    def timeout_seconds(self) -> int:
        try:
            return max(int(self.meta.get("timeout_seconds") or 0), 1)
        except (TypeError, ValueError):
            return 1

    @property
    def progress(self) -> dict[str, Any]:
        progress = self.meta.get("progress")
        if not isinstance(progress, dict):
            return {}
        snapshot = dict(progress)
        # 终态优先：老任务可能残留运行期快照（phase=running），读取时统一
        # 归一到当前终态，避免 API / 工具输出出现「已完成 + 阶段=running」并存。
        if self.status in TERMINAL_TASK_STATUSES:
            snapshot["phase"] = self.status
            snapshot["running_tools"] = 0
        return snapshot

    @property
    def error(self) -> str:
        return str(self.meta.get("error") or "")

    @property
    def report_path(self) -> str:
        """超长报告落盘后的隐藏文件路径；空表示正文仍以账本为准。"""
        return str(self.result_meta.get("report_path") or "")

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES

    def as_dict(self, *, include_report: bool = True) -> dict[str, Any]:
        """API / 工具共用结构；时间统一 ISO，避免前端重复解析格式。"""
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "conversation_id": self.conversation_id,
            "parent_run_id": self.parent_run_id,
            "parent_tool_call_id": self.parent_tool_call_id,
            "subagent_type": self.subagent_type,
            "label": self.label,
            "description": self.description,
            "status": self.status,
            "status_label": TASK_STATUS_LABELS.get(self.status, self.status),
            "progress": self.progress,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "notified_at": _iso(self.notified_at),
            "timeout_seconds": self.timeout_seconds,
        }
        if include_report:
            payload["report"] = self.report
            payload["report_path"] = self.report_path
            payload["error"] = self.error
            payload["result_meta"] = self.result_meta
        return payload


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _task_meta_text(meta: dict[str, Any]) -> str:
    return json.dumps(meta, ensure_ascii=False)


# 任务结果信封：report 与 result_meta 一起写 record_content（MEDIUMTEXT）。
# record_meta 只保留任务身份/进度/错误等小字段，避免过程快照撑爆 TEXT 列。
_TASK_CONTENT_MARKER = "__chatdata_task_content__"


def _encode_task_content(report: str, result_meta: dict[str, Any] | None) -> str:
    if not result_meta:
        return report
    return json.dumps(
        {_TASK_CONTENT_MARKER: 1, "report": report, "result_meta": result_meta},
        ensure_ascii=False,
    )


def _decode_task_content(content: str | None) -> tuple[str, dict[str, Any]]:
    text = content or ""
    if not text.startswith("{"):
        return text, {}
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text, {}
    if not isinstance(payload, dict) or payload.get(_TASK_CONTENT_MARKER) != 1:
        return text, {}
    report = payload.get("report")
    result_meta = payload.get("result_meta")
    return (
        report if isinstance(report, str) else "",
        dict(result_meta) if isinstance(result_meta, dict) else {},
    )


def _preview(text: str, max_chars: int = NOTIFICATION_REPORT_PREVIEW_CHARS) -> str:
    normalized = (text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars] + (
        f"\n…[报告共 {len(normalized)} 字符，当前只展示预览；"
        '完整内容请调用 task(action="check", task_id=...)；超长报告会落盘并返回 result_path]'
    )


def notification_message_id(task_id: str) -> str:
    return f"ntf-{task_id}"


class SubagentTaskService:
    """任务生命周期、取消、回收与通知派发。"""

    def __init__(
        self,
        dao: SubagentTaskDao = subagent_task_dao,
        inbox: Any = agent_inbox_service,
        event_service: Any = agent_run_event_service,
    ) -> None:
        self._dao = dao
        self._inbox = inbox
        self._event_service = event_service
        self._local_tasks: dict[str, asyncio.Task] = {}

    def register_local_task(self, task_id: str, task: asyncio.Task) -> None:
        self._local_tasks[task_id] = task

        def _forget_task(_task: asyncio.Task) -> None:
            self._local_tasks.pop(task_id, None)

        task.add_done_callback(_forget_task)

    def create_event_writer(self, task: SubagentTaskView) -> Any:
        """由 run 层构造实时事件 writer，避免 middleware 反向依赖 run。"""
        return SubagentTaskStreamWriter(
            subagent_task_event_service,
            task_id=task.task_id,
            conversation_id=task.conversation_id,
            user_id=task.user_id,
        )

    def cancel_local_task(self, task_id: str) -> bool:
        task = self._local_tasks.get(task_id)
        if task is None or task.done():
            return False
        task.cancel("subagent_task_cancelled")
        return True

    def has_local_task(self, task_id: str) -> bool:
        task = self._local_tasks.get(task_id)
        return task is not None and not task.done()

    async def shutdown_local_tasks(self) -> None:
        """进程退出时停止本地后台任务；执行体按 interrupted 收口，不伪装成功。"""
        tasks = [task for task in self._local_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel("runtime_shutdown")
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._local_tasks.clear()

    def snapshot_runtime(self, runtime: Any) -> SimpleNamespace:
        """后台任务只持有轻量状态快照，不长期引用父 Run 的 ToolRuntime。"""
        return SimpleNamespace(
            state=dict(getattr(runtime, "state", None) or {}),
            tool_call_id=getattr(runtime, "tool_call_id", None),
        )

    async def create_task(
        self,
        *,
        conversation_id: str,
        user_id: str,
        parent_run_id: str,
        subagent_type: str,
        description: str,
        label: str | None,
        parent_tool_call_id: str,
        parent_owner_token: str | None,
        timeout_seconds: int | None,
        operator_name: str,
    ) -> SubagentTaskRecord:
        max_active = int(await get_runtime_setting("CHAT_DATA_SUBAGENT_TASK_MAX_ACTIVE"))
        lease_seconds = int(await get_runtime_setting("CHAT_DATA_SUBAGENT_TASK_LEASE_SECONDS"))
        effective_timeout = int(timeout_seconds or await get_runtime_setting("CHAT_DATA_SUBAGENT_TASK_TIMEOUT_SECONDS"))
        task_id = uuid4().hex
        owner_token = uuid4().hex
        record: SubagentTaskRecord | None = None
        async with get_session() as session:
            now = await self._dao.now(session)
            thread = await agent_inbox_dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            active = await self._dao.count_active(session, conversation_id, user_id, ACTIVE_TASK_STATUSES)
            if active >= max_active:
                raise SubagentTaskCapacityError(
                    f"后台子智能体任务已达上限（{max_active} 个执行中），请等待任务完成或取消后再派发。"
                )
            meta = {
                "subagent_type": subagent_type,
                "description": description,
                "label": (label or description.splitlines()[0][:80]).strip(),
                "parent_tool_call_id": parent_tool_call_id,
                "parent_owner_token": str(parent_owner_token or ""),
                "operator_name": operator_name,
                "timeout_seconds": effective_timeout,
                "progress": {},
            }
            row = await self._dao.create(
                session,
                task_id=task_id,
                conversation_id=conversation_id,
                user_id=user_id,
                parent_run_id=parent_run_id,
                owner_token=owner_token,
                record_status=TASK_STATUS_PENDING,
                record_meta=meta,
                lease_expire_time=now + timedelta(seconds=lease_seconds),
                now=now,
                operator_name=operator_name,
            )
            logger.info(
                "后台子智能体任务已登记: task_id=%s conversation_id=%s type=%s",
                task_id,
                conversation_id,
                subagent_type,
            )
            record = SubagentTaskRecord.from_row(row)
        await self.publish_task_update(record)
        return record

    async def get_task(
        self,
        task_id: str,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> SubagentTaskRecord | None:
        async with get_session() as session:
            row = await self._dao.get(
                session,
                task_id,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            return SubagentTaskRecord.from_row(row) if row else None

    async def list_tasks(
        self,
        conversation_id: str,
        user_id: str,
        *,
        statuses: set[str] | None = None,
        limit: int = 50,
    ) -> list[SubagentTaskRecord]:
        async with get_session() as session:
            rows = await self._dao.list_for_conversation(
                session,
                conversation_id=conversation_id,
                user_id=user_id,
                statuses=statuses,
                limit=limit,
            )
            return [SubagentTaskRecord.from_row(row, include_content=False) for row in rows]

    async def publish_task_update(self, task: SubagentTaskRecord) -> None:
        """向当前活跃 Run 的 SSE 推送紧凑任务快照；失败只降级为前端轮询。"""
        parent_owner_token = str(task.meta.get("parent_owner_token") or "")
        if (
            task.parent_run_id
            and parent_owner_token
            and await self._append_task_event(task, task.parent_run_id, parent_owner_token)
        ):
            return
        active = await self._load_active_run_target(task)
        if active is None:
            return
        run_id, owner_token = active
        if run_id == task.parent_run_id and owner_token == parent_owner_token:
            return
        await self._append_task_event(task, run_id, owner_token)

    async def _load_active_run_target(self, task: SubagentTaskRecord) -> tuple[str, str] | None:
        try:
            async with get_session() as session:
                active = await agent_inbox_dao.get_active_run_snapshot(
                    session,
                    task.conversation_id,
                    task.user_id,
                )
        except Exception:
            logger.debug("读取任务实时推送目标 Run 失败: task_id=%s", task.task_id, exc_info=True)
            return None
        if active is None or not active.owner_token:
            return None
        return active.run_id, active.owner_token

    async def _append_task_event(self, task: SubagentTaskRecord, run_id: str, owner_token: str) -> bool:
        payload = format_sse(
            SSE_EVENT_SUBAGENT_TASK_UPDATED,
            {"task": task.as_dict(include_report=False)},
        )
        try:
            await self._event_service.append(
                run_id=run_id,
                conversation_id=task.conversation_id,
                user_id=task.user_id,
                event_id=f"{run_id}:subagent-task:{task.task_id}:{uuid4().hex}",
                payload=payload,
                owner_token=owner_token,
            )
        except Exception:
            logger.debug("后台任务实时事件推送失败，等待列表轮询兜底: task_id=%s", task.task_id, exc_info=True)
            return False
        return True

    async def start_task(self, task_id: str, owner_token: str) -> SubagentTaskRecord | None:
        lease_seconds = int(await get_runtime_setting("CHAT_DATA_SUBAGENT_TASK_LEASE_SECONDS"))
        record: SubagentTaskRecord | None = None
        async with get_session() as session:
            now = await self._dao.now(session)
            row = await self._dao.get(session, task_id, for_update=True)
            if row is None or row.owner_token != owner_token or row.record_status != TASK_STATUS_PENDING:
                return None
            meta = load_task_meta(row.record_meta)
            meta["started_at"] = now.isoformat()
            meta["progress"] = {"phase": "starting", "updated_at": now.isoformat()}
            row.record_status = TASK_STATUS_RUNNING
            row.claimed_time = now
            row.lease_expire_time = now + timedelta(seconds=lease_seconds)
            row.record_meta = _task_meta_text(meta)
            row.update_time = now
            record = SubagentTaskRecord.from_row(row)
        await self.publish_task_update(record)
        return record

    async def heartbeat_task(
        self,
        task_id: str,
        owner_token: str,
        progress: dict[str, Any],
    ) -> str | None:
        lease_seconds = int(await get_runtime_setting("CHAT_DATA_SUBAGENT_TASK_LEASE_SECONDS"))
        async with get_session() as session:
            now = await self._dao.now(session)
            row = await self._dao.get(session, task_id, for_update=True)
            if row is None or row.owner_token != owner_token:
                return None
            if row.record_status in TERMINAL_TASK_STATUSES or row.record_status == TASK_STATUS_CANCELLING:
                return row.record_status
            if row.record_status != TASK_STATUS_RUNNING:
                return row.record_status
            meta = load_task_meta(row.record_meta)
            meta["progress"] = {**progress, "updated_at": now.isoformat()}
            meta["last_heartbeat_at"] = now.isoformat()
            row.record_meta = _task_meta_text(meta)
            row.lease_expire_time = now + timedelta(seconds=lease_seconds)
            row.update_time = now
            record = SubagentTaskRecord.from_row(row)
        await self.publish_task_update(record)
        return record.status

    async def finish_task(
        self,
        task_id: str,
        owner_token: str,
        *,
        status: str,
        report: str,
        error: str = "",
        result_meta: dict[str, Any] | None = None,
    ) -> SubagentTaskRecord | None:
        async with get_session() as session:
            now = await self._dao.now(session)
            row = await self._dao.get(session, task_id, for_update=True)
            if row is None or row.owner_token != owner_token:
                return None
            if row.record_status in TERMINAL_TASK_STATUSES:
                return SubagentTaskRecord.from_row(row)
            final_status = TASK_STATUS_CANCELLED if row.record_status == TASK_STATUS_CANCELLING else status
            meta = load_task_meta(row.record_meta)
            # 结果快照（过程 trail / token 用量）动辄上百 KB，record_meta 是
            # TEXT 列（64KB），写进去会 DataError 1406 并回滚收口事务，任务
            # 只能等租约过期变 lost。改存 record_content（MEDIUMTEXT）的信封。
            meta.pop("result_meta", None)
            meta.update(
                {
                    "finished_at": now.isoformat(),
                    "phase": final_status,
                    "error": error,
                }
            )
            progress = meta.get("progress")
            progress = dict(progress) if isinstance(progress, dict) else {}
            progress.update(
                {
                    "phase": final_status,
                    "running_tools": 0,
                    "finished_at": now.isoformat(),
                }
            )
            meta["progress"] = progress
            row.record_status = final_status
            row.record_content = _encode_task_content(
                report or ("任务已取消。" if final_status == TASK_STATUS_CANCELLED else ""),
                result_meta,
            )
            row.record_meta = _task_meta_text(meta)
            row.owner_token = None
            row.lease_expire_time = None
            row.update_time = now
            logger.info("后台子智能体任务收口: task_id=%s status=%s", task_id, final_status)
            record = SubagentTaskRecord.from_row(row)
        await self.publish_task_update(record)
        return record

    async def cancel_task(
        self,
        conversation_id: str,
        user_id: str,
        task_id: str,
        operator_name: str,
    ) -> SubagentTaskRecord | None:
        cancelled_running = False
        async with get_session() as session:
            now = await self._dao.now(session)
            row = await self._dao.get(
                session,
                task_id,
                conversation_id=conversation_id,
                user_id=user_id,
                for_update=True,
            )
            if row is None:
                return None
            if row.record_status in TERMINAL_TASK_STATUSES:
                return SubagentTaskRecord.from_row(row)
            meta = load_task_meta(row.record_meta)
            meta["cancel_requested_at"] = now.isoformat()
            # 用户主动取消不再自动唤醒一个「任务已取消」Run：即时状态已经
            # 通过 API/SSE 返回给用户，自动通知只会制造一条无信息量的回复。
            # 标记 consumed 只抑制通知，不影响任务详情与取消收口。
            if row.consumed_time is None:
                row.consumed_time = now
            if row.record_status == TASK_STATUS_PENDING:
                meta["finished_at"] = now.isoformat()
                meta["phase"] = TASK_STATUS_CANCELLED
                progress = meta.get("progress")
                progress = dict(progress) if isinstance(progress, dict) else {}
                progress.update({"phase": TASK_STATUS_CANCELLED, "running_tools": 0})
                meta["progress"] = progress
                row.record_status = TASK_STATUS_CANCELLED
                row.record_content = "任务在开始执行前已取消。"
                row.owner_token = None
                row.lease_expire_time = None
            else:
                row.record_status = TASK_STATUS_CANCELLING
                cancelled_running = True
            row.record_meta = _task_meta_text(meta)
            row.update_time = now
            row.update_by = user_id
            row.update_by_name = operator_name
            record = SubagentTaskRecord.from_row(row)
        if cancelled_running:
            self.cancel_local_task(task_id)
        await self.publish_task_update(record)
        logger.info("后台子智能体取消请求: task_id=%s local=%s", task_id, cancelled_running)
        return record

    async def mark_viewed(self, record: SubagentTaskView) -> None:
        """模型已通过 check/wait 读到报告：阻止同一终态再发完成通知。"""
        if not record.is_terminal:
            return
        async with get_session() as session:
            now = await self._dao.now(session)
            row = await self._dao.get(
                session,
                record.task_id,
                conversation_id=record.conversation_id,
                user_id=record.user_id,
                for_update=True,
            )
            if row is None:
                return
            if row.consumed_time is None:
                row.consumed_time = now
            notification = await agent_inbox_dao.get_message_by_id(
                session,
                row.conversation_id,
                row.user_id,
                notification_message_id(row.message_id),
            )
            if notification is not None and notification.record_status == "pending":
                notification.record_status = "cancelled"
                notification.delete_flag = 1
                notification.update_time = now
            row.update_time = now

    async def reap_expired_tasks(self, *, limit: int = 20) -> int:
        reaped_ids: list[str] = []
        async with get_session() as session:
            now = await self._dao.now(session)
            rows = await self._dao.list_expired(session, set(ACTIVE_TASK_STATUSES), now, limit=limit)
            if not rows:
                return 0
            reaped = 0
            for row in rows:
                status, report = _expired_status_and_report(row.record_status)
                meta = load_task_meta(row.record_meta)
                meta.update(
                    {
                        "finished_at": now.isoformat(),
                        "phase": status,
                        "error": "执行节点租约过期",
                    }
                )
                progress = meta.get("progress")
                progress = dict(progress) if isinstance(progress, dict) else {}
                progress.update({"phase": status, "running_tools": 0})
                meta["progress"] = progress
                updated = await self._dao.update_any(
                    session,
                    row.message_id,
                    allowed_statuses=ACTIVE_TASK_STATUSES,
                    values={
                        "record_status": status,
                        "record_content": report,
                        "record_meta": _task_meta_text(meta),
                        "owner_token": None,
                        "lease_expire_time": None,
                        "update_time": now,
                        "update_by": "system",
                        "update_by_name": "system",
                    },
                )
                reaped += updated
                if updated:
                    reaped_ids.append(row.message_id)
                    logger.warning(
                        "后台子智能体任务租约过期回收: task_id=%s old_status=%s new_status=%s",
                        row.message_id,
                        row.record_status,
                        status,
                    )
        for task_id in reaped_ids:
            record = await self.get_task(task_id)
            if record is not None:
                await self.publish_task_update(record)
        return reaped

    async def dispatch_pending_notifications(self, *, limit: int = 10) -> int:
        async with get_session() as session:
            rows = await self._dao.list_pending_notifications(session, set(TERMINAL_TASK_STATUSES), limit=limit)
        dispatched = 0
        for row in rows:
            try:
                if await self._dispatch_task_notification(SubagentTaskRecord.from_row(row)):
                    dispatched += 1
            except Exception:
                logger.error("后台子智能体完成通知派发失败: task_id=%s", row.message_id, exc_info=True)
        return dispatched

    async def _dispatch_task_notification(self, task: SubagentTaskRecord) -> bool:
        notif_id = notification_message_id(task.task_id)
        active_run_id: str | None = None
        existing_status: str | None = None
        async with get_session() as session:
            now = await self._dao.now(session)
            existing = await agent_inbox_dao.get_message_by_id(
                session,
                task.conversation_id,
                task.user_id,
                notif_id,
            )
            existing_status = existing.record_status if existing else None
            active = await agent_inbox_dao.get_active_run_snapshot(
                session,
                task.conversation_id,
                task.user_id,
            )
            active_run_id = active.run_id if active else None
            if existing is not None and existing.record_status in {"claimed", "consumed"}:
                await self._mark_notified_in_session(session, task.task_id, now)
                return True
            content = self._notification_text(task)
            if active_run_id:
                if existing is None:
                    await self._dao.create_notification_message(
                        session,
                        conversation_id=task.conversation_id,
                        user_id=task.user_id,
                        run_id=active_run_id,
                        message_id=notif_id,
                        content=content,
                        record_meta=self._notification_meta(task),
                        now=now,
                        operator_name=self._operator_name(task),
                    )
                else:
                    await self._dao.attach_pending_message(session, existing, active_run_id, now)
                await self._mark_notified_in_session(session, task.task_id, now)
                return True
            if existing is None:
                await self._dao.create_notification_message(
                    session,
                    conversation_id=task.conversation_id,
                    user_id=task.user_id,
                    run_id=task.parent_run_id,
                    message_id=notif_id,
                    content=content,
                    record_meta=self._notification_meta(task),
                    now=now,
                    operator_name=self._operator_name(task),
                )
        if existing_status == "cancelled":
            await self.mark_viewed(task)
            return True
        return await self._start_notification_run(task)

    async def _start_notification_run(self, task: SubagentTaskRecord) -> bool:
        runtime_context = await self._notification_runtime_context(task)
        try:
            await self._inbox.start_run(
                task.conversation_id,
                task.user_id,
                self._operator_name(task),
                runtime_context,
                adopt_pending_messages=False,
                selected_message_id=notification_message_id(task.task_id),
            )
        except AgentRunConflictError:
            return await self._attach_notification_to_active_run(task)
        except AgentRunNotActiveError:
            logger.warning("后台任务通知 Run 无法创建：会话不存在 task_id=%s", task.task_id)
            await self._mark_notified(task)
            return True
        except Exception:
            logger.error("后台任务通知 Run 创建失败，下轮重试: task_id=%s", task.task_id, exc_info=True)
            return False
        await self._mark_notified(task)
        logger.info("后台任务完成通知 Run 已创建: task_id=%s", task.task_id)
        return True

    async def _attach_notification_to_active_run(self, task: SubagentTaskRecord) -> bool:
        async with get_session() as session:
            now = await self._dao.now(session)
            active = await agent_inbox_dao.get_active_run_snapshot(
                session,
                task.conversation_id,
                task.user_id,
            )
            message = await agent_inbox_dao.get_message_by_id(
                session,
                task.conversation_id,
                task.user_id,
                notification_message_id(task.task_id),
            )
            if active is not None and message is not None:
                await self._dao.attach_pending_message(session, message, active.run_id, now)
            await self._mark_notified_in_session(session, task.task_id, now)
        return True

    async def _notification_runtime_context(self, task: SubagentTaskRecord) -> dict[str, Any]:
        parent_context: dict[str, Any] = {}
        async with get_session() as session:
            parent = await agent_inbox_dao.get_run_by_id(session, task.parent_run_id)
            if parent is not None:
                loaded = load_run_content(parent.record_content).get("runtime_context")
                if isinstance(loaded, dict):
                    parent_context = dict(loaded)
        return {
            **parent_context,
            "query": "",
            "disable_user_interaction": True,
            "subagent_notification_task_id": task.task_id,
            "subagent_notification_status": task.status,
        }

    async def _mark_notified(self, task: SubagentTaskRecord) -> None:
        async with get_session() as session:
            now = await self._dao.now(session)
            await self._mark_notified_in_session(session, task.task_id, now)

    async def _mark_notified_in_session(self, session: Any, task_id: str, now: datetime) -> None:
        row = await self._dao.get(session, task_id, for_update=True)
        if row is None or row.consumed_time is not None:
            return
        row.consumed_time = now
        row.update_time = now

    @staticmethod
    def _operator_name(task: SubagentTaskRecord) -> str:
        return str(task.meta.get("operator_name") or task.user_id or "系统")

    @staticmethod
    def _notification_meta(task: SubagentTaskRecord) -> dict[str, Any]:
        return {
            "hide_from_ui": True,
            "subagent_notification": {
                "task_id": task.task_id,
                "subagent_type": task.subagent_type,
                "status": task.status,
            },
        }

    @staticmethod
    def _notification_text(task: SubagentTaskRecord) -> str:
        outcome = TASK_STATUS_LABELS.get(task.status, task.status)
        report = _preview(task.report or task.error or "无报告正文")
        report_path = task.report_path
        path_line = (
            f"完整报告已落盘：result_path={report_path}（{len(task.report)} 字符，可用 file_read 读取）。\n"
            if report_path
            else ""
        )
        return (
            "## 后台子智能体完成通知（内部消息）\n"
            f"task_id={task.task_id}\n"
            f"类型={task.subagent_type}；状态={outcome}\n"
            f"任务={task.label}\n\n"
            f"报告预览：\n{report}\n\n{path_line}"
            "请根据报告向用户简要汇报结论（不要重复执行任务）；"
            f'需要完整报告或最新状态时调用 task(action="check", task_id="{task.task_id}")；'
            + (
                "完整报告已落盘，请优先用 file_read 读取 result_path，不要只凭预览下结论。"
                if report_path
                else 'task(action="check") 会返回完整正文，不要只凭预览下结论。'
            )
            + "如报告中提到产物文件，用 present_artifacts 展示。"
        )


def _expired_status_and_report(current_status: str) -> tuple[str, str]:
    if current_status == TASK_STATUS_CANCELLING:
        return TASK_STATUS_CANCELLED, "取消请求已生效（执行节点失联）。"
    if current_status == TASK_STATUS_PENDING:
        return TASK_STATUS_LOST, "任务未能在执行节点启动，已标记为执行节点失联。"
    return TASK_STATUS_LOST, "执行节点心跳租约过期，任务已中断；可重新派发。"


subagent_task_service = SubagentTaskService()

register_subagent_task_service(subagent_task_service)
