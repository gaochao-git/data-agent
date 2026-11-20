"""``subagent(send=...)`` 与 ``subagent(decide=...)`` 的状态分流。"""

from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.middleware import subagents as subagents_module
from apps.chat_data_app.tests.unit.subagent_test_support import (
    CONV,
    USER,
    build_subagent_tool,
    invoke_subagent_tool,
)


def _spec():
    return subagents_module.SubagentSpec(
        name="explore",
        description="深度分析子智能体",
        system_prompt="你是分析子智能体",
        tools=[],
        model=object(),
    )


def _fake_runtime(conversation_id=CONV, tool_call_id="call_bg_1"):
    return SimpleNamespace(
        state={
            "conversation_id": conversation_id,
            "user_id": USER,
            "operator_name": "用户一",
            "run_id": "run-parent",
        },
        tool_call_id=tool_call_id,
    )


async def _create_task(harness):
    return await harness.service.create_task(
        conversation_id=CONV,
        user_id=USER,
        parent_run_id="run-parent",
        subagent_type="explore",
        description="深度分析近 30 天入金趋势",
        label="入金趋势",
        parent_tool_call_id="call-parent",
        parent_owner_token="parent-owner",
        operator_name="用户一",
    )


async def _start(harness, record):
    started = await harness.service.start_task(record.task_id, record.owner_token or "")
    assert started is not None
    return started


@pytest.mark.asyncio
async def test_send_to_running_task_becomes_steer(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)
    tool = build_subagent_tool([_spec()])

    result = await invoke_subagent_tool(
        tool, "send", task_id=record.task_id, message="补充渠道拆分", runtime=_fake_runtime()
    )

    assert "插话已排队" in result
    records = await harness.service.list_tasks(CONV, USER)
    assert len(records) == 1
    queued = await harness.service.claim_steer_messages(record.task_id, record.owner_token or "")
    assert queued == ["补充渠道拆分"]


@pytest.mark.asyncio
async def test_send_to_waiting_approval_requires_decide(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)
    await harness.service.suspend_task(
        record.task_id,
        record.owner_token or "",
        status="waiting_approval",
        interrupt_payload={"kind": "approval", "approval_items": []},
    )
    tool = build_subagent_tool([_spec()])

    with pytest.raises(ValueError, match="decide"):
        await invoke_subagent_tool(
            tool, "send", task_id=record.task_id, message="补充渠道拆分", runtime=_fake_runtime()
        )


@pytest.mark.asyncio
async def test_send_rejects_round_options_while_task_running(harness):
    await harness.create_conversation()
    record = await _create_task(harness)
    await _start(harness, record)
    tool = build_subagent_tool([_spec()])

    with pytest.raises(ValueError, match="background"):
        await invoke_subagent_tool(
            tool,
            "send",
            task_id=record.task_id,
            message="补充",
            background=True,
            runtime=_fake_runtime(),
        )
    with pytest.raises(ValueError, match="write_scopes"):
        await invoke_subagent_tool(
            tool,
            "send",
            task_id=record.task_id,
            message="补充",
            write_scopes=["outputs/a.md"],
            runtime=_fake_runtime(),
        )


@pytest.mark.asyncio
async def test_decide_maps_allow_and_deny_to_approval_resume(harness, monkeypatch):
    launched = []
    monkeypatch.setattr(
        subagents_module,
        "_launch_task_runner",
        lambda *args, **kwargs: launched.append(args),
    )
    tool = build_subagent_tool([_spec()])
    await harness.create_conversation()
    captured_values = []
    original_resume = harness.service.resume_task

    async def spy(task_id, **kwargs):
        captured_values.append(kwargs["resume_value"])
        return await original_resume(task_id, **kwargs)

    monkeypatch.setattr(harness.service, "resume_task", spy)

    for decision, label, selected in (
        ("allow", "已批准", "允许"),
        ("deny", "已拒绝", "拒绝"),
    ):
        record = await _create_task(harness)
        await _start(harness, record)
        await harness.service.suspend_task(
            record.task_id,
            record.owner_token or "",
            status="waiting_approval",
            interrupt_payload={"kind": "approval", "approval_items": []},
        )

        result = await invoke_subagent_tool(
            tool, "decide", task_id=record.task_id, decision=decision, runtime=_fake_runtime()
        )

        assert label in result
        assert captured_values[-1] == {"answers": [{"id": "approval", "selected": [selected]}]}
        refreshed = await harness.service.get_task(record.task_id, conversation_id=CONV, user_id=USER)
        assert refreshed is not None and refreshed.status == "running"

    assert len(launched) == 2
