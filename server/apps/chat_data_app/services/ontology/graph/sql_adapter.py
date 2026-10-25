"""SQL 图查询适配器：MVP 不要求图数据同步，直接用对象投影拼一跳拓扑。

契约与 Nebula 适配器一致，返回 GraphQueryResult；后端可在 SQL / Nebula 之间切换。
"""

from __future__ import annotations

from typing import Any, Literal

from ..object_projection import read_object_projection
from .contracts import GraphEdge, GraphNode, GraphQueryError, GraphQueryResult, GraphUnavailableError
from .service import _text

_MAX_DEPTH = 1
_MAX_LIMIT = 200


def _resolve_scope(object_key: str, member_ids: list[str]) -> tuple[str, str, str, list[str]]:
    key = _text(object_key)
    if not key:
        raise GraphQueryError("object_key 不能为空")
    parts = key.split(":")
    if len(parts) < 3 or not parts[1]:
        raise GraphQueryError("object_key 必须形如 member:object_type:local_key")
    member = _text(parts[0]) or (member_ids[0] if member_ids else "")
    members = [member] if member else [item for item in member_ids if _text(item)]
    if not members:
        raise GraphUnavailableError("SQL 图查询必须指定 member_datasource_id")
    return key, _text(parts[1]), member, members


def _link_nodes(
    *,
    anchor_key: str,
    anchor_member: str,
    relation_type: str,
    entry: dict[str, Any],
    direction: Literal["out", "in", "both"],
    nodes: dict[str, GraphNode],
    edges: list[GraphEdge],
) -> str:
    related_key = _text(entry.get("related_object_key"))
    if not related_key:
        return ""
    relation_direction = _text(entry.get("direction"))
    if direction == "out" and relation_direction != "to":
        return ""
    if direction == "in" and relation_direction != "from":
        return ""
    related_member = _text(entry.get("member_datasource_id")) or anchor_member
    nodes.setdefault(
        related_key,
        GraphNode(
            object_key=related_key,
            object_type=_text(entry.get("related_object_type")),
            title=_text(entry.get("related_title")) or related_key,
            member_id=related_member,
        ),
    )
    if relation_direction == "to":
        edges.append(GraphEdge(related_key, anchor_key, _text(relation_type), related_member))
    else:
        edges.append(GraphEdge(anchor_key, related_key, _text(relation_type), related_member))
    return related_key


def _build_result(
    detail: dict[str, Any],
    key: str,
    object_type: str,
    member: str,
    direction: Literal["out", "in", "both"],
    limit: int,
) -> GraphQueryResult:
    nodes: dict[str, GraphNode] = {key: GraphNode(key, object_type, key, member)}
    edges: list[GraphEdge] = []
    paths: list[list[str]] = []
    truncated = False
    for relation_type, entries in (detail.get("links") or {}).items():
        for entry in entries or []:
            if len(paths) >= limit:
                truncated = True
                break
            related_key = _link_nodes(
                anchor_key=key,
                anchor_member=member,
                relation_type=relation_type,
                entry=entry,
                direction=direction,
                nodes=nodes,
                edges=edges,
            )
            if related_key:
                paths.append([key, related_key])
        if truncated:
            break
    result = GraphQueryResult(
        nodes=sorted(nodes.values(), key=lambda node: node.object_key),
        edges=edges,
        paths=paths,
        graph_as_of="",
    )
    if truncated:
        result.degraded_reason = f"SQL 图查询仅展示前 {limit} 个邻居对象"
    return result


class SqlGraphQueryAdapter:
    """只读 SQL 图查询：一跳邻居来自对象的 links 投影。"""

    backend = "sql"

    def __init__(self, *, user_id: str = "", username: str = "") -> None:
        self._user_id = user_id
        self._username = username

    def capabilities(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "max_depth": _MAX_DEPTH,
            "topology": True,
            "property_filters": False,
        }

    async def traverse_objects(
        self,
        object_key: str,
        *,
        depth: int = 1,
        direction: Literal["out", "in", "both"] = "both",
        limit: int = 50,
        member_ids: list[str],
    ) -> GraphQueryResult:
        if depth < 1 or depth > _MAX_DEPTH:
            raise GraphQueryError(f"SQL 图查询当前只支持 1 跳，收到 depth={depth}")
        if limit < 1 or limit > _MAX_LIMIT:
            raise GraphQueryError(f"图查询 limit 必须在 1~{_MAX_LIMIT}")
        if direction not in {"out", "in", "both"}:
            raise GraphQueryError(f"不支持的方向: {direction}")
        key, object_type, member, members = _resolve_scope(object_key, member_ids)
        try:
            detail = await read_object_projection(
                object_type,
                key,
                member_datasource_ids=members,
                user_id=self._user_id,
                username=self._username,
            )
        except Exception as exc:  # noqa: BLE001 - 转成稳定图查询错误
            raise GraphQueryError(f"读取对象一跳关系失败: {exc}") from exc
        return _build_result(detail, key, object_type, member, direction, limit)
