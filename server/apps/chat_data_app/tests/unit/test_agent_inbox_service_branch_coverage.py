"""agent_inbox_service 分支覆盖测试。

用 FakeDao 直接注入每个 DAO 调用的返回值，把「防御性 return / 竞争路径 / 参数校验」
这些真实 DB 下难以稳定复现的分支逐个走通；纯静态方法则直接调用断言。
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.chat_data_app.agent.run import agent_inbox_service as inbox_module
from apps.chat_data_app.agent.run.agent_inbox_service import (
    AgentInboxService,
    AgentQuestionNotFoundError,
    AgentRunLeaseLostError,
    AgentRunNotActiveError,
    SteerMessageNotFoundError,
    SteerMessageNotPendingError,
    is_valid_redis_stream_id,
    normalize_history_checkpoint,
)

NOW = datetime(2026, 1, 1, 12, 0, 0)


class FakeDao:
    """未显式配置的方法一律返回 None 并记录调用名，便于断言调用路径。"""

    def __init__(self, **returns):
        returns.setdefault("now", NOW)  # 时间到处都要用，未显式覆盖时给稳定值
        for list_method in ("claim_messages", "list_claimed_messages", "list_expired_runs"):
            returns.setdefault(list_method, [])  # 列表型 DAO 真实实现不会返回 None
        self.returns = returns
        self.calls: list[str] = []

    def __getattr__(self, name):
        async def _call(*args, **kwargs):
            self.calls.append(name)
            value = self.returns.get(name)
            return value(*args, **kwargs) if callable(value) else value

        return _call


@pytest.fixture(autouse=True)
def _fake_session(monkeypatch):
    @asynccontextmanager
    async def _get_session():
        yield MagicMock()

    monkeypatch.setattr(inbox_module, "get_session", _get_session)


def _service(**returns) -> AgentInboxService:
    return AgentInboxService(dao=FakeDao(**returns))


def _run(**overrides):
    base = dict(
        run_id="r1",
        conversation_id="conv-1",
        user_id="user-1",
        record_status="active",
        owner_token="tok-1",
        lease_expire_time=None,
        record_content=json.dumps({"owner_generation": 1}),
        create_by_name="用户一",
        update_by=None,
        update_by_name=None,
        update_time=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------
# 纯函数 / 静态方法
# --------------------------------------------------------------------------


def test_is_valid_redis_stream_id():
    assert is_valid_redis_stream_id("12-3") is True
    assert is_valid_redis_stream_id(" 12-3 ") is True
    assert is_valid_redis_stream_id("0-0") is False
    assert is_valid_redis_stream_id("bad") is False
    assert is_valid_redis_stream_id(None) is False


def test_normalize_history_checkpoint_rejects_missing_or_foreign():
    assert normalize_history_checkpoint({}, "r1") is None
    assert normalize_history_checkpoint({"history_checkpoint": "not-a-dict"}, "r1") is None
    assert normalize_history_checkpoint({"history_checkpoint": {"run_id": "other"}}, "r1") is None


def test_normalize_history_checkpoint_rejects_invalid_stream_id():
    content = {"history_checkpoint": {"run_id": "r1", "stream_id": "0-0"}}
    assert normalize_history_checkpoint(content, "r1") is None
    content["history_checkpoint"]["stream_id"] = "not-an-id"
    assert normalize_history_checkpoint(content, "r1") is None


def test_normalize_history_checkpoint_coerces_bad_sequence_number():
    """history_sequence_no 无法转 int 或为负数时按 0 处理。"""
    content = {
        "history_checkpoint": {
            "run_id": "r1",
            "stream_id": "1-1",
            "turn_id": "t1",
            "history_sequence_no": "not-a-number",
        }
    }
    assert normalize_history_checkpoint(content, "r1") == {
        "run_id": "r1",
        "turn_id": "t1",
        "history_sequence_no": 0,
        "stream_id": "1-1",
    }
    content["history_checkpoint"]["history_sequence_no"] = -5
    assert normalize_history_checkpoint(content, "r1")["history_sequence_no"] == 0


def test_load_run_content_handles_broken_payloads():
    load = AgentInboxService._load_run_content
    assert load("not json") == {}
    assert load(None) == {}
    assert load("[1, 2]") == {}  # 非 dict
    assert load('{"a": 1}') == {"a": 1}


def test_next_run_id_normalizes_blank():
    assert AgentInboxService._next_run_id({}) is None
    assert AgentInboxService._next_run_id({"next_run_id": ""}) is None
    assert AgentInboxService._next_run_id({"next_run_id": " next "}) == "next"


def test_assert_owner_fences_superseded_workers():
    run = SimpleNamespace(owner_token="tok-1")
    AgentInboxService._assert_owner(run, "tok-1")
    for bad_token in (None, "", "other"):
        with pytest.raises(AgentRunLeaseLostError):
            AgentInboxService._assert_owner(run, bad_token)
    with pytest.raises(AgentRunLeaseLostError):
        AgentInboxService._assert_owner(SimpleNamespace(owner_token=None), "tok-1")


def test_question_response_requires_dict_payload():
    service = _service()
    run = SimpleNamespace(run_id="r1", record_content=json.dumps({"pending_question": "not-a-dict"}))
    assert service._question_response(run) is None

    valid = SimpleNamespace(run_id="r1", record_content=json.dumps({"pending_question": {"question_id": "q1"}}))
    assert service._question_response(valid) == {"run_id": "r1", "question_id": "q1"}


def test_validate_question_answer_rules():
    validate = AgentInboxService._validate_question_answer
    question = {
        "questions": [{"id": "scope", "options": [{"label": "全部"}, {"label": "本月"}], "multi_select": False}]
    }

    validate(question, {"cancelled": True})  # 取消：直接放行
    validate(question, {"answers": [{"id": "scope", "skipped": True}]})  # 跳过
    validate(question, {"answers": [{"id": "scope", "selected": [], "custom": "其他"}]})

    with pytest.raises(ValueError, match="answers 必须是数组"):
        validate(question, {"answers": "scope"})
    with pytest.raises(ValueError, match="不能重复且必须是对象"):
        validate(question, {"answers": ["not-a-dict"]})
    with pytest.raises(ValueError, match="不能重复且必须是对象"):
        validate(question, {"answers": [{"id": "scope"}, {"id": "scope"}]})
    with pytest.raises(ValueError, match="与待回答问题不一致"):
        validate(question, {"answers": [{"id": "unknown", "selected": ["全部"]}]})
    with pytest.raises(ValueError, match="selected 必须是字符串数组"):
        validate(question, {"answers": [{"id": "scope", "selected": [1]}]})
    with pytest.raises(ValueError, match="无效选项"):
        validate(question, {"answers": [{"id": "scope", "selected": ["去年"]}]})
    with pytest.raises(ValueError, match="单选问题只能选择一个选项"):
        validate(question, {"answers": [{"id": "scope", "selected": ["全部", "本月"]}]})
    with pytest.raises(ValueError, match="必须选择选项或填写自定义回答"):
        validate(question, {"answers": [{"id": "scope", "selected": []}]})


def test_restore_unstarted_question_only_rolls_back_unstarted():
    content = {"resuming_question": {"question_id": "q"}, "pending_answer": {}, "resume_started": True}
    AgentInboxService._restore_unstarted_question(content)
    assert content["resuming_question"] == {"question_id": "q"}  # 已开始：不回滚

    content = {"resuming_question": {"question_id": "q"}, "pending_answer": {"answers": []}}
    AgentInboxService._restore_unstarted_question(content)
    assert content == {"pending_question": {"question_id": "q"}}


# --------------------------------------------------------------------------
# service 分支
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_run_selected_message_errors():
    with pytest.raises(SteerMessageNotFoundError):
        await _service(lock_thread=SimpleNamespace(), get_active_run=None).start_run(
            "conv-1", "user-1", "用户一", selected_message_id="m1"
        )
    with pytest.raises(SteerMessageNotPendingError):
        await _service(
            lock_thread=SimpleNamespace(),
            get_active_run=None,
            get_message_by_id=SimpleNamespace(record_status="consumed"),
        ).start_run("conv-1", "user-1", "用户一", selected_message_id="m1")


@pytest.mark.asyncio
async def test_start_run_selected_message_is_adopted():
    """选中队列消息时只迁移该条，其余保持排队。"""
    selected = SimpleNamespace(record_status="pending")
    service = _service(
        lock_thread=SimpleNamespace(),
        get_active_run=None,
        get_message_by_id=selected,
    )
    run_id, messages = await service.start_run("conv-1", "user-1", "用户一", selected_message_id="m1")
    assert run_id
    assert selected.run_id == run_id
    assert "adopt_pending_messages" not in service._dao.calls  # 单条迁移不走批量接管


@pytest.mark.asyncio
async def test_prepare_pending_run_returns_none_for_ineligible_runs():
    assert await _service().prepare_pending_run("r1") is None  # Run 不存在
    assert await _service(get_run_by_id=_run()).prepare_pending_run("r1") is None  # 会话不存在
    assert await _service(get_run_by_id=_run(), lock_thread=SimpleNamespace()).prepare_pending_run("r1") is None

    # 非 pending 不接管
    service = _service(
        get_run_by_id=_run(), lock_thread=SimpleNamespace(), get_run_by_id_for_update=_run(record_status="active")
    )
    assert await service.prepare_pending_run("r1") is None

    # 已有其它 active Run
    service = _service(
        get_run_by_id=_run(),
        lock_thread=SimpleNamespace(),
        get_run_by_id_for_update=_run(record_status="pending"),
        get_active_run=_run(run_id="other"),
    )
    assert await service.prepare_pending_run("r1") is None


@pytest.mark.asyncio
async def test_reap_expired_runs_skips_missing_and_collects():
    assert await _service(list_expired_runs=[_run()]).reap_expired_runs() == []

    expired = _run(lease_expire_time=NOW.replace(year=2020))
    service = _service(list_expired_runs=[expired], get_run_by_id_for_update=expired)
    results = await service.reap_expired_runs()
    assert len(results) == 1
    assert results[0].status == "failed"


@pytest.mark.asyncio
async def test_reap_expired_runs_drops_candidates_nobody_can_close():
    """候选拿到行锁后已不具备收口条件（已终态）→ 本轮不产出结果。"""
    finished = _run(record_status="finished", lease_expire_time=NOW.replace(year=2020))
    service = _service(list_expired_runs=[finished], get_run_by_id_for_update=finished)
    assert await service.reap_expired_runs() == []


@pytest.mark.asyncio
async def test_finish_run_requeues_only_terminal_failures():
    service = _service(get_run_by_id_for_update=_run(record_status="active"))
    assert await service.finish_run("r1", "failed", "user-1", "用户一", "tok-1") == "failed"
    assert "requeue_messages" in service._dao.calls

    service = _service(get_run_by_id_for_update=_run(record_status="active"))
    assert await service.finish_run("r1", "finished", "user-1", "用户一", "tok-1") == "finished"
    assert "requeue_messages" not in service._dao.calls


@pytest.mark.asyncio
async def test_update_pending_message_keeps_omitted_fields():
    """content / queue_type 传 None 表示「不改」，不能把已有值覆盖掉。"""
    message = SimpleNamespace(message_id="m1", record_status="pending", record_content="原文", queue_type="next-turn")
    service = _service(lock_thread=SimpleNamespace(), get_message_by_id=message)
    assert await service.update_pending_message("conv-1", "user-1", "用户一", "m1") == {
        "message_id": "m1",
        "content": "原文",
        "queue_type": "next-turn",
    }


@pytest.mark.asyncio
async def test_reap_expired_runs_swallows_single_failure():
    """单个候选收口失败只记日志，不影响本轮其余候选。"""
    state = {"calls": 0}

    def _now(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] > 1:
            raise RuntimeError("db down")
        return NOW

    service = _service(list_expired_runs=[_run()], now=_now)
    assert await service.reap_expired_runs() == []


@pytest.mark.asyncio
async def test_reap_expired_run_state_skips_ineligible():
    service = _service()
    assert await service._reap_expired_run_state(None, _run(record_status="finished"), NOW, "u", "n") is None
    assert await service._reap_expired_run_state(None, _run(lease_expire_time=None), NOW, "u", "n") is None
    future = _run(lease_expire_time=NOW.replace(year=2030))
    assert await service._reap_expired_run_state(None, future, NOW, "u", "n") is None


@pytest.mark.asyncio
async def test_reap_expired_run_keeps_waiting_user_and_drops_others():
    service = _service()
    assert await service._reap_expired_run(None, None, NOW, "u", "n") is None

    finished = _run(record_status="finished")
    assert await service._reap_expired_run(None, finished, NOW, "u", "n") is finished

    not_expired = _run(lease_expire_time=NOW.replace(year=2030))
    assert await service._reap_expired_run(None, not_expired, NOW, "u", "n") is not_expired

    waiting = _run(
        lease_expire_time=NOW.replace(year=2020),
        record_content=json.dumps({"pending_question": {"question_id": "q"}}),
    )
    assert await service._reap_expired_run(None, waiting, NOW, "u", "n") is waiting
    assert waiting.record_status == "waiting_user"

    cancelling = _run(record_status="cancelling", lease_expire_time=NOW.replace(year=2020))
    assert await service._reap_expired_run(None, cancelling, NOW, "u", "n") is None
    assert cancelling.record_status == "cancelled"


@pytest.mark.asyncio
async def test_reconcile_recovered_messages():
    with pytest.raises(AgentRunNotActiveError):
        await _service().reconcile_recovered_messages("r1", set())

    checkpointed = SimpleNamespace(message_id="m1", record_status="claimed")
    await _service(get_run_by_id_for_update=_run(), list_claimed_messages=[checkpointed]).reconcile_recovered_messages(
        "r1", {"m1"}, owner_token="tok-1"
    )
    assert checkpointed.record_status == "consumed"

    orphan = SimpleNamespace(message_id="m2", record_status="claimed")
    await _service(get_run_by_id_for_update=_run(), list_claimed_messages=[orphan]).reconcile_recovered_messages(
        "r1", set(), owner_token="tok-1"
    )
    assert orphan.record_status == "pending"  # 旧 Worker 已领取但未落检查点 → 退回中间件


@pytest.mark.asyncio
async def test_heartbeat_run_competition_paths():
    assert await _service(renew_run_lease=False, get_run_by_id=None).heartbeat_run("r1", "tok-1") == "inactive"
    terminal = _service(renew_run_lease=False, get_run_by_id=_run(record_status="finished"))
    assert await terminal.heartbeat_run("r1", "tok-1") == "inactive"
    cancelling = _service(renew_run_lease=False, get_run_by_id=_run(record_status="cancelling"))
    assert await cancelling.heartbeat_run("r1", "tok-1") == "cancelling"
    still_owner = _service(renew_run_lease=False, get_run_by_id=_run(record_status="active"))
    assert await still_owner.heartbeat_run("r1", "tok-1") == "active"


@pytest.mark.asyncio
async def test_assert_run_owner_all_paths():
    await _service(get_run_by_id=_run()).assert_run_owner("r1", "tok-1")
    with pytest.raises(AgentRunLeaseLostError):
        await _service().assert_run_owner("r1", "tok-1")
    with pytest.raises(AgentRunLeaseLostError):
        await _service(get_run_by_id=_run(record_status="finished")).assert_run_owner("r1", "tok-1")
    with pytest.raises(AgentRunLeaseLostError):
        await _service(get_run_by_id=_run(owner_token="tok-2")).assert_run_owner("r1", "tok-1")


@pytest.mark.asyncio
async def test_mark_waiting_for_user_requires_active_run():
    with pytest.raises(AgentRunNotActiveError):
        await _service().mark_waiting_for_user("r1", {"question_id": "q"}, "user-1", "用户一", "tok-1")


@pytest.mark.asyncio
async def test_get_pending_question_requires_thread():
    with pytest.raises(AgentRunNotActiveError):
        await _service().get_pending_question("conv-1", "user-1")


@pytest.mark.asyncio
async def test_claim_question_answer_rejects_ineligible():
    with pytest.raises(AgentQuestionNotFoundError):
        await _service().claim_question_answer("conv-1", "user-1", "用户一", "q1", {"answers": []})
    with pytest.raises(AgentQuestionNotFoundError):
        await _service(lock_thread=SimpleNamespace()).claim_question_answer(
            "conv-1", "user-1", "用户一", "q1", {"answers": []}
        )
    mismatched = _run(
        record_status="waiting_user", record_content=json.dumps({"pending_question": {"question_id": "other"}})
    )
    with pytest.raises(AgentQuestionNotFoundError):
        await _service(lock_thread=SimpleNamespace(), get_active_run=mismatched).claim_question_answer(
            "conv-1", "user-1", "用户一", "q1", {"answers": []}
        )


@pytest.mark.asyncio
async def test_record_terminal_event():
    await _service().record_terminal_event("r1", "1-1")  # Run 不存在：静默返回

    run = _run()
    await _service(get_run_by_id_for_update=run).record_terminal_event("r1", "1-1")
    assert json.loads(run.record_content)["terminal_event"] == {"stream_id": "1-1", "event_type": "done"}


@pytest.mark.asyncio
async def test_mark_question_resume_started_guards():
    await _service().mark_question_resume_started("r1", "tok-1")
    await _service(get_run_by_id_for_update=_run(record_status="finished")).mark_question_resume_started("r1", "tok-1")

    without_question = _run(record_status="active")
    await _service(get_run_by_id_for_update=without_question).mark_question_resume_started("r1", "tok-1")
    assert "resume_started" not in json.loads(without_question.record_content)

    with_question = _run(record_status="active", record_content=json.dumps({"resuming_question": {"question_id": "q"}}))
    await _service(get_run_by_id_for_update=with_question).mark_question_resume_started("r1", "tok-1")
    assert json.loads(with_question.record_content)["resume_started"] is True


@pytest.mark.asyncio
async def test_restore_question_answer_guards_and_rollback():
    await _service().restore_question_answer("r1", "user-1", "用户一", "tok-1")

    without_question = _run(record_status="active")
    await _service(get_run_by_id_for_update=without_question).restore_question_answer("r1", "user-1", "用户一", "tok-1")
    assert "pending_question" not in json.loads(without_question.record_content)

    without_answer = _run(
        record_status="active", record_content=json.dumps({"resuming_question": {"question_id": "q"}})
    )
    await _service(get_run_by_id_for_update=without_answer).restore_question_answer("r1", "user-1", "用户一", "tok-1")
    assert json.loads(without_answer.record_content).get("pending_question") is None

    full = _run(
        record_status="active",
        record_content=json.dumps(
            {
                "resuming_question": {"question_id": "q"},
                "pending_answer": {"answers": []},
                "resume_started": True,
            }
        ),
    )
    await _service(get_run_by_id_for_update=full).restore_question_answer("r1", "user-1", "用户一", "tok-1")
    content = json.loads(full.record_content)
    assert content["pending_question"] == {"question_id": "q"}
    assert "pending_answer" not in content
    assert full.record_status == "waiting_user"


@pytest.mark.asyncio
async def test_enqueue_message_validation():
    with pytest.raises(ValueError, match="非法队列类型"):
        await _service().enqueue_message("conv-1", "user-1", "用户一", "hi", queue_type="later")
    with pytest.raises(ValueError, match="消息内容不能为空"):
        await _service().enqueue_message("conv-1", "user-1", "用户一", "   ")
    with pytest.raises(AgentRunNotActiveError):
        await _service(lock_thread=SimpleNamespace(), get_active_run=None).enqueue_message(
            "conv-1", "user-1", "用户一", "hi"
        )


@pytest.mark.asyncio
async def test_mark_consumed_guards():
    await _service().mark_consumed("r1", [], "tok-1")  # 空列表：直接返回
    await _service().mark_consumed("r1", ["m1"], "tok-1")  # Run 不存在：静默返回


@pytest.mark.asyncio
async def test_finish_run_returns_none_when_nothing_to_close():
    assert await _service().finish_run("r1", "finished", "user-1", "用户一", "tok-1") is None


@pytest.mark.asyncio
async def test_complete_run_lease_checks():
    with pytest.raises(AgentRunLeaseLostError):
        await _service().complete_run("r1", "user-1", "用户一", "tok-1")
    with pytest.raises(AgentRunLeaseLostError):
        await _service(get_run_by_id=_run()).complete_run("r1", "user-1", "用户一", "tok-1")
    with pytest.raises(AgentRunLeaseLostError):
        await _service(
            get_run_by_id=_run(), lock_thread=SimpleNamespace(), get_run_by_id_for_update=_run(record_status="finished")
        ).complete_run("r1", "user-1", "用户一", "tok-1")


@pytest.mark.asyncio
async def test_complete_run_schedules_continuation_without_runtime_context():
    """runtime_context 损坏时按空字典处理，仍能调度下一轮。"""
    run = _run(record_content=json.dumps({"runtime_context": "not-a-dict"}))
    claimed_row = SimpleNamespace(message_id="m9", record_content="排队消息", create_time=NOW)
    service = _service(
        get_run_by_id=_run(),
        lock_thread=SimpleNamespace(),
        get_run_by_id_for_update=run,
        has_pending_messages=True,
        claim_messages=[claimed_row],
    )
    result = await service.complete_run("r1", "user-1", "用户一", "tok-1")
    assert result.status == "finished"
    assert result.next_run_id
    assert json.loads(run.record_content)["next_run_id"] == result.next_run_id


@pytest.mark.asyncio
async def test_cancel_active_run_requires_thread():
    with pytest.raises(AgentRunNotActiveError):
        await _service().cancel_active_run("conv-1", "user-1", "用户一", "r1")


@pytest.mark.asyncio
async def test_update_pending_message_validation():
    with pytest.raises(ValueError, match="消息内容不能为空"):
        await _service().update_pending_message("conv-1", "user-1", "用户一", "m1", content="  ")
    with pytest.raises(ValueError, match="非法队列类型"):
        await _service().update_pending_message("conv-1", "user-1", "用户一", "m1", queue_type="later")


@pytest.mark.asyncio
async def test_cancel_pending_message_requires_thread():
    with pytest.raises(AgentRunNotActiveError):
        await _service().cancel_pending_message("conv-1", "user-1", "用户一", "m1")


@pytest.mark.asyncio
async def test_get_run_snapshot_scoped_to_conversation_and_user():
    assert await _service().get_run_snapshot("conv-1", "user-1", "r1") is None
    foreign_conversation = _service(get_run_by_id=_run(conversation_id="other"))
    assert await foreign_conversation.get_run_snapshot("conv-1", "user-1", "r1") is None
    foreign_user = _service(get_run_by_id=_run(user_id="other"))
    assert await foreign_user.get_run_snapshot("conv-1", "user-1", "r1") is None

    snapshot = await _service(get_run_by_id=_run()).get_run_snapshot("conv-1", "user-1", "r1")
    assert snapshot["run_id"] == "r1"
    assert snapshot["status"] == "active"
    assert snapshot["next_run_id"] is None
    assert snapshot["history_checkpoint"] is None
