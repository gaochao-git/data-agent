"""本体 DAO：对象类型/属性/关系类型/对象类型映射四张表的读写。

对象类型/属性/关系类型为三等实体（对齐 Palantir Object/Property/Link Type）。删除均为
软删：对象类型与关系类型同名重建走复活复用原行（翻回 delete_flag=0 并更新内容），属性
按 (object_type_id, property_name) 复活。语义模型映射独立存映射表
chat_data_ontology_mapping（对齐 ossie 概念层/映射层分离），随对象类型
upsert 同事务软删（delete_flag=行 id）+重插全量重写；「一源一对象类型」由映射表唯一索引约束，
service 层先查占用对象类型给友好报错，数据库唯一键兜底。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, or_, select, update

from utils.logger import get_logger
from utils.models import (
    ChatDataOntologyDomain,
    ChatDataOntologyObjectType,
    ChatDataOntologyObjectTypeMapping,
    ChatDataOntologyProperty,
    ChatDataOntologyRelationType,
    SemanticConfig,
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


# 写入路径键翻译：service/validator 与 API 契约使用对外键名（description/status/multiplicity），
# DB 列按「除 id 外必须 ≥2 个单词下划线」红线命名为 description_text/lifecycle_status/multiplicity_type。
# 该翻译只作用于 ORM 行字段（setattr/构造器/values），视图 dict 的对外键名不变。
_ROW_FIELD_ALIASES = {
    "description": "description_text",
    "status": "lifecycle_status",
    "multiplicity": "multiplicity_type",
}


def _to_row_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """API 键名 → ORM 行字段键名（其余键原样透传）。"""
    return {_ROW_FIELD_ALIASES.get(key, key): value for key, value in payload.items()}


def _object_type_row_to_dict(row: ChatDataOntologyObjectType, *, include_audit: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": int(row.id),
        "object_type_name": _text(row.object_type_name),
        "display_name": _text(row.display_name),
        "description": _text(row.description_text),
        "status": _text(row.lifecycle_status),
        "domain_id": int(row.domain_id) if row.domain_id else None,
        "deprecation_reason": _text(row.deprecation_reason),
        "deprecated_until": _format_datetime(row.deprecated_until),
        "successor_object_type_name": _text(row.successor_object_type_name),
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
        "object_type_id": int(row.object_type_id),
        "property_name": _text(row.property_name),
        "display_name": _text(row.display_name),
        "description": _text(row.description_text),
        "value_type": _text(row.value_type),
        "enum_ref": _text(row.enum_ref),
        "render_hints": _parse_json(row.render_hints_json, dict),
        "is_identifier": bool(row.is_identifier),
        "is_title": bool(row.is_title),
    }


def _relation_row_to_dict(row: ChatDataOntologyRelationType) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "relation_type_name": _text(row.relation_type_name),
        "from_object_type_id": int(row.from_object_type_id),
        "to_object_type_id": int(row.to_object_type_id),
        "from_columns": _parse_json(row.from_columns_json, list),
        "to_columns": _parse_json(row.to_columns_json, list),
        "multiplicity": _text(row.multiplicity_type),
        "from_display_name": _text(row.from_display_name),
        "to_display_name": _text(row.to_display_name),
        "from_verbalize": _text(row.from_verbalize),
        "to_verbalize": _text(row.to_verbalize),
        "description": _text(row.description_text),
        "status": _text(row.lifecycle_status),
        "delete_flag": int(row.delete_flag or 0),
        "create_time": _format_datetime(row.create_time),
        "update_time": _format_datetime(row.update_time),
        "update_by": _text(row.update_by),
        "update_by_name": _text(row.update_by_name),
    }


def _mapping_row_to_dict(row: ChatDataOntologyObjectTypeMapping) -> dict[str, Any]:
    """映射行 → 映射视图（binding_type 固定 semantic，对外 API 形状与旧 mappings 一致）。"""
    return {
        "object_type_id": int(row.object_type_id),
        "binding_type": _BINDING_TYPE_SEMANTIC,
        "semantic_config_id": _text(row.semantic_config_id),
        "column_mappings": _parse_json(row.column_mappings_json, dict),
    }


class OntologyDao:
    """本体领域/对象类型/属性/关系类型/映射访问对象。"""

    # ---------- 领域 ----------

    def _domain_row_to_dict(self, row: ChatDataOntologyDomain) -> dict[str, Any]:
        return {
            "id": int(row.id),
            "domain_name": _text(row.domain_name),
            "group_id": _text(row.group_id),
            "display_name": _text(row.display_name),
            "description": _text(row.description_text),
            "delete_flag": int(row.delete_flag or 0),
            "create_time": _format_datetime(row.create_time),
            "update_time": _format_datetime(row.update_time),
            "update_by": _text(row.update_by),
            "update_by_name": _text(row.update_by_name),
        }

    async def list_domains_async(self) -> list[dict[str, Any]]:
        """列出领域（delete_flag=0），按领域名字典序。"""
        async with session_scope() as session:
            stmt = (
                select(ChatDataOntologyDomain)
                .where(ChatDataOntologyDomain.delete_flag == 0)
                .order_by(ChatDataOntologyDomain.domain_name.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
        return [self._domain_row_to_dict(row) for row in rows]

    async def get_domain_row_async(self, domain_name: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        """按 domain_name 读取领域行；include_deleted=True 时含软删行（复活判断用）。"""
        name = _text(domain_name)
        if not name:
            return None
        async with session_scope() as session:
            stmt = select(ChatDataOntologyDomain).where(ChatDataOntologyDomain.domain_name == name)
            if not include_deleted:
                stmt = stmt.where(ChatDataOntologyDomain.delete_flag == 0)
            row = (await session.execute(stmt)).scalars().first()
        return self._domain_row_to_dict(row) if row is not None else None

    async def get_domain_row_by_id_async(self, domain_id: int) -> dict[str, Any] | None:
        """按 id 读取活跃领域行。"""
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyDomain).where(
                            ChatDataOntologyDomain.id == int(domain_id),
                            ChatDataOntologyDomain.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .first()
            )
        return self._domain_row_to_dict(row) if row is not None else None

    async def upsert_domain_async(
        self,
        domain_name: str,
        payload: dict[str, Any],
        *,
        allow_overwrite: bool,
        create_by: str = "",
        create_by_name: str = "",
        update_by: str = "",
        update_by_name: str = "",
    ) -> dict[str, Any] | None:
        """按 domain_name upsert 领域：无行插入 / 软删行复活 / 活跃行需 allow_overwrite。"""
        name = _text(domain_name)
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyDomain).where(ChatDataOntologyDomain.domain_name == name)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                row = ChatDataOntologyDomain(
                    domain_name=name,
                    delete_flag=0,
                    create_by=create_by or "",
                    create_by_name=create_by_name or "",
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                    **_to_row_fields(payload),
                )
                session.add(row)
            else:
                if int(row.delete_flag or 0) == 1:
                    row.delete_flag = 0
                elif not allow_overwrite:
                    return None
                for field, value in _to_row_fields(payload).items():
                    setattr(row, field, value)
                row.update_by = update_by or ""
                row.update_by_name = update_by_name or ""
        return {"domain_name": name, **payload}

    async def soft_delete_domain_async(
        self, domain_name: str, *, update_by: str = "", update_by_name: str = ""
    ) -> bool:
        """软删领域；领域不存在或已删返回 False。挂有对象类型时由 service 层先拦。"""
        name = _text(domain_name)
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyDomain).where(
                            ChatDataOntologyDomain.domain_name == name,
                            ChatDataOntologyDomain.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return False
            row.delete_flag = 1
            row.update_by = update_by or ""
            row.update_by_name = update_by_name or ""
        return True

    async def count_objects_by_domain_ids_async(self, domain_ids: list[int]) -> dict[int, int]:
        """统计各领域挂靠的活跃对象类型数（领域删除门禁用）。"""
        counts: dict[int, int] = {}
        if not domain_ids:
            return counts
        async with session_scope() as session:
            stmt = (
                select(ChatDataOntologyObjectType.domain_id, func.count(ChatDataOntologyObjectType.id))
                .where(
                    ChatDataOntologyObjectType.delete_flag == 0,
                    ChatDataOntologyObjectType.domain_id.in_(domain_ids),
                )
                .group_by(ChatDataOntologyObjectType.domain_id)
            )
            rows = (await session.execute(stmt)).all()
        for domain_id, count in rows:
            if domain_id is not None:
                counts[int(domain_id)] = int(count)
        return counts

    async def list_domain_object_type_mapping_configs_async(self, domain_id: int) -> list[dict[str, Any]]:
        """列出领域内活跃对象类型及其映射的语义模型 ID（领域改组锚点校验用）。

        返回 [{object_type_name, semantic_config_id}]；无映射的对象类型不出现在结果中。
        """
        async with session_scope() as session:
            stmt = (
                select(
                    ChatDataOntologyObjectType.object_type_name, ChatDataOntologyObjectTypeMapping.semantic_config_id
                )
                .join(
                    ChatDataOntologyObjectTypeMapping,
                    ChatDataOntologyObjectTypeMapping.object_type_id == ChatDataOntologyObjectType.id,
                )
                .where(
                    ChatDataOntologyObjectType.domain_id == int(domain_id),
                    ChatDataOntologyObjectType.delete_flag == 0,
                    ChatDataOntologyObjectTypeMapping.delete_flag == 0,
                )
                .order_by(ChatDataOntologyObjectType.object_type_name.asc())
            )
            rows = (await session.execute(stmt)).all()
        return [
            {"object_type_name": _text(object_type_name), "semantic_config_id": _text(config_id)}
            for object_type_name, config_id in rows
        ]

    async def list_active_domain_names_by_group_async(self, group_id: str) -> list[str]:
        """列出绑定指定数据源组的活跃本体领域名（数据源组删除守护用）。"""
        gid = _text(group_id)
        if not gid:
            return []
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyDomain.domain_name)
                        .where(
                            ChatDataOntologyDomain.group_id == gid,
                            ChatDataOntologyDomain.delete_flag == 0,
                        )
                        .order_by(ChatDataOntologyDomain.domain_name.asc())
                    )
                )
                .scalars()
                .all()
            )
        return [_text(name) for name in rows]

    # ---------- 对象类型 ----------

    async def list_object_types_async(
        self, keyword: str = "", status: str = "", domain_id: int | None = None
    ) -> list[dict[str, Any]]:
        """列出对象类型（delete_flag=0），keyword 按名称/展示名/描述模糊过滤。"""
        async with session_scope() as session:
            stmt = select(ChatDataOntologyObjectType).where(ChatDataOntologyObjectType.delete_flag == 0)
            keyword_text = _text(keyword)
            if keyword_text:
                like = f"%{keyword_text}%"
                stmt = stmt.where(
                    (ChatDataOntologyObjectType.object_type_name.like(like))
                    | (ChatDataOntologyObjectType.display_name.like(like))
                    | (ChatDataOntologyObjectType.description_text.like(like))
                )
            if _text(status):
                stmt = stmt.where(ChatDataOntologyObjectType.lifecycle_status == _text(status))
            if domain_id is not None:
                stmt = stmt.where(ChatDataOntologyObjectType.domain_id == int(domain_id))
            stmt = stmt.order_by(ChatDataOntologyObjectType.object_type_name.asc())
            rows = (await session.execute(stmt)).scalars().all()
        return [_object_type_row_to_dict(row) for row in rows]

    async def get_object_type_row_async(
        self, object_type_name: str, *, include_deleted: bool = False
    ) -> dict[str, Any] | None:
        """按 object_type_name 读取对象类型行；include_deleted=True 时含软删行（复活判断用）。"""
        name = _text(object_type_name)
        if not name:
            return None
        async with session_scope() as session:
            stmt = select(ChatDataOntologyObjectType).where(ChatDataOntologyObjectType.object_type_name == name)
            if not include_deleted:
                stmt = stmt.where(ChatDataOntologyObjectType.delete_flag == 0)
            row = (await session.execute(stmt)).scalars().first()
        return _object_type_row_to_dict(row) if row is not None else None

    async def upsert_object_type_async(
        self,
        object_type_name: str,
        payload: dict[str, Any],
        *,
        allow_overwrite: bool,
        create_by: str = "",
        create_by_name: str = "",
        update_by: str = "",
        update_by_name: str = "",
    ) -> dict[str, Any] | None:
        """按 object_type_name 部分量 upsert 对象类型（仅状态机等窄写路径使用）。

        - 无行（含从未创建）→ 插入新行；
        - 软删行 → 复活复用原行，不算覆盖；
        - 活跃行 → 仅 allow_overwrite=True 时更新内容，否则返回 None。
        完整编辑（对象类型+属性+映射原子提交）走 upsert_object_type_full_async。
        """
        name = _text(object_type_name)
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectType).where(ChatDataOntologyObjectType.object_type_name == name)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                session.add(
                    ChatDataOntologyObjectType(
                        object_type_name=name,
                        delete_flag=0,
                        create_by=create_by or "",
                        create_by_name=create_by_name or "",
                        update_by=update_by or "",
                        update_by_name=update_by_name or "",
                        **_to_row_fields(payload),
                    )
                )
                return {"object_type_name": name, **payload}
            if int(row.delete_flag or 0) == 1:
                row.delete_flag = 0
            elif not allow_overwrite:
                return None
            for field, value in _to_row_fields(payload).items():
                setattr(row, field, value)
            row.update_by = update_by or ""
            row.update_by_name = update_by_name or ""
        return {"object_type_name": name, **payload}

    async def upsert_object_type_full_async(
        self,
        object_type_name: str,
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
        """完整编辑对象类型：对象类型行 + 属性集 + 映射表单事务原子提交。

        - 无行 → 插入；软删行 → 复活；活跃行 → 覆盖（service 层已完成
          allow_overwrite 门禁，此处恒覆盖）；
        - 属性按 property_name 对齐 upsert（allow_property_delete 控制缺失属性软删）；
        - 映射软删+重插全量重写（软删行 delete_flag 置为行 id，腾出唯一索引槽位；
          唯一索引含 delete_flag，活跃行仍兜底一源一对象类型）；
        - 返回完整视图；理论冲突（并发占用同一映射）由唯一键 IntegrityError 兜底。
        """
        name = _text(object_type_name)
        incoming = {str(item.get("property_name") or "").strip(): item for item in properties}
        async with session_scope() as session:
            row = await self._load_or_create_object_type_row(
                session, name, row_payload, create_by, create_by_name, update_by, update_by_name
            )
            await session.flush()
            object_type_id = int(row.id)
            rows_by_name = await self._load_property_rows(session, object_type_id)
            self._upsert_property_rows(
                session, object_type_id, incoming, rows_by_name, create_by, create_by_name, update_by, update_by_name
            )
            if allow_property_delete:
                self._soft_delete_missing_properties(rows_by_name, incoming, update_by, update_by_name)
            await self._replace_mapping_rows(
                session, object_type_id, mappings, create_by, create_by_name, update_by, update_by_name
            )
        return {"object_type_name": name, **row_payload, "mappings": mappings}

    async def _load_or_create_object_type_row(
        self,
        session: Any,
        name: str,
        row_payload: dict[str, Any],
        create_by: str,
        create_by_name: str,
        update_by: str,
        update_by_name: str,
    ) -> ChatDataOntologyObjectType:
        """读取对象类型行（含软删）；无行插入、软删行翻回复活、活跃行覆盖内容。"""
        row = (
            (
                await session.execute(
                    select(ChatDataOntologyObjectType).where(ChatDataOntologyObjectType.object_type_name == name)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            row = ChatDataOntologyObjectType(
                object_type_name=name,
                delete_flag=0,
                create_by=create_by or "",
                create_by_name=create_by_name or "",
                update_by=update_by or "",
                update_by_name=update_by_name or "",
                **_to_row_fields(row_payload),
            )
            session.add(row)
            return row
        row.delete_flag = 0
        for field, value in _to_row_fields(row_payload).items():
            setattr(row, field, value)
        row.update_by = update_by or ""
        row.update_by_name = update_by_name or ""
        return row

    async def _load_property_rows(self, session: Any, object_type_id: int) -> dict[str, ChatDataOntologyProperty]:
        """按属性名索引对象类型全部属性行（含软删行，供复活对齐）。"""
        rows = (
            (
                await session.execute(
                    select(ChatDataOntologyProperty).where(ChatDataOntologyProperty.object_type_id == object_type_id)
                )
            )
            .scalars()
            .all()
        )
        return {_text(item.property_name): item for item in rows}

    async def _replace_mapping_rows(
        self,
        session: Any,
        object_type_id: int,
        mappings: list[dict[str, Any]],
        create_by: str,
        create_by_name: str,
        update_by: str,
        update_by_name: str,
    ) -> None:
        """映射表全量重写：活跃行软删（delete_flag=行 id）后重插本次映射。"""
        await session.execute(
            update(ChatDataOntologyObjectTypeMapping)
            .where(
                ChatDataOntologyObjectTypeMapping.object_type_id == object_type_id,
                ChatDataOntologyObjectTypeMapping.delete_flag == 0,
            )
            .values(delete_flag=ChatDataOntologyObjectTypeMapping.id)
        )
        for mapping in mappings:
            session.add(
                ChatDataOntologyObjectTypeMapping(
                    object_type_id=object_type_id,
                    semantic_config_id=_text(mapping.get("semantic_config_id")),
                    column_mappings_json=json.dumps(mapping.get("column_mappings") or {}, ensure_ascii=False),
                    create_by=create_by or "",
                    create_by_name=create_by_name or "",
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )

    async def soft_delete_object_type_async(
        self, object_type_name: str, update_by: str = "", update_by_name: str = ""
    ) -> None:
        name = _text(object_type_name)
        if not name:
            return
        async with session_scope() as session:
            await session.execute(
                update(ChatDataOntologyObjectType)
                .where(ChatDataOntologyObjectType.object_type_name == name, ChatDataOntologyObjectType.delete_flag == 0)
                .values(delete_flag=1, update_by=update_by or "", update_by_name=update_by_name or "")
            )

    async def get_objects_by_ids_async(self, object_type_ids: list[int]) -> dict[int, dict[str, Any]]:
        """按 ID 批量读对象类型（仅活跃行），校验器与关系类型行补对象类型名用。"""
        ids = {int(oid) for oid in object_type_ids if oid}
        if not ids:
            return {}
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectType).where(
                            ChatDataOntologyObjectType.id.in_(ids), ChatDataOntologyObjectType.delete_flag == 0
                        )
                    )
                )
                .scalars()
                .all()
            )
        return {int(row.id): _object_type_row_to_dict(row, include_audit=False) for row in rows}

    async def list_object_type_names_async(self) -> set[str]:
        """全部活跃对象类型 API 名集合（successor 存在性校验用）。"""
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectType.object_type_name).where(
                            ChatDataOntologyObjectType.delete_flag == 0
                        )
                    )
                )
                .scalars()
                .all()
            )
        return {_text(row) for row in rows}

    async def find_mapping_owners_async(
        self,
        mappings: list[dict[str, Any]],
        exclude_object_type_id: int | None = None,
    ) -> dict[str, str]:
        """查映射键已被其他活跃对象类型占用的映射（「一源一对象类型」友好预检，唯一索引兜底）。

        返回 {semantic_config_id: object_type_name}；exclude_object_type_id 用于编辑自身时排除。
        """
        config_ids = {_text(mapping.get("semantic_config_id")) for mapping in mappings} - {""}
        if not config_ids:
            return {}
        stmt = (
            select(ChatDataOntologyObjectTypeMapping, ChatDataOntologyObjectType.object_type_name)
            .join(
                ChatDataOntologyObjectType,
                ChatDataOntologyObjectType.id == ChatDataOntologyObjectTypeMapping.object_type_id,
            )
            .where(
                ChatDataOntologyObjectTypeMapping.semantic_config_id.in_(config_ids),
                ChatDataOntologyObjectType.delete_flag == 0,
                ChatDataOntologyObjectTypeMapping.delete_flag == 0,
            )
        )
        if exclude_object_type_id is not None:
            stmt = stmt.where(ChatDataOntologyObjectTypeMapping.object_type_id != int(exclude_object_type_id))
        async with session_scope() as session:
            rows = (await session.execute(stmt)).all()
        return {_text(row.semantic_config_id): _text(object_type_name) for row, object_type_name in rows}

    @staticmethod
    def _binding_row_to_dict(row: ChatDataOntologyObjectTypeMapping, object_type_name: Any) -> dict[str, Any]:
        return {
            "semantic_config_id": _text(row.semantic_config_id),
            "object_type_id": int(row.object_type_id),
            "object_type_name": _text(object_type_name),
            "column_mappings": _parse_json(row.column_mappings_json, dict),
        }

    async def find_bindings_for_configs_async(self, config_ids: list[str]) -> list[dict[str, Any]]:
        """按语义模型 ID 集查活跃绑定（悬空绑定守护用）。

        返回 [{semantic_config_id, object_type_id, object_type_name, column_mappings}]；
        只统计对象类型仍活跃（delete_flag=0）的映射——对象类型已删的墓碑映射不阻止模型删除。
        """
        ids = sorted({_text(item) for item in config_ids} - {""})
        if not ids:
            return []
        stmt = (
            select(ChatDataOntologyObjectTypeMapping, ChatDataOntologyObjectType.object_type_name)
            .join(
                ChatDataOntologyObjectType,
                ChatDataOntologyObjectType.id == ChatDataOntologyObjectTypeMapping.object_type_id,
            )
            .where(
                ChatDataOntologyObjectTypeMapping.semantic_config_id.in_(ids),
                ChatDataOntologyObjectType.delete_flag == 0,
                ChatDataOntologyObjectTypeMapping.delete_flag == 0,
            )
            .order_by(ChatDataOntologyObjectTypeMapping.semantic_config_id.asc())
        )
        async with session_scope() as session:
            rows = (await session.execute(stmt)).all()
        return [self._binding_row_to_dict(row, object_type_name) for row, object_type_name in rows]

    async def find_bindings_for_group_async(self, group_id: str) -> list[dict[str, Any]]:
        """按数据源组查活跃绑定（数据源组删除守护用，一次 join 带出组内全部绑定）。

        语义模型侧限定该组活跃模型；对象类型/映射侧限定活跃。返回结构同 find_bindings_for_configs_async。
        """
        gid = _text(group_id)
        if not gid:
            return []
        stmt = (
            select(ChatDataOntologyObjectTypeMapping, ChatDataOntologyObjectType.object_type_name)
            .join(
                SemanticConfig,
                SemanticConfig.semantic_config_id == ChatDataOntologyObjectTypeMapping.semantic_config_id,
            )
            .join(
                ChatDataOntologyObjectType,
                ChatDataOntologyObjectType.id == ChatDataOntologyObjectTypeMapping.object_type_id,
            )
            .where(
                SemanticConfig.group_id == gid,
                SemanticConfig.delete_flag == 0,
                ChatDataOntologyObjectType.delete_flag == 0,
                ChatDataOntologyObjectTypeMapping.delete_flag == 0,
            )
            .order_by(ChatDataOntologyObjectTypeMapping.semantic_config_id.asc())
        )
        async with session_scope() as session:
            rows = (await session.execute(stmt)).all()
        return [self._binding_row_to_dict(row, object_type_name) for row, object_type_name in rows]

    async def list_object_type_mappings_async(self, object_type_id: int) -> list[dict[str, Any]]:
        """列出对象类型的全部活跃映射（映射视图，按语义配置ID排序）。"""
        oid = int(object_type_id)
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectTypeMapping)
                        .where(
                            ChatDataOntologyObjectTypeMapping.object_type_id == oid,
                            ChatDataOntologyObjectTypeMapping.delete_flag == 0,
                        )
                        .order_by(ChatDataOntologyObjectTypeMapping.semantic_config_id.asc())
                    )
                )
                .scalars()
                .all()
            )
        return [_mapping_row_to_dict(row) for row in rows]

    async def count_mappings_by_object_type_ids_async(self, object_type_ids: list[int]) -> dict[int, int]:
        """按对象类型批量统计活跃映射数（列表页绑定计数）。"""
        ids = {int(oid) for oid in object_type_ids if oid}
        if not ids:
            return {}
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(ChatDataOntologyObjectTypeMapping.object_type_id, func.count())
                    .where(
                        ChatDataOntologyObjectTypeMapping.object_type_id.in_(ids),
                        ChatDataOntologyObjectTypeMapping.delete_flag == 0,
                    )
                    .group_by(ChatDataOntologyObjectTypeMapping.object_type_id)
                )
            ).all()
        return {int(object_type_id): int(count) for object_type_id, count in rows}

    async def delete_object_type_mappings_async(self, object_type_id: int) -> None:
        """软删对象类型全部映射（对象类型删除时级联；映射无独立生命周期，软删行置 delete_flag=id）。"""
        async with session_scope() as session:
            await session.execute(
                update(ChatDataOntologyObjectTypeMapping)
                .where(
                    ChatDataOntologyObjectTypeMapping.object_type_id == int(object_type_id),
                    ChatDataOntologyObjectTypeMapping.delete_flag == 0,
                )
                .values(delete_flag=ChatDataOntologyObjectTypeMapping.id)
            )

    async def load_injection_bundle_async(self, semantic_config_ids: list[str]) -> dict[str, Any]:
        """按启用语义模型集合一次性装载本体注入数据（消费侧专用，单事务四次查询）。

        返回 {"object_types", "properties", "relation_types", "mappings"}：
        - 对象类型：绑定到给定语义模型、活跃、状态 certified/experimental；
        - 属性：上述对象类型的全部活跃属性；
        - 关系类型：两端对象类型都在上述集合内、状态 certified/experimental 的关系类型
          （单端在场无法导航不注入；draft/deprecated 不进 AI 目录，对齐对象类型侧口径）；
        - 映射：绑定给定语义模型的活跃映射行（渲染侧按通过过滤的对象类型归属裁剪）。
        """
        empty: dict[str, Any] = {"object_types": [], "properties": [], "relation_types": [], "mappings": []}
        ids = sorted({_text(item) for item in semantic_config_ids} - {""})
        if not ids:
            return empty
        async with session_scope() as session:
            mapping_rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectTypeMapping).where(
                            ChatDataOntologyObjectTypeMapping.semantic_config_id.in_(ids),
                            ChatDataOntologyObjectTypeMapping.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .all()
            )
            candidate_ids = sorted({int(row.object_type_id) for row in mapping_rows})
            if not candidate_ids:
                return empty
            object_type_rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyObjectType).where(
                            ChatDataOntologyObjectType.id.in_(candidate_ids),
                            ChatDataOntologyObjectType.delete_flag == 0,
                            ChatDataOntologyObjectType.lifecycle_status.in_(("certified", "experimental")),
                        )
                    )
                )
                .scalars()
                .all()
            )
            kept_ids = {int(row.id) for row in object_type_rows}
            if not kept_ids:
                return empty
            property_rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyProperty).where(
                            ChatDataOntologyProperty.object_type_id.in_(kept_ids),
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
                        select(ChatDataOntologyRelationType).where(
                            ChatDataOntologyRelationType.delete_flag == 0,
                            ChatDataOntologyRelationType.lifecycle_status.in_(("certified", "experimental")),
                            ChatDataOntologyRelationType.from_object_type_id.in_(kept_ids),
                            ChatDataOntologyRelationType.to_object_type_id.in_(kept_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
        return {
            "object_types": [_object_type_row_to_dict(row, include_audit=False) for row in object_type_rows],
            "properties": [_property_row_to_dict(row) for row in property_rows],
            "relation_types": [_relation_row_to_dict(row) for row in relation_rows],
            "mappings": [_mapping_row_to_dict(row) for row in mapping_rows],
        }

    # ---------- 属性 ----------

    async def list_properties_async(self, object_type_id: int) -> list[dict[str, Any]]:
        """列出对象类型下全部活跃属性（按属性名排序）。"""
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyProperty)
                        .where(
                            ChatDataOntologyProperty.object_type_id == int(object_type_id),
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
        object_type_id: int,
        items: list[dict[str, Any]],
        *,
        allow_delete: bool,
        create_by: str = "",
        create_by_name: str = "",
        update_by: str = "",
        update_by_name: str = "",
    ) -> list[dict[str, Any]]:
        """全量替换对象类型属性集：items 为标准化属性字典（service 层校验后）。

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
                            ChatDataOntologyProperty.object_type_id == int(object_type_id),
                        )
                    )
                )
                .scalars()
                .all()
            )
            rows_by_name = {_text(row.property_name): row for row in rows}
            self._upsert_property_rows(
                session, object_type_id, incoming, rows_by_name, create_by, create_by_name, update_by, update_by_name
            )
            if allow_delete:
                self._soft_delete_missing_properties(rows_by_name, incoming, update_by, update_by_name)
            fresh = (
                (
                    await session.execute(
                        select(ChatDataOntologyProperty)
                        .where(
                            ChatDataOntologyProperty.object_type_id == int(object_type_id),
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
        object_type_id: int,
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
                        object_type_id=int(object_type_id),
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

    # ---------- 关系类型 ----------

    async def list_relations_async(
        self, keyword: str = "", from_object_type_id: int | None = None, to_object_type_id: int | None = None
    ) -> list[dict[str, Any]]:
        """列出关系类型（delete_flag=0），keyword 按名称/描述/方向展示名模糊过滤。"""
        async with session_scope() as session:
            stmt = select(ChatDataOntologyRelationType).where(ChatDataOntologyRelationType.delete_flag == 0)
            keyword_text = _text(keyword)
            if keyword_text:
                like = f"%{keyword_text}%"
                stmt = stmt.where(
                    (ChatDataOntologyRelationType.relation_type_name.like(like))
                    | (ChatDataOntologyRelationType.description_text.like(like))
                    | (ChatDataOntologyRelationType.from_display_name.like(like))
                    | (ChatDataOntologyRelationType.to_display_name.like(like))
                )
            if from_object_type_id is not None:
                stmt = stmt.where(ChatDataOntologyRelationType.from_object_type_id == int(from_object_type_id))
            if to_object_type_id is not None:
                stmt = stmt.where(ChatDataOntologyRelationType.to_object_type_id == int(to_object_type_id))
            stmt = stmt.order_by(ChatDataOntologyRelationType.relation_type_name.asc())
            rows = (await session.execute(stmt)).scalars().all()
        return [_relation_row_to_dict(row) for row in rows]

    async def get_relation_row_async(
        self, relation_type_name: str, *, include_deleted: bool = False
    ) -> dict[str, Any] | None:
        name = _text(relation_type_name)
        if not name:
            return None
        async with session_scope() as session:
            stmt = select(ChatDataOntologyRelationType).where(ChatDataOntologyRelationType.relation_type_name == name)
            if not include_deleted:
                stmt = stmt.where(ChatDataOntologyRelationType.delete_flag == 0)
            row = (await session.execute(stmt)).scalars().first()
        return _relation_row_to_dict(row) if row is not None else None

    async def upsert_relation_async(
        self,
        relation_type_name: str,
        payload: dict[str, Any],
        *,
        allow_overwrite: bool,
        create_by: str = "",
        create_by_name: str = "",
        update_by: str = "",
        update_by_name: str = "",
    ) -> dict[str, Any] | None:
        """按 relation_type_name upsert 关系类型（软删行复活复用，语义同对象类型 upsert）。"""
        name = _text(relation_type_name)
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataOntologyRelationType).where(
                            ChatDataOntologyRelationType.relation_type_name == name
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                session.add(
                    ChatDataOntologyRelationType(
                        relation_type_name=name,
                        delete_flag=0,
                        create_by=create_by or "",
                        create_by_name=create_by_name or "",
                        update_by=update_by or "",
                        update_by_name=update_by_name or "",
                        **_to_row_fields(payload),
                    )
                )
            else:
                if int(row.delete_flag or 0) == 1:
                    row.delete_flag = 0
                elif not allow_overwrite:
                    return None
                for field, value in _to_row_fields(payload).items():
                    setattr(row, field, value)
                row.update_by = update_by or ""
                row.update_by_name = update_by_name or ""
        return self._build_relation_view(name, payload)

    async def soft_delete_relation_async(
        self, relation_type_name: str, update_by: str = "", update_by_name: str = ""
    ) -> None:
        name = _text(relation_type_name)
        if not name:
            return
        async with session_scope() as session:
            await session.execute(
                update(ChatDataOntologyRelationType)
                .where(
                    ChatDataOntologyRelationType.relation_type_name == name,
                    ChatDataOntologyRelationType.delete_flag == 0,
                )
                .values(delete_flag=1, update_by=update_by or "", update_by_name=update_by_name or "")
            )

    async def list_relations_for_object_type_async(self, object_type_id: int) -> list[dict[str, Any]]:
        """对象类型作为 from 或 to 参与的全部活跃关系类型（级联与悬空检查用）。"""
        oid = int(object_type_id)
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataOntologyRelationType).where(
                            or_(
                                ChatDataOntologyRelationType.from_object_type_id == oid,
                                ChatDataOntologyRelationType.to_object_type_id == oid,
                            ),
                            ChatDataOntologyRelationType.delete_flag == 0,
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
        """批量设置关系类型状态（对象类型状态级联同步）。"""
        ids = {int(rid) for rid in relation_ids if rid}
        if not ids:
            return
        async with session_scope() as session:
            await session.execute(
                update(ChatDataOntologyRelationType)
                .where(ChatDataOntologyRelationType.id.in_(ids), ChatDataOntologyRelationType.delete_flag == 0)
                .values(lifecycle_status=_text(status), update_by=update_by or "", update_by_name=update_by_name or "")
            )

    # ---------- 视图构造 ----------

    def _build_relation_view(self, relation_type_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "relation_type_name": relation_type_name,
            **payload,
            "from_columns": _parse_json(payload.get("from_columns_json"), list),
            "to_columns": _parse_json(payload.get("to_columns_json"), list),
        }

    def _property_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        """属性行内容字段（不含 object_type_id/property_name/审计字段；键=ORM 行字段名）。"""
        return {
            "display_name": _text(item.get("display_name")),
            "description_text": _text(item.get("description")),
            "value_type": _text(item.get("value_type")) or "String",
            "enum_ref": _text(item.get("enum_ref")),
            "render_hints_json": json.dumps(_parse_json(item.get("render_hints_json"), dict), ensure_ascii=False),
            "is_identifier": 1 if item.get("is_identifier") else 0,
            "is_title": 1 if item.get("is_title") else 0,
        }
