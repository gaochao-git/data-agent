"""``subagent(send=...)`` 的状态分流。"""

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
async def test_send_to_waiting_approval_points_to_cancel(harness):
    """等待审批是历史/非标准状态；模型侧只能取消后重新派发。"""
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

    with pytest.raises(ValueError, match="取消"):
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
