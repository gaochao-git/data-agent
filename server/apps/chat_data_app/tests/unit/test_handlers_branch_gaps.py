"""handlers.py 分支缺口补测。

聚焦 `_generate_chat_data_stream` 闭包之外（无需驱动整条 SSE 流）就能触达的分支：
- 纯归一化函数（思考档位、工具事件格式化）
- 可注入 fake agent / fake DAO 的小协程（AI 消息时间戳回填、快照 writer、心跳续约）
- `_ToolStreamState` 的状态机边角（非 dict 元素、缺 name、index 映射）

闭包内的分支（`write_model_snapshot` 空 adapter、`mark_resume_checkpointed` 等）
需要驱动完整流，成本高出数倍，不在本文件范围内。
"""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage, ToolMessageChunk

from apps.chat_data_app import handlers
from apps.chat_data_app.agent.run.agent_inbox_service import AgentRunLeaseLostError


# --------------------------------------------------------------------------- #
# _normalize_thinking_effort（291-296）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("HIGH", "high"),          # 大小写与空白都要归一化
        ("  Medium  ", "medium"),
        ("", None),                # 空串归一后仍是空 → None
        ("   ", None),
        (None, None),              # 非 str 直接 None
        (3, None),
        (["high"], None),
    ],
)
def test_normalize_thinking_effort(raw, expected):
    assert handlers._normalize_thinking_effort(raw) == expected


# --------------------------------------------------------------------------- #
# _persist_latest_ai_message_timestamp（299-321）
# --------------------------------------------------------------------------- #


class _FakeTimestampAgent:
    """最小 agent 替身：只提供 aupdate_state / aget_state，可注入异常。"""

    def __init__(self, *, aupdate_error: Exception | None = None, with_aget_state: bool = True):
        self.aupdate_calls: list[dict] = []
        self._aupdate_error = aupdate_error
        if with_aget_state:
            async def aget_state(config):
                return SimpleNamespace(values={"messages": [], "source": "aget_state"})

            self.aget_state = aget_state

    async def aupdate_state(self, config, values, as_node=None):
        self.aupdate_calls.append({"config": config, "values": values, "as_node": as_node})
        if self._aupdate_error is not None:
            raise self._aupdate_error


def _state_with(messages) -> SimpleNamespace:
    return SimpleNamespace(values={"messages": messages})


@pytest.mark.asyncio
async def test_persist_timestamp_returns_state_when_no_ai_message():
    """消息里没有 AIMessage 时直接原样返回，不触碰 agent（305->304、308->309）。"""
    state = _state_with([HumanMessage(content="用户问题"), ToolMessage(content="工具结果", tool_call_id="c1")])

    result = await handlers._persist_latest_ai_message_timestamp(
        _FakeTimestampAgent(), {"configurable": {}}, state
    )

    assert result is state


@pytest.mark.asyncio
async def test_persist_timestamp_handles_none_state_and_empty_messages():
    state = None
    assert await handlers._persist_latest_ai_message_timestamp(_FakeTimestampAgent(), {}, state) is None

    empty = _state_with([])
    assert await handlers._persist_latest_ai_message_timestamp(_FakeTimestampAgent(), {}, empty) is empty

    non_dict = SimpleNamespace(values=None)
    assert await handlers._persist_latest_ai_message_timestamp(_FakeTimestampAgent(), {}, non_dict) is non_dict


@pytest.mark.asyncio
async def test_persist_timestamp_skips_when_timestamp_already_present():
    """latest AI 消息已带 timestamp 时不重复回填（311->312）。"""
    agent = _FakeTimestampAgent()
    state = _state_with([
        HumanMessage(content="问题"),
        AIMessage(content="答案", additional_kwargs={"timestamp": "2026-08-30 00:00:00"}),
    ])

    result = await handlers._persist_latest_ai_message_timestamp(agent, {"configurable": {}}, state)

    assert result is state
    assert agent.aupdate_calls == []


@pytest.mark.asyncio
async def test_persist_timestamp_skips_when_agent_cannot_update_state():
    """agent 没有 aupdate_state 时直接放弃，不抛错（311->312）。"""
    state = _state_with([AIMessage(content="答案")])

    result = await handlers._persist_latest_ai_message_timestamp(object(), {"configurable": {}}, state)

    assert result is state


@pytest.mark.asyncio
async def test_persist_timestamp_writes_timestamp_and_returns_fresh_state():
    """正常回填：写入 timestamp 并把 aget_state 的最新状态返回（304->305）。"""
    agent = _FakeTimestampAgent()
    state = _state_with([HumanMessage(content="问题"), AIMessage(content="答案")])

    result = await handlers._persist_latest_ai_message_timestamp(agent, {"configurable": {}}, state)

    assert result is not state
    assert result.values["source"] == "aget_state"
    assert len(agent.aupdate_calls) == 1
    call = agent.aupdate_calls[0]
    assert call["as_node"] == "model"
    patched = call["values"]["messages"][0]
    assert patched.additional_kwargs["timestamp"]
    # 原消息不应被就地修改
    assert "timestamp" not in state.values["messages"][1].additional_kwargs


@pytest.mark.asyncio
async def test_persist_timestamp_falls_back_to_state_when_agent_has_no_aget_state():
    agent = _FakeTimestampAgent(with_aget_state=False)
    state = _state_with([AIMessage(content="答案")])

    result = await handlers._persist_latest_ai_message_timestamp(agent, {"configurable": {}}, state)

    assert result is state
    assert len(agent.aupdate_calls) == 1


@pytest.mark.asyncio
async def test_persist_timestamp_swallows_agent_update_failure():
    """aupdate_state 抛错只告警，不向上冒泡（319-321）。"""
    agent = _FakeTimestampAgent(aupdate_error=RuntimeError("checkpointer unavailable"))
    state = _state_with([AIMessage(content="答案")])

    result = await handlers._persist_latest_ai_message_timestamp(agent, {"configurable": {}}, state)

    assert result is state


# --------------------------------------------------------------------------- #
# _persist_agent_turn_history（324-386）：state_override 直传分支
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_persist_agent_turn_history_uses_state_override_without_touching_adapter():
    """传入 state_override 时不再回落到 adapter.state（339->341）。"""

    class _ExplodingAdapter:
        @property
        def state(self):
            raise AssertionError("不应读取 adapter.state")

    await handlers._persist_agent_turn_history(
        _ExplodingAdapter(),
        conversation_id="conv-1",
        user_id="u1",
        username=None,
        turn_id="turn-1",
        state_override={"messages": "不是列表"},
    )


@pytest.mark.asyncio
async def test_persist_partial_agent_turn_history_noop_without_adapter():
    """adapter 为 None 时静默返回，不抛异常（543->544）。"""
    await handlers._persist_partial_agent_turn_history(
        None, conversation_id="conv-1", user_id="u1", username=None, turn_id="turn-1"
    )


# --------------------------------------------------------------------------- #
# _LatestModelSnapshotWriter（389-427）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_snapshot_writer_submit_copies_only_list_fields():
    """messages / context_compression_events 做浅拷贝，其余字段保持引用（405->407、407->408）。"""
    writes: list[dict] = []

    async def write(snapshot):
        writes.append(snapshot)

    writer = handlers._LatestModelSnapshotWriter(write, task_name="t")
    marker = object()
    state = {
        "messages": [HumanMessage(content="a")],
        "context_compression_events": [{"kind": "x"}],
        "scalar": marker,
    }

    writer.submit(state)

    pending = writer._pending
    assert pending["messages"] is not state["messages"]
    assert pending["messages"] == state["messages"]
    assert pending["context_compression_events"] is not state["context_compression_events"]
    assert pending["scalar"] is marker

    await writer.flush()
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_snapshot_writer_submit_tolerates_none_and_non_list_fields():
    writes: list[dict] = []

    async def write(snapshot):
        writes.append(snapshot)

    writer = handlers._LatestModelSnapshotWriter(write, task_name="t")
    writer.submit(None)
    await writer.flush()
    assert writes == [{}]

    writer.submit({"messages": None, "context_compression_events": "oops"})
    await writer.flush()
    assert writes[-1] == {"messages": None, "context_compression_events": "oops"}


@pytest.mark.asyncio
async def test_snapshot_writer_swallows_write_failure():
    """后台写入抛错只告警，不影响 flush 返回（426-427）。"""

    async def write(_snapshot):
        raise RuntimeError("db down")

    writer = handlers._LatestModelSnapshotWriter(write, task_name="t")
    writer.submit({"n": 1})
    await writer.flush()
    assert writer._pending is None


@pytest.mark.asyncio
async def test_snapshot_writer_flush_rechecks_when_newer_task_appears():
    """flush 期间若又有快照被提交并起了新任务，必须继续等新任务排空（417->414）。

    构造方式：让第一个 runner 阻塞在 gate 上，flush 挂在 `await runner` 处；
    gate.set() 后 runner 先结束并把自己的完成回调排入队列，随后才轮到同样等在
    gate 上的 helper —— helper 这时看到的 `writer._task` 已完成，于是 submit 会
    新建第二个任务。flush 恢复时发现 `_task` 已经换人，需要再等一轮。
    """
    writes: list[dict] = []
    gate = asyncio.Event()

    async def write(snapshot):
        writes.append(snapshot)
        if snapshot.get("n") == 1:
            await gate.wait()

    writer = handlers._LatestModelSnapshotWriter(write, task_name="t")
    writer.submit({"n": 1})
    await asyncio.sleep(0)  # runner 取走 pending，阻塞在 gate

    flush_task = asyncio.create_task(writer.flush())
    await asyncio.sleep(0)  # flush 挂起在 await runner

    async def submit_after_runner_finishes():
        await gate.wait()
        writer.submit({"n": 2})

    helper = asyncio.create_task(submit_after_runner_finishes())
    await asyncio.sleep(0)  # helper 注册为 gate 的第二个等待者

    gate.set()
    await flush_task
    await helper

    assert [item["n"] for item in writes] == [1, 2]
    assert writer._pending is None


# --------------------------------------------------------------------------- #
# _merge_message_snapshots（450-468）
# --------------------------------------------------------------------------- #


def test_merge_message_snapshots_keeps_messages_without_dedup_key():
    """`_stream_message_key` 返回 None 的消息不去重，全部追加且不加进 seen（466->461）。"""
    existing = [HumanMessage(content="已有")]
    no_key_a = ToolMessage(content="结果A", tool_call_id="")
    no_key_b = ToolMessage(content="结果B", tool_call_id="")

    merged = handlers._merge_message_snapshots(existing, [no_key_a, no_key_b])

    assert merged == [existing[0], no_key_a, no_key_b]


def test_merge_message_snapshots_dedups_by_key():
    tool_a = ToolMessage(content="结果A", tool_call_id="call-1")
    tool_a_dup = ToolMessage(content="结果A(重复)", tool_call_id="call-1")

    merged = handlers._merge_message_snapshots([tool_a], [tool_a_dup])

    assert merged == [tool_a]


# --------------------------------------------------------------------------- #
# _format_tool_event（562-597）
# --------------------------------------------------------------------------- #


def test_format_tool_event_includes_args_and_message():
    """args / message 非空时挂到事件体上（590->592、595->596）。"""
    event = handlers._format_tool_event(
        "file_read",
        "call-1",
        "completed",
        args={"path": "/mnt/a"},
        result="文件内容",
        message="读取成功",
        timestamp="2026-08-30 00:00:00",
    )

    assert '"args"' in event and "/mnt/a" in event
    assert "读取成功" in event
    assert '"timestamp": "2026-08-30 00:00:00"' in event


def test_format_tool_event_omits_optional_fields():
    event = handlers._format_tool_event("file_read", None, "running")

    assert '"args"' not in event
    assert '"message"' not in event
    assert '"result"' not in event


# --------------------------------------------------------------------------- #
# _ToolStreamState（695-801）
# --------------------------------------------------------------------------- #


def test_handle_tool_calls_skips_non_dict_entries():
    """非 dict 元素直接跳过（713->714）。"""
    state = handlers._ToolStreamState()

    events = state.handle_tool_calls(["not-a-dict", None, 42])

    assert events == []
    assert state.tool_names == {}
    assert state.active_tool_call_id is None


def test_handle_tool_calls_records_index_and_args_fallback():
    """带 index 的调用登记到下标记映射，dict args 存入兜底表（722->723）。"""
    state = handlers._ToolStreamState()

    state.handle_tool_calls([
        {"id": "call-1", "name": "file_read", "index": 0, "args": {"path": "/a"}},
        {"id": "call-2", "name": "file_write", "index": "not-an-int", "args": {"path": "/b"}},
        {"id": "call-3", "name": "glob", "index": 1, "args": "raw-string"},
        {"id": "", "name": "missing-id"},
        {"id": "call-4", "name": ""},
    ])

    assert state.index_map == {0: "call-1", 1: "call-3"}
    assert state.args_fallback == {"call-1": {"path": "/a"}, "call-2": {"path": "/b"}}
    assert "call-3" not in state.args_fallback
    assert state.tool_names == {"call-1": "file_read", "call-2": "file_write", "call-3": "glob"}


def test_handle_tool_call_chunks_skips_non_dict_entries():
    """非 dict 分片跳过（733->734）。"""
    state = handlers._ToolStreamState()

    assert state.handle_tool_call_chunks([None, "junk", 7]) == []
    assert state.tool_names == {}


def test_handle_tool_call_chunks_without_name_keeps_id_only():
    """有 id 没 name 时不写入工具名，也因拿不到工具名而不发 running 事件（740->742、747->750）。"""
    state = handlers._ToolStreamState()

    events = state.handle_tool_call_chunks([{"id": "call-9", "args": "{}"}])

    assert events == []
    assert state.active_tool_call_id == "call-9"
    assert "call-9" not in state.tool_names


def test_handle_tool_call_chunks_records_index_and_emits_running():
    """带 index 且能解析出工具名时登记下标并补发 running（743->744）。"""
    state = handlers._ToolStreamState()

    events = state.handle_tool_call_chunks([
        {"id": "call-x", "name": "file_read", "index": 3, "args": '{"pa'},
    ])

    assert state.index_map == {3: "call-x"}
    assert state.tool_names["call-x"] == "file_read"
    assert state.args_buffer["call-x"] == '{"pa'
    # _running_event 一次补发「工具开始」+「工具执行中」两条事件
    assert len(events) == 2
    assert all("running" in event and "file_read" in event for event in events)


def test_handle_tool_call_chunks_resolves_id_from_index_map():
    """分片只有 index 没有 id 时从下标记映射反查（覆盖 750-753 的三段兜底）。"""
    state = handlers._ToolStreamState()
    state.index_map[5] = "call-known"

    state.handle_tool_call_chunks([{"index": 5, "args": "th"}])

    assert state.args_buffer["call-known"] == "th"


def test_build_tool_message_payload_attaches_accumulated_args():
    """tool_call_id 非空时把累积参数挂上并清理缓冲（770->777）。"""
    state = handlers._ToolStreamState()
    state.args_buffer["call-1"] = '{"path": "/a"}'
    state.args_fallback["call-1"] = {"path": "/fallback"}
    state.tool_names["call-1"] = "file_read"

    payload = state.build_tool_message_payload(
        ToolMessageChunk(content="文件内容", tool_call_id="call-1")
    )

    assert payload["type"] == "tool"
    assert payload["tool_name"] == "file_read"
    assert payload["status"] == "completed"
    assert payload["accumulated_args"] == {"path": "/a"}
    assert state.args_buffer == {}
    assert state.args_fallback == {}


def test_build_tool_message_payload_marks_error_and_timing():
    state = handlers._ToolStreamState()

    chunk = ToolMessageChunk(
        content="Error: 工具执行失败",
        tool_call_id="call-2",
        additional_kwargs={"started_at_ms": 100, "ended_at_ms": 250},
    )
    payload = state.build_tool_message_payload(chunk)

    assert payload["status"] == "error"
    assert payload["started_at_ms"] == 100
    assert payload["ended_at_ms"] == 250
    assert payload["accumulated_args"] == {}


def test_build_tool_message_payload_skips_args_without_tool_call_id():
    """没有 tool_call_id 时不挂累积参数，工具名回落到消息自带的 name（770->777）。"""
    state = handlers._ToolStreamState()
    state.args_buffer[""] = "ignored"

    payload = state.build_tool_message_payload(ToolMessageChunk(content="结果", tool_call_id=""))

    assert "accumulated_args" not in payload
    assert payload["tool_name"] == ""
    assert state.args_buffer == {"": "ignored"}


# --------------------------------------------------------------------------- #
# _resolve_end_usage / _parse_stream_event
# --------------------------------------------------------------------------- #


def _adapter_with(**kwargs) -> handlers._AgentSseStreamAdapter:
    adapter = handlers._AgentSseStreamAdapter(
        agent=SimpleNamespace(), initial_state={}, config={}
    )
    for key, value in kwargs.items():
        setattr(adapter, key, value)
    return adapter


def test_resolve_end_usage_prefers_latest_in_memory_payload():
    """显式要求最新用量时返回内存里的完整 payload（1052->1053）。"""
    adapter = _adapter_with(
        state=SimpleNamespace(values={"token_usage": {"llm_calls": 1}}),
        latest_token_usage={"total_tokens": 999},
    )

    assert adapter._resolve_end_usage(True) == {"total_tokens": 999}


def test_resolve_end_usage_treats_legacy_checkpoint_as_authoritative():
    """新版 checkpoint 只存计数器，命中断言集合时才以 state 为准（1056->1057）。"""
    adapter = _adapter_with(
        state=SimpleNamespace(values={"token_usage": {"llm_calls": 3, "breakdown": []}}),
        latest_token_usage={"total_tokens": 999},
    )

    assert adapter._resolve_end_usage(False) == {"llm_calls": 3, "breakdown": []}


def test_resolve_end_usage_keeps_legacy_checkpoint_with_breakdown_only():
    adapter = _adapter_with(
        state=SimpleNamespace(values={"token_usage": {"breakdown": [{"role": "human"}]}}),
        latest_token_usage={"total_tokens": 999},
    )

    assert adapter._resolve_end_usage(False) == {"breakdown": [{"role": "human"}]}


def test_resolve_end_usage_falls_back_when_state_has_no_usage():
    adapter = _adapter_with(state=None, latest_token_usage={"total_tokens": 7})

    assert adapter._resolve_end_usage(False) == {"total_tokens": 7}


def test_parse_stream_event_marks_unknown_shape():
    """既不是二元组也不是已知 stream_mode 时归为 unknown（1062->1070）。"""
    sentinel = object()

    assert handlers._AgentSseStreamAdapter._parse_stream_event(sentinel) == ("unknown", sentinel)
    assert handlers._AgentSseStreamAdapter._parse_stream_event(("a", "b", "c")) == (
        "unknown",
        ("a", "b", "c"),
    )


# --------------------------------------------------------------------------- #
# _heartbeat_agent_run（1194-1225）
# --------------------------------------------------------------------------- #


@pytest.fixture
def _fast_heartbeat(monkeypatch):
    """把心跳里的 1 秒 sleep 换成一次事件循环让出，避免用例真的等 1 秒。"""
    real_sleep = asyncio.sleep

    async def fast_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr(handlers.asyncio, "sleep", fast_sleep)


async def _pending_forever():
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_heartbeat_lease_lost_without_owner_task_returns_quietly(_fast_heartbeat, monkeypatch):
    """租约丢失但没有可取消的宿主任务时直接返回（1207->1209）。"""

    async def heartbeat_run(run_id, owner_token):
        raise AgentRunLeaseLostError("taken over")

    monkeypatch.setattr(handlers.agent_inbox_service, "heartbeat_run", heartbeat_run)

    await handlers._heartbeat_agent_run("run-1", None, "tok")


@pytest.mark.asyncio
async def test_heartbeat_lease_lost_cancels_owner_task(_fast_heartbeat, monkeypatch):
    """租约丢失且宿主任务仍在跑时协作取消（1207->1208）。"""

    async def heartbeat_run(run_id, owner_token):
        raise AgentRunLeaseLostError("taken over")

    monkeypatch.setattr(handlers.agent_inbox_service, "heartbeat_run", heartbeat_run)

    owner = asyncio.create_task(_pending_forever())
    await handlers._heartbeat_agent_run("run-1", owner, "tok")

    assert owner.cancelling()
    with pytest.raises(asyncio.CancelledError):
        await owner


@pytest.mark.asyncio
async def test_heartbeat_cancelling_status_without_owner_task_returns(_fast_heartbeat, monkeypatch):
    """状态变为 cancelling 但没有宿主任务时直接返回（1214->1216）。"""

    async def heartbeat_run(run_id, owner_token):
        return "cancelling"

    monkeypatch.setattr(handlers.agent_inbox_service, "heartbeat_run", heartbeat_run)

    await handlers._heartbeat_agent_run("run-1", None, "tok")


@pytest.mark.asyncio
async def test_heartbeat_cancelling_status_cancels_owner_task(_fast_heartbeat, monkeypatch):
    """状态变为 cancelling 时取消本地执行（1214->1215）。"""

    async def heartbeat_run(run_id, owner_token):
        return "cancelling"

    monkeypatch.setattr(handlers.agent_inbox_service, "heartbeat_run", heartbeat_run)

    owner = asyncio.create_task(_pending_forever())
    await handlers._heartbeat_agent_run("run-1", owner, "tok")

    assert owner.cancelling()
    with pytest.raises(asyncio.CancelledError):
        await owner


@pytest.mark.asyncio
async def test_heartbeat_renews_owner_lease_while_active(_fast_heartbeat, monkeypatch):
    """active 且持有 owner_token 时续约事件流租约（1204->1205）。"""
    renewed: list[tuple] = []
    calls = {"n": 0}

    async def heartbeat_run(run_id, owner_token):
        calls["n"] += 1
        return "active"

    async def renew_owner(run_id, owner_token):
        renewed.append((run_id, owner_token))

    monkeypatch.setattr(handlers.agent_inbox_service, "heartbeat_run", heartbeat_run)
    monkeypatch.setattr(handlers.agent_run_event_service, "renew_owner", renew_owner)

    owner = asyncio.create_task(_pending_forever())
    task = asyncio.create_task(handlers._heartbeat_agent_run("run-1", owner, "tok"))
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()

    assert renewed and renewed[0] == ("run-1", "tok")
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner


# --------------------------------------------------------------------------- #
# _persist_agent_turn_history：带 Run 的 checkpoint 写入分支（374->375）
# --------------------------------------------------------------------------- #


def _turn_history_adapter() -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(values={"messages": []}),
        archived_turn_messages=[],
        reasoning_segments=[],
        latest_token_usage=None,
    )


@pytest.mark.asyncio
async def test_persist_turn_history_uses_checkpoint_writer_when_run_id_present(monkeypatch):
    """带 run_id 且给了游标提供者时走 checkpoint 写入，游标由 provider 现场取（374->375）。"""
    captured: dict = {}

    async def persist_turn_with_checkpoint(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        handlers.conversation_event_service,
        "persist_turn_with_checkpoint",
        persist_turn_with_checkpoint,
    )

    await handlers._persist_agent_turn_history(
        _turn_history_adapter(),
        conversation_id="conv-1",
        user_id="u1",
        username=None,
        turn_id="turn-1",
        state_override={"messages": []},
        run_id="run-1",
        owner_token="tok",
        stream_event_id_provider=lambda: "stream-9",
    )

    assert captured["run_id"] == "run-1"
    assert captured["owner_token"] == "tok"
    assert captured["stream_id"] == "stream-9"
    assert captured["turn_id"] == "turn-1"
    assert captured["operator_name"] == "u1"


@pytest.mark.asyncio
async def test_persist_turn_history_prefers_snapshot_stream_id(monkeypatch):
    """显式给了快照游标时不再调用 provider（379-383 的三元短路）。"""
    captured: dict = {}

    async def persist_turn_with_checkpoint(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        handlers.conversation_event_service,
        "persist_turn_with_checkpoint",
        persist_turn_with_checkpoint,
    )

    await handlers._persist_agent_turn_history(
        _turn_history_adapter(),
        conversation_id="conv-1",
        user_id="u1",
        username="测试用户",
        turn_id="turn-1",
        state_override={"messages": []},
        run_id="run-1",
        owner_token="tok",
        stream_event_id_provider=lambda: "stream-from-provider",
        snapshot_stream_id="stream-from-snapshot",
    )

    assert captured["stream_id"] == "stream-from-snapshot"
    assert captured["operator_name"] == "测试用户"


# --------------------------------------------------------------------------- #
# _AgentSseStreamAdapter.stream_events / finish
# --------------------------------------------------------------------------- #


class _ScriptedAgent:
    """按脚本产出 astream 事件；可选在末尾抛错。"""

    def __init__(self, events, *, raise_exc: Exception | None = None):
        self._events = events
        self._raise_exc = raise_exc

    async def astream(self, *_args, **_kwargs):
        for event in self._events:
            yield event
        if self._raise_exc is not None:
            raise self._raise_exc


class _DisconnectedRequest:
    async def is_disconnected(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_stream_events_invokes_on_stream_started_once():
    """首个事件到达时回调一次 on_stream_started 并标记 graph 已启动（869->870）。"""
    calls: list[int] = []

    async def on_started():
        calls.append(1)

    usage_event = ("custom", {"type": "token_usage", "usage": {"total_tokens": 1}})
    adapter = handlers._AgentSseStreamAdapter(
        agent=_ScriptedAgent([usage_event, usage_event]),
        initial_state={},
        config={},
        on_stream_started=on_started,
    )

    events = [event async for event in adapter.stream_events()]

    assert calls == [1]
    assert adapter.graph_started is True
    assert len(events) == 2


@pytest.mark.asyncio
async def test_stream_events_stops_immediately_when_client_disconnected():
    """客户端断开后收到首个事件就收流，标记 disconnected（871->872）。"""
    adapter = handlers._AgentSseStreamAdapter(
        agent=_ScriptedAgent([("custom", {"type": "tool_progress", "tool_call_id": "c1"})]),
        initial_state={},
        config={},
        request=_DisconnectedRequest(),
    )

    events = [event async for event in adapter.stream_events()]

    assert events == []
    assert adapter.disconnected is True


@pytest.mark.asyncio
async def test_stream_events_skips_unknown_event_kind():
    """既不是 custom 也不是 messages 的事件直接跳过（879->880）。"""
    adapter = handlers._AgentSseStreamAdapter(
        agent=_ScriptedAgent(["不是二元组", ("updates", {"node": "model"})]),
        initial_state={},
        config={},
    )

    events = [event async for event in adapter.stream_events()]

    # ("updates", {...}) 会被 _parse_stream_event 兜底成 messages 事件并继续处理
    assert events == []


@pytest.mark.asyncio
async def test_stream_events_emits_alarm_and_reraises_on_stream_failure(monkeypatch):
    """流抛错且还没见到任何可见内容时发告警，再把异常原样抛给 Runtime（891->893）。"""
    alarms: list[dict] = []
    monkeypatch.setattr(
        handlers,
        "emit_llm_connection_alarm_if_needed",
        lambda exc, **kwargs: alarms.append({"exc": exc, **kwargs}),
    )

    adapter = handlers._AgentSseStreamAdapter(
        agent=_ScriptedAgent([], raise_exc=RuntimeError("model unavailable")),
        initial_state={},
        config={},
    )

    with pytest.raises(RuntimeError, match="model unavailable"):
        [event async for event in adapter.stream_events()]

    assert alarms and isinstance(alarms[0]["exc"], RuntimeError)
    assert alarms[0]["ext2"]["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_stream_events_yields_events_from_tool_call_chunks():
    """工具调用分片产生的 running 事件要透传给前端（1037->1038）。"""
    chunk = AIMessageChunk(
        content="",
        tool_call_chunks=[{"id": "call-1", "name": "file_read", "index": 0, "args": '{"pa'}],
    )
    adapter = handlers._AgentSseStreamAdapter(
        agent=_ScriptedAgent([("messages", (chunk, {"langgraph_node": "model"}))]),
        initial_state={},
        config={},
    )

    events = [event async for event in adapter.stream_events()]

    assert events
    assert any("file_read" in event for event in events)


@pytest.mark.asyncio
async def test_finish_persists_latest_ai_message_timestamp_when_requested():
    """finish(persist_latest_ai_timestamp=True) 会把回填后的最新状态返回（907->908）。"""
    agent = _FakeTimestampAgent()
    agent.initial_state = {}
    adapter = handlers._AgentSseStreamAdapter(agent=agent, initial_state={}, config={})

    payload = await adapter.finish(persist_latest_ai_timestamp=True)

    assert agent.aupdate_calls == []
    assert payload is not None


@pytest.mark.asyncio
async def test_finish_without_timestamp_persist_keeps_agent_state():
    agent = _FakeTimestampAgent()
    adapter = handlers._AgentSseStreamAdapter(agent=agent, initial_state={}, config={})

    await adapter.finish()

    assert adapter.state is not None
    assert adapter.state.values["source"] == "aget_state"


# --------------------------------------------------------------------------- #
# sql_completion_handler：skill_names 归一化（1702-1707）
# --------------------------------------------------------------------------- #


@pytest.fixture
def _stub_completion_runtime(monkeypatch):
    captured: dict = {}

    async def fake_get_or_create(*_args, **_kwargs):
        return {}

    async def fake_start_run(conversation_id, user_id, username, runtime_context, **_kwargs):
        captured["runtime_context"] = runtime_context
        return "run-test", []

    def fake_submit(**_kwargs):
        return None

    async def fake_event_stream(**_kwargs):
        if False:  # pragma: no cover - 保持生成器语义
            yield {}

    monkeypatch.setattr(handlers.conversation, "get_or_create_conversation", fake_get_or_create)
    monkeypatch.setattr(handlers.agent_inbox_service, "start_run", fake_start_run)
    monkeypatch.setattr(handlers.agent_run_runtime, "submit", fake_submit)
    monkeypatch.setattr(handlers.agent_run_event_service, "stream", fake_event_stream)
    return captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("单个技能", ["单个技能"]),                      # str → 单元素列表（1702->1703）
        ([" 技能A ", "技能B", "", None], ["技能A", "技能B"]),  # list → 去空白去空
        ("非法类型", ["非法类型"]),
        (123, []),                                    # 既不是 str 也不是 list（1704->1707）
        ({"name": "x"}, []),
        (None, []),
    ],
)
async def test_sql_completion_handler_normalizes_skill_names(_stub_completion_runtime, raw, expected):
    await handlers.sql_completion_handler(
        conversation_id="conv-1",
        query="查订单",
        options={"datasource_id": "ds_a", "skill_names": raw},
        user_id="u1",
        username="测试用户",
    )

    assert _stub_completion_runtime["runtime_context"]["skill_names"] == expected
