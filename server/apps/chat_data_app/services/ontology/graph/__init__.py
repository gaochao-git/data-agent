"""本体图查询层：NebulaGraph 只读关系投影。

图库只保存对象身份、标题和关系边；属性/指标仍回 SQL 投影与语义层。
"""

from __future__ import annotations

from .contracts import GraphEdge, GraphNode, GraphQueryError, GraphQueryResult, GraphUnavailableError
from .service import (
    GraphQueryService,
    get_graph_query_service,
    graph_query_enabled,
    reset_graph_query_service,
)
from .sync import (
    GraphSyncCapacityError,
    GraphSyncError,
    GraphSyncResult,
    NebulaGraphWriter,
    collect_member_graph,
    collect_member_graph_batch,
    latest_graph_as_of_async,
    sync_member_graph,
    sync_member_graph_batch,
)

__all__ = [
    "GraphEdge",
    "GraphNode",
    "GraphQueryError",
    "GraphQueryResult",
    "GraphQueryService",
    "GraphSyncCapacityError",
    "GraphSyncError",
    "GraphSyncResult",
    "GraphUnavailableError",
    "NebulaGraphWriter",
    "collect_member_graph",
    "collect_member_graph_batch",
    "latest_graph_as_of_async",
    "get_graph_query_service",
    "graph_query_enabled",
    "reset_graph_query_service",
    "sync_member_graph",
    "sync_member_graph_batch",
]
