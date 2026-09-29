"""agent_stream.py 覆盖率补充测试。

聚焦 SSE 适配器与流式入口尚未触达的分支：消息去重键、interrupt 结构校验、
断连标记、custom 事件各类型、首 token 超时看门狗、断线续传 handler，以及
_run_chat_data_agent_stream 前段的附件解析与超长输入转写。
不访问真实模型、Redis 与数据库。
"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app import agent_run_support, agent_stream
from apps.chat_data_app.agent_stream import AgentRunStreamFailedError
from apps.chat_data_app.agent.run.chat_data_stream_request import ChatDataStreamRequest


def _adapter(**kwargs):
    return agent_stream._AgentSseStreamAdapter(
        agent=kwargs.pop("agent", SimpleNamespace()),
        initial_state=kwargs.pop("initial_state", {}),
        config=kwargs.pop("config", {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 消息去重键
# ---------------------------------------------------------------------------


def test_stream_message_key_branches():
    assert agent_run_support._stream_message_key(HumanMessage(content="hi", id="m-1"), 0) == ("id", "m-1")
    # 无 tool_call_id 的 ToolMessage 不参与去重
    assert agent_run_support._stream_message_key(ToolMessage(content="r", tool_call_id=""), 1) is None
    assert agent_run_support._stream_message_key(ToolMessage(content="r", tool_call_id="c-1"), 2) == ("tool", "c-1")
    ai_with_tools = AIMessage(content="", tool_calls=[{"name": "f", "args": {}, "id": "c-2"}])
    assert agent_run_support._stream_message_key(ai_with_tools, 3) == ("ai_tools", "c-2")
    assert agent_run_support._stream_message_key(HumanMessage(content="问题"), 4) == ("human", "问题")
    assert agent_run_support._stream_message_key(AIMessage(content="回答"), 5) == ("ai", "回答")
    # 其它消息类型退化为 (类型, 内容, 序号)
    assert agent_run_support._stream_message_key(SystemMessage(content="sys"), 6) == (
        "SystemMessage", "sys", 6,
    )


def test_merge_message_snapshots_dedupes_by_key():
    incoming = [
        AIMessage(content="a", id="id-a"),
        AIMessage(content="a", id="id-a"),
        AIMessage(content="b", id="id-b"),
    ]
    merged = agent_stream._merge_message_snapshots([], incoming)
    assert [message.id for message in merged] == ["id-a", "id-b"]


# ---------------------------------------------------------------------------
# interrupt / 断连
# ---------------------------------------------------------------------------


def test_pending_interrupt_requires_structured_value():
    adapter = _adapter()
    adapter.state = SimpleNamespace(interrupts=())
    assert adapter.pending_interrupt() is None

    adapter.state = SimpleNamespace(interrupts=[SimpleNamespace(value="不是字典")])
    assert adapter.pending_interrupt() is None

    payload = {"question_id": "q1", "questions": [{"id": "x", "question": "请选择"}]}
    adapter.state = SimpleNamespace(interrupts=[SimpleNamespace(value=payload)])
    assert adapter.pending_interrupt() == payload


@pytest.mark.asyncio
async def test_handle_disconnect_branches():
    # 无 request：不算断连
    adapter = _adapter()
    assert await adapter._handle_disconnect() is False
    assert adapter.disconnected is False

    adapter = _adapter(request=SimpleNamespace(is_disconnected=lambda: _const(False)))
    assert await adapter._handle_disconnect() is False
    assert adapter.disconnected is False

    adapter = _adapter(
        request=SimpleNamespace(is_disconnected=lambda: _const(True)),
        disconnect_log_id="run-1",
    )
    assert await adapter._handle_disconnect() is True
    assert adapter.disconnected is True


def _const(value):
    async def inner():
        return value
    return inner()


# ---------------------------------------------------------------------------
# custom 事件
# ---------------------------------------------------------------------------


def test_handle_custom_payload_branches():
    adapter = _adapter()
    assert adapter._handle_custom_payload(None) == []
    assert adapter._handle_custom_payload("字符串") == []
    assert adapter._handle_custom_payload({"type": "未知类型"}) == []

    events = adapter._handle_custom_payload({"type": "token_usage", "usage": {"total": 12}})
    assert len(events) == 1 and "event: token_usage" in events[0]
    assert adapter.latest_token_usage == {"total": 12}

    events = adapter._handle_custom_payload({
        "type": "context_compression",
        "event": {"archived": 3},
        "archived_turn_messages": [HumanMessage(content="旧问题", id="h-1")],
    })
    assert len(events) == 1 and "event: context_compression" in events[0]
    assert [message.id for message in adapter.archived_turn_messages] == ["h-1"]

    # 非列表 archived 不应污染已归档消息
    adapter._handle_custom_payload({
        "type": "context_compression", "event": {}, "archived_turn_messages": "坏数据",
    })
    assert len(adapter.archived_turn_messages) == 1

    assert adapter._handle_custom_payload({"type": "inbox_admitted", "message_ids": []}) == []
    assert adapter._handle_custom_payload({"type": "inbox_admitted", "message_ids": ["", "m-2"]}) == [
        agent_stream._format_sse("inbox_admitted", {"message_ids": ["m-2"]})
    ]

    # tool_progress 缺字段一律忽略
    assert adapter._handle_custom_payload({"type": "tool_progress", "tool_call_id": "c"}) == []
    events = adapter._handle_custom_payload({
        "type": "tool_progress", "tool_call_id": "c-9", "tool_name": "run_sql",
        "message": "执行中", "progress": 40,
    })
    assert len(events) == 1 and '"progress": 40' in events[0]


# ---------------------------------------------------------------------------
# 首 token 超时看门狗
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_token_timeout_watchdog_disabled_returns_early(monkeypatch):
    monkeypatch.setattr(agent_stream, "LLM_FIRST_TOKEN_TIMEOUT_SECONDS", 0)
    adapter = _adapter()
    called = []
    monkeypatch.setattr(
        adapter, "_emit_first_token_timeout_if_needed",
        lambda *_a, **_k: called.append(True),
    )
    await adapter._first_token_timeout_watchdog(0.0)
    assert called == []


@pytest.mark.asyncio
async def test_first_token_timeout_watchdog_fires_when_no_payload(monkeypatch):
    monkeypatch.setattr(agent_stream, "LLM_FIRST_TOKEN_TIMEOUT_SECONDS", 1)
    adapter = _adapter()
    fired = []
    monkeypatch.setattr(
        adapter, "_emit_first_token_timeout_if_needed",
        lambda *_a, **_k: fired.append(True),
    )
    await adapter._first_token_timeout_watchdog(0.0)
    assert fired == [True]

    # 已有可见输出 / 已断连时不再告警
    fired.clear()
    adapter._first_visible_payload_seen = True
    await adapter._first_token_timeout_watchdog(0.0)
    assert fired == []

    fired.clear()
    adapter._first_visible_payload_seen = False
    adapter.disconnected = True
    await adapter._first_token_timeout_watchdog(0.0)
    assert fired == []


def test_emit_first_token_timeout_alarm_once(monkeypatch):
    import time as time_module

    monkeypatch.setattr(agent_stream, "LLM_FIRST_TOKEN_TIMEOUT_SECONDS", 1)
    alarms = []
    monkeypatch.setattr(
        agent_stream, "emit_llm_first_token_timeout_alarm",
        lambda **kwargs: alarms.append(kwargs),
    )
    adapter = _adapter(debug_label="AI Content from model")
    # 未到阈值：不告警
    adapter._emit_first_token_timeout_if_needed(time_module.perf_counter())
    assert alarms == []
    # 超过阈值：告警一次，重复调用不再告警
    adapter._emit_first_token_timeout_if_needed(time_module.perf_counter() - 5)
    adapter._emit_first_token_timeout_if_needed(time_module.perf_counter() - 5)
    assert len(alarms) == 1
    assert alarms[0]["ext1"]["thread_id"] == "t1"
    assert alarms[0]["ext1"]["debug_label"] == "AI Content from model"


# ---------------------------------------------------------------------------
# 断线续传 handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_handler_submits_and_streams_run_events(monkeypatch):
    submitted = []
    monkeypatch.setattr(
        agent_stream.agent_run_runtime, "submit",
        lambda **kwargs: submitted.append(kwargs),
    )

    async def fake_stream(*, run_id, last_event_id, is_disconnected=None):
        assert run_id == "run-9" and last_event_id == "3-0"
        yield {"stream_id": "3-1", "payload": "data: chunk-a\n\n"}
        yield {"stream_id": "3-2", "payload": "data: chunk-b\n\n"}

    monkeypatch.setattr(agent_stream.agent_run_event_service, "stream", fake_stream)

    response = await agent_stream.resume_chat_data_question_handler(
        conversation_id="conv-1", user_id="u1", username="张三", run_id="run-9",
        runtime_context={}, answer={"a": 1}, turn_id="turn-1",
        last_event_id="3-0", request=None,
    )
    assert response.headers["X-ChatData-Run-Id"] == "run-9"
    assert response.media_type == "text/event-stream"

    body = [chunk async for chunk in response.body_iterator]
    assert body == ["id: 3-1\ndata: chunk-a\n\n", "id: 3-2\ndata: chunk-b\n\n"]
    assert submitted == [{
        "run_id": "run-9", "conversation_id": "conv-1", "user_id": "u1", "operator_name": "张三",
    }]


# ---------------------------------------------------------------------------
# 流式入口前段：指标集准备失败 / 数据源绑定冲突 / 附件 / 超长输入
# ---------------------------------------------------------------------------


def _semantic_context(**overrides):
    base = {
        "semantic_config_error": "",
        "datasource_id": None,
        "datasource_meta": None,
        "semantic_config_id": None,
        "semantic_config": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch_stream_preamble(monkeypatch, semantic_context, *, workspace_root, runtime_cls=None):
    async def fake_prepare_semantic(**_kwargs):
        return semantic_context

    async def fake_workspace(*_args, **_kwargs):
        return {"root": workspace_root}

    async def fake_snapshot(*_args, **_kwargs):
        return None

    # 运行准备逻辑已下沉 agent_run_support：相关桩打在消费方命名空间。
    monkeypatch.setattr(agent_run_support, "prepare_semantic_conversation_context", fake_prepare_semantic)
    monkeypatch.setattr(agent_run_support, "prepare_conversation_workspace", fake_workspace)
    monkeypatch.setattr(
        agent_stream, "resolve_agent_runtime_options",
        lambda **_kwargs: SimpleNamespace(workspace_backend="local"),
    )
    monkeypatch.setattr(agent_run_support, "build_runtime_skill_snapshot_async", fake_snapshot)
    monkeypatch.setattr(agent_run_support, "list_snapshot_skill_metadata", lambda _dir: [])
    monkeypatch.setattr(
        agent_run_support, "ConversationWorkspaceRuntime",
        runtime_cls or SimpleNamespace,
    )


async def _drain_until_checkpointer_failure(gen):
    """驱动到 get_checkpointer 处，借流式入口的兜底 except 收口。"""
    events = []
    with pytest.raises(AgentRunStreamFailedError):
        async for event in gen:
            events.append(event)
    return events


class _ExplodingCheckpointer:
    """真正的 async context manager：让代码确实走到 `async with get_checkpointer()`。"""

    def __init__(self, message: str):
        self.message = message
        self.entered = False

    def __call__(self):
        return self

    async def __aenter__(self):
        self.entered = True
        raise AssertionError(self.message)

    async def __aexit__(self, *_exc):
        return False


@pytest.mark.asyncio
async def test_stream_fails_fast_on_semantic_config_error(monkeypatch):
    _patch_stream_preamble(
        monkeypatch,
        _semantic_context(semantic_config_error="指标集不存在"),
        workspace_root="/tmp/unused",
    )
    events = await _drain_until_checkpointer_failure(
        agent_stream._run_chat_data_agent_stream(req=ChatDataStreamRequest(
            thread_id="t1",
            user_query="今天销量",
            datasource_id=None,
            user_id="u1",
            username="张三",
            member_datasource_ids=None,
            semantic_config_id=None,
            request=None,
            model_id=None,
            file_selections=None,
            enable_thinking=None,
            thinking_effort=None,
            skill_names=[],
            options={},
            file_ids=None,
            run_id=None,
        ),
        log_phase=lambda *_a, **_k: None,)
    )
    assert any("指标集不存在" in event for event in events)


@pytest.mark.asyncio
async def test_stream_rejects_datasource_binding_conflict(monkeypatch, tmp_path):
    _patch_stream_preamble(
        monkeypatch,
        _semantic_context(datasource_id="ds-2", datasource_meta={}),
        workspace_root=str(tmp_path),
    )

    async def fake_binding(*_args, **_kwargs):
        return "ds-1"  # 会话已绑定另一个数据源组

    checkpointer = _ExplodingCheckpointer("绑定冲突时应提前退出")
    monkeypatch.setattr(agent_stream.conversation, "resolve_datasource_binding", fake_binding)
    monkeypatch.setattr(agent_stream, "get_checkpointer", checkpointer)

    events = await _drain_until_checkpointer_failure(
        agent_stream._run_chat_data_agent_stream(req=ChatDataStreamRequest(
            thread_id="t1",
            user_query="今天销量",
            datasource_id="ds-2",
            user_id="u1",
            username="张三",
            member_datasource_ids=None,
            semantic_config_id=None,
            request=None,
            model_id=None,
            file_selections=None,
            enable_thinking=None,
            thinking_effort=None,
            skill_names=[],
            options={},
            file_ids=None,
            run_id=None,
        ),
        log_phase=lambda *_a, **_k: None,)
    )
    assert any("已绑定其他数据源组" in event for event in events)
    # 必须在 checkpointer 之前退出
    assert checkpointer.entered is False


@pytest.mark.asyncio
async def test_stream_resolves_attachments_and_offloads_long_input(monkeypatch, tmp_path):
    written = {}

    class RecordingRuntime:
        available_skill_names: list[str] = []

        def __init__(self, **_kwargs):
            pass

        async def write_text_file(self, **kwargs):
            written.update(kwargs)
            return {"bytes": len(kwargs["content"])}

    _patch_stream_preamble(
        monkeypatch,
        _semantic_context(),
        workspace_root=str(tmp_path),
        runtime_cls=RecordingRuntime,
    )

    files = {
        "f-ok": {"thread_id": "t1", "original_filename": "a.csv", "file_type": "csv",
                 "file_size": 10, "storage_dir": "a.csv"},
        "f-missing": None,
        "f-other": {"thread_id": "t2", "original_filename": "b.csv", "file_type": "csv",
                    "file_size": 10, "storage_dir": "b.csv"},
    }

    async def fake_get_file_info(file_id, **_kwargs):
        return files.get(file_id)

    checkpointer = _ExplodingCheckpointer("附件解析完成后无需继续")
    monkeypatch.setattr(agent_run_support, "get_file_info", fake_get_file_info)
    monkeypatch.setattr(agent_stream, "get_checkpointer", checkpointer)

    long_query = "销" * (agent_stream.USER_INPUT_OFFLOAD_THRESHOLD + 10)
    events = await _drain_until_checkpointer_failure(
        agent_stream._run_chat_data_agent_stream(req=ChatDataStreamRequest(
            thread_id="t1",
            user_query=long_query,
            datasource_id=None,
            user_id="u1",
            username="张三",
            member_datasource_ids=None,
            semantic_config_id=None,
            request=None,
            model_id=None,
            file_selections=None,
            enable_thinking=None,
            thinking_effort=None,
            skill_names=["demo-skill"],
            options={},
            file_ids=["f-ok", "f-missing", "f-other"],
            run_id=None,
        ),
        log_phase=lambda *_a, **_k: None,)
    )
    assert events  # 至少产出了兜底 error 事件
    # 确实走完了附件解析与超长输入转写，才到达 checkpointer
    assert checkpointer.entered is True

    # 超长输入已转写 outputs，并被登记为附件
    assert written["file_path"].startswith("/mnt/user-data/outputs/input-")
    assert written["content"] == long_query
    assert written["mode"] == "overwrite"


@pytest.mark.asyncio
async def test_stream_without_attachments_skips_offload(monkeypatch, tmp_path):
    calls = []

    class RecordingRuntime:
        available_skill_names: list[str] = []

        def __init__(self, **_kwargs):
            pass

        async def write_text_file(self, **kwargs):
            calls.append(kwargs)
            return {"bytes": 0}

    _patch_stream_preamble(
        monkeypatch, _semantic_context(), workspace_root=str(tmp_path), runtime_cls=RecordingRuntime,
    )

    checkpointer = _ExplodingCheckpointer("短输入不应写文件")
    monkeypatch.setattr(agent_stream, "get_checkpointer", checkpointer)
    await _drain_until_checkpointer_failure(
        agent_stream._run_chat_data_agent_stream(req=ChatDataStreamRequest(
            thread_id="t1",
            user_query="短问题",
            datasource_id=None,
            user_id="u1",
            username="张三",
            member_datasource_ids=None,
            semantic_config_id=None,
            request=None,
            model_id=None,
            file_selections=None,
            enable_thinking=None,
            thinking_effort=None,
            skill_names=[],
            options={},
            file_ids=None,
            run_id=None,
        ),
        log_phase=lambda *_a, **_k: None,)
    )
    # 走完了前段流程，但短输入没有触发任何转写
    assert checkpointer.entered is True
    assert calls == []


# ---------------------------------------------------------------------------
# Run 心跳租约
# ---------------------------------------------------------------------------


class _FakeOwnerTask:
    def __init__(self):
        self.cancelled_with: list[str] = []

    def done(self):
        return False

    def cancel(self, msg=None):
        self.cancelled_with.append(str(msg or ""))


def _run_heartbeat(monkeypatch, statuses, *, owner_token="tok-1"):
    """驱动 _heartbeat_agent_run：statuses 逐个返回，None 表示抛通用异常。"""
    from apps.chat_data_app.agent.run.agent_run_runtime import AgentRunLeaseLostError

    iterator = iter(statuses)
    renewals: list[tuple] = []

    async def fake_heartbeat(_run_id, _token):
        status = next(iterator)
        if isinstance(status, Exception):
            raise status
        return status

    async def fake_renew(run_id, token):
        renewals.append((run_id, token))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(agent_stream.agent_inbox_service, "heartbeat_run", fake_heartbeat)
    monkeypatch.setattr(agent_stream.agent_run_event_service, "renew_owner", fake_renew)
    monkeypatch.setattr(agent_stream.asyncio, "sleep", no_sleep)
    task = _FakeOwnerTask()
    asyncio.run(agent_stream._heartbeat_agent_run("run-hb", task, owner_token))
    return task, renewals


def test_heartbeat_renews_owner_while_active(monkeypatch):
    task, renewals = _run_heartbeat(monkeypatch, ["active", "finished"])
    assert renewals == [("run-hb", "tok-1")]
    assert task.cancelled_with == []


def test_heartbeat_stops_without_cancel_on_normal_transition(monkeypatch):
    # waiting_user / finished 属正常流转：停心跳但不取消流式任务
    task, _renewals = _run_heartbeat(monkeypatch, ["waiting_user"])
    assert task.cancelled_with == []


def test_heartbeat_cancels_owner_on_lease_lost(monkeypatch):
    from apps.chat_data_app.agent.run.agent_run_runtime import AgentRunLeaseLostError

    task, _ = _run_heartbeat(monkeypatch, [AgentRunLeaseLostError("租约被接管")])
    assert task.cancelled_with == ["agent_run_lease_lost"]


def test_heartbeat_cancels_owner_on_cancel_requested(monkeypatch):
    task, _ = _run_heartbeat(monkeypatch, ["cancelling"])
    assert task.cancelled_with == ["agent_run_cancelled"]


def test_heartbeat_retries_after_generic_failure(monkeypatch):
    # 首轮续约失败后 continue，第二轮成功
    task, renewals = _run_heartbeat(monkeypatch, [RuntimeError("redis down"), "active", "finished"])
    assert renewals == [("run-hb", "tok-1")]
    assert task.cancelled_with == []


def test_heartbeat_without_owner_token_skips_renewal(monkeypatch):
    _task, renewals = _run_heartbeat(monkeypatch, ["active", "finished"], owner_token=None)
    assert renewals == []
