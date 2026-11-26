"""后台子智能体失联恢复的纯辅助逻辑。

任务服务的行数已接近红线；这里只放恢复判定、持久化上下文解析与
无 runner 时的宽限收口，真正的账本更新仍由 ``SubagentTaskService`` 执行。
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from ..contracts.subagent_task import TASK_STATUS_PENDING, TASK_STATUS_RUNNING

MAX_TASK_RECOVERY_ATTEMPTS = 2
TASK_RECOVERY_RUNNER_WAIT_SECONDS = 120


def recovery_count(meta: dict[str, Any]) -> int:
    try:
        return max(int(meta.get("recovery_count") or 0), 0)
    except (TypeError, ValueError):
        return 0


def execution_attempt(meta: dict[str, Any]) -> int:
    try:
        return max(int(meta.get("execution_attempt") or 1), 1)
    except (TypeError, ValueError):
        return 1


def recovery_runtime(record: Any) -> SimpleNamespace | None:
    payload = record.meta.get("recovery_runtime")
    payload = payload if isinstance(payload, dict) else {}
    state = payload.get("state")
    if not isinstance(state, dict):
        if record.meta.get("recovery_kind") == "pending_start":
            return None
        state = {}
    context = {"user_id": str(getattr(record, "user_id", "") or "")}
    operator_name = str((record.meta or {}).get("operator_name") or "").strip()
    if operator_name:
        context["operator_name"] = operator_name
    return SimpleNamespace(
        state=dict(state),
        tool_call_id=payload.get("tool_call_id") or None,
        context=context,
    )


def recovery_candidate(record: Any) -> bool:
    return (
        record.execution_mode == "background"
        and record.status in {TASK_STATUS_PENDING, TASK_STATUS_RUNNING}
        and recovery_count(record.meta) < MAX_TASK_RECOVERY_ATTEMPTS
    )


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


async def defer_or_expire_missing_runner(service: Any, session: Any, record: Any, now: datetime) -> str:
    """没有恢复执行器时先记等待起点；超时后按失联收口，避免永久悬挂。"""
    started_at = _parse_datetime(record.meta.get("recovery_wait_started_at"))
    if started_at is not None and (now - started_at).total_seconds() >= TASK_RECOVERY_RUNNER_WAIT_SECONDS:
        updated = await service._mark_expired_terminal(session, record, now)
        return "terminal" if updated else ""
    if started_at is not None:
        return ""
    meta = dict(record.meta)
    meta["recovery_wait_started_at"] = now.isoformat()
    await service._dao.update_expired(
        session,
        record.task_id,
        owner_token=record.owner_token or "",
        record_status=record.status,
        lease_expire_time=record.lease_expire_time or now,
        now=now,
        values={
            "record_meta": json.dumps(meta, ensure_ascii=False),
            "update_time": now,
            "update_by": "system",
            "update_by_name": "system",
        },
    )
    return ""
