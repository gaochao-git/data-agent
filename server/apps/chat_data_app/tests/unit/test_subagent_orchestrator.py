"""统一子智能体命令门面：动作边界与路由。"""

import pytest

from apps.chat_data_app.agent.middleware.subagent_orchestrator import (
    SubagentCommand,
    SubagentCommandHandlers,
    SubagentOrchestrator,
)


def _orchestrator(calls: list[tuple[str, str]]):
    def handler(name: str):
        async def _handle(command: SubagentCommand):
            calls.append((name, command.action))
            return name

        return _handle

    return SubagentOrchestrator(
        SubagentCommandHandlers(
            start=handler("start"),
            follow_up=handler("follow_up"),
            task_admin=handler("task_admin"),
            control=handler("control"),
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("start", "start"),
        ("follow_up", "follow_up"),
        ("list", "task_admin"),
        ("check", "task_admin"),
        ("wait", "task_admin"),
        ("cancel", "task_admin"),
        ("steer", "control"),
        ("approve", "control"),
        ("reject", "control"),
    ],
)
async def test_orchestrator_routes_every_supported_action(action, expected):
    calls: list[tuple[str, str]] = []

    result = await _orchestrator(calls).execute(SubagentCommand(action=action))

    assert result == expected
    assert calls == [(expected, action)]


@pytest.mark.asyncio
async def test_orchestrator_rejects_agent_id_for_non_list_actions():
    calls: list[tuple[str, str]] = []

    result = await _orchestrator(calls).execute(SubagentCommand(action="check", agent_id="agent-1"))

    assert "agent_id 仅用于" in result
    assert calls == []


@pytest.mark.asyncio
async def test_orchestrator_rejects_unknown_action_without_dispatch():
    calls: list[tuple[str, str]] = []

    result = await _orchestrator(calls).execute(SubagentCommand(action="unknown"))

    assert "不支持的操作" in result
    assert calls == []
