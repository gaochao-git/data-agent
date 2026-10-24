"""Nebula 图同步运行记录 DAO；只存运行状态，不存图数据。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from utils.logger import get_logger
from utils.models import ChatDataGraphSyncRun

from .dao_session import session_scope

logger = get_logger(__name__)


def _as_of_text(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class GraphSyncDao:
    """图同步运行记录读写；表未迁移时降级为空结果，不影响同步主流程。"""

    async def save_run_async(
        self,
        *,
        member_datasource_id: str,
        datasource_group_id: str,
        status: str,
        object_types: int,
        nodes: int,
        edges: int,
        skipped_links: int,
        pruned_nodes: int,
        errors: list[str],
        started_at: datetime,
        finished_at: datetime,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        try:
            async with session_scope() as session:
                row = ChatDataGraphSyncRun(
                    member_datasource_id=member_datasource_id,
                    datasource_group_id=datasource_group_id,
                    run_status=status,
                    object_types=int(object_types),
                    node_count=int(nodes),
                    edge_count=int(edges),
                    skipped_links=int(skipped_links),
                    pruned_nodes=int(pruned_nodes),
                    errors_json=json.dumps(errors, ensure_ascii=False),
                    as_of=finished_at if status == "succeeded" else None,
                    started_at=started_at,
                    finished_at=finished_at,
                    create_by=operator,
                    create_by_name=operator_name,
                )
                session.add(row)
                await session.flush()
                await session.refresh(row)
                return {"id": int(row.id), "as_of": _as_of_text(row.as_of), "status": row.run_status}
        except SQLAlchemyError as exc:
            logger.warning("图同步运行记录写入失败（请确认表已迁移）: %s", exc)
            return {}

    async def latest_succeeded_async(
        self, member_datasource_id: str, datasource_group_id: str = ""
    ) -> dict[str, Any] | None:
        try:
            async with session_scope() as session:
                stmt = select(ChatDataGraphSyncRun).where(
                    ChatDataGraphSyncRun.member_datasource_id == member_datasource_id,
                    ChatDataGraphSyncRun.run_status == "succeeded",
                )
                if datasource_group_id:
                    stmt = stmt.where(ChatDataGraphSyncRun.datasource_group_id == datasource_group_id)
                row = (await session.execute(stmt.order_by(ChatDataGraphSyncRun.id.desc()).limit(1))).scalars().first()
                if row is None:
                    return None
                return {
                    "id": int(row.id),
                    "as_of": _as_of_text(row.as_of),
                    "finished_at": row.finished_at.strftime("%Y-%m-%d %H:%M:%S") if row.finished_at else "",
                    "node_count": int(row.node_count),
                    "edge_count": int(row.edge_count),
                    "pruned_nodes": int(row.pruned_nodes),
                }
        except SQLAlchemyError as exc:
            logger.warning("图同步运行记录读取失败（请确认表已迁移）: %s", exc)
            return None


__all__ = ["GraphSyncDao"]
