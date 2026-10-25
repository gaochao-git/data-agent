"""本体对象集合（Object Set）服务：static 主键列表的保存与回源加载。

对齐 Palantir Object Set 的 static/temporary 子集（`object-backend/overview.md:53-63`）：
- static：保存主键列表，输入数据变化不影响集合成员；
- temporary：仅创建者使用，24 小时后过期（`api/v2/.../create-temporary-object-set`）；
- 不实现 dynamic（保存过滤条件）与 permanent 共享，边界写在 README。

对象集合不复制属性：保存时只校验主键格式与数量，加载时按主键回源投影，缺失对象
跳过而不是报错（static 集合可能比源数据活得久）。
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta
from typing import Any

from apps.chat_data_app.daos.ontology_action_object_dao import OntologyActionObjectDao
from apps.chat_data_app.daos.ontology_object_set_dao import OntologyObjectSetDao
from apps.chat_data_app.services.ontology import ontology_service
from apps.chat_data_app.services.ontology.errors import (
    OntologyAlreadyExistsError,
    OntologyInvalidError,
    OntologyNotFoundError,
)
from apps.chat_data_app.services.ontology.object_projection import (
    ObjectProjectionError,
    read_object_projection,
    search_object_projection,
)
from apps.chat_data_app.services.ontology.ontology_validator import validate_api_name

MAX_OBJECT_SET_KEYS = 100
TEMPORARY_TTL_HOURS = 24
_LOADABLE_STATUSES = {"certified", "experimental"}
_LOAD_CONCURRENCY = 8


def _text(value: Any) -> str:
    return str(value or "").strip()


def _expires_at(temporary: bool) -> datetime | None:
    return datetime.now() + timedelta(hours=TEMPORARY_TTL_HOURS) if temporary else None


def build_default_object_set_name(object_type_name: str) -> str:
    """生成默认集合名（API 名口径：字母数字下划线），供智能体未显式命名时使用。"""
    safe_type = "".join(char for char in _text(object_type_name) if char.isalnum() or char == "_")[:60] or "objects"
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"oset_{safe_type}_{stamp}_{secrets.token_hex(2)}"


def _identifier_properties(detail: dict[str, Any]) -> list[str]:
    return [
        _text(prop.get("property_name"))
        for prop in detail.get("properties") or []
        if isinstance(prop, dict) and bool(prop.get("is_identifier")) and _text(prop.get("property_name"))
    ]


def _normalize_object_keys(raw: Any, *, object_type_name: str, member_datasource_id: str) -> list[str]:
    """把 local_key 或完整对象身份归一化为去重后的 local_key 列表。"""
    keys: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        text = _text(item)
        if not text:
            continue
        local_key = _local_key(text, object_type_name=object_type_name, member_datasource_id=member_datasource_id)
        if local_key in seen:
            continue
        seen.add(local_key)
        keys.append(local_key)
    if not keys:
        raise OntologyInvalidError("对象集合至少需要一个对象主键")
    if len(keys) > MAX_OBJECT_SET_KEYS:
        raise OntologyInvalidError(f"对象集合单次最多 {MAX_OBJECT_SET_KEYS} 个对象主键")
    return keys


def _local_key(text: str, *, object_type_name: str, member_datasource_id: str) -> str:
    """解析 local_key；完整身份必须与对象类型/机构一致，避免跨机构串对象。"""
    if ":" not in text:
        return text
    parts = text.split(":")
    if len(parts) != 3:
        raise OntologyInvalidError(f"对象主键格式不合法（应为 local_key 或 member:object_type:local_key）: {text}")
    key_member, key_type, local_key = parts
    if key_member != member_datasource_id or key_type != object_type_name:
        raise OntologyInvalidError("对象集合主键与目标对象类型/机构不一致: " + text)
    if not local_key:
        raise OntologyInvalidError(f"对象主键缺少 local_key: {text}")
    return local_key


def _qualified_key(member_datasource_id: str, object_type_name: str, local_key: str) -> str:
    return f"{member_datasource_id}:{object_type_name}:{local_key}"


def _local_key_of(object_key: str, *, object_type_name: str, member_datasource_id: str) -> str:
    prefix = f"{member_datasource_id}:{object_type_name}:"
    return object_key[len(prefix) :] if object_key.startswith(prefix) else object_key


async def _read_action_created_objects(
    detail: dict[str, Any],
    *,
    member_datasource_id: str,
    local_keys: list[str],
    action_object_dao: OntologyActionObjectDao,
) -> list[dict[str, Any]]:
    """动作对象按主键逐条读取（动作对象不走源表投影）。"""
    objects: list[dict[str, Any]] = []
    for local_key in local_keys:
        stored = await action_object_dao.get_object_async(int(detail.get("id") or 0), local_key)
        if stored is not None:
            objects.append(
                {
                    "object_type": _text(detail.get("object_type_name")),
                    "object_key": _qualified_key(
                        member_datasource_id, _text(detail.get("object_type_name")), local_key
                    ),
                    "properties": stored.get("properties") or {},
                }
            )
    return objects


async def _read_single_identifier_objects(
    detail: dict[str, Any],
    *,
    member_datasource_id: str,
    local_keys: list[str],
    user_id: str,
    username: str,
) -> list[dict[str, Any]]:
    """单标识属性对象类型：一次 IN 查询取回整页对象，再按集合顺序排列。"""
    identifier = _identifier_properties(detail)[0]
    data = await search_object_projection(
        _text(detail.get("object_type_name")),
        member_datasource_ids=[member_datasource_id],
        filters=[{"property": identifier, "operator": "in", "values": local_keys, "value": ""}],
        limit=len(local_keys),
        user_id=user_id,
        username=username,
    )
    by_local_key = {
        _local_key_of(
            _text(item.get("object_key")),
            object_type_name=_text(detail.get("object_type_name")),
            member_datasource_id=member_datasource_id,
        ): item
        for item in data.get("objects") or []
        if isinstance(item, dict)
    }
    return [by_local_key[local_key] for local_key in local_keys if local_key in by_local_key]


async def _read_each_object(
    detail: dict[str, Any],
    *,
    member_datasource_id: str,
    local_keys: list[str],
    user_id: str,
    username: str,
) -> list[dict[str, Any]]:
    """复合主键/兜底路径：逐对象读取，缺失对象跳过。"""
    object_type_name = _text(detail.get("object_type_name"))
    semaphore = asyncio.Semaphore(_LOAD_CONCURRENCY)

    async def load(local_key: str) -> dict[str, Any] | None:
        async with semaphore:
            try:
                return await read_object_projection(
                    object_type_name,
                    _qualified_key(member_datasource_id, object_type_name, local_key),
                    user_id=user_id,
                    username=username,
                )
            except ObjectProjectionError:
                return None

    loaded = await asyncio.gather(*(load(local_key) for local_key in local_keys))
    return [item for item in loaded if item is not None]


async def load_objects_by_keys(
    detail: dict[str, Any],
    *,
    member_datasource_id: str,
    local_keys: list[str],
    user_id: str = "",
    username: str = "",
    action_object_dao: OntologyActionObjectDao | None = None,
) -> list[dict[str, Any]]:
    """按主键回源加载对象（不含 links/派生值的批量语义；动作对象走动作对象表）。"""
    if not local_keys:
        return []
    if (_text(detail.get("write_mode")) or "read_only") == "action_created":
        return await _read_action_created_objects(
            detail,
            member_datasource_id=member_datasource_id,
            local_keys=local_keys,
            action_object_dao=action_object_dao or OntologyActionObjectDao(),
        )
    if len(_identifier_properties(detail)) == 1:
        return await _read_single_identifier_objects(
            detail,
            member_datasource_id=member_datasource_id,
            local_keys=local_keys,
            user_id=user_id,
            username=username,
        )
    return await _read_each_object(
        detail,
        member_datasource_id=member_datasource_id,
        local_keys=local_keys,
        user_id=user_id,
        username=username,
    )


class OntologyObjectSetService:
    """对象集合保存/列表/读取/删除；对象属性在加载时回源取得。"""

    def __init__(
        self,
        dao: OntologyObjectSetDao | None = None,
        action_object_dao: OntologyActionObjectDao | None = None,
    ) -> None:
        self._dao = dao or OntologyObjectSetDao()
        self._action_object_dao = action_object_dao or OntologyActionObjectDao()

    async def _object_type_detail(self, object_type_name: str) -> dict[str, Any]:
        detail = await ontology_service.get_object_type_async(object_type_name)
        if detail is None:
            raise OntologyNotFoundError(f"本体对象类型不存在: {object_type_name}")
        if _text(detail.get("status")) not in _LOADABLE_STATUSES:
            raise OntologyInvalidError("只有 certified/experimental 对象类型可以创建或加载对象集合")
        return detail

    async def list_object_sets_async(
        self,
        object_type_name: str = "",
        *,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        object_type_id = None
        if object_type_name:
            detail = await self._object_type_detail(object_type_name)
            object_type_id = int(detail["id"])
        return await self._dao.list_object_sets_async(object_type_id, include_expired=include_expired)

    async def get_object_set_async(self, object_type_name: str, set_name: str) -> dict[str, Any]:
        detail = await self._object_type_detail(object_type_name)
        item = await self._dao.get_object_set_async(set_name)
        if item is None or int(item.get("object_type_id") or 0) != int(detail["id"]):
            raise OntologyNotFoundError(f"对象集合不存在: {set_name}")
        if item.get("expired"):
            raise OntologyNotFoundError(f"对象集合已过期（临时集合有效期 {TEMPORARY_TTL_HOURS} 小时）: {set_name}")
        return item

    async def upsert_object_set_async(
        self,
        object_type_name: str,
        set_name: str,
        payload: dict[str, Any],
        *,
        allow_overwrite: bool = False,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        validate_api_name(set_name, "Object Set")
        detail = await self._object_type_detail(object_type_name)
        member_datasource_id = _text(payload.get("member_datasource_id"))
        if not member_datasource_id:
            raise OntologyInvalidError("对象集合必须指定 member_datasource_id")
        object_keys = _normalize_object_keys(
            payload.get("object_keys"),
            object_type_name=object_type_name,
            member_datasource_id=member_datasource_id,
        )
        temporary = bool(payload.get("temporary", True))
        stored = await self._dao.upsert_object_set_async(
            set_name,
            {
                "display_name": _text(payload.get("display_name")),
                "description": _text(payload.get("description")),
                "object_type_id": int(detail["id"]),
                "object_type_name": object_type_name,
                "member_datasource_id": member_datasource_id,
                "object_keys": object_keys,
                "temporary": temporary,
                "expires_at": _expires_at(temporary),
            },
            allow_overwrite=allow_overwrite,
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        if stored is None:
            raise OntologyAlreadyExistsError(f"对象集合已存在: {set_name}（如需覆盖请显式确认）")
        return stored

    async def delete_object_set_async(
        self,
        object_type_name: str,
        set_name: str,
        *,
        operator: str = "",
        operator_name: str = "",
    ) -> None:
        await self.get_object_set_async(object_type_name, set_name)
        deleted = await self._dao.soft_delete_object_set_async(
            set_name, update_by=operator, update_by_name=operator_name
        )
        if not deleted:
            raise OntologyNotFoundError(f"对象集合不存在: {set_name}")

    async def load_object_set_objects_async(
        self,
        object_type_name: str,
        set_name: str,
        *,
        limit: int = 20,
        offset: int = 0,
        user_id: str = "",
        username: str = "",
    ) -> dict[str, Any]:
        """按集合顺序分页加载对象属性；缺失对象跳过并计数。"""
        item = await self.get_object_set_async(object_type_name, set_name)
        detail = await self._object_type_detail(object_type_name)
        object_keys = [str(key) for key in item.get("object_keys") or []]
        page_keys = object_keys[offset : offset + limit]
        objects = await load_objects_by_keys(
            detail,
            member_datasource_id=_text(item.get("member_datasource_id")),
            local_keys=page_keys,
            user_id=user_id,
            username=username,
            action_object_dao=self._action_object_dao,
        )
        return {
            "set_name": _text(item.get("set_name")),
            "display_name": _text(item.get("display_name")),
            "object_type": object_type_name,
            "member_datasource_id": _text(item.get("member_datasource_id")),
            "objects": objects,
            "total": len(object_keys),
            "returned": len(objects),
            "missing_count": len(page_keys) - len(objects),
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < len(object_keys),
            "expires_at": _text(item.get("expires_at")),
        }


__all__ = [
    "MAX_OBJECT_SET_KEYS",
    "TEMPORARY_TTL_HOURS",
    "OntologyObjectSetService",
    "build_default_object_set_name",
    "load_objects_by_keys",
]
