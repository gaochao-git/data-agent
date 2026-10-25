"""本体对象集合 DAO：static 主键列表的保存、读取与软删。

对象集合只存 local_key 列表（member/object_type 单独成列），不复制对象属性；
读取时由服务层回源投影。JSON 文本列不用 MySQL JSON 类型（与本体其它表一致）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select

from utils.models import ChatDataOntologyObjectSet

from .dao_session import session_scope


def _text(value: Any) -> str:
    return str(value or "").strip()


def _format_time(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _load_definition(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_to_dict(row: ChatDataOntologyObjectSet, *, include_keys: bool = True) -> dict[str, Any]:
    definition = _load_definition(row.definition_json)
    keys = [str(item) for item in definition.get("object_keys") or [] if str(item).strip()]
    data: dict[str, Any] = {
        "id": int(row.id),
        "set_name": _text(row.set_name),
        "display_name": _text(row.display_name),
        "description": _text(row.description_text),
        "object_type_id": int(row.object_type_id),
        "object_type_name": _text(row.object_type_name),
        "member_datasource_id": _text(row.member_datasource_id),
        "definition_kind": _text(definition.get("kind")) or "static",
        "object_key_count": len(keys),
        "temporary": bool(row.temporary),
        "expires_at": _format_time(row.expires_at),
        "expired": bool(row.expires_at and row.expires_at <= datetime.now()),
        "delete_flag": int(row.delete_flag or 0),
        "create_time": _format_time(row.create_time),
        "update_time": _format_time(row.update_time),
        "create_by": _text(row.create_by),
        "create_by_name": _text(row.create_by_name),
        "update_by": _text(row.update_by),
        "update_by_name": _text(row.update_by_name),
    }
    if include_keys:
        data["object_keys"] = keys
    return data


class OntologyObjectSetDao:
    """对象集合表读写；同名软删行复用原行（与 Action Type 口径一致）。"""

    async def list_object_sets_async(
        self,
        object_type_id: int | None = None,
        *,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        async with session_scope() as session:
            stmt = select(ChatDataOntologyObjectSet).where(ChatDataOntologyObjectSet.delete_flag == 0)
            if object_type_id is not None:
                stmt = stmt.where(ChatDataOntologyObjectSet.object_type_id == int(object_type_id))
            if not include_expired:
                # 用应用侧时间比较而不是 DB 的 NOW()：expires_at 由服务层按应用时区写入，
                # 两端都用同一时钟，避免部署时区不一致导致集合提前/延后过期。
                stmt = stmt.where(
                    (ChatDataOntologyObjectSet.expires_at.is_(None))
                    | (ChatDataOntologyObjectSet.expires_at > datetime.now())
                )
            stmt = stmt.order_by(ChatDataOntologyObjectSet.update_time.desc(), ChatDataOntologyObjectSet.id.desc())
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_dict(row, include_keys=False) for row in rows]

    async def get_object_set_async(
        self,
        name: str,
        *,
        include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        async with session_scope() as session:
            stmt = select(ChatDataOntologyObjectSet).where(ChatDataOntologyObjectSet.set_name == name)
            if not include_deleted:
                stmt = stmt.where(ChatDataOntologyObjectSet.delete_flag == 0)
            row = (await session.execute(stmt)).scalars().first()
        return _row_to_dict(row) if row is not None else None

    async def upsert_object_set_async(
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
        """保存对象集合；活跃同名且未允许覆盖时返回 None（上层转已存在错误）。"""
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectSet).where(ChatDataOntologyObjectSet.set_name == name)
                    )
                )
                .scalars()
                .first()
            )
            if row is not None and int(row.delete_flag or 0) == 0 and not allow_overwrite:
                return None
            if row is None:
                row = ChatDataOntologyObjectSet(set_name=name, create_by=create_by, create_by_name=create_by_name)
                session.add(row)
            row.display_name = _text(payload.get("display_name")) or name
            row.description_text = _text(payload.get("description"))
            row.object_type_id = int(payload.get("object_type_id") or 0)
            row.object_type_name = _text(payload.get("object_type_name"))
            row.member_datasource_id = _text(payload.get("member_datasource_id"))
            row.definition_json = json.dumps(
                {"kind": "static", "object_keys": payload.get("object_keys") or []},
                ensure_ascii=False,
            )
            row.temporary = 1 if payload.get("temporary") else 0
            row.expires_at = payload.get("expires_at")
            row.delete_flag = 0
            row.update_by = update_by
            row.update_by_name = update_by_name
            await session.flush()
            await session.refresh(row)
            result = _row_to_dict(row)
        return result

    async def soft_delete_object_set_async(
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
                        select(ChatDataOntologyObjectSet).where(
                            ChatDataOntologyObjectSet.set_name == name,
                            ChatDataOntologyObjectSet.delete_flag == 0,
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


__all__ = ["OntologyObjectSetDao"]
