"""Read-only current time tool for agents that need relative-time resolution."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool


_SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def get_current_time_payload(current_time: datetime | None = None) -> dict[str, str]:
    """Build the stable response payload used by the time tool."""
    value = current_time.astimezone(_SHANGHAI_TIMEZONE) if current_time else datetime.now(_SHANGHAI_TIMEZONE)
    offset = value.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    return {
        "current_time": value.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Shanghai",
        "utc_offset": offset,
        "weekday": _WEEKDAYS[value.weekday()],
    }


@tool("get_current_time")
async def get_current_time_tool() -> str:
    """获取当前北京时间、时区和星期。用户问题涉及今天、昨天、本周、当前等相对时间且需要确定日期时必须调用。"""
    return json.dumps(get_current_time_payload(), ensure_ascii=False)
