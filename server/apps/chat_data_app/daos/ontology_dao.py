"""本体 DAO：对象/属性/关系/对象映射四张表的读写。

对象/属性/关系为三等实体（对齐 Palantir Object/Property/Link Type）。删除均为
软删：对象与关系同名重建走复活复用原行（翻回 delete_flag=0 并更新内容），属性
按 (object_id, property_name) 复活。语义模型映射独立存映射表
chat_data_ontology_object_mapping（对齐 ossie 概念层/映射层分离），随对象
upsert 同事务硬删+重插全量重写；「一源一对象」由映射表唯一索引约束，
service 层先查占用对象给友好报错，数据库唯一键兜底。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, or_, select, update
from utils.logger import get_logger
from utils.models import (
    ChatDataOntologyObject,
    ChatDataOntologyObjectMapping,
    ChatDataOntologyProperty,
    ChatDataOntologyRelation,
)

from .dao_session import session_scope

logger = get_logger(__name__)

_BINDING_TYPE_SEMANTIC = "semantic"


def _format_datetime(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_json(value: Any, expect_type: type) -> Any:
    """解析 JSON 文本，失败返回 expect_type 默认值（不静默：记录 warning）。"""
    if isinstance(value, expect_type):
        return value
    text = str(value or "").strip()
    if not text:
        return expect_type()
    try:
        parsed = json.loads(text)
    except Exception:
        logger.warning("本体 JSON 字段解析失败，按空值处理: %s", text[:120])
        return expect_type()
    return parsed if isinstance(parsed, expect_type) else expect_type()


def _object_row_to_dict(row: ChatDataOntologyObject, *, include_audit: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": int(row.id),
        "object_name": _text(row.object_name),
        "display_name": _text(row.display_name),
        "description": _text(row.description),
        "business_notes": _text(row.business_notes),
        "status": _text(row.status),
        "domain": _text(row.domain),
        "deprecation_reason": _text(row.deprecation_reason),
        "deprecated_until": _format_datetime(row.deprecated_until),
        "successor_object_name": _text(row.successor_object_name),
        "delete_flag": int(row.delete_flag or 0),
    }
    if include_audit:
        data.update(
            {
                "create_time": _format_datetime(row.create_time),
                "update_time": _format_datetime(row.update_time),
                "create_by": _text(row.create_by),
                "create_by_name": _text(row.create_by_name),
                "update_by": _text(row.update_by),
                "update_by_name": _text(row.update_by_name),
            }
        )
    return data


def _property_row_to_dict(row: ChatDataOntologyProperty) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "object_id": int(row.object_id),
        "property_name": _text(row.property_name),
        "display_name": _text(row.display_name),
        "description": _text(row.description),
        "value_type": _text(row.value_type),
        "enum_ref": _text(row.enum_ref),
        "render_hints": _parse_json(row.render_hints_json, dict),
        "is_identifier": bool(row.is_identifier),
        "is_title": bool(row.is_title),
    }


def _relation_row_to_dict(row: ChatDataOntologyRelation) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "relation_name": _text(row.relation_name),
        "from_object_id": int(row.from_object_id),
        "to_object_id": int(row.to_object_id),
        "from_columns": _parse_json(row.from_columns_json, list),
        "to_columns": _parse_json(row.to_columns_json, list),
        "multiplicity": _text(row.multiplicity),
        "from_display_name": _text(row.from_display_name),
        "to_display_name": _text(row.to_display_name),
        "from_verbalize": _text(row.from_verbalize),
        "to_verbalize": _text(row.to_verbalize),
        "description": _text(row.description),
        "business_notes": _text(row.business_notes),
        "status": _text(row.status),
        "create_time": _format_datetime(row.create_time),
        "update_time": _format_datetime(row.update_time),
        "update_by": _text(row.update_by),
        "update_by_name": _text(row.update_by_name),
    }


def _mapping_row_to_dict(row: ChatDataOntologyObjectMapping) -> dict[str, Any]:
    """映射行 → 映射视图（binding_type 固定 semantic，对外 API 形状与旧 mappings 一致）。"""
    return {
        "object_id": int(row.object_id),
        "binding_type": _BINDING_TYPE_SEMANTIC,
        "semantic_config_id": _text(row.semantic_config_id),
        "column_mappings": _parse_json(row.column_mappings_json, dict),
    }


class OntologyDao:
    """本体对象/属性/关系/映射访问对象。"""

    # ---------- 对象 ----------

    async def list_objects_async(self, keyword: str = "", status: str = "", domain: str = "") -> list[dict[str, Any]]:
        """列出对象（delete_flag=0），keyword 按名称/展示名/描述模糊过滤。"""
        async with session_scope() as session:
            stmt = select(ChatDataOntologyObject).where(ChatDataOntologyObject.delete_flag == 0)
            keyword_text = _text(keyword)
            if keyword_text:
                like = f"%{keyword_text}%"
                stmt = stmt.where(
                    (ChatDataOntologyObject.object_name.like(like))
                    | (ChatDataOntologyObject.display_name.like(like))
                    | (ChatDataOntologyObject.description.like(like))
                )
            if _text(status):
                stmt = stmt.where(ChatDataOntologyObject.status == _text(status))
            if _text(domain):
                stmt = stmt.where(ChatDataOntologyObject.domain == _text(domain))
            stmt = stmt.order_by(ChatDataOntologyObject.object_name.asc())
            rows = (await session.execute(stmt)).scalars().all()
        return [_object_row_to_dict(row) for row in rows]

    async def get_object_row_async(self, object_name: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        """按 object_name 读取对象行；include_deleted=True 时含软删行（复活判断用）。"""
        name = _text(object_name)
        if not name:
            return None
        async with session_scope() as session:
            stmt = select(ChatDataOntologyObject).where(ChatDataOntologyObject.object_name == name)
            if not include_deleted:
                stmt = stmt.where(ChatDataOntologyObject.delete_flag == 0)
            row = (await session.execute(stmt)).scalars().first()
        return _object_row_to_dict(row) if row is not None else None

    async def upsert_object_async(
        self,
        object_name: str,
        payload: dict[str, Any],
        *,
        allow_overwrite: bool,
        create_by: str = "",
        create_by_name: str = "",
        update_by: str = "",
        update_by_name: str = "",
    ) -> dict[str, Any] | None:
        """按 object_name 部分量 upsert 对象（仅状态机等窄写路径使用）。

        - 无行（含从未创建）→ 插入新行；
        - 软删行 → 复活复用原行，不算覆盖；
        - 活跃行 → 仅 allow_overwrite=True 时更新内容，否则返回 None。
        完整编辑（对象+属性+映射原子提交）走 upsert_object_full_async。
        """
        name = _text(object_name)
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyObject).where(ChatDataOntologyObject.object_name == name)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                session.add(
                    ChatDataOntologyObject(
                        object_name=name,
                        delete_flag=0,
                        create_by=create_by or "",
                        create_by_name=create_by_name or "",
                        update_by=update_by or "",
                        update_by_name=update_by_name or "",
                        **payload,
                    )
                )
                return {"object_name": name, **payload}
            if int(row.delete_flag or 0) == 1:
                row.delete_flag = 0
            elif not allow_overwrite:
                return None
            for field, value in payload.items():
                setattr(row, field, value)
            row.update_by = update_by or ""
            row.update_by_name = update_by_name or ""
        return {"object_name": name, **payload}

    async def upsert_object_full_async(
        self,
        object_name: str,
        row_payload: dict[str, Any],
        properties: list[dict[str, Any]],
        mappings: list[dict[str, Any]],
        *,
        allow_property_delete: bool,
        create_by: str = "",
        create_by_name: str = "",
        update_by: str = "",
        update_by_name: str = "",
    ) -> dict[str, Any] | None:
        """完整编辑对象：对象行 + 属性集 + 映射表单事务原子提交。

        - 无行 → 插入；软删行 → 复活；活跃行 → 覆盖（service 层已完成
          allow_overwrite 门禁，此处恒覆盖）；
        - 属性按 property_name 对齐 upsert（allow_property_delete 控制缺失属性软删）；
        - 映射软删+重插全量重写（软删行 delete_flag 置为行 id，腾出唯一索引槽位；
          唯一索引含 delete_flag，活跃行仍兜底一源一对象）；
        - 返回完整视图；理论冲突（并发占用同一映射）由唯一键 IntegrityError 兜底。
        """
        name = _text(object_name)
        incoming = {str(item.get("property_name") or "").strip(): item for item in properties}
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyObject).where(ChatDataOntologyObject.object_name == name)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                row = ChatDataOntologyObject(
                    object_name=name,
                    delete_flag=0,
                    create_by=create_by or "",
                    create_by_name=create_by_name or "",
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                    **row_payload,
                )
                session.add(row)
            else:
                row.delete_flag = 0
                for field, value in row_payload.items():
                    setattr(row, field, value)
                row.update_by = update_by or ""
                row.update_by_name = update_by_name or ""
            await session.flush()
            object_id = int(row.id)
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyProperty).where(ChatDataOntologyProperty.object_id == object_id)
                    )
                )
                .scalars()
                .all()
            )
            rows_by_name = {_text(item.property_name): item for item in rows}
            self._upsert_property_rows(
                session, object_id, incoming, rows_by_name, create_by, create_by_name, update_by, update_by_name
            )
            if allow_property_delete:
                self._soft_delete_missing_properties(rows_by_name, incoming, update_by, update_by_name)
            await session.execute(
                update(ChatDataOntologyObjectMapping)
                .where(
                    ChatDataOntologyObjectMapping.object_id == object_id,
                    ChatDataOntologyObjectMapping.delete_flag == 0,
                )
                .values(delete_flag=ChatDataOntologyObjectMapping.id)
            )
            for mapping in mappings:
                session.add(
                    ChatDataOntologyObjectMapping(
                        object_id=object_id,
                        semantic_config_id=_text(mapping.get("semantic_config_id")),
                        column_mappings_json=json.dumps(mapping.get("column_mappings") or {}, ensure_ascii=False),
                        create_by=create_by or "",
                        create_by_name=create_by_name or "",
                        update_by=update_by or "",
                        update_by_name=update_by_name or "",
                    )
                )
        return {"object_name": name, **row_payload, "mappings": mappings}

    async def soft_delete_object_async(self, object_name: str, update_by: str = "", update_by_name: str = "") -> None:
        name = _text(object_name)
        if not name:
            return
        async with session_scope() as session:
            await session.execute(
                update(ChatDataOntologyObject)
                .where(ChatDataOntologyObject.object_name == name, ChatDataOntologyObject.delete_flag == 0)
                .values(delete_flag=1, update_by=update_by or "", update_by_name=update_by_name or "")
            )

    async def get_objects_by_ids_async(self, object_ids: list[int]) -> dict[int, dict[str, Any]]:
        """按 ID 批量读对象（仅活跃行），校验器与关系行补对象名用。"""
        ids = {int(oid) for oid in object_ids if oid}
        if not ids:
            return {}
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObject).where(
                            ChatDataOntologyObject.id.in_(ids), ChatDataOntologyObject.delete_flag == 0
                        )
                    )
                )
                .scalars()
                .all()
            )
        return {int(row.id): _object_row_to_dict(row, include_audit=False) for row in rows}

    async def list_object_names_async(self) -> set[str]:
        """全部活跃对象 API 名集合（successor 存在性校验用）。"""
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObject.object_name).where(ChatDataOntologyObject.delete_flag == 0)
                    )
                )
                .scalars()
                .all()
            )
        return {_text(row) for row in rows}

    async def find_mapping_owners_async(
        self,
        mappings: list[dict[str, Any]],
        exclude_object_id: int | None = None,
    ) -> dict[str, str]:
        """查映射键已被其他活跃对象占用的映射（「一源一对象」友好预检，唯一索引兜底）。

        返回 {semantic_config_id: object_name}；exclude_object_id 用于编辑自身时排除。
        """
        config_ids = {_text(mapping.get("semantic_config_id")) for mapping in mappings} - {""}
        if not config_ids:
            return {}
        stmt = (
            select(ChatDataOntologyObjectMapping, ChatDataOntologyObject.object_name)
            .join(ChatDataOntologyObject, ChatDataOntologyObject.id == ChatDataOntologyObjectMapping.object_id)
            .where(
                ChatDataOntologyObjectMapping.semantic_config_id.in_(config_ids),
                ChatDataOntologyObject.delete_flag == 0,
                ChatDataOntologyObjectMapping.delete_flag == 0,
            )
        )
        if exclude_object_id is not None:
            stmt = stmt.where(ChatDataOntologyObjectMapping.object_id != int(exclude_object_id))
        async with session_scope() as session:
            rows = (await session.execute(stmt)).all()
        return {_text(row.semantic_config_id): _text(object_name) for row, object_name in rows}

    async def list_object_mappings_async(self, object_id: int) -> list[dict[str, Any]]:
        """列出对象的全部活跃映射（映射视图，按语义配置ID排序）。"""
        oid = int(object_id)
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectMapping)
                        .where(
                            ChatDataOntologyObjectMapping.object_id == oid,
                            ChatDataOntologyObjectMapping.delete_flag == 0,
                        )
                        .order_by(ChatDataOntologyObjectMapping.semantic_config_id.asc())
                    )
                )
                .scalars()
                .all()
            )
        return [_mapping_row_to_dict(row) for row in rows]

    async def count_mappings_by_object_ids_async(self, object_ids: list[int]) -> dict[int, int]:
        """按对象批量统计活跃映射数（列表页绑定计数）。"""
        ids = {int(oid) for oid in object_ids if oid}
        if not ids:
            return {}
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectMapping.object_id, func.count())
                        .where(
                            ChatDataOntologyObjectMapping.object_id.in_(ids),
                            ChatDataOntologyObjectMapping.delete_flag == 0,
                        )
                        .group_by(ChatDataOntologyObjectMapping.object_id)
                    )
                )
                .all()
            )
        return {int(object_id): int(count) for object_id, count in rows}

    async def delete_object_mappings_async(self, object_id: int) -> None:
        """软删对象全部映射（对象删除时级联；映射无独立生命周期，软删行置 delete_flag=id）。"""
        async with session_scope() as session:
            await session.execute(
                update(ChatDataOntologyObjectMapping)
                .where(
                    ChatDataOntologyObjectMapping.object_id == int(object_id),
                    ChatDataOntologyObjectMapping.delete_flag == 0,
                )
                .values(delete_flag=ChatDataOntologyObjectMapping.id)
            )

    async def load_injection_bundle_async(self, semantic_config_ids: list[str]) -> dict[str, Any]:
        """按启用语义模型集合一次性装载本体注入数据（消费侧专用，单事务四次查询）。

        返回 {"objects", "properties", "relations", "mappings"}：
        - 对象：绑定到给定语义模型、活跃、状态 certified/experimental；
        - 属性：上述对象的全部活跃属性；
        - 关系：两端对象都在上述集合内、状态 certified/experimental 的关系
          （单端在场无法导航不注入；draft/deprecated 不进 AI 目录，对齐对象侧口径）；
        - 映射：绑定给定语义模型的活跃映射行（渲染侧按通过过滤的对象归属裁剪）。
        """
        empty: dict[str, Any] = {"objects": [], "properties": [], "relations": [], "mappings": []}
        ids = sorted({_text(item) for item in semantic_config_ids} - {""})
        if not ids:
            return empty
        async with session_scope() as session:
            mapping_rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectMapping).where(
                            ChatDataOntologyObjectMapping.semantic_config_id.in_(ids),
                            ChatDataOntologyObjectMapping.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .all()
            )
            candidate_ids = sorted({int(row.object_id) for row in mapping_rows})
            if not candidate_ids:
                return empty
            object_rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObject).where(
                            ChatDataOntologyObject.id.in_(candidate_ids),
                            ChatDataOntologyObject.delete_flag == 0,
                            ChatDataOntologyObject.status.in_(("certified", "experimental")),
                        )
                    )
                )
                .scalars()
                .all()
            )
            kept_ids = {int(row.id) for row in object_rows}
            if not kept_ids:
                return empty
            property_rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyProperty).where(
                            ChatDataOntologyProperty.object_id.in_(kept_ids),
                            ChatDataOntologyProperty.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .all()
            )
            relation_rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyRelation).where(
                            ChatDataOntologyRelation.delete_flag == 0,
                            ChatDataOntologyRelation.status.in_(("certified", "experimental")),
                            ChatDataOntologyRelation.from_object_id.in_(kept_ids),
                            ChatDataOntologyRelation.to_object_id.in_(kept_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
        return {
            "objects": [_object_row_to_dict(row, include_audit=False) for row in object_rows],
            "properties": [_property_row_to_dict(row) for row in property_rows],
            "relations": [_relation_row_to_dict(row) for row in relation_rows],
            "mappings": [_mapping_row_to_dict(row) for row in mapping_rows],
        }

    # ---------- 属性 ----------

    async def list_properties_async(self, object_id: int) -> list[dict[str, Any]]:
        """列出对象下全部活跃属性（按属性名排序）。"""
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyProperty)
                        .where(
                            ChatDataOntologyProperty.object_id == int(object_id),
                            ChatDataOntologyProperty.delete_flag == 0,
                        )
                        .order_by(ChatDataOntologyProperty.property_name.asc())
                    )
                )
                .scalars()
                .all()
            )
        return [_property_row_to_dict(row) for row in rows]

    async def replace_properties_async(
        self,
        object_id: int,
        items: list[dict[str, Any]],
        *,
        allow_delete: bool,
        create_by: str = "",
        create_by_name: str = "",
        update_by: str = "",
        update_by_name: str = "",
    ) -> list[dict[str, Any]]:
        """全量替换对象属性集：items 为标准化属性字典（service 层校验后）。

        以 property_name 对齐既有行：存在则更新，软删/不存在则插入（软删行复活）；
        既有行不在 items 中时，allow_delete=True 才软删（certified 冻结属性时
        service 层传 False 并自行拦截）。
        """
        incoming = {str(item.get("property_name") or "").strip(): item for item in items}
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyProperty).where(
                            ChatDataOntologyProperty.object_id == int(object_id),
                        )
                    )
                )
                .scalars()
                .all()
            )
            rows_by_name = {_text(row.property_name): row for row in rows}
            self._upsert_property_rows(
                session, object_id, incoming, rows_by_name, create_by, create_by_name, update_by, update_by_name
            )
            if allow_delete:
                self._soft_delete_missing_properties(rows_by_name, incoming, update_by, update_by_name)
            fresh = (
                (
                    await session.execute(
                        select(ChatDataOntologyProperty)
                        .where(
                            ChatDataOntologyProperty.object_id == int(object_id),
                            ChatDataOntologyProperty.delete_flag == 0,
                        )
                        .order_by(ChatDataOntologyProperty.property_name.asc())
                    )
                )
                .scalars()
                .all()
            )
            result = [_property_row_to_dict(row) for row in fresh]
        return result

    def _upsert_property_rows(
        self,
        session: Any,
        object_id: int,
        incoming: dict[str, dict[str, Any]],
        rows_by_name: dict[str, ChatDataOntologyProperty],
        create_by: str,
        create_by_name: str,
        update_by: str,
        update_by_name: str,
    ) -> None:
        """按 property_name 对齐 upsert 属性行（软删行复活复用）。"""
        for name, item in incoming.items():
            row = rows_by_name.get(name)
            if row is None:
                session.add(
                    ChatDataOntologyProperty(
                        object_id=int(object_id),
                        property_name=name,
                        delete_flag=0,
                        create_by=create_by or "",
                        create_by_name=create_by_name or "",
                        update_by=update_by or "",
                        update_by_name=update_by_name or "",
                        **self._property_payload(item),
                    )
                )
                continue
            if int(row.delete_flag or 0) == 1:
                row.delete_flag = 0
            for field, value in self._property_payload(item).items():
                setattr(row, field, value)
            row.update_by = update_by or ""
            row.update_by_name = update_by_name or ""

    def _soft_delete_missing_properties(
        self,
        rows_by_name: dict[str, ChatDataOntologyProperty],
        incoming: dict[str, dict[str, Any]],
        update_by: str,
        update_by_name: str,
    ) -> None:
        """软删本轮未提交的既有属性（certified 场景由 service 层拦截）。"""
        for name, row in rows_by_name.items():
            if name not in incoming and int(row.delete_flag or 0) == 0:
                row.delete_flag = 1
                row.update_by = update_by or ""
                row.update_by_name = update_by_name or ""

    # ---------- 关系 ----------

    async def list_relations_async(
        self, keyword: str = "", from_object_id: int | None = None, to_object_id: int | None = None
    ) -> list[dict[str, Any]]:
        """列出关系（delete_flag=0），keyword 按名称/描述/方向展示名模糊过滤。"""
        async with session_scope() as session:
            stmt = select(ChatDataOntologyRelation).where(ChatDataOntologyRelation.delete_flag == 0)
            keyword_text = _text(keyword)
            if keyword_text:
                like = f"%{keyword_text}%"
                stmt = stmt.where(
                    (ChatDataOntologyRelation.relation_name.like(like))
                    | (ChatDataOntologyRelation.description.like(like))
                    | (ChatDataOntologyRelation.from_display_name.like(like))
                    | (ChatDataOntologyRelation.to_display_name.like(like))
                )
            if from_object_id is not None:
                stmt = stmt.where(ChatDataOntologyRelation.from_object_id == int(from_object_id))
            if to_object_id is not None:
                stmt = stmt.where(ChatDataOntologyRelation.to_object_id == int(to_object_id))
            stmt = stmt.order_by(ChatDataOntologyRelation.relation_name.asc())
            rows = (await session.execute(stmt)).scalars().all()
        return [_relation_row_to_dict(row) for row in rows]

    async def get_relation_row_async(
        self, relation_name: str, *, include_deleted: bool = False
    ) -> dict[str, Any] | None:
        name = _text(relation_name)
        if not name:
            return None
        async with session_scope() as session:
            stmt = select(ChatDataOntologyRelation).where(ChatDataOntologyRelation.relation_name == name)
            if not include_deleted:
                stmt = stmt.where(ChatDataOntologyRelation.delete_flag == 0)
            row = (await session.execute(stmt)).scalars().first()
        return _relation_row_to_dict(row) if row is not None else None

    async def upsert_relation_async(
        self,
        relation_name: str,
        payload: dict[str, Any],
        *,
        allow_overwrite: bool,
        create_by: str = "",
        create_by_name: str = "",
        update_by: str = "",
        update_by_name: str = "",
    ) -> dict[str, Any] | None:
        """按 relation_name upsert 关系（软删行复活复用，语义同对象 upsert）。"""
        name = _text(relation_name)
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyRelation).where(ChatDataOntologyRelation.relation_name == name)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                session.add(
                    ChatDataOntologyRelation(
                        relation_name=name,
                        delete_flag=0,
                        create_by=create_by or "",
                        create_by_name=create_by_name or "",
                        update_by=update_by or "",
                        update_by_name=update_by_name or "",
                        **payload,
                    )
                )
            else:
                if int(row.delete_flag or 0) == 1:
                    row.delete_flag = 0
                elif not allow_overwrite:
                    return None
                for field, value in payload.items():
                    setattr(row, field, value)
                row.update_by = update_by or ""
                row.update_by_name = update_by_name or ""
        return self._build_relation_view(name, payload)

    async def soft_delete_relation_async(
        self, relation_name: str, update_by: str = "", update_by_name: str = ""
    ) -> None:
        name = _text(relation_name)
        if not name:
            return
        async with session_scope() as session:
            await session.execute(
                update(ChatDataOntologyRelation)
                .where(ChatDataOntologyRelation.relation_name == name, ChatDataOntologyRelation.delete_flag == 0)
                .values(delete_flag=1, update_by=update_by or "", update_by_name=update_by_name or "")
            )

    async def list_relations_for_object_async(self, object_id: int) -> list[dict[str, Any]]:
        """对象作为 from 或 to 参与的全部活跃关系（级联与悬空检查用）。"""
        oid = int(object_id)
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyRelation).where(
                            or_(
                                ChatDataOntologyRelation.from_object_id == oid,
                                ChatDataOntologyRelation.to_object_id == oid,
                            ),
                            ChatDataOntologyRelation.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [_relation_row_to_dict(row) for row in rows]

    async def set_relations_status_async(
        self, relation_ids: list[int], status: str, update_by: str = "", update_by_name: str = ""
    ) -> None:
        """批量设置关系状态（对象状态级联同步）。"""
        ids = {int(rid) for rid in relation_ids if rid}
        if not ids:
            return
        async with session_scope() as session:
            await session.execute(
                update(ChatDataOntologyRelation)
                .where(ChatDataOntologyRelation.id.in_(ids), ChatDataOntologyRelation.delete_flag == 0)
                .values(status=_text(status), update_by=update_by or "", update_by_name=update_by_name or "")
            )

    # ---------- 视图构造 ----------

    def _build_relation_view(self, relation_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "relation_name": relation_name,
            **payload,
            "from_columns": _parse_json(payload.get("from_columns_json"), list),
            "to_columns": _parse_json(payload.get("to_columns_json"), list),
        }

    def _property_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        """属性行内容字段（不含 object_id/property_name/审计字段）。"""
        return {
            "display_name": _text(item.get("display_name")),
            "description": _text(item.get("description")),
            "value_type": _text(item.get("value_type")) or "String",
            "enum_ref": _text(item.get("enum_ref")),
            "render_hints_json": json.dumps(_parse_json(item.get("render_hints_json"), dict), ensure_ascii=False),
            "is_identifier": 1 if item.get("is_identifier") else 0,
            "is_title": 1 if item.get("is_title") else 0,
        }
