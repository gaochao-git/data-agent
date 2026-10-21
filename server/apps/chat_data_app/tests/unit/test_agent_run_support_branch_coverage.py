"""agent_run_support 收口辅助函数的分支覆盖（早退路径 / resume 水位条件）。"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app import agent_run_support


@pytest.mark.asyncio
async def test_materialize_conversation_semantic_models_meta_failure_keeps_empty_source_type(monkeypatch):
    """数据源组元信息读取失败只告警，物化以空 source_type 继续执行。"""
    captured: dict = {}

    async def fake_created_date(_conversation_id, *, user_id):
        return "2026-10-20"

    async def fake_prepare_workspace(_conversation_id, _user_id, *, created_date):
        return {"root": "/tmp/chatdata-test-workspace"}

    class _BrokenManager:
        async def get_datasource_meta_async(self, _datasource_id):
            raise RuntimeError("meta down")

    async def fake_prematerialize(**kwargs):
        captured.update(kwargs)
        return {"total": 0, "failed_ids": []}

    monkeypatch.setattr(agent_run_support, "get_conversation_created_date", fake_created_date)
    monkeypatch.setattr(agent_run_support, "prepare_conversation_workspace", fake_prepare_workspace)
    monkeypatch.setattr(agent_run_support, "get_datasource_manager", lambda: _BrokenManager())
    monkeypatch.setattr(agent_run_support, "prematerialize_enabled_semantic_models", fake_prematerialize)

    result = await agent_run_support.materialize_conversation_semantic_models(
        "conv-1",
        user_id="user-1",
        datasource_id="ds-1",
    )
    assert result == {"total": 0, "failed_ids": []}
    assert captured["source_type"] == ""
    assert captured["datasource_id"] == "ds-1"


@pytest.mark.asyncio
async def test_materialize_skips_empty_datasource_id():
    assert await agent_run_support.materialize_conversation_semantic_models(
        "conv-1", user_id="user-1", datasource_id=""
    ) == {"total": 0, "failed_ids": []}


@pytest.mark.asyncio
async def test_write_agent_model_snapshot_without_adapter_returns_early():
    callbacks = SimpleNamespace(stream_adapter=None)
    await agent_run_support._write_agent_model_snapshot(callbacks, {"k": "v"})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_persist_agent_model_snapshot_without_writer_skips_submit():
    submitted: list = []

    class _Writer:
        def submit(self, snapshot):
            submitted.append(snapshot)

    callbacks = SimpleNamespace(
        stream_event_id_provider=lambda: "1-2",
        model_snapshot_writer=None,
    )
    await agent_run_support._persist_agent_model_snapshot(callbacks, {"k": "v"})  # type: ignore[arg-type]
    assert submitted == []

    writer = _Writer()
    callbacks = SimpleNamespace(
        stream_event_id_provider=None,
        model_snapshot_writer=writer,
    )
    await agent_run_support._persist_agent_model_snapshot(callbacks, {"k": "v"})  # type: ignore[arg-type]
    assert submitted == [{"k": "v", "__history_stream_id": None}]


@pytest.mark.asyncio
async def test_flush_agent_model_snapshots_without_writer_is_noop():
    callbacks = SimpleNamespace(model_snapshot_writer=None)
    await agent_run_support._flush_agent_model_snapshots(callbacks)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_persist_agent_user_message_without_message_is_noop():
    callbacks = SimpleNamespace(user_message=None)
    await agent_run_support._persist_agent_user_message(callbacks)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_persist_agent_partial_history_marks_question_resume_started(monkeypatch):
    """异常收口时若 resume 答案已写入且图已推进，必须把旧问题标记为已恢复。"""
    marked: list[tuple] = []

    async def fake_partial_history(*_args, **_kwargs):
        return None

    async def fake_mark(run_id, owner_token):
        marked.append((run_id, owner_token))

    monkeypatch.setattr(agent_run_support, "_persist_partial_agent_turn_history", fake_partial_history)
    monkeypatch.setattr(agent_run_support.agent_inbox_service, "mark_question_resume_started", fake_mark)

    callbacks = SimpleNamespace(
        model_snapshot_writer=None,
        stream_adapter=SimpleNamespace(
            graph_started=True,
            state={"messages": []},
            pending_interrupt=lambda: False,
        ),
        thread_id="conv-1",
        user_id="user-1",
        username=None,
        turn_id="turn-1",
        run_id="run-1",
        owner_token="owner-1",
        resume_answer={"cancelled": True},
        effective_turn_id="turn-1",
        stream_event_id_provider=None,
    )
    await agent_run_support._persist_agent_partial_history(callbacks)  # type: ignore[arg-type]
    assert marked == [("run-1", "owner-1")]


@pytest.mark.asyncio
async def test_persist_agent_partial_history_skips_mark_when_interrupt_pending(monkeypatch):
    """仍在等待用户回答（pending interrupt）时不能标记恢复。"""
    marked: list[tuple] = []

    async def fake_partial_history(*_args, **_kwargs):
        return None

    async def fake_mark(run_id, owner_token):
        marked.append((run_id, owner_token))

    monkeypatch.setattr(agent_run_support, "_persist_partial_agent_turn_history", fake_partial_history)
    monkeypatch.setattr(agent_run_support.agent_inbox_service, "mark_question_resume_started", fake_mark)

    callbacks = SimpleNamespace(
        model_snapshot_writer=None,
        stream_adapter=SimpleNamespace(
            graph_started=True,
            state={"messages": []},
            pending_interrupt=lambda: True,
        ),
        thread_id="conv-1",
        user_id="user-1",
        username=None,
        turn_id="turn-1",
        run_id="run-1",
        owner_token="owner-1",
        resume_answer={"cancelled": True},
        effective_turn_id="turn-1",
        stream_event_id_provider=None,
    )
    await agent_run_support._persist_agent_partial_history(callbacks)  # type: ignore[arg-type]
    assert marked == []


@pytest.mark.asyncio
async def test_persist_agent_turn_history_snapshot_without_adapter_is_noop():
    callbacks = SimpleNamespace(model_snapshot_writer=None, stream_adapter=None)
    await agent_run_support._persist_agent_turn_history_snapshot(callbacks)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mark_agent_resume_checkpointed_invoked(monkeypatch):
    """恢复段把答案写入检查点后，旧问题要标记为已恢复，避免下个 worker 复活。"""
    marked: list[tuple] = []

    async def fake_mark(run_id, owner_token):
        marked.append((run_id, owner_token))

    monkeypatch.setattr(agent_run_support.agent_inbox_service, "mark_question_resume_started", fake_mark)

    callbacks = SimpleNamespace(
        resume_answer={"cancelled": True},
        run_id="run-1",
        owner_token="owner-1",
        stream_adapter=SimpleNamespace(graph_started=True, pending_interrupt=lambda: False),
    )
    await agent_run_support._mark_agent_resume_checkpointed(callbacks)  # type: ignore[arg-type]
    assert marked == [("run-1", "owner-1")]


@pytest.mark.asyncio
async def test_mark_agent_resume_checkpointed_skips_when_interrupt_pending(monkeypatch):
    marked: list[tuple] = []

    async def fake_mark(run_id, owner_token):
        marked.append((run_id, owner_token))

    monkeypatch.setattr(agent_run_support.agent_inbox_service, "mark_question_resume_started", fake_mark)

    callbacks = SimpleNamespace(
        resume_answer={"cancelled": True},
        run_id="run-1",
        owner_token="owner-1",
        stream_adapter=SimpleNamespace(graph_started=True, pending_interrupt=lambda: True),
    )
    await agent_run_support._mark_agent_resume_checkpointed(callbacks)  # type: ignore[arg-type]
    assert marked == []
