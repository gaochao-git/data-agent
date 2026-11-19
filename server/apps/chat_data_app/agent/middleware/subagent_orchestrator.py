"""子智能体统一命令门面。

工具层只负责把模型入参归一成 ``SubagentCommand``，所有动作由这里按命令族
分发；具体执行仍由既有同步/后台适配器承担。门面不重写终态语义，只保证
“一个入口、一套动作集合、一致的参数边界”，后续新增控制命令不会散落到
``atask`` 的 if 分支里。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

ADMIN_ACTIONS = frozenset({"list", "check", "wait", "cancel"})
CONTROL_ACTIONS = frozenset({"steer", "approve", "reject"})
SUPPORTED_ACTIONS = ADMIN_ACTIONS | CONTROL_ACTIONS | {"start", "follow_up"}


@dataclass(frozen=True)
class SubagentCommand:
    """``subagent`` 工具的归一命令；字段与 TaskToolArgs 一一对应。"""

    action: str
    description: str | None = None
    subagent_type: str | None = None
    background: bool = True
    label: str | None = None
    timeout_seconds: int | None = None
    task_id: str | None = None
    message: str | None = None
    task_ids: list[str] | None = None
    agent_id: str | None = None
    status: str | None = None
    decision: str = "allow"
    write_scopes: list[str] | None = None
    wait_seconds: int = 30
    # 工具运行时由 atask 注入，不来自模型参数；不放进 args_schema。
    runtime: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class SubagentCommandHandlers:
    """命令族处理器；由 build_task_tool 注入，门面本身不依赖执行实现。"""

    start: Callable[[SubagentCommand], Awaitable[Any]]
    follow_up: Callable[[SubagentCommand], Awaitable[Any]]
    task_admin: Callable[[SubagentCommand], Awaitable[Any]]
    control: Callable[[SubagentCommand], Awaitable[Any]]


class SubagentOrchestrator:
    """唯一命令入口：校验动作与参数边界，再交给对应处理器。"""

    def __init__(self, handlers: SubagentCommandHandlers) -> None:
        self._handlers = handlers

    async def execute(self, command: SubagentCommand) -> Any:
        action = str(command.action or "").strip()
        if action not in SUPPORTED_ACTIONS:
            return f"不支持的操作：{action}"
        if command.agent_id and action != "list":
            return 'agent_id 仅用于 action="list" 查询任务明细；check/cancel/follow_up 等动作请传 task_id。'
        if action == "start":
            return await self._handlers.start(command)
        if action == "follow_up":
            return await self._handlers.follow_up(command)
        if action in CONTROL_ACTIONS:
            return await self._handlers.control(command)
        return await self._handlers.task_admin(command)


__all__ = [
    "ADMIN_ACTIONS",
    "CONTROL_ACTIONS",
    "SUPPORTED_ACTIONS",
    "SubagentCommand",
    "SubagentCommandHandlers",
    "SubagentOrchestrator",
]
