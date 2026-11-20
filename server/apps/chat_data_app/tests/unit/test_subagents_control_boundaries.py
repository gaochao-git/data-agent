"""子智能体控制边界：匿名 id 跨链路一致性与同步任务控制能力。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from apps.chat_data_app.agent.middleware import subagents as subagents_module


def test_anonymous_equal_messages_keep_distinct_ids_across_live_and_snapshot():
    """相同内容的不同消息不能合并；独立反序列化的快照仍与直播 ID 对齐。"""
    live = [
        AIMessage(content="同一段结论", additional_kwargs={"reasoning_content": "同一段思考"}),
        AIMessage(content="同一段结论", additional_kwargs={"reasoning_content": "同一段思考"}),
    ]
    snapshot = [
        AIMessage(content="同一段结论", additional_kwargs={"reasoning_content": "同一段思考"}),
        AIMessage(content="同一段结论", additional_kwargs={"reasoning_content": "同一段思考"}),
    ]
    stream_state = subagents_module._SubagentStreamState("call-1")

    live_ids = [stream_state.resolve_anonymous_id(message) for message in live]
    trail = subagents_module._build_trail_snapshot(snapshot)

    assert live_ids[0] != live_ids[1]
    assert live_ids[0].startswith("anon-")
    assert [item["id"] for item in trail] == live_ids
    assert [message.id for message in live] == live_ids


def test_follow_up_history_occurrence_stays_aligned_with_snapshot():
    """跨轮追问：历史里的同内容无 id 消息先占序号，新消息不能与它撞 key。"""
    history = [AIMessage(content="同一段结论", additional_kwargs={"reasoning_content": "同一段思考"})]
    stream_state = subagents_module._SubagentStreamState("call-1")
    stream_state.prime_state_messages(history)

    new_message = AIMessage(content="同一段结论", additional_kwargs={"reasoning_content": "同一段思考"})
    new_id = stream_state.resolve_anonymous_id(new_message)

    snapshot_history = [AIMessage(content="同一段结论", additional_kwargs={"reasoning_content": "同一段思考"})]
    trail = subagents_module._build_trail_snapshot([*snapshot_history, new_message])

    assert new_id != subagents_module.anonymous_message_id(history[0])
    assert [item["id"] for item in trail] == [subagents_module.anonymous_message_id(history[0]), new_id]


def test_primed_history_message_keeps_its_reserved_id_when_resolved_again():
    history_message = AIMessage(content="历史结论")
    stream_state = subagents_module._SubagentStreamState("call-1")
    stream_state.prime_state_messages([history_message])

    assert stream_state.resolve_anonymous_id(history_message) == subagents_module.anonymous_message_id(history_message)


def test_anonymous_id_snapshot_handles_partially_assigned_equal_messages():
    first = AIMessage(content="继续")
    stream_state = subagents_module._SubagentStreamState("call-1")
    first_id = stream_state.resolve_anonymous_id(first)

    trail = subagents_module._build_trail_snapshot([first, AIMessage(content="继续")])

    assert len(trail) == 2
    assert trail[0]["id"] == first_id
    assert trail[1]["id"] != first_id


@pytest.mark.asyncio
async def test_steer_rejects_sync_task_before_enqueue():
    """同步任务没有控制中间件，排队也不会被消费，必须直接拒绝。"""

    class _Service:
        async def enqueue_steer_message(self, *_args, **_kwargs):
            raise AssertionError("sync task must not enqueue steer")

    target = SimpleNamespace(status="running", is_terminal=False, execution_mode="sync")

    result = await subagents_module._control_steer(
        _Service(),
        target,
        "task-1",
        conversation_id="c1",
        user_id="u1",
        message="补充说明",
        operator_name="tester",
    )

    assert "不支持插话" in result
