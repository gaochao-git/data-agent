"""本体服务：CRUD 编排、状态机与治理门禁（对齐 Palantir Ontology Manager）。

分层边界：DAO 只做持久化；本层负责业务规则——确定性校验器（ontology_validator）
红线检查、certified 冻结门禁、破坏性变更显式确认、状态机与关系类型级联、映射
「一源一对象类型」唯一归属检查与全局枚举引用存在性校验。
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from apps.chat_data_app.services.dataset.dataset_service import logical_dataset_service
from utils.logger import get_logger

from ...daos.dao_session import transaction_scope
from ...daos.meta_global_enum_dao import _split_enum_ref
from ...daos.ontology_dao import OntologyDao
from ...daos.semantic_config_dao import SemanticConfigDao
from ..meta.global_enum_service import MetaGlobalEnumNotFoundError, global_enum_service
from . import ontology_validator as validator
from .errors import (
    OntologyAlreadyExistsError,
    OntologyInvalidError,
    OntologyNotFoundError,
    OntologyStateInvalidError,
)

logger = get_logger(__name__)

# 映射存在性/维度校验用（模块级单例，测试经 monkeypatch 打桩，同 global_enum_service 模式）
semantic_config_dao = SemanticConfigDao()

STATUS_ORDER = {"draft": 0, "experimental": 1, "certified": 2}

# 关系类型状态手动迁移的封闭词表（deprecated 仅随对象类型级联）
RELATION_MANUAL_STATUSES = ("draft", "experimental", "certified")

# 对象类型复活时的状态重置：同名软删行重建视为新建对象类型，废弃字段必须清空。
_REVIVED_OBJECT_RESET = {
    "status": "draft",
    "deprecation_reason": "",
    "successor_object_type_name": "",
    "deprecated_until": None,
}


def _transactional(method: Callable[..., Any]) -> Callable[..., Any]:
    """公开写方法统一事务边界：多步 DAO 写要么全部成功、要么整体回滚。

    DAO 的 session_scope 复用 dao_session 的 ContextVar 环境会话，因此这里只需
    建立事务作用域，嵌套调用会复用同一会话（不在 DAO 层提交）。
    """

    @functools.wraps(method)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        async with transaction_scope():
            return await method(self, *args, **kwargs)

    return wrapper


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status_rank(status: str) -> int:
    return STATUS_ORDER.get(status, 0)


def _derived_relation_status(from_status: str, to_status: str) -> str:
    """关系类型状态 = 两侧对象类型状态的较低者；任一侧废弃则关系类型废弃。"""
    if from_status == "deprecated" or to_status == "deprecated":
        return "deprecated"
    order = min(_status_rank(from_status), _status_rank(to_status))
    return {0: "draft", 1: "experimental", 2: "certified"}[order]


class OntologyService:
    """本体/对象类型/属性/关系类型业务规则与治理门禁。"""

    def __init__(self, dao: OntologyDao | None = None) -> None:
        # 允许注入 DAO（测试用），默认单例 DAO
        self._dao = dao or OntologyDao()

    # ---------- 本体 ----------

    async def list_ontologies_async(self) -> list[dict[str, Any]]:
        return await self._dao.list_ontologies_async()

    @_transactional
    async def upsert_ontology_async(
        self,
        ontology_name: str,
        payload: dict[str, Any],
        *,
        operator: str = "",
        operator_name: str = "",
        allow_overwrite: bool = False,
    ) -> dict[str, Any]:
        """创建或编辑本体（纯语义容器，不绑数据源组）。

        软删行同名重建走复活（不要求 allow_overwrite）。对象类型的取数后端由其
        映射指向的语义模型决定（可跨组），本体层不做组锚点校验。
        """
        name = validator.validate_api_name(ontology_name, "本体API名", max_length=validator.ONTOLOGY_NAME_MAX_LENGTH)
        normalized = validator.validate_ontology_payload(payload)
        existing = await self._dao.get_ontology_row_async(name, include_deleted=True)
        existing_active = existing is not None and int(existing.get("delete_flag") or 0) == 0
        if existing_active and not allow_overwrite:
            raise OntologyAlreadyExistsError(f"本体已存在: {name}，请更换本体名或编辑现有本体")
        saved = await self._dao.upsert_ontology_async(
            name,
            normalized,
            allow_overwrite=allow_overwrite,
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        if saved is None:  # 理论不可达（并发建同名本体由唯一键兜底）
            raise OntologyAlreadyExistsError(f"本体已存在: {name}")
        detail = await self._dao.get_ontology_row_async(name)
        if detail is None:  # 理论不可达（upsert 刚成功）
            raise OntologyStateInvalidError(f"本体保存后读取失败: {name}")
        return detail

    @_transactional
    async def delete_ontology_async(self, ontology_name: str, *, operator: str = "", operator_name: str = "") -> None:
        """删除本体（软删）：不存在报 404，挂有活跃对象类型时拒绝。"""
        existing = await self._dao.get_ontology_row_async(ontology_name)
        if existing is None:
            raise OntologyNotFoundError(f"本体不存在: {ontology_name}")
        counts = await self._dao.count_objects_by_ontology_ids_async([int(existing["id"])])
        if counts.get(int(existing["id"]), 0) > 0:
            raise OntologyStateInvalidError(
                f"本体下仍有 {counts[int(existing['id'])]} 个对象类型，请先移出或删除对象类型后再删除本体"
            )
        await self._dao.soft_delete_ontology_async(ontology_name, update_by=operator, update_by_name=operator_name)

    # ---------- 对象类型查询 ----------

    async def list_object_types_async(
        self, keyword: str = "", status: str = "", ontology_id: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._dao.list_object_types_async(keyword, status, ontology_id)

    async def get_object_type_async(self, object_type_name: str) -> dict[str, Any] | None:
        """对象类型详情（含属性集与语义模型映射；软删行视为不存在）。"""
        data = await self._dao.get_object_type_row_async(object_type_name)
        if data is None:
            return None
        data["properties"] = await self._dao.list_properties_async(int(data["id"]))
        binding_getter = getattr(self._dao, "get_source_binding_async", None)
        binding = await binding_getter(int(data["id"])) if callable(binding_getter) else None
        if binding:
            dataset = await logical_dataset_service.get_dataset_async_by_id_async(int(binding["dataset_id"]))
            if dataset:
                binding["dataset_name"] = dataset.get("dataset_name") or ""
                binding["binding_count"] = len(dataset.get("bindings") or [])
                binding["member_datasource_ids"] = [
                    str(item.get("member_datasource_id") or "")
                    for item in dataset.get("bindings") or []
                    if item.get("member_datasource_id")
                ]
        data["source_binding"] = binding
        return data

    async def _ensure_dataset_not_reused_async(
        self,
        dataset: dict[str, Any],
        dataset_id: int,
        object_type_id: int,
    ) -> None:
        """一源一对象类型校验（对齐 Palantir：一个 datasource 只能支撑一个对象类型）。"""
        conflicts = await self._dao.list_source_binding_conflicts_async(
            dataset_id, exclude_object_type_id=object_type_id
        )
        if not conflicts:
            return
        owners = "、".join(item["object_type_name"] for item in conflicts if item.get("object_type_name"))
        raise OntologyInvalidError(
            f"Dataset {dataset.get('dataset_name') or dataset_id} 已被对象类型 {owners} 绑定；"
            "一张 Dataset 只能支撑一个对象类型（对齐 Palantir 一源一对象类型）"
        )

    @staticmethod
    def _validate_source_binding_columns(
        detail: dict[str, Any],
        dataset: dict[str, Any],
        column_mappings: Any,
    ) -> dict[str, str]:
        """校验对象属性 → Dataset 逻辑列映射，返回归一化映射。"""
        if not isinstance(column_mappings, dict):
            raise OntologyInvalidError("column_mappings 必须是对象")
        dataset_columns = {str(item.get("name") or "") for item in dataset.get("columns") or []}
        property_names = {str(item.get("property_name") or "") for item in detail.get("properties") or []}
        for property_name, column_name in column_mappings.items():
            if property_name not in property_names:
                raise OntologyInvalidError(f"对象属性不存在: {property_name}")
            if column_name not in dataset_columns:
                raise OntologyInvalidError(f"Dataset 列不存在: {column_name}")
        return {str(key): str(value) for key, value in column_mappings.items()}

    async def set_source_binding_async(
        self,
        object_type_name: str,
        payload: dict[str, Any],
        *,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """把对象类型绑定到 Logical Dataset；本体不再引用语义模型。"""
        name = validator.validate_api_name(object_type_name, "对象类型API名")
        detail = await self.get_object_type_async(name)
        if detail is None:
            raise OntologyNotFoundError(f"对象类型不存在: {name}")
        dataset_id = int(payload.get("dataset_id") or 0)
        if dataset_id <= 0:
            raise OntologyInvalidError("dataset_id 不能为空")
        dataset = await logical_dataset_service.get_dataset_async_by_id_async(dataset_id)
        if dataset is None:
            raise OntologyInvalidError(f"Dataset 不存在: {dataset_id}")
        await self._ensure_dataset_not_reused_async(dataset, dataset_id, int(detail["id"]))
        column_mappings = self._validate_source_binding_columns(detail, dataset, payload.get("column_mappings") or {})
        return await self._dao.upsert_source_binding_async(
            int(detail["id"]),
            dataset_id=dataset_id,
            column_mappings={str(key): str(value) for key, value in column_mappings.items()},
            operator=operator,
            operator_name=operator_name,
        )

    # ---------- 对象类型创建/编辑 ----------

    @_transactional
    async def upsert_object_type_async(
        self,
        object_type_name: str,
        payload: dict[str, Any],
        *,
        operator: str = "",
        operator_name: str = "",
        allow_overwrite: bool = False,
    ) -> dict[str, Any]:
        """创建或整体编辑对象类型（元数据 + 属性集 + 映射表映射原子提交）。

        - 新建：allow_overwrite 可为 False（无行即插入）；同名软删行走复活并重置状态；
        - 编辑既有活跃对象类型：allow_overwrite 必须为 True；
        - certified 门禁：主键组合/标题键冻结、既有属性禁删禁改名；
        - 破坏性变更（映射变更、主键组合变化）需 payload.confirm_destructive；
        - 「一源一对象类型」：service 先查占用给友好报错，映射表唯一索引 DB 兜底。
        """
        name = validator.validate_api_name(object_type_name, "对象类型API名")
        meta_payload = validator.validate_object_type_payload(payload)
        # 本体存在性校验（本体不绑组，挂靠只做归属导航，无组锚点校验）
        await self._resolve_object_type_ontology(meta_payload.get("ontology_id"))
        properties = validator.validate_property_items(payload.get("properties"))
        existing = await self._dao.get_object_type_row_async(name, include_deleted=True)
        existing_active = existing is not None and int(existing.get("delete_flag") or 0) == 0
        if existing is not None and not existing_active:
            meta_payload.update(_REVIVED_OBJECT_RESET)
        allow_property_delete = True
        if existing is not None and existing_active:
            if not allow_overwrite:
                raise OntologyAlreadyExistsError(f"对象类型已存在: {name}，请更换对象类型名或编辑现有对象类型")
            allow_property_delete = await self._prepare_object_type_edit(existing, properties, payload)
        await self._validate_enum_refs(properties)
        saved = await self._dao.upsert_object_type_full_async(
            name,
            meta_payload,
            properties,
            allow_property_delete=allow_property_delete,
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        if saved is None:
            raise OntologyAlreadyExistsError(f"对象类型已存在: {name}")
        detail = await self.get_object_type_async(name)
        if detail is None:  # 理论不可达（upsert 刚成功）：退回保存视图，避免写成功却报错
            logger.warning("对象类型保存后读取详情失败，返回保存视图: %s", name)
            return saved
        return detail

    async def _resolve_object_type_ontology(self, ontology_id: Any) -> dict[str, Any] | None:
        """解析对象类型挂靠的本体行（本体不存在直接拒绝）。"""
        if ontology_id is None:
            return None
        ontology_row = await self._dao.get_ontology_row_by_id_async(int(ontology_id))
        if ontology_row is None:
            raise OntologyInvalidError(f"本体不存在: ontology_id={ontology_id}")
        return ontology_row

    async def _prepare_object_type_edit(
        self,
        existing: dict[str, Any],
        properties: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> bool:
        """活跃对象类型编辑前置：废弃终态拒绝、编辑门禁，返回(排除对象类型ID, 是否允许删属性)。"""
        if _text(existing.get("status")) == "deprecated":
            raise OntologyStateInvalidError(
                f"对象类型已废弃且不可逆，禁止编辑: {_text(existing.get('object_type_name'))}（如需继续请基于替代对象类型重建）"
            )
        await self._enforce_edit_guards(existing, properties, payload)
        return _text(existing.get("status")) != "certified"

    async def _enforce_edit_guards(
        self,
        existing: dict[str, Any],
        properties: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> None:
        """编辑既有对象类型时的冻结与破坏性变更门禁。"""
        status = _text(existing.get("status"))
        old_properties = await self._dao.list_properties_async(int(existing["id"]))
        old_identifiers = sorted(item["property_name"] for item in old_properties if item["is_identifier"])
        new_identifiers = sorted(item["property_name"] for item in properties if item["is_identifier"])
        old_titles = sorted(item["property_name"] for item in old_properties if item["is_title"])
        new_titles = sorted(item["property_name"] for item in properties if item["is_title"])
        if status == "certified":
            self._enforce_certified_freeze(
                existing, old_properties, old_identifiers, new_identifiers, old_titles, new_titles, properties
            )
        else:
            await self._enforce_relation_column_guard(
                existing, old_properties, properties, old_identifiers, new_identifiers
            )
            if old_identifiers != new_identifiers or old_titles != new_titles:
                self._require_destructive_confirm(payload, "主键组合或标题键变更")

    async def _enforce_relation_column_guard(
        self,
        existing: dict[str, Any],
        old_properties: list[dict[str, Any]],
        new_properties: list[dict[str, Any]],
        old_identifiers: list[str],
        new_identifiers: list[str],
    ) -> None:
        """属性变更门禁：不允许让既有关系类型关联列悬空（删属性/改名/主键组合变化）。"""
        object_type_id = int(existing["id"])
        removed_names = {item["property_name"] for item in old_properties} - {
            item["property_name"] for item in new_properties
        }
        identifier_changed = old_identifiers != new_identifiers
        new_identifier_set = set(new_identifiers)
        relation_types = await self._dao.list_relations_for_object_type_async(object_type_id)
        for relation in relation_types:
            self._require_relation_columns_valid(
                relation, object_type_id, removed_names, identifier_changed, new_identifier_set
            )

    def _require_relation_columns_valid(
        self,
        relation: dict[str, Any],
        object_type_id: int,
        removed_names: set[str],
        identifier_changed: bool,
        new_identifier_set: set[str],
    ) -> None:
        """单条关系类型的关联列检查：被移除属性或主键组合变化导致悬空即拒绝。"""
        relation_type_name = _text(relation.get("relation_type_name"))
        problems: list[str] = []
        if int(relation.get("from_object_type_id") or 0) == object_type_id:
            problems.extend(self._relation_column_problems(relation.get("from_columns") or [], removed_names))
        if int(relation.get("to_object_type_id") or 0) == object_type_id:
            problems.extend(
                self._relation_column_problems(
                    relation.get("to_columns") or [], removed_names, identifier_changed, new_identifier_set
                )
            )
        if problems:
            raise OntologyInvalidError(
                f"属性变更会破坏关系类型 {relation_type_name} 的关联列：{'；'.join(problems)}，请先调整或删除该关系类型后重试"
            )

    @staticmethod
    def _relation_column_problems(
        columns: list[str],
        removed_names: set[str],
        identifier_changed: bool = False,
        new_identifier_set: set[str] | None = None,
    ) -> list[str]:
        """单侧关联列问题：引用被移除属性，或目标侧列不再是新主键组合中的标识属性。"""
        dangling = sorted({column for column in columns if column in removed_names})
        if dangling:
            return [f"引用了被移除的属性 {', '.join(dangling)}"]
        if not identifier_changed or new_identifier_set is None:
            return []
        invalid = sorted({column for column in columns if column not in new_identifier_set})
        if invalid:
            return [f"不再是标识属性（主键组合变更）: {', '.join(invalid)}"]
        return []

    def _enforce_certified_freeze(
        self,
        existing: dict[str, Any],
        old_properties: list[dict[str, Any]],
        old_identifiers: list[str],
        new_identifiers: list[str],
        old_titles: list[str],
        new_titles: list[str],
        new_properties: list[dict[str, Any]],
    ) -> None:
        """certified 冻结：主键/标题键不可变，既有属性禁删（API 名由 URL 定位天然冻结）。"""
        if old_identifiers != new_identifiers or old_titles != new_titles:
            raise OntologyStateInvalidError("certified 对象类型的主键组合与标题键冻结，请先降级为 experimental 再变更")
        old_names = {item["property_name"] for item in old_properties}
        new_names = {item["property_name"] for item in new_properties}
        removed = old_names - new_names
        if removed:
            raise OntologyStateInvalidError(
                f"certified 对象类型禁止删除属性: {', '.join(sorted(removed))}，请先降级为 experimental"
            )

    @_transactional
    async def delete_object_type_async(
        self, object_type_name: str, operator: str = "", operator_name: str = ""
    ) -> None:
        """删除对象类型（软删）：certified 拒绝；同时软删其映射、属性与参与的关系类型。"""
        existing = await self._dao.get_object_type_row_async(object_type_name)
        if existing is None:
            raise OntologyNotFoundError(f"对象类型不存在: {object_type_name}")
        if _text(existing.get("status")) == "certified":
            raise OntologyStateInvalidError("certified 对象类型禁止删除，请先降级或标记 deprecated")
        relation_types = await self._dao.list_relations_for_object_type_async(int(existing["id"]))
        for relation in relation_types:
            await self._dao.soft_delete_relation_async(
                _text(relation.get("relation_type_name")), operator, operator_name
            )
        await self._dao.replace_properties_async(int(existing["id"]), [], allow_delete=True)
        await self._dao.soft_delete_object_type_async(object_type_name, operator, operator_name)

    # ---------- 对象类型状态机 ----------

    @_transactional
    async def set_object_type_status_async(
        self,
        object_type_name: str,
        target_status: str,
        payload: dict[str, Any],
        *,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """对象类型状态迁移（升级/降级/废弃），关系类型状态按对象类型级联（降级自动，升级手动）。"""
        target = _text(target_status)
        existing = await self._dao.get_object_type_row_async(object_type_name)
        if existing is None:
            raise OntologyNotFoundError(f"对象类型不存在: {object_type_name}")
        current = _text(existing.get("status"))
        if target not in validator.OBJECT_STATUSES:
            raise OntologyStateInvalidError(f"目标状态必须是 {'/'.join(validator.OBJECT_STATUSES)} 之一")
        if current == "deprecated":
            raise OntologyStateInvalidError("deprecated 为终态且不可逆")
        if target == current:
            return existing
        if target == "deprecated":
            return await self._deprecate_object_type(existing, payload, operator=operator, operator_name=operator_name)
        if target == "certified":
            await self._require_certifiable(object_type_name)
        await self._dao.upsert_object_type_async(
            object_type_name, {"status": target}, allow_overwrite=True, update_by=operator, update_by_name=operator_name
        )
        await self._cascade_relation_statuses(object_type_name, operator, operator_name)
        refreshed = await self._dao.get_object_type_row_async(object_type_name)
        return refreshed or existing

    @_transactional
    async def set_object_type_ontology_async(
        self,
        object_type_name: str,
        ontology_id: int | None,
        *,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """对象类型本体归属变更（拖拽换本体窄写路径；ontology_id=None 表示移出本体）。

        归属变更不动对象类型名/属性/映射，无破坏性；deprecated 对象类型也允许挪归属。
        本体不绑数据源组，归属迁移不涉及映射组校验（映射可跨组）。
        """
        existing = await self._dao.get_object_type_row_async(object_type_name)
        if existing is None:
            raise OntologyNotFoundError(f"对象类型不存在: {object_type_name}")
        normalized_ontology_id: int | None = None
        if ontology_id is not None:
            ontology = await self._dao.get_ontology_row_by_id_async(int(ontology_id))
            if ontology is None:
                raise OntologyNotFoundError(f"目标本体不存在: ontology_id={ontology_id}")
            normalized_ontology_id = int(ontology["id"])
        if existing.get("ontology_id") == normalized_ontology_id:
            return existing
        await self._dao.upsert_object_type_async(
            object_type_name,
            {"ontology_id": normalized_ontology_id},
            allow_overwrite=True,
            update_by=operator,
            update_by_name=operator_name,
        )
        refreshed = await self._dao.get_object_type_row_async(object_type_name)
        return refreshed or existing

    async def _deprecate_object_type(
        self, existing: dict[str, Any], payload: dict[str, Any], *, operator: str, operator_name: str
    ) -> dict[str, Any]:
        """废弃为终态：reason 必填，successor 必须存在且非自身；附期限可选。"""
        reason = _text(payload.get("deprecation_reason"))
        if not reason:
            raise OntologyStateInvalidError("废弃对象类型必须填写废弃原因（deprecation_reason）")
        successor = _text(payload.get("successor_object_type_name"))
        if successor:
            if successor == _text(existing.get("object_type_name")):
                raise OntologyStateInvalidError("替代对象类型不能是对象类型自身")
            active_names = await self._dao.list_object_type_names_async()
            if successor not in active_names:
                raise OntologyNotFoundError(f"替代对象类型不存在: {successor}")
        deprecated_until = _parse_deprecated_until(payload.get("deprecated_until"))
        await self._dao.upsert_object_type_async(
            _text(existing.get("object_type_name")),
            {
                "status": "deprecated",
                "deprecation_reason": reason[:512],
                "successor_object_type_name": successor,
                **({"deprecated_until": deprecated_until} if deprecated_until else {}),
            },
            allow_overwrite=True,
            update_by=operator,
            update_by_name=operator_name,
        )
        await self._cascade_relation_statuses(_text(existing.get("object_type_name")), operator, operator_name)
        return (await self._dao.get_object_type_row_async(_text(existing.get("object_type_name")))) or existing

    async def _require_certifiable(self, object_type_name: str) -> None:
        """升 certified 前置：有标识属性 + 每一条映射都真实覆盖全部标识属性。

        本体物化根基对齐项目路线：Palantir 物化物理表，我们物化语义模型——
        映射只指向语义模型，认证对象类型必然能在语义层拿到口径；空映射或主键未落
        模型意味着语义层拿不到实体标识，认证必然产生不可解析对象类型。
        """
        detail = await self.get_object_type_async(object_type_name)
        if detail is None:
            raise OntologyNotFoundError(f"对象类型不存在: {object_type_name}")
        properties = detail.get("properties") or []
        identifier_names = {item["property_name"] for item in properties if item.get("is_identifier")}
        if not identifier_names:
            raise OntologyInvalidError("认证前对象类型必须至少定义一个标识属性（is_identifier）作为主键")
        source_binding = detail.get("source_binding")
        if not isinstance(source_binding, dict):
            raise OntologyInvalidError("认证前对象类型必须配置 Dataset 数据源绑定")
        column_mappings = source_binding.get("column_mappings") or {}
        missing = sorted(identifier_names - set(column_mappings))
        if missing:
            raise OntologyInvalidError(f"认证前 Dataset 绑定必须覆盖全部标识属性，缺少: {', '.join(missing)}")

    async def _cascade_relation_statuses(self, object_type_name: str, operator: str, operator_name: str) -> None:
        """对象类型状态变化后级联关系类型状态：仅自动降级，升级需手动确认。"""
        detail = await self._dao.get_object_type_row_async(object_type_name)
        if detail is None:
            return
        object_type_id = int(detail["id"])
        relation_types = await self._dao.list_relations_for_object_type_async(object_type_id)
        object_type_status_map = await self._dao.get_objects_by_ids_async(
            [int(r["from_object_type_id"]) for r in relation_types]
            + [int(r["to_object_type_id"]) for r in relation_types]
        )
        downgrades: dict[str, list[int]] = {}
        for relation in relation_types:
            from_status = _text(object_type_status_map.get(int(relation["from_object_type_id"]), {}).get("status"))
            to_status = _text(object_type_status_map.get(int(relation["to_object_type_id"]), {}).get("status"))
            if not from_status or not to_status:
                continue
            derived = _derived_relation_status(from_status, to_status)
            current = _text(relation.get("status"))
            if _status_rank(derived) < _status_rank(current) or derived == "deprecated":
                downgrades.setdefault(derived, []).append(int(relation["id"]))
        for derived, ids in downgrades.items():
            await self._dao.set_relations_status_async(ids, derived, operator, operator_name)

    # ---------- 关系类型 ----------

    async def list_relations_async(
        self, keyword: str = "", from_object_type_name: str = "", to_object_type_name: str = ""
    ) -> list[dict[str, Any]]:
        relation_types = await self._dao.list_relations_async(keyword)
        relation_types = await self._attach_object_type_names(relation_types)
        from_name = _text(from_object_type_name)
        to_name = _text(to_object_type_name)
        if from_name:
            relation_types = [r for r in relation_types if _text(r.get("from_object_type_name")) == from_name]
        if to_name:
            relation_types = [r for r in relation_types if _text(r.get("to_object_type_name")) == to_name]
        return relation_types

    @_transactional
    async def upsert_relation_async(
        self,
        relation_type_name: str,
        payload: dict[str, Any],
        *,
        operator: str = "",
        operator_name: str = "",
        allow_overwrite: bool = False,
    ) -> dict[str, Any]:
        """创建/编辑关系类型。from/to/关联列/基数变更视为破坏性，需显式确认。

        状态口径：新建与软删行复活一律按两侧对象类型状态派生（关系类型不得高于任一端）；
        活跃编辑只降不升（min(旧状态, 派生状态)），避免结构变更后关系类型状态虚高。
        """
        name = validator.validate_api_name(relation_type_name, "关系类型API名")
        fields = validator.validate_relation_payload(payload)
        from_object_type = await self._resolve_active_object_type(payload.get("from_object_type_name"))
        to_object_type = await self._resolve_active_object_type(payload.get("to_object_type_name"))
        self._reject_relations_on_deprecated(from_object_type, to_object_type)
        await self._validate_relation_from_columns(from_object_type, fields["from_columns"], fields["multiplicity"])
        await self._validate_relation_columns(to_object_type, fields["to_columns"])
        existing = await self._dao.get_relation_row_async(name, include_deleted=True)
        existing_active = existing is not None and int(existing.get("delete_flag") or 0) == 0
        if existing is not None and existing_active:
            if not allow_overwrite:
                raise OntologyAlreadyExistsError(f"关系类型已存在: {name}，请更换关系类型名或编辑现有关系类型")
            if self._relation_structural_changed(existing, from_object_type, to_object_type, fields):
                self._require_destructive_confirm(payload, "关系类型的连接对象类型、关联列或基数变更")
        row_payload: dict[str, Any] = {
            "from_object_type_id": int(from_object_type["id"]),
            "to_object_type_id": int(to_object_type["id"]),
            "from_columns_json": json.dumps(fields["from_columns"], ensure_ascii=False),
            "to_columns_json": json.dumps(fields["to_columns"], ensure_ascii=False),
            "status": self._resolve_relation_status(existing, existing_active, from_object_type, to_object_type),
            **{key: value for key, value in fields.items() if key not in ("from_columns", "to_columns")},
        }
        saved = await self._dao.upsert_relation_async(
            name,
            row_payload,
            allow_overwrite=True,
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        if saved is None:  # 理论不可达（并发建同名关系类型由唯一键兜底）
            raise OntologyAlreadyExistsError(f"关系类型已存在: {name}")
        row = await self._dao.get_relation_row_async(name)
        if row is None:  # 理论不可达（upsert 刚成功）
            raise OntologyStateInvalidError(f"关系类型保存后读取失败: {name}")
        return (await self._attach_object_type_names([row]))[0]

    def _resolve_relation_status(
        self,
        existing: dict[str, Any] | None,
        existing_active: bool,
        from_object_type: dict[str, Any],
        to_object_type: dict[str, Any],
    ) -> str:
        """关系类型状态：新建/复活取两侧较低者；活跃编辑只降不升。"""
        derived = _derived_relation_status(_text(from_object_type.get("status")), _text(to_object_type.get("status")))
        if existing is None or not existing_active:
            return derived
        old_status = _text(existing.get("status")) or "draft"
        return old_status if _status_rank(old_status) <= _status_rank(derived) else derived

    def _relation_structural_changed(
        self,
        existing: dict[str, Any],
        from_object_type: dict[str, Any],
        to_object_type: dict[str, Any],
        fields: dict[str, Any],
    ) -> bool:
        """关系类型结构性字段（连接对象类型/关联列/基数）是否发生变化（破坏性确认触发条件）。"""
        return (
            int(existing.get("from_object_type_id") or 0) != int(from_object_type["id"])
            or int(existing.get("to_object_type_id") or 0) != int(to_object_type["id"])
            or list(existing.get("from_columns") or []) != fields["from_columns"]
            or list(existing.get("to_columns") or []) != fields["to_columns"]
            or _text(existing.get("multiplicity")) != fields["multiplicity"]
        )

    @_transactional
    async def delete_relation_async(self, relation_type_name: str, operator: str = "", operator_name: str = "") -> None:
        existing = await self._dao.get_relation_row_async(relation_type_name)
        if existing is None:
            raise OntologyNotFoundError(f"关系类型不存在: {relation_type_name}")
        await self._dao.soft_delete_relation_async(relation_type_name, operator, operator_name)

    @_transactional
    async def set_relation_status_async(
        self,
        relation_type_name: str,
        target_status: str,
        *,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """关系类型状态手动迁移（升级/降级），补齐「升级需手动」的操作入口。

        规则：
        - 目标状态只允许 draft/experimental/certified（deprecated 仅随对象类型级联）；
        - 上限 = 两侧对象类型状态的较低者（关系类型不得高于任一端，先升对象类型再升关系类型）；
        - 任一端对象类型已废弃时拒绝手动迁移（关系类型状态随对象类型级联，语义只可能是废弃）。
        """
        target = _text(target_status)
        existing = await self._dao.get_relation_row_async(relation_type_name)
        if existing is None:
            raise OntologyNotFoundError(f"关系类型不存在: {relation_type_name}")
        current = _text(existing.get("status"))
        if target not in RELATION_MANUAL_STATUSES:
            raise OntologyStateInvalidError(
                f"目标状态必须是 {'/'.join(RELATION_MANUAL_STATUSES)} 之一（deprecated 仅随对象类型级联，不可手动迁移）"
            )
        if current == "deprecated":
            raise OntologyStateInvalidError("deprecated 为终态且不可逆")
        if target == current:
            return existing
        object_type_map = await self._dao.get_objects_by_ids_async(
            [int(existing["from_object_type_id"]), int(existing["to_object_type_id"])]
        )
        from_status = _text(object_type_map.get(int(existing["from_object_type_id"]), {}).get("status"))
        to_status = _text(object_type_map.get(int(existing["to_object_type_id"]), {}).get("status"))
        if from_status == "deprecated" or to_status == "deprecated":
            raise OntologyStateInvalidError("连接对象类型已废弃，关系类型状态随对象类型级联，不可手动迁移")
        if not from_status or not to_status:
            raise OntologyStateInvalidError("关系类型连接的对象类型不存在或已被删除，无法迁移关系类型状态")
        bound = _derived_relation_status(from_status, to_status)
        if _status_rank(target) > _status_rank(bound):
            raise OntologyStateInvalidError(
                f"关系类型状态不得高于两侧对象类型的较低者（当前上限: {bound}），请先升级对象类型状态"
            )
        await self._dao.set_relations_status_async([int(existing["id"])], target, operator, operator_name)
        return await self._dao.get_relation_row_async(relation_type_name) or existing

    # ---------- 内部校验 ----------

    def _require_destructive_confirm(self, payload: dict[str, Any], action: str) -> None:
        if not payload.get("confirm_destructive"):
            raise OntologyStateInvalidError(f"{action}属于破坏性变更，需显式确认（confirm_destructive=true）")

    async def _resolve_active_object_type(self, object_type_name: Any) -> dict[str, Any]:
        name = validator.validate_api_name(object_type_name, "关系类型连接对象类型API名")
        detail = await self._dao.get_object_type_row_async(name)
        if detail is None:
            raise OntologyNotFoundError(f"关系类型连接对象类型不存在: {name}")
        return detail

    def _reject_relations_on_deprecated(self, from_object_type: dict[str, Any], to_object_type: dict[str, Any]) -> None:
        for obj in (from_object_type, to_object_type):
            if _text(obj.get("status")) == "deprecated":
                raise OntologyStateInvalidError(f"废弃对象类型禁止新建或变更关系类型: {obj.get('object_type_name')}")

    async def _validate_relation_columns(self, to_object_type: dict[str, Any], to_columns: list[str]) -> None:
        """目标侧关联列必须引用对方标识属性（FK→PK 红线）。"""
        properties = await self._dao.list_properties_async(int(to_object_type["id"]))
        identifier_names = {item["property_name"] for item in properties if item["is_identifier"]}
        invalid = [column for column in to_columns if column not in identifier_names]
        if invalid:
            raise OntologyInvalidError(
                f"目标侧关联列必须引用对象类型 {to_object_type.get('object_type_name')} 的标识属性，非法列: {', '.join(invalid)}"
            )

    async def _validate_relation_from_columns(
        self, from_object_type: dict[str, Any], from_columns: list[str], multiplicity: str
    ) -> None:
        """起始侧关联列校验：必须是 from 对象类型已定义属性；one_to_one 还必须是标识属性。

        many_to_one 的 from 侧是「多」端，FK 列不要求唯一；one_to_one 要求两侧都唯一，
        from 侧列若不引用 from 对象类型标识属性（唯一列）就无法在元数据层保证一对一，
        因此按 to 侧同口径收紧（数据侧唯一性仍由标识属性语义保证）。
        """
        properties = await self._dao.list_properties_async(int(from_object_type["id"]))
        property_names = {item["property_name"] for item in properties}
        invalid = [column for column in from_columns if column not in property_names]
        if invalid:
            raise OntologyInvalidError(
                f"起始侧关联列必须引用对象类型 {from_object_type.get('object_type_name')} 已定义的属性，非法列: {', '.join(invalid)}"
            )
        if multiplicity != "one_to_one":
            return
        identifier_names = {item["property_name"] for item in properties if item["is_identifier"]}
        non_identifier = [column for column in from_columns if column not in identifier_names]
        if non_identifier:
            raise OntologyInvalidError(
                f"one_to_one 关系类型起始侧关联列必须引用起始对象类型 {from_object_type.get('object_type_name')} 的标识属性"
                f"（保证一对一唯一性），非法列: {', '.join(non_identifier)}"
            )

    async def _validate_enum_refs(self, properties: list[dict[str, Any]]) -> None:
        """value_type=enum 的属性：全局枚举引用必须真实存在（格式非法直接拒绝）。"""
        for item in properties:
            enum_ref = _text(item.get("enum_ref"))
            if not enum_ref:
                continue
            try:
                domain, name = _split_enum_ref(enum_ref)
                found = await global_enum_service.get_enum_async(name, domain)
            except MetaGlobalEnumNotFoundError:
                found = None
            if found is None:
                raise OntologyInvalidError(f"属性 {item['property_name']} 引用的全局枚举不存在: {enum_ref}")

    async def _attach_object_type_names(self, relation_types: list[dict[str, Any]]) -> list[dict[str, Any]]:
        object_type_map = await self._dao.get_objects_by_ids_async(
            [int(r["from_object_type_id"]) for r in relation_types]
            + [int(r["to_object_type_id"]) for r in relation_types]
        )
        for relation in relation_types:
            from_obj = object_type_map.get(int(relation["from_object_type_id"])) or {}
            to_obj = object_type_map.get(int(relation["to_object_type_id"])) or {}
            relation["from_object_type_name"] = _text(from_obj.get("object_type_name"))
            relation["to_object_type_name"] = _text(to_obj.get("object_type_name"))
        return relation_types


def _parse_deprecated_until(value: Any) -> datetime | None:
    """解析废弃期限为 datetime（DB DateTime 列不接受字符串，避免 StatementError）。

    接受 "YYYY-MM-DD HH:MM:SS" 与 "YYYY-MM-DD"（后者补 00:00:00）；格式非法抛
    OntologyInvalidError，不让非法值落到数据库层。
    """
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    try:
        return datetime.strptime(f"{text} 00:00:00", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise OntologyInvalidError("废弃期限格式非法，支持 YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DD") from None
