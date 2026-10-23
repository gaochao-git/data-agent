"""Dataset ↔ 语义模型关联服务。

设计边界（对齐 Ossie mapping document，2026-10-23）：
- 指标口径唯一权威仍在语义层（``semantic_config``），本服务只存引用与列对照；
- 关联挂在 Logical Dataset 上，因此一个 Dataset 下所有对象类型共享同一份映射；
- 运行时由「对象类型 source_binding（属性→Dataset 逻辑列）」+「本关联（逻辑列→语义维度）」
  组合出「对象属性→指标维度」，不需要把指标定义复制进本体。
"""

from __future__ import annotations

from typing import Any

from apps.chat_data_app.daos.dao_session import transaction_scope
from apps.chat_data_app.daos.dataset_semantic_link_dao import DatasetSemanticLinkDao, link_row_to_dict
from apps.chat_data_app.services.semantic.run.semantic_config_service import get_semantic_config_service
from utils.logger import get_logger
from utils.models import ChatDataDatasetSemanticLink

from .dataset_service import logical_dataset_service

logger = get_logger(__name__)

LINK_STATUSES = ("draft", "active")
_ACTIVE_EXECUTE_STATUSES = ("active",)


class DatasetSemanticLinkError(Exception):
    """Dataset ↔ 语义模型关联业务异常基类。"""


class DatasetSemanticLinkInvalidError(DatasetSemanticLinkError):
    """关联参数/口径不合法。"""


class DatasetSemanticLinkNotFoundError(DatasetSemanticLinkError):
    """关联不存在。"""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _metric_kind(metric: dict[str, Any]) -> str:
    kind = _text(metric.get("metric_kind")).lower()
    return kind if kind in {"derived", "composite"} else "atomic"


class DatasetSemanticLinkService:
    """Dataset ↔ 语义模型关联的校验、持久化与运行时解析。"""

    def __init__(self) -> None:
        self._dao = DatasetSemanticLinkDao()

    # ---------- 查询 ----------

    async def _dataset_detail_async(self, dataset_name: str) -> dict[str, Any]:
        name = _text(dataset_name)
        if not name:
            raise DatasetSemanticLinkInvalidError("dataset_name 不能为空")
        return await logical_dataset_service.get_dataset_async(name)

    async def list_links_async(
        self,
        dataset_name: str,
        *,
        datasource_group_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出某 Dataset 的语义模型关联（含 Dataset/模型名称）。"""
        dataset = await self._dataset_detail_async(dataset_name)
        async with transaction_scope() as session:
            rows = await self._dao.list_by_dataset(
                session,
                int(dataset["id"]),
                datasource_group_id=_text(datasource_group_id) or None,
            )
            row_views = [link_row_to_dict(row) for row in rows]
        return [await self._link_view_async(dataset, row_view) for row_view in row_views]

    async def list_links_for_dataset_id_async(
        self,
        dataset_id: int,
        *,
        datasource_group_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """按 Dataset ID 列出关联（运行时用；Dataset 已由调用方校验）。"""
        async with transaction_scope() as session:
            rows = await self._dao.list_by_dataset(
                session,
                int(dataset_id),
                datasource_group_id=_text(datasource_group_id) or None,
            )
            row_views = [link_row_to_dict(row) for row in rows]
        dataset = await logical_dataset_service.get_dataset_async_by_id_async(int(dataset_id))
        dataset = dataset or {}
        return [await self._link_view_async(dataset, row_view) for row_view in row_views]

    async def _link_view_async(self, dataset: dict[str, Any], row_view: dict[str, Any]) -> dict[str, Any]:
        view = dict(row_view)
        view["dataset_name"] = _text(dataset.get("dataset_name"))
        config = await self._load_config_async(view["datasource_group_id"], view["semantic_config_id"])
        view["semantic_config_name"] = _text((config or {}).get("name"))
        view["metric_count"] = len((config or {}).get("metrics") or [])
        return view

    # ---------- 写入 ----------

    async def upsert_link_async(
        self,
        dataset_name: str,
        semantic_config_id: str,
        payload: dict[str, Any],
        *,
        operator: str = "",
        operator_name: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        """创建/更新关联；保存前完成 Dataset/语义模型/指标/维度/表名全量校验。"""
        dataset = await self._dataset_detail_async(dataset_name)
        config_id = _text(semantic_config_id)
        if not config_id:
            raise DatasetSemanticLinkInvalidError("semantic_config_id 不能为空")
        group_id = _text(payload.get("datasource_group_id"))
        if not group_id:
            raise DatasetSemanticLinkInvalidError("datasource_group_id 不能为空")
        group_bindings = [
            item for item in dataset.get("bindings") or [] if _text(item.get("datasource_group_id")) == group_id
        ]
        if not group_bindings:
            raise DatasetSemanticLinkInvalidError(
                f"Dataset 在数据源组 {group_id} 下没有 member binding，无法关联语义模型"
            )
        config = await self._require_config_async(group_id, config_id, user_id=user_id)
        table_name = self._resolve_table_name(dataset, group_bindings, payload.get("table_name"), config)
        metrics = [item for item in config.get("metrics") or [] if isinstance(item, dict)]
        dimensions = [item for item in config.get("dimensions") or [] if isinstance(item, dict)]
        metric_scope = self._normalize_metric_scope(payload.get("metric_scope"), metrics, table_name)
        column_mappings = self._normalize_column_mappings(
            dataset.get("columns") or [], dimensions, payload.get("column_mappings"), table_name
        )
        status = _text(payload.get("lifecycle_status")) or "draft"
        if status not in LINK_STATUSES:
            raise DatasetSemanticLinkInvalidError(f"lifecycle_status 不合法: {status}")
        async with transaction_scope() as session:
            row = await self._dao.get_by_dataset_and_config(session, int(dataset["id"]), config_id)
            if row is None:
                row = ChatDataDatasetSemanticLink(
                    dataset_id=int(dataset["id"]),
                    semantic_config_id=config_id,
                    datasource_group_id=group_id,
                    table_name=table_name,
                    column_mappings_json="{}",
                    metric_scope_json="[]",
                    lifecycle_status=status,
                    delete_flag=0,
                    create_by=operator,
                    create_by_name=operator_name,
                    update_by=operator,
                    update_by_name=operator_name,
                )
            await self._dao.save(
                session,
                row,
                {
                    "table_name": table_name,
                    "column_mappings": column_mappings,
                    "metric_scope": metric_scope,
                    "lifecycle_status": status,
                },
                operator=operator,
                operator_name=operator_name,
            )
            await session.refresh(row)
            row_view = link_row_to_dict(row)
        logger.info(
            "Dataset 语义模型关联已保存: dataset=%s group=%s config=%s scope=%s",
            dataset_name,
            group_id,
            config_id,
            metric_scope,
        )
        return await self._link_view_async(dataset, row_view)

    async def delete_link_async(
        self,
        dataset_name: str,
        semantic_config_id: str,
        *,
        datasource_group_id: str | None = None,
        operator: str = "",
        operator_name: str = "",
    ) -> None:
        dataset = await self._dataset_detail_async(dataset_name)
        async with transaction_scope() as session:
            row = await self._dao.get_by_dataset_and_config(session, int(dataset["id"]), _text(semantic_config_id))
            if row is None:
                raise DatasetSemanticLinkNotFoundError(
                    f"Dataset 未关联该语义模型: {dataset_name} / {_text(semantic_config_id)}"
                )
            if _text(datasource_group_id) and _text(row.datasource_group_id) != _text(datasource_group_id):
                raise DatasetSemanticLinkInvalidError("关联不属于指定数据源组")
            await self._dao.soft_delete(session, row, operator=operator, operator_name=operator_name)

    # ---------- 运行时 ----------

    async def get_active_link_async(
        self,
        dataset_id: int,
        datasource_group_id: str,
        *,
        semantic_config_id: str | None = None,
    ) -> dict[str, Any] | None:
        """运行时取一条可执行关联；未指定模型时优先 active，其次 draft。"""
        async with transaction_scope() as session:
            rows = await self._dao.list_by_dataset(
                session, int(dataset_id), datasource_group_id=_text(datasource_group_id)
            )
            row_views = [link_row_to_dict(row) for row in rows]
        if not row_views:
            return None
        config_id = _text(semantic_config_id)
        if config_id:
            row_views = [view for view in row_views if _text(view.get("semantic_config_id")) == config_id]
        row_views.sort(key=lambda view: 0 if _text(view.get("lifecycle_status")) in _ACTIVE_EXECUTE_STATUSES else 1)
        dataset = await logical_dataset_service.get_dataset_async_by_id_async(int(dataset_id))
        return await self._link_view_async(dataset or {}, row_views[0])

    async def load_config_for_link_async(
        self,
        link: dict[str, Any],
        *,
        user_id: str = "",
    ) -> dict[str, Any] | None:
        return await self._load_config_async(
            _text(link.get("datasource_group_id")),
            _text(link.get("semantic_config_id")),
            user_id=user_id,
        )

    async def _load_config_async(
        self,
        group_id: str,
        config_id: str,
        *,
        user_id: str = "",
    ) -> dict[str, Any] | None:
        if not group_id or not config_id:
            return None
        try:
            return await get_semantic_config_service().get_config_async(group_id, config_id, user_id)
        except Exception as exc:  # noqa: BLE001 - 关联视图允许模型缺失时降级展示
            logger.warning("读取关联语义模型失败: group=%s config=%s error=%s", group_id, config_id, exc)
            return None

    async def _require_config_async(
        self,
        group_id: str,
        config_id: str,
        *,
        user_id: str = "",
    ) -> dict[str, Any]:
        config = await self._load_config_async(group_id, config_id, user_id=user_id)
        if config is None:
            raise DatasetSemanticLinkInvalidError(f"数据源组 {group_id} 下不存在语义模型: {config_id}")
        if not isinstance(config.get("metrics"), list) or not isinstance(config.get("dimensions"), list):
            raise DatasetSemanticLinkInvalidError(f"语义模型结构不完整: {config_id}")
        return config

    # ---------- 校验辅助 ----------

    @staticmethod
    def _resolve_table_name(
        dataset: dict[str, Any],
        group_bindings: list[dict[str, Any]],
        raw_table_name: Any,
        config: dict[str, Any],
    ) -> str:
        table_name = _text(raw_table_name) or _text(dataset.get("dataset_name"))
        allowed_tables = {_text(dataset.get("dataset_name"))}
        allowed_tables.update(_text(item.get("physical_table")) for item in group_bindings)
        allowed_tables.discard("")
        if table_name not in allowed_tables:
            raise DatasetSemanticLinkInvalidError(
                f"table_name 必须是该 Dataset 在该数据源组下的物理表 {sorted(allowed_tables)}: {table_name}"
            )
        config_tables = {
            _text(item.get("table"))
            for item in list(config.get("metrics") or []) + list(config.get("dimensions") or [])
            if isinstance(item, dict) and _text(item.get("table"))
        }
        if config_tables and table_name not in config_tables:
            raise DatasetSemanticLinkInvalidError(
                f"table_name 不存在于语义模型 {config.get('name') or config.get('id')}: {table_name}"
            )
        return table_name

    @staticmethod
    def _normalize_metric_scope(
        raw_scope: Any,
        metrics: list[dict[str, Any]],
        table_name: str,
    ) -> list[str]:
        metrics_by_name = {_text(item.get("name")): item for item in metrics if _text(item.get("name"))}
        if raw_scope and not isinstance(raw_scope, list):
            raise DatasetSemanticLinkInvalidError("metric_scope 必须是字符串数组")
        requested = [_text(item) for item in raw_scope or [] if _text(item)]
        unknown = [name for name in requested if name not in metrics_by_name]
        if unknown:
            raise DatasetSemanticLinkInvalidError(f"metric_scope 包含不存在的指标: {unknown}")
        scope = requested or sorted(metrics_by_name)
        mismatched = [name for name in scope if _text(metrics_by_name[name].get("table")) not in ("", table_name)]
        if mismatched:
            raise DatasetSemanticLinkInvalidError(f"指标不属于该表 {table_name}: {mismatched}")
        return scope

    @staticmethod
    def _normalize_column_mappings(
        dataset_columns: list[Any],
        dimensions: list[dict[str, Any]],
        raw_mappings: Any,
        table_name: str,
    ) -> dict[str, str]:
        """Dataset 逻辑列 → 语义维度名；空映射时按同名列/列名自动对照。"""
        column_names = {
            _text(item.get("name")) for item in dataset_columns if isinstance(item, dict) and _text(item.get("name"))
        }
        dimension_by_name = {_text(item.get("name")): item for item in dimensions if _text(item.get("name"))}
        dimension_by_column = {_text(item.get("column")): item for item in dimensions if _text(item.get("column"))}
        mappings = (
            _explicit_column_mappings(raw_mappings, column_names, dimension_by_name, dimension_by_column, table_name)
            if raw_mappings
            else _auto_column_mappings(column_names, dimension_by_name, dimension_by_column, table_name)
        )
        if not mappings:
            raise DatasetSemanticLinkInvalidError(
                "Dataset 逻辑列与语义维度没有任何可对照项，请显式提供 column_mappings"
            )
        return mappings


def _explicit_column_mappings(
    raw_mappings: Any,
    column_names: set[str],
    dimension_by_name: dict[str, dict[str, Any]],
    dimension_by_column: dict[str, dict[str, Any]],
    table_name: str,
) -> dict[str, str]:
    """显式映射：Dataset 逻辑列 → 语义维度名（值也接受维度 column）。"""
    if not isinstance(raw_mappings, dict):
        raise DatasetSemanticLinkInvalidError("column_mappings 必须是 {Dataset逻辑列: 语义维度名} 结构")
    mappings: dict[str, str] = {}
    for raw_column, raw_dimension in raw_mappings.items():
        column = _text(raw_column)
        if column not in column_names:
            raise DatasetSemanticLinkInvalidError(f"column_mappings 引用了不存在的 Dataset 逻辑列: {column}")
        mappings[column] = _resolve_dimension_name(
            _text(raw_dimension), dimension_by_name, dimension_by_column, table_name
        )
    return mappings


def _auto_column_mappings(
    column_names: set[str],
    dimension_by_name: dict[str, dict[str, Any]],
    dimension_by_column: dict[str, dict[str, Any]],
    table_name: str,
) -> dict[str, str]:
    """自动对照：Dataset 逻辑列名与语义维度 name/column 同名即映射。"""
    mappings: dict[str, str] = {}
    for column in sorted(column_names):
        dimension = dimension_by_name.get(column) or dimension_by_column.get(column)
        if dimension is None or _text(dimension.get("table")) not in ("", table_name):
            continue
        mappings[column] = _text(dimension.get("name"))
    return mappings


def _resolve_dimension_name(
    dimension_ref: str,
    dimension_by_name: dict[str, dict[str, Any]],
    dimension_by_column: dict[str, dict[str, Any]],
    table_name: str,
) -> str:
    """维度引用（name 或 column）→ 维度 name，并校验归属表。"""
    dimension = dimension_by_name.get(dimension_ref) or dimension_by_column.get(dimension_ref)
    if dimension is None:
        raise DatasetSemanticLinkInvalidError(f"column_mappings 引用了不存在的语义维度: {dimension_ref}")
    if _text(dimension.get("table")) not in ("", table_name):
        raise DatasetSemanticLinkInvalidError(f"语义维度不属于该表 {table_name}: {dimension_ref}")
    return _text(dimension.get("name"))


dataset_semantic_link_service = DatasetSemanticLinkService()

__all__ = [
    "DatasetSemanticLinkError",
    "DatasetSemanticLinkInvalidError",
    "DatasetSemanticLinkNotFoundError",
    "DatasetSemanticLinkService",
    "dataset_semantic_link_service",
]
