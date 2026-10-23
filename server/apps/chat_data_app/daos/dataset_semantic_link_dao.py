"""Dataset ↔ 语义模型关联 DAO。

关联只存引用（semantic_config_id）与 Dataset 逻辑列 → 语义维度名对照，
指标定义/口径仍只在语义层（Ossie mapping 的简化形态）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.models import ChatDataDatasetSemanticLink


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_text(value: Any, default: Any) -> str:
    try:
        return json.dumps(value if value is not None else default, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(default, ensure_ascii=False)


def _parse_json(value: Any, expect_type: type) -> Any:
    if isinstance(value, expect_type):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return expect_type()
    return parsed if isinstance(parsed, expect_type) else expect_type()


def _format_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def link_row_to_dict(row: ChatDataDatasetSemanticLink) -> dict[str, Any]:
    """关联行 → 服务层视图（不含 Dataset/模型名称，由服务层补齐）。"""
    return {
        "id": int(row.id),
        "dataset_id": int(row.dataset_id),
        "semantic_config_id": _text(row.semantic_config_id),
        "datasource_group_id": _text(row.datasource_group_id),
        "table_name": _text(row.table_name),
        "column_mappings": _parse_json(row.column_mappings_json, dict),
        "metric_scope": _parse_json(row.metric_scope_json, list),
        "lifecycle_status": _text(row.lifecycle_status) or "draft",
        "delete_flag": int(row.delete_flag or 0),
        "update_time": _format_time(row.update_time),
        "create_by": _text(row.create_by),
        "create_by_name": _text(row.create_by_name),
        "update_by": _text(row.update_by),
        "update_by_name": _text(row.update_by_name),
    }


class DatasetSemanticLinkDao:
    """Dataset ↔ 语义模型关联持久化；事务由服务层 transaction_scope 编排。"""

    async def get_by_id(self, session: AsyncSession, link_id: int) -> ChatDataDatasetSemanticLink | None:
        row = await session.get(ChatDataDatasetSemanticLink, int(link_id))
        if row is None or int(row.delete_flag or 0) != 0:
            return None
        return row

    async def get_by_dataset_and_config(
        self,
        session: AsyncSession,
        dataset_id: int,
        semantic_config_id: str,
    ) -> ChatDataDatasetSemanticLink | None:
        stmt = select(ChatDataDatasetSemanticLink).where(
            ChatDataDatasetSemanticLink.dataset_id == int(dataset_id),
            ChatDataDatasetSemanticLink.semantic_config_id == _text(semantic_config_id),
            ChatDataDatasetSemanticLink.delete_flag == 0,
        )
        return (await session.execute(stmt)).scalars().first()

    async def list_by_dataset(
        self,
        session: AsyncSession,
        dataset_id: int,
        *,
        datasource_group_id: str | None = None,
    ) -> list[ChatDataDatasetSemanticLink]:
        stmt = select(ChatDataDatasetSemanticLink).where(
            ChatDataDatasetSemanticLink.dataset_id == int(dataset_id),
            ChatDataDatasetSemanticLink.delete_flag == 0,
        )
        group_id = _text(datasource_group_id)
        if group_id:
            stmt = stmt.where(ChatDataDatasetSemanticLink.datasource_group_id == group_id)
        stmt = stmt.order_by(ChatDataDatasetSemanticLink.id.asc())
        return list((await session.execute(stmt)).scalars().all())

    async def save(
        self,
        session: AsyncSession,
        row: ChatDataDatasetSemanticLink,
        payload: dict[str, Any],
        *,
        operator: str,
        operator_name: str,
    ) -> ChatDataDatasetSemanticLink:
        row.table_name = _text(payload.get("table_name"))
        row.column_mappings_json = _json_text(payload.get("column_mappings"), {})
        row.metric_scope_json = _json_text(payload.get("metric_scope"), [])
        row.lifecycle_status = _text(payload.get("lifecycle_status")) or "draft"
        row.update_by = operator
        row.update_by_name = operator_name
        session.add(row)
        await session.flush()
        return row

    async def soft_delete(
        self,
        session: AsyncSession,
        row: ChatDataDatasetSemanticLink,
        *,
        operator: str,
        operator_name: str,
    ) -> None:
        row.delete_flag = int(row.id)
        row.update_by = operator
        row.update_by_name = operator_name
        session.add(row)
        await session.flush()


__all__ = ["DatasetSemanticLinkDao", "link_row_to_dict"]
