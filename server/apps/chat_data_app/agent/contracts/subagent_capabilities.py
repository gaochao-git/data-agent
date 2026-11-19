"""子智能体任务能力矩阵：由任务模式与状态统一推导允许的控制动作。

工具的各个分支不再各写一套 ``status == ...`` 判断；展示层、控制层和执行层
共用这里，避免出现「UI 显示可取消、后端拒绝」或「同步任务排队插话但永远
不消费」这类假契约。
"""

from __future__ import annotations

from dataclasses import dataclass

from .subagent_task import (
    ACTIVE_TASK_STATUSES,
    SESSION_STATUS_CLOSED,
    TASK_STATUS_CANCELLING,
    TASK_STATUS_WAITING_APPROVAL,
    TERMINAL_TASK_STATUSES,
    SubagentTaskView,
)


@dataclass(frozen=True)
class SubagentTaskCapabilities:
    """单个任务在当前模式下允许的动作。"""

    can_cancel: bool
    can_steer: bool
    can_approve: bool
    can_reject: bool
    can_follow_up: bool

    def labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        if self.can_cancel:
            labels.append("取消")
        if self.can_steer:
            labels.append("插话")
        if self.can_approve:
            labels.append("批准")
        if self.can_reject:
            labels.append("拒绝")
        if self.can_follow_up:
            labels.append("追问")
        return tuple(labels)

    def actions_text(self) -> str:
        return "、".join(self.labels()) or "无"

    def describe(self) -> str:
        return f"可操作：{self.actions_text()}"


def resolve_task_capabilities(
    task: SubagentTaskView,
    *,
    session_status: str | None = None,
) -> SubagentTaskCapabilities:
    """按执行模式 + 当前状态推导能力；未知模式按后台处理（兼容旧任务）。"""
    status = str(getattr(task, "status", "") or "")
    terminal = bool(getattr(task, "is_terminal", status in TERMINAL_TASK_STATUSES))
    mode = str(getattr(task, "execution_mode", "") or "background")
    background = mode != "sync"
    active = status in ACTIVE_TASK_STATUSES
    session_closed = str(session_status or "").strip() == SESSION_STATUS_CLOSED
    # 取消中不再重复取消/插话；审批只存在于后台任务（同步子图没有审批中间件）。
    settled = terminal or status == TASK_STATUS_CANCELLING
    waiting_approval = status == TASK_STATUS_WAITING_APPROVAL
    return SubagentTaskCapabilities(
        can_cancel=not session_closed and active and not settled,
        can_steer=not session_closed and background and active and not settled,
        can_approve=not session_closed and background and waiting_approval,
        can_reject=not session_closed and background and waiting_approval,
        can_follow_up=not session_closed and terminal,
    )
