"""图查询的稳定契约：节点、边、结果与错误类型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class GraphQueryError(ValueError):
    """图查询入参/配置/执行失败。"""


class GraphUnavailableError(GraphQueryError):
    """图查询未启用或 Nebula 客户端不可用。"""


@dataclass(frozen=True)
class GraphNode:
    object_key: str
    object_type: str = ""
    title: str = ""
    member_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GraphEdge:
    from_object_key: str
    to_object_key: str
    relation_type: str
    member_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GraphQueryResult:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    paths: list[list[str]] = field(default_factory=list)
    graph_as_of: str = ""
    degraded_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.as_dict() for node in self.nodes],
            "edges": [edge.as_dict() for edge in self.edges],
            "paths": self.paths,
            "graph_as_of": self.graph_as_of,
            "degraded_reason": self.degraded_reason,
        }
