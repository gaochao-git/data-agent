"""图查询门面：统一 SQL / Nebula 后端，供前端接口和智能体工具复用。"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from .contracts import GraphQueryError, GraphQueryResult
from .service import get_graph_query_service, graph_query_enabled, graph_read_backend
from .sql_adapter import SqlGraphQueryAdapter


class GraphQueryFacade:
    """按配置选择图后端；调用方只依赖这类和 GraphQueryResult。"""

    def __init__(self, *, user_id: str = "", username: str = "") -> None:
        self._user_id = user_id
        self._username = username
        self.backend = graph_read_backend()

    def capabilities(self) -> dict[str, Any]:
        if self.backend == "sql":
            return SqlGraphQueryAdapter().capabilities()
        return {
            "backend": "nebula",
            "max_depth": 3,
            "topology": True,
            "property_filters": False,
            "path_query": True,
        }

    def _require_available(self) -> None:
        if self.backend == "nebula" and not graph_query_enabled():
            raise GraphQueryError(
                "Nebula 图查询未启用：需设置 CHAT_DATA_NEBULA_ENABLED=1，或切换 CHAT_DATA_GRAPH_BACKEND=sql"
            )

    async def traverse_objects(
        self,
        object_key: str,
        *,
        depth: int = 1,
        direction: Literal["out", "in", "both"] = "both",
        limit: int = 50,
        member_ids: list[str],
    ) -> GraphQueryResult:
        self._require_available()
        if self.backend == "sql":
            return await SqlGraphQueryAdapter(
                user_id=self._user_id,
                username=self._username,
            ).traverse_objects(
                object_key,
                depth=depth,
                direction=direction,
                limit=limit,
                member_ids=member_ids,
            )
        return await asyncio.to_thread(
            get_graph_query_service().traverse_objects,
            object_key,
            depth=depth,
            direction=direction,
            limit=limit,
            member_ids=member_ids,
        )

    async def find_paths(
        self,
        from_object_key: str,
        to_object_key: str,
        *,
        max_depth: int = 3,
        limit: int = 10,
        member_ids: list[str],
    ) -> GraphQueryResult:
        self._require_available()
        if self.backend != "nebula":
            raise GraphQueryError(
                "当前 SQL 图后端暂不支持路径查询；请启用 Nebula 或切换 CHAT_DATA_GRAPH_BACKEND=nebula"
            )
        return await asyncio.to_thread(
            get_graph_query_service().find_paths,
            from_object_key,
            to_object_key,
            max_depth=max_depth,
            limit=limit,
            member_ids=member_ids,
        )
