"""后台子智能体任务账本、租约与完成通知。

任务行持久化在 ``chat_data_agent_inbox``（``record_type='task'``）：
- 进程内只保留 asyncio 执行句柄，任务状态以 MySQL 为唯一真值；
- worker 每 5 秒续租并写进度，取消请求先落 ``cancelling``，执行 worker 通过
  心跳观察后停止本地协程，因而跨 worker 也能取消；
- 生命周期变化（终态、审批、审批恢复、未来用户直聊）写入
  ``chat_data_subagent_event`` outbox；统一 dispatcher 按 wake/attach/silent
  策略投递，事件 ID 与隐藏消息 ID 都幂等，重复派发不会重复注入。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal
from uuid import uuid4

from apps.chat_data_app.daos.agent_inbox_dao import agent_inbox_dao
from apps.chat_data_app.daos.subagent_session_dao import subagent_session_dao
from apps.chat_data_app.daos.subagent_task_dao import (
    SubagentTaskDao,
    load_task_meta,
    subagent_task_dao,
)
from apps.chat_data_app.services.setting_service import get_runtime_setting
from apps.chat_data_app.sse_protocol import SSE_EVENT_SUBAGENT_TASK_UPDATED, format_sse
from utils.database import get_session
from utils.logger import get_logger
from utils.models import ChatDataAgentInbox, ChatDataSubagentSession

from ..contracts.subagent_task import (
    ACTIVE_TASK_STATUSES,
    CANCEL_ORIGIN_AGENT,
    CANCEL_ORIGIN_USER,
    EXECUTING_TASK_STATUSES,
    SESSION_STATUS_CLOSED,
    SESSION_STATUS_OPEN,
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
    WAITING_APPROVAL_TASK_STATUSES,
    SubagentSessionActiveError,
    SubagentSessionClosedError,
    SubagentSessionState,
    SubagentTaskCapacityError,
    SubagentTaskLeaseLostError,
    SubagentTaskView,
    parse_task_status_filter,
)
from .agent_inbox_service import (
    AgentRunLeaseLostError,
    AgentRunNotActiveError,
    agent_inbox_service,
)
from .agent_run_event_service import agent_run_event_service
from .subagent_event_service import (
    APPROVAL_NOTIFICATION_EVENTS,
    EVENT_RESUMED,
    EVENT_TERMINAL,
    EVENT_USER_MESSAGE,
    EVENT_WAITING_APPROVAL,
    WAKE_POLICY_ATTACH,
    WAKE_POLICY_WAKE,
    build_event_meta,
    event_message_id,
    lifecycle_event_id,
    subagent_event_service,
    terminal_event_id,
)
from .subagent_notification_text import (
    NOTIFICATION_REPORT_PREVIEW_CHARS,
    terminal_notification_text,
)
from .subagent_notification_text import (
    resumed_notification_text as _resumed_notification_text,
)
from .subagent_notification_text import (
    suspension_notification_text as _suspension_notification_text,
)
from .subagent_notification_text import (
    user_message_notification_text as _user_message_notification_text,
)
from .subagent_recovery import (
    MAX_TASK_RECOVERY_ATTEMPTS,
    TASK_RECOVERY_RUNNER_WAIT_SECONDS,
    defer_or_expire_missing_runner,
    execution_attempt,
    recovery_candidate,
    recovery_count,
    recovery_runtime,
)
from .subagent_task_event_service import (
    SubagentTaskStreamWriter,
    subagent_task_event_service,
)

logger = get_logger(__name__)

# 任务卡推送失败后的静默期：任务过程流与前端 3.5s 列表轮询各自兜底，
# 没必要每个心跳（5s）都重试一次并刷一行日志。
_TASK_PUSH_DEGRADED_SECONDS = 30.0

# owner 失配是可预期的失效 token，和普通网络/Redis 失败分开处理。
_PUSH_RESULT_OK: Literal["ok"] = "ok"
_PUSH_RESULT_LEASE_LOST: Literal["lease_lost"] = "lease_lost"
_PUSH_RESULT_FAILED: Literal["failed"] = "failed"

__all__ = [
    "ACTIVE_TASK_STATUSES",
    "MAX_TASK_RECOVERY_ATTEMPTS",
    "NOTIFICATION_REPORT_PREVIEW_CHARS",
    "SubagentTaskCapacityError",
    "SubagentTaskRecord",
    "SubagentTaskService",
    "TASK_HEARTBEAT_SECONDS",
    "TASK_RECOVERY_RUNNER_WAIT_SECONDS",
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
        explicit = str(self.meta.get("label") or "")
        return explicit or next(iter(self.description.splitlines()), "")[:80]

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
    def execution_mode(self) -> str:
        """sync=前台同步等待；background=后台独立执行。旧任务默认按后台处理。"""
        mode = str(self.meta.get("execution_mode") or "").strip()
        return mode if mode in {"sync", "background"} else "background"

    @property
    def notify_on_finish(self) -> bool:
        """同步任务由当前轮直接返回结果，不生成完成通知；旧任务默认通知。"""
        value = self.meta.get("notify_on_finish")
        return True if value is None else bool(value)

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
            # 「正在压缩」是纯运行态标记：异常中断可能收不到 completed，
            # 终态统一剥掉，避免历史详情/工具输出里残留一个永不结束的状态。
            snapshot.pop("compressing", None)
        return snapshot

    @property
    def error(self) -> str:
        return str(self.meta.get("error") or "")

    @property
    def report_path(self) -> str:
        """超长报告落盘后的隐藏文件路径；空表示正文仍以账本为准。"""
        return str(self.result_meta.get("report_path") or "")

    @property
    def parent_task_id(self) -> str:
        """上一轮任务 ID；空表示这是子会话首轮。

        注意：它只表达“任务链上一轮”，不表达“父智能体”。树形派生的父子
        关系见 ``parent_agent_id``。
        """
        return str(self.meta.get("parent_task_id") or "")

    @property
    def agent_session_id(self) -> str:
        """子智能体的记忆容器 ID，同一智能体的所有轮次共享。

        新数据写 ``agent_session_id``；旧数据没有该字段时复用历史
        ``root_task_id``（首轮 task_id），再回退到当前 task_id。
        """
        return str(self.meta.get("agent_session_id") or self.meta.get("root_task_id") or self.task_id)

    @property
    def agent_id(self) -> str:
        """子智能体实例 ID；当前固定一层，旧数据与子会话同 ID。"""
        return str(self.meta.get("agent_id") or self.agent_session_id)

    @property
    def parent_agent_id(self) -> str:
        """父智能体 ID；当前固定为空（子智能体由主智能体直接派生）。"""
        return str(self.meta.get("parent_agent_id") or "")

    @property
    def root_agent_id(self) -> str:
        """子智能体根标识；当前固定等于自身。"""
        return str(self.meta.get("root_agent_id") or self.agent_id)

    @property
    def depth(self) -> int:
        """层级深度；当前固定为 1（子智能体不再派发）。"""
        try:
            return max(int(self.meta.get("depth") or 1), 1)
        except (TypeError, ValueError):
            return 1

    @property
    def root_task_id(self) -> str:
        """兼容字段：历史调用方用它当子会话 ID；新代码请用 ``agent_session_id``。"""
        return self.agent_session_id

    @property
    def turn_index(self) -> int:
        try:
            return max(int(self.meta.get("turn_index") or 1), 1)
        except (TypeError, ValueError):
            return 1

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES

    @property
    def compaction_count(self) -> int:
        """已完成的子图上下文压缩次数；终态以结果快照为准，运行态读心跳 progress。

        事件在生成时已按 id 去重；两处都是累计值，取较大者可避免最后一次心跳
        滞后于结果快照时反而把次数算少（取消/失联任务尤其常见）。
        """
        compactions = self.result_meta.get("subagent_compactions")
        result_count = len(compactions) if isinstance(compactions, list) else 0
        running = self.progress.get("compaction_count")
        running_count = running if isinstance(running, int) and not isinstance(running, bool) and running > 0 else 0
        return max(result_count, running_count)

    def as_dict(self, *, include_report: bool = True) -> dict[str, Any]:
        """API / 工具共用结构；时间统一 ISO，避免前端重复解析格式。"""
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "conversation_id": self.conversation_id,
            "parent_run_id": self.parent_run_id,
            "parent_tool_call_id": self.parent_tool_call_id,
            "parent_task_id": self.parent_task_id or None,
            "root_task_id": self.root_task_id,
            "agent_id": self.agent_id,
            "agent_session_id": self.agent_session_id,
            "parent_agent_id": self.parent_agent_id or None,
            "root_agent_id": self.root_agent_id,
            "depth": self.depth,
            "turn_index": self.turn_index,
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
            "execution_mode": self.execution_mode,
            "compaction_count": self.compaction_count,
        }
        if include_report:
            payload["report"] = self.report
            payload["report_path"] = self.report_path
            payload["error"] = self.error
            payload["result_meta"] = self.result_meta
            payload["pending_interrupt"] = self.meta.get("pending_interrupt")
        return payload


@dataclass(frozen=True)
class _TaskLineage:
    """一次任务在子会话/任务链上的稳定身份，创建任务前一次性解析。"""

    root_task: str
    parent_task: str
    turn_index: int
    agent_id: str
    agent_session: str
    parent_agent_id: str
    root_agent_id: str
    depth: int


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


_SCHEMA_MISSING_MYSQL_CODES = frozenset({1054, 1146})  # unknown column / table doesn't exist


def _is_session_schema_missing(exc: BaseException) -> bool:
    """判断异常是否只是会话表/关闭列尚未迁移；其余数据库故障不允许降级。"""
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", None) or getattr(exc, "args", ())
    code = args[0] if args else None
    if isinstance(code, int) and code in _SCHEMA_MISSING_MYSQL_CODES:
        return True
    message = str(orig or exc).lower()
    return any(token in message for token in ("no such column", "no such table", "unknown column", "doesn't exist"))


def _session_state(row: ChatDataSubagentSession) -> SubagentSessionState:
    return SubagentSessionState(
        session_id=row.session_id,
        status=row.session_status or SESSION_STATUS_OPEN,
        closed_at=row.closed_time,
    )


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


def notification_message_id(task_id: str) -> str:
    """兼容旧调用方的完成通知消息 ID；统一走事件哈希，保证列宽稳定。"""
    return event_message_id(terminal_event_id(task_id))


def _legacy_notification_message_id(task_id: str) -> str:
    """升级前已生成的通知消息 ID，只用于已读时清理存量 pending 消息。"""
    return f"ntf-{task_id}"


def _resolve_cancel_origin(meta: dict[str, Any], origin: str) -> str:
    """用户来源优先，避免用户停止后被主智能体的后续 cancel 覆盖。"""
    existing = str(meta.get("cancel_origin") or "")
    if origin == CANCEL_ORIGIN_USER or existing == CANCEL_ORIGIN_USER:
        return CANCEL_ORIGIN_USER
    if origin == CANCEL_ORIGIN_AGENT:
        return CANCEL_ORIGIN_AGENT
    return existing or origin


class SubagentTaskService:
    """任务生命周期、取消、回收与通知派发。"""

    def __init__(
        self,
        dao: SubagentTaskDao = subagent_task_dao,
        inbox: Any = agent_inbox_service,
        event_service: Any = agent_run_event_service,
        notification_service: Any = subagent_event_service,
    ) -> None:
        self._dao = dao
        self._inbox = inbox
        self._event_service = event_service
        self._notification_service = notification_service
        self._local_tasks: dict[str, asyncio.Task[None]] = {}
        self._recovery_runner: Callable[[SubagentTaskView, Any], Coroutine[Any, Any, None]] | None = None
        # 后台任务由创建它的 Agent 装配上下文执行；恢复必须复用同一任务绑定的
        # runner，不能用进程内最后一次装配的 runner 猜测用户身份与工作区。
        self._task_recovery_runners: dict[str, Callable[[SubagentTaskView, Any], Coroutine[Any, Any, None]]] = {}
        # 任务卡推送降级截止时间（monotonic），避免失败后每个心跳重复重试。
        self._push_degraded_until: dict[str, float] = {}
        # 父 Run 收口后 Redis owner 已删除；记住失效 token，后续心跳跳过父 Run。
        self._stale_parent_push_tokens: dict[str, str] = {}

    def register_recovery_runner(
        self,
        runner: Callable[[SubagentTaskView, Any], Coroutine[Any, Any, None]] | None,
        *,
        task_id: str | None = None,
    ) -> None:
        """由子图装配层注入恢复执行器，run 层只负责认领与调度。

        生产路径按 task_id 绑定，防止多用户会话共用进程级单例时串用工具与
        数据源；无 task_id 的默认 runner 仅供测试或兼容旧调用。
        """
        if task_id is not None:
            if runner is None:
                self._task_recovery_runners.pop(task_id, None)
            else:
                self._task_recovery_runners[task_id] = runner
            return
        self._recovery_runner = runner
        if runner is None:
            self._task_recovery_runners.clear()

    def register_local_task(self, task_id: str, task: asyncio.Task[None]) -> None:
        self._local_tasks[task_id] = task

        def _forget_task(completed: asyncio.Task[None]) -> None:
            if self._local_tasks.get(task_id) is completed:
                self._local_tasks.pop(task_id, None)
                # 不能在这里立即释放恢复执行器：协程可能异常退出但任务仍停在
                # running，租约过期后需要同一 runner 继续接管。runner 只在
                # 任务确认进入终态时清理。

        task.add_done_callback(_forget_task)

    def _recovery_runner_for(self, task_id: str) -> Callable[[SubagentTaskView, Any], Coroutine[Any, Any, None]] | None:
        return self._task_recovery_runners.get(task_id) or self._recovery_runner

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
            context=dict(getattr(runtime, "context", None) or {}),
        )

    async def _resolve_task_lineage(
        self,
        session: Any,
        *,
        task_id: str,
        conversation_id: str,
        user_id: str,
        parent_task_id: str | None,
        agent_session_id: str | None,
        root_task_id: str | None,
    ) -> _TaskLineage:
        """解析新任务或追问在子会话中的身份，并原子拦截并行的活跃追问。"""
        root_task = str(agent_session_id or root_task_id or task_id)
        parent_task = str(parent_task_id or "")
        turn_index = 1
        agent_id = root_task
        agent_session = root_task
        parent_agent_id = ""
        root_agent_id = agent_id
        depth = 1
        if root_task == task_id:
            return _TaskLineage(
                root_task=root_task,
                parent_task=parent_task,
                turn_index=turn_index,
                agent_id=agent_id,
                agent_session=agent_session,
                parent_agent_id=parent_agent_id,
                root_agent_id=root_agent_id,
                depth=depth,
            )
        # 会话行锁已把同会话的 create_task 串行化；在这里再查一次活跃轮次，
        # 拦住主智能体同一轮并行发出的多个 follow_up（前置查询无法原子挡）。
        session_active = await self._dao.list_active_for_conversation(
            session,
            conversation_id,
            user_id,
            ACTIVE_TASK_STATUSES,
        )
        if any(
            SubagentTaskRecord.from_row(row, include_content=False).root_task_id == root_task for row in session_active
        ):
            raise SubagentTaskCapacityError("该子智能体已有进行中的追问任务，请等待其结束或先取消。")
        parent_row = await self._dao.get(
            session,
            parent_task,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        parent_record = SubagentTaskRecord.from_row(parent_row, include_content=False) if parent_row else None
        if parent_record is None:
            return _TaskLineage(
                root_task=root_task,
                parent_task=parent_task,
                turn_index=2,
                agent_id=agent_id,
                agent_session=agent_session,
                parent_agent_id=parent_agent_id,
                root_agent_id=root_agent_id,
                depth=depth,
            )
        # 追问沿用同一智能体身份与同一份会话快照；parent_task_id 只是任务链
        # 上一轮，不代表新的智能体。
        return _TaskLineage(
            root_task=parent_record.agent_session_id,
            parent_task=parent_task,
            turn_index=parent_record.turn_index + 1,
            agent_id=parent_record.agent_id,
            agent_session=parent_record.agent_session_id,
            parent_agent_id=parent_record.parent_agent_id,
            root_agent_id=parent_record.root_agent_id,
            depth=parent_record.depth,
        )

    async def _ensure_session_open(
        self,
        session: Any,
        session_id: str,
        *,
        current_task_id: str,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """首轮无需检查；追问前拒绝已永久关闭的会话。

        只有会话表/关闭列尚未迁移时允许降级为“未关闭”，其它数据库异常必须
        向上抛出（fail-closed），避免永久关闭被瞬时故障绕过。
        """
        if session_id == current_task_id:
            return
        try:
            snapshot = await subagent_session_dao.get(
                session,
                session_id,
                conversation_id=conversation_id,
                user_id=user_id,
            )
        except Exception as exc:
            if not _is_session_schema_missing(exc):
                raise
            # 会话表/关闭列尚未迁移时不能阻断原有追问；历史读取与收口写入
            # 已有各自的降级路径。
            logger.warning("子智能体会话表/关闭列尚未迁移，按未关闭处理: session_id=%s", session_id, exc_info=True)
            return
        if snapshot is not None and snapshot.session_status == SESSION_STATUS_CLOSED:
            raise SubagentSessionClosedError("该子智能体会话已关闭，不能继续追问；如需同类任务请重新派发。")

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
        operator_name: str,
        parent_task_id: str | None = None,
        agent_session_id: str | None = None,
        root_task_id: str | None = None,
    ) -> SubagentTaskRecord:
        max_active = int(await get_runtime_setting("CHAT_DATA_SUBAGENT_TASK_MAX_ACTIVE"))
        max_subagents_per_conversation = int(await get_runtime_setting("CHAT_DATA_SUBAGENT_MAX_PER_CONVERSATION"))
        lease_seconds = int(await get_runtime_setting("CHAT_DATA_SUBAGENT_TASK_LEASE_SECONDS"))
        # 子任务超时统一由全局配置控制：模型不可按单次调用覆盖，避免生成过短超时。
        effective_timeout = int(await get_runtime_setting("CHAT_DATA_SUBAGENT_TASK_TIMEOUT_SECONDS"))
        task_id = uuid4().hex
        owner_token = uuid4().hex
        record: SubagentTaskRecord | None = None
        async with get_session() as session:
            now = await self._dao.now(session)
            thread = await agent_inbox_dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            lineage = await self._resolve_task_lineage(
                session,
                task_id=task_id,
                conversation_id=conversation_id,
                user_id=user_id,
                parent_task_id=parent_task_id,
                agent_session_id=agent_session_id,
                root_task_id=root_task_id,
            )
            await self._ensure_session_open(
                session,
                lineage.root_task,
                current_task_id=task_id,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            await self._check_capacity(
                session,
                conversation_id=conversation_id,
                user_id=user_id,
                max_active=max_active,
                max_subagents_per_conversation=max_subagents_per_conversation,
                continuing=bool(parent_task_id or agent_session_id or root_task_id),
            )
            meta = {
                "subagent_type": subagent_type,
                "description": description,
                "label": (label or description.splitlines()[0][:80]).strip(),
                "parent_tool_call_id": parent_tool_call_id,
                "parent_owner_token": str(parent_owner_token or ""),
                "parent_task_id": lineage.parent_task,
                "root_task_id": lineage.root_task,
                "agent_id": lineage.agent_id,
                "agent_session_id": lineage.agent_session,
                "parent_agent_id": lineage.parent_agent_id,
                "root_agent_id": lineage.root_agent_id,
                "depth": lineage.depth,
                "turn_index": lineage.turn_index,
                "operator_name": operator_name,
                "timeout_seconds": effective_timeout,
                "execution_mode": "background",
                "notify_on_finish": True,
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
                "子智能体任务已登记: task_id=%s conversation_id=%s type=%s",
                task_id,
                conversation_id,
                subagent_type,
            )
            record = SubagentTaskRecord.from_row(row)
        await self.publish_task_update(record)
        return record

    async def configure_task_execution(
        self,
        task_id: str,
        owner_token: str,
        *,
        execution_mode: str,
    ) -> SubagentTaskRecord | None:
        """在任务启动前写入执行模式；同步任务据此关闭完成通知。"""
        normalized_mode = "sync" if execution_mode == "sync" else "background"
        async with get_session() as session:
            now = await self._dao.now(session)
            row = await self._dao.get(session, task_id, for_update=True)
            if row is None or row.owner_token != owner_token or row.record_status != TASK_STATUS_PENDING:
                return None
            meta = load_task_meta(row.record_meta)
            meta["execution_mode"] = normalized_mode
            meta["notify_on_finish"] = normalized_mode != "sync"
            row.record_meta = _task_meta_text(meta)
            row.update_time = now
            record = SubagentTaskRecord.from_row(row)
        await self.publish_task_update(record)
        return record

    async def save_task_recovery_runtime(
        self,
        task_id: str,
        owner_token: str,
        *,
        state: dict[str, Any],
        tool_call_id: str | None,
    ) -> SubagentTaskRecord | None:
        """在启动前持久化最小恢复上下文，pending 任务不依赖原进程内存重建。"""
        async with get_session() as session:
            now = await self._dao.now(session)
            row = await self._dao.get(session, task_id, for_update=True)
            if row is None or row.owner_token != owner_token or row.record_status != TASK_STATUS_PENDING:
                return None
            meta = load_task_meta(row.record_meta)
            meta["recovery_runtime"] = {"state": state, "tool_call_id": tool_call_id or ""}
            meta["execution_attempt"] = 1
            meta["recovery_count"] = 0
            row.record_meta = _task_meta_text(meta)
            row.update_time = now
            record = SubagentTaskRecord.from_row(row)
        await self.publish_task_update(record)
        return record

    async def _check_capacity(
        self,
        session: Any,
        *,
        conversation_id: str,
        user_id: str,
        max_active: int,
        max_subagents_per_conversation: int,
        continuing: bool,
    ) -> None:
        """在会话行锁内校验当前主对话的并发与新子智能体总额度。"""
        active = await self._dao.count_active(session, conversation_id, user_id, ACTIVE_TASK_STATUSES)
        if active >= max_active:
            raise SubagentTaskCapacityError(
                f"子智能体任务已达上限（{max_active} 个执行中），请等待任务完成或取消后再派发。"
            )
        if continuing:
            return
        try:
            subagent_count = await self._dao.count_subagents(session, conversation_id, user_id)
        except Exception as exc:
            if not _is_session_schema_missing(exc):
                raise
            # 会话表/关闭列尚未迁移时只失去“关闭后释放额度”的能力，不能把
            # 新派发全部阻断；退回旧口径：任务里出现过的子会话都计入。
            logger.warning("子智能体会话表/关闭列尚未迁移，容量按未关闭会话统计", exc_info=True)
            subagent_count = len(await self._dao.list_subagent_session_ids(session, conversation_id, user_id))
        if subagent_count >= max_subagents_per_conversation:
            raise SubagentTaskCapacityError(
                f"当前主对话已有 {subagent_count} 个可用子智能体，达到上限（{max_subagents_per_conversation} 个）。"
                '请先用 subagent({"action":"list"}) 查看已有子智能体；任务类似时用 '
                'subagent({"action":"send","task_id":"...","message":"..."}) 复用原会话，不要新开。'
                "如果已有智能体不再使用，请提示用户在子智能体管理面板关闭它们以腾出额度。"
            )

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

    async def load_session_messages(
        self,
        session_id: str,
        *,
        conversation_id: str,
        user_id: str,
        expected_turn_index: int | None = None,
    ) -> list[dict[str, Any]]:
        """读取子会话的完整消息历史；缺失或损坏时返回空列表。"""
        try:
            async with get_session() as session:
                row = await subagent_session_dao.get(
                    session,
                    session_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
        except Exception:
            # 新表尚未迁移/数据库暂时不可用不能阻断追问：退化为“任务书+报告”
            # 兜底，任务本身仍可继续执行。
            logger.warning("子智能体会话历史读取失败，按空历史处理: session_id=%s", session_id, exc_info=True)
            return []
        if row is None or not row.messages_json:
            return []
        if expected_turn_index is not None and int(row.turn_count or 0) < expected_turn_index:
            # 本轮快照写入失败时表里仍是旧轮次。不能用旧历史冒充当前上下文，
            # 交给调用方退回“上一轮任务书+报告”兜底。
            logger.warning(
                "子智能体会话快照落后于当前任务轮次，按空历史处理: session_id=%s expected=%s actual=%s",
                session_id,
                expected_turn_index,
                row.turn_count,
            )
            return []
        try:
            loaded = json.loads(row.messages_json)
        except (TypeError, ValueError):
            logger.warning("子智能体会话历史损坏，按空历史处理: session_id=%s", session_id, exc_info=True)
            return []
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]

    async def find_active_task_in_session(
        self,
        session_id: str,
        *,
        conversation_id: str,
        user_id: str,
    ) -> SubagentTaskRecord | None:
        """同一子会话只允许一个活跃轮次，避免两轮追问并发改写同一份历史。"""
        async with get_session() as session:
            rows = await self._dao.list_active_for_conversation(
                session,
                conversation_id,
                user_id,
                ACTIVE_TASK_STATUSES,
            )
        for row in rows:
            record = SubagentTaskRecord.from_row(row, include_content=False)
            if record.root_task_id == session_id:
                return record
        return None

    async def get_latest_task_in_session(
        self,
        session_id: str,
        *,
        conversation_id: str,
        user_id: str,
    ) -> SubagentTaskRecord | None:
        """返回子会话最新一轮任务，保证追问的 parent/turn_index 始终线性推进。"""
        try:
            async with get_session() as session:
                snapshot = await subagent_session_dao.get(
                    session,
                    session_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
        except Exception:
            logger.warning("子智能体会话最新任务读取失败，回退到指定任务: session_id=%s", session_id, exc_info=True)
            return None
        if snapshot is None or not snapshot.latest_task_id:
            return None
        return await self.get_task(
            snapshot.latest_task_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )

    async def get_session_state(
        self,
        session_id: str,
        *,
        conversation_id: str,
        user_id: str,
    ) -> SubagentSessionState | None:
        """读取子智能体逻辑会话状态；缺失快照按未关闭处理。"""
        try:
            async with get_session() as session:
                row = await subagent_session_dao.get(
                    session,
                    session_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
        except Exception:
            logger.warning("子智能体会话状态读取失败，按未关闭处理: session_id=%s", session_id, exc_info=True)
            return None
        return _session_state(row) if row is not None else None

    async def list_session_states(
        self,
        session_ids: Sequence[str],
        *,
        conversation_id: str,
        user_id: str,
    ) -> dict[str, SubagentSessionState]:
        """批量读取会话状态；list 页面用一次查询标注哪些会话已关闭。"""
        try:
            async with get_session() as session:
                rows = await subagent_session_dao.list_by_session_ids(
                    session,
                    list(session_ids),
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
        except Exception:
            logger.warning("子智能体会话状态批量读取失败，按未关闭处理", exc_info=True)
            return {}
        return {row.session_id: _session_state(row) for row in rows}

    async def close_session(
        self,
        task_id: str,
        *,
        conversation_id: str,
        user_id: str,
        operator_name: str,
    ) -> SubagentSessionState | None:
        """永久关闭任务所属子会话；已关闭幂等返回，有活跃轮次时拒绝。"""
        async with get_session() as session:
            now = await self._dao.now(session)
            thread = await agent_inbox_dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            target_row = await self._dao.get(
                session,
                task_id,
                conversation_id=conversation_id,
                user_id=user_id,
                for_update=True,
            )
            if target_row is None:
                return None
            target = SubagentTaskRecord.from_row(target_row, include_content=False)
            session_id = target.agent_session_id
            active_rows = await self._dao.list_active_for_conversation(
                session,
                conversation_id,
                user_id,
                ACTIVE_TASK_STATUSES,
            )
            if any(
                SubagentTaskRecord.from_row(row, include_content=False).root_task_id == session_id
                for row in active_rows
            ):
                raise SubagentSessionActiveError("该子智能体仍有进行中的任务，请先等待完成或取消，再关闭会话。")
            snapshot = await subagent_session_dao.get(
                session,
                session_id,
                conversation_id=conversation_id,
                user_id=user_id,
                for_update=True,
            )
            snapshot_turn_count = int(snapshot.turn_count or 1) if snapshot is not None else 0
            # 收口时快照写失败会只丢快照，此时 snapshot 可能落后于已终态的最新任务；
            # 从更新的一轮关闭时必须锚定目标任务，避免 turn_count 前进而
            # latest_task_id 退回旧轮。从旧任务关闭仍保留快照里的最新指针。
            latest_task_id = (
                snapshot.latest_task_id
                if snapshot is not None and target.turn_index <= snapshot_turn_count
                else target.task_id
            )
            turn_count = max(snapshot_turn_count or 1, target.turn_index)
            row = await subagent_session_dao.close(
                session,
                session_id=session_id,
                conversation_id=conversation_id,
                user_id=user_id,
                latest_task_id=latest_task_id,
                turn_count=turn_count,
                now=now,
                operator_name=operator_name,
            )
            state = _session_state(row)
        logger.info("子智能体会话已关闭: session_id=%s task_id=%s", state.session_id, task_id)
        return state

    async def publish_task_update(self, task: SubagentTaskRecord) -> None:
        """向当前活跃 Run 的 SSE 推送紧凑任务快照；失败只降级为前端轮询。"""
        now = time.monotonic()
        if self._push_degraded_until.get(task.task_id, 0.0) > now:
            return
        parent_owner_token = str(task.meta.get("parent_owner_token") or "")
        parent_token_stale = self._stale_parent_push_tokens.get(task.task_id) == parent_owner_token
        if task.parent_run_id and parent_owner_token and not parent_token_stale:
            parent_result = await self._append_task_event(task, task.parent_run_id, parent_owner_token)
            if parent_result == _PUSH_RESULT_OK:
                self._push_degraded_until.pop(task.task_id, None)
                return
            if parent_result == _PUSH_RESULT_LEASE_LOST:
                self._stale_parent_push_tokens[task.task_id] = parent_owner_token
        active = await self._load_active_run_target(task)
        if active is None:
            self._push_degraded_until[task.task_id] = now + _TASK_PUSH_DEGRADED_SECONDS
            return
        run_id, owner_token = active
        if run_id == task.parent_run_id and owner_token == parent_owner_token:
            self._push_degraded_until[task.task_id] = now + _TASK_PUSH_DEGRADED_SECONDS
            return
        if await self._append_task_event(task, run_id, owner_token) == _PUSH_RESULT_OK:
            self._push_degraded_until.pop(task.task_id, None)
            return
        self._push_degraded_until[task.task_id] = now + _TASK_PUSH_DEGRADED_SECONDS

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

    async def _append_task_event(
        self, task: SubagentTaskRecord, run_id: str, owner_token: str
    ) -> Literal["ok", "lease_lost", "failed"]:
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
        except AgentRunLeaseLostError:
            logger.debug("后台任务推送目标 Run 已失效: task_id=%s run_id=%s", task.task_id, run_id)
            return _PUSH_RESULT_LEASE_LOST
        except Exception:
            logger.debug("后台任务实时事件推送失败，等待列表轮询兜底: task_id=%s", task.task_id, exc_info=True)
            return _PUSH_RESULT_FAILED
        return _PUSH_RESULT_OK

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

    async def assert_task_owner(self, task_id: str, owner_token: str) -> None:
        """工具副作用前的硬围栏；旧执行体即使恢复调度也不能继续调工具。"""
        async with get_session() as session:
            row = await self._dao.get(session, task_id)
            if row is None or row.owner_token != owner_token or row.record_status != TASK_STATUS_RUNNING:
                raise SubagentTaskLeaseLostError("子智能体任务已结束或已由其他 Worker 接管")

    async def enqueue_steer_message(
        self,
        task_id: str,
        *,
        conversation_id: str,
        user_id: str,
        message: str,
        operator_name: str,
    ) -> SubagentTaskRecord | None:
        """把主智能体的插话排入子任务队列，由子图在下次模型调用前领取。"""
        content = str(message or "").strip()
        if not content:
            return None
        async with get_session() as session:
            now = await self._dao.now(session)
            row = await self._dao.get(
                session,
                task_id,
                conversation_id=conversation_id,
                user_id=user_id,
                for_update=True,
            )
            if row is None or row.record_status in TERMINAL_TASK_STATUSES:
                return None
            meta = load_task_meta(row.record_meta)
            queue = meta.get("steer_queue")
            queue = list(queue) if isinstance(queue, list) else []
            queue.append({"message": content[:2000], "enqueued_at": now.isoformat()})
            meta["steer_queue"] = queue[-20:]
            row.record_meta = _task_meta_text(meta)
            row.update_time = now
            row.update_by = user_id
            row.update_by_name = operator_name
            record = SubagentTaskRecord.from_row(row)
        await self.publish_task_update(record)
        return record

    async def claim_steer_messages(self, task_id: str, owner_token: str) -> list[str]:
        """原子领取并清空插话队列；只有当前执行者能领取。"""
        async with get_session() as session:
            row = await self._dao.get(session, task_id, for_update=True)
            if row is None or row.owner_token != owner_token:
                return []
            if row.record_status != TASK_STATUS_RUNNING:
                return []
            meta = load_task_meta(row.record_meta)
            queue = meta.pop("steer_queue", [])
            if not isinstance(queue, list) or not queue:
                return []
            row.record_meta = _task_meta_text(meta)
            row.update_time = await self._dao.now(session)
        return [str(item.get("message") or "") for item in queue if isinstance(item, dict) and item.get("message")]

    async def suspend_task(
        self,
        task_id: str,
        owner_token: str,
        *,
        status: str,
        interrupt_payload: dict[str, Any] | None = None,
    ) -> SubagentTaskRecord | None:
        """执行体在审批 interrupt 后把任务落到等待审批，并释放租约。"""
        if status not in WAITING_APPROVAL_TASK_STATUSES:
            raise ValueError(f"不支持的挂起状态: {status}")
        notification_event_id: str | None = None
        async with get_session() as session:
            now = await self._dao.now(session)
            row = await self._dao.get(session, task_id, for_update=True)
            if row is None or row.owner_token != owner_token:
                return None
            if row.record_status in TERMINAL_TASK_STATUSES or row.record_status == TASK_STATUS_CANCELLING:
                return SubagentTaskRecord.from_row(row)
            meta = load_task_meta(row.record_meta)
            meta["waiting_approval_at"] = now.isoformat()
            meta["pending_interrupt"] = interrupt_payload
            progress = meta.get("progress")
            progress = dict(progress) if isinstance(progress, dict) else {}
            progress.update({"phase": status, "running_tools": 0})
            meta["progress"] = progress
            row.record_status = status
            row.owner_token = None
            row.lease_expire_time = None
            row.record_meta = _task_meta_text(meta)
            row.update_time = now
            record = SubagentTaskRecord.from_row(row)
            notification_event_id = lifecycle_event_id(EVENT_WAITING_APPROVAL, task_id, now)
            await self._notification_service.cancel_pending_in_session(
                session,
                task_id,
                APPROVAL_NOTIFICATION_EVENTS,
                now,
            )
            await self._emit_event_in_session(
                session,
                record,
                now,
                event_id=notification_event_id,
                event_type=EVENT_WAITING_APPROVAL,
                wake_policy=WAKE_POLICY_WAKE,
                content=_suspension_notification_text(record),
            )
        await self.publish_task_update(record)
        logger.info("后台子智能体已挂起: task_id=%s status=%s", task_id, status)
        await self._dispatch_event_best_effort(notification_event_id)
        return record

    async def resume_task(
        self,
        task_id: str,
        *,
        conversation_id: str,
        user_id: str,
        resume_value: Any,
    ) -> tuple[SubagentTaskRecord, Any] | None:
        """把等待审批的任务重新置为运行中并返回新的执行者身份与审批结果。"""
        notification_event_id: str | None = None
        async with get_session() as session:
            now = await self._dao.now(session)
            lease_seconds = int(await get_runtime_setting("CHAT_DATA_SUBAGENT_TASK_LEASE_SECONDS"))
            row = await self._dao.get(
                session,
                task_id,
                conversation_id=conversation_id,
                user_id=user_id,
                for_update=True,
            )
            if row is None or row.record_status not in WAITING_APPROVAL_TASK_STATUSES:
                return None
            meta = load_task_meta(row.record_meta)
            meta["resumed_at"] = now.isoformat()
            meta["pending_interrupt"] = None
            # 审批前的父 Run owner token 已随原 Run 收口失效；恢复后继续拿它推送
            # 必然 owner mismatch（每 5 秒刷一行失败日志）。清空后直接回落到
            # 当前活跃 Run，找不到就静默交给前端列表轮询兜底。
            meta["parent_owner_token"] = ""
            progress = meta.get("progress")
            progress = dict(progress) if isinstance(progress, dict) else {}
            progress.update({"phase": "resumed", "running_tools": 0})
            meta["progress"] = progress
            row.record_status = TASK_STATUS_RUNNING
            row.owner_token = uuid4().hex
            row.lease_expire_time = now + timedelta(seconds=lease_seconds)
            row.record_meta = _task_meta_text(meta)
            row.update_time = now
            record = SubagentTaskRecord.from_row(row)
            await self._notification_service.cancel_pending_in_session(
                session,
                task_id,
                APPROVAL_NOTIFICATION_EVENTS,
                now,
            )
            notification_event_id = lifecycle_event_id(EVENT_RESUMED, task_id, now)
            await self._emit_event_in_session(
                session,
                record,
                now,
                event_id=notification_event_id,
                event_type=EVENT_RESUMED,
                wake_policy=WAKE_POLICY_ATTACH,
                content=_resumed_notification_text(record),
            )
        await self.publish_task_update(record)
        logger.info("后台子智能体审批后恢复执行: task_id=%s", task_id)
        await self._dispatch_event_best_effort(notification_event_id)
        return record, resume_value

    async def finish_task(
        self,
        task_id: str,
        owner_token: str,
        *,
        status: str,
        report: str,
        error: str = "",
        result_meta: dict[str, Any] | None = None,
        history: Sequence[dict[str, Any]] | None = None,
    ) -> SubagentTaskRecord | None:
        async with get_session() as session:
            now = await self._dao.now(session)
            row = await self._dao.get(session, task_id, for_update=True)
            if row is None or row.owner_token != owner_token:
                return None
            if row.record_status in TERMINAL_TASK_STATUSES:
                record = SubagentTaskRecord.from_row(row)
                self._task_recovery_runners.pop(task_id, None)
                return record
            final_status = TASK_STATUS_CANCELLED if row.record_status == TASK_STATUS_CANCELLING else status
            meta = load_task_meta(row.record_meta)
            # 结果快照（过程 trail / token 用量）动辄上百 KB，record_meta 是
            # TEXT 列（64KB），写进去会 DataError 1406 并回滚收口事务，任务
            # 只能等租约过期变 lost。改存 record_content（MEDIUMTEXT）的信封。
            meta.pop("result_meta", None)
            notify_on_finish = bool(meta.get("notify_on_finish", True))
            if not notify_on_finish and row.consumed_time is None:
                # 同步任务在父 Run 内直接返回结论，不能再派生完成通知 Run。
                row.consumed_time = now
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
            # 待审批已不再成立，先撤回尚未投递的旧事件，避免终态之后又冒泡。
            await self._notification_service.cancel_pending_in_session(
                session,
                task_id,
                APPROVAL_NOTIFICATION_EVENTS,
                now,
            )
            if record.notify_on_finish:
                await self._emit_event_in_session(
                    session,
                    record,
                    now,
                    event_id=terminal_event_id(task_id),
                    event_type=EVENT_TERMINAL,
                    wake_policy=WAKE_POLICY_WAKE,
                    content=self._notification_text(record),
                )
            if history is not None:
                # 快照与终态同事务提交，避免“任务已 terminal、latest_task_id 仍指向
                # 上一轮”的追问竞态；保存点保证新表未上线/写失败时只丢快照，
                # 不会把任务收口回滚成等待租约回收。
                try:
                    async with session.begin_nested():
                        await self._save_session_history(
                            session,
                            meta,
                            history,
                            now,
                            conversation_id=record.conversation_id,
                            user_id=record.user_id,
                            task_id=task_id,
                        )
                except Exception:
                    logger.error(
                        "子智能体会话快照写入失败，任务已正常收口: task_id=%s session_id=%s",
                        task_id,
                        str(meta.get("agent_session_id") or meta.get("root_task_id") or task_id),
                        exc_info=True,
                    )
        self._task_recovery_runners.pop(task_id, None)
        await self.publish_task_update(record)
        return record

    async def _save_session_history(
        self,
        session: Any,
        meta: dict[str, Any],
        history: Sequence[dict[str, Any]],
        now: datetime,
        *,
        conversation_id: str,
        user_id: str,
        task_id: str,
    ) -> None:
        """把本轮结束时的完整消息历史整体写入子会话快照。"""
        session_id = str(meta.get("agent_session_id") or meta.get("root_task_id") or "").strip()
        if not session_id:
            return
        try:
            messages_json = json.dumps(list(history), ensure_ascii=False)
        except (TypeError, ValueError):
            logger.warning("子智能体会话历史序列化失败，跳过持久化: session_id=%s", session_id, exc_info=True)
            return
        try:
            turn_count = max(int(meta.get("turn_index") or 1), 1)
        except (TypeError, ValueError):
            turn_count = 1
        await subagent_session_dao.upsert(
            session,
            session_id=session_id,
            conversation_id=conversation_id,
            user_id=user_id,
            latest_task_id=task_id,
            turn_count=turn_count,
            messages_json=messages_json,
            now=now,
            operator_name=str(meta.get("operator_name") or ""),
        )

    async def cancel_task(
        self,
        conversation_id: str,
        user_id: str,
        task_id: str,
        operator_name: str,
        *,
        origin: str = CANCEL_ORIGIN_USER,
    ) -> SubagentTaskRecord | None:
        cancelled_running = False
        notification_event_id: str | None = None
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
            # 用户优先级最高：同一任务先被用户停止、后又被主智能体 cancel 时，
            # 终态通知仍必须按“用户主动停止”处理，不能被覆盖成可继续执行。
            cancel_origin = _resolve_cancel_origin(meta, origin)
            meta["cancel_origin"] = cancel_origin
            # 取消同样要告知主智能体：运行中的任务在下次心跳才真正停下，
            # 调用方只拿到「已提交」；终态通知才是主智能体继续编排的依据。
            if row.record_status in (TASK_STATUS_PENDING, *WAITING_APPROVAL_TASK_STATUSES):
                meta["finished_at"] = now.isoformat()
                meta["phase"] = TASK_STATUS_CANCELLED
                progress = meta.get("progress")
                progress = dict(progress) if isinstance(progress, dict) else {}
                progress.update({"phase": TASK_STATUS_CANCELLED, "running_tools": 0})
                meta["progress"] = progress
                meta.pop("pending_interrupt", None)
                row.record_status = TASK_STATUS_CANCELLED
                row.record_content = (
                    "任务在开始执行前已取消（用户主动停止）。"
                    if cancel_origin == CANCEL_ORIGIN_USER
                    else "任务在开始执行前已取消。"
                )
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
            await self._notification_service.cancel_pending_in_session(
                session,
                task_id,
                APPROVAL_NOTIFICATION_EVENTS,
                now,
            )
            if record.status == TASK_STATUS_CANCELLED and record.notify_on_finish:
                notification_event_id = terminal_event_id(task_id)
                await self._emit_event_in_session(
                    session,
                    record,
                    now,
                    event_id=notification_event_id,
                    event_type=EVENT_TERMINAL,
                    wake_policy=WAKE_POLICY_WAKE,
                    content=self._notification_text(record),
                )
            if record.is_terminal:
                self._task_recovery_runners.pop(task_id, None)
        if cancelled_running:
            self.cancel_local_task(task_id)
        await self.publish_task_update(record)
        # 排队中/已挂起的任务取消即终态，当场派发终态事件；运行中任务由收口路径派发。
        await self._dispatch_event_best_effort(notification_event_id)
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
            await self._notification_service.cancel_pending_in_session(
                session,
                row.message_id,
                APPROVAL_NOTIFICATION_EVENTS | {EVENT_TERMINAL},
                now,
            )
            notification = await agent_inbox_dao.get_message_by_id(
                session,
                row.conversation_id,
                row.user_id,
                _legacy_notification_message_id(row.message_id),
            )
            if notification is not None and notification.record_status == "pending":
                notification.record_status = "cancelled"
                notification.delete_flag = 1
                notification.update_time = now
            row.update_time = now

    async def reap_expired_tasks(self, *, limit: int = 20) -> int:
        recovered_ids: list[str] = []
        terminal_ids: list[str] = []
        lease_seconds = int(await get_runtime_setting("CHAT_DATA_SUBAGENT_TASK_LEASE_SECONDS"))
        async with get_session() as session:
            now = await self._dao.now(session)
            # 等待审批没有执行体与租约，不能被当成「执行节点失联」回收。
            rows = await self._dao.list_expired(session, set(EXECUTING_TASK_STATUSES), now, limit=limit)
            if not rows:
                return 0
            reaped = 0
            for row in rows:
                record = SubagentTaskRecord.from_row(row, include_content=False)
                outcome = await self._reap_expired_record(session, record, now, lease_seconds)
                if outcome == "recovered":
                    recovered_ids.append(record.task_id)
                    reaped += 1
                elif outcome == "terminal":
                    terminal_ids.append(record.task_id)
                    reaped += 1
        for task_id in recovered_ids:
            recovered_record = await self.get_task(task_id)
            if recovered_record is not None:
                await self.publish_task_update(recovered_record)
                await self._launch_recovered_task(recovered_record)
        for task_id in terminal_ids:
            terminal_record = await self.get_task(task_id)
            if terminal_record is not None:
                await self.publish_task_update(terminal_record)
                await self._notify_reaped_terminal(terminal_record)
        return reaped

    async def _reap_expired_record(
        self,
        session: Any,
        record: SubagentTaskRecord,
        now: datetime,
        lease_seconds: int,
    ) -> str:
        if recovery_candidate(record):
            # 同一进程内协程异常退出时，绑定的 runner 仍在，可直接接管；
            # 进程重启后没有该任务的 runner，允许一个短暂宽限期等待装配，
            # 超过后必须 fail-closed 标记 lost，不能让任务无限期停在 running。
            if self._recovery_runner_for(record.task_id) is None:
                return await defer_or_expire_missing_runner(self, session, record, now)
            updated = await self._claim_expired_for_recovery(session, record, now, lease_seconds)
            return "recovered" if updated else ""
        updated = await self._mark_expired_terminal(session, record, now)
        return "terminal" if updated else ""

    async def _claim_expired_for_recovery(
        self,
        session: Any,
        record: SubagentTaskRecord,
        now: datetime,
        lease_seconds: int,
    ) -> int:
        meta = dict(record.meta)
        recovery_kind = "pending_start" if record.status == TASK_STATUS_PENDING else "checkpoint"
        meta.update(
            {
                "execution_attempt": execution_attempt(meta) + 1,
                "recovery_count": recovery_count(meta) + 1,
                "last_recovery_at": now.isoformat(),
                "last_recovery_reason": "lease_expired",
                "recovery_kind": recovery_kind,
                "parent_owner_token": "",
            }
        )
        meta.pop("recovery_wait_started_at", None)
        if record.status == TASK_STATUS_PENDING:
            meta["started_at"] = now.isoformat()
        progress = dict(record.progress)
        progress.update({"phase": "recovering", "running_tools": 0, "updated_at": now.isoformat()})
        meta["progress"] = progress
        updated = await self._dao.update_expired(
            session,
            record.task_id,
            owner_token=record.owner_token or "",
            record_status=record.status,
            lease_expire_time=record.lease_expire_time or now,
            now=now,
            values={
                "record_status": TASK_STATUS_RUNNING,
                "owner_token": uuid4().hex,
                "lease_expire_time": now + timedelta(seconds=lease_seconds),
                "record_meta": _task_meta_text(meta),
                **({"claimed_time": now} if record.status == TASK_STATUS_PENDING else {}),
                "update_time": now,
                "update_by": "system",
                "update_by_name": "system",
            },
        )
        if updated:
            logger.warning(
                "后台子智能体失联任务已原子接管: task_id=%s kind=%s attempt=%s",
                record.task_id,
                recovery_kind,
                meta["execution_attempt"],
            )
        return updated

    async def _mark_expired_terminal(
        self,
        session: Any,
        record: SubagentTaskRecord,
        now: datetime,
    ) -> int:
        status, report = _expired_status_and_report(record.status)
        meta = dict(record.meta)
        meta.update({"finished_at": now.isoformat(), "phase": status, "error": "执行节点租约过期"})
        progress = dict(record.progress)
        progress.update({"phase": status, "running_tools": 0})
        meta["progress"] = progress
        updated = await self._dao.update_expired(
            session,
            record.task_id,
            owner_token=record.owner_token or "",
            record_status=record.status,
            lease_expire_time=record.lease_expire_time or now,
            now=now,
            values={
                "record_status": status,
                "record_content": report,
                "record_meta": _task_meta_text(meta),
                "owner_token": None,
                "lease_expire_time": None,
                **({"consumed_time": now} if not record.notify_on_finish else {}),
                "update_time": now,
                "update_by": "system",
                "update_by_name": "system",
            },
        )
        if updated:
            self._task_recovery_runners.pop(record.task_id, None)
            logger.warning(
                "后台子智能体任务租约过期回收: task_id=%s old_status=%s new_status=%s",
                record.task_id,
                record.status,
                status,
            )
        return updated

    async def _launch_recovered_task(self, record: SubagentTaskRecord) -> None:
        runner = self._recovery_runner_for(record.task_id)
        if runner is None:
            return
        runtime = recovery_runtime(record)
        if runtime is None and record.meta.get("recovery_kind") == "pending_start":
            await self.finish_task(
                record.task_id,
                record.owner_token or "",
                status=TASK_STATUS_LOST,
                report="任务启动前的恢复上下文缺失，已停止自动接管；请重新派发。",
                error="pending 恢复上下文缺失",
            )
            return
        task: asyncio.Task[None] = asyncio.create_task(
            runner(record, runtime),
            name=f"chatdata-subagent-recovery-{record.task_id}",
        )
        self.register_local_task(record.task_id, task)

        def _on_done(completed: asyncio.Task[None]) -> None:
            if completed.cancelled():
                return
            exc = completed.exception()
            if exc is not None:
                logger.error("后台子智能体恢复协程异常退出: task_id=%s", record.task_id, exc_info=exc)

        task.add_done_callback(_on_done)

    async def _notify_reaped_terminal(self, record: SubagentTaskRecord) -> None:
        if not record.notify_on_finish:
            return
        event_id = terminal_event_id(record.task_id)
        try:
            async with get_session() as session:
                now = await self._dao.now(session)
                await self._notification_service.cancel_pending_in_session(
                    session,
                    record.task_id,
                    APPROVAL_NOTIFICATION_EVENTS,
                    now,
                )
                await self._emit_event_in_session(
                    session,
                    record,
                    now,
                    event_id=event_id,
                    event_type=EVENT_TERMINAL,
                    wake_policy=WAKE_POLICY_WAKE,
                    content=self._notification_text(record),
                )
        except Exception:
            logger.error("回收子智能体终态事件写入失败: task_id=%s", record.task_id, exc_info=True)
            return
        await self._dispatch_event_best_effort(event_id)

    async def dispatch_pending_notifications(self, *, limit: int = 10) -> int:
        return await self._notification_service.dispatch_pending(limit=limit)

    async def _dispatch_event_best_effort(self, event_id: str | None) -> None:
        if not event_id:
            return
        try:
            await self._notification_service.dispatch_event(event_id)
        except Exception:
            logger.warning("子智能体事件即时派发失败，等待调度重试: event_id=%s", event_id, exc_info=True)

    async def _emit_event_in_session(
        self,
        session: Any,
        task: SubagentTaskRecord,
        now: datetime,
        *,
        event_id: str,
        event_type: str,
        wake_policy: str,
        content: str,
    ) -> Any:
        return await self._notification_service.emit_in_session(
            session,
            event_id=event_id,
            parent_run_id=task.parent_run_id,
            task_id=task.task_id,
            session_id=task.agent_session_id,
            conversation_id=task.conversation_id,
            user_id=task.user_id,
            event_type=event_type,
            task_status=task.status,
            wake_policy=wake_policy,
            content=content,
            meta=self._event_meta(task),
            now=now,
        )

    async def notify_user_message(self, task: SubagentTaskRecord, message: str) -> str | None:
        """用户直接与子智能体对话时，把消息作为 attach 事件同步给主智能体。"""
        excerpt = str(message or "").strip()[:1000]
        if not excerpt:
            return None
        event_id: str | None = None
        async with get_session() as session:
            now = await self._dao.now(session)
            event_id = lifecycle_event_id(EVENT_USER_MESSAGE, task.task_id, now)
            await self._emit_event_in_session(
                session,
                task,
                now,
                event_id=event_id,
                event_type=EVENT_USER_MESSAGE,
                wake_policy=WAKE_POLICY_ATTACH,
                content=_user_message_notification_text(task, excerpt),
            )
        await self._dispatch_event_best_effort(event_id)
        return event_id

    def _event_meta(self, task: SubagentTaskRecord) -> dict[str, Any]:
        return build_event_meta(
            operator_name=self._operator_name(task),
            subagent_type=task.subagent_type,
            execution_mode=task.execution_mode,
            turn_index=task.turn_index,
            label=task.label,
            cancel_origin=task.meta.get("cancel_origin"),
        )

    @staticmethod
    def _operator_name(task: SubagentTaskRecord) -> str:
        return str(task.meta.get("operator_name") or task.user_id or "系统")

    @staticmethod
    def _notification_text(task: SubagentTaskRecord) -> str:
        return terminal_notification_text(task)


def _expired_status_and_report(current_status: str) -> tuple[str, str]:
    if current_status == TASK_STATUS_CANCELLING:
        return TASK_STATUS_CANCELLED, "取消请求已生效（执行节点失联）。"
    if current_status == TASK_STATUS_PENDING:
        return TASK_STATUS_LOST, "任务未能在执行节点启动，已标记为执行节点失联。"
    return TASK_STATUS_LOST, "执行节点心跳租约过期，任务已中断；可重新派发。"


subagent_task_service = SubagentTaskService()
