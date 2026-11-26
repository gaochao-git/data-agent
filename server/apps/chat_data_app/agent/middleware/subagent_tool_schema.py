"""``subagent`` 单工具的扁平参数契约。

一次调用只描述一个 ``action``，所有参数平铺在顶层：小模型不需要生成嵌套
JSON，避免 ``send`` 值写成字符串或 task_id 平铺到顶层这类结构错误。
执行层仍在 ``to_operation`` 里归一成 DispatchArgs/SendArgs/ManageArgs。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .subagent_async import DEFAULT_WAIT_TIMEOUT_SECONDS, MAX_WAIT_TASKS

__all__ = [
    "DispatchArgs",
    "ManageArgs",
    "SendArgs",
    "SubagentAction",
    "SubagentToolArgs",
]

SubagentAction = Literal["dispatch", "send", "list", "check", "wait", "cancel"]


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


_ACTION_ALLOWED_FIELDS: dict[str, set[str]] = {
    "dispatch": {"action", "intent", "description", "subagent_type", "background", "label"},
    "send": {"action", "intent", "task_id", "message", "background"},
    "list": {"action", "intent", "agent_id", "status"},
    "check": {"action", "intent", "task_id"},
    "wait": {"action", "intent", "task_ids", "wait_seconds"},
    "cancel": {"action", "intent", "task_id"},
}

_ACTION_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "dispatch": ("description", "subagent_type"),
    "send": ("task_id", "message"),
    "list": (),
    "check": ("task_id",),
    "wait": ("task_ids",),
    "cancel": ("task_id",),
}


class SubagentToolArgs(_SubagentToolArgs):
    """单工具扁平参数：``action`` 决定本次调用，其余字段按动作白名单校验。"""

    @model_validator(mode="before")
    @classmethod
    def _explain_legacy_nested_shape(cls, data: Any) -> Any:
        """模型沿用旧嵌套写法时，直接给出可照抄的扁平示例。"""
        if isinstance(data, dict):
            for legacy in ("dispatch", "send", "manage"):
                if legacy in data:
                    raise ValueError(
                        "subagent 参数已改为扁平 action 写法，不要再嵌套 dispatch/send/manage；"
                        '例如 {"action":"send","task_id":"...","message":"..."}。'
                    )
        return data

    intent: str = Field(
        default="",
        description="一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。",
    )
    action: SubagentAction = Field(
        description="dispatch=派发新任务；send=向已有子会话发消息；list/check/wait/cancel=管理任务。",
    )
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=8000,
        description="dispatch 必填：给子智能体的任务书，含目标、背景、交付格式和事实文件路径。",
    )
    subagent_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="dispatch 必填：子智能体类型，必须是工具描述中列出的类型之一。",
    )
    background: bool | None = Field(
        default=None,
        description=(
            "dispatch 默认 true（后台执行，立即返回 task_id）；"
            "send 仅目标任务已结束时有效：true=后台执行下一轮，false=当前步骤同步等待；目标运行中不要传。"
        ),
    )
    label: str | None = Field(
        default=None,
        max_length=120,
        description="dispatch 可选：任务列表里展示的短标题；不传时取任务书首行。",
    )
    task_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="send/check/cancel 必填：目标子会话最近一轮的任务 ID，不要传 agent_id。",
    )
    message: str | None = Field(
        default=None,
        min_length=1,
        max_length=8000,
        description="send 必填：发给子智能体的消息。",
    )
    task_ids: list[str] | None = Field(
        default=None,
        max_length=MAX_WAIT_TASKS,
        description="wait 必填：要等待的任务 ID 列表（最多 10 个）。",
    )
    agent_id: str | None = Field(
        default=None,
        max_length=64,
        description="list 可选：只列出该稳定子智能体 ID 名下的任务 ID。",
    )
    status: str | None = Field(
        default=None,
        max_length=64,
        description="list 可选：active、terminal，或 running/completed/failed/cancelled 等具体状态。",
    )
    wait_seconds: int | None = Field(
        default=None,
        ge=1,
        le=60,
        description="wait 可选：最长等待秒数；超时返回当前状态，不取消任务。",
    )

    def _reject_unexpected_fields(self) -> None:
        allowed = _ACTION_ALLOWED_FIELDS[self.action]
        unexpected = sorted(name for name in self.model_fields_set - allowed if getattr(self, name) is not None)
        if unexpected:
            raise ValueError(f'action="{self.action}" 不支持参数：{", ".join(unexpected)}。')

    def _require_field(self, field: str) -> None:
        value = getattr(self, field)
        if field == "task_ids":
            present = any(str(item).strip() for item in (value or []))
        else:
            present = bool(str(value or "").strip())
        if present:
            return
        if field == "task_ids":
            raise ValueError('action="wait" 需要提供 task_ids（至少一个任务 ID）。')
        raise ValueError(f'action="{self.action}" 需要提供 {field}。')

    @model_validator(mode="after")
    def _validate_action_fields(self) -> "SubagentToolArgs":
        self._reject_unexpected_fields()
        for field in _ACTION_REQUIRED_FIELDS[self.action]:
            self._require_field(field)
        return self

    def to_operation(self) -> tuple[str, DispatchArgs | SendArgs | ManageArgs]:
        """把扁平参数归一成执行层使用的三组内部模型。"""
        if self.action == "dispatch":
            dispatch_kwargs: dict[str, Any] = {
                "description": self.description or "",
                "subagent_type": self.subagent_type or "",
            }
            if self.background is not None:
                dispatch_kwargs["background"] = self.background
            if self.label is not None:
                dispatch_kwargs["label"] = self.label
            return "dispatch", DispatchArgs(**dispatch_kwargs)
        if self.action == "send":
            return "send", SendArgs(
                task_id=self.task_id or "",
                message=self.message or "",
                background=self.background,
            )
        manage_kwargs: dict[str, Any] = {"action": self.action}
        for name in ("task_id", "task_ids", "agent_id", "status", "wait_seconds"):
            value = getattr(self, name)
            if value is not None:
                manage_kwargs[name] = value
        return "manage", ManageArgs(**manage_kwargs)
