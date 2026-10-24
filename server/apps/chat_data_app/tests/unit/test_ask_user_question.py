"""ask_user_question 工具测试——入参校验与 interrupt 载荷。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.chat_data_app.agent.tools import ask_user_question as auq_module
from apps.chat_data_app.agent.tools.ask_user_question import (
    UserQuestion,
    ask_user_question_tool,
)


def _question(**overrides) -> dict:
    payload = {
        "id": "q1",
        "question": "请选择数据源",
        "options": [{"label": "A"}, {"label": "B"}],
    }
    payload.update(overrides)
    return payload


def test_question_accepts_unique_option_labels():
    question = UserQuestion(**_question())
    assert [option.label for option in question.options] == ["A", "B"]
    assert question.multi_select is False


def test_question_rejects_duplicate_option_labels():
    with pytest.raises(ValidationError, match="选项名称不能重复"):
        UserQuestion(**_question(options=[{"label": "A"}, {"label": " A "}]))


def test_input_rejects_duplicate_question_ids():
    with pytest.raises(ValueError, match="问题 ID 不能重复"):
        ask_user_question_tool.invoke({"questions": [_question(), _question()]})


def test_input_requires_at_least_one_question():
    with pytest.raises(ValidationError):
        ask_user_question_tool.invoke({"questions": []})


def test_input_rejects_more_than_five_questions():
    with pytest.raises(ValidationError):
        ask_user_question_tool.invoke({"questions": [_question(id=f"q{i}") for i in range(6)]})


def test_tool_interrupts_with_payload_and_merges_answer(monkeypatch):
    captured: dict = {}

    def _fake_interrupt(payload):
        captured.update(payload)
        return {"answers": ["A"]}

    monkeypatch.setattr(auq_module, "interrupt", _fake_interrupt)

    result = ask_user_question_tool.invoke({"questions": [_question()]})

    assert result["status"] == "answered"
    assert result["answers"] == ["A"]
    assert captured["questions"][0]["id"] == "q1"
    assert captured["question_id"]
    assert isinstance(captured["asked_at_ms"], int)
    # options 为空时允许用户自由输入：不强制要求选项
    assert captured["questions"][0]["multi_select"] is False


def test_tool_accepts_question_without_options(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(auq_module, "interrupt", lambda payload: captured.update(payload) or {})

    result = ask_user_question_tool.invoke({"questions": [_question(options=[], multi_select=True)]})

    assert result["status"] == "answered"
    assert captured["questions"][0]["options"] == []
    assert captured["questions"][0]["multi_select"] is True


def test_tool_returns_cancelled_status_when_user_cancels(monkeypatch):
    monkeypatch.setattr(auq_module, "interrupt", lambda _payload: {"cancelled": True, "answers": []})

    result = ask_user_question_tool.invoke({"questions": [_question()]})

    assert result == {"status": "cancelled", "cancelled": True, "answers": []}
