"""conversation_event_service.py 覆盖率补充测试。

聚焦历史快照事务与恢复水位推进的分支：无事件短路、租约丢失直传、
非连接类错误不重试、连接类错误重试后成功、水位回退保护，以及
历史事件反序列化时的脏数据跳过与派生消息归类。
不访问真实 MySQL。
"""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy.exc import OperationalError

from apps.chat_data_app.agent.run.agent_inbox_service import AgentRunLeaseLostError
from apps.chat_data_app.agent.run.conversation_event_service import ConversationEventService

_build_history_payload = ConversationEventService._build_history_payload


def _connection_error() -> OperationalError:
    return OperationalError(None, None, RuntimeError(2003, "can't connect to MySQL server"))


class _SessionCtx:
    async def __aenter__(self):
        return "session"

    async def __aexit__(self, *_exc):
        return False


class SnapshotDao:
    """会话事件 DAO 替身：可注入前 N 次连接失败。"""

    def __init__(self, *, connection_failures=0, hard_error=None):
        self.session_context = _SessionCtx
        self.connection_failures = connection_failures
        self.hard_error = hard_error
        self.append_calls = 0
        self.max_sequence = 7

    async def append_events_in_session(self, _session, **_kwargs):
        self.append_calls += 1
        if self.hard_error is not None:
            raise self.hard_error
        if self.append_calls <= self.connection_failures:
            raise _connection_error()
        return []

    async def get_max_sequence_in_session(self, _session, _conversation_id):
        return self.max_sequence


class InboxDao:
    def __init__(self, run=None, *, run_error=None):
        self.run = (
            run
            if run is not None
            else SimpleNamespace(
                conversation_id="conv-1",
                user_id="user-1",
                record_status="active",
                owner_token="tok-1",
                record_content="{}",
            )
        )
        self.run_error = run_error

    async def get_run_by_id_for_update(self, _session, _run_id):
        if self.run_error is not None:
            raise self.run_error
        return self.run


def _service(dao=None, inbox_dao=None) -> ConversationEventService:
    return ConversationEventService(dao or SnapshotDao(), inbox_dao or InboxDao())


async def _persist(service, *, stream_id="5-0", **overrides):
    kwargs = {
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "operator_name": "用户一",
        "turn_id": "turn-1",
        "messages": [HumanMessage(content="你好", id="turn-1")],
        "reasoning_segments": [],
        "run_id": "run-1",
        "stream_id": stream_id,
        "owner_token": "tok-1",
    }
    kwargs.update(overrides)
    return await service.persist_turn_with_checkpoint(**kwargs)


@pytest.fixture
def no_retry_delay(monkeypatch):
    import apps.chat_data_app.agent.run.conversation_event_service as module

    async def skip(_delay):
        return None

    monkeypatch.setattr(module.asyncio, "sleep", skip)


# ---------------------------------------------------------------------------
# persist_turn_with_checkpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_turn_short_circuits_without_events():
    dao = SnapshotDao()
    result = await _persist(_service(dao), messages=[], reasoning_segments=[])
    assert result is None
    assert dao.append_calls == 0


@pytest.mark.asyncio
async def test_persist_turn_advances_checkpoint():
    dao = SnapshotDao()
    checkpoint = await _persist(_service(dao))
    assert checkpoint == {
        "run_id": "run-1",
        "turn_id": "turn-1",
        "history_sequence_no": 7,
        "stream_id": "5-0",
    }
    assert dao.append_calls == 1


@pytest.mark.asyncio
async def test_persist_turn_without_stream_id_skips_checkpoint():
    assert await _persist(_service(), stream_id=None) is None


@pytest.mark.asyncio
async def test_persist_turn_propagates_lease_lost_without_retry(no_retry_delay):
    dao = SnapshotDao()
    inbox = InboxDao(run_error=AgentRunLeaseLostError("已被其他 Worker 接管"))
    with pytest.raises(AgentRunLeaseLostError, match="已被其他 Worker 接管"):
        await _persist(_service(dao, inbox))
    assert dao.append_calls == 1  # 租约类错误不重试


@pytest.mark.asyncio
async def test_persist_turn_rejects_run_owned_by_another_worker(no_retry_delay):
    dao = SnapshotDao()
    stale_run = SimpleNamespace(
        conversation_id="conv-1",
        user_id="user-1",
        record_status="active",
        owner_token="other-token",
        record_content="{}",
    )
    with pytest.raises(AgentRunLeaseLostError, match="其他 Worker 接管"):
        await _persist(_service(dao, InboxDao(run=stale_run)))


@pytest.mark.asyncio
async def test_persist_turn_rejects_finished_run():
    dao = SnapshotDao()
    finished = SimpleNamespace(
        conversation_id="conv-1",
        user_id="user-1",
        record_status="finished",
        owner_token="tok-1",
        record_content="{}",
    )
    with pytest.raises(AgentRunLeaseLostError, match="当前运行已结束"):
        await _persist(_service(dao, InboxDao(run=finished)))


@pytest.mark.asyncio
async def test_persist_turn_does_not_retry_non_connection_error(no_retry_delay):
    dao = SnapshotDao(hard_error=RuntimeError("死锁了"))
    with pytest.raises(RuntimeError, match="死锁了"):
        await _persist(_service(dao))
    assert dao.append_calls == 1  # 非连接类错误直接抛出，不进入重试


@pytest.mark.asyncio
async def test_persist_turn_retries_connection_error_then_succeeds(no_retry_delay):
    dao = SnapshotDao(connection_failures=1)
    checkpoint = await _persist(_service(dao))
    assert dao.append_calls == 2
    assert checkpoint["stream_id"] == "5-0"


@pytest.mark.asyncio
async def test_persist_turn_gives_up_after_max_attempts(no_retry_delay):
    dao = SnapshotDao(connection_failures=99)
    with pytest.raises(OperationalError):
        await _persist(_service(dao))
    assert dao.append_calls > 1


# ---------------------------------------------------------------------------
# 恢复水位推进
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_keeps_newer_previous_watermark():
    run = SimpleNamespace(
        conversation_id="conv-1",
        user_id="user-1",
        record_status="active",
        owner_token="tok-1",
        record_content=json.dumps({"history_checkpoint": {"run_id": "run-1", "stream_id": "9-0"}}),
    )
    # 传入的 stream_id 比已记录的更旧：不回退，直接沿用旧水位
    checkpoint = await _persist(_service(inbox_dao=InboxDao(run=run)), stream_id="3-0")
    assert checkpoint == {"run_id": "run-1", "stream_id": "9-0"}


@pytest.mark.asyncio
async def test_checkpoint_rejects_foreign_run_watermark():
    run = SimpleNamespace(
        conversation_id="conv-1",
        user_id="user-1",
        record_status="active",
        owner_token="tok-1",
        record_content=json.dumps({"history_checkpoint": {"run_id": "run-other", "stream_id": "2-0"}}),
    )
    with pytest.raises(AgentRunLeaseLostError, match="恢复水位归属已变化"):
        await _persist(_service(inbox_dao=InboxDao(run=run)))


@pytest.mark.asyncio
async def test_validate_run_rejects_missing_or_mismatched_run():
    service = _service()
    with pytest.raises(AgentRunLeaseLostError, match="当前运行不存在"):
        service._validate_run_for_checkpoint(None, conversation_id="c", user_id="u", run_id="r", owner_token="t")
    other = SimpleNamespace(
        conversation_id="other", user_id="u", record_status="active", owner_token="t", record_content="{}"
    )
    with pytest.raises(AgentRunLeaseLostError, match="归属已变化"):
        service._validate_run_for_checkpoint(other, conversation_id="c", user_id="u", run_id="r", owner_token="t")


def test_normalize_stream_id_trims_and_treats_blank_as_none():
    assert ConversationEventService._normalize_stream_id(" 7-1 ") == "7-1"
    assert ConversationEventService._normalize_stream_id("   ") is None
    assert ConversationEventService._normalize_stream_id(None) is None


# ---------------------------------------------------------------------------
# 历史事件反序列化
# ---------------------------------------------------------------------------


def _row(sequence_no, event_type, payload, turn_id="turn-1"):
    return SimpleNamespace(
        sequence_no=sequence_no,
        event_type=event_type,
        payload=payload,
        turn_id=turn_id,
    )


def test_build_history_payload_skips_dirty_rows_and_classifies_events():
    rows = [
        _row(1, "user_message", '{"id":"turn-1","role":"user","content":"你好"}'),
        _row(2, "broken_json", "不是 JSON"),
        _row(3, "reasoning", '{"id":"turn-1","content":"思考中"}'),
        _row(4, "assistant_message", '{"id":"turn-1","role":"assistant","content":"答案"}'),
        _row(5, "tool_result", '{"tool_call_id":"c1","content":"结果"}'),
        _row(6, "unknown_type", '{"anything":1}'),
    ]
    payload = _build_history_payload(rows, '{"total": 9}', include_derived=True)

    assert [item["sequence_no"] for item in payload["history_events"]] == [1, 3, 4, 5, 6]
    # 用户消息 / 助手消息 / 工具结果都归入可见消息，推理单独成段
    assert [item["content"] for item in payload["messages"]] == ["你好", "答案", "结果"]
    assert [item["content"] for item in payload["reasoning_segments"]] == ["思考中"]
    assert payload["token_usage"] == {"total": 9}


def test_build_history_payload_can_skip_derived_lists():
    rows = [
        _row(1, "user_message", '{"id":"turn-1","role":"user","content":"你好"}'),
        _row(2, "reasoning", '{"id":"turn-1","content":"思考中"}'),
    ]
    payload = _build_history_payload(rows, None, include_derived=False)
    assert payload["messages"] == []
    assert payload["reasoning_segments"] == []
    assert len(payload["history_events"]) == 2
    assert payload["token_usage"] is None


def test_build_history_payload_tolerates_empty_rows():
    payload = _build_history_payload([], None, include_derived=True)
    assert payload["messages"] == [] and payload["history_events"] == []


@pytest.mark.asyncio
async def test_load_history_in_session_survives_token_usage_failure():
    class SessionDao(SnapshotDao):
        async def list_visible_event_window_in_session(self, _session, **_kwargs):
            return [_row(1, "user_message", '{"id":"turn-1","role":"user","content":"保留"}')], False

        async def get_latest_token_usage_payload_in_session(self, _session, **_kwargs):
            raise RuntimeError("token usage 表不可用")

    class SessionInboxDao(InboxDao):
        async def get_active_run_snapshot(self, _session, _conversation_id, _user_id):
            return None

    history = await _service(SessionDao(), SessionInboxDao()).load_history_with_recovery(
        conversation_id="conv-1",
        user_id="user-1",
    )
    assert history["messages"][0]["content"] == "保留"
    assert history["token_usage"] is None
    assert history["active_run"] is None


@pytest.mark.asyncio
async def test_load_history_with_recovery_exposes_active_run_watermark():
    class SessionDao(SnapshotDao):
        async def list_visible_event_window_in_session(self, _session, **_kwargs):
            return [_row(1, "user_message", '{"id":"turn-1","role":"user","content":"保留"}')], False

        async def get_latest_token_usage_payload_in_session(self, _session, **_kwargs):
            return '{"total": 5}'

    active_run = SimpleNamespace(
        run_id="run-9",
        record_status="active",
        turn_id="turn-1",
        record_content=json.dumps({"history_checkpoint": {"run_id": "run-9", "stream_id": "4-0"}}),
    )

    class SessionInboxDao(InboxDao):
        async def get_active_run_snapshot(self, _session, _conversation_id, _user_id):
            return active_run

    history = await _service(SessionDao(), SessionInboxDao()).load_history_with_recovery(
        conversation_id="conv-1",
        user_id="user-1",
    )
    assert history["token_usage"] == {"total": 5}
    assert history["active_run"]["run_id"] == "run-9"


@pytest.mark.asyncio
async def test_load_history_reports_none_when_no_active_run():
    class Dao(SnapshotDao):
        async def list_visible_events(self, **_kwargs):
            return [_row(1, "assistant_message", '{"id":"t","role":"assistant","content":"ok"}')]

        async def get_latest_token_usage_payload(self, **_kwargs):
            return '{"total": 3}'

    history = await _service(Dao()).load_history(conversation_id="conv-1", user_id="user-1")
    assert history["token_usage"] == {"total": 3}
    assert history["messages"][0]["role"] == "assistant"
