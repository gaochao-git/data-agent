"""Persistent teammate roster and shared task board (single-process MVP)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from utils.logger import get_logger

from ...services.runtime_config import AGENT_RECURSION_LIMIT
from ..middleware.subagents import SUBAGENT_STATE_PASSTHROUGH_KEYS, SubagentSpec, _compile_subagent
from .models import MEMBER_STATUSES, TeamMember, TeamState, TeamTask
from .store import TeamStore

logger = get_logger(__name__)

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_MAX_MEMBERS = 8
_MAX_TASKS = 256
_STALE_PROVISIONING_SECONDS = 600
_LEAD_NAME = "lead"
_SERVICE_REGISTRY: dict[str, TeamService] = {}


def register_team_service(service: TeamService) -> None:
    _SERVICE_REGISTRY[service.conversation_id] = service


def get_team_service(conversation_id: str) -> TeamService | None:
    return _SERVICE_REGISTRY.get(conversation_id)


class TeamError(ValueError):
    """Typed Team operation failure surfaced to the model as a tool error."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _plain_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item) for item in content)
    return str(content or "")


def _last_ai_text(result: Any) -> str:
    messages = result.get("messages") if isinstance(result, dict) else None
    for message in reversed(messages or []):
        if isinstance(message, AIMessage):
            text = _plain_text(message.content).strip()
            if text:
                return text
    return ""


class TeamService:
    """Team operations backed by one durable file per root conversation."""

    def __init__(
        self,
        *,
        conversation_id: str,
        user_id: str,
        specs: dict[str, SubagentSpec],
        checkpointer: Any,
        base_state: dict[str, Any] | None = None,
        store: TeamStore | None = None,
        max_members: int = _MAX_MEMBERS,
        recursion_limit: int | None = None,
        execute: Callable[[TeamMember, str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.specs = dict(specs)
        self.checkpointer = checkpointer
        self.base_state = dict(base_state or {})
        self.store = store or TeamStore()
        self.max_members = max_members
        self.recursion_limit = max(int(recursion_limit or AGENT_RECURSION_LIMIT), 1)
        self._execute_override = execute
        self._member_locks: dict[str, asyncio.Lock] = {}

    # ---------- roster ----------

    async def spawn(
        self,
        *,
        name: str,
        description: str,
        prompt: str,
        subagent_type: str,
        runtime_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = str(name or "").strip().lower()
        if not _NAME_RE.fullmatch(name):
            raise TeamError("name 必须是 lower-kebab-case，且不超过 32 字符")
        if name == _LEAD_NAME:
            raise TeamError("lead 是保留名称")
        if subagent_type not in self.specs:
            raise TeamError(f"未知 subagent_type: {subagent_type}")
        async with self.store.lock_for(self.conversation_id):
            state = self.store.load(self.conversation_id)
            if any(member.name == name for member in state.members):
                raise TeamError(f"teammate 名称已存在: {name}")
            if len(state.members) >= self.max_members:
                raise TeamError(f"teammate 数量达到上限: {self.max_members}")
            now = _now()
            member = TeamMember(
                name=name,
                description=str(description or "").strip(),
                subagent_type=subagent_type,
                thread_id=f"team:{self.conversation_id}:{name}",
                status="provisioning",
                created_at=now,
                updated_at=now,
            )
            state.members.append(member)
            self.store.save(self.conversation_id, state)
        try:
            reply = await self._run_member(member, prompt, runtime_state)
        except Exception as exc:
            await self._set_member_status(name, "failed", error=str(exc))
            raise TeamError(f"teammate {name} 创建失败: {exc}") from exc
        await self._set_member_status(name, "active", last_message=reply)
        return {"name": name, "status": "active", "reply": reply, "thread_id": member.thread_id}

    async def send(
        self,
        *,
        target: str,
        message: str,
        runtime_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = str(target or "").strip().lower()
        if target == _LEAD_NAME:
            raise TeamError("不能给 lead 发送 teammate 消息")
        member = self._get_member(target)
        if member.status != "active":
            raise TeamError(f"teammate {target} 当前不可用: {member.status}")
        lock = self._member_locks.setdefault(target, asyncio.Lock())
        async with lock:
            # Re-read after waiting for a previous turn on the same teammate.
            member = self._get_member(target)
            if member.status != "active":
                raise TeamError(f"teammate {target} 当前不可用: {member.status}")
            try:
                reply = await self._run_member(member, message, runtime_state)
            except Exception as exc:
                await self._set_member_status(target, "failed", error=str(exc))
                raise TeamError(f"teammate {target} 执行失败: {exc}") from exc
            await self._set_member_status(target, "active", last_message=reply)
            return {"target": target, "status": "active", "reply": reply}

    def list_members(self) -> list[dict[str, Any]]:
        state = self.store.load(self.conversation_id)
        return [
            {
                "target": member.name,
                "name": member.name,
                "description": member.description,
                "subagent_type": member.subagent_type,
                "status": member.status,
                "error": member.error,
                "last_message": member.last_message,
            }
            for member in state.members
        ]

    # ---------- task board ----------

    async def create_task(
        self,
        *,
        subject: str,
        description: str = "",
        blocked_by: list[str] | None = None,
        write_scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        subject = str(subject or "").strip()
        if not subject:
            raise TeamError("任务 subject 不能为空")
        async with self.store.lock_for(self.conversation_id):
            state = self.store.load(self.conversation_id)
            active_count = sum(1 for item in state.tasks if item.status != "deleted")
            if active_count >= _MAX_TASKS:
                raise TeamError(f"任务数量达到上限: {_MAX_TASKS}")
            now = _now()
            task = TeamTask(
                id=f"task-{state.next_task_no}",
                subject=subject,
                description=str(description or "").strip(),
                blocked_by=self._validate_dependencies(state, blocked_by or [], current_id=""),
                write_scopes=self._normalize_scopes(write_scopes or []),
                created_at=now,
                updated_at=now,
            )
            state.next_task_no += 1
            state.tasks.append(task)
            self.store.save(self.conversation_id, state)
            return self._task_view(state, task)

    def list_tasks(self) -> list[dict[str, Any]]:
        state = self.store.load(self.conversation_id)
        return [self._task_view(state, task) for task in state.tasks if task.status != "deleted"]

    def get_task(self, task_id: str) -> dict[str, Any]:
        state = self.store.load(self.conversation_id)
        return self._task_view(state, self._find_task(state, task_id))

    async def update_task(
        self,
        *,
        task_id: str,
        action: str,
        expected_revision: int,
        owner: str | None = None,
        subject: str | None = None,
        description: str | None = None,
        blocked_by: list[str] | None = None,
        write_scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        async with self.store.lock_for(self.conversation_id):
            state = self.store.load(self.conversation_id)
            task = self._find_task(state, task_id)
            if task.revision != int(expected_revision):
                raise TeamError(f"任务 revision 过期: 当前 {task.revision}")
            now = _now()
            self._apply_task_update(
                state,
                task,
                action=str(action or "").strip(),
                owner=owner,
                subject=subject,
                description=description,
                blocked_by=blocked_by,
                write_scopes=write_scopes,
                now=now,
            )
            task.revision += 1
            task.updated_at = now
            self.store.save(self.conversation_id, state)
            return self._task_view(state, task)

    @staticmethod
    async def reconcile_stale_provisioning(
        conversation_id: str,
        *,
        store: TeamStore | None = None,
        max_age_seconds: int = _STALE_PROVISIONING_SECONDS,
    ) -> None:
        """把长时间停在 provisioning 的成员收敛为 failed，避免面板永久卡住。"""
        team_store = store or TeamStore()
        async with team_store.lock_for(conversation_id):
            state = team_store.load(conversation_id)
            now = datetime.now(UTC)
            changed = False
            for member in state.members:
                if member.status != "provisioning":
                    continue
                try:
                    updated = datetime.fromisoformat(member.updated_at)
                except (TypeError, ValueError):
                    updated = now
                if (now - updated).total_seconds() < max_age_seconds:
                    continue
                member.status = "failed"
                member.error = "provisioning 超时，子智能体未完成创建"
                member.updated_at = now.isoformat(timespec="seconds")
                changed = True
            if changed:
                team_store.save(conversation_id, state)

    # ---------- execution ----------

    async def _run_member(self, member: TeamMember, message: str, runtime_state: dict[str, Any] | None) -> str:
        if self._execute_override is not None:
            return str(await self._execute_override(member, message, runtime_state or {}))
        spec = self.specs.get(member.subagent_type)
        if spec is None:
            raise TeamError(f"未知 subagent_type: {member.subagent_type}")
        if self.checkpointer is None:
            raise TeamError("持久 teammate 需要 checkpointer")
        graph = _compile_subagent(spec, checkpointer=self.checkpointer)
        state = {
            key: value
            for key, value in {**self.base_state, **(runtime_state or {})}.items()
            if key in SUBAGENT_STATE_PASSTHROUGH_KEYS
        }
        state["messages"] = [HumanMessage(content=message)]
        config = {
            "configurable": {"thread_id": member.thread_id, "chatdata_subagent": spec.name},
            "recursion_limit": max(int(spec.recursion_limit or 0), self.recursion_limit),
            "callbacks": [],
        }
        result = await graph.ainvoke(state, config)
        return _last_ai_text(result)

    async def _set_member_status(
        self, name: str, status: str, *, error: str = "", last_message: str | None = None
    ) -> None:
        if status not in MEMBER_STATUSES:
            raise TeamError(f"非法成员状态: {status}")
        async with self.store.lock_for(self.conversation_id):
            state = self.store.load(self.conversation_id)
            member = self._get_member(name, state)
            member.status = status
            member.error = error
            if last_message is not None:
                member.last_message = last_message
            member.updated_at = _now()
            self.store.save(self.conversation_id, state)

    def _get_member(self, name: str, state: TeamState | None = None) -> TeamMember:
        source = state or self.store.load(self.conversation_id)
        for member in source.members:
            if member.name == name:
                return member
        raise TeamError(f"teammate 不存在: {name}")

    # ---------- task helpers ----------

    def _find_task(self, state: TeamState, task_id: str) -> TeamTask:
        for task in state.tasks:
            if task.id == task_id:
                return task
        raise TeamError(f"任务不存在: {task_id}")

    def _validate_dependencies(self, state: TeamState, blocked_by: list[str], *, current_id: str) -> list[str]:
        result: list[str] = []
        ids = {task.id for task in state.tasks if task.status != "deleted"}
        for task_id in blocked_by:
            task_id = str(task_id or "").strip()
            if not task_id or task_id == current_id:
                raise TeamError(f"非法依赖: {task_id}")
            if task_id not in ids:
                raise TeamError(f"依赖任务不存在: {task_id}")
            if task_id not in result:
                result.append(task_id)
        if current_id and self._has_dependency_cycle(state, current_id, result):
            raise TeamError("blocked_by 会形成循环依赖")
        return result

    @staticmethod
    def _has_dependency_cycle(state: TeamState, current_id: str, blocked_by: list[str]) -> bool:
        by_id = {task.id: task for task in state.tasks if task.status != "deleted"}
        pending = list(blocked_by)
        seen: set[str] = set()
        while pending:
            task_id = pending.pop()
            if task_id == current_id:
                return True
            if task_id in seen:
                continue
            seen.add(task_id)
            task = by_id.get(task_id)
            if task is not None:
                pending.extend(task.blocked_by)
        return False

    @staticmethod
    def _normalize_scopes(values: list[str]) -> list[str]:
        scopes: list[str] = []
        for value in values:
            scope = str(value or "").strip().strip("/")
            if scope and scope not in scopes:
                scopes.append(scope)
        return scopes

    def _task_view(self, state: TeamState, task: TeamTask) -> dict[str, Any]:
        completed = {item.id for item in state.tasks if item.status == "completed"}
        ready = task.status == "pending" and all(blocker in completed for blocker in task.blocked_by)
        return {
            "id": task.id,
            "revision": task.revision,
            "subject": task.subject,
            "description": task.description,
            "status": task.status,
            "owner": task.owner,
            "blocked_by": list(task.blocked_by),
            "write_scopes": list(task.write_scopes),
            "ready": ready,
        }

    def _apply_task_update(
        self,
        state: TeamState,
        task: TeamTask,
        *,
        action: str,
        owner: str | None,
        subject: str | None,
        description: str | None,
        blocked_by: list[str] | None,
        write_scopes: list[str] | None,
        now: str,
    ) -> None:
        handlers = {
            "claim": lambda: self._claim_task(state, task, owner),
            "complete": lambda: self._complete_task(task),
            "release": lambda: self._release_task(task),
            "reopen": lambda: self._reopen_task(task),
            "delete": lambda: self._delete_task(task),
            "edit": lambda: self._edit_task(task, subject, description, write_scopes),
            "assign": lambda: self._assign_task(task, owner),
            "set_dependencies": lambda: self._set_task_dependencies(state, task, blocked_by),
        }
        handler = handlers.get(action)
        if handler is None:
            raise TeamError(f"不支持的任务操作: {action}")
        handler()
        task.updated_at = now

    def _claim_task(self, state: TeamState, task: TeamTask, owner: str | None) -> None:
        if task.status != "pending":
            raise TeamError("只有 pending 任务可以 claim")
        if not self._task_view(state, task)["ready"]:
            raise TeamError("任务依赖未完成")
        task.status = "in_progress"
        task.owner = str(owner or self.user_id)

    @staticmethod
    def _complete_task(task: TeamTask) -> None:
        if task.status != "in_progress":
            raise TeamError("只有 in_progress 任务可以 complete")
        task.status = "completed"

    @staticmethod
    def _release_task(task: TeamTask) -> None:
        if task.status != "in_progress":
            raise TeamError("只有 in_progress 任务可以 release")
        task.status = "pending"
        task.owner = ""

    @staticmethod
    def _reopen_task(task: TeamTask) -> None:
        task.status = "pending"
        task.owner = ""

    @staticmethod
    def _delete_task(task: TeamTask) -> None:
        task.status = "deleted"
        task.owner = ""

    def _edit_task(
        self,
        task: TeamTask,
        subject: str | None,
        description: str | None,
        write_scopes: list[str] | None,
    ) -> None:
        if subject is not None:
            task.subject = str(subject).strip() or task.subject
        if description is not None:
            task.description = str(description)
        if write_scopes is not None:
            task.write_scopes = self._normalize_scopes(write_scopes)

    @staticmethod
    def _assign_task(task: TeamTask, owner: str | None) -> None:
        task.owner = str(owner or "").strip()

    def _set_task_dependencies(
        self,
        state: TeamState,
        task: TeamTask,
        blocked_by: list[str] | None,
    ) -> None:
        if blocked_by is None:
            raise TeamError("set_dependencies 需要 blocked_by")
        task.blocked_by = self._validate_dependencies(state, blocked_by, current_id=task.id)
