"""本体动作对象 DAO：write_mode=action_created 对象类型的对象行读写。

对象行完全由 Action 创建（对齐 Palantir 无 backing datasource 对象类型），
不经覆盖层、不映射物理表；属性集存 properties_json（服务端解析校验）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select

from utils.models import ChatDataOntologyActionObject

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


def _row_to_dict(row: ChatDataOntologyActionObject) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "object_type_id": int(row.object_type_id),
        "object_key": _text(row.object_key),
        "title_value": _text(row.title_value),
        "properties": _load_json(row.properties_json) or {},
        "action_execution_id": int(row.action_execution_id or 0),
        "action_type_name": _text(row.action_type_name),
        "create_time": _format_time(row.create_time),
        "create_by": _text(row.create_by),
        "create_by_name": _text(row.create_by_name),
    }


class OntologyActionObjectDao:
    """动作对象行读写；创建需在 transaction_scope 内与执行审计一起提交。"""

    async def create_object_async(
        self,
        *,
        object_type_id: int,
        object_key: str,
        properties: dict[str, Any],
        title_value: str = "",
        action_execution_id: int = 0,
        action_type_name: str = "",
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any] | None:
        """插入动作对象行；同一 object_key 活跃行已存在时返回 None（上层转唯一冲突）。"""
        async with session_scope() as session:
            existing = (
                (
                    await session.execute(
                        select(ChatDataOntologyActionObject).where(
                            ChatDataOntologyActionObject.object_type_id == int(object_type_id),
                            ChatDataOntologyActionObject.object_key == object_key,
                            ChatDataOntologyActionObject.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return None
            row = ChatDataOntologyActionObject(
                object_type_id=int(object_type_id),
                object_key=object_key,
                title_value=title_value or "",
                properties_json=json.dumps(properties, ensure_ascii=False),
                action_execution_id=int(action_execution_id),
                action_type_name=action_type_name or "",
                create_by=operator,
                create_by_name=operator_name,
                update_by=operator,
                update_by_name=operator_name,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return _row_to_dict(row)

    async def get_object_async(self, object_type_id: int, object_key: str) -> dict[str, Any] | None:
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyActionObject).where(
                            ChatDataOntologyActionObject.object_type_id == int(object_type_id),
                            ChatDataOntologyActionObject.object_key == object_key,
                            ChatDataOntologyActionObject.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .first()
            )
            return _row_to_dict(row) if row is not None else None

    async def list_objects_async(
        self,
        object_type_id: int,
        *,
        keyword: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """动作对象列表（关键词匹配 object_key/title_value）与总数。"""
        conditions = [
            ChatDataOntologyActionObject.object_type_id == int(object_type_id),
            ChatDataOntologyActionObject.delete_flag == 0,
        ]
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))
        text = f"%{_text(keyword)}%"
        if _text(keyword):
            conditions.append(
                ChatDataOntologyActionObject.object_key.like(text) | ChatDataOntologyActionObject.title_value.like(text)
            )
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyActionObject)
                        .where(*conditions)
                        .order_by(ChatDataOntologyActionObject.id.desc())
                        .limit(safe_limit)
                        .offset(safe_offset)
                    )
                )
                .scalars()
                .all()
            )
            total = (
                await session.execute(select(func.count()).select_from(ChatDataOntologyActionObject).where(*conditions))
            ).scalar() or 0
            return [_row_to_dict(row) for row in rows], int(total)
