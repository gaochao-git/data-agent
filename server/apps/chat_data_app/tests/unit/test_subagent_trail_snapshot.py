"""运行中子智能体检查点快照：详情页秒开的数据来源。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from apps.chat_data_app.agent.run import subagent_trail_snapshot as snapshot_module


class _FakeCheckpointer:
    def __init__(self, snapshot: object | None) -> None:
        self._snapshot = snapshot
        self.config: dict | None = None

    async def aget_tuple(self, config: dict) -> object | None:
        self.config = config
        return self._snapshot


class _FakeContext:
    def __init__(self, checkpointer: object) -> None:
        self._checkpointer = checkpointer

    async def __aenter__(self) -> object:
        return self._checkpointer

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _patch_checkpointer(monkeypatch: pytest.MonkeyPatch, checkpointer: object) -> None:
    monkeypatch.setattr(snapshot_module, "get_checkpointer", lambda: _FakeContext(checkpointer))


@pytest.mark.asyncio
async def test_load_live_trail_snapshot_builds_running_trail(monkeypatch: pytest.MonkeyPatch):
    messages = [
        HumanMessage("分析订单模型", id="user-1"),
        AIMessage(
            "先读取两个模型文件",
            id="ai-1",
            tool_calls=[
                {"id": "call-read", "name": "file_read", "args": {"path": "a.yaml"}},
                {"id": "call-running", "name": "file_read", "args": {"path": "b.yaml"}},
            ],
        ),
        ToolMessage("a.yaml 内容", tool_call_id="call-read", name="file_read"),
    ]
    checkpointer = _FakeCheckpointer(
        SimpleNamespace(checkpoint={"channel_values": {"messages": messages, "context_compression_events": []}})
    )
    _patch_checkpointer(monkeypatch, checkpointer)

    result = await snapshot_module.load_live_trail_snapshot(task_id="task-1", agent_session_id="session-1")

    assert result is not None
    trail = result["trail"]
    assert [item["kind"] for item in trail] == ["message", "tool", "tool"]
    assert trail[1]["status"] == "completed"
    # 还没有 ToolMessage 的工具按运行中展示，避免详情页把在跑的工具标成已完成。
    assert trail[2]["status"] == "running"
    assert checkpointer.config == {
        "configurable": {
            "thread_id": "chatdata-agent-session-session-1",
            "checkpoint_ns": "task-task-1",
        }
    }


@pytest.mark.asyncio
async def test_load_live_trail_snapshot_returns_none_without_messages(monkeypatch: pytest.MonkeyPatch):
    _patch_checkpointer(monkeypatch, _FakeCheckpointer(None))
    assert await snapshot_module.load_live_trail_snapshot(task_id="task-1", agent_session_id="s") is None

    _patch_checkpointer(
        monkeypatch,
        _FakeCheckpointer(SimpleNamespace(checkpoint={"channel_values": {"messages": []}})),
    )
    assert await snapshot_module.load_live_trail_snapshot(task_id="task-1", agent_session_id="s") is None

    # 检查点结构不符合预期时按不可用处理，不能把异常抛给详情接口。
    _patch_checkpointer(monkeypatch, _FakeCheckpointer(SimpleNamespace(checkpoint=[])))
    assert await snapshot_module.load_live_trail_snapshot(task_id="task-1", agent_session_id="s") is None

    _patch_checkpointer(monkeypatch, _FakeCheckpointer(SimpleNamespace(checkpoint={"channel_values": []})))
    assert await snapshot_module.load_live_trail_snapshot(task_id="task-1", agent_session_id="s") is None


@pytest.mark.asyncio
async def test_load_live_trail_snapshot_swallows_checkpointer_errors(monkeypatch: pytest.MonkeyPatch):
    class _BrokenContext:
        async def __aenter__(self) -> object:
            raise RuntimeError("checkpointer down")

        async def __aexit__(self, *_exc: object) -> bool:
            return False

    monkeypatch.setattr(snapshot_module, "get_checkpointer", lambda: _BrokenContext())
    assert await snapshot_module.load_live_trail_snapshot(task_id="task-1", agent_session_id="s") is None
