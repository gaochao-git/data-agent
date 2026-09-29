"""五字段 cron 表达式解析：分 时 日 月 周。

智能体定时任务的调度表达式解析入口。支持 ``*``、单值、区间 ``a-b``、
步进 ``*/n`` 与 ``a-b/n``、列表 ``a,b,c``；星期字段 0 和 7 都表示周日。
日期与星期同时受限时按 Vixie cron 语义取并集，其余取交集。

表达式按服务器本地时区的 naive datetime 计算，与库内 DateTime 字段
（naive）口径一致；多节点部署依赖各节点时钟经 NTP 对齐。
"""

from __future__ import annotations

from datetime import datetime, timedelta

_FIELD_COUNT = 5
# (字段名, 最小值, 最大值)；星期按 cron 惯例允许 0-7，7 归一化为 0（周日）
_FIELD_SPECS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of month", 1, 31),
    ("month", 1, 12),
    ("day of week", 0, 7),
)
# 日期扫描迭代上限：按日推进，覆盖约 27 年，足够判定的表达式永远无触发点
_MAX_DAY_STEPS = 10000
_TIMEDELTA_MINUTE = timedelta(minutes=1)


class CronExprError(ValueError):
    """cron 表达式非法。"""


def _resolve_field_range(body: str, part: str, name: str, minimum: int, maximum: int) -> tuple[int, int]:
    """解析单分段的值区间，返回 (start, end)；区间/取值非法抛 CronExprError。

    part 为含步进的完整分段，仅用于报错文案与原实现保持一致。
    """
    if body == "*":
        return minimum, maximum
    if "/" in body or not body:
        raise CronExprError(f"cron {name} 取值范围非法: {part!r}")
    head, _, tail = body.partition("-")
    try:
        start = int(head)
        end = int(tail) if tail else start
    except ValueError as exc:
        raise CronExprError(f"cron {name} 取值非法: {part!r}") from exc
    if end < start:
        raise CronExprError(f"cron {name} 区间起点不能大于终点: {part!r}")
    return start, end


def _parse_field_part(part: str, name: str, minimum: int, maximum: int) -> set[int]:
    """解析单个逗号分段（不含逗号），返回字段取值集合。"""
    body, _, step_text = part.partition("/")
    if step_text:
        try:
            step = int(step_text)
        except ValueError as exc:
            raise CronExprError(f"cron {name} 步进非法: {part!r}") from exc
        if step < 1:
            raise CronExprError(f"cron {name} 步进必须大于 0: {part!r}")
    else:
        step = 1

    start, end = _resolve_field_range(body, part, name, minimum, maximum)

    if start < minimum or end > maximum:
        raise CronExprError(f"cron {name} 取值超出 [{minimum}, {maximum}]: {part!r}")
    return set(range(start, end + 1, step))


def parse_cron_expr(expr: str) -> tuple[frozenset[int], frozenset[int], frozenset[int], frozenset[int], frozenset[int]]:
    """解析表达式，返回 (分, 时, 日, 月, 星期) 五个取值集合。

    星期集合已归一化为 Python weekday 口径（周一=0 ... 周日=6）。
    """
    normalized = str(expr or "").strip()
    if not normalized:
        raise CronExprError("cron 表达式不能为空")
    fields = normalized.split()
    if len(fields) != _FIELD_COUNT:
        raise CronExprError(f"cron 表达式必须是 5 个字段（分 时 日 月 周）: {expr!r}")

    parsed: list[set[int]] = []
    for text, (name, minimum, maximum) in zip(fields, _FIELD_SPECS, strict=True):
        values: set[int] = set()
        for part in text.split(","):
            if not part:
                raise CronExprError(f"cron {name} 存在空分段: {text!r}")
            values |= _parse_field_part(part, name, minimum, maximum)
        if not values:
            raise CronExprError(f"cron {name} 没有合法取值: {text!r}")
        parsed.append(values)

    minute_set, hour_set, _dom, month_set, dow = parsed
    # 星期 7（周日）归一化为 0，再换算成 Python weekday（cron 1=周一 减 1）
    python_dow = {(value - 1) % 7 for value in dow}
    return (
        frozenset(minute_set),
        frozenset(hour_set),
        frozenset(_dom),
        frozenset(month_set),
        frozenset(python_dow),
    )


def _day_matches(candidate: datetime, dom: frozenset[int], dow: frozenset[int]) -> bool:
    """日期命中判断：日/星期都受限取并集，否则取交集（Vixie cron 语义）。"""
    dom_restricted = dom != frozenset(range(1, 32))
    dow_restricted = dow != frozenset(range(7))
    dom_hit = candidate.day in dom
    dow_hit = candidate.weekday() in dow
    if dom_restricted and dow_restricted:
        return dom_hit or dow_hit
    return dom_hit and dow_hit


def _advance_day(candidate: datetime) -> datetime:
    return (candidate + timedelta(days=1)).replace(hour=0, minute=0)


def _resolve_fire_time_in_day(candidate: datetime, minute_set: frozenset[int], hour_set: frozenset[int]) -> datetime | None:
    """在已命中日期的 candidate 上解析当日触发时刻；当日无触发点返回 None。"""
    if candidate.hour not in hour_set:
        later_hours = [hour for hour in hour_set if hour > candidate.hour]
        if not later_hours:
            return None
        candidate = candidate.replace(hour=min(later_hours), minute=0)
    minutes = [minute for minute in minute_set if minute >= candidate.minute]
    if not minutes:
        later_hours = [hour for hour in hour_set if hour > candidate.hour]
        if not later_hours:
            return None
        return candidate.replace(hour=min(later_hours), minute=min(minute_set))
    return candidate.replace(minute=min(minutes))


def next_fire_time(expr: str, after: datetime) -> datetime | None:
    """返回严格晚于 after 的下一个触发时间；表达式永远无法命中时返回 None。"""
    minute_set, hour_set, dom, month_set, dow = parse_cron_expr(expr)
    candidate = (after + _TIMEDELTA_MINUTE).replace(second=0, microsecond=0)
    for _ in range(_MAX_DAY_STEPS):
        if candidate.month not in month_set:
            if candidate.month == 12:
                candidate = candidate.replace(year=candidate.year + 1, month=1, day=1)
            else:
                candidate = candidate.replace(month=candidate.month + 1, day=1)
            continue
        if not _day_matches(candidate, dom, dow):
            candidate = _advance_day(candidate)
            continue
        fire_time = _resolve_fire_time_in_day(candidate, minute_set, hour_set)
        if fire_time is not None:
            return fire_time
        candidate = _advance_day(candidate)
    return None
