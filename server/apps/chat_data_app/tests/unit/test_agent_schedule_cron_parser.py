"""五字段 cron 解析与触发时间计算的单测。"""

from datetime import datetime

import pytest

from apps.chat_data_app.services.agent_schedule.cron_parser import (
    CronExprError,
    next_fire_time,
    parse_cron_expr,
)
from apps.chat_data_app.services.agent_schedule.trigger_time import (
    SCHEDULE_TYPE_CRON,
    SCHEDULE_TYPE_ONCE,
    AgentScheduleValidationError,
    compute_next_trigger,
    normalize_prompt,
    normalize_title,
    validate_cron_expr,
)

# 2026-09-12 是周六
_BASE = datetime(2026, 9, 12, 8, 30)


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("0 9 * * *", datetime(2026, 9, 12, 9, 0)),
        ("*/15 * * * *", datetime(2026, 9, 12, 8, 45)),
        ("30 8 1 * *", datetime(2026, 10, 1, 8, 30)),
        ("0 0 * * 1", datetime(2026, 9, 14, 0, 0)),
        ("0 0 * * 0", datetime(2026, 9, 13, 0, 0)),
        ("0 0 * * 7", datetime(2026, 9, 13, 0, 0)),
        ("0 9 * * 1-5", datetime(2026, 9, 14, 9, 0)),
        # 日/星期同时受限按 Vixie cron 并集：9/13 是周日，命中星期字段
        ("15 8 13 * 5", datetime(2026, 9, 13, 8, 15)),
        ("5,35 7-9 * * *", datetime(2026, 9, 12, 8, 35)),
        # 日期字段的 */2 从 1 号步进：9/12 是偶数日不命中，下一命中为 9/13
        ("0 12 */2 * *", datetime(2026, 9, 13, 12, 0)),
        ("59 23 31 1 *", datetime(2027, 1, 31, 23, 59)),
    ],
)
def test_next_fire_time(expr, expected):
    assert next_fire_time(expr, _BASE) == expected


@pytest.mark.parametrize(
    "expr",
    ["", "  ", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "*/0 * * * *", "5-2 * * * *", "a * * * *", "* * * * 8", "1,,2 * * * *"],
)
def test_invalid_expr_rejected(expr):
    with pytest.raises(CronExprError):
        parse_cron_expr(expr)


def test_impossible_expr_returns_none():
    assert next_fire_time("0 0 30 2 *", _BASE) is None


def test_parse_normalizes_weekday_seven_to_python_weekday():
    *_prefix, dow = parse_cron_expr("0 0 * * 7")
    assert dow == frozenset({6})


def test_next_fire_strictly_after_boundary():
    # 恰好在触发点上的 after，必须返回下一个周期而不是当前时刻
    assert next_fire_time("30 8 * * *", datetime(2026, 9, 12, 8, 30)) == datetime(2026, 9, 13, 8, 30)


def test_normalize_title_and_prompt():
    assert normalize_title("  每日报表 ") == "每日报表"
    with pytest.raises(AgentScheduleValidationError):
        normalize_title("   ")
    assert normalize_prompt(" 查询销售额 ") == "查询销售额"
    with pytest.raises(AgentScheduleValidationError):
        normalize_prompt("")
    with pytest.raises(AgentScheduleValidationError):
        normalize_prompt("x" * 2001)


def test_validate_cron_expr_rejects_invalid():
    assert validate_cron_expr(" 0 9 * * * ") == "0 9 * * *"
    with pytest.raises(AgentScheduleValidationError):
        validate_cron_expr("bad")


def test_compute_next_trigger_once():
    plan = datetime(2026, 9, 12, 10, 0)
    assert compute_next_trigger(
        schedule_type=SCHEDULE_TYPE_ONCE,
        cron_expr="",
        plan_time=plan,
        effective_end_time=None,
        after=_BASE,
    ) == plan
    # 已过期或超出有效期：无下一次
    assert compute_next_trigger(
        schedule_type=SCHEDULE_TYPE_ONCE,
        cron_expr="",
        plan_time=plan,
        effective_end_time=None,
        after=datetime(2026, 9, 12, 10, 1),
    ) is None
    assert compute_next_trigger(
        schedule_type=SCHEDULE_TYPE_ONCE,
        cron_expr="",
        plan_time=plan,
        effective_end_time=datetime(2026, 9, 12, 9, 0),
        after=_BASE,
    ) is None


def test_compute_next_trigger_cron_respects_effective_end():
    assert compute_next_trigger(
        schedule_type=SCHEDULE_TYPE_CRON,
        cron_expr="0 9 * * *",
        plan_time=None,
        effective_end_time=datetime(2026, 9, 13, 0, 0),
        after=_BASE,
    ) == datetime(2026, 9, 12, 9, 0)
    assert compute_next_trigger(
        schedule_type=SCHEDULE_TYPE_CRON,
        cron_expr="0 9 * * *",
        plan_time=None,
        effective_end_time=datetime(2026, 9, 12, 8, 45),
        after=_BASE,
    ) is None
