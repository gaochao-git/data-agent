"""Read-only current time tool for agents that need relative-time resolution."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool


_SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def get_current_time_payload(
    current_time: datetime | None = None,
    *,
    detail: bool,
) -> dict[str, str | int]:
    """Build the time payload; detailed fields are only returned on demand."""
    value = current_time.astimezone(_SHANGHAI_TIMEZONE) if current_time else datetime.now(_SHANGHAI_TIMEZONE)
    payload: dict[str, str | int] = {
        "current_time": value.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if not detail:
        return payload
    offset = value.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    payload.update({
        "timezone": "Asia/Shanghai",
        "utc_offset": offset,
        "weekday": _WEEKDAYS[value.weekday()],
        "timestamp": int(value.timestamp()),
    })
    return payload


@tool("get_current_time")
async def get_current_time_tool(detail: bool) -> str:
    """获取当前北京时间。detail 为必填参数：false 只返回时间；true 返回时区、偏移、星期和 Unix 时间戳。"""
    return json.dumps(get_current_time_payload(detail=detail), ensure_ascii=False)
