"""本体服务：CRUD 编排、状态机与治理门禁（对齐 Palantir Ontology Manager）。

分层边界：DAO 只做持久化；本层负责业务规则——确定性校验器（ontology_validator）
红线检查、certified 冻结门禁、破坏性变更显式确认、状态机与关系级联、绑定
「一源一对象」唯一归属检查与全局枚举引用存在性校验。
"""

from __future__ import annotations

import json
from typing import Any

from utils.logger import get_logger

from ...daos.meta_global_enum_dao import _split_enum_ref
from ...daos.ontology_dao import OntologyDao
from ...daos.semantic_config_dao import SemanticConfigDao
from ..meta.global_enum_service import MetaGlobalEnumNotFoundError, global_enum_service
from . import ontology_validator as validator
from .errors import (
    OntologyInvalidError,
    OntologyNotFoundError,
    OntologyStateInvalidError,
)

logger = get_logger(__name__)

# 绑定存在性校验用（模块级单例，测试经 monkeypatch 打桩，同 global_enum_service 模式）
semantic_config_dao = SemanticConfigDao()

STATUS_ORDER = {"draft": 0, "experimental": 1, "certified": 2}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status_rank(status: str) -> int:
    return STATUS_ORDER.get(status, 0)


def _derived_relation_status(from_status: str, to_status: str) -> str:
    """关系状态 = 两侧对象状态的较低者；任一侧废弃则关系废弃。"""
    if from_status == "deprecated" or to_status == "deprecated":
        return "deprecated"
    order = min(_status_rank(from_status), _status_rank(to_status))
    return {0: "draft", 1: "experimental", 2: "certified"}[order]


class OntologyService:
    """本体对象/属性/关系业务规则与治理门禁。"""

    def __init__(self, dao: OntologyDao | None = None) -> None:
        # 允许注入 DAO（测试用），默认单例 DAO
        self._dao = dao or OntologyDao()

    # ---------- 对象查询 ----------

    async def list_objects_async(self, keyword: str = "", status: str = "", domain: str = "") -> list[dict[str, Any]]:
        objects = await self._dao.list_objects_async(keyword, status, domain)
        counts = await self._dao.count_mappings_by_object_ids_async([int(item["id"]) for item in objects])
        for item in objects:
            item["bindings_count"] = counts.get(int(item["id"]), 0)
        return objects

    async def get_object_async(self, object_name: str) -> dict[str, Any] | None:
        """对象详情（含属性集与语义模型绑定；软删行视为不存在）。"""
        data = await self._dao.get_object_row_async(object_name)
        if data is None:
            return None
        data["properties"] = await self._dao.list_properties_async(int(data["id"]))
        data["bindings"] = await self._dao.list_object_mappings_async(int(data["id"]))
        return data

    # ---------- 对象创建/编辑 ----------

    async def upsert_object_async(
        self,
        object_name: str,
        payload: dict[str, Any],
        *,
        operator: str = "",
        operator_name: str = "",
        allow_overwrite: bool = False,
    ) -> dict[str, Any]:
        """创建或整体编辑对象（元数据 + 属性集 + 映射表绑定原子提交）。

        - 新建：allow_overwrite 可为 False（无行即插入）；
        - 编辑既有活跃对象：allow_overwrite 必须为 True；
        - certified 门禁：主键组合/标题键冻结、既有属性禁删禁改名；
        - 破坏性变更（绑定变更、主键组合变化）需 payload.confirm_destructive；
        - 「一源一对象」：service 先查占用给友好报错，映射表唯一索引 DB 兜底。
        """
        name = validator.validate_api_name(object_name, "对象API名")
        meta_payload = validator.validate_object_payload(payload)
        properties = validator.validate_property_items(payload.get("properties"))
        property_names = {item["property_name"] for item in properties}
        bindings = validator.validate_bindings(payload.get("bindings"), property_names, name)
        existing = await self._dao.get_object_row_async(name, include_deleted=True)
        allow_property_delete = True
        saved_status = "draft"
        exclude_object_id: int | None = None
        existing_active = existing is not None and int(existing.get("delete_flag") or 0) == 0
        if existing is not None and existing_active:
            if not allow_overwrite:
                raise OntologyStateInvalidError(f"对象已存在: {name}，编辑请携带 allow_overwrite")
            if _text(existing.get("status")) == "deprecated":
                raise OntologyStateInvalidError(
                    f"对象已废弃且不可逆，禁止编辑: {name}（如需继续请基于替代对象重建）"
                )
            await self._enforce_edit_guards(existing, properties, bindings, payload)
            exclude_object_id = int(existing["id"])
            allow_property_delete = _text(existing.get("status")) != "certified"
            saved_status = _text(existing.get("status"))
        await self._validate_enum_refs(properties)
        await self._validate_binding_targets(bindings)
        await self._check_binding_ownership(bindings, exclude_object_id)
        saved = await self._dao.upsert_object_full_async(
            name,
            meta_payload,
            properties,
            bindings,
            allow_property_delete=allow_property_delete,
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        if saved is None:
            raise OntologyStateInvalidError(f"对象已存在: {name}")
        saved["status"] = saved_status
        return saved

    async def _enforce_edit_guards(
        self,
        existing: dict[str, Any],
        properties: list[dict[str, Any]],
        bindings: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> None:
        """编辑既有对象时的冻结与破坏性变更门禁。"""
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
            if not bindings:
                raise OntologyStateInvalidError(
                    "certified 对象必须保持至少一个语义模型绑定，请先降级为 experimental 再变更"
                )
        elif old_identifiers != new_identifiers or old_titles != new_titles:
            self._require_destructive_confirm(payload, "主键组合或标题键变更")
        old_bindings = await self._dao.list_object_mappings_async(int(existing["id"]))
        if json.dumps(_normalize_bindings(old_bindings), ensure_ascii=False) != json.dumps(
            _normalize_bindings(bindings), ensure_ascii=False
        ):
            self._require_destructive_confirm(payload, "绑定变更")

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
            raise OntologyStateInvalidError("certified 对象的主键组合与标题键冻结，请先降级为 experimental 再变更")
        old_names = {item["property_name"] for item in old_properties}
        new_names = {item["property_name"] for item in new_properties}
        removed = old_names - new_names
        if removed:
            raise OntologyStateInvalidError(
                f"certified 对象禁止删除属性: {', '.join(sorted(removed))}，请先降级为 experimental"
            )

    async def delete_object_async(self, object_name: str, operator: str = "", operator_name: str = "") -> None:
        """删除对象（软删）：certified 拒绝；同时硬删其映射、软删属性与参与的关系。"""
        existing = await self._dao.get_object_row_async(object_name)
        if existing is None:
            raise OntologyNotFoundError(f"对象不存在: {object_name}")
        if _text(existing.get("status")) == "certified":
            raise OntologyStateInvalidError("certified 对象禁止删除，请先降级或标记 deprecated")
        relations = await self._dao.list_relations_for_object_async(int(existing["id"]))
        for relation in relations:
            await self._dao.soft_delete_relation_async(_text(relation.get("relation_name")), operator, operator_name)
        await self._dao.replace_properties_async(int(existing["id"]), [], allow_delete=True)
        await self._dao.delete_object_mappings_async(int(existing["id"]))
        await self._dao.soft_delete_object_async(object_name, operator, operator_name)

    # ---------- 对象状态机 ----------

    async def set_object_status_async(
        self,
        object_name: str,
        target_status: str,
        payload: dict[str, Any],
        *,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """对象状态迁移（升级/降级/废弃），关系状态按对象级联（降级自动，升级手动）。"""
        target = _text(target_status)
        existing = await self._dao.get_object_row_async(object_name)
        if existing is None:
            raise OntologyNotFoundError(f"对象不存在: {object_name}")
        current = _text(existing.get("status"))
        if target not in validator.OBJECT_STATUSES:
            raise OntologyStateInvalidError(f"目标状态必须是 {'/'.join(validator.OBJECT_STATUSES)} 之一")
        if current == "deprecated":
            raise OntologyStateInvalidError("deprecated 为终态且不可逆")
        if target == current:
            return existing
        if target == "deprecated":
            return await self._deprecate_object(existing, payload, operator=operator, operator_name=operator_name)
        if target == "certified":
            await self._require_certifiable(object_name)
        await self._dao.upsert_object_async(
            object_name, {"status": target}, allow_overwrite=True, update_by=operator, update_by_name=operator_name
        )
        await self._cascade_relation_statuses(object_name, operator, operator_name)
        refreshed = await self._dao.get_object_row_async(object_name)
        return refreshed or existing

    async def _deprecate_object(
        self, existing: dict[str, Any], payload: dict[str, Any], *, operator: str, operator_name: str
    ) -> dict[str, Any]:
        """废弃为终态：reason 必填，successor 必须存在且非自身；附期限可选。"""
        reason = _text(payload.get("deprecation_reason"))
        if not reason:
            raise OntologyStateInvalidError("废弃对象必须填写废弃原因（deprecation_reason）")
        successor = _text(payload.get("successor_object_name"))
        if successor:
            if successor == _text(existing.get("object_name")):
                raise OntologyStateInvalidError("替代对象不能是对象自身")
            active_names = await self._dao.list_object_names_async()
            if successor not in active_names:
                raise OntologyNotFoundError(f"替代对象不存在: {successor}")
        deprecated_until = _text(payload.get("deprecated_until"))
        await self._dao.upsert_object_async(
            _text(existing.get("object_name")),
            {
                "status": "deprecated",
                "deprecation_reason": reason[:512],
                "successor_object_name": successor,
                **({"deprecated_until": deprecated_until} if deprecated_until else {}),
            },
            allow_overwrite=True,
            update_by=operator,
            update_by_name=operator_name,
        )
        await self._cascade_relation_statuses(_text(existing.get("object_name")), operator, operator_name)
        return (await self._dao.get_object_row_async(_text(existing.get("object_name")))) or existing

    async def _require_certifiable(self, object_name: str) -> None:
        """升 certified 前置：必须有属性（含主键）且至少一个绑定（绑定统一为语义模型轨）。

        本体物化根基对齐项目路线：Palantir 物化物理表，我们物化语义模型——
        绑定只指向语义模型，认证对象必然能在语义层拿到口径。
        """
        detail = await self.get_object_async(object_name)
        if detail is None:
            raise OntologyNotFoundError(f"对象不存在: {object_name}")
        properties = detail.get("properties") or []
        if not any(item.get("is_identifier") for item in properties):
            raise OntologyInvalidError("认证前对象必须至少定义一个标识属性（is_identifier）作为主键")
        if not detail.get("bindings"):
            raise OntologyInvalidError("认证前对象必须至少配置一个语义模型绑定（semantic_config_id + table_alias）")

    async def _cascade_relation_statuses(self, object_name: str, operator: str, operator_name: str) -> None:
        """对象状态变化后级联关系状态：仅自动降级，升级需手动确认。"""
        detail = await self._dao.get_object_row_async(object_name)
        if detail is None:
            return
        object_id = int(detail["id"])
        relations = await self._dao.list_relations_for_object_async(object_id)
        object_status_map = await self._dao.get_objects_by_ids_async(
            [int(r["from_object_id"]) for r in relations] + [int(r["to_object_id"]) for r in relations]
        )
        downgrades: dict[str, list[int]] = {}
        for relation in relations:
            from_status = _text(object_status_map.get(int(relation["from_object_id"]), {}).get("status"))
            to_status = _text(object_status_map.get(int(relation["to_object_id"]), {}).get("status"))
            if not from_status or not to_status:
                continue
            derived = _derived_relation_status(from_status, to_status)
            current = _text(relation.get("status"))
            if _status_rank(derived) < _status_rank(current) or derived == "deprecated":
                downgrades.setdefault(derived, []).append(int(relation["id"]))
        for derived, ids in downgrades.items():
            await self._dao.set_relations_status_async(ids, derived, operator, operator_name)

    # ---------- 关系 ----------

    async def list_relations_async(
        self, keyword: str = "", from_object_name: str = "", to_object_name: str = ""
    ) -> list[dict[str, Any]]:
        relations = await self._dao.list_relations_async(keyword)
        relations = await self._attach_object_names(relations)
        from_name = _text(from_object_name)
        to_name = _text(to_object_name)
        if from_name:
            relations = [r for r in relations if _text(r.get("from_object_name")) == from_name]
        if to_name:
            relations = [r for r in relations if _text(r.get("to_object_name")) == to_name]
        return relations

    async def upsert_relation_async(
        self,
        relation_name: str,
        payload: dict[str, Any],
        *,
        operator: str = "",
        operator_name: str = "",
        allow_overwrite: bool = False,
    ) -> dict[str, Any]:
        """创建/编辑关系。from/to/关联列/基数变更视为破坏性，需显式确认。"""
        name = validator.validate_api_name(relation_name, "关系API名")
        fields = validator.validate_relation_payload(payload)
        from_object = await self._resolve_active_object(payload.get("from_object_name"))
        to_object = await self._resolve_active_object(payload.get("to_object_name"))
        self._reject_relations_on_deprecated(from_object, to_object)
        await self._validate_relation_from_columns(from_object, fields["from_columns"])
        await self._validate_relation_columns(to_object, fields["to_columns"])
        existing = await self._dao.get_relation_row_async(name, include_deleted=True)
        if existing is not None and int(existing.get("delete_flag") or 0) == 0:
            if not allow_overwrite:
                raise OntologyStateInvalidError(f"关系已存在: {name}，编辑请携带 allow_overwrite")
            if self._relation_structural_changed(existing, from_object, to_object, fields):
                self._require_destructive_confirm(payload, "关系的连接对象、关联列或基数变更")
        from_status = _text(from_object.get("status"))
        to_status = _text(to_object.get("status"))
        row_payload: dict[str, Any] = {
            "from_object_id": int(from_object["id"]),
            "to_object_id": int(to_object["id"]),
            "from_columns_json": json.dumps(fields["from_columns"], ensure_ascii=False),
            "to_columns_json": json.dumps(fields["to_columns"], ensure_ascii=False),
            "status": _derived_relation_status(from_status, to_status)
            if existing is None
            else _text(existing.get("status")) or "draft",
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
        if saved is None:
            raise OntologyStateInvalidError(f"关系已存在: {name}")
        return saved

    def _relation_structural_changed(
        self, existing: dict[str, Any], from_object: dict[str, Any], to_object: dict[str, Any], fields: dict[str, Any]
    ) -> bool:
        """关系结构性字段（连接对象/关联列/基数）是否发生变化（破坏性确认触发条件）。"""
        return (
            int(existing.get("from_object_id") or 0) != int(from_object["id"])
            or int(existing.get("to_object_id") or 0) != int(to_object["id"])
            or list(existing.get("from_columns") or []) != fields["from_columns"]
            or list(existing.get("to_columns") or []) != fields["to_columns"]
            or _text(existing.get("multiplicity")) != fields["multiplicity"]
        )

    async def delete_relation_async(self, relation_name: str, operator: str = "", operator_name: str = "") -> None:
        existing = await self._dao.get_relation_row_async(relation_name)
        if existing is None:
            raise OntologyNotFoundError(f"关系不存在: {relation_name}")
        await self._dao.soft_delete_relation_async(relation_name, operator, operator_name)

    # ---------- 内部校验 ----------

    def _require_destructive_confirm(self, payload: dict[str, Any], action: str) -> None:
        if not payload.get("confirm_destructive"):
            raise OntologyStateInvalidError(f"{action}属于破坏性变更，需显式确认（confirm_destructive=true）")

    def _allow_property_delete(self, existing: dict[str, Any] | None) -> bool:
        return existing is None or _text(existing.get("status")) != "certified"

    async def _resolve_active_object(self, object_name: Any) -> dict[str, Any]:
        name = validator.validate_api_name(object_name, "关系连接对象API名")
        detail = await self._dao.get_object_row_async(name)
        if detail is None:
            raise OntologyNotFoundError(f"关系连接对象不存在: {name}")
        return detail

    def _reject_relations_on_deprecated(self, from_object: dict[str, Any], to_object: dict[str, Any]) -> None:
        for obj in (from_object, to_object):
            if _text(obj.get("status")) == "deprecated":
                raise OntologyStateInvalidError(f"废弃对象禁止新建或变更关系: {obj.get('object_name')}")

    async def _validate_relation_columns(self, to_object: dict[str, Any], to_columns: list[str]) -> None:
        """目标侧关联列必须引用对方标识属性（FK→PK 红线）。"""
        properties = await self._dao.list_properties_async(int(to_object["id"]))
        identifier_names = {item["property_name"] for item in properties if item["is_identifier"]}
        invalid = [column for column in to_columns if column not in identifier_names]
        if invalid:
            raise OntologyInvalidError(
                f"目标侧关联列必须引用对象 {to_object.get('object_name')} 的标识属性，非法列: {', '.join(invalid)}"
            )

    async def _validate_relation_from_columns(self, from_object: dict[str, Any], from_columns: list[str]) -> None:
        """起始侧关联列必须引用 from 对象已定义的属性（外键侧不要求是标识属性）。"""
        properties = await self._dao.list_properties_async(int(from_object["id"]))
        property_names = {item["property_name"] for item in properties}
        invalid = [column for column in from_columns if column not in property_names]
        if invalid:
            raise OntologyInvalidError(
                f"起始侧关联列必须引用对象 {from_object.get('object_name')} 已定义的属性，非法列: {', '.join(invalid)}"
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

    async def _check_binding_ownership(
        self, bindings: list[dict[str, Any]], exclude_object_id: int | None
    ) -> None:
        """「一源一对象」：每个绑定键全局只能归属一个对象（唯一索引兜底，预检给友好报错）。"""
        owners = await self._dao.find_mapping_owners_async(bindings, exclude_object_id=exclude_object_id)
        for (config_id, table_alias), owner_name in sorted(owners.items()):
            raise OntologyInvalidError(
                f"绑定 {config_id}:{table_alias} 已被对象 {owner_name} 占用"
                "（一个语义模型绑定只能归属一个对象），请先解除原对象绑定"
            )

    async def _validate_binding_targets(self, bindings: list[dict[str, Any]]) -> None:
        """绑定的语义模型必须真实存在（存在性校验；与「一源一对象」归属检查互补）。"""
        config_ids = {_text(binding.get("semantic_config_id")) for binding in bindings} - {""}
        if not config_ids:
            return
        existing_ids = await semantic_config_dao.filter_existing_config_ids_async(sorted(config_ids))
        missing = sorted(config_ids - existing_ids)
        if missing:
            raise OntologyInvalidError(f"绑定的语义模型不存在: {', '.join(missing)}")

    async def _attach_object_names(self, relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        object_map = await self._dao.get_objects_by_ids_async(
            [int(r["from_object_id"]) for r in relations] + [int(r["to_object_id"]) for r in relations]
        )
        for relation in relations:
            from_obj = object_map.get(int(relation["from_object_id"])) or {}
            to_obj = object_map.get(int(relation["to_object_id"])) or {}
            relation["from_object_name"] = _text(from_obj.get("object_name"))
            relation["to_object_name"] = _text(to_obj.get("object_name"))
        return relations


def _normalize_bindings(bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """绑定排序后规范化（仅保留结构键），用于编辑前后 diff 判断。"""
    normalized = []
    for binding in bindings:
        normalized.append(
            {
                "binding_type": _text(binding.get("binding_type")),
                "semantic_config_id": _text(binding.get("semantic_config_id")),
                "table_alias": _text(binding.get("table_alias")),
                "column_mappings": {
                    _text(key): _text(value) for key, value in (binding.get("column_mappings") or {}).items()
                },
            }
        )
    return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
