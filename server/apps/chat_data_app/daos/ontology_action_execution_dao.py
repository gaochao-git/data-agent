"""本体 Action 执行/对象写覆盖层 DAO。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from utils.models import ChatDataOntologyActionExecution, ChatDataOntologyObjectEdit

from .dao_session import session_scope


def _text(value: Any) -> str:
    return str(value or "").strip()


def _format_time(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _load_json(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


@dataclass(frozen=True)
class ActionExecutionOptions:
    """执行审计中较少使用的扩展字段，避免 DAO 主入口参数继续膨胀。"""

    conflict_check_level: str = "object"
    isolation_level: str = "legacy_equivalent"
    revert_of_execution_id: int = 0
    status: str = "succeeded"
    error_message: str = ""


_DEFAULT_ACTION_EXECUTION_OPTIONS = ActionExecutionOptions()


class OntologyActionExecutionDao:
    """执行审计与属性覆盖值读写；写方法需在 transaction_scope 内与编辑一起提交。"""

    async def create_execution_async(
        self,
        *,
        action_type_name: str,
        object_type_id: int,
        object_key: str,
        parameters: dict[str, Any],
        edits: list[dict[str, Any]],
        operator: str = "",
        operator_name: str = "",
        action_type_version: int = 0,
        edited_object_keys: list[str] | None = None,
        compiled_edits: dict[str, Any] | None = None,
        idempotency_key: str = "",
        baseline: dict[str, Any] | None = None,
        options: ActionExecutionOptions = _DEFAULT_ACTION_EXECUTION_OPTIONS,
    ) -> dict[str, Any]:
        async with session_scope() as session:
            row = ChatDataOntologyActionExecution(
                action_type_name=action_type_name,
                action_type_version=int(action_type_version or 0),
                object_type_id=int(object_type_id),
                object_key=object_key,
                edited_object_keys=json.dumps(edited_object_keys or [object_key], ensure_ascii=False),
                parameters_json=json.dumps(parameters, ensure_ascii=False),
                edits_json=json.dumps(edits, ensure_ascii=False),
                compiled_edits_json=json.dumps(compiled_edits or {}, ensure_ascii=False),
                baseline_json=json.dumps(baseline, ensure_ascii=False) if baseline else None,
                conflict_check_level=_text(options.conflict_check_level) or "object",
                isolation_level=_text(options.isolation_level) or "legacy_equivalent",
                idempotency_key=_text(idempotency_key)[:64],
                execution_status=_text(options.status) or "succeeded",
                error_message=_text(options.error_message)[:1000],
                revert_of_execution_id=int(options.revert_of_execution_id or 0),
                create_by=operator,
                create_by_name=operator_name,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return self._execution_to_dict(row)

    async def get_execution_async(self, execution_id: int) -> dict[str, Any] | None:
        async with session_scope() as session:
            row = await session.get(ChatDataOntologyActionExecution, int(execution_id))
        return self._execution_to_dict(row) if row is not None else None

    async def mark_reverted_async(
        self,
        execution_id: int,
        *,
        operator: str = "",
    ) -> bool:
        """把原执行标记为已撤销（保留行，不物理删除）。"""
        async with session_scope() as session:
            row = await session.get(ChatDataOntologyActionExecution, int(execution_id))
            if row is None:
                return False
            row.execution_status = "reverted"
            row.reverted_by = operator
            row.reverted_at = datetime.now().replace(microsecond=0)
            await session.flush()
            return True

    async def find_execution_by_idempotency_async(
        self, action_type_name: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        """按幂等键查既有执行；键为空时不做查询（空键表示调用方未启用幂等）。"""
        key = _text(idempotency_key)
        if not key:
            return None
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyActionExecution)
                        .where(
                            ChatDataOntologyActionExecution.action_type_name == action_type_name,
                            ChatDataOntologyActionExecution.idempotency_key == key,
                        )
                        .order_by(ChatDataOntologyActionExecution.id.desc())
                    )
                )
                .scalars()
                .first()
            )
        return self._execution_to_dict(row) if row is not None else None

    async def append_edit_async(
        self,
        *,
        object_type_id: int,
        object_key: str,
        property_name: str,
        value: Any,
        action_execution_id: int,
        action_type_name: str,
        object_generation: int = 1,
        operator: str = "",
        operator_name: str = "",
    ) -> None:
        async with session_scope() as session:
            session.add(
                ChatDataOntologyObjectEdit(
                    object_type_id=int(object_type_id),
                    object_key=object_key,
                    object_generation=int(object_generation or 1),
                    property_name=property_name,
                    property_value_json=json.dumps(value, ensure_ascii=False),
                    action_execution_id=int(action_execution_id),
                    action_type_name=action_type_name,
                    create_by=operator,
                    create_by_name=operator_name,
                )
            )
            await session.flush()

    async def latest_object_values_async(
        self, object_type_id: int, object_key: str, object_generation: int = 1
    ) -> dict[str, Any]:
        """按属性取**当前实例代际**的最新覆盖值；无编辑时返回空 dict。

        只读当前代际是「删除后重建不继承旧编辑」的落点：旧代际记录保留可追溯，
        但不参与合并（object-edits/how-edits-applied.md:86）。
        """
        edits = await self.latest_object_edits_async(object_type_id, object_key, object_generation)
        return {name: entry["value"] for name, entry in edits.items()}

    async def latest_object_edits_async(
        self, object_type_id: int, object_key: str, object_generation: int = 1
    ) -> dict[str, dict[str, Any]]:
        """同 `latest_object_values_async`，但带上 `applied_at`。

        读时冲突策略 `apply_most_recent_value` 需要比较「覆盖生效时间 vs 源行 timestamp」，
        因此把时间一并带出，避免读路径再查一次。
        """
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectEdit)
                        .where(
                            ChatDataOntologyObjectEdit.object_type_id == int(object_type_id),
                            ChatDataOntologyObjectEdit.object_key == object_key,
                            ChatDataOntologyObjectEdit.object_generation == int(object_generation or 1),
                        )
                        .order_by(ChatDataOntologyObjectEdit.id.desc())
                    )
                )
                .scalars()
                .all()
            )
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            property_name = _text(row.property_name)
            if property_name and property_name not in latest:
                latest[property_name] = {
                    "value": _load_json(row.property_value_json),
                    "applied_at": row.applied_at or row.create_time,
                    "edit_id": int(row.id),
                    "execution_id": int(row.action_execution_id or 0),
                }
        return latest

    async def latest_edit_async(
        self, object_type_id: int, object_key: str, object_generation: int = 1
    ) -> dict[str, Any] | None:
        """对象当前代际的最新一条编辑（撤销时判定「是否仍是最近一次编辑」）。"""
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectEdit)
                        .where(
                            ChatDataOntologyObjectEdit.object_type_id == int(object_type_id),
                            ChatDataOntologyObjectEdit.object_key == object_key,
                            ChatDataOntologyObjectEdit.object_generation == int(object_generation or 1),
                        )
                        .order_by(ChatDataOntologyObjectEdit.id.desc())
                    )
                )
                .scalars()
                .first()
            )
        if row is None:
            return None
        return {
            "edit_id": int(row.id),
            "property_name": _text(row.property_name),
            "execution_id": int(row.action_execution_id or 0),
            "action_type_name": _text(row.action_type_name),
        }

    async def overridden_keys_async(
        self,
        object_type_id: int,
        object_keys: list[str],
        generations: dict[str, int] | None = None,
    ) -> set[str]:
        """一批对象里「当前代际存在覆盖值」的主键集合（列表角标用，一次查询）。"""
        keys = [key for key in dict.fromkeys(object_keys) if key]
        if not keys:
            return set()
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(
                        ChatDataOntologyObjectEdit.object_key,
                        ChatDataOntologyObjectEdit.object_generation,
                    )
                    .where(
                        ChatDataOntologyObjectEdit.object_type_id == int(object_type_id),
                        ChatDataOntologyObjectEdit.object_key.in_(keys),
                    )
                    .distinct()
                )
            ).all()
        expected = generations or {}
        return {
            str(object_key)
            for object_key, generation in rows
            if int(generation or 1) == int(expected.get(str(object_key), 1))
        }

    async def list_executions_async(
        self,
        *,
        action_type_name: str = "",
        object_key: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with session_scope() as session:
            stmt = select(ChatDataOntologyActionExecution)
            if action_type_name:
                stmt = stmt.where(ChatDataOntologyActionExecution.action_type_name == action_type_name)
            if object_key:
                stmt = stmt.where(ChatDataOntologyActionExecution.object_key == object_key)
            stmt = stmt.order_by(ChatDataOntologyActionExecution.id.desc()).limit(max(1, min(limit, 200)))
            rows = (await session.execute(stmt)).scalars().all()
        return [self._execution_to_dict(row) for row in rows]

    @staticmethod
    def _execution_to_dict(row: ChatDataOntologyActionExecution) -> dict[str, Any]:
        return {
            "id": int(row.id),
            "action_type_name": _text(row.action_type_name),
            "action_type_version": int(row.action_type_version or 0),
            "object_type_id": int(row.object_type_id),
            "object_key": _text(row.object_key),
            "edited_object_keys": _load_json(row.edited_object_keys) or [],
            "parameters": _load_json(row.parameters_json) or {},
            "edits": _load_json(row.edits_json) or [],
            "compiled_edits": _load_json(row.compiled_edits_json) or {},
            "baseline": _load_json(row.baseline_json),
            "conflict_check_level": _text(row.conflict_check_level) or "object",
            "isolation_level": _text(row.isolation_level) or "legacy_equivalent",
            "idempotency_key": _text(row.idempotency_key),
            "status": _text(row.execution_status),
            "error_message": _text(row.error_message),
            "revert_of_execution_id": int(row.revert_of_execution_id or 0),
            "reverted_by": _text(row.reverted_by),
            "reverted_at": _format_time(row.reverted_at),
            "create_time": _format_time(row.create_time),
            "create_by": _text(row.create_by),
            "create_by_name": _text(row.create_by_name),
        }
