"""定时任务触发时间计算：纯函数，无 DB 依赖。

任务定义（schedule_type/cron_expr/plan_time/effective_end_time）与
下一次触发时间（next_trigger_time）之间的换算入口，管理 service 与
调度 runtime 共用，保证创建入口和扫描推进的计算口径一致。
"""

from __future__ import annotations

from datetime import datetime

from .cron_parser import CronExprError, next_fire_time

SCHEDULE_TYPE_CRON = "cron"
SCHEDULE_TYPE_ONCE = "once"
SCHEDULE_TYPES = (SCHEDULE_TYPE_CRON, SCHEDULE_TYPE_ONCE)

MAX_TITLE_LENGTH = 100
MAX_PROMPT_LENGTH = 2000


class AgentScheduleValidationError(ValueError):
    """任务参数非法，错误信息可直接展示给用户。"""


def normalize_title(title: str) -> str:
    normalized = str(title or "").strip()
    if not normalized:
        raise AgentScheduleValidationError("任务标题不能为空")
    if len(normalized) > MAX_TITLE_LENGTH:
        raise AgentScheduleValidationError(f"任务标题不能超过 {MAX_TITLE_LENGTH} 个字符")
    return normalized


def normalize_prompt(prompt: str) -> str:
    normalized = str(prompt or "").strip()
    if not normalized:
        raise AgentScheduleValidationError("任务指令不能为空")
    if len(normalized) > MAX_PROMPT_LENGTH:
        raise AgentScheduleValidationError(f"任务指令不能超过 {MAX_PROMPT_LENGTH} 个字符")
    return normalized


def validate_cron_expr(cron_expr: str) -> str:
    normalized = str(cron_expr or "").strip()
    if not normalized:
        raise AgentScheduleValidationError("周期任务必须提供 cron 表达式")
    try:
        next_fire_time(normalized, datetime.now())
    except CronExprError as exc:
        raise AgentScheduleValidationError(f"cron 表达式非法：{exc}") from None
    return normalized


def compute_next_trigger(
    *,
    schedule_type: str,
    cron_expr: str,
    plan_time: datetime | None,
    effective_end_time: datetime | None,
    after: datetime,
) -> datetime | None:
    """计算 after 之后的下一次触发时间；有效期内无触发点时返回 None。"""
    if schedule_type == SCHEDULE_TYPE_ONCE:
        if plan_time is None or plan_time <= after:
            return None
        if effective_end_time is not None and plan_time > effective_end_time:
            return None
        return plan_time
    candidate = next_fire_time(cron_expr, after)
    if candidate is None:
        return None
    if effective_end_time is not None and candidate > effective_end_time:
        return None
    return candidate
