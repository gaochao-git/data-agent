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
    GraphSyncResult,
    NebulaGraphWriter,
    collect_member_graph,
    collect_member_graph_batch,
    sync_member_graph,
    sync_member_graph_batch,
)

__all__ = [
    "GraphEdge",
    "GraphNode",
    "GraphQueryError",
    "GraphQueryResult",
    "GraphQueryService",
    "GraphSyncResult",
    "GraphUnavailableError",
    "NebulaGraphWriter",
    "collect_member_graph",
    "collect_member_graph_batch",
    "get_graph_query_service",
    "graph_query_enabled",
    "reset_graph_query_service",
    "sync_member_graph",
    "sync_member_graph_batch",
]
