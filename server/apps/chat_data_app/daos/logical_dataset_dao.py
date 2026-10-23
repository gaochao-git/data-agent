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
    ChatDataPipelineDefinition,
    ChatDataPipelineEdge,
    ChatDataPipelineNode,
    ChatDataPipelineRun,
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

    async def list_all(
        self,
        session: Any,
        *,
        dataset_layer: str | None = None,
        dataset_origin: str | None = None,
    ) -> list[ChatDataLogicalDataset]:
        stmt = select(ChatDataLogicalDataset).where(ChatDataLogicalDataset.delete_flag == 0)
        if dataset_layer:
            stmt = stmt.where(ChatDataLogicalDataset.dataset_layer == dataset_layer)
        if dataset_origin:
            stmt = stmt.where(ChatDataLogicalDataset.dataset_origin == dataset_origin)
        stmt = stmt.order_by(ChatDataLogicalDataset.update_time.desc(), ChatDataLogicalDataset.id.desc())
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
        run_id: str = "",
    ) -> ChatDataDatasetTransaction:
        row = ChatDataDatasetTransaction(
            transaction_id=transaction_id,
            dataset_id=dataset_id,
            branch_id=branch_id,
            transaction_status="open",
            run_id=run_id,
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
        row_count: int = 0,
    ) -> ChatDataDatasetVersion:
        row = ChatDataDatasetVersion(
            version_id=version_id,
            dataset_id=dataset_id,
            branch_id=branch_id,
            version_no=version_no,
            transaction_id=transaction_id,
            schema_json=schema_json,
            row_count=row_count,
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
        "dataset_layer": _text(row.dataset_layer) or "source",
        "dataset_origin": _text(row.dataset_origin) or "datasource",
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


class DatasetPipelineDao:
    """Dataset Pipeline 定义/节点/边/运行的持久化；节点与边按版本只追加。"""

    async def get_definition_by_name(self, session: Any, name: str) -> ChatDataPipelineDefinition | None:
        stmt = select(ChatDataPipelineDefinition).where(
            ChatDataPipelineDefinition.pipeline_name == name,
            ChatDataPipelineDefinition.delete_flag == 0,
        )
        return (await session.execute(stmt)).scalars().first()

    async def get_definition_by_pipeline_id(self, session: Any, pipeline_id: str) -> ChatDataPipelineDefinition | None:
        stmt = select(ChatDataPipelineDefinition).where(
            ChatDataPipelineDefinition.pipeline_id == pipeline_id,
            ChatDataPipelineDefinition.delete_flag == 0,
        )
        return (await session.execute(stmt)).scalars().first()

    async def list_definitions(self, session: Any) -> list[ChatDataPipelineDefinition]:
        stmt = (
            select(ChatDataPipelineDefinition)
            .where(ChatDataPipelineDefinition.delete_flag == 0)
            .order_by(ChatDataPipelineDefinition.update_time.desc(), ChatDataPipelineDefinition.id.desc())
        )
        return list((await session.execute(stmt)).scalars().all())

    async def save_definition(self, session: Any, row: ChatDataPipelineDefinition) -> ChatDataPipelineDefinition:
        session.add(row)
        await session.flush()
        return row

    async def list_nodes(self, session: Any, pipeline_id: str, version_no: int) -> list[ChatDataPipelineNode]:
        stmt = (
            select(ChatDataPipelineNode)
            .where(
                ChatDataPipelineNode.pipeline_id == pipeline_id,
                ChatDataPipelineNode.pipeline_version == version_no,
            )
            .order_by(ChatDataPipelineNode.ordinal, ChatDataPipelineNode.id)
        )
        return list((await session.execute(stmt)).scalars().all())

    async def list_edges(self, session: Any, pipeline_id: str, version_no: int) -> list[ChatDataPipelineEdge]:
        stmt = select(ChatDataPipelineEdge).where(
            ChatDataPipelineEdge.pipeline_id == pipeline_id,
            ChatDataPipelineEdge.pipeline_version == version_no,
        )
        return list((await session.execute(stmt)).scalars().all())

    async def append_version(
        self,
        session: Any,
        *,
        pipeline_id: str,
        version_no: int,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        operator: str,
        operator_name: str,
    ) -> None:
        for ordinal, node in enumerate(nodes):
            session.add(
                ChatDataPipelineNode(
                    pipeline_id=pipeline_id,
                    pipeline_version=version_no,
                    node_id=_text(node.get("node_id")),
                    node_type=_text(node.get("node_type")),
                    node_name=_text(node.get("node_name")),
                    config_json=_json_text(node.get("config"), {}),
                    ordinal=ordinal,
                    create_by=operator,
                    create_by_name=operator_name,
                )
            )
        for edge in edges:
            session.add(
                ChatDataPipelineEdge(
                    pipeline_id=pipeline_id,
                    pipeline_version=version_no,
                    edge_id=_text(edge.get("edge_id")),
                    from_node_id=_text(edge.get("from_node_id")),
                    to_node_id=_text(edge.get("to_node_id")),
                    create_by=operator,
                    create_by_name=operator_name,
                )
            )
        await session.flush()

    async def create_run(
        self,
        session: Any,
        *,
        run_id: str,
        pipeline_id: str,
        pipeline_version: int,
        trigger_type: str,
        operator: str,
        operator_name: str,
    ) -> ChatDataPipelineRun:
        row = ChatDataPipelineRun(
            run_id=run_id,
            pipeline_id=pipeline_id,
            pipeline_version=pipeline_version,
            run_status="running",
            trigger_type=trigger_type,
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        session.add(row)
        await session.flush()
        return row

    async def finish_run(
        self,
        session: Any,
        row: ChatDataPipelineRun,
        *,
        status: str,
        output_dataset_id: int | None = None,
        output_summary: dict[str, Any] | None = None,
        error_message: str = "",
    ) -> None:
        row.run_status = status
        row.output_dataset_id = output_dataset_id
        row.output_summary_json = _json_text(output_summary or {}, {})
        row.error_message = error_message[:1000]
        row.finished_at = datetime.now()
        session.add(row)
        await session.flush()

    async def get_run_by_id(self, session: Any, run_id: str) -> ChatDataPipelineRun | None:
        stmt = select(ChatDataPipelineRun).where(ChatDataPipelineRun.run_id == run_id)
        return (await session.execute(stmt)).scalars().first()

    async def list_runs(self, session: Any, pipeline_id: str, limit: int = 20) -> list[ChatDataPipelineRun]:
        stmt = (
            select(ChatDataPipelineRun)
            .where(ChatDataPipelineRun.pipeline_id == pipeline_id)
            .order_by(ChatDataPipelineRun.id.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())

    async def soft_delete_definition(self, session: Any, row: ChatDataPipelineDefinition) -> None:
        row.delete_flag = row.id
        session.add(row)
        await session.flush()


def pipeline_row_to_dict(row: ChatDataPipelineDefinition) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "pipeline_id": _text(row.pipeline_id),
        "pipeline_name": _text(row.pipeline_name),
        "display_name": _text(row.display_name),
        "description": _text(row.description_text),
        "target_layer": _text(row.target_layer) or "cleaned",
        "status": _text(row.lifecycle_status),
        "current_version_no": int(row.current_version_no or 0),
        "delete_flag": int(row.delete_flag or 0),
        "create_time": _format_time(row.create_time),
        "update_time": _format_time(row.update_time),
        "create_by": _text(row.create_by),
        "create_by_name": _text(row.create_by_name),
        "update_by": _text(row.update_by),
        "update_by_name": _text(row.update_by_name),
    }


def pipeline_node_row_to_dict(row: ChatDataPipelineNode) -> dict[str, Any]:
    return {
        "pipeline_version": int(row.pipeline_version),
        "node_id": _text(row.node_id),
        "node_type": _text(row.node_type),
        "node_name": _text(row.node_name),
        "config": _parse_json(row.config_json, dict),
        "ordinal": int(row.ordinal or 0),
    }


def pipeline_edge_row_to_dict(row: ChatDataPipelineEdge) -> dict[str, Any]:
    return {
        "pipeline_version": int(row.pipeline_version),
        "edge_id": _text(row.edge_id),
        "from_node_id": _text(row.from_node_id),
        "to_node_id": _text(row.to_node_id),
    }


def pipeline_run_row_to_dict(row: ChatDataPipelineRun) -> dict[str, Any]:
    return {
        "run_id": _text(row.run_id),
        "pipeline_id": _text(row.pipeline_id),
        "pipeline_version": int(row.pipeline_version or 0),
        "run_status": _text(row.run_status),
        "trigger_type": _text(row.trigger_type),
        "output_dataset_id": int(row.output_dataset_id) if row.output_dataset_id else None,
        "output_summary": _parse_json(row.output_summary_json, dict),
        "error_message": _text(row.error_message),
        "started_at": _format_time(row.started_at),
        "finished_at": _format_time(row.finished_at),
        "create_time": _format_time(row.create_time),
        "create_by": _text(row.create_by),
        "create_by_name": _text(row.create_by_name),
    }


__all__ = [
    "DatasetPipelineDao",
    "LogicalDatasetDao",
    "binding_row_to_dict",
    "branch_row_to_dict",
    "dataset_row_to_dict",
    "pipeline_edge_row_to_dict",
    "pipeline_node_row_to_dict",
    "pipeline_row_to_dict",
    "pipeline_run_row_to_dict",
    "version_row_to_dict",
]
