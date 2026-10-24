"""message_integrity 残余分支补测：工具调用 ID 归集、边界与修复辅助函数。"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from apps.chat_data_app.agent.middleware import message_integrity as mi


def _ai(tool_calls, message_id=None):
    return AIMessage.model_construct(content="", id=message_id, tool_calls=tool_calls)


def test_message_tool_call_ids_skips_non_mapping_entries():
    message = _ai(["junk", {"id": "call-1", "name": "run_sql", "args": {}}])

    assert mi.message_tool_call_ids(message) == ("call-1",)


def test_message_tool_call_ids_deduplicates_repeated_ids():
    message = _ai(
        [
            {"id": "call-1", "name": "run_sql", "args": {}},
            {"id": "call-1", "name": "run_sql", "args": {}},
        ]
    )

    assert mi.message_tool_call_ids(message) == ("call-1",)


def test_collect_ai_group_result_ids_skips_unrelated_tool_message():
    messages = [
        _ai([{"id": "call-1", "name": "run_sql", "args": {}}]),
        ToolMessage(content="其他结果", tool_call_id="call-2"),
    ]

    result_ids, next_index = mi._collect_ai_group_result_ids(messages, 0, ("call-1",))

    assert result_ids == set()
    assert next_index == 2


def test_complete_boundaries_skips_ai_group_without_ids():
    messages = [
        _ai([{"name": "run_sql", "args": {}}]),
        HumanMessage(content="下一问"),
    ]

    assert mi.complete_message_boundaries(messages) == [2]


def test_repair_removes_orphan_tool_message():
    human = HumanMessage(content="问题")
    messages = [ToolMessage(content="孤儿结果", tool_call_id="call-9"), human]

    repaired, report = mi.repair_tool_call_messages(messages)

    assert repaired == [human]
    assert report["removed_message_count"] == 1
    assert report["repaired"] is True


def test_skip_orphan_tool_results_stops_at_unrelated_id():
    messages = [
        _ai([{"id": "call-1", "name": "run_sql", "args": {}}]),
        ToolMessage(content="别的结果", tool_call_id="call-2"),
    ]

    index, removed = mi._skip_orphan_tool_results(messages, 1, ("call-1",))

    assert index == 1
    assert removed == 0
