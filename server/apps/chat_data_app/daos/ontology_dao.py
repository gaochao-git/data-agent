"""本体 DAO：对象/属性/关系三张表的读写与绑定冲突粗筛。

对象/属性/关系为三等实体（对齐 Palantir Object/Property/Link Type）。删除均为
软删：对象与关系同名重建走复活复用原行（翻回 delete_flag=0 并更新内容），属性
按 (object_id, property_name) 复活。绑定双轨存于对象 bindings_json：semantic
绑语义模型、dataset 绑物理表；「一源一对象」唯一归属检查遵循「禁止数据库原生
JSON 语法」约束，LIKE 粗筛 + 应用层 JSON 解析精确确认（两步法）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_, select, update

from utils.logger import get_logger
from utils.models import ChatDataOntologyObject, ChatDataOntologyProperty, ChatDataOntologyRelation

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


def _parse_bindings(raw: Any) -> list[dict[str, Any]]:
    """解析对象绑定 JSON 文本（统一语义模型绑定），非法条目过滤并告警。

    dataset 轨已下线：历史数据若残留 dataset 绑定则过滤并告警，不进入运行时。
    """
    bindings = _parse_json(raw, list)
    result: list[dict[str, Any]] = []
    for item in bindings:
        if not isinstance(item, dict):
            continue
        binding_type = _text(item.get("binding_type"))
        if binding_type != _BINDING_TYPE_SEMANTIC:
            logger.warning("本体绑定 binding_type 非法（仅支持 semantic），已过滤: %s", binding_type)
            continue
        result.append({**item, "binding_type": binding_type})
    return result


def _object_row_to_dict(row: ChatDataOntologyObject, *, include_audit: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": int(row.id),
        "object_name": _text(row.object_name),
        "display_name": _text(row.display_name),
        "plural_display_name": _text(row.plural_display_name),
        "description": _text(row.description),
        "business_notes": _text(row.business_notes),
        "status": _text(row.status),
        "visibility": _text(row.visibility),
        "domain": _text(row.domain),
        "deprecation_reason": _text(row.deprecation_reason),
        "deprecated_until": _format_datetime(row.deprecated_until),
        "successor_object_name": _text(row.successor_object_name),
        "bindings": _parse_bindings(row.bindings_json),
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


def _binding_identity(binding: dict[str, Any]) -> tuple[str, str, str] | None:
    """提取绑定的唯一归属键：(binding_type, 语义配置ID, 表别名)。"""
    config_id = _text(binding.get("semantic_config_id"))
    if not config_id:
        return None
    return (_BINDING_TYPE_SEMANTIC, config_id, _text(binding.get("table_alias")))


class OntologyDao:
    """本体三表访问对象。"""

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
        """按 object_name upsert 对象，返回写入后的完整视图。

        - 无行（含从未创建）→ 插入新行；
        - 软删行 → 复活复用原行，不算覆盖；
        - 活跃行 → 仅 allow_overwrite=True 时更新内容，否则返回 None。
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
                return self._build_object_view(name, payload)
            if int(row.delete_flag or 0) == 1:
                row.delete_flag = 0
            elif not allow_overwrite:
                return None
            for field, value in payload.items():
                setattr(row, field, value)
            row.update_by = update_by or ""
            row.update_by_name = update_by_name or ""
        return self._build_object_view(name, payload)

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

    async def find_binding_owner_async(
        self, identity: tuple[str, str, str], exclude_object_name: str = ""
    ) -> dict[str, Any] | None:
        """查找占用指定绑定唯一键的活跃对象（一源一对象检查，两步法）。

        先按绑定主体标识 LIKE 粗筛 bindings_json，再在应用层解析精确确认；
        exclude_object_name 用于编辑自身时排除。
        """
        _, subject, _ = identity
        if not subject:
            return None
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObject).where(
                            ChatDataOntologyObject.bindings_json.like(f"%{subject}%"),
                            ChatDataOntologyObject.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .all()
            )
        for row in rows:
            if _text(row.object_name) == _text(exclude_object_name):
                continue
            bindings = _parse_bindings(row.bindings_json)
            if any(_binding_identity(binding) == identity for binding in bindings):
                return _object_row_to_dict(row, include_audit=False)
        return None

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

    def _build_object_view(self, object_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """upsert 返回视图（与行内容字段同源，审计时间由数据库生成不回显）。"""
        return {
            "object_name": object_name,
            **payload,
            "bindings": _parse_bindings(payload.get("bindings_json")),
        }

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
