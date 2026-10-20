"""按需审批中间件单测：载荷形状、答案解析、ask 模式放行/拒绝与 fail-closed。"""

import os
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.agent.middleware import tool_approval
from apps.chat_data_app.agent.middleware.tool_approval import (
    APPROVAL_ALLOW_LABEL,
    APPROVAL_DENY_LABEL,
    build_approval_payload,
    create_tool_approval_middleware,
    is_approval_allowed,
)


def _request(tool_name: str = "file_delete") -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"id": "call-1", "name": tool_name, "args": {"path": "/mnt/user-data/workspace/a.txt"}},
    )


async def _ok_handler(_request):
    return ToolMessage(
        content="done", name=str(getattr(_request, "tool_call", {}).get("name") or ""), tool_call_id="call-1"
    )


def _answer(selected: list[str]) -> dict:
    return {"answers": [{"id": "approval", "selected": selected, "custom": "", "skipped": False}]}


def test_build_approval_payload_is_question_shaped():
    payload = build_approval_payload("file_delete", {"args": {"path": "/mnt/user-data/workspace/a.txt"}})
    assert payload["kind"] == "approval"
    assert payload["tool_name"] == "file_delete"
    assert "/mnt/user-data/workspace/a.txt" in payload["tool_args_preview"]
    question = payload["questions"][0]
    assert question["id"] == "approval"
    assert question["multi_select"] is False
    assert [option["label"] for option in question["options"]] == [APPROVAL_ALLOW_LABEL, APPROVAL_DENY_LABEL]


def test_is_approval_allowed_only_accepts_explicit_allow():
    assert is_approval_allowed(_answer([APPROVAL_ALLOW_LABEL])) is True
    assert is_approval_allowed(_answer([APPROVAL_DENY_LABEL])) is False
    assert is_approval_allowed(_answer([])) is False
    assert is_approval_allowed({"cancelled": True}) is False
    assert is_approval_allowed({"answers": [{"id": "other", "selected": [APPROVAL_ALLOW_LABEL]}]}) is False
    assert is_approval_allowed({"answers": "bad"}) is False
    assert is_approval_allowed(None) is False


@pytest.mark.asyncio
async def test_ask_mode_allow_executes_tool(monkeypatch):
    calls: list[dict] = []

    def fake_interrupt(payload: dict) -> dict:
        calls.append(payload)
        return _answer([APPROVAL_ALLOW_LABEL])

    monkeypatch.setattr(tool_approval, "interrupt", fake_interrupt)
    middleware = create_tool_approval_middleware()

    result = await middleware.awrap_tool_call(_request(), _ok_handler)

    assert result.content == "done"
    assert calls and calls[0]["tool_name"] == "file_delete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        _answer([APPROVAL_DENY_LABEL]),
        _answer([]),
        {"cancelled": True},
        {"answers": [{"id": "approval", "selected": [], "custom": "允许", "skipped": True}]},
    ],
)
async def test_ask_mode_deny_returns_error_without_executing(monkeypatch, answer):
    executed: list[int] = []

    async def handler(_request):
        executed.append(1)
        return ToolMessage(content="done", name="file_delete", tool_call_id="call-1")

    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: answer)
    middleware = create_tool_approval_middleware()

    result = await middleware.awrap_tool_call(_request(), handler)

    assert executed == []
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "未获用户批准" in str(result.content)


@pytest.mark.asyncio
async def test_non_approval_tool_skips_interrupt(monkeypatch):
    def failing_interrupt(_payload):
        raise AssertionError("非审批工具不应触发审批中断")

    monkeypatch.setattr(tool_approval, "interrupt", failing_interrupt)
    middleware = create_tool_approval_middleware()

    result = await middleware.awrap_tool_call(_request("file_read"), _ok_handler)

    assert result.content == "done"


@pytest.mark.asyncio
async def test_graph_interrupt_is_not_swallowed(monkeypatch):
    def raising_interrupt(_payload):
        raise GraphInterrupt([])

    monkeypatch.setattr(tool_approval, "interrupt", raising_interrupt)
    middleware = create_tool_approval_middleware()

    with pytest.raises(GraphInterrupt):
        await middleware.awrap_tool_call(_request(), _ok_handler)


@pytest.mark.asyncio
async def test_approval_channel_error_fails_closed(monkeypatch):
    def broken_interrupt(_payload):
        raise RuntimeError("no answerer")

    monkeypatch.setattr(tool_approval, "interrupt", broken_interrupt)
    middleware = create_tool_approval_middleware()

    result = await middleware.awrap_tool_call(_request(), _ok_handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "审批通道不可用" in str(result.content)
