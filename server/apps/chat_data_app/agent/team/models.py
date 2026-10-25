"""Persistent teammate and shared task models for the experimental Agent Team."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

MEMBER_STATUSES = frozenset({"provisioning", "active", "failed"})
TASK_STATUSES = frozenset({"pending", "in_progress", "completed", "deleted"})


@dataclass
class TeamMember:
    name: str
    description: str
    subagent_type: str
    thread_id: str
    status: str = "provisioning"
    error: str = ""
    last_message: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class TeamTask:
    id: str
    subject: str
    description: str = ""
    status: str = "pending"
    owner: str = ""
    blocked_by: list[str] = field(default_factory=list)
    write_scopes: list[str] = field(default_factory=list)
    revision: int = 1
    created_at: str = ""
    updated_at: str = ""


@dataclass
class TeamState:
    members: list[TeamMember] = field(default_factory=list)
    tasks: list[TeamTask] = field(default_factory=list)
    next_task_no: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> TeamState:
        if not isinstance(raw, dict):
            return cls()
        members = [
            TeamMember(**item) for item in raw.get("members") or [] if isinstance(item, dict) and item.get("name")
        ]
        tasks = [
            TeamTask(**item)
            for item in raw.get("tasks") or []
            if isinstance(item, dict) and item.get("id") and item.get("subject")
        ]
        next_task_no = raw.get("next_task_no")
        return cls(
            members=members,
            tasks=tasks,
            next_task_no=max(int(next_task_no or 1), 1),
        )
