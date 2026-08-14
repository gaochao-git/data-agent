"""Durable human-in-the-loop question tool backed by LangGraph interrupts."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field, model_validator


class QuestionOption(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class UserQuestion(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=1000)
    header: str = Field(default="", max_length=100)
    options: list[QuestionOption] = Field(default_factory=list, max_length=20)
    multi_select: bool = False

    @model_validator(mode="after")
    def validate_options(self):
        labels = [option.label.strip() for option in self.options]
        if len(labels) != len(set(labels)):
            raise ValueError("同一问题的选项名称不能重复")
        return self


class AskUserQuestionInput(BaseModel):
    questions: list[UserQuestion] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_question_ids(self):
        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("问题 ID 不能重复")
        return self


@tool("ask_user_question", args_schema=AskUserQuestionInput)
def ask_user_question_tool(questions: list[dict[str, Any]]) -> dict[str, Any]:
    """需要用户补充信息、确认选择或授权后才能继续时暂停执行并提问。

    每个问题使用稳定且唯一的 id。options 为空时允许用户自由输入；有选项时仍允许用户补充自定义答案。
    multi_select=false 表示单选，true 表示多选。不要用本工具询问可以自行从上下文或工具结果确定的信息。
    """
    validated = AskUserQuestionInput(questions=questions)
    payload = {
        "question_id": uuid4().hex,
        "questions": [question.model_dump() for question in validated.questions],
    }
    answer = interrupt(payload)
    return {"status": "answered", **answer}
