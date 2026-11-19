"""子智能体能力矩阵：模式 + 状态推导出的动作必须与执行体一致。"""

from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.contracts.subagent_capabilities import resolve_task_capabilities
from apps.chat_data_app.agent.contracts.subagent_task import (
    SESSION_STATUS_CLOSED,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_CANCELLING,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
)


def _task(status: str, *, mode: str = "background", terminal: bool = False):
    return SimpleNamespace(status=status, execution_mode=mode, is_terminal=terminal)


@pytest.mark.parametrize(
    ("status", "mode", "terminal", "expected"),
    [
        (TASK_STATUS_RUNNING, "background", False, ("取消", "插话")),
        (TASK_STATUS_RUNNING, "sync", False, ("取消",)),
        (TASK_STATUS_WAITING_APPROVAL, "background", False, ("取消", "插话", "批准", "拒绝")),
        (TASK_STATUS_CANCELLING, "background", False, ()),
        (TASK_STATUS_COMPLETED, "background", True, ("追问",)),
        (TASK_STATUS_CANCELLED, "sync", True, ("追问",)),
    ],
)
def test_capability_matrix(status, mode, terminal, expected):
    capabilities = resolve_task_capabilities(_task(status, mode=mode, terminal=terminal))

    assert capabilities.labels() == expected
    assert capabilities.actions_text() == ("、".join(expected) or "无")


def test_unknown_execution_mode_falls_back_to_background():
    """旧任务没有 execution_mode，按后台能力处理，避免误判为同步后禁用插话。"""
    capabilities = resolve_task_capabilities(_task(TASK_STATUS_RUNNING, mode=""))

    assert capabilities.can_steer is True


def test_missing_is_terminal_falls_back_to_status():
    """旧只读视图可能没有 is_terminal，终态仍要允许追问。"""
    task = SimpleNamespace(status=TASK_STATUS_COMPLETED, execution_mode="background")

    capabilities = resolve_task_capabilities(task)

    assert capabilities.can_follow_up is True
    assert capabilities.can_cancel is False


def test_capability_describe_is_stable_for_tool_output():
    capabilities = resolve_task_capabilities(_task(TASK_STATUS_COMPLETED, terminal=True))

    assert capabilities.describe() == "可操作：追问"


def test_closed_session_removes_all_capabilities():
    capabilities = resolve_task_capabilities(
        _task(TASK_STATUS_COMPLETED, terminal=True),
        session_status=SESSION_STATUS_CLOSED,
    )

    assert capabilities.actions_text() == "无"
