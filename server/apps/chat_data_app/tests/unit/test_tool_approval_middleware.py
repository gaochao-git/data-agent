"""按需审批中间件单测：载荷形状、答案解析、ask 模式放行/拒绝与 fail-closed。"""

import os
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.agent.middleware import tool_approval
from apps.chat_data_app.agent.middleware.tool_approval import (
    APPROVAL_ALLOW_LABEL,
    APPROVAL_ALWAYS_ALLOW_LABEL,
    APPROVAL_DENY_LABEL,
    _append_grant,
    _tool_args_preview,
    build_approval_payload,
    create_tool_approval_middleware,
    is_always_allowed,
    is_approval_allowed,
    requires_approval,
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
    assert [option["label"] for option in question["options"]] == [
        APPROVAL_ALLOW_LABEL,
        APPROVAL_ALWAYS_ALLOW_LABEL,
        APPROVAL_DENY_LABEL,
    ]


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


def test_requires_approval_covers_builtin_and_mcp():
    """内置注册表标记 + MCP 动态工具默认 ask；只读检索工具不打断。"""
    assert requires_approval("file_delete") is True
    assert requires_approval("mcp_fault-db_db_mysql_info") is True
    assert requires_approval("mcp_tool_search") is False
    assert requires_approval("file_read") is False
    assert requires_approval("present_artifacts") is False


@pytest.mark.asyncio
async def test_mcp_tool_triggers_approval_with_display_metadata(monkeypatch):
    payloads: list[dict] = []

    def fake_interrupt(payload: dict) -> dict:
        payloads.append(payload)
        return _answer([APPROVAL_ALLOW_LABEL])

    monkeypatch.setattr(tool_approval, "interrupt", fake_interrupt)
    middleware = create_tool_approval_middleware()
    request = SimpleNamespace(
        tool_call={"id": "call-mcp", "name": "mcp_fault-db_db_mysql_info", "args": {"host": "db1"}},
        tool=SimpleNamespace(
            metadata={
                "origin": "mcp",
                "mcp_server_id": "fault-db",
                "mcp_tool_name": "db_mysql_info",
                "display_name": "MySQL 基本信息",
            }
        ),
    )

    result = await middleware.awrap_tool_call(request, _ok_handler)

    assert result.content == "done"
    assert payloads[0]["mcp_server_id"] == "fault-db"
    assert payloads[0]["mcp_tool_name"] == "db_mysql_info"
    assert payloads[0]["display_name"] == "MySQL 基本信息"
    assert "MCP 工具「MySQL 基本信息」" in payloads[0]["questions"][0]["question"]


@pytest.mark.asyncio
async def test_mcp_tool_without_metadata_falls_back_to_qualified_name(monkeypatch):
    payloads: list[dict] = []

    def fake_interrupt(payload: dict) -> dict:
        payloads.append(payload)
        return _answer([APPROVAL_DENY_LABEL])

    monkeypatch.setattr(tool_approval, "interrupt", fake_interrupt)
    middleware = create_tool_approval_middleware()
    request = SimpleNamespace(tool_call={"id": "call-mcp", "name": "mcp_srv_do", "args": {}}, tool=None)

    result = await middleware.awrap_tool_call(request, _ok_handler)

    assert result.status == "error"
    assert "mcp_srv_do" in payloads[0]["questions"][0]["question"]


# ============ 会话级"始终允许" ============


def test_payload_offers_always_allow_option():
    payload = build_approval_payload("file_delete", {"args": {"path": "/a"}})
    labels = [option["label"] for option in payload["questions"][0]["options"]]
    assert labels == [APPROVAL_ALLOW_LABEL, APPROVAL_ALWAYS_ALLOW_LABEL, APPROVAL_DENY_LABEL]
    assert "本会话内「file_delete」" in payload["questions"][0]["options"][1]["description"]


def test_mcp_payload_always_allow_scope_is_server():
    tool = SimpleNamespace(metadata={"origin": "mcp", "mcp_server_id": "fault-db", "mcp_tool_name": "db_mysql_info"})
    payload = build_approval_payload("mcp_fault-db_db_mysql_info", {"args": {}}, tool)
    assert "MCP server「fault-db」" in payload["questions"][0]["options"][1]["description"]


def test_is_always_allowed_parsing():
    assert is_always_allowed(_answer([APPROVAL_ALWAYS_ALLOW_LABEL])) is True
    assert is_always_allowed(_answer([APPROVAL_ALLOW_LABEL])) is False
    assert is_always_allowed({"cancelled": True}) is False
    assert is_always_allowed(None) is False


@pytest.mark.asyncio
async def test_granted_tool_skips_interrupt(monkeypatch):
    def failing_interrupt(_payload):
        raise AssertionError("已授予会话许可的工具不应再触发审批")

    monkeypatch.setattr(tool_approval, "interrupt", failing_interrupt)
    middleware = create_tool_approval_middleware()
    request = SimpleNamespace(
        tool_call={"id": "call-1", "name": "file_delete", "args": {"path": "/a"}},
        state={"approval_grants": ["tool:file_delete"]},
        tool=None,
    )
    result = await middleware.awrap_tool_call(request, _ok_handler)
    assert result.content == "done"


@pytest.mark.asyncio
async def test_always_allow_returns_grant_command_and_reexecutes_tool(monkeypatch):
    executed: list[int] = []

    async def handler(_request):
        executed.append(1)
        return ToolMessage(content="done", name="file_delete", tool_call_id="call-1")

    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: _answer([APPROVAL_ALWAYS_ALLOW_LABEL]))
    middleware = create_tool_approval_middleware()
    request = SimpleNamespace(
        tool_call={"id": "call-1", "name": "file_delete", "args": {"path": "/a"}}, state={}, tool=None
    )

    result = await middleware.awrap_tool_call(request, handler)

    assert executed == [1]
    assert isinstance(result, Command)
    assert result.update["approval_grants"] == ["tool:file_delete"]
    assert [message.content for message in result.update["messages"]] == ["done"]


@pytest.mark.asyncio
async def test_mcp_always_allow_grants_server_scope(monkeypatch):
    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: _answer([APPROVAL_ALWAYS_ALLOW_LABEL]))
    middleware = create_tool_approval_middleware()
    request = SimpleNamespace(
        tool_call={"id": "c", "name": "mcp_fault-db_db_info", "args": {}},
        state={},
        tool=SimpleNamespace(metadata={"origin": "mcp", "mcp_server_id": "fault-db", "mcp_tool_name": "db_info"}),
    )
    result = await middleware.awrap_tool_call(request, _ok_handler)
    assert isinstance(result, Command)
    assert result.update["approval_grants"] == ["mcp_server:fault-db"]


@pytest.mark.asyncio
async def test_always_allow_merges_grant_into_command_result(monkeypatch):
    """工具本身返回 Command（如 task 子智能体）时，grant 合并进 update 且保留 goto。"""
    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: _answer([APPROVAL_ALWAYS_ALLOW_LABEL]))

    async def handler(_request):
        return Command(update={"messages": [ToolMessage(content="x", name="t", tool_call_id="c")]}, goto="next")

    middleware = create_tool_approval_middleware()
    request = SimpleNamespace(tool_call={"id": "c", "name": "file_delete", "args": {}}, state={}, tool=None)
    result = await middleware.awrap_tool_call(request, handler)

    assert isinstance(result, Command)
    assert result.goto == "next"
    assert result.update["approval_grants"] == ["tool:file_delete"]
    assert "messages" in result.update


def test_tool_args_preview_falls_back_for_unserializable_args():
    preview = _tool_args_preview({"args": {"bad": object()}})
    assert "object" in preview


def test_is_always_allowed_edge_branches():
    assert is_always_allowed({"answers": "bad"}) is False
    assert is_always_allowed({"answers": ["bad"]}) is False
    assert is_always_allowed({"answers": [{"id": "other", "selected": [APPROVAL_ALWAYS_ALLOW_LABEL]}]}) is False
    assert is_always_allowed({"answers": [{"id": "approval", "selected": []}]}) is False


def test_is_approval_allowed_skips_non_dict_item():
    assert is_approval_allowed({"answers": ["bad"]}) is False


def test_append_grant_passthrough_for_unknown_result():
    assert _append_grant("raw-result", "tool:file_delete") == "raw-result"


@pytest.mark.asyncio
async def test_approval_interrupt_resume_and_session_grant_end_to_end():
    """真实 create_agent 图：审批中断 → resume 放行 → "始终允许"跨轮生效。"""
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.tools import tool as lc_tool
    from langgraph.checkpoint.memory import MemorySaver

    from apps.chat_data_app.agent.agent_state import ChatDataAgentState

    executed: list[str] = []

    @lc_tool
    def file_delete(path: str) -> str:
        """删除指定路径（测试用）。"""
        executed.append(path)
        return f"deleted {path}"

    class _ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, _tools, **_kwargs):
            return self

    def _tool_call(call_id: str, path: str) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[{"id": call_id, "type": "tool_call", "name": "file_delete", "args": {"path": path}}],
        )

    model = _ToolCallingFakeModel(
        responses=[
            _tool_call("c1", "/a"),
            AIMessage(content="删除 /a 完成"),
            _tool_call("c2", "/b"),
            AIMessage(content="删除 /b 完成"),
            _tool_call("c3", "/c"),
            AIMessage(content="删除 /c 完成"),
        ]
    )
    graph = create_agent(
        model=model,
        tools=[file_delete],
        system_prompt="test",
        middleware=[create_tool_approval_middleware()],
        state_schema=ChatDataAgentState,
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "approval-e2e"}}

    # 第一轮：中断等待审批
    result = await graph.ainvoke({"messages": [HumanMessage("删除 /a")]}, config)
    assert result.get("__interrupt__")
    assert executed == []
    # 允许本次 → 执行
    await graph.ainvoke(Command(resume=_answer([APPROVAL_ALLOW_LABEL])), config)
    assert executed == ["/a"]

    # 第二轮：再次中断
    result = await graph.ainvoke({"messages": [HumanMessage("删除 /b")]}, config)
    assert result.get("__interrupt__")
    # 本次会话始终允许 → 执行并写入 grant
    await graph.ainvoke(Command(resume=_answer([APPROVAL_ALWAYS_ALLOW_LABEL])), config)
    assert executed == ["/a", "/b"]

    # 第三轮：grant 生效，不再中断，直接执行
    result = await graph.ainvoke({"messages": [HumanMessage("删除 /c")]}, config)
    assert not result.get("__interrupt__")
    assert executed == ["/a", "/b", "/c"]
