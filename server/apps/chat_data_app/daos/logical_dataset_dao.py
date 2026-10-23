"""Logical Dataset DAO：Dataset 身份、分支、版本、事务、绑定、血缘的持久化。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from utils.models import (
    ChatDataDatasetBinding,
    ChatDataDatasetBranch,
    ChatDataDatasetLineageEdge,
    ChatDataDatasetTransaction,
    ChatDataDatasetVersion,
    ChatDataLogicalDataset,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_text(value: Any, default: Any) -> str:
    try:
        return json.dumps(value if value is not None else default, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(default, ensure_ascii=False)


def _parse_json(value: Any, expect_type: type) -> Any:
    if isinstance(value, expect_type):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return expect_type()
    return parsed if isinstance(parsed, expect_type) else expect_type()


def _format_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


class LogicalDatasetDao:
    """Dataset 聚合根 DAO；事务内方法接收调用方 session，避免嵌套提交。"""

    async def get_by_name(self, session: Any, name: str) -> ChatDataLogicalDataset | None:
        stmt = select(ChatDataLogicalDataset).where(
            ChatDataLogicalDataset.dataset_name == name,
            ChatDataLogicalDataset.delete_flag == 0,
        )
        return (await session.execute(stmt)).scalars().first()

    async def get_by_id(self, session: Any, dataset_id: int) -> ChatDataLogicalDataset | None:
        return await session.get(ChatDataLogicalDataset, dataset_id)

    async def list_all(self, session: Any) -> list[ChatDataLogicalDataset]:
        stmt = (
            select(ChatDataLogicalDataset)
            .where(ChatDataLogicalDataset.delete_flag == 0)
            .order_by(ChatDataLogicalDataset.update_time.desc(), ChatDataLogicalDataset.id.desc())
        )
        return list((await session.execute(stmt)).scalars().all())

    async def save_dataset(self, session: Any, row: ChatDataLogicalDataset) -> ChatDataLogicalDataset:
        session.add(row)
        await session.flush()
        return row

    async def ensure_main_branch(
        self, session: Any, dataset_id: int, operator: str, operator_name: str
    ) -> ChatDataDatasetBranch:
        stmt = select(ChatDataDatasetBranch).where(
            ChatDataDatasetBranch.dataset_id == dataset_id,
            ChatDataDatasetBranch.branch_name == "main",
            ChatDataDatasetBranch.delete_flag == 0,
        )
        branch = (await session.execute(stmt)).scalars().first()
        if branch is not None:
            return branch
        branch = ChatDataDatasetBranch(
            dataset_id=dataset_id,
            branch_name="main",
            base_version_id=None,
            is_default=1,
            lifecycle_status="active",
            delete_flag=0,
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        session.add(branch)
        await session.flush()
        return branch

    async def create_branch(
        self,
        session: Any,
        *,
        dataset_id: int,
        branch_name: str,
        base_version_id: int | None,
        operator: str,
        operator_name: str,
    ) -> ChatDataDatasetBranch:
        row = ChatDataDatasetBranch(
            dataset_id=dataset_id,
            branch_name=branch_name,
            base_version_id=base_version_id,
            is_default=0,
            lifecycle_status="active",
            delete_flag=0,
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        session.add(row)
        await session.flush()
        return row

    async def create_transaction(
        self,
        session: Any,
        *,
        transaction_id: str,
        dataset_id: int,
        branch_id: int,
        operator: str,
        operator_name: str,
    ) -> ChatDataDatasetTransaction:
        row = ChatDataDatasetTransaction(
            transaction_id=transaction_id,
            dataset_id=dataset_id,
            branch_id=branch_id,
            transaction_status="open",
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        session.add(row)
        await session.flush()
        return row

    async def commit_transaction(self, session: Any, row: ChatDataDatasetTransaction) -> None:
        row.transaction_status = "committed"
        row.committed_at = datetime.now()
        session.add(row)
        await session.flush()

    async def next_version_no(self, session: Any, dataset_id: int, branch_id: int) -> int:
        stmt = select(func.max(ChatDataDatasetVersion.version_no)).where(
            ChatDataDatasetVersion.dataset_id == dataset_id,
            ChatDataDatasetVersion.branch_id == branch_id,
        )
        current = (await session.execute(stmt)).scalar()
        return int(current or 0) + 1

    async def create_version(
        self,
        session: Any,
        *,
        version_id: str,
        dataset_id: int,
        branch_id: int,
        version_no: int,
        transaction_id: str,
        schema_json: str,
        operator: str,
        operator_name: str,
    ) -> ChatDataDatasetVersion:
        row = ChatDataDatasetVersion(
            version_id=version_id,
            dataset_id=dataset_id,
            branch_id=branch_id,
            version_no=version_no,
            transaction_id=transaction_id,
            schema_json=schema_json,
            version_status="committed",
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        session.add(row)
        await session.flush()
        return row

    async def replace_bindings(
        self,
        session: Any,
        *,
        dataset_id: int,
        bindings: list[dict[str, str]],
        operator: str,
        operator_name: str,
    ) -> None:
        await session.execute(
            update(ChatDataDatasetBinding)
            .where(
                ChatDataDatasetBinding.dataset_id == dataset_id,
                ChatDataDatasetBinding.delete_flag == 0,
            )
            .values(delete_flag=ChatDataDatasetBinding.id)
        )
        for item in bindings:
            session.add(
                ChatDataDatasetBinding(
                    dataset_id=dataset_id,
                    datasource_group_id=_text(item.get("datasource_group_id")),
                    member_datasource_id=_text(item.get("member_datasource_id")),
                    physical_table=_text(item.get("physical_table")),
                    binding_status="active",
                    delete_flag=0,
                    create_by=operator,
                    create_by_name=operator_name,
                    update_by=operator,
                    update_by_name=operator_name,
                )
            )
        await session.flush()

    async def list_bindings(self, session: Any, dataset_id: int) -> list[ChatDataDatasetBinding]:
        stmt = select(ChatDataDatasetBinding).where(
            ChatDataDatasetBinding.dataset_id == dataset_id,
            ChatDataDatasetBinding.delete_flag == 0,
        )
        return list((await session.execute(stmt)).scalars().all())

    async def list_branches(self, session: Any, dataset_id: int) -> list[ChatDataDatasetBranch]:
        stmt = select(ChatDataDatasetBranch).where(
            ChatDataDatasetBranch.dataset_id == dataset_id,
            ChatDataDatasetBranch.delete_flag == 0,
        )
        return list((await session.execute(stmt)).scalars().all())

    async def list_versions(self, session: Any, dataset_id: int) -> list[ChatDataDatasetVersion]:
        stmt = (
            select(ChatDataDatasetVersion)
            .where(ChatDataDatasetVersion.dataset_id == dataset_id)
            .order_by(ChatDataDatasetVersion.version_no.desc())
        )
        return list((await session.execute(stmt)).scalars().all())

    async def add_lineage(
        self,
        session: Any,
        *,
        from_version_id: str,
        to_version_id: str,
        operation: str,
        run_id: str,
        metadata: dict[str, Any] | None,
        operator: str,
        operator_name: str,
    ) -> ChatDataDatasetLineageEdge:
        row = ChatDataDatasetLineageEdge(
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            operation=operation,
            run_id=run_id,
            edge_metadata_json=_json_text(metadata or {}, {}),
            create_by=operator,
            create_by_name=operator_name,
        )
        session.add(row)
        await session.flush()
        return row

    async def soft_delete(self, session: Any, row: ChatDataLogicalDataset) -> None:
        row.delete_flag = row.id
        session.add(row)
        await session.execute(
            update(ChatDataDatasetBinding)
            .where(
                ChatDataDatasetBinding.dataset_id == row.id,
                ChatDataDatasetBinding.delete_flag == 0,
            )
            .values(delete_flag=ChatDataDatasetBinding.id)
        )
        await session.flush()


def dataset_row_to_dict(row: ChatDataLogicalDataset) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "dataset_name": _text(row.dataset_name),
        "display_name": _text(row.display_name),
        "description": _text(row.description_text),
        "primary_key": _parse_json(row.primary_key_json, list),
        "columns": _parse_json(row.schema_json, list),
        "current_version_id": int(row.current_version_id) if row.current_version_id else None,
        "status": _text(row.lifecycle_status),
        "delete_flag": int(row.delete_flag or 0),
        "create_time": _format_time(row.create_time),
        "update_time": _format_time(row.update_time),
        "create_by": _text(row.create_by),
        "create_by_name": _text(row.create_by_name),
        "update_by": _text(row.update_by),
        "update_by_name": _text(row.update_by_name),
    }


def binding_row_to_dict(row: ChatDataDatasetBinding) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "datasource_group_id": _text(row.datasource_group_id),
        "member_datasource_id": _text(row.member_datasource_id),
        "physical_table": _text(row.physical_table),
        "status": _text(row.binding_status),
    }


def version_row_to_dict(row: ChatDataDatasetVersion) -> dict[str, Any]:
    return {
        "version_id": _text(row.version_id),
        "version_no": int(row.version_no),
        "transaction_id": _text(row.transaction_id),
        "columns": _parse_json(row.schema_json, list),
        "row_count": int(row.row_count or 0),
        "status": _text(row.version_status),
        "create_time": _format_time(row.create_time),
    }


def branch_row_to_dict(row: ChatDataDatasetBranch) -> dict[str, Any]:
    return {
        "branch_name": _text(row.branch_name),
        "base_version_id": int(row.base_version_id) if row.base_version_id else None,
        "is_default": bool(row.is_default),
        "status": _text(row.lifecycle_status),
    }


__all__ = [
    "LogicalDatasetDao",
    "binding_row_to_dict",
    "branch_row_to_dict",
    "dataset_row_to_dict",
    "version_row_to_dict",
]
