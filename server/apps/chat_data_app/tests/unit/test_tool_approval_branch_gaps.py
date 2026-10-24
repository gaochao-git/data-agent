"""tool_approval 覆盖率补充：已决策调用、代理目标解析与答案结构容错。"""

import asyncio
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from apps.chat_data_app.agent.middleware import tool_approval
from apps.chat_data_app.agent.middleware.tool_approval import (
    ToolApprovalMiddleware,
    _approval_target,
    _has_proxy_target,
    _selected_label,
    _string_set,
)


def test_string_set_supports_dict_list_and_invalid_state():
    assert _string_set({"decisions": {"a": "allow", "b": "deny"}}, "decisions") == {"a", "b"}
    assert _string_set({"decisions": ["a", 2]}, "decisions") == {"a", "2"}
    assert _string_set(None, "decisions") == set()
    assert _string_set({"decisions": "a"}, "decisions") == set()


def test_pending_call_skips_already_decided_call():
    middleware = ToolApprovalMiddleware(approval_policies={"write_file": "ask"})
    call = {"id": "call_1", "name": "write_file"}
    assert middleware._pending_call(call, 0, {"call_1"}) is None
    assert middleware._pending_call(call, 0, set()) == {
        "call_id": "call_1",
        "tool_name": "write_file",
        "tool_call": call,
    }


def test_pending_call_generates_id_when_missing():
    middleware = ToolApprovalMiddleware(approval_policies={"write_file": "ask"})
    pending = middleware._pending_call({"name": "write_file"}, 3, set())
    assert pending["call_id"] == "write_file:3"


def test_has_proxy_target_rejects_non_dict():
    assert _has_proxy_target(None) is False
    assert _has_proxy_target({"args": {"name": "mcp_demo_tool"}}) is True
    assert _has_proxy_target({"args": {"name": "mcp_tool_search"}}) is False


def test_approval_target_keeps_original_call_for_invalid_proxy_shapes():
    non_proxy = {"id": "c1", "name": "write_file", "args": {"name": "mcp_x"}}
    assert _approval_target("write_file", non_proxy) == ("write_file", non_proxy)

    bad_args = {"id": "c1", "name": "mcp_tool_call", "args": ["mcp_x"]}
    assert _approval_target("mcp_tool_call", bad_args) == ("mcp_tool_call", bad_args)

    non_mcp_target = {"id": "c1", "name": "mcp_tool_call", "args": {"name": "other_tool"}}
    assert _approval_target("mcp_tool_call", non_mcp_target) == ("mcp_tool_call", non_mcp_target)


def test_approval_target_carries_intent_into_effective_args():
    proxy = {
        "id": "c1",
        "name": "mcp_tool_call",
        "args": {"name": "mcp_demo_tool", "input": {"path": "/a"}, "intent": "读取文件"},
    }
    target_name, effective = _approval_target("mcp_tool_call", proxy)
    assert target_name == "mcp_demo_tool"
    assert effective["args"] == {"path": "/a", "intent": "读取文件"}
    assert effective is not proxy


def test_selected_label_handles_invalid_answer_shapes():
    assert _selected_label(None) == ""
    assert _selected_label({"cancelled": True}) == ""
    assert _selected_label({"answers": "not-a-list"}) == ""
    assert _selected_label({"answers": ["not-a-dict"]}) == ""
    assert _selected_label({"answers": [{"id": "other", "selected": ["允许"]}]}) == ""
    assert _selected_label({"answers": [{"id": tool_approval._APPROVAL_QUESTION_ID, "selected": []}]}) == ""
    assert _selected_label({"answers": []}) == ""


def test_selected_label_returns_selected_option():
    answer = {"answers": [{"id": tool_approval._APPROVAL_QUESTION_ID, "selected": ["允许"]}]}
    assert _selected_label(answer) == "允许"


def test_async_wrap_skips_proxy_call_without_target():
    middleware = ToolApprovalMiddleware(approval_policies={})
    request = SimpleNamespace(
        tool_call={"id": "c1", "name": "mcp_tool_call", "args": {}},
        state={},
    )
    message = ToolMessage(content="ok", name="mcp_tool_call", tool_call_id="c1")

    async def _handler(_request):
        return message

    assert asyncio.run(middleware.awrap_tool_call(request, _handler)) is message
