"""本体 Action 执行/对象写覆盖层 DAO。"""

from __future__ import annotations

import json
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
    ) -> dict[str, Any]:
        async with session_scope() as session:
            row = ChatDataOntologyActionExecution(
                action_type_name=action_type_name,
                object_type_id=int(object_type_id),
                object_key=object_key,
                parameters_json=json.dumps(parameters, ensure_ascii=False),
                edits_json=json.dumps(edits, ensure_ascii=False),
                execution_status="succeeded",
                create_by=operator,
                create_by_name=operator_name,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return self._execution_to_dict(row)

    async def append_edit_async(
        self,
        *,
        object_type_id: int,
        object_key: str,
        property_name: str,
        value: Any,
        action_execution_id: int,
        action_type_name: str,
        operator: str = "",
        operator_name: str = "",
    ) -> None:
        async with session_scope() as session:
            session.add(
                ChatDataOntologyObjectEdit(
                    object_type_id=int(object_type_id),
                    object_key=object_key,
                    property_name=property_name,
                    property_value_json=json.dumps(value, ensure_ascii=False),
                    action_execution_id=int(action_execution_id),
                    action_type_name=action_type_name,
                    create_by=operator,
                    create_by_name=operator_name,
                )
            )
            await session.flush()

    async def latest_object_values_async(self, object_type_id: int, object_key: str) -> dict[str, Any]:
        """按属性取最新覆盖值；无编辑时返回空 dict。"""
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectEdit)
                        .where(
                            ChatDataOntologyObjectEdit.object_type_id == int(object_type_id),
                            ChatDataOntologyObjectEdit.object_key == object_key,
                        )
                        .order_by(ChatDataOntologyObjectEdit.id.desc())
                    )
                )
                .scalars()
                .all()
            )
        latest: dict[str, Any] = {}
        for row in rows:
            property_name = _text(row.property_name)
            if property_name and property_name not in latest:
                latest[property_name] = _load_json(row.property_value_json)
        return latest

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
            "object_type_id": int(row.object_type_id),
            "object_key": _text(row.object_key),
            "parameters": _load_json(row.parameters_json) or {},
            "edits": _load_json(row.edits_json) or [],
            "status": _text(row.execution_status),
            "error_message": _text(row.error_message),
            "create_time": _format_time(row.create_time),
            "create_by": _text(row.create_by),
            "create_by_name": _text(row.create_by_name),
        }
