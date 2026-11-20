"""``subagent`` 单工具的分层参数契约。

三组主参数对应一次调用的生命周期阶段，必须且只能选一组；
所有层级都禁止未知字段，避免模型自行发明参数（例如 timeout_seconds）。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .subagent_async import DEFAULT_WAIT_TIMEOUT_SECONDS, MAX_WAIT_TASKS

__all__ = [
    "DispatchArgs",
    "ManageArgs",
    "SendArgs",
    "SubagentToolArgs",
]


class _SubagentToolArgs(BaseModel):
    """子智能体工具参数的共同边界：模型传入未知字段时直接拒绝。"""

    model_config = ConfigDict(extra="forbid")


class DispatchArgs(_SubagentToolArgs):
    """派发新子智能体任务。"""

    description: str = Field(
        min_length=1,
        max_length=8000,
        description=("给子智能体的任务书：目标、必要背景、交付格式和事实文件路径。子智能体看不到主对话。"),
    )
    subagent_type: str = Field(
        min_length=1,
        max_length=64,
        description="子智能体类型，必须是工具描述中列出的类型之一。",
    )
    background: bool = Field(
        default=True,
        description="true（默认）=后台执行，立即返回 task_id；false=同步等待。",
    )
    label: str | None = Field(
        default=None,
        max_length=120,
        description="子智能体任务列表里展示的短标题；不传时取任务书首行。",
    )


class SendArgs(_SubagentToolArgs):
    """向已有子智能体会话发送消息。"""

    task_id: str = Field(min_length=1, max_length=64, description="目标子会话最近一轮的任务 ID，不要传 agent_id。")
    message: str = Field(min_length=1, max_length=8000, description="发给子智能体的消息。")
    background: bool | None = Field(
        default=None,
        description=(
            "仅目标任务已结束时有效：true=后台执行下一轮（默认）；false=当前步骤同步等待。目标仍在运行时不要传。"
        ),
    )


class ManageArgs(_SubagentToolArgs):
    """查询、等待或取消子智能体任务。"""

    action: Literal["list", "check", "wait", "cancel"] = Field(
        description="list=列出任务；check=查单个任务；wait=等待任务终态；cancel=取消任务。",
    )
    task_id: str | None = Field(default=None, max_length=64, description="check/cancel 时要操作的任务 ID。")
    task_ids: list[str] | None = Field(
        default=None,
        max_length=MAX_WAIT_TASKS,
        description="wait 时要等待的任务 ID 列表（最多 10 个）。",
    )
    agent_id: str | None = Field(
        default=None,
        max_length=64,
        description="list 时可选：只列出该稳定子智能体 ID 名下的任务 ID。",
    )
    status: str | None = Field(
        default=None,
        max_length=64,
        description="list 时可选：active、terminal，或 running/completed/failed/cancelled 等具体状态。",
    )
    wait_seconds: int = Field(
        default=DEFAULT_WAIT_TIMEOUT_SECONDS,
        ge=1,
        le=60,
        description="wait 时最长等待秒数；超时返回当前状态，不取消任务。",
    )

    @model_validator(mode="after")
    def _validate_action_fields(self) -> "ManageArgs":
        allowed_fields = {
            "list": {"action", "agent_id", "status"},
            "check": {"action", "task_id"},
            "wait": {"action", "task_ids", "wait_seconds"},
            "cancel": {"action", "task_id"},
        }[self.action]
        unexpected = self.model_fields_set - allowed_fields
        if unexpected:
            raise ValueError(f'manage.action="{self.action}" 不支持参数：{", ".join(sorted(unexpected))}。')
        if self.action in {"check", "cancel"} and not str(self.task_id or "").strip():
            raise ValueError(f'manage.action="{self.action}" 需要提供 task_id。')
        if self.action == "wait" and not any(str(item).strip() for item in (self.task_ids or [])):
            raise ValueError('manage.action="wait" 需要提供 task_ids（至少一个任务 ID）。')
        return self


class SubagentToolArgs(_SubagentToolArgs):
    """单工具三组主参数：必须且只能选择其中一组。"""

    intent: str = Field(
        default="",
        description="一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。",
    )
    dispatch: DispatchArgs | None = Field(
        default=None,
        description="派发新的隔离子智能体任务。",
    )
    send: SendArgs | None = Field(
        default=None,
        description="向已有子会话发送消息；运行中插话，已结束继续追问。",
    )
    manage: ManageArgs | None = Field(
        default=None,
        description="查询、等待或取消子智能体任务。",
    )

    @model_validator(mode="after")
    def _exactly_one_operation(self) -> "SubagentToolArgs":
        selected = [name for name in ("dispatch", "send", "manage") if getattr(self, name) is not None]
        if len(selected) != 1:
            raise ValueError("subagent 必须且只能提供 dispatch、send、manage 中的一组参数。")
        return self
