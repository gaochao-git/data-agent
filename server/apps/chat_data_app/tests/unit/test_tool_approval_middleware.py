"""按需审批中间件单测：载荷形状、答案解析、ask 模式放行/拒绝与 fail-closed。"""

import os
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.agent.middleware import tool_approval
from apps.chat_data_app.agent.middleware.tool_approval import (
    APPROVAL_ALLOW_LABEL,
    APPROVAL_ALWAYS_ALLOW_LABEL,
    APPROVAL_DENY_LABEL,
    APPROVAL_RUN_ALWAYS_ALLOW_LABEL,
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


def _batch_answer(selected_by_id: dict[str, str]) -> dict:
    return {
        "answers": [
            {"id": call_id, "selected": [label], "custom": "", "skipped": False}
            for call_id, label in selected_by_id.items()
        ]
    }


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
        APPROVAL_RUN_ALWAYS_ALLOW_LABEL,
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
    assert labels == [
        APPROVAL_ALLOW_LABEL,
        APPROVAL_RUN_ALWAYS_ALLOW_LABEL,
        APPROVAL_ALWAYS_ALLOW_LABEL,
        APPROVAL_DENY_LABEL,
    ]
    assert "本会话内「file_delete」" in payload["questions"][0]["options"][2]["description"]


def test_mcp_payload_always_allow_scope_is_tool():
    tool = SimpleNamespace(metadata={"origin": "mcp", "mcp_server_id": "fault-db", "mcp_tool_name": "db_mysql_info"})
    payload = build_approval_payload("mcp_fault-db_db_mysql_info", {"args": {}}, tool)
    assert "本会话内「db_mysql_info」" in payload["questions"][0]["options"][2]["description"]


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
async def test_mcp_always_allow_grants_single_tool_scope(monkeypatch):
    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: _answer([APPROVAL_ALWAYS_ALLOW_LABEL]))
    middleware = create_tool_approval_middleware()
    request = SimpleNamespace(
        tool_call={"id": "c", "name": "mcp_fault-db_db_info", "args": {}},
        state={},
        tool=SimpleNamespace(metadata={"origin": "mcp", "mcp_server_id": "fault-db", "mcp_tool_name": "db_info"}),
    )
    result = await middleware.awrap_tool_call(request, _ok_handler)
    assert isinstance(result, Command)
    assert result.update["approval_grants"] == ["tool:mcp_fault-db_db_info"]


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


def test_append_grant_preserves_existing_order():
    result = _append_grant(
        ToolMessage(content="done", name="file_delete", tool_call_id="c1"),
        "tool:file_delete",
        {"approval_grants": ["tool:file_move", "tool:dir_delete"]},
    )
    assert isinstance(result, Command)
    assert result.update["approval_grants"] == ["tool:file_move", "tool:dir_delete", "tool:file_delete"]


@pytest.mark.asyncio
async def test_after_model_single_always_preserves_existing_grants(monkeypatch):
    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: _answer([APPROVAL_ALWAYS_ALLOW_LABEL]))
    middleware = create_tool_approval_middleware()
    state = {
        "messages": [_ai_message([{"id": "c1", "type": "tool_call", "name": "file_delete", "args": {}}])],
        "approval_grants": ["tool:file_move", "tool:dir_delete"],
    }

    update = await middleware.aafter_model(state, None)

    assert update["approval_grants"] == ["tool:file_move", "tool:dir_delete", "tool:file_delete"]


def test_append_grant_passthrough_for_unknown_result():
    assert _append_grant("raw-result", "tool:file_delete", {}) == "raw-result"


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
    # 本会话内始终允许 → 执行并写入 grant
    await graph.ainvoke(Command(resume=_answer([APPROVAL_ALWAYS_ALLOW_LABEL])), config)
    assert executed == ["/a", "/b"]

    # 第三轮：grant 生效，不再中断，直接执行
    result = await graph.ainvoke({"messages": [HumanMessage("删除 /c")]}, config)
    assert not result.get("__interrupt__")
    assert executed == ["/a", "/b", "/c"]


# ============ 并行工具调用：after_model 批量审批 ============


def _ai_message(tool_calls: list[dict]) -> AIMessage:
    return AIMessage(content="", tool_calls=tool_calls)


@pytest.mark.asyncio
async def test_after_model_batches_parallel_approvals(monkeypatch):
    payloads: list[dict] = []

    def fake_interrupt(payload: dict) -> dict:
        payloads.append(payload)
        return _batch_answer({"c1": APPROVAL_ALLOW_LABEL, "c2": APPROVAL_DENY_LABEL})

    monkeypatch.setattr(tool_approval, "interrupt", fake_interrupt)
    middleware = create_tool_approval_middleware()
    state = {
        "messages": [
            _ai_message(
                [
                    {
                        "id": "c1",
                        "type": "tool_call",
                        "name": "file_delete",
                        "args": {"path": "/a", "intent": "删除 a"},
                    },
                    {"id": "c2", "type": "tool_call", "name": "mcp_fault-db_db_info", "args": {}},
                ]
            )
        ]
    }

    update = await middleware.aafter_model(state, None)

    # 逐条决策：允许第一条、拒绝第二条
    assert update["approval_decisions"] == {"c1": "allow", "c2": "deny"}
    assert "approval_grants" not in update
    items = payloads[0]["approval_items"]
    assert [item["tool_name"] for item in items] == ["file_delete", "mcp_fault-db_db_info"]
    assert items[0]["args_summary"] == {"path": "/a"}
    assert items[0]["intent"] == "删除 a"
    assert [item["call_id"] for item in items] == ["c1", "c2"]
    questions = payloads[0]["questions"]
    assert [question["id"] for question in questions] == ["c1", "c2"]
    assert [option["label"] for option in questions[0]["options"]] == [
        APPROVAL_ALLOW_LABEL,
        APPROVAL_RUN_ALWAYS_ALLOW_LABEL,
        APPROVAL_ALWAYS_ALLOW_LABEL,
        APPROVAL_DENY_LABEL,
    ]


@pytest.mark.asyncio
async def test_after_model_single_tool_always_allow_grants(monkeypatch):
    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: _answer([APPROVAL_ALWAYS_ALLOW_LABEL]))
    middleware = create_tool_approval_middleware()
    state = {"messages": [_ai_message([{"id": "c1", "type": "tool_call", "name": "file_delete", "args": {}}])]}

    update = await middleware.aafter_model(state, None)

    assert update["approval_decisions"] == {"c1": "allow"}
    assert update["approval_grants"] == ["tool:file_delete"]


@pytest.mark.asyncio
async def test_after_model_deny_all(monkeypatch):
    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: _answer([APPROVAL_DENY_LABEL]))
    middleware = create_tool_approval_middleware()
    state = {"messages": [_ai_message([{"id": "c1", "type": "tool_call", "name": "file_delete", "args": {}}])]}

    update = await middleware.aafter_model(state, None)

    assert update == {"approval_decisions": {"c1": "deny"}}


@pytest.mark.asyncio
async def test_after_model_skips_granted_and_decided_calls(monkeypatch):
    def failing_interrupt(_payload):
        raise AssertionError("已授权/已决策的调用不应再次询问")

    monkeypatch.setattr(tool_approval, "interrupt", failing_interrupt)
    middleware = create_tool_approval_middleware()
    state = {
        "messages": [_ai_message([{"id": "c1", "type": "tool_call", "name": "file_delete", "args": {}}])],
        "approval_grants": ["tool:file_delete"],
    }
    assert await middleware.aafter_model(state, None) == {"approval_decisions": {}}

    decided_state = {
        "messages": [_ai_message([{"id": "c2", "type": "tool_call", "name": "file_move", "args": {}}])],
        "approval_decisions": {"c2": "deny"},
    }
    assert await middleware.aafter_model(decided_state, None) == {"approval_decisions": {}}


@pytest.mark.asyncio
async def test_after_model_uses_mcp_display_label(monkeypatch):
    payloads: list[dict] = []

    def fake_interrupt(payload: dict) -> dict:
        payloads.append(payload)
        return _answer([APPROVAL_ALLOW_LABEL])

    monkeypatch.setattr(tool_approval, "interrupt", fake_interrupt)
    tool = SimpleNamespace(
        name="mcp_fault-db_db_info",
        metadata={
            "origin": "mcp",
            "mcp_server_id": "fault-db",
            "mcp_tool_name": "db_info",
            "display_name": "MySQL 诊断",
        },
    )
    middleware = create_tool_approval_middleware([tool])
    state = {"messages": [_ai_message([{"id": "c1", "type": "tool_call", "name": "mcp_fault-db_db_info", "args": {}}])]}

    update = await middleware.aafter_model(state, None)

    assert update == {"approval_decisions": {"c1": "allow"}}
    assert "MySQL 诊断" in payloads[0]["questions"][0]["question"]


@pytest.mark.asyncio
async def test_wrap_tool_call_consumes_decisions(monkeypatch):
    def failing_interrupt(_payload):
        raise AssertionError("已有决策时不应再次审批")

    monkeypatch.setattr(tool_approval, "interrupt", failing_interrupt)
    middleware = create_tool_approval_middleware()
    allow_request = SimpleNamespace(
        tool_call={"id": "c1", "name": "file_delete", "args": {"path": "/a"}},
        state={"approval_decisions": {"c1": "allow"}},
        tool=None,
    )
    result = await middleware.awrap_tool_call(allow_request, _ok_handler)
    assert result.content == "done"

    deny_request = SimpleNamespace(
        tool_call={"id": "c1", "name": "file_delete", "args": {"path": "/a"}},
        state={"approval_decisions": {"c1": "deny"}},
        tool=None,
    )
    result = await middleware.awrap_tool_call(deny_request, _ok_handler)
    assert result.status == "error"


@pytest.mark.asyncio
async def test_parallel_tool_calls_single_approval_end_to_end():
    """模型一次返回两个审批工具调用：只中断一次，允许全部后两个都执行。"""
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool as lc_tool
    from langgraph.checkpoint.memory import MemorySaver

    from apps.chat_data_app.agent.agent_state import ChatDataAgentState

    executed: list[str] = []

    @lc_tool
    def file_delete(path: str) -> str:
        """删除文件（测试用）。"""
        executed.append(f"delete:{path}")
        return f"deleted {path}"

    @lc_tool
    def dir_delete(path: str) -> str:
        """删除目录（测试用）。"""
        executed.append(f"rmdir:{path}")
        return f"removed {path}"

    class _ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, _tools, **_kwargs):
            return self

    model = _ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "d1", "type": "tool_call", "name": "file_delete", "args": {"path": "/a"}},
                    {"id": "d2", "type": "tool_call", "name": "dir_delete", "args": {"path": "/b"}},
                ],
            ),
            AIMessage(content="全部删除完成"),
        ]
    )
    graph = create_agent(
        model=model,
        tools=[file_delete, dir_delete],
        system_prompt="test",
        middleware=[create_tool_approval_middleware([file_delete, dir_delete])],
        state_schema=ChatDataAgentState,
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "approval-parallel"}}

    result = await graph.ainvoke({"messages": [HumanMessage("删除 /a 和 /b")]}, config)
    assert result.get("__interrupt__")
    assert executed == []
    await graph.ainvoke(Command(resume=_batch_answer({"d1": APPROVAL_ALLOW_LABEL, "d2": APPROVAL_ALLOW_LABEL})), config)
    assert sorted(executed) == ["delete:/a", "rmdir:/b"]


@pytest.mark.asyncio
async def test_after_model_clears_previous_decisions(monkeypatch):
    """每轮模型响应重置 decisions：无待审批调用时写空，防止 checkpoint 累积。"""
    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: _answer([APPROVAL_ALLOW_LABEL]))
    middleware = create_tool_approval_middleware()
    state = {
        "messages": [AIMessage(content="无需工具")],
        "approval_decisions": {"old-call": "allow"},
    }
    assert await middleware.aafter_model(state, None) == {"approval_decisions": {}}


def test_replace_approval_decisions_drops_history():
    from apps.chat_data_app.agent.agent_state import replace_approval_decisions

    assert replace_approval_decisions({"old": "allow"}, {"new": "deny"}) == {"new": "deny"}
    assert replace_approval_decisions({"old": "allow"}, {}) == {}


# ============ 边界分支 ============


@pytest.mark.asyncio
async def test_after_model_interrupt_channel_error_denies_all(monkeypatch):
    def raising_interrupt(_payload):
        raise RuntimeError("channel down")

    monkeypatch.setattr(tool_approval, "interrupt", raising_interrupt)
    middleware = create_tool_approval_middleware()
    state = {
        "messages": [
            _ai_message(
                [
                    {"id": "c1", "type": "tool_call", "name": "file_delete", "args": {}},
                    {"id": "c2", "type": "tool_call", "name": "dir_delete", "args": {}},
                ]
            )
        ]
    }

    update = await middleware.aafter_model(state, None)

    assert update == {"approval_decisions": {"c1": "deny", "c2": "deny"}}


@pytest.mark.asyncio
async def test_after_model_ignores_non_approval_calls_and_empty_state(monkeypatch):
    def failing_interrupt(_payload):
        raise AssertionError("非审批工具不应触发审批")

    monkeypatch.setattr(tool_approval, "interrupt", failing_interrupt)
    middleware = create_tool_approval_middleware()

    assert await middleware.aafter_model({"messages": []}, None) == {"approval_decisions": {}}
    state = {"messages": [_ai_message([{"id": "c1", "type": "tool_call", "name": "file_read", "args": {}}])]}
    assert await middleware.aafter_model(state, None) == {"approval_decisions": {}}


def test_args_summary_keeps_scalars_and_caps_entries():
    from apps.chat_data_app.agent.middleware.tool_approval import _args_summary

    assert _args_summary({"args": "not-a-dict"}) == {}
    assert _args_summary({"args": None}) == {}
    summary = _args_summary(
        {
            "args": {
                "a": 1,
                "b": object(),
                "c": 2,
                "d": 3,
                "e": 4,
                "f": 5,
                "g": 6,
                "h": 7,
            }
        }
    )
    assert "b" not in summary
    assert len(summary) == 6
    assert summary["a"] == "1"


def test_resolve_batch_answers_malformed_answers_fail_closed():
    from apps.chat_data_app.agent.middleware.tool_approval import _resolve_batch_answers

    calls = [{"call_id": "c1", "grant_key": "tool:a"}, {"call_id": "c2", "grant_key": "tool:b"}]
    assert _resolve_batch_answers({"answers": "bad"}, calls) == ({"c1": "deny", "c2": "deny"}, [], [])
    assert _resolve_batch_answers({"answers": [None, {"id": "c1", "selected": []}]}, calls) == (
        {"c1": "deny", "c2": "deny"},
        [],
        [],
    )
    assert _resolve_batch_answers({"answers": [{"id": "c1", "selected": [APPROVAL_ALLOW_LABEL]}]}, calls) == (
        {"c1": "allow", "c2": "deny"},
        [],
        [],
    )
    assert _resolve_batch_answers({"answers": [{"id": "c1", "selected": [APPROVAL_ALWAYS_ALLOW_LABEL]}]}, calls) == (
        {"c1": "allow", "c2": "deny"},
        ["tool:a"],
        [],
    )
    assert _resolve_batch_answers(
        {"answers": [{"id": "c1", "selected": [APPROVAL_RUN_ALWAYS_ALLOW_LABEL]}]}, calls
    ) == ({"c1": "allow", "c2": "deny"}, [], ["tool:a"])


@pytest.mark.asyncio
async def test_after_model_batch_supports_per_item_always_allow(monkeypatch):
    monkeypatch.setattr(
        tool_approval,
        "interrupt",
        lambda _payload: _batch_answer({"c1": APPROVAL_ALWAYS_ALLOW_LABEL, "c2": APPROVAL_DENY_LABEL}),
    )
    middleware = create_tool_approval_middleware()
    state = {
        "messages": [
            _ai_message(
                [
                    {"id": "c1", "type": "tool_call", "name": "file_delete", "args": {}},
                    {"id": "c2", "type": "tool_call", "name": "dir_delete", "args": {}},
                ]
            )
        ]
    }

    update = await middleware.aafter_model(state, None)

    assert update["approval_decisions"] == {"c1": "allow", "c2": "deny"}
    assert update["approval_grants"] == ["tool:file_delete"]


def test_create_middleware_skips_tools_without_name():
    tool = SimpleNamespace(name="", metadata=None)
    named = SimpleNamespace(name="mcp_s_t", metadata={"display_name": "展示名"})
    middleware = create_tool_approval_middleware([tool, named])
    assert middleware._tool_labels == {"mcp_s_t": "展示名"}


def test_single_approval_payload_includes_item_with_call_id():
    payload = build_approval_payload(
        "file_delete", {"id": "call-9", "args": {"file_path": "/mnt/user-data/workspace/a.txt", "intent": "删除 a"}}
    )
    assert payload["approval_items"] == [
        {
            "call_id": "call-9",
            "tool_name": "file_delete",
            "label": "file_delete",
            "intent": "删除 a",
            "args_summary": {"file_path": "/mnt/user-data/workspace/a.txt"},
        }
    ]


def test_args_summary_keeps_nested_json():
    from apps.chat_data_app.agent.middleware.tool_approval import _args_summary

    summary = _args_summary({"args": {"nested": {"a": 1}, "list": [1, 2], "text": "x"}})
    assert summary["text"] == "x"
    assert summary["nested"] == '{"a": 1}'
    assert summary["list"] == "[1, 2]"


# ============ legacy 文案兼容 + 同 run 连续审批 ============


def test_legacy_always_label_still_grants_after_wording_rename():
    """升级前已下发的 pending 卡片仍带旧文案「本次会话始终允许」，不能静默 deny。"""
    from apps.chat_data_app.agent.middleware.tool_approval import (
        _LEGACY_ALWAYS_ALLOW_LABELS,
        _resolve_batch_answers,
        _resolve_decision,
    )

    for legacy_label in _LEGACY_ALWAYS_ALLOW_LABELS:
        legacy_answer = _answer([legacy_label])
        assert is_always_allowed(legacy_answer) is True
        assert is_approval_allowed(legacy_answer) is False
        assert _resolve_decision(legacy_answer) == "always"
        decisions, conversation_grants, run_grants = _resolve_batch_answers(
            {"answers": [{"id": "c1", "selected": [legacy_label]}]},
            [{"call_id": "c1", "grant_key": "tool:file_delete"}],
        )
        assert decisions == {"c1": "allow"}
        assert conversation_grants == ["tool:file_delete"]
        assert run_grants == []


@pytest.mark.asyncio
async def test_second_approval_within_same_run_end_to_end():
    """同一 run 内 resume 后模型再次发起审批：第二次弹卡、决策替换均正常。"""
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool as lc_tool
    from langgraph.checkpoint.memory import MemorySaver

    from apps.chat_data_app.agent.agent_state import ChatDataAgentState

    executed: list[str] = []

    @lc_tool
    def file_delete(path: str) -> str:
        """删除文件（测试用）。"""
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
            _tool_call("c2", "/b"),
            AIMessage(content="两轮审批完成"),
        ]
    )
    graph = create_agent(
        model=model,
        tools=[file_delete],
        system_prompt="test",
        middleware=[create_tool_approval_middleware([file_delete])],
        state_schema=ChatDataAgentState,
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "approval-same-run"}}

    # 第一次审批
    result = await graph.ainvoke({"messages": [HumanMessage("删除 /a 和 /b")]}, config)
    assert result.get("__interrupt__")
    assert executed == []
    # 允许第一次后，模型在同一个 run 内再次请求审批
    result = await graph.ainvoke(Command(resume=_answer([APPROVAL_ALLOW_LABEL])), config)
    assert result.get("__interrupt__")
    assert executed == ["/a"]
    # 第二次放行，run 继续完成
    await graph.ainvoke(Command(resume=_answer([APPROVAL_ALLOW_LABEL])), config)
    assert executed == ["/a", "/b"]


# ============ 本轮内始终允许（run 作用域） ============


@pytest.mark.asyncio
async def test_after_model_single_run_always_writes_run_grant_only(monkeypatch):
    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: _answer([APPROVAL_RUN_ALWAYS_ALLOW_LABEL]))
    middleware = create_tool_approval_middleware(run_id="run-1")
    state = {"messages": [_ai_message([{"id": "c1", "type": "tool_call", "name": "file_delete", "args": {}}])]}

    update = await middleware.aafter_model(state, None)

    assert update["approval_decisions"] == {"c1": "allow"}
    assert "approval_grants" not in update
    assert update["approval_run_grants"] == {"run-1": ["tool:file_delete"]}

    # 同一 run 内下一次调用不再询问；换 run 后重新询问
    carried = {**state, "approval_run_grants": update["approval_run_grants"]}
    assert middleware._pending_calls(carried) == []
    next_run = create_tool_approval_middleware(run_id="run-2")
    assert len(next_run._pending_calls(carried)) == 1


@pytest.mark.asyncio
async def test_wrap_tool_call_consumes_run_grant(monkeypatch):
    def failing_interrupt(_payload):
        raise AssertionError("本轮已授权，不应再次审批")

    monkeypatch.setattr(tool_approval, "interrupt", failing_interrupt)
    middleware = create_tool_approval_middleware(run_id="run-1")
    request = SimpleNamespace(
        tool_call={"id": "c1", "name": "file_delete", "args": {"path": "/a"}},
        state={"approval_run_grants": {"run-1": ["tool:file_delete"]}},
        tool=None,
    )
    assert (await middleware.awrap_tool_call(request, _ok_handler)).content == "done"

    # 换 run 后本轮许可失效：走审批兜底（这里用拒绝答案验证）
    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: _answer([APPROVAL_DENY_LABEL]))
    other_run = create_tool_approval_middleware(run_id="run-2")
    denied = await other_run.awrap_tool_call(request, _ok_handler)
    assert denied.status == "error"


@pytest.mark.asyncio
async def test_after_model_batch_run_always_grants_only_that_call(monkeypatch):
    monkeypatch.setattr(
        tool_approval,
        "interrupt",
        lambda _payload: _batch_answer({"c1": APPROVAL_RUN_ALWAYS_ALLOW_LABEL, "c2": APPROVAL_DENY_LABEL}),
    )
    middleware = create_tool_approval_middleware(run_id="run-1")
    state = {
        "messages": [
            _ai_message(
                [
                    {"id": "c1", "type": "tool_call", "name": "file_delete", "args": {}},
                    {"id": "c2", "type": "tool_call", "name": "dir_delete", "args": {}},
                ]
            )
        ]
    }

    update = await middleware.aafter_model(state, None)

    assert update["approval_decisions"] == {"c1": "allow", "c2": "deny"}
    assert "approval_grants" not in update
    assert update["approval_run_grants"] == {"run-1": ["tool:file_delete"]}


@pytest.mark.asyncio
async def test_run_grant_survives_following_model_responses(monkeypatch):
    """本轮许可必须在同一 run 的后续模型响应中保留，不能被"无待审批清理"误清。"""
    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: _answer([APPROVAL_RUN_ALWAYS_ALLOW_LABEL]))
    middleware = create_tool_approval_middleware(run_id="run-1")
    first_state = {"messages": [_ai_message([{"id": "c1", "type": "tool_call", "name": "file_delete", "args": {}}])]}
    update = await middleware.aafter_model(first_state, None)

    follow_state = {
        **first_state,
        **update,
        "messages": [_ai_message([{"id": "c2", "type": "tool_call", "name": "file_delete", "args": {}}])],
    }
    follow_update = await middleware.aafter_model(follow_state, None)

    assert follow_update["approval_decisions"] == {}
    assert follow_update["approval_run_grants"] == {"run-1": ["tool:file_delete"]}
    assert middleware._pending_calls({**follow_state, **follow_update}) == []


def test_resolve_batch_answers_mixed_same_tool_combinations():
    """同工具多行组合：单次拒绝优先执行；窗口许可强于本轮且去重。"""
    from apps.chat_data_app.agent.middleware.tool_approval import _resolve_batch_answers

    calls = [{"call_id": "c1", "grant_key": "tool:file_delete"}, {"call_id": "c2", "grant_key": "tool:file_delete"}]
    assert _resolve_batch_answers(
        {
            "answers": [
                {"id": "c1", "selected": [APPROVAL_ALWAYS_ALLOW_LABEL]},
                {"id": "c2", "selected": [APPROVAL_DENY_LABEL]},
            ]
        },
        calls,
    ) == ({"c1": "allow", "c2": "deny"}, ["tool:file_delete"], [])
    assert _resolve_batch_answers(
        {
            "answers": [
                {"id": "c1", "selected": [APPROVAL_RUN_ALWAYS_ALLOW_LABEL]},
                {"id": "c2", "selected": [APPROVAL_ALLOW_LABEL]},
            ]
        },
        calls,
    ) == ({"c1": "allow", "c2": "allow"}, [], ["tool:file_delete"])
    assert _resolve_batch_answers(
        {
            "answers": [
                {"id": "c1", "selected": [APPROVAL_RUN_ALWAYS_ALLOW_LABEL]},
                {"id": "c2", "selected": [APPROVAL_ALWAYS_ALLOW_LABEL]},
            ]
        },
        calls,
    ) == ({"c1": "allow", "c2": "allow"}, ["tool:file_delete"], [])


def test_string_list_supports_dict_state_field():
    from apps.chat_data_app.agent.middleware.tool_approval import _string_list

    assert _string_list({"approval_grants": {"tool:a": True, "tool:b": False}}, "approval_grants") == [
        "tool:a",
        "tool:b",
    ]
