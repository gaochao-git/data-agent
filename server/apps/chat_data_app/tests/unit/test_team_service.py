"""Persistent Agent Team MVP tests: roster, multi-turn send, task CAS."""

from __future__ import annotations

import pytest

from apps.chat_data_app.agent.middleware.subagents import SubagentSpec
from apps.chat_data_app.agent.team.service import TeamError, TeamService
from apps.chat_data_app.agent.team.store import TeamStore


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, member, message, _state):
        self.calls.append((member.name, message))
        return f"reply-{member.name}-{len(self.calls)}"


def _service(tmp_path, executor):
    specs = {"general-purpose": SubagentSpec(name="general-purpose", description="test", system_prompt="test")}
    return TeamService(
        conversation_id="conv-1",
        user_id="user-1",
        specs=specs,
        checkpointer=object(),
        store=TeamStore(base_dir=tmp_path),
        execute=executor,
    )


@pytest.mark.asyncio
async def test_spawn_and_multi_turn_send(tmp_path):
    executor = FakeExecutor()
    service = _service(tmp_path, executor)

    spawned = await service.spawn(
        name="reviewer",
        description="reviewer",
        prompt="first",
        subagent_type="general-purpose",
    )
    assert spawned["status"] == "active"
    second = await service.send(target="reviewer", message="second")
    assert second["reply"].startswith("reply-reviewer-2")
    assert executor.calls == [("reviewer", "first"), ("reviewer", "second")]
    assert service.list_members()[0]["last_message"] == second["reply"]


@pytest.mark.asyncio
async def test_spawn_rejects_duplicate_and_bad_name(tmp_path):
    service = _service(tmp_path, FakeExecutor())
    await service.spawn(name="reviewer", description="r", prompt="p", subagent_type="general-purpose")

    with pytest.raises(TeamError):
        await service.spawn(name="reviewer", description="r", prompt="p", subagent_type="general-purpose")
    with pytest.raises(TeamError):
        await service.spawn(name="Bad Name", description="r", prompt="p", subagent_type="general-purpose")


@pytest.mark.asyncio
async def test_task_cas_and_dependencies(tmp_path):
    service = _service(tmp_path, FakeExecutor())
    first = await service.create_task(subject="first")
    second = await service.create_task(subject="second", blocked_by=[first["id"]])

    with pytest.raises(TeamError, match="依赖未完成"):
        await service.update_task(task_id=second["id"], action="claim", expected_revision=1)

    claimed = await service.update_task(task_id=first["id"], action="claim", expected_revision=1, owner="lead")
    assert claimed["status"] == "in_progress"
    completed = await service.update_task(task_id=first["id"], action="complete", expected_revision=2)
    assert completed["status"] == "completed"
    ready = service.get_task(second["id"])
    assert ready["ready"] is True

    with pytest.raises(TeamError, match="revision 过期"):
        await service.update_task(task_id=second["id"], action="claim", expected_revision=99)


@pytest.mark.asyncio
async def test_task_dependency_cycle_rejected(tmp_path):
    service = _service(tmp_path, FakeExecutor())
    first = await service.create_task(subject="first")
    second = await service.create_task(subject="second", blocked_by=[first["id"]])

    with pytest.raises(TeamError, match="循环依赖"):
        await service.update_task(
            task_id=first["id"],
            action="set_dependencies",
            expected_revision=1,
            blocked_by=[second["id"]],
        )


@pytest.mark.asyncio
async def test_stale_provisioning_is_reconciled_to_failed(tmp_path):
    store = TeamStore(base_dir=tmp_path)
    state = store.load("conv-stale")
    from apps.chat_data_app.agent.team.models import TeamMember

    state.members.append(
        TeamMember(
            name="stale",
            description="stale",
            subagent_type="general-purpose",
            thread_id="team:conv-stale:stale",
            status="provisioning",
            updated_at="2020-01-01T00:00:00+00:00",
        )
    )
    store.save("conv-stale", state)

    await TeamService.reconcile_stale_provisioning("conv-stale", store=store)

    assert store.load("conv-stale").members[0].status == "failed"
