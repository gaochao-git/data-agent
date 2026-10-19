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

from utils.logger import get_logger

from ...daos.dao_session import transaction_scope
from ...daos.datasource_dao import DataSourceDao
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
    """本体领域/对象类型/属性/关系类型业务规则与治理门禁。"""

    def __init__(self, dao: OntologyDao | None = None) -> None:
        # 允许注入 DAO（测试用），默认单例 DAO
        self._dao = dao or OntologyDao()

    # ---------- 领域 ----------

    async def list_domains_async(self) -> list[dict[str, Any]]:
        return await self._dao.list_domains_async()

    @_transactional
    async def upsert_domain_async(
        self,
        domain_name: str,
        payload: dict[str, Any],
        *,
        operator: str = "",
        operator_name: str = "",
        allow_overwrite: bool = False,
    ) -> dict[str, Any]:
        """创建或编辑领域（一对一绑定数据源组）。

        软删行同名重建走复活（不要求 allow_overwrite）；改组需保证领域内对象类型的
        映射语义模型仍属于新组，否则拒绝（避免领域组与映射模型组漂移）。
        """
        name = validator.validate_api_name(domain_name, "领域API名", max_length=validator.DOMAIN_NAME_MAX_LENGTH)
        normalized = validator.validate_domain_payload(payload)
        new_group_id = _text(normalized.get("group_id"))
        existing = await self._dao.get_domain_row_async(name, include_deleted=True)
        existing_active = existing is not None and int(existing.get("delete_flag") or 0) == 0
        if existing_active and not allow_overwrite:
            raise OntologyAlreadyExistsError(f"领域已存在: {name}，请更换领域名或编辑现有领域")
        await self._validate_group_exists(new_group_id)
        if existing is not None and _text(existing.get("group_id")) != new_group_id:
            await self._validate_domain_group_change(existing, new_group_id)
        saved = await self._dao.upsert_domain_async(
            name,
            normalized,
            allow_overwrite=allow_overwrite,
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        if saved is None:  # 理论不可达（并发建同名领域由唯一键兜底）
            raise OntologyAlreadyExistsError(f"领域已存在: {name}")
        detail = await self._dao.get_domain_row_async(name)
        if detail is None:  # 理论不可达（upsert 刚成功）
            raise OntologyStateInvalidError(f"领域保存后读取失败: {name}")
        return detail

    @_transactional
    async def delete_domain_async(self, domain_name: str, *, operator: str = "", operator_name: str = "") -> None:
        """删除领域（软删）：不存在报 404，挂有活跃对象类型时拒绝。"""
        existing = await self._dao.get_domain_row_async(domain_name)
        if existing is None:
            raise OntologyNotFoundError(f"领域不存在: {domain_name}")
        counts = await self._dao.count_objects_by_domain_ids_async([int(existing["id"])])
        if counts.get(int(existing["id"]), 0) > 0:
            raise OntologyStateInvalidError(
                f"领域下仍有 {counts[int(existing['id'])]} 个对象类型，请先移出或删除对象类型后再删除领域"
            )
        await self._dao.soft_delete_domain_async(domain_name, update_by=operator, update_by_name=operator_name)

    async def _validate_group_exists(self, group_id: str) -> None:
        """领域绑定的数据源组必须存在。"""
        if not group_id:
            raise OntologyInvalidError("领域必须绑定数据源组")
        if not await DataSourceDao().has_datasource_async(group_id):
            raise OntologyInvalidError(f"数据源组不存在: {group_id}")

    async def _validate_domain_group_change(self, existing: dict[str, Any], new_group_id: str) -> None:
        """领域改组门禁：领域内活跃对象类型的映射语义模型必须都属于新数据源组。"""
        rows = await self._dao.list_domain_object_type_mapping_configs_async(int(existing["id"]))
        config_ids = {_text(row.get("semantic_config_id")) for row in rows} - {""}
        if not config_ids:
            return
        group_ids = await semantic_config_dao.get_config_group_ids_async(sorted(config_ids))
        offenders = sorted(
            {
                _text(row.get("object_type_name"))
                for row in rows
                if group_ids.get(_text(row.get("semantic_config_id"))) != new_group_id
            }
        )
        if offenders:
            raise OntologyStateInvalidError(
                f"领域改组失败：对象类型 {', '.join(offenders)} 的映射语义模型不属于新数据源组 {new_group_id}，"
                "请先调整对象类型映射或将这些对象类型移出领域"
            )

    # ---------- 对象类型查询 ----------

    async def list_object_types_async(
        self, keyword: str = "", status: str = "", domain_id: int | None = None
    ) -> list[dict[str, Any]]:
        object_types = await self._dao.list_object_types_async(keyword, status, domain_id)
        counts = await self._dao.count_mappings_by_object_type_ids_async([int(item["id"]) for item in object_types])
        for item in object_types:
            item["mappings_count"] = counts.get(int(item["id"]), 0)
        return object_types

    async def get_object_type_async(self, object_type_name: str) -> dict[str, Any] | None:
        """对象类型详情（含属性集与语义模型映射；软删行视为不存在）。"""
        data = await self._dao.get_object_type_row_async(object_type_name)
        if data is None:
            return None
        data["properties"] = await self._dao.list_properties_async(int(data["id"]))
        data["mappings"] = await self._dao.list_object_type_mappings_async(int(data["id"]))
        return data

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
        domain_row = await self._resolve_object_type_domain(meta_payload.get("domain_id"))
        properties = validator.validate_property_items(payload.get("properties"))
        property_names = {item["property_name"] for item in properties}
        mappings = validator.validate_mappings(payload.get("mappings"), property_names, name)
        existing = await self._dao.get_object_type_row_async(name, include_deleted=True)
        existing_active = existing is not None and int(existing.get("delete_flag") or 0) == 0
        if existing is not None and not existing_active:
            meta_payload.update(_REVIVED_OBJECT_RESET)
        self._validate_single_source_properties(properties, mappings)
        exclude_object_type_id: int | None = None
        allow_property_delete = True
        if existing is not None and existing_active:
            if not allow_overwrite:
                raise OntologyAlreadyExistsError(f"对象类型已存在: {name}，请更换对象类型名或编辑现有对象类型")
            exclude_object_type_id, allow_property_delete = await self._prepare_object_type_edit(
                existing, properties, mappings, payload
            )
        await self._validate_enum_refs(properties)
        await self._validate_mapping_targets(mappings)
        await self._validate_mapping_dimensions(mappings)
        await self._validate_mapping_group_anchor(mappings, domain_row)
        await self._check_mapping_ownership(mappings, exclude_object_type_id)
        saved = await self._dao.upsert_object_type_full_async(
            name,
            meta_payload,
            properties,
            mappings,
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

    async def _resolve_object_type_domain(self, domain_id: Any) -> dict[str, Any] | None:
        """解析对象类型挂靠的领域行（域不存在直接拒绝）。"""
        if domain_id is None:
            return None
        domain_row = await self._dao.get_domain_row_by_id_async(int(domain_id))
        if domain_row is None:
            raise OntologyInvalidError(f"领域不存在: domain_id={domain_id}")
        return domain_row

    async def _prepare_object_type_edit(
        self,
        existing: dict[str, Any],
        properties: list[dict[str, Any]],
        mappings: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> tuple[int, bool]:
        """活跃对象类型编辑前置：废弃终态拒绝、编辑门禁，返回(排除对象类型ID, 是否允许删属性)。"""
        if _text(existing.get("status")) == "deprecated":
            raise OntologyStateInvalidError(
                f"对象类型已废弃且不可逆，禁止编辑: {_text(existing.get('object_type_name'))}（如需继续请基于替代对象类型重建）"
            )
        await self._enforce_edit_guards(existing, properties, mappings, payload)
        return int(existing["id"]), _text(existing.get("status")) != "certified"

    async def _enforce_edit_guards(
        self,
        existing: dict[str, Any],
        properties: list[dict[str, Any]],
        mappings: list[dict[str, Any]],
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
            if not mappings:
                raise OntologyStateInvalidError(
                    "certified 对象类型必须保持至少一个语义模型映射，请先降级为 experimental 再变更"
                )
        else:
            await self._enforce_relation_column_guard(
                existing, old_properties, properties, old_identifiers, new_identifiers
            )
            if old_identifiers != new_identifiers or old_titles != new_titles:
                self._require_destructive_confirm(payload, "主键组合或标题键变更")
        old_mappings = await self._dao.list_object_type_mappings_async(int(existing["id"]))
        if json.dumps(_normalize_mappings(old_mappings), ensure_ascii=False) != json.dumps(
            _normalize_mappings(mappings), ensure_ascii=False
        ):
            self._require_destructive_confirm(payload, "映射变更")

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
    async def delete_object_type_async(self, object_type_name: str, operator: str = "", operator_name: str = "") -> None:
        """删除对象类型（软删）：certified 拒绝；同时软删其映射、属性与参与的关系类型。"""
        existing = await self._dao.get_object_type_row_async(object_type_name)
        if existing is None:
            raise OntologyNotFoundError(f"对象类型不存在: {object_type_name}")
        if _text(existing.get("status")) == "certified":
            raise OntologyStateInvalidError("certified 对象类型禁止删除，请先降级或标记 deprecated")
        relation_types = await self._dao.list_relations_for_object_type_async(int(existing["id"]))
        for relation in relation_types:
            await self._dao.soft_delete_relation_async(_text(relation.get("relation_type_name")), operator, operator_name)
        await self._dao.replace_properties_async(int(existing["id"]), [], allow_delete=True)
        await self._dao.delete_object_type_mappings_async(int(existing["id"]))
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
    async def set_object_type_domain_async(
        self,
        object_type_name: str,
        domain_id: int | None,
        *,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """对象类型领域归属变更（拖拽换域窄写路径；domain_id=None 表示移出领域）。

        归属变更不动对象类型名/属性/映射，无破坏性；deprecated 对象类型也允许挪归属。
        换入领域时既有映射的语义模型必须属于目标领域的数据源组（组锚点一致）。
        """
        existing = await self._dao.get_object_type_row_async(object_type_name)
        if existing is None:
            raise OntologyNotFoundError(f"对象类型不存在: {object_type_name}")
        normalized_domain_id: int | None = None
        domain: dict[str, Any] | None = None
        if domain_id is not None:
            domain = await self._dao.get_domain_row_by_id_async(int(domain_id))
            if domain is None:
                raise OntologyNotFoundError(f"目标领域不存在: domain_id={domain_id}")
            normalized_domain_id = int(domain["id"])
        if existing.get("domain_id") == normalized_domain_id:
            return existing
        if domain is not None:
            mappings = await self._dao.list_object_type_mappings_async(int(existing["id"]))
            await self._validate_mapping_group_anchor(mappings, domain)
        await self._dao.upsert_object_type_async(
            object_type_name,
            {"domain_id": normalized_domain_id},
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
        mappings = detail.get("mappings") or []
        if not mappings:
            raise OntologyInvalidError("认证前对象类型必须至少配置一个语义模型映射（semantic_config_id + 字段映射）")
        for mapping in mappings:
            config_id = _text(mapping.get("semantic_config_id"))
            column_mappings = mapping.get("column_mappings") or {}
            if not column_mappings:
                raise OntologyInvalidError(
                    f"认证前映射 {config_id} 必须填写 column_mappings（属性→模型维度），空映射无法在语义层定位实体"
                )
            missing = sorted(identifier_names - set(column_mappings))
            if missing:
                raise OntologyInvalidError(
                    f"认证前映射 {config_id} 必须覆盖全部标识属性，缺少: {', '.join(missing)}"
                    "（标识属性需映射到模型维度，否则认证对象类型无法唯一定位）"
                )

    async def _cascade_relation_statuses(self, object_type_name: str, operator: str, operator_name: str) -> None:
        """对象类型状态变化后级联关系类型状态：仅自动降级，升级需手动确认。"""
        detail = await self._dao.get_object_type_row_async(object_type_name)
        if detail is None:
            return
        object_type_id = int(detail["id"])
        relation_types = await self._dao.list_relations_for_object_type_async(object_type_id)
        object_type_status_map = await self._dao.get_objects_by_ids_async(
            [int(r["from_object_type_id"]) for r in relation_types] + [int(r["to_object_type_id"]) for r in relation_types]
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
        self, existing: dict[str, Any], from_object_type: dict[str, Any], to_object_type: dict[str, Any], fields: dict[str, Any]
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
            raise OntologyStateInvalidError(f"关系类型状态不得高于两侧对象类型的较低者（当前上限: {bound}），请先升级对象类型状态")
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

    @staticmethod
    def _validate_single_source_properties(properties: list[dict[str, Any]], mappings: list[dict[str, Any]]) -> None:
        """多源对象类型属性单源规则（对齐 Palantir MDO 属性单源铁律）。

        一个对象类型可绑定多个语义模型（column-wise 多源）；除标识属性（主键必须在每个
        数据源存在，用于跨源对齐）外，同一属性只能出现在一个映射里，否则「该属性由
        哪个源背书」不可判定，目录与 join 口径都会出现歧义。
        """
        identifiers = {item["property_name"] for item in properties if item.get("is_identifier")}
        sources: dict[str, set[str]] = {}
        for mapping in mappings:
            config_id = _text(mapping.get("semantic_config_id"))
            for property_name in mapping.get("column_mappings") or {}:
                sources.setdefault(_text(property_name), set()).add(config_id)
        conflicts = sorted(
            (property_name, sorted(config_ids))
            for property_name, config_ids in sources.items()
            if property_name not in identifiers and len(config_ids) > 1
        )
        if conflicts:
            details = "；".join(
                f"{property_name} 同时映射到 {', '.join(config_ids)}" for property_name, config_ids in conflicts
            )
            raise OntologyInvalidError(
                f"多源对象类型中非标识属性只能单源映射（标识属性可作为跨源主键）：{details}；请为属性选择唯一来源模型"
            )

    async def _check_mapping_ownership(self, mappings: list[dict[str, Any]], exclude_object_type_id: int | None) -> None:
        """「一源一对象类型」：每个映射键全局只能归属一个对象类型（唯一索引兜底，预检给友好报错）。"""
        owners = await self._dao.find_mapping_owners_async(mappings, exclude_object_type_id=exclude_object_type_id)
        for config_id, owner_name in sorted(owners.items()):
            raise OntologyInvalidError(
                f"映射 {config_id} 已被对象类型 {owner_name} 占用（一个语义模型映射只能归属一个对象类型），请先解除原对象类型映射"
            )

    async def _validate_mapping_targets(self, mappings: list[dict[str, Any]]) -> None:
        """映射的语义模型必须真实存在（存在性校验；与「一源一对象类型」归属检查互补）。"""
        config_ids = {_text(mapping.get("semantic_config_id")) for mapping in mappings} - {""}
        if not config_ids:
            return
        existing_ids = await semantic_config_dao.filter_existing_config_ids_async(sorted(config_ids))
        missing = sorted(config_ids - existing_ids)
        if missing:
            raise OntologyInvalidError(f"映射的语义模型不存在: {', '.join(missing)}")

    async def _validate_mapping_group_anchor(
        self, mappings: list[dict[str, Any]], domain_row: dict[str, Any] | None
    ) -> None:
        """组锚点一致：对象类型挂领域时，其映射的语义模型必须都属于领域绑定的数据源组。"""
        if domain_row is None:
            return
        config_ids = {_text(mapping.get("semantic_config_id")) for mapping in mappings} - {""}
        if not config_ids:
            return
        domain_group_id = _text(domain_row.get("group_id"))
        group_ids = await semantic_config_dao.get_config_group_ids_async(sorted(config_ids))
        mismatched = sorted(config_id for config_id in config_ids if group_ids.get(config_id) != domain_group_id)
        if mismatched:
            raise OntologyInvalidError(
                f"映射的语义模型必须属于对象类型所属领域的数据源组 {domain_group_id}，"
                f"以下模型不在该组: {', '.join(mismatched)}"
            )

    async def _validate_mapping_dimensions(self, mappings: list[dict[str, Any]]) -> None:
        """映射右值必须是所选语义模型里真实存在的维度，且全部维度同表（实体锚点唯一）。

        层级红线：本体层只引用模型维度名，不重复定义物理列；实体锚点（行结构
        落在哪张表）由映射维度的所属表派生，跨表映射意味着对象类型没有确定行结构。
        """
        config_ids = {_text(mapping.get("semantic_config_id")) for mapping in mappings} - {""}
        if not config_ids:
            return
        dimension_index = await semantic_config_dao.get_config_dimensions_async(sorted(config_ids))
        for mapping in mappings:
            config_id = _text(mapping.get("semantic_config_id"))
            column_mappings = mapping.get("column_mappings") or {}
            if not column_mappings:
                continue
            dimensions = dimension_index.get(config_id)
            if dimensions is None:
                continue  # 模型不存在由 _validate_mapping_targets 报错，此处不重复
            anchor_tables: set[str] = set()
            for prop, dimension_name in column_mappings.items():
                table = _text(dimensions.get(_text(dimension_name)))
                if not table:
                    raise OntologyInvalidError(
                        f"映射 {config_id} 的属性 {prop} 引用了模型中不存在的维度: {dimension_name}"
                    )
                anchor_tables.add(table)
            if len(anchor_tables) > 1:
                raise OntologyInvalidError(
                    f"映射 {config_id} 的维度跨多张表（{', '.join(sorted(anchor_tables))}）："
                    "同一映射的维度必须同表，实体锚点才能唯一确定；跨表口径请拆分映射或建 join 模型"
                )

    async def _attach_object_type_names(self, relation_types: list[dict[str, Any]]) -> list[dict[str, Any]]:
        object_type_map = await self._dao.get_objects_by_ids_async(
            [int(r["from_object_type_id"]) for r in relation_types] + [int(r["to_object_type_id"]) for r in relation_types]
        )
        for relation in relation_types:
            from_obj = object_type_map.get(int(relation["from_object_type_id"])) or {}
            to_obj = object_type_map.get(int(relation["to_object_type_id"])) or {}
            relation["from_object_type_name"] = _text(from_obj.get("object_type_name"))
            relation["to_object_type_name"] = _text(to_obj.get("object_type_name"))
        return relation_types


def _normalize_mappings(mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """映射排序后规范化（仅保留结构键），用于编辑前后 diff 判断。"""
    normalized = []
    for mapping in mappings:
        normalized.append(
            {
                "binding_type": _text(mapping.get("binding_type")),
                "semantic_config_id": _text(mapping.get("semantic_config_id")),
                "column_mappings": {
                    _text(key): _text(value) for key, value in (mapping.get("column_mappings") or {}).items()
                },
            }
        )
    return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))


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
