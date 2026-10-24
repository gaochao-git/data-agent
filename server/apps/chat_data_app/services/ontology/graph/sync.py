"""本体对象投影 → NebulaGraph 只读图同步。

只抽对象身份/标题和关系边，不导业务明细；同步按 member 分批，可重复执行
（Nebula MERGE/INSERT 覆盖同一 VID）。这是手动/任务调用的同步入口，不进入
在线查询请求路径。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from utils.logger import get_logger

from ..object_projection import read_object_projection, search_object_projection
from ..query_mode import load_domain_ontology_bundle_async
from .contracts import GraphEdge, GraphNode, GraphQueryError
from .service import _quote, _text

logger = get_logger(__name__)

_EDGE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_OBJECTS_PER_TYPE = 100


@dataclass
class GraphSyncResult:
    member_id: str
    object_types: int = 0
    nodes: int = 0
    edges: int = 0
    skipped_links: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "object_types": self.object_types,
            "nodes": self.nodes,
            "edges": self.edges,
            "skipped_links": self.skipped_links,
            "errors": list(self.errors),
        }


class GraphSyncError(GraphQueryError):
    """图同步失败。"""


def _edge_type() -> str:
    raw = _text(os.getenv("CHAT_DATA_NEBULA_EDGE_TYPES")) or "belongs_to"
    edge = raw.split(",")[0].strip()
    if not _EDGE_RE.fullmatch(edge):
        raise GraphSyncError(f"非法图边类型: {edge!r}")
    return edge


def _vertex_statement(nodes: list[GraphNode]) -> str:
    values = []
    for node in nodes:
        values.append(
            f"{_quote(node.object_key)}:({_quote(node.object_type)},{_quote(node.member_id)},{_quote(node.title)})"
        )
    return "INSERT VERTEX Object(object_type, member_id, title) VALUES " + ", ".join(values)


def _edge_statement(edges: list[GraphEdge], edge_type: str) -> str:
    values = [
        f"{_quote(edge.from_object_key)}->{_quote(edge.to_object_key)}:"
        f"({_quote(edge.member_id)},{_quote(edge.relation_type)})"
        for edge in edges
    ]
    return f"INSERT EDGE {edge_type}(member_id, relation_type) VALUES " + ", ".join(values)


def _object_node(object_type: str, member_id: str, item: dict[str, Any], title_property: str) -> GraphNode:
    key = _text(item.get("object_key"))
    properties = item.get("properties") or {}
    title = _text(properties.get(title_property)) if title_property else ""
    return GraphNode(
        object_key=key,
        object_type=object_type,
        title=title or key,
        member_id=member_id,
    )


def _link_edges(
    *,
    current: GraphNode,
    links: dict[str, list[dict[str, Any]]],
    nodes: dict[str, GraphNode],
) -> tuple[list[GraphEdge], int]:
    """把对象 links 转成规范方向边；返回 (edges, skipped)。"""
    edges: list[GraphEdge] = []
    skipped = 0
    for relation_type, entries in links.items():
        for entry in entries:
            related_key = _text(entry.get("related_object_key"))
            if not related_key:
                skipped += 1
                continue
            related_type = _text(entry.get("related_object_type"))
            related_title = _text(entry.get("related_title")) or related_key
            member_id = _text(entry.get("member_datasource_id")) or current.member_id
            nodes.setdefault(
                related_key,
                GraphNode(
                    object_key=related_key,
                    object_type=related_type,
                    title=related_title,
                    member_id=member_id,
                ),
            )
            direction = _text(entry.get("direction"))
            if direction == "to":
                # 当前对象是 to 侧，对端是 from 侧
                edges.append(
                    GraphEdge(
                        from_object_key=related_key,
                        to_object_key=current.object_key,
                        relation_type=_text(relation_type),
                        member_id=member_id,
                    )
                )
            else:
                edges.append(
                    GraphEdge(
                        from_object_key=current.object_key,
                        to_object_key=related_key,
                        relation_type=_text(relation_type),
                        member_id=member_id,
                    )
                )
    return edges, skipped


async def _collect_object_type(
    *,
    name: str,
    member: str,
    safe_limit: int,
    user_id: str,
    username: str,
    nodes: dict[str, GraphNode],
    read_keys: set[str],
) -> tuple[list[GraphEdge], int]:
    """读取一个对象类型的对象/links，返回该类型新增边与跳过数。"""
    page = await search_object_projection(
        name,
        member_datasource_ids=[member],
        limit=safe_limit,
        user_id=user_id,
        username=username,
    )
    title_property = _text(page.get("title_property"))
    edges: list[GraphEdge] = []
    skipped = 0
    for item in page.get("objects") or []:
        node = _object_node(name, member, item, title_property)
        if not node.object_key:
            continue
        nodes[node.object_key] = node
        if node.object_key in read_keys:
            continue
        read_keys.add(node.object_key)
        try:
            detail = await read_object_projection(
                name,
                node.object_key,
                member_datasource_ids=[member],
                user_id=user_id,
                username=username,
            )
        except Exception:  # noqa: BLE001 - 单对象 links 失败只跳过
            skipped += 1
            logger.warning("图同步读取对象关系失败: %s", node.object_key, exc_info=True)
            continue
        link_edges, link_skipped = _link_edges(
            current=node,
            links=detail.get("links") or {},
            nodes=nodes,
        )
        skipped += link_skipped
        edges.extend(link_edges)
    return edges, skipped


async def collect_member_graph(
    *,
    member_id: str,
    datasource_group_id: str,
    limit_per_type: int = _MAX_OBJECTS_PER_TYPE,
    user_id: str = "",
    username: str = "",
) -> tuple[list[GraphNode], list[GraphEdge], GraphSyncResult]:
    """从当前成员的对象投影抽取节点/边（不写库，便于单测与预览）。"""
    member = _text(member_id)
    group_id = _text(datasource_group_id)
    if not member or not group_id:
        raise GraphSyncError("同步必须指定 member_id 和 datasource_group_id")
    safe_limit = max(1, min(int(limit_per_type), _MAX_OBJECTS_PER_TYPE))
    bundle = await load_domain_ontology_bundle_async(group_id)
    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    result = GraphSyncResult(member_id=member)
    read_keys: set[str] = set()

    for object_type in bundle.get("object_types") or []:
        name = _text(object_type.get("object_type_name"))
        binding = object_type.get("source_binding") or {}
        members = {
            _text(item.get("member_datasource_id")) for item in binding.get("members") or [] if isinstance(item, dict)
        }
        if not name or member not in members:
            continue
        result.object_types += 1
        try:
            object_edges, skipped = await _collect_object_type(
                name=name,
                member=member,
                safe_limit=safe_limit,
                user_id=user_id,
                username=username,
                nodes=nodes,
                read_keys=read_keys,
            )
        except Exception as exc:  # noqa: BLE001 - 单对象类型失败不阻塞其它类型
            result.errors.append(f"{name}: {exc}")
            logger.warning("图同步读取对象类型失败: %s", name, exc_info=True)
            continue
        result.skipped_links += skipped
        for edge in object_edges:
            edges[(edge.from_object_key, edge.to_object_key, edge.relation_type)] = edge

    result.nodes = len(nodes)
    result.edges = len(edges)
    return list(nodes.values()), list(edges.values()), result


class NebulaGraphWriter:
    """批量写入 Nebula；同一 VID/边重复写为覆盖，不做删除。"""

    def __init__(self, executor: Any | None = None) -> None:
        self._executor = executor

    def _execute(self, ngql: str) -> None:
        if self._executor is not None:
            self._executor(ngql)
            return
        try:
            from nebula3.Config import Config
            from nebula3.gclient.net import ConnectionPool
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise GraphSyncError("Nebula 客户端未安装：需要 nebula3-python") from exc
        hosts = []
        for item in (_text(os.getenv("CHAT_DATA_NEBULA_HOSTS")) or "127.0.0.1:9669").split(","):
            host, _, port = item.partition(":")
            if host:
                hosts.append((host, int(port or "9669")))
        pool = ConnectionPool()
        config = Config()
        config.timeout = int(os.getenv("CHAT_DATA_NEBULA_TIMEOUT_SECONDS") or "10")
        if not pool.init(hosts, config):
            raise GraphSyncError("Nebula 连接失败")
        space = _text(os.getenv("CHAT_DATA_NEBULA_SPACE")) or "chatdata_graph"
        session = pool.get_session(
            _text(os.getenv("CHAT_DATA_NEBULA_USER")) or "root",
            _text(os.getenv("CHAT_DATA_NEBULA_PASSWORD")) or "nebula",
        )
        try:
            use = session.execute(f"USE {space}")
            if not use.is_succeeded():
                raise GraphSyncError(f"Nebula space 不可用: {use.error_msg()}")
            result = session.execute(ngql)
            if not result.is_succeeded():
                raise GraphSyncError(f"Nebula 写入失败: {result.error_msg()}")
        finally:
            session.release()
            pool.close()

    def write(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        if nodes:
            self._execute(_vertex_statement(nodes))
        if edges:
            self._execute(_edge_statement(edges, _edge_type()))


async def sync_member_graph(
    *,
    member_id: str,
    datasource_group_id: str,
    limit_per_type: int = _MAX_OBJECTS_PER_TYPE,
    user_id: str = "",
    username: str = "",
    writer: Any | None = None,
) -> GraphSyncResult:
    """从对象投影抽取并写入 Nebula；返回计数。"""
    nodes, edges, result = await collect_member_graph(
        member_id=member_id,
        datasource_group_id=datasource_group_id,
        limit_per_type=limit_per_type,
        user_id=user_id,
        username=username,
    )
    (writer or NebulaGraphWriter()).write(nodes, edges)
    return result
