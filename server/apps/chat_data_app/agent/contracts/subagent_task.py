"""后台子智能体任务账本契约。

middleware 只依赖本契约：任务状态、展示标签与 Port 方法都由 run 层实现，
避免 `middleware -> run` 的反向包级依赖。具体实现由 run 模块在导入时注册。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Protocol

TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_CANCELLING = "cancelling"
TASK_STATUS_PAUSED = "paused"
TASK_STATUS_WAITING_APPROVAL = "waiting_approval"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELLED = "cancelled"
TASK_STATUS_TIMEOUT = "timeout"
TASK_STATUS_INTERRUPTED = "interrupted"
TASK_STATUS_LOST = "lost"

# 「活动」用于容量与同会话互斥：暂停/等待审批的任务仍占住该子会话，
# 不允许主智能体在未处理前再开一轮，否则追问链会分叉。
SUSPENDED_TASK_STATUSES = frozenset({TASK_STATUS_PAUSED, TASK_STATUS_WAITING_APPROVAL})
EXECUTING_TASK_STATUSES = frozenset({TASK_STATUS_PENDING, TASK_STATUS_RUNNING, TASK_STATUS_CANCELLING})
ACTIVE_TASK_STATUSES = EXECUTING_TASK_STATUSES | SUSPENDED_TASK_STATUSES
TERMINAL_TASK_STATUSES = frozenset(
    {
        TASK_STATUS_COMPLETED,
        TASK_STATUS_FAILED,
        TASK_STATUS_CANCELLED,
        TASK_STATUS_TIMEOUT,
        TASK_STATUS_INTERRUPTED,
        TASK_STATUS_LOST,
    }
)
TASK_STATUS_LABELS = {
    TASK_STATUS_PENDING: "排队中",
    TASK_STATUS_RUNNING: "执行中",
    TASK_STATUS_CANCELLING: "正在取消",
    TASK_STATUS_PAUSED: "已暂停",
    TASK_STATUS_WAITING_APPROVAL: "等待审批",
    TASK_STATUS_COMPLETED: "已完成",
    TASK_STATUS_FAILED: "失败",
    TASK_STATUS_CANCELLED: "已取消",
    TASK_STATUS_TIMEOUT: "已超时",
    TASK_STATUS_INTERRUPTED: "已中断",
    TASK_STATUS_LOST: "执行节点失联",
}
TASK_HEARTBEAT_SECONDS = 5


class SubagentTaskCapacityError(RuntimeError):
    """当前会话后台任务已达到并发或子智能体总数上限。"""


def parse_task_status_filter(raw: str | None) -> set[str] | None:
    """把工具/API 的 status 参数归一成状态集合；未知值按空过滤处理。"""
    value = (raw or "").strip().lower()
    if not value or value == "all":
        return None
    if value == "active":
        return set(ACTIVE_TASK_STATUSES)
    if value == "suspended":
        return set(SUSPENDED_TASK_STATUSES)
    if value == "terminal":
        return set(TERMINAL_TASK_STATUSES)
    statuses = {item.strip() for item in value.split(",") if item.strip()}
    return statuses or None


class SubagentTaskView(Protocol):
    """任务账本只读视图；middleware 需要的字段子集。"""

    @property
    def task_id(self) -> str: ...

    @property
    def conversation_id(self) -> str: ...

    @property
    def user_id(self) -> str: ...

    @property
    def owner_token(self) -> str | None: ...

    @property
    def status(self) -> str: ...

    @property
    def subagent_type(self) -> str: ...

    @property
    def label(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def report(self) -> str: ...

    @property
    def report_path(self) -> str: ...

    @property
    def parent_task_id(self) -> str: ...

    @property
    def agent_id(self) -> str: ...

    @property
    def agent_session_id(self) -> str: ...

    @property
    def parent_agent_id(self) -> str: ...

    @property
    def root_agent_id(self) -> str: ...

    @property
    def depth(self) -> int: ...

    @property
    def root_task_id(self) -> str: ...

    @property
    def turn_index(self) -> int: ...

    @property
    def error(self) -> str: ...

    @property
    def progress(self) -> dict[str, Any]: ...

    @property
    def meta(self) -> dict[str, Any]: ...

    @property
    def timeout_seconds(self) -> int: ...

    @property
    def execution_mode(self) -> str: ...

    @property
    def notify_on_finish(self) -> bool: ...

    @property
    def is_terminal(self) -> bool: ...


class SubagentTaskPort(Protocol):
    """任务生命周期与查询端口；实现见 `run/subagent_task_service.py`。"""

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
        parent_task_id: str | None = None,
        agent_session_id: str | None = None,
        root_task_id: str | None = None,
    ) -> SubagentTaskView: ...

    async def configure_task_execution(
        self,
        task_id: str,
        owner_token: str,
        *,
        execution_mode: str,
    ) -> SubagentTaskView | None: ...

    async def get_task(
        self,
        task_id: str,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> SubagentTaskView | None: ...

    async def list_tasks(
        self,
        conversation_id: str,
        user_id: str,
        *,
        statuses: set[str] | None = None,
        limit: int = 50,
    ) -> Sequence[SubagentTaskView]: ...

    async def start_task(self, task_id: str, owner_token: str) -> SubagentTaskView | None: ...

    async def heartbeat_task(
        self,
        task_id: str,
        owner_token: str,
        progress: dict[str, Any],
    ) -> str | None: ...

    async def request_pause(
        self,
        task_id: str,
        *,
        conversation_id: str,
        user_id: str,
        operator_name: str,
    ) -> SubagentTaskView | None: ...

    async def is_pause_requested(self, task_id: str, owner_token: str) -> bool: ...

    async def enqueue_steer_message(
        self,
        task_id: str,
        *,
        conversation_id: str,
        user_id: str,
        message: str,
        operator_name: str,
    ) -> SubagentTaskView | None: ...

    async def claim_steer_messages(self, task_id: str, owner_token: str) -> list[str]: ...

    async def suspend_task(
        self,
        task_id: str,
        owner_token: str,
        *,
        status: str,
        interrupt_payload: dict[str, Any] | None = None,
    ) -> SubagentTaskView | None: ...

    async def resume_task(
        self,
        task_id: str,
        *,
        conversation_id: str,
        user_id: str,
        resume_value: Any,
    ) -> tuple[SubagentTaskView, Any] | None: ...

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
    ) -> SubagentTaskView | None: ...

    async def load_session_messages(
        self,
        session_id: str,
        *,
        conversation_id: str,
        user_id: str,
        expected_turn_index: int | None = None,
    ) -> Sequence[dict[str, Any]]: ...

    async def find_active_task_in_session(
        self,
        session_id: str,
        *,
        conversation_id: str,
        user_id: str,
    ) -> SubagentTaskView | None: ...

    async def get_latest_task_in_session(
        self,
        session_id: str,
        *,
        conversation_id: str,
        user_id: str,
    ) -> SubagentTaskView | None: ...

    async def cancel_task(
        self,
        conversation_id: str,
        user_id: str,
        task_id: str,
        operator_name: str,
    ) -> SubagentTaskView | None: ...

    async def mark_viewed(self, record: SubagentTaskView) -> None: ...

    async def dispatch_pending_notifications(self, *, limit: int = 10) -> int: ...

    def snapshot_runtime(self, runtime: Any) -> Any: ...

    def register_local_task(self, task_id: str, task: asyncio.Task[Any]) -> None: ...

    def create_event_writer(self, task: SubagentTaskView) -> Any: ...


_service: SubagentTaskPort | None = None


def register_subagent_task_service(service: SubagentTaskPort) -> None:
    """由 run 实现注册默认账本服务；重复注册以最后一次为准。"""
    global _service
    _service = service


def get_subagent_task_service() -> SubagentTaskPort | None:
    return _service


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "SubagentTaskCapacityError",
    "SubagentTaskPort",
    "SubagentTaskView",
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
    "get_subagent_task_service",
    "parse_task_status_filter",
    "register_subagent_task_service",
]
