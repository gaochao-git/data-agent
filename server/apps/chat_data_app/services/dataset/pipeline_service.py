"""Dataset Pipeline 服务：定义版本（只追加）、手动 build、run 查询。

分层：
- 本服务负责 pipeline 定义校验、build 编排与 run 状态流转；
- Dataset 版本/事务/血缘一律复用 ``LogicalDatasetService.commit_pipeline_output_async``，
  pipeline 不另造一套 Dataset 版本；
- 数据源读取走 ``pipeline_runner`` 适配层（单测可替换）。

事务边界（明确分阶段提交）：
1. 先落 run(running)，保证失败也有运行记录；
2. 输出 Dataset 的 transaction + version + lineage 在 Dataset 服务的同一事务内原子提交；
3. 最后更新 run 终态。中途失败会把 run 置 failed，不会留下半提交的 Dataset 版本。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from apps.chat_data_app.daos.dao_session import transaction_scope
from apps.chat_data_app.daos.logical_dataset_dao import (
    DatasetPipelineDao,
    pipeline_edge_row_to_dict,
    pipeline_node_row_to_dict,
    pipeline_row_to_dict,
    pipeline_run_row_to_dict,
)
from utils.logger import get_logger
from utils.models import ChatDataPipelineDefinition

from . import pipeline_transforms
from .dataset_service import (
    DATASET_LAYERS,
    DatasetInvalidError,
    DatasetNotFoundError,
    LogicalDatasetService,
    logical_dataset_service,
)
from .pipeline_errors import PipelineBuildError, PipelineInvalidError, PipelineNotFoundError
from .pipeline_runner import DEFAULT_FETCH_LIMIT, load_dataset_rows_async

logger = get_logger(__name__)

NODE_TYPES = ("source", "transform", "output")
PIPELINE_STATUSES = ("draft", "experimental", "certified")
OUTPUT_LAYERS = ("cleaned", "enriched")
MAX_NODES = 50
DEFAULT_RUN_LIST_LIMIT = 20

ErrorType = type[Exception]


class DatasetPipelineService:
    """Pipeline 聚合服务；定义版本只追加，build 复用 Dataset 版本链路。"""

    def __init__(
        self,
        dao: DatasetPipelineDao | None = None,
        dataset_service: LogicalDatasetService | None = None,
    ) -> None:
        self._dao = dao or DatasetPipelineDao()
        self._dataset_service = dataset_service or logical_dataset_service

    # ------------------------------------------------------------------
    # 定义
    # ------------------------------------------------------------------
    async def list_pipelines_async(self) -> list[dict[str, Any]]:
        async with transaction_scope() as session:
            rows = await self._dao.list_definitions(session)
            result = []
            for row in rows:
                item = pipeline_row_to_dict(row)
                nodes = await self._dao.list_nodes(session, row.pipeline_id, int(row.current_version_no or 0))
                item["output_dataset_name"] = self._output_dataset_name(
                    [pipeline_node_row_to_dict(node) for node in nodes]
                )
                runs = await self._dao.list_runs(session, row.pipeline_id, limit=1)
                item["last_run"] = self._run_summary_for_list(runs[0]) if runs else None
                result.append(item)
            return result

    @staticmethod
    def _run_summary_for_list(row: Any) -> dict[str, Any]:
        """列表页不回传 preview 明细，避免列表响应随行数膨胀。"""
        item = pipeline_run_row_to_dict(row)
        if isinstance(item.get("output_summary"), dict):
            item["output_summary"].pop("preview", None)
        return item

    async def get_pipeline_async(self, pipeline_name: str) -> dict[str, Any]:
        name = self._require_name(pipeline_name)
        async with transaction_scope() as session:
            row = await self._dao.get_definition_by_name(session, name)
            if row is None:
                raise PipelineNotFoundError(f"Pipeline 不存在: {name}")
            return await self._detail_async(session, row)

    async def upsert_pipeline_async(
        self,
        pipeline_name: str,
        payload: dict[str, Any],
        *,
        operator: str,
        operator_name: str,
    ) -> dict[str, Any]:
        name = self._require_name(pipeline_name)
        normalized = self._normalize_definition_payload(payload)
        async with transaction_scope() as session:
            row = await self._dao.get_definition_by_name(session, name)
            if row is None:
                row = ChatDataPipelineDefinition(
                    pipeline_id=uuid4().hex,
                    pipeline_name=name,
                    display_name=normalized["display_name"] or name,
                    description_text=normalized["description"],
                    target_layer=normalized["target_layer"],
                    lifecycle_status=normalized["status"],
                    current_version_no=0,
                    delete_flag=0,
                    create_by=operator,
                    create_by_name=operator_name,
                    update_by=operator,
                    update_by_name=operator_name,
                )
                await self._dao.save_definition(session, row)
            await self._append_definition_version(session, row, normalized, operator, operator_name)
            return await self._detail_async(session, row)

    async def delete_pipeline_async(self, pipeline_name: str) -> None:
        name = self._require_name(pipeline_name)
        async with transaction_scope() as session:
            row = await self._dao.get_definition_by_name(session, name)
            if row is None:
                raise PipelineNotFoundError(f"Pipeline 不存在: {name}")
            await self._dao.soft_delete_definition(session, row)

    # ------------------------------------------------------------------
    # Build / Run
    # ------------------------------------------------------------------
    async def build_pipeline_async(
        self,
        pipeline_name: str,
        *,
        operator: str,
        operator_name: str,
        user_id: str,
        username: str,
    ) -> dict[str, Any]:
        definition, nodes, edges = await self._load_build_context_async(pipeline_name)
        run_id = uuid4().hex
        async with transaction_scope() as session:
            await self._dao.create_run(
                session,
                run_id=run_id,
                pipeline_id=definition["pipeline_id"],
                pipeline_version=definition["current_version_no"],
                trigger_type="manual",
                operator=operator,
                operator_name=operator_name,
            )
        try:
            output_dataset, row_count, output_columns, output_rows = await self._execute_build_async(
                definition,
                nodes,
                edges,
                run_id=run_id,
                operator=operator,
                operator_name=operator_name,
                user_id=user_id,
                username=username,
            )
        except Exception as error:
            message = self._safe_build_error(error)
            await self._mark_run_failed_async(run_id, message, operator, operator_name)
            raise PipelineBuildError(message) from error
        await self._mark_run_succeeded_async(
            run_id,
            definition,
            output_dataset,
            row_count,
            output_columns,
            output_rows,
            operator,
            operator_name,
        )
        return await self.get_run_async(run_id)

    async def list_runs_async(self, pipeline_name: str, limit: int = DEFAULT_RUN_LIST_LIMIT) -> list[dict[str, Any]]:
        name = self._require_name(pipeline_name)
        async with transaction_scope() as session:
            row = await self._dao.get_definition_by_name(session, name)
            if row is None:
                raise PipelineNotFoundError(f"Pipeline 不存在: {name}")
            runs = await self._dao.list_runs(session, row.pipeline_id, limit=max(1, min(int(limit), 100)))
            return [pipeline_run_row_to_dict(item) for item in runs]

    async def get_run_async(self, run_id: str) -> dict[str, Any]:
        key = self._require_name(run_id, "run_id")
        async with transaction_scope() as session:
            row = await self._dao.get_run_by_id(session, key)
            if row is None:
                raise PipelineNotFoundError(f"Pipeline run 不存在: {key}")
            data = pipeline_run_row_to_dict(row)
            definition = await self._dao.get_definition_by_pipeline_id(session, row.pipeline_id)
            data["pipeline_name"] = definition.pipeline_name if definition is not None else ""
            output_dataset_id = data["output_dataset_id"]
        data["output_dataset"] = (
            await self._dataset_service.get_dataset_async_by_id_async(int(output_dataset_id))
            if output_dataset_id
            else None
        )
        return data

    # ------------------------------------------------------------------
    # 内部：定义校验与版本追加
    # ------------------------------------------------------------------
    @staticmethod
    def _require_name(value: Any, label: str = "pipeline_name") -> str:
        name = str(value or "").strip()
        if not name:
            raise PipelineInvalidError(f"{label} 不能为空")
        return name

    @classmethod
    def _normalize_definition_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        target_layer = str(payload.get("target_layer") or "cleaned").strip()
        if target_layer not in OUTPUT_LAYERS:
            raise PipelineInvalidError(f"target_layer 不合法: {target_layer}")
        status = str(payload.get("status") or "draft").strip()
        if status not in PIPELINE_STATUSES:
            raise PipelineInvalidError(f"status 不合法: {status}")
        nodes = cls._normalize_nodes(payload.get("nodes"), target_layer)
        edges = cls._normalize_edges(payload.get("edges"))
        cls._validate_linear_chain(nodes, edges)
        return {
            "display_name": str(payload.get("display_name") or "").strip(),
            "description": str(payload.get("description") or "").strip(),
            "target_layer": target_layer,
            "status": status,
            "nodes": nodes,
            "edges": edges,
        }

    @classmethod
    def _normalize_nodes(cls, raw_nodes: Any, target_layer: str) -> list[dict[str, Any]]:
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise PipelineInvalidError("Pipeline nodes 不能为空")
        if len(raw_nodes) > MAX_NODES:
            raise PipelineInvalidError(f"Pipeline nodes 不能超过 {MAX_NODES} 个")
        nodes = [cls._normalize_node(raw, index, target_layer) for index, raw in enumerate(raw_nodes)]
        node_ids = [node["node_id"] for node in nodes]
        if len(set(node_ids)) != len(node_ids):
            raise PipelineInvalidError("Pipeline node_id 不能重复")
        return nodes

    @classmethod
    def _normalize_node(cls, raw: Any, index: int, target_layer: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise PipelineInvalidError("Pipeline node 必须是对象")
        node_id = str(raw.get("node_id") or f"node_{index + 1}").strip()
        node_type = str(raw.get("node_type") or "").strip()
        if node_type not in NODE_TYPES:
            raise PipelineInvalidError(f"node_type 不合法: {node_type or '(空)'}")
        config = raw.get("config") or {}
        if not isinstance(config, dict):
            raise PipelineInvalidError("Pipeline node config 必须是对象")
        return {
            "node_id": node_id,
            "node_type": node_type,
            "node_name": str(raw.get("node_name") or node_id).strip(),
            "config": cls._normalize_node_config(node_type, config, node_id, target_layer),
        }

    @classmethod
    def _normalize_node_config(
        cls,
        node_type: str,
        config: dict[str, Any],
        node_id: str,
        target_layer: str,
    ) -> dict[str, Any]:
        handlers = {
            "source": cls._normalize_source_config,
            "transform": cls._normalize_transform_config,
            "output": cls._normalize_output_config,
        }
        return handlers[node_type](config, node_id, target_layer)

    @staticmethod
    def _normalize_source_config(
        config: dict[str, Any],
        node_id: str,
        target_layer: str,
    ) -> dict[str, Any]:
        dataset_name = str(config.get("dataset_name") or "").strip()
        if not dataset_name:
            raise PipelineInvalidError(f"source 节点缺少 dataset_name: {node_id}")
        return {
            "dataset_name": dataset_name,
            "member_datasource_ids": [
                str(item).strip() for item in config.get("member_datasource_ids") or [] if str(item).strip()
            ],
            "limit": config.get("limit") or DEFAULT_FETCH_LIMIT,
        }

    @staticmethod
    def _normalize_transform_config(
        config: dict[str, Any],
        node_id: str,
        target_layer: str,
    ) -> dict[str, Any]:
        try:
            return pipeline_transforms.normalize_transform_config(config)
        except pipeline_transforms.TransformError as error:
            raise PipelineInvalidError(str(error)) from error

    @staticmethod
    def _normalize_output_config(
        config: dict[str, Any],
        node_id: str,
        target_layer: str,
    ) -> dict[str, Any]:
        dataset_name = str(config.get("dataset_name") or "").strip()
        if not dataset_name:
            raise PipelineInvalidError(f"output 节点缺少 dataset_name: {node_id}")
        layer = str(config.get("layer") or target_layer).strip()
        if layer not in OUTPUT_LAYERS:
            raise PipelineInvalidError(f"output.layer 不合法: {layer}")
        return {
            "dataset_name": dataset_name,
            "layer": layer,
            "display_name": str(config.get("display_name") or "").strip(),
            "description": str(config.get("description") or "").strip(),
            "primary_key": [str(item).strip() for item in config.get("primary_key") or [] if str(item).strip()],
            "status": str(config.get("status") or "experimental").strip() or "experimental",
        }

    @staticmethod
    def _normalize_edges(raw_edges: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_edges, list) or not raw_edges:
            raise PipelineInvalidError("Pipeline edges 不能为空")
        edges = []
        for index, raw in enumerate(raw_edges):
            if not isinstance(raw, dict):
                raise PipelineInvalidError("Pipeline edge 必须是对象")
            from_node_id = str(raw.get("from_node_id") or "").strip()
            to_node_id = str(raw.get("to_node_id") or "").strip()
            if not from_node_id or not to_node_id:
                raise PipelineInvalidError("Pipeline edge 必须包含 from_node_id/to_node_id")
            edges.append(
                {
                    "edge_id": str(raw.get("edge_id") or f"edge_{index + 1}").strip(),
                    "from_node_id": from_node_id,
                    "to_node_id": to_node_id,
                }
            )
        return edges

    @classmethod
    def _validate_linear_chain(cls, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
        node_types = [node["node_type"] for node in nodes]
        if node_types.count("source") != 1:
            raise PipelineInvalidError("Pipeline 必须且只能有 1 个 source 节点")
        if node_types.count("output") != 1:
            raise PipelineInvalidError("Pipeline 必须且只能有 1 个 output 节点")
        if node_types.count("transform") < 1:
            raise PipelineInvalidError("Pipeline 至少需要 1 个 transform 节点")
        if len(edges) != len(nodes) - 1:
            raise PipelineInvalidError("Pipeline 必须是单链：边数 = 节点数 - 1")
        by_id = {node["node_id"]: node for node in nodes}
        outgoing = cls._edge_maps(by_id, edges)
        cls._walk_chain(by_id, outgoing)

    @staticmethod
    def _edge_maps(by_id: dict[str, dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, str]:
        outgoing: dict[str, str] = {}
        incoming: set[str] = set()
        for edge in edges:
            from_node_id = edge["from_node_id"]
            to_node_id = edge["to_node_id"]
            if from_node_id not in by_id or to_node_id not in by_id:
                raise PipelineInvalidError("Pipeline edge 引用了不存在的节点")
            if from_node_id == to_node_id:
                raise PipelineInvalidError("Pipeline 不支持自环边")
            if from_node_id in outgoing:
                raise PipelineInvalidError(f"Pipeline 节点存在多条出边: {from_node_id}")
            if to_node_id in incoming:
                raise PipelineInvalidError(f"Pipeline 节点存在多条入边: {to_node_id}")
            outgoing[from_node_id] = to_node_id
            incoming.add(to_node_id)
        return outgoing

    @staticmethod
    def _walk_chain(by_id: dict[str, dict[str, Any]], outgoing: dict[str, str]) -> list[str]:
        source_id = next((node_id for node_id, node in by_id.items() if node["node_type"] == "source"), "")
        visited = [source_id]
        current = source_id
        while current in outgoing:
            current = outgoing[current]
            if current in visited:
                raise PipelineInvalidError("Pipeline 定义存在环")
            visited.append(current)
        if len(visited) != len(by_id):
            raise PipelineInvalidError("Pipeline 存在未连通节点")
        if by_id[current]["node_type"] != "output":
            raise PipelineInvalidError("Pipeline 链路末端必须是 output 节点")
        return visited

    async def _append_definition_version(
        self,
        session: Any,
        row: ChatDataPipelineDefinition,
        normalized: dict[str, Any],
        operator: str,
        operator_name: str,
    ) -> None:
        version_no = int(row.current_version_no or 0) + 1
        await self._dao.append_version(
            session,
            pipeline_id=row.pipeline_id,
            version_no=version_no,
            nodes=normalized["nodes"],
            edges=normalized["edges"],
            operator=operator,
            operator_name=operator_name,
        )
        row.display_name = normalized["display_name"] or row.display_name or row.pipeline_name
        row.description_text = normalized["description"]
        row.target_layer = normalized["target_layer"]
        row.lifecycle_status = normalized["status"]
        row.current_version_no = version_no
        row.update_by = operator
        row.update_by_name = operator_name
        session.add(row)
        await session.flush()

    @staticmethod
    def _output_dataset_name(nodes: list[dict[str, Any]]) -> str:
        for node in nodes:
            if node.get("node_type") == "output":
                return str((node.get("config") or {}).get("dataset_name") or "").strip()
        return ""

    async def _detail_async(self, session: Any, row: ChatDataPipelineDefinition) -> dict[str, Any]:
        await session.refresh(row)
        data = pipeline_row_to_dict(row)
        version_no = int(row.current_version_no or 0)
        nodes = (
            [
                pipeline_node_row_to_dict(item)
                for item in await self._dao.list_nodes(session, row.pipeline_id, version_no)
            ]
            if version_no
            else []
        )
        data["nodes"] = nodes
        data["edges"] = (
            [
                pipeline_edge_row_to_dict(item)
                for item in await self._dao.list_edges(session, row.pipeline_id, version_no)
            ]
            if version_no
            else []
        )
        data["output_dataset_name"] = self._output_dataset_name(nodes)
        data["runs"] = [
            pipeline_run_row_to_dict(item) for item in await self._dao.list_runs(session, row.pipeline_id, limit=10)
        ]
        return data

    # ------------------------------------------------------------------
    # 内部：build 执行
    # ------------------------------------------------------------------
    async def _load_build_context_async(
        self, pipeline_name: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        name = self._require_name(pipeline_name)
        async with transaction_scope() as session:
            row = await self._dao.get_definition_by_name(session, name)
            if row is None:
                raise PipelineNotFoundError(f"Pipeline 不存在: {name}")
            version_no = int(row.current_version_no or 0)
            if version_no <= 0:
                raise PipelineInvalidError(f"Pipeline 还没有可执行的定义版本: {name}")
            nodes = [
                pipeline_node_row_to_dict(item)
                for item in await self._dao.list_nodes(session, row.pipeline_id, version_no)
            ]
            edges = [
                pipeline_edge_row_to_dict(item)
                for item in await self._dao.list_edges(session, row.pipeline_id, version_no)
            ]
            definition = pipeline_row_to_dict(row)
        try:
            normalized = self._normalize_definition_payload(
                {
                    "display_name": definition["display_name"],
                    "description": definition["description"],
                    "target_layer": definition["target_layer"],
                    "status": definition["status"],
                    "nodes": nodes,
                    "edges": edges,
                }
            )
        except PipelineInvalidError as error:
            raise PipelineBuildError(f"Pipeline 定义不合法: {error}") from error
        return definition, normalized["nodes"], normalized["edges"]

    async def _execute_build_async(
        self,
        definition: dict[str, Any],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        *,
        run_id: str,
        operator: str,
        operator_name: str,
        user_id: str,
        username: str,
    ) -> tuple[dict[str, Any], int, list[dict[str, Any]], list[dict[str, Any]]]:
        source_node, transform_nodes, output_node = self._split_chain(nodes, edges)
        input_dataset = await self._dataset_service.get_dataset_async(source_node["config"]["dataset_name"])
        source_rows = await load_dataset_rows_async(
            input_dataset,
            member_datasource_ids=source_node["config"].get("member_datasource_ids") or None,
            user_id=user_id,
            username=username,
            limit=source_node["config"].get("limit"),
        )
        input_columns = self._input_columns(input_dataset, source_rows)
        transform_configs = [
            pipeline_transforms.normalize_transform_config(node["config"], input_columns) for node in transform_nodes
        ]
        output_rows, output_columns = pipeline_transforms.apply_transforms(
            source_rows, input_columns, transform_configs
        )
        output_dataset = await self._commit_output_async(
            definition,
            input_dataset,
            output_node,
            output_rows,
            output_columns,
            run_id=run_id,
            operator=operator,
            operator_name=operator_name,
        )
        return output_dataset, len(output_rows), output_columns, output_rows

    @staticmethod
    def _split_chain(
        nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        by_id = {node["node_id"]: node for node in nodes}
        outgoing = {edge["from_node_id"]: edge["to_node_id"] for edge in edges}
        ordered_ids = DatasetPipelineService._walk_chain(by_id, outgoing)
        ordered = [by_id[node_id] for node_id in ordered_ids]
        transform_nodes = [node for node in ordered if node["node_type"] == "transform"]
        return ordered[0], transform_nodes, ordered[-1]

    @staticmethod
    def _input_columns(input_dataset: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        columns = [item for item in input_dataset.get("columns") or [] if isinstance(item, dict)]
        if columns:
            return columns
        if not rows:
            return []
        return [
            {"name": name, "display_name": name, "data_type": "string", "physical_column": name} for name in rows[0]
        ]

    @staticmethod
    def _current_version_id(input_dataset: dict[str, Any]) -> str:
        versions = [item for item in input_dataset.get("versions") or [] if isinstance(item, dict)]
        version_id = str((versions[0] or {}).get("version_id") or "").strip() if versions else ""
        if not version_id:
            raise PipelineBuildError(f"输入 Dataset 没有可用版本: {input_dataset.get('dataset_name')}")
        return version_id

    async def _commit_output_async(
        self,
        definition: dict[str, Any],
        input_dataset: dict[str, Any],
        output_node: dict[str, Any],
        output_rows: list[dict[str, Any]],
        output_columns: list[dict[str, Any]],
        *,
        run_id: str,
        operator: str,
        operator_name: str,
    ) -> dict[str, Any]:
        config = output_node["config"]
        layer = str(config.get("layer") or definition["target_layer"]).strip()
        if layer not in DATASET_LAYERS:
            raise PipelineBuildError(f"输出 Dataset layer 不合法: {layer}")
        output_column_names = {str(item.get("name") or "") for item in output_columns}
        primary_key = [name for name in input_dataset.get("primary_key") or [] if name in output_column_names]
        payload = {
            "display_name": config.get("display_name") or config["dataset_name"],
            "description": config.get("description") or f"Pipeline {definition['pipeline_name']} 输出",
            "primary_key": primary_key,
            "columns": output_columns,
            "bindings": [],
            "status": config.get("status") or "experimental",
            "dataset_layer": layer,
            "dataset_origin": "pipeline",
        }
        return await self._dataset_service.commit_pipeline_output_async(
            config["dataset_name"],
            payload,
            run_id=run_id,
            input_version_ids=[self._current_version_id(input_dataset)],
            operation="pipeline_build",
            row_count=len(output_rows),
            operator=operator,
            operator_name=operator_name,
        )

    # ------------------------------------------------------------------
    # 内部：run 状态
    # ------------------------------------------------------------------
    async def _mark_run_failed_async(self, run_id: str, message: str, operator: str, operator_name: str) -> None:
        async with transaction_scope() as session:
            row = await self._dao.get_run_by_id(session, run_id)
            if row is None:
                logger.warning("Pipeline run 不存在，无法标记失败: %s", run_id)
                return
            row.update_by = operator
            row.update_by_name = operator_name
            await self._dao.finish_run(session, row, status="failed", error_message=message)

    async def _mark_run_succeeded_async(
        self,
        run_id: str,
        definition: dict[str, Any],
        output_dataset: dict[str, Any],
        row_count: int,
        output_columns: list[dict[str, Any]],
        output_rows: list[dict[str, Any]],
        operator: str,
        operator_name: str,
    ) -> None:
        summary = {
            "pipeline_name": definition["pipeline_name"],
            "dataset_name": output_dataset["dataset_name"],
            "dataset_layer": output_dataset.get("dataset_layer"),
            "row_count": row_count,
            "columns": [str(item.get("name") or "") for item in output_columns],
            "preview": self._preview_rows(output_rows),
        }
        async with transaction_scope() as session:
            row = await self._dao.get_run_by_id(session, run_id)
            if row is None:
                logger.warning("Pipeline run 不存在，无法标记成功: %s", run_id)
                return
            row.update_by = operator
            row.update_by_name = operator_name
            await self._dao.finish_run(
                session,
                row,
                status="succeeded",
                output_dataset_id=int(output_dataset["id"]),
                output_summary=summary,
            )

    @staticmethod
    def _preview_rows(rows: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
        try:
            return json.loads(json.dumps(rows[:limit], ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return []

    @staticmethod
    def _safe_build_error(error: Exception) -> str:
        known_types: tuple[ErrorType, ...] = (
            PipelineBuildError,
            PipelineInvalidError,
            DatasetInvalidError,
            DatasetNotFoundError,
            pipeline_transforms.TransformError,
        )
        if isinstance(error, known_types):
            return f"Pipeline build 失败: {error}"
        logger.error("Pipeline build 未预期异常: %s", error, exc_info=True)
        return "Pipeline build 失败，请查看服务端日志"


dataset_pipeline_service = DatasetPipelineService()

__all__ = [
    "DEFAULT_RUN_LIST_LIMIT",
    "DatasetPipelineService",
    "dataset_pipeline_service",
]
