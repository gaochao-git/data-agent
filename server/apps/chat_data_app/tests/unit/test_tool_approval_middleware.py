"""工具审批中间件单测：载荷形状、答案解析、allow/ask 放行/拒绝与 fail-closed。"""

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
    _tool_args_preview,
    build_approval_payload,
    create_tool_approval_middleware,
)


@pytest.fixture(autouse=True)
def _synthetic_system_approval_policy(monkeypatch):
    """审批中间件单测使用合成策略：真实系统工具已统一 allow，MCP 仍默认 ask。"""
    from apps.chat_data_app.agent.tools import registry as registry_module

    for tool_name in ("file_delete", "dir_delete", "file_move"):
        monkeypatch.setitem(registry_module.SYSTEM_TOOL_APPROVALS, tool_name, "ask")


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
        APPROVAL_DENY_LABEL,
    ]


def test_approval_payload_contains_asked_at_ms():
    import time

    before = int(time.time() * 1000)
    payload = build_approval_payload("file_delete", {"args": {"path": "/a"}})
    asked_at = payload["asked_at_ms"]
    assert isinstance(asked_at, int)
    assert before <= asked_at <= int(time.time() * 1000)


def test_resolve_approval_policies_covers_system_mcp_and_control_tools():
    """统一入口：系统工具、MCP 默认 ask、控制工具 allow 全在一张映射里。"""
    from types import SimpleNamespace

    from apps.chat_data_app.agent.approval_policy import resolve_approval_policies

    policies = resolve_approval_policies(
        [
            SimpleNamespace(name="mcp_fault-db_db_mysql_info", metadata={"origin": "mcp"}),
            SimpleNamespace(name="mcp_fault-db_db_readonly", metadata={"origin": "mcp", "approval": "allow"}),
        ]
    )
    assert policies["file_delete"] == "ask"
    assert policies["file_read"] == "allow"
    assert policies["mcp_fault-db_db_mysql_info"] == "ask"
    assert policies["mcp_fault-db_db_readonly"] == "allow"
    assert policies["mcp_tool_search"] == "allow"
    assert policies["mcp_tool_call"] == "allow"


@pytest.mark.asyncio
async def test_mcp_tool_triggers_approval_with_display_metadata(monkeypatch):
    payloads: list[dict] = []

    def fake_interrupt(payload: dict) -> dict:
        payloads.append(payload)
        return _answer([APPROVAL_ALLOW_LABEL])

    monkeypatch.setattr(tool_approval, "interrupt", fake_interrupt)
    middleware = create_tool_approval_middleware(
        [SimpleNamespace(name="mcp_fault-db_db_mysql_info", metadata={"approval": "ask"})]
    )
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
    middleware = create_tool_approval_middleware([SimpleNamespace(name="mcp_srv_do", metadata={})])
    request = SimpleNamespace(tool_call={"id": "call-mcp", "name": "mcp_srv_do", "args": {}}, tool=None)

    result = await middleware.awrap_tool_call(request, _ok_handler)

    assert result.status == "error"
    assert "mcp_srv_do" in payloads[0]["questions"][0]["question"]


# ============ 单次审批（无持久授权） ============


def test_payload_offers_only_allow_or_deny():
    payload = build_approval_payload("file_delete", {"args": {"path": "/a"}})
    labels = [option["label"] for option in payload["questions"][0]["options"]]
    assert labels == [APPROVAL_ALLOW_LABEL, APPROVAL_DENY_LABEL]


def test_tool_args_preview_falls_back_for_unserializable_args():
    preview = _tool_args_preview({"args": {"bad": object()}})
    assert "object" in preview


def _ai_message(tool_calls: list[dict]) -> AIMessage:
    return AIMessage(content="", tool_calls=tool_calls)


@pytest.mark.asyncio
async def test_after_model_batches_parallel_approvals(monkeypatch):
    payloads: list[dict] = []

    def fake_interrupt(payload: dict) -> dict:
        payloads.append(payload)
        return _batch_answer({"c1": APPROVAL_ALLOW_LABEL, "c2": APPROVAL_DENY_LABEL})

    monkeypatch.setattr(tool_approval, "interrupt", fake_interrupt)
    middleware = create_tool_approval_middleware(
        [
            SimpleNamespace(name="file_delete", metadata={}),
            SimpleNamespace(name="mcp_fault-db_db_info", metadata={"approval": "ask"}),
        ]
    )
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
        APPROVAL_DENY_LABEL,
    ]


@pytest.mark.asyncio
async def test_after_model_deny_all(monkeypatch):
    monkeypatch.setattr(tool_approval, "interrupt", lambda _payload: _answer([APPROVAL_DENY_LABEL]))
    middleware = create_tool_approval_middleware()
    state = {"messages": [_ai_message([{"id": "c1", "type": "tool_call", "name": "file_delete", "args": {}}])]}

    update = await middleware.aafter_model(state, None)

    assert update == {"approval_decisions": {"c1": "deny"}}


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
async def test_graph_interrupt_is_not_swallowed(monkeypatch):
    """兜底审批必须让 GraphInterrupt 冒泡：吞掉会被当成拒绝，恢复后无法续跑。"""

    def raising_interrupt(_payload):
        raise GraphInterrupt([])

    monkeypatch.setattr(tool_approval, "interrupt", raising_interrupt)
    middleware = create_tool_approval_middleware()

    with pytest.raises(GraphInterrupt):
        await middleware.awrap_tool_call(_request(), _ok_handler)


@pytest.mark.asyncio
async def test_approval_channel_error_fails_closed(monkeypatch):
    """审批通道不可用时必须拒绝执行（fail-closed），不能放行。"""

    def broken_interrupt(_payload):
        raise RuntimeError("no answerer")

    monkeypatch.setattr(tool_approval, "interrupt", broken_interrupt)
    middleware = create_tool_approval_middleware()

    result = await middleware.awrap_tool_call(_request(), _ok_handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "审批通道不可用" in str(result.content)


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

    calls = [{"call_id": "c1"}, {"call_id": "c2"}]
    assert _resolve_batch_answers({"answers": "bad"}, calls) == {"c1": "deny", "c2": "deny"}
    assert _resolve_batch_answers({"answers": [None, {"id": "c1", "selected": []}]}, calls) == {
        "c1": "deny",
        "c2": "deny",
    }
    assert _resolve_batch_answers({"answers": [{"id": "c1", "selected": [APPROVAL_ALLOW_LABEL]}]}, calls) == {
        "c1": "allow",
        "c2": "deny",
    }
    assert _resolve_batch_answers({"answers": [{"id": "c1", "selected": [APPROVAL_ALWAYS_ALLOW_LABEL]}]}, calls) == {
        "c1": "allow",
        "c2": "deny",
    }
    assert _resolve_batch_answers(
        {"answers": [{"id": "c1", "selected": [APPROVAL_RUN_ALWAYS_ALLOW_LABEL]}]}, calls
    ) == ({"c1": "allow", "c2": "deny"})


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


def test_resolve_batch_answers_mixed_same_tool_combinations():
    from apps.chat_data_app.agent.middleware.tool_approval import _resolve_batch_answers

    calls = [{"call_id": "c1"}, {"call_id": "c2"}]
    assert _resolve_batch_answers(
        {
            "answers": [
                {"id": "c1", "selected": [APPROVAL_ALWAYS_ALLOW_LABEL]},
                {"id": "c2", "selected": [APPROVAL_DENY_LABEL]},
            ]
        },
        calls,
    ) == ({"c1": "allow", "c2": "deny"})


def test_args_summary_serialization_fallback():
    class Unserializable:
        def __str__(self):
            return "<obj>"

    # 非 dict/list 的未知对象直接跳过；dict/list 序列化失败回退 str()
    summary = tool_approval._args_summary({"id": "c1", "args": {"ok": "v", "obj": Unserializable()}})
    assert summary["ok"] == "v"
    assert "obj" not in summary
    summary = tool_approval._args_summary({"id": "c1", "args": {"raw": {"inner": Unserializable()}}})
    assert "Unserializable" in summary["raw"]


def test_mcp_tool_call_approval_targets_wrapped_tool_not_proxy():
    middleware = create_tool_approval_middleware(
        tools=[
            SimpleNamespace(name="mcp_fault_ssh_exec", metadata={"origin": "mcp", "display_name": "远程执行"}),
        ]
    )
    tool_call = {
        "id": "call-proxy",
        "name": "mcp_tool_call",
        "args": {"name": "mcp_fault_ssh_exec", "input": {"cmd": "rm -rf /tmp/x"}},
    }
    call = middleware._pending_call(tool_call, 0, decided=set())
    assert call is not None
    assert call["tool_name"] == "mcp_fault_ssh_exec"
    assert call["tool_call"]["args"] == {"cmd": "rm -rf /tmp/x"}


def test_mcp_tool_call_invalid_target_skips_approval():
    middleware = create_tool_approval_middleware()
    for args in ({}, {"name": "bash_run"}, {"name": "mcp_tool_call"}):
        call = middleware._pending_call({"id": "call-proxy", "name": "mcp_tool_call", "args": args}, 0, decided=set())
        assert call is None


def test_tool_approval_policy_matrix():
    tools = [
        SimpleNamespace(name="mcp_allow", metadata={"approval": "allow"}),
        SimpleNamespace(name="mcp_ask", metadata={"approval": "ask"}),
        SimpleNamespace(name="mcp_default", metadata={}),
    ]
    middleware = create_tool_approval_middleware(tools)
    assert middleware._needs_approval("mcp_allow") is False
    assert middleware._needs_approval("mcp_ask") is True
    assert middleware._needs_approval("mcp_default") is True
    assert middleware._needs_approval("file_delete") is True


def test_resolve_batch_answers_unknown_label_fails_closed():
    from apps.chat_data_app.agent.middleware.tool_approval import _resolve_batch_answers

    calls = [{"call_id": "c1"}]
    assert _resolve_batch_answers({"answers": [{"id": "c1", "selected": ["未知选项"]}]}, calls) == {"c1": "deny"}


def test_unknown_direct_mcp_tool_skips_approval():
    middleware = create_tool_approval_middleware([SimpleNamespace(name="mcp_known", metadata={})])
    call = middleware._pending_call({"id": "c1", "name": "mcp_boom", "args": {}}, 0, decided=set())
    assert call is None


def test_resolve_decision_legacy_always_label_allows_once():
    from apps.chat_data_app.agent.middleware.tool_approval import _resolve_decision

    assert _resolve_decision(_answer([APPROVAL_ALWAYS_ALLOW_LABEL])) == "allow"
