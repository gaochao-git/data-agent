"""Run 入口期上下文的唯一契约。

Run 的 ``runtime_context`` 会被多个阶段读取（装配、模型选择、通知投递），
入口各自拼 dict 时字段集合与默认值容易漂移。本模块只负责两件事：

1. 定义入口期字段的静态形状（``RunContext``）；
2. 提供两个构造器——从运行参数构造、从父 Run 继承——保证同一来源的
   字段集合与覆盖顺序只有一处实现。

运行期追加的字段（如 ``user_turns``、``workspace_root``）不在这里声明，
仍由对应阶段写入；本契约只约束入口必须提供的部分。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, TypedDict, cast

from .completion_options import CompletionRunOptions


class RunSource(str, Enum):
    """Run 的提交来源，用于审计与字段口径区分。"""

    USER = "user"
    SCHEDULE = "schedule"
    SUBAGENT = "subagent"


class _RunContextRequired(TypedDict):
    task_source: str
    query: str
    disable_user_interaction: bool


class RunContext(_RunContextRequired, total=False):
    """入口期 runtime_context 的显式形状。

    必填三键由 ``_RunContextRequired`` 约束；其余字段按来源可选。
    运行期字段不在此声明，避免把入口契约与运行期状态混在一起。
    """

    datasource_id: str | None
    member_datasource_ids: list[Any]
    semantic_config_id: str | None
    model_id: Any
    file_selections: list[str]
    quoted_snippets: list[dict[str, str]]
    annotations: list[dict[str, Any]]
    enable_thinking: Any
    thinking_effort: Any
    skill_names: list[str]
    operator_name: str
    options: dict[str, Any]
    file_ids: list[str]


def build_run_context(
    *,
    source: RunSource,
    run_options: CompletionRunOptions,
    query: str,
    operator_name: str,
    raw_options: dict[str, Any] | None = None,
    file_ids: list[str] | None = None,
    disable_user_interaction: bool | None = None,
    include_annotations: bool = True,
) -> RunContext:
    """从已解析的运行参数构造入口上下文。

    ``disable_user_interaction`` 不传时沿用 ``run_options`` 的值；定时等
    无人值守入口显式传 True。``include_annotations=False`` 保留定时入口
    历史上不携带 annotations 的口径。
    """

    context: RunContext = {
        "task_source": source.value,
        "query": query,
        "datasource_id": run_options.datasource_id,
        "member_datasource_ids": run_options.member_datasource_ids,
        "semantic_config_id": run_options.semantic_config_id,
        "model_id": run_options.model_id,
        "file_selections": run_options.file_selections,
        "quoted_snippets": run_options.quoted_snippets,
        "enable_thinking": run_options.enable_thinking,
        "thinking_effort": run_options.thinking_effort,
        "skill_names": run_options.skill_names,
        "operator_name": operator_name,
        "options": raw_options or {},
        "file_ids": file_ids or [],
        "disable_user_interaction": (
            run_options.disable_user_interaction if disable_user_interaction is None else disable_user_interaction
        ),
    }
    if include_annotations:
        context["annotations"] = run_options.annotations
    return context


def inherit_run_context(
    parent: Mapping[str, Any],
    *,
    source: RunSource,
    overrides: Mapping[str, Any],
) -> RunContext:
    """从父 Run 继承上下文，只覆盖来源自己的字段。

    通知 Run 需要保留父 Run 的模型/数据源/技能等选择，同时把 ``query``
    置空并写入通知元数据；这里固定覆盖顺序为「父上下文 → 本次覆盖 →
    task_source」，避免不同字段的覆盖先后漂移。
    """

    context: dict[str, Any] = dict(parent)
    context.update(overrides)
    context["task_source"] = source.value
    return cast(RunContext, context)


RUN_CONTEXT_REQUIRED_KEYS = ("task_source", "query", "disable_user_interaction")


def validate_run_context(context: Mapping[str, Any]) -> None:
    """校验入口期上下文的必填键；仅用于已声明 ``task_source`` 的新契约。"""

    missing = [key for key in RUN_CONTEXT_REQUIRED_KEYS if key not in context]
    if missing:
        raise ValueError(f"runtime_context 缺少必填字段: {', '.join(missing)}")
