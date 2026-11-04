"""本体对象实例生命周期 DAO：可见性与代际。

对齐 Palantir 的两条语义（docs/palantir-ontology/object-edits/how-edits-applied.md:86）：
- 删除不是编辑：删除后对象不可见，与数据源状态无关；编辑值保留；
- 删除后重建是新实例，不继承旧编辑。

本平台用一张实例表承载「可见性 + 代际」，覆盖层按代际过滤。无实例行时视为
active / generation=1，兼容既有数据，无需回填。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from utils.models import ChatDataOntologyObjectInstance

from .dao_session import session_scope

ACTIVE = "active"
DELETED = "deleted"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _format_time(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _instance_to_dict(row: ChatDataOntologyObjectInstance) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "object_type_id": int(row.object_type_id),
        "object_key": _text(row.object_key),
        "generation": int(row.generation or 1),
        "lifecycle_status": _text(row.lifecycle_status) or ACTIVE,
        "last_execution_id": int(row.last_execution_id or 0),
        "create_time": _format_time(row.create_time),
        "update_time": _format_time(row.update_time),
        "create_by": _text(row.create_by),
        "create_by_name": _text(row.create_by_name),
        "update_by": _text(row.update_by),
        "update_by_name": _text(row.update_by_name),
    }


class OntologyObjectInstanceDao:
    """对象实例可见性与代际读写；写方法需在 transaction_scope 内调用。"""

    async def get_instance_async(self, object_type_id: int, object_key: str) -> dict[str, Any] | None:
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectInstance).where(
                            ChatDataOntologyObjectInstance.object_type_id == int(object_type_id),
                            ChatDataOntologyObjectInstance.object_key == object_key,
                        )
                    )
                )
                .scalars()
                .first()
            )
        return _instance_to_dict(row) if row is not None else None

    async def instances_async(self, object_type_id: int, object_keys: list[str]) -> dict[str, dict[str, Any]]:
        """一批对象的实例行（一次 IN 查询），供列表过滤可见性与合并代际。"""
        keys = [key for key in dict.fromkeys(object_keys) if key]
        if not keys:
            return {}
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectInstance).where(
                            ChatDataOntologyObjectInstance.object_type_id == int(object_type_id),
                            ChatDataOntologyObjectInstance.object_key.in_(keys),
                        )
                    )
                )
                .scalars()
                .all()
            )
        return {_text(row.object_key): _instance_to_dict(row) for row in rows}

    async def deleted_keys_async(self, object_type_id: int, object_keys: list[str]) -> set[str]:
        """一批对象里处于已删除状态的主键集合（供列表/搜索过滤，避免 N 次查询）。"""
        instances = await self.instances_async(object_type_id, object_keys)
        return {key for key, row in instances.items() if _text(row.get("lifecycle_status")) != ACTIVE}

    async def ensure_active_async(
        self,
        *,
        object_type_id: int,
        object_key: str,
        execution_id: int = 0,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """取实例；不存在则以 active/generation=1 建行（不改已存在的可见性）。"""
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectInstance).where(
                            ChatDataOntologyObjectInstance.object_type_id == int(object_type_id),
                            ChatDataOntologyObjectInstance.object_key == object_key,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                row = ChatDataOntologyObjectInstance(
                    object_type_id=int(object_type_id),
                    object_key=object_key,
                    generation=1,
                    lifecycle_status=ACTIVE,
                    last_execution_id=int(execution_id or 0),
                    create_by=operator,
                    create_by_name=operator_name,
                    update_by=operator,
                    update_by_name=operator_name,
                )
                session.add(row)
                await session.flush()
                await session.refresh(row)
            return _instance_to_dict(row)

    async def mark_deleted_async(
        self,
        *,
        object_type_id: int,
        object_key: str,
        execution_id: int = 0,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """标记删除（幂等）：不改变代际，也不动覆盖层记录。"""
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectInstance).where(
                            ChatDataOntologyObjectInstance.object_type_id == int(object_type_id),
                            ChatDataOntologyObjectInstance.object_key == object_key,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                row = ChatDataOntologyObjectInstance(
                    object_type_id=int(object_type_id),
                    object_key=object_key,
                    generation=1,
                    create_by=operator,
                    create_by_name=operator_name,
                )
                session.add(row)
            row.lifecycle_status = DELETED
            row.last_execution_id = int(execution_id or 0)
            row.update_by = operator
            row.update_by_name = operator_name
            await session.flush()
            await session.refresh(row)
            return _instance_to_dict(row)

    async def restore_async(
        self,
        *,
        object_type_id: int,
        object_key: str,
        execution_id: int = 0,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """撤销删除：回到 active，**不改代际**（撤销不是重建，编辑值继续生效）。"""
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectInstance).where(
                            ChatDataOntologyObjectInstance.object_type_id == int(object_type_id),
                            ChatDataOntologyObjectInstance.object_key == object_key,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return {"generation": 1, "lifecycle_status": ACTIVE, "last_execution_id": 0}
            row.lifecycle_status = ACTIVE
            row.last_execution_id = int(execution_id or 0)
            row.update_by = operator
            row.update_by_name = operator_name
            await session.flush()
            await session.refresh(row)
            return _instance_to_dict(row)

    async def revive_async(
        self,
        *,
        object_type_id: int,
        object_key: str,
        execution_id: int = 0,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """重建为**新实例**：代际 +1、回到 active；旧代际覆盖值不再参与合并。"""
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectInstance).where(
                            ChatDataOntologyObjectInstance.object_type_id == int(object_type_id),
                            ChatDataOntologyObjectInstance.object_key == object_key,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                row = ChatDataOntologyObjectInstance(
                    object_type_id=int(object_type_id),
                    object_key=object_key,
                    generation=1,
                    create_by=operator,
                    create_by_name=operator_name,
                )
                session.add(row)
            else:
                row.generation = int(row.generation or 1) + 1
            row.lifecycle_status = ACTIVE
            row.last_execution_id = int(execution_id or 0)
            row.update_by = operator
            row.update_by_name = operator_name
            await session.flush()
            await session.refresh(row)
            return _instance_to_dict(row)
