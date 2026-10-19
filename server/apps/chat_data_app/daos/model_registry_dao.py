"""模型注册表数据访问层：chat_data_model_registry 表读写。

页面化模型管理的持久层（替代 models.json 文件）。DAO 只做读写与持久化映射，
不主动 commit（由 ``get_session`` 退出统一提交）。删除为软删；生产 DB 账号
无 DELETE 权限，软删行请求名改写墓碑名，不占用「供应商+请求名」业务组合。

行按 ``model_key``（注册键，唯一）定位；``model_id`` 是发给上游的请求名，
不做唯一约束——同一请求名可接多个供应商（base_url/key 不同的多行）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select, update

from utils.database import get_session as default_get_session
from utils.models import ChatDataModelRegistry

# 行 -> 业务 dict 的公共列（模型参数集中在 params_json；审计列单独透出供管理端展示）
_REGISTRY_COLUMNS = (
    "model_key",
    "model_id",
    "provider",
    "display_name",
    "base_url",
    "api_key_cipher",
    "params_json",
    "is_default",
    "enabled_flag",
    "remark_text",
    "delete_flag",
)

# 审计列（管理端展示最近操作人与时间，写路径不经过该清单）
_AUDIT_COLUMNS = (
    "create_time",
    "update_time",
    "create_by",
    "create_by_name",
    "update_by",
    "update_by_name",
)


def registry_row_to_dict(row: ChatDataModelRegistry) -> dict[str, Any]:
    """ORM 行转业务 dict（管理端与加载层共用；业务列 + 审计列，字段一一对应列名）。

    时间列转 ISO 字符串：响应体经 json.dumps 序列化，datetime 不可直接编码。
    """
    data: dict[str, Any] = {"id": row.id}
    for column in _REGISTRY_COLUMNS:
        data[column] = getattr(row, column)
    for column in _AUDIT_COLUMNS:
        value = getattr(row, column)
        data[column] = value.isoformat() if isinstance(value, datetime) else value
    return data


class ModelRegistryDao:
    """封装 chat_data_model_registry 表读写。"""

    def __init__(self, session_context: Callable | None = None) -> None:
        self._session_context = session_context or default_get_session

    async def list_rows(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        """返回全部未删除模型（is_default 优先、id 升序）；enabled_only 过滤启用行。"""
        filters: list[Any] = [ChatDataModelRegistry.delete_flag == 0]
        if enabled_only:
            filters.append(ChatDataModelRegistry.enabled_flag == 1)
        async with self._session_context() as db:
            result = await db.execute(
                select(ChatDataModelRegistry)
                .filter(*filters)
                .order_by(ChatDataModelRegistry.is_default.desc(), ChatDataModelRegistry.id.asc())
            )
            return [registry_row_to_dict(row) for row in result.scalars().all()]

    async def get_by_model_key(self, *, model_key: str, include_deleted: bool = False) -> dict[str, Any] | None:
        """按注册键精确读取；include_deleted 用于命中墓碑行（复活场景）。"""
        async with self._session_context() as db:
            query = select(ChatDataModelRegistry).filter_by(model_key=model_key)
            if not include_deleted:
                query = query.filter(ChatDataModelRegistry.delete_flag == 0)
            row = (await db.execute(query.limit(1))).scalar_one_or_none()
            return registry_row_to_dict(row) if row is not None else None

    async def has_active_provider_model(self, *, provider: str, model_id: str, exclude_key: str = "") -> bool:
        """同供应商+同请求名的活跃行是否已存在（exclude_key 用于更新时排除自身）。"""
        filters = [
            ChatDataModelRegistry.provider == provider,
            ChatDataModelRegistry.model_id == model_id,
            ChatDataModelRegistry.delete_flag == 0,
        ]
        if exclude_key:
            filters.append(ChatDataModelRegistry.model_key != exclude_key)
        async with self._session_context() as db:
            row = (await db.execute(select(ChatDataModelRegistry.id).filter(*filters).limit(1))).scalar_one_or_none()
            return row is not None

    async def upsert(
        self,
        *,
        model_key: str,
        fields: dict[str, Any],
        operator_id: str,
        operator_name: str,
    ) -> int:
        """按注册键定位写入行；返回行 id。

        行存在（含软删墓碑）则覆盖写并复位 delete_flag；不存在则插入。
        fields 为 _REGISTRY_COLUMNS 子集（不含 model_key 与审计列，
        model_id 作为请求名随 fields 写入）。api_key_cipher 为空串时
        保持原值不动（编辑未改 key 的场景）。
        """
        async with self._session_context() as db:
            row = (
                await db.execute(select(ChatDataModelRegistry).filter_by(model_key=model_key).limit(1))
            ).scalar_one_or_none()
            if row is None:
                row = ChatDataModelRegistry(
                    model_key=model_key,
                    model_id=model_key,
                    api_key_cipher="",
                    create_by=operator_id,
                    create_by_name=operator_name,
                    update_by=operator_id,
                    update_by_name=operator_name,
                )
                db.add(row)
            row.delete_flag = 0
            for key, value in fields.items():
                if value is None or (key == "api_key_cipher" and not value):
                    # None=未传保持原值（如 enabled）；api_key_cipher 空串=编辑未改 key
                    continue
                setattr(row, key, value)
            row.update_by = operator_id
            row.update_by_name = operator_name
            await db.flush()
            return int(row.id)

    async def set_enabled(self, *, model_key: str, enabled: bool, operator_id: str, operator_name: str) -> bool:
        """切换启停；返回是否存在（未删除）。先查存在性再更新（rowcount 不能判定幂等切换）。"""
        async with self._session_context() as db:
            exists = (
                await db.execute(
                    select(ChatDataModelRegistry.id).filter_by(model_key=model_key, delete_flag=0).limit(1)
                )
            ).first()
            if exists is None:
                return False
            await db.execute(
                update(ChatDataModelRegistry)
                .where(ChatDataModelRegistry.model_key == model_key, ChatDataModelRegistry.delete_flag == 0)
                .values(
                    enabled_flag=1 if enabled else 0,
                    update_by=operator_id,
                    update_by_name=operator_name,
                )
            )
            return True

    async def set_default(self, *, model_key: str, operator_id: str, operator_name: str) -> bool:
        """设唯一默认模型：先清全部默认位，再置目标行；返回目标是否存在（未删除）。"""
        async with self._session_context() as db:
            exists = (
                await db.execute(
                    select(ChatDataModelRegistry.id).filter_by(model_key=model_key, delete_flag=0).limit(1)
                )
            ).first()
            if exists is None:
                return False
            await db.execute(
                update(ChatDataModelRegistry)
                .where(ChatDataModelRegistry.delete_flag == 0, ChatDataModelRegistry.is_default == 1)
                .values(is_default=0)
            )
            await db.execute(
                update(ChatDataModelRegistry)
                .where(ChatDataModelRegistry.model_key == model_key, ChatDataModelRegistry.delete_flag == 0)
                .values(
                    is_default=1,
                    enabled_flag=1,
                    update_by=operator_id,
                    update_by_name=operator_name,
                )
            )
            return True

    async def mark_deleted(self, *, model_key: str, operator_id: str, operator_name: str) -> bool:
        """软删除模型行；请求名改写墓碑名（不占用业务组合，重加同名可新建）。

        墓碑名形如 ``{model_id}__d{id}_{日期}``（同 semantic_config 的
        _DELETED_NAME_EXPR 模式，Python 侧拼接以兼容 sqlite 测试库）。
        返回是否命中（不存在返回 False）。默认行允许删除（加载层兜底取首行）。
        """
        async with self._session_context() as db:
            row = (
                await db.execute(select(ChatDataModelRegistry).filter_by(model_key=model_key, delete_flag=0).limit(1))
            ).scalar_one_or_none()
            if row is None:
                return False
            tombstone_name = f"{str(row.model_id)[:92]}__d{int(row.id)}_{datetime.now().strftime('%Y%m%d')}"
            row.delete_flag = 1
            row.model_id = tombstone_name
            row.enabled_flag = 0
            row.is_default = 0
            row.update_by = operator_id
            row.update_by_name = operator_name
            await db.flush()
            return True
