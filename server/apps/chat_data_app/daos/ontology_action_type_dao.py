"""本体 Action Type 定义 DAO（定义层，不含执行/审计）。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from utils.models import ChatDataOntologyActionType

from .dao_session import session_scope


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_json_list(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _format_time(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _row_to_dict(row: ChatDataOntologyActionType, *, include_audit: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": int(row.id),
        "action_type_name": _text(row.action_type_name),
        "display_name": _text(row.display_name),
        "description": _text(row.description_text),
        "object_type_id": int(row.object_type_id),
        "parameters": _parse_json_list(row.parameter_schema_json),
        "edits": _parse_json_list(row.edit_schema_json),
        "status": _text(row.lifecycle_status),
        "delete_flag": int(row.delete_flag or 0),
    }
    if include_audit:
        data.update(
            {
                "create_time": _format_time(row.create_time),
                "update_time": _format_time(row.update_time),
                "create_by": _text(row.create_by),
                "create_by_name": _text(row.create_by_name),
                "update_by": _text(row.update_by),
                "update_by_name": _text(row.update_by_name),
            }
        )
    return data


class OntologyActionTypeDao:
    """Action Type 定义表读写；同名软删行复用原行。"""

    async def list_action_types_async(self, object_type_id: int | None = None) -> list[dict[str, Any]]:
        async with session_scope() as session:
            stmt = select(ChatDataOntologyActionType).where(ChatDataOntologyActionType.delete_flag == 0)
            if object_type_id is not None:
                stmt = stmt.where(ChatDataOntologyActionType.object_type_id == int(object_type_id))
            stmt = stmt.order_by(ChatDataOntologyActionType.action_type_name.asc())
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_dict(row) for row in rows]

    async def get_action_type_async(self, name: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        async with session_scope() as session:
            stmt = select(ChatDataOntologyActionType).where(ChatDataOntologyActionType.action_type_name == name)
            if not include_deleted:
                stmt = stmt.where(ChatDataOntologyActionType.delete_flag == 0)
            row = (await session.execute(stmt)).scalars().first()
        return _row_to_dict(row) if row is not None else None

    async def upsert_action_type_async(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        allow_overwrite: bool,
        create_by: str = "",
        create_by_name: str = "",
        update_by: str = "",
        update_by_name: str = "",
    ) -> dict[str, Any] | None:
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyActionType).where(ChatDataOntologyActionType.action_type_name == name)
                    )
                )
                .scalars()
                .first()
            )
            if row is not None and int(row.delete_flag or 0) == 0 and not allow_overwrite:
                return None
            if row is None:
                row = ChatDataOntologyActionType(
                    action_type_name=name,
                    create_by=create_by,
                    create_by_name=create_by_name,
                )
                session.add(row)
            row.display_name = payload.get("display_name") or ""
            row.description_text = payload.get("description") or ""
            row.object_type_id = int(payload.get("object_type_id") or 0)
            row.parameter_schema_json = json.dumps(payload.get("parameters") or [], ensure_ascii=False)
            row.edit_schema_json = json.dumps(payload.get("edits") or [], ensure_ascii=False)
            row.lifecycle_status = payload.get("status") or "draft"
            row.delete_flag = 0
            row.update_by = update_by
            row.update_by_name = update_by_name
            await session.flush()
            await session.refresh(row)
            result = _row_to_dict(row)
        return result

    async def soft_delete_action_type_async(
        self,
        name: str,
        *,
        update_by: str = "",
        update_by_name: str = "",
    ) -> bool:
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyActionType).where(
                            ChatDataOntologyActionType.action_type_name == name,
                            ChatDataOntologyActionType.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return False
            row.delete_flag = 1
            row.update_by = update_by
            row.update_by_name = update_by_name
        return True
