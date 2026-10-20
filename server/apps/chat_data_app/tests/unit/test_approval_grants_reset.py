"""会话审批许可重置：checkpoint state 清理。"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app import agent_run_support


def _checkpoint_tuple(values):
    return SimpleNamespace(checkpoint={"channel_values": values})


def _patch_graph(monkeypatch, graph, checkpoint_values=None, read_error=None):
    class FakeCheckpointer:
        async def aget_tuple(self, _config):
            if read_error is not None:
                raise read_error
            return _checkpoint_tuple(checkpoint_values) if checkpoint_values is not None else None

    @asynccontextmanager
    async def fake_checkpointer():
        yield FakeCheckpointer()

    monkeypatch.setattr("utils.checkpoint.get_checkpointer", fake_checkpointer)
    monkeypatch.setattr("apps.chat_data_app.agent.data_agent.get_data_agent_graph", lambda **_kw: graph)


@pytest.mark.asyncio
async def test_reset_clears_grants(monkeypatch):
    calls: list[dict] = []

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"approval_grants": ["tool:file_delete"]})

        async def aupdate_state(self, config, values):
            calls.append({"config": config, "values": values})

    _patch_graph(monkeypatch, FakeGraph())
    await agent_run_support.reset_conversation_approval_grants("conv-1")

    assert calls == [
        {
            "config": {"configurable": {"thread_id": "conv-1"}},
            "values": {"approval_grants": [], "approval_run_grants": {}},
        }
    ]


@pytest.mark.asyncio
async def test_reset_noops_without_checkpoint(monkeypatch):
    class FakeGraph:
        async def aget_state(self, _config):
            raise RuntimeError("no checkpoint")

        async def aupdate_state(self, _config, _values):
            raise AssertionError("不应更新不存在的 checkpoint")

    _patch_graph(monkeypatch, FakeGraph())
    await agent_run_support.reset_conversation_approval_grants("conv-1")


@pytest.mark.asyncio
async def test_reset_noops_when_state_empty(monkeypatch):
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values=None)

        async def aupdate_state(self, _config, _values):
            raise AssertionError("状态为空时不应更新")

    _patch_graph(monkeypatch, FakeGraph())
    await agent_run_support.reset_conversation_approval_grants("conv-1")


@pytest.mark.asyncio
async def test_read_and_revoke_grants(monkeypatch):
    updates: list[dict] = []

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "approval_grants": ["tool:file_delete", "tool:file_move"],
                    "approval_run_grants": {"run-1": ["tool:file_delete", "tool:dir_delete"]},
                }
            )

        async def aupdate_state(self, _config, values):
            updates.append(values)

    _patch_graph(
        monkeypatch,
        FakeGraph(),
        checkpoint_values={
            "approval_grants": ["tool:file_delete", "tool:file_move"],
            "approval_run_grants": {"run-1": ["tool:file_delete", "tool:dir_delete"]},
        },
    )
    assert await agent_run_support.read_conversation_approval_grants("conv-1") == [
        "tool:file_delete",
        "tool:file_move",
    ]

    # 旧格式 key（无 tool: 前缀）也能撤销
    remaining = await agent_run_support.revoke_conversation_approval_grants("conv-1", ["file_delete"])
    assert remaining == ["tool:file_move"]
    assert updates == [
        {
            "approval_grants": ["tool:file_move"],
            "approval_run_grants": {"run-1": ["tool:dir_delete"]},
        }
    ]


@pytest.mark.asyncio
async def test_revoke_empty_or_unmatched_does_not_write(monkeypatch):
    updates: list[dict] = []

    class FakeGraph:
        async def aupdate_state(self, _config, values):
            updates.append(values)

    _patch_graph(
        monkeypatch,
        FakeGraph(),
        checkpoint_values={"approval_grants": ["tool:file_delete"]},
    )
    assert await agent_run_support.revoke_conversation_approval_grants("conv-1", []) == ["tool:file_delete"]
    assert await agent_run_support.revoke_conversation_approval_grants("conv-1", ["dir_delete"]) == ["tool:file_delete"]
    assert updates == []


@pytest.mark.asyncio
async def test_revoke_legacy_mcp_server_key(monkeypatch):
    updates: list[dict] = []

    class FakeGraph:
        async def aupdate_state(self, _config, values):
            updates.append(values)

    _patch_graph(
        monkeypatch,
        FakeGraph(),
        checkpoint_values={"approval_grants": ["mcp_server:srv-1"]},
    )
    remaining = await agent_run_support.revoke_conversation_approval_grants("conv-1", ["mcp_server:srv-1"])
    assert remaining == []
    assert updates == [{"approval_grants": [], "approval_run_grants": {}}]


@pytest.mark.asyncio
async def test_read_and_revoke_noop_without_checkpoint(monkeypatch):
    class FakeGraph:
        async def aget_state(self, _config):
            raise RuntimeError("no checkpoint")

    _patch_graph(monkeypatch, FakeGraph(), read_error=RuntimeError("no checkpoint"))
    with pytest.raises(RuntimeError):
        await agent_run_support.read_conversation_approval_grants("conv-1")
    with pytest.raises(RuntimeError):
        await agent_run_support.revoke_conversation_approval_grants("conv-1", ["file_delete"])


@pytest.mark.asyncio
async def test_read_and_revoke_noop_when_state_empty(monkeypatch):
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values=None)

    _patch_graph(monkeypatch, FakeGraph())
    assert await agent_run_support.read_conversation_approval_grants("conv-1") == []
    assert await agent_run_support.revoke_conversation_approval_grants("conv-1", ["file_delete"]) == []
