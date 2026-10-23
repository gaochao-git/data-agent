"""Logical Dataset 服务：稳定身份、版本、事务、分支、member 绑定。"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from apps.chat_data_app.daos.dao_session import transaction_scope
from apps.chat_data_app.daos.logical_dataset_dao import (
    LogicalDatasetDao,
    binding_row_to_dict,
    branch_row_to_dict,
    dataset_row_to_dict,
    version_row_to_dict,
)
from apps.chat_data_app.services.datasource.manager import get_datasource_manager
from utils.logger import get_logger
from utils.models import ChatDataLogicalDataset

logger = get_logger(__name__)


class DatasetError(ValueError):
    """Dataset 业务错误基类。"""


class DatasetNotFoundError(DatasetError):
    """Dataset 不存在。"""


class DatasetAlreadyExistsError(DatasetError):
    """Dataset 已存在。"""


class DatasetInvalidError(DatasetError):
    """Dataset 入参/状态不合法。"""


def _text(value: Any) -> str:
    return str(value or "").strip()


class LogicalDatasetService:
    """Dataset 聚合根服务；版本/事务写入在同一事务内完成。"""

    def __init__(self, dao: LogicalDatasetDao | None = None) -> None:
        self._dao = dao or LogicalDatasetDao()

    async def list_datasets_async(self) -> list[dict[str, Any]]:
        async with transaction_scope() as session:
            rows = await self._dao.list_all(session)
            result = []
            for row in rows:
                item = dataset_row_to_dict(row)
                item["binding_count"] = len(await self._dao.list_bindings(session, int(row.id)))
                result.append(item)
            return result

    async def get_dataset_async_by_id_async(self, dataset_id: int) -> dict[str, Any] | None:
        async with transaction_scope() as session:
            row = await self._dao.get_by_id(session, int(dataset_id))
            if row is None or int(row.delete_flag or 0) != 0:
                return None
            return await self._detail_async(session, row)

    async def get_dataset_async(self, dataset_name: str) -> dict[str, Any]:
        name = _text(dataset_name)
        if not name:
            raise DatasetInvalidError("dataset_name 不能为空")
        async with transaction_scope() as session:
            row = await self._dao.get_by_name(session, name)
            if row is None:
                raise DatasetNotFoundError(f"Dataset 不存在: {name}")
            return await self._detail_async(session, row)

    @staticmethod
    def _normalize_payload(
        payload: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str], str, str, list[dict[str, Any]]]:
        columns = payload.get("columns") or []
        if not isinstance(columns, list) or not all(isinstance(item, dict) for item in columns):
            raise DatasetInvalidError("Dataset columns 必须是对象数组")
        column_names = {_text(item.get("name")) for item in columns}
        primary_key = [_text(item) for item in payload.get("primary_key") or [] if _text(item)]
        if primary_key and not set(primary_key).issubset(column_names):
            raise DatasetInvalidError("Dataset primary_key 必须引用 columns 中的列")
        bindings = payload.get("bindings") or []
        if not isinstance(bindings, list):
            raise DatasetInvalidError("Dataset bindings 必须是数组")
        member_ids = [_text(item.get("member_datasource_id")) for item in bindings if isinstance(item, dict)]
        if len(member_ids) != len(set(member_ids)):
            raise DatasetInvalidError("Dataset bindings 中 member_datasource_id 不能重复")
        return (
            columns,
            primary_key,
            json.dumps(columns, ensure_ascii=False),
            json.dumps(primary_key, ensure_ascii=False),
            [item for item in bindings if isinstance(item, dict)],
        )

    async def _create_dataset_row(
        self,
        session: Any,
        *,
        name: str,
        payload: dict[str, Any],
        operator: str,
        operator_name: str,
    ) -> ChatDataLogicalDataset:
        row = ChatDataLogicalDataset(
            dataset_name=name,
            display_name=_text(payload.get("display_name")) or name,
            description_text=_text(payload.get("description")),
            primary_key_json="[]",
            schema_json="[]",
            lifecycle_status=_text(payload.get("status")) or "draft",
            delete_flag=0,
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        await self._dao.save_dataset(session, row)
        return row

    def _apply_metadata(
        self,
        row: ChatDataLogicalDataset,
        payload: dict[str, Any],
        *,
        schema_json: str,
        primary_key_json: str,
        version_id: int,
    ) -> None:
        row.display_name = _text(payload.get("display_name")) or row.display_name or row.dataset_name
        row.description_text = _text(payload.get("description"))
        row.primary_key_json = primary_key_json
        row.schema_json = schema_json
        row.lifecycle_status = _text(payload.get("status")) or row.lifecycle_status or "draft"
        row.current_version_id = version_id

    async def upsert_dataset_async(
        self,
        dataset_name: str,
        payload: dict[str, Any],
        *,
        operator: str,
        operator_name: str,
    ) -> dict[str, Any]:
        name = _text(dataset_name)
        if not name:
            raise DatasetInvalidError("dataset_name 不能为空")
        columns, primary_key, schema_json, primary_key_json, bindings = self._normalize_payload(payload)
        async with transaction_scope() as session:
            row = await self._dao.get_by_name(session, name)
            created = row is None
            if row is None:
                row = await self._create_dataset_row(
                    session, name=name, payload=payload, operator=operator, operator_name=operator_name
                )
            branch = await self._dao.ensure_main_branch(session, int(row.id), operator, operator_name)
            transaction_id = uuid4().hex
            transaction = await self._dao.create_transaction(
                session,
                transaction_id=transaction_id,
                dataset_id=int(row.id),
                branch_id=int(branch.id),
                operator=operator,
                operator_name=operator_name,
            )
            await self._dao.replace_bindings(
                session,
                dataset_id=int(row.id),
                bindings=bindings,
                operator=operator,
                operator_name=operator_name,
            )
            version_no = await self._dao.next_version_no(session, int(row.id), int(branch.id))
            version_id = uuid4().hex
            previous_version_id = _text(payload.get("previous_version_id"))
            version = await self._dao.create_version(
                session,
                version_id=version_id,
                dataset_id=int(row.id),
                branch_id=int(branch.id),
                version_no=version_no,
                transaction_id=transaction_id,
                schema_json=schema_json,
                operator=operator,
                operator_name=operator_name,
            )
            await self._dao.commit_transaction(session, transaction)
            self._apply_metadata(
                row,
                payload,
                schema_json=schema_json,
                primary_key_json=primary_key_json,
                version_id=int(version.id),
            )
            session.add(row)
            await session.flush()
            if previous_version_id:
                await self._dao.add_lineage(
                    session,
                    from_version_id=previous_version_id,
                    to_version_id=version_id,
                    operation="upsert",
                    run_id="",
                    metadata={"created": created},
                    operator=operator,
                    operator_name=operator_name,
                )
            return await self._detail_async(session, row)

    async def bootstrap_from_datasource_async(
        self,
        datasource_group_id: str,
        *,
        table_names: list[str] | None = None,
        operator: str = "",
        operator_name: str = "",
    ) -> list[dict[str, Any]]:
        """从数据源组逻辑表批量注册 Dataset：一套表结构 + 每个 member 一条 binding。"""
        group_id = _text(datasource_group_id)
        if not group_id:
            raise DatasetInvalidError("datasource_group_id 不能为空")
        manager = get_datasource_manager()
        datasources = await manager.get_all_datasources_async(include_tables=True)
        group = next((item for item in datasources if _text(item.get("group_id")) == group_id), None)
        if group is None:
            raise DatasetNotFoundError(f"数据源组不存在: {group_id}")
        member_ids = [_text(item) for item in group.get("member_datasource_ids") or [] if _text(item)]
        if not member_ids:
            raise DatasetInvalidError(f"数据源组没有成员数据源: {group_id}")
        wanted = {_text(item) for item in table_names or [] if _text(item)}
        tables = [item for item in group.get("ds_tables") or [] if isinstance(item, dict)]
        if wanted:
            tables = [item for item in tables if _text(item.get("table_name")) in wanted]
        if not tables:
            raise DatasetInvalidError("没有可注册的物理表")
        results = []
        for table in tables:
            table_name = _text(table.get("table_name"))
            if not table_name:
                continue
            columns = []
            primary_key = []
            for column in table.get("columns") or []:
                if not isinstance(column, dict):
                    continue
                name = _text(column.get("name"))
                if not name:
                    continue
                columns.append(
                    {
                        "name": name,
                        "display_name": _text(column.get("description")) or name,
                        "data_type": _text(column.get("type")) or "string",
                        "physical_column": name,
                    }
                )
                if bool(column.get("primary_key")):
                    primary_key.append(name)
            for name in table.get("primary_keys") or []:
                normalized = _text(name)
                if normalized and normalized not in primary_key:
                    primary_key.append(normalized)
            payload = {
                "display_name": _text(table.get("business_desc")) or table_name,
                "description": _text(table.get("description")) or table_name,
                "primary_key": primary_key,
                "columns": columns,
                "bindings": [
                    {
                        "datasource_group_id": group_id,
                        "member_datasource_id": member_id,
                        "physical_table": table_name,
                    }
                    for member_id in member_ids
                ],
                "status": "draft",
            }
            results.append(
                await self.upsert_dataset_async(
                    table_name,
                    payload,
                    operator=operator,
                    operator_name=operator_name,
                )
            )
        return results

    async def create_branch_async(
        self,
        dataset_name: str,
        *,
        branch_name: str,
        base_version_id: int | None,
        operator: str,
        operator_name: str,
    ) -> dict[str, Any]:
        name = _text(dataset_name)
        branch_name = _text(branch_name)
        if not name or not branch_name:
            raise DatasetInvalidError("dataset_name 和 branch_name 不能为空")
        async with transaction_scope() as session:
            row = await self._dao.get_by_name(session, name)
            if row is None:
                raise DatasetNotFoundError(f"Dataset 不存在: {name}")
            branch = await self._dao.create_branch(
                session,
                dataset_id=int(row.id),
                branch_name=branch_name,
                base_version_id=base_version_id,
                operator=operator,
                operator_name=operator_name,
            )
            return branch_row_to_dict(branch)

    async def delete_dataset_async(self, dataset_name: str) -> None:
        name = _text(dataset_name)
        async with transaction_scope() as session:
            row = await self._dao.get_by_name(session, name)
            if row is None:
                raise DatasetNotFoundError(f"Dataset 不存在: {name}")
            await self._dao.soft_delete(session, row)

    async def _detail_async(self, session: Any, row: ChatDataLogicalDataset) -> dict[str, Any]:
        await session.refresh(row)
        data = dataset_row_to_dict(row)
        data["bindings"] = [binding_row_to_dict(item) for item in await self._dao.list_bindings(session, int(row.id))]
        data["branches"] = [branch_row_to_dict(item) for item in await self._dao.list_branches(session, int(row.id))]
        data["versions"] = [version_row_to_dict(item) for item in await self._dao.list_versions(session, int(row.id))]
        return data


logical_dataset_service = LogicalDatasetService()

__all__ = [
    "DatasetAlreadyExistsError",
    "DatasetError",
    "DatasetInvalidError",
    "DatasetNotFoundError",
    "LogicalDatasetService",
    "logical_dataset_service",
]
