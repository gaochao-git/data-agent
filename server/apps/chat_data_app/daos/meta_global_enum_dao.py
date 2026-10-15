"""全局枚举 DAO：chat_data_meta_global_enum 的读写与引用方粗筛。

全局枚举是全系统枚举值域唯一权威（跨数据源、跨环境共享），数据字典列通过
dict_json 中的 enum_ref 引用。唯一键为 (business_domain, enum_name)：不同
业务域的同名枚举互相隔离。删除保护所需的引用计数遵循「禁止数据库原生 JSON
语法」约束：LIKE 字符串粗筛 + 应用层 JSON 解析精确确认（两步法）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_, select, update
from utils.logger import get_logger
from utils.models import ChatDataMetaGlobalEnum, DatasourceColumnDict, DatasourceGroup

from .dao_session import session_scope
from .datasource_dao_helpers import _normalize_column_enum_values

logger = get_logger(__name__)


def _normalize_enum_items(raw_items: Any) -> list[dict[str, str]]:
    """标准化全局枚举值列表（最多 100 个，按 value 去重）。"""
    return _normalize_column_enum_values(raw_items)


def _parse_enum_json(value: Any) -> list[dict[str, str]]:
    if isinstance(value, list):
        return _normalize_enum_items(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            logger.warning("解析全局枚举 JSON 失败，返回空列表")
            return []
        return _normalize_enum_items(parsed if isinstance(parsed, list) else [])
    return []


def _format_datetime(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _row_to_dict(row: ChatDataMetaGlobalEnum) -> dict[str, Any]:
    return {
        "business_domain": str(row.business_domain or "").strip(),
        "enum_name": str(row.enum_name or "").strip(),
        "enum_desc": str(row.enum_desc or ""),
        "enum_items": _parse_enum_json(row.enum_json),
        "create_time": _format_datetime(row.create_time),
        "update_time": _format_datetime(row.update_time),
        "create_by": str(row.create_by or ""),
        "create_by_name": str(row.create_by_name or ""),
        "update_by": str(row.update_by or ""),
        "update_by_name": str(row.update_by_name or ""),
    }


class MetaGlobalEnumDao:
    """全局枚举表访问对象。"""

    async def list_enums_async(self, keyword: str = "", business_domain: str | None = None) -> list[dict[str, Any]]:
        """列出全局枚举（delete_flag=0）。

        business_domain 为 None 时不按域过滤（全部域）；空串表示仅通用域；
        非空串按指定业务域过滤。keyword 非空时按名称/说明模糊过滤。
        """
        async with session_scope() as session:
            stmt = select(ChatDataMetaGlobalEnum).where(ChatDataMetaGlobalEnum.delete_flag == 0)
            if business_domain is not None:
                stmt = stmt.where(ChatDataMetaGlobalEnum.business_domain == str(business_domain).strip())
            keyword_text = str(keyword or "").strip()
            if keyword_text:
                like = f"%{keyword_text}%"
                stmt = stmt.where(
                    (ChatDataMetaGlobalEnum.enum_name.like(like))
                    | (ChatDataMetaGlobalEnum.enum_desc.like(like))
                )
            stmt = stmt.order_by(ChatDataMetaGlobalEnum.business_domain.asc(), ChatDataMetaGlobalEnum.enum_name.asc())
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_dict(row) for row in rows]

    async def get_enum_by_name_async(self, enum_name: str, business_domain: str = "") -> dict[str, Any] | None:
        """按 (business_domain, enum_name) 读取单个全局枚举（仅未删除），不存在返回 None。"""
        name = str(enum_name or "").strip()
        if not name:
            return None
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataMetaGlobalEnum).where(
                            ChatDataMetaGlobalEnum.business_domain == str(business_domain or "").strip(),
                            ChatDataMetaGlobalEnum.enum_name == name,
                            ChatDataMetaGlobalEnum.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .first()
            )
        return _row_to_dict(row) if row is not None else None

    async def list_enum_domains_async(self) -> list[str]:
        """列出已有全局枚举的业务域去重清单（供前端域选择器）。"""
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatDataMetaGlobalEnum.business_domain)
                        .where(ChatDataMetaGlobalEnum.delete_flag == 0)
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
        return sorted({str(row or "").strip() for row in rows if str(row or "").strip()})

    async def upsert_enum_async(
        self,
        business_domain: str,
        enum_name: str,
        enum_desc: str,
        enum_items: list[dict[str, str]],
        *,
        allow_overwrite: bool,
        create_by: str = "",
        create_by_name: str = "",
        update_by: str = "",
        update_by_name: str = "",
    ) -> dict[str, Any] | None:
        """按 (business_domain, enum_name) upsert，返回写入后的内容。

        - 无行（含从未创建）→ 插入新行；
        - 软删行 → 复活复用原行（翻回 delete_flag=0 并更新内容），不算覆盖；
        - 活跃行 → 仅 allow_overwrite=True 时更新内容，否则返回 None（由
          service 层拒绝，避免新建/存为模板时静默覆盖已有枚举）。
        """
        domain = str(business_domain or "").strip()
        name = str(enum_name or "").strip()
        normalized_items = _normalize_enum_items(enum_items)
        content = _build_enum_content(enum_desc, normalized_items)
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(ChatDataMetaGlobalEnum).where(
                            ChatDataMetaGlobalEnum.business_domain == domain,
                            ChatDataMetaGlobalEnum.enum_name == name,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                session.add(
                    ChatDataMetaGlobalEnum(
                        business_domain=domain,
                        enum_name=name,
                        delete_flag=0,
                        create_by=create_by or "",
                        create_by_name=create_by_name or "",
                        **content,
                        update_by=update_by or "",
                        update_by_name=update_by_name or "",
                    )
                )
                return _build_enum_content_view(domain, name, content, normalized_items)
            if int(row.delete_flag or 0) == 1:
                row.delete_flag = 0
            elif not allow_overwrite:
                return None
            _apply_enum_row_content(row, content, update_by, update_by_name)
        return _build_enum_content_view(domain, name, content, normalized_items)

    async def soft_delete_enum_async(
        self, enum_name: str, business_domain: str = "", update_by: str = "", update_by_name: str = ""
    ) -> None:
        name = str(enum_name or "").strip()
        if not name:
            return
        async with session_scope() as session:
            await session.execute(
                update(ChatDataMetaGlobalEnum)
                .where(
                    ChatDataMetaGlobalEnum.business_domain == str(business_domain or "").strip(),
                    ChatDataMetaGlobalEnum.enum_name == name,
                    ChatDataMetaGlobalEnum.delete_flag == 0,
                )
                .values(
                    delete_flag=1,
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )

    async def find_column_dict_refs_async(self, enum_name: str, business_domain: str = "") -> list[dict[str, Any]]:
        """查找引用指定全局枚举的数据字典列。

        两步法：先按 enum_name 做 LIKE 字符串粗筛（禁止数据库原生 JSON 语法），
        再在应用层解析 dict_json 精确确认 enum_ref 解析后的 (domain, enum_name)
        与目标一致（enum_ref 格式："enum_name" 或 "business_domain:enum_name"）。
        """
        name = str(enum_name or "").strip()
        if not name:
            return []
        domain = str(business_domain or "").strip()
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(DatasourceColumnDict).where(
                            DatasourceColumnDict.dict_json.like(f"%{name}%"),
                            DatasourceColumnDict.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .all()
            )
        refs = [
            {
                "group_id": str(row.group_id or ""),
                "table_name": str(row.table_name or ""),
                "column_name": str(row.column_name or ""),
            }
            for row in rows
            if _row_refs_enum(row, name, domain)
        ]
        group_names = await self._load_group_names({ref["group_id"] for ref in refs})
        for ref in refs:
            ref["group_name"] = group_names.get(ref["group_id"], ref["group_id"])
        return refs

    async def _load_group_names(self, group_ids: set[str]) -> dict[str, str]:
        """批量把数据源组 ID 映射为组名（展示用），缺失或已删组回退由调用方处理。"""
        ids = {gid for gid in group_ids if gid}
        if not ids:
            return {}
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(DatasourceGroup.group_id, DatasourceGroup.group_name).where(
                            DatasourceGroup.group_id.in_(ids),
                            DatasourceGroup.delete_flag == 0,
                        )
                    )
                )
                .all()
            )
        return {str(row.group_id): str(row.group_name or "") for row in rows if row.group_name}

    async def count_column_dict_refs_batch_async(self, targets: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
        """批量统计每个 (domain, enum_name) 的引用数（供列表页展示引用数量）。

        与 find_column_dict_refs_async 相同两步法，但对整组目标枚举名只做一次
        OR LIKE 粗筛查询，应用层解析后按 (domain, enum_name) 计数，避免逐行查询。
        """
        unique_names = sorted({name for _, name in targets if name})
        if not unique_names:
            return {}
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(DatasourceColumnDict).where(
                            or_(*[DatasourceColumnDict.dict_json.like(f"%{name}%") for name in unique_names]),
                            DatasourceColumnDict.delete_flag == 0,
                        )
                    )
                )
                .scalars()
                .all()
            )
        target_set = {(domain, name) for domain, name in targets if name}
        counts: dict[tuple[str, str], int] = dict.fromkeys(target_set, 0)
        for row in rows:
            try:
                payload = json.loads(str(row.dict_json or ""))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            ref = _split_enum_ref(str(payload.get("enum_ref") or ""))
            if ref in target_set:
                counts[ref] += 1
        return counts


def _split_enum_ref(enum_ref: str) -> tuple[str, str]:
    """拆分 enum_ref 为 (business_domain, enum_name)。

    格式：通用域 "enum_name"；业务域 "business_domain:enum_name"。
    enum_name 仅允许 [A-Za-z0-9_]，":" 分隔符无歧义；取最后一个冒号分段，
    未含冒号视为通用域。
    """
    text = str(enum_ref or "").strip()
    if ":" in text:
        domain, _, name = text.rpartition(":")
        return domain.strip(), name.strip()
    return "", text


def _build_enum_content(enum_desc: str, enum_items: list[dict[str, str]]) -> dict[str, str]:
    """构造行内容字段（enum_desc/enum_json），供插入与更新共用。"""
    return {
        "enum_desc": str(enum_desc or "").strip()[:512],
        "enum_json": json.dumps(enum_items, ensure_ascii=False),
    }


def _apply_enum_row_content(row: Any, content: dict[str, str], update_by: str, update_by_name: str) -> None:
    """把内容字段与操作人写入既有行（复活与覆盖共用）。"""
    row.enum_desc = content["enum_desc"]
    row.enum_json = content["enum_json"]
    row.update_by = update_by or ""
    row.update_by_name = update_by_name or ""


def _build_enum_content_view(
    domain: str, name: str, content: dict[str, str], enum_items: list[dict[str, str]]
) -> dict[str, Any]:
    """构造 upsert 返回的内容视图（与行内容字段同源）。"""
    return {
        "business_domain": domain,
        "enum_name": name,
        "enum_desc": content["enum_desc"],
        "enum_items": enum_items,
    }


def _row_refs_enum(row: Any, enum_name: str, business_domain: str) -> bool:
    """应用层精确确认列字典行的 enum_ref 是否指向目标枚举（解析失败视为不引用）。"""
    try:
        payload = json.loads(str(row.dict_json or ""))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    ref_domain, ref_name = _split_enum_ref(str(payload.get("enum_ref") or ""))
    return ref_name == enum_name and ref_domain == business_domain
