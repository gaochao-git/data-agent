"""Semantic configuration persistence."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert

from utils.logger import get_logger
from utils.models import SemanticConfig

from .dao_session import session_scope
from .datasource_dao_helpers import _batch_items, _format_time, _load_json

logger = get_logger(__name__)

# 软删除时的改名表达式。
#
# 生产库唯一索引现为三列 uniq_group_id_config_name_delete_flag (group_id, semantic_config_name,
# delete_flag)（2026-09-19 SHOW CREATE TABLE 核对；迁移 SQL 见 next_version.md，旧环境为两列）。
# 三列索引允许「墓碑行与活跃行同名」，但同一名字反复删除仍会积累多行 (group_id, name, 1) 撞键；
# 未迁移的两列索引环境下墓碑保留原名还会持续占名，导致「删除 → 用同名重建」被唯一键拒绝
# 或被 upsert 复活墓碑。因此删除时仍须同步改名——把自增主键拼进名字，保证任意多次删除互不撞名。
#
# 必须在同一条 UPDATE 的 SQL 表达式里完成，不能用 Python 读-改-写：
# soft_delete_by_group_id_async 是单条批量 UPDATE（一次可删整组），没有逐行拼值的机会，且读改写有并发竞态。
#
# 长度：LEFT(name, 92) + "__d" + id(≤7) + "_" + yyyyMMdd(8) = 110 < varchar(128)。
_DELETED_NAME_EXPR = func.concat(
    func.left(SemanticConfig.semantic_config_name, 92),
    "__d",
    SemanticConfig.id,
    "_",
    func.date_format(func.now(), "%Y%m%d"),
)


def _semantic_config_json_columns(config: dict[str, Any]) -> dict[str, str]:
    """序列化语义配置的三个 JSON 落库列。"""
    return {
        "metrics_json": json.dumps(config.get("metrics") or [], ensure_ascii=False),
        "dimensions_json": json.dumps(config.get("dimensions") or [], ensure_ascii=False),
        "relationships_json": json.dumps(config.get("relationships") or [], ensure_ascii=False),
    }


class SemanticConfigDao:
    """基于 MySQL 的语义配置数据访问对象。"""

    @staticmethod
    def _model_to_row(model: SemanticConfig) -> dict[str, Any]:
        return {
            "semantic_config_id": model.semantic_config_id,
            "group_id": model.group_id,
            "semantic_config_name": model.semantic_config_name,
            "semantic_config_desc": model.semantic_config_desc,
            "business_notes": model.business_notes,
            "enabled_flag": model.enabled_flag,
            "metrics_json": model.metrics_json,
            "dimensions_json": model.dimensions_json,
            "relationships_json": model.relationships_json,
            "scope_type": model.scope_type,
            "create_time": model.create_time,
            "update_time": model.update_time,
            "create_by": model.create_by,
            "create_by_name": model.create_by_name,
            "update_by": model.update_by,
            "update_by_name": model.update_by_name,
        }

    @staticmethod
    def _map_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["semantic_config_id"],
            "semantic_config_id": row["semantic_config_id"],
            "group_id": row["group_id"],
            "name": row.get("semantic_config_name") or "",
            "description": row.get("semantic_config_desc") or "",
            "business_notes": row.get("business_notes"),
            "scope_type": row.get("scope_type") or "public",
            "enabled": bool(row.get("enabled_flag", 1)),
            "metrics": _load_json(row.get("metrics_json"), []),
            "dimensions": _load_json(row.get("dimensions_json"), []),
            "relationships": _load_json(row.get("relationships_json"), []),
            "create_time": _format_time(row.get("create_time")) or "",
            "update_time": _format_time(row.get("update_time")) or "",
            "create_by": row.get("create_by") or "",
            "create_by_name": row.get("create_by_name") or "",
            "update_by": row.get("update_by") or "",
            "update_by_name": row.get("update_by_name") or "",
        }

    @staticmethod
    def _visibility_conditions(group_id: str, user_id: str = "") -> list[Any]:
        conditions = [SemanticConfig.group_id == group_id, SemanticConfig.delete_flag == 0]
        normalized_user_id = str(user_id or "").strip()
        if normalized_user_id:
            conditions.append(
                or_(
                    SemanticConfig.scope_type == "public",
                    and_(SemanticConfig.scope_type == "private", SemanticConfig.create_by == normalized_user_id),
                )
            )
        return conditions

    @staticmethod
    def _map_directory_row(row: dict[str, Any]) -> dict[str, Any]:
        metrics = _load_json(row.get("metrics_json"), [])
        dimensions = _load_json(row.get("dimensions_json"), [])
        return {
            "id": row.get("semantic_config_id") or "",
            "name": row.get("semantic_config_name") or "",
            "description": row.get("semantic_config_desc") or "",
            "enabled": bool(row.get("enabled_flag", 1)),
            "metrics_total": len(metrics) if isinstance(metrics, list) else 0,
            "dimensions_total": len(dimensions) if isinstance(dimensions, list) else 0,
        }

    async def get_by_group_id_async(self, group_id: str, user_id: str = "") -> list[dict[str, Any]]:
        if not group_id:
            return []
        async with session_scope() as session:
            result = await session.execute(
                select(SemanticConfig)
                .where(*self._visibility_conditions(group_id, user_id))
                .order_by(
                    case((SemanticConfig.scope_type == "public", 0), else_=1).asc(),
                    SemanticConfig.semantic_config_name.asc(),
                    SemanticConfig.id.asc(),
                )
            )
            rows = [self._model_to_row(row) for row in result.scalars().all()]
        return [self._map_row(row) for row in rows]

    async def get_directory_by_group_id_async(self, group_id: str, user_id: str = "") -> list[dict[str, Any]]:
        if not group_id:
            return []
        async with session_scope() as session:
            result = await session.execute(
                select(
                    SemanticConfig.semantic_config_id,
                    SemanticConfig.semantic_config_name,
                    SemanticConfig.semantic_config_desc,
                    SemanticConfig.enabled_flag,
                    SemanticConfig.metrics_json,
                    SemanticConfig.dimensions_json,
                )
                .where(*self._visibility_conditions(group_id, user_id))
                .order_by(
                    case((SemanticConfig.scope_type == "public", 0), else_=1).asc(),
                    SemanticConfig.semantic_config_name.asc(),
                    SemanticConfig.id.asc(),
                )
            )
            rows = [dict(row) for row in result.mappings().all()]
        return [self._map_directory_row(row) for row in rows]

    async def get_by_id_async(self, group_id: str, config_id: str, user_id: str = "") -> dict[str, Any] | None:
        if not group_id or not config_id:
            return None
        conditions = self._visibility_conditions(group_id, user_id)
        conditions.append(SemanticConfig.semantic_config_id == config_id)
        async with session_scope() as session:
            result = await session.execute(select(SemanticConfig).where(*conditions).limit(1))
            model = result.scalars().first()
        return self._map_row(self._model_to_row(model)) if model else None

    async def upsert_async(self, group_id: str, config: dict[str, Any]) -> bool:
        if not group_id or not ((config or {}).get("id") or (config or {}).get("semantic_config_id")):
            return False
        return await self.upsert_many_async(group_id, [config])

    @staticmethod
    def _map_config_to_row(group_id: str, config: dict[str, Any]) -> dict[str, Any]:
        config = config or {}
        row = {
            "semantic_config_id": config.get("id") or config.get("semantic_config_id"),
            "group_id": group_id,
            "semantic_config_name": config.get("name") or config.get("semantic_config_name") or "",
            "semantic_config_desc": config.get("description") or config.get("semantic_config_desc"),
            "business_notes": config.get("business_notes"),
            "enabled_flag": 1 if config.get("enabled", True) else 0,
            "scope_type": config.get("scope_type") or "public",
            "sort_no": int(config.get("sort_no") or 0),
            "create_by": config.get("create_by") or "",
            "create_by_name": config.get("create_by_name") or "",
            "update_by": config.get("update_by") or "",
            "update_by_name": config.get("update_by_name") or "",
            "delete_flag": 0,
        }
        row.update(_semantic_config_json_columns(config))
        return row

    @staticmethod
    def _upsert_batch_statement(batch: list[dict[str, Any]]):
        statement = mysql_insert(SemanticConfig).values(batch)
        return statement.on_duplicate_key_update(
            group_id=statement.inserted.group_id,
            semantic_config_name=statement.inserted.semantic_config_name,
            semantic_config_desc=statement.inserted.semantic_config_desc,
            business_notes=statement.inserted.business_notes,
            enabled_flag=statement.inserted.enabled_flag,
            metrics_json=statement.inserted.metrics_json,
            dimensions_json=statement.inserted.dimensions_json,
            relationships_json=statement.inserted.relationships_json,
            scope_type=statement.inserted.scope_type,
            sort_no=statement.inserted.sort_no,
            update_by=statement.inserted.update_by,
            update_by_name=statement.inserted.update_by_name,
            delete_flag=0,
        )

    async def upsert_many_async(self, group_id: str, configs: list[dict[str, Any]]) -> bool:
        """在一个事务中批量保存语义配置。"""
        if not group_id or not configs:
            return False
        rows = []
        for config in configs:
            config_id = (config or {}).get("id") or (config or {}).get("semantic_config_id")
            if not config_id:
                return False
            rows.append(self._map_config_to_row(group_id, config))
        async with session_scope() as session:
            for batch in _batch_items(rows):
                await session.execute(self._upsert_batch_statement(batch))
        return True

    async def batch_update_enabled_async(
        self,
        group_id: str,
        config_ids: list[str],
        enabled: bool,
        update_by: str = "",
        update_by_name: str = "",
    ) -> int:
        """在一个事务中批量切换语义配置启用状态，返回实际更新行数。

        只命中未删除且属于该分组的记录；update_time 由模型 onupdate 维护，此处不显式赋值。

        ⚠️ 授权前提：本方法自身不含任何 user/scope 限定，UPDATE 只按 group_id + id 列表生效。
        调用方必须先完成逐条授权分类（service 层 classify_batch_toggle_ids：
        普通通道仅限本人私有模型、后台通道须有后台权限且仅限公共模型），
        再将授权子集传入，否则会绕过横向越权防护。当前唯一调用方
        是 SemanticConfigService.batch_toggle；新增调用方前请复核这一点。
        """
        if not group_id or not config_ids:
            return 0
        async with session_scope() as session:
            result = await session.execute(
                update(SemanticConfig)
                .where(
                    SemanticConfig.group_id == group_id,
                    SemanticConfig.delete_flag == 0,
                    SemanticConfig.semantic_config_id.in_(config_ids),
                )
                .values(
                    enabled_flag=1 if enabled else 0,
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )
            rowcount = int(result.rowcount or 0)
        return rowcount

    async def soft_delete_async(self, config_id: str, update_by: str = "", update_by_name: str = "") -> None:
        """软删除单个语义配置。

        带 delete_flag == 0 条件保证幂等（重复调用不会二次改名、也不会重复累加后缀）。
        """
        async with session_scope() as session:
            await session.execute(
                update(SemanticConfig)
                .where(
                    SemanticConfig.semantic_config_id == config_id,
                    SemanticConfig.delete_flag == 0,
                )
                .values(
                    delete_flag=1,
                    semantic_config_name=_DELETED_NAME_EXPR,
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )

    async def soft_delete_by_group_id_async(self, group_id: str, update_by: str = "", update_by_name: str = "") -> None:
        if not group_id:
            return
        async with session_scope() as session:
            await session.execute(
                update(SemanticConfig)
                .where(SemanticConfig.group_id == group_id, SemanticConfig.delete_flag == 0)
                .values(
                    delete_flag=1,
                    semantic_config_name=_DELETED_NAME_EXPR,
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )
