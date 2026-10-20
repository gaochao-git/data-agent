"""用户消息提前落库：截断、insert-only、失败吞掉不阻塞主链路。"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app import agent_run_support


@pytest.fixture()
def capture(monkeypatch):
    calls: list[dict] = []

    async def persist(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(agent_run_support.conversation_event_service, "persist_user_message", persist)
    return calls


@pytest.mark.asyncio
async def test_persist_early_user_message_is_insert_only(capture):
    await agent_run_support.persist_early_user_message("conv-1", "user-1", "用户一", "run-1", "删除文件")
    assert len(capture) == 1
    kwargs = capture[0]
    assert kwargs["conversation_id"] == "conv-1"
    assert kwargs["insert_only"] is True
    assert kwargs["message"].id == "run-1"
    assert kwargs["message"].content == "删除文件"


@pytest.mark.asyncio
async def test_persist_early_user_message_truncates_long_query(capture):
    await agent_run_support.persist_early_user_message("conv-1", "user-1", "用户一", "run-1", "x" * 5000)
    content = capture[0]["message"].content
    assert len(content) <= 4000 + 64
    assert "消息过长" in content


@pytest.mark.asyncio
async def test_persist_early_user_message_swallows_failure(monkeypatch):
    async def fail(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(agent_run_support.conversation_event_service, "persist_user_message", fail)
    # 提前落库失败不能阻塞 Run 启动
    await agent_run_support.persist_early_user_message("conv-1", "user-1", "用户一", "run-1", "删除文件")


def test_should_persist_early_user_message_guard():
    assert agent_run_support.should_persist_early_user_message("查询", None, False) is True
    assert agent_run_support.should_persist_early_user_message("", None, False) is False
    assert agent_run_support.should_persist_early_user_message("查询", "msg-1", False) is False
    assert agent_run_support.should_persist_early_user_message("查询", None, True) is False


@pytest.mark.asyncio
async def test_persist_early_user_message_if_needed_guard(capture):
    # 负向场景：空 query / 队列消息 / 保留队列 都不应提前落库
    for query, queued, preserve in [("", None, False), ("查询", "msg-1", False), ("查询", None, True)]:
        await agent_run_support.persist_early_user_message_if_needed(
            "conv-1", "user-1", "用户一", "run-1", query, queued, preserve
        )
    assert capture == []

    await agent_run_support.persist_early_user_message_if_needed(
        "conv-1", "user-1", "用户一", "run-1", "查询", None, False
    )
    assert len(capture) == 1
