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

from ...datasource.manager import get_datasource_manager
from ..object_projection import _query_rows, read_object_projection, search_object_projection
from ..query_mode import load_domain_ontology_bundle_async
from .contracts import GraphEdge, GraphNode, GraphQueryError
from .service import _quote, _text

logger = get_logger(__name__)

_EDGE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_OBJECTS_PER_TYPE = 100
_SYNC_PAGE_SIZE = 5_000
_WRITE_CHUNK_SIZE = 200


@dataclass
class GraphSyncResult:
    member_id: str
    object_types: int = 0
    nodes: int = 0
    edges: int = 0
    skipped_links: int = 0
    pruned_nodes: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "object_types": self.object_types,
            "nodes": self.nodes,
            "edges": self.edges,
            "skipped_links": self.skipped_links,
            "pruned_nodes": self.pruned_nodes,
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
        self._pool: Any | None = None
        self._session: Any | None = None

    def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        try:
            from .service import ensure_nebula_dependency_path

            ensure_nebula_dependency_path()
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
        config.timeout = int(os.getenv("CHAT_DATA_NEBULA_TIMEOUT_SECONDS") or "120")
        if not pool.init(hosts, config):
            raise GraphSyncError("Nebula 连接失败")
        space = _text(os.getenv("CHAT_DATA_NEBULA_SPACE")) or "chatdata_graph"
        session = pool.get_session(
            _text(os.getenv("CHAT_DATA_NEBULA_USER")) or "root",
            _text(os.getenv("CHAT_DATA_NEBULA_PASSWORD")) or "nebula",
        )
        use = session.execute(f"USE {space}")
        if not use.is_succeeded():
            session.release()
            pool.close()
            raise GraphSyncError(f"Nebula space 不可用: {use.error_msg()}")
        self._pool = pool
        self._session = session
        return session

    def _execute(self, ngql: str) -> None:
        if self._executor is not None:
            self._executor(ngql)
            return
        session = self._ensure_session()
        result = session.execute(ngql)
        if not result.is_succeeded():
            raise GraphSyncError(f"Nebula 写入失败: {result.error_msg()}")

    def close(self) -> None:
        if self._session is not None:
            self._session.release()
            self._session = None
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def _member_node_keys(self, member_id: str) -> set[str]:
        """读取当前 member 在 Nebula 的所有节点 ID；用于全量同步后的残留清理。"""
        session = self._ensure_session()
        result = session.execute(
            f"MATCH (n:Object) WHERE n.Object.member_id == {_quote(member_id)} RETURN id(n) AS `key`"
        )
        if not result.is_succeeded():
            raise GraphSyncError(f"Nebula 读取现有节点失败: {result.error_msg()}")
        keys: set[str] = set()
        for index in range(result.row_size()):
            values = result.row_values(index)
            if values:
                keys.add(_text(values[0]))
        return keys

    def prune(self, member_id: str, keep_keys: set[str]) -> int:
        """删除源数据已不存在、但 Nebula 仍残留的 member 节点及其边。"""
        stale = sorted(self._member_node_keys(member_id) - keep_keys)
        session = self._ensure_session()
        deleted = 0
        for object_key in stale:
            result = session.execute(f"DELETE VERTEX {_quote(object_key)} WITH EDGE")
            if not result.is_succeeded():
                raise GraphSyncError(f"Nebula 删除残留节点失败: {object_key}: {result.error_msg()}")
            deleted += 1
        return deleted

    def write(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        # 分块写，避免单条 INSERT 超过 Nebula 请求体/内存上限；一个 session 写完整批。
        try:
            for start in range(0, len(nodes), _WRITE_CHUNK_SIZE):
                self._execute(_vertex_statement(nodes[start : start + _WRITE_CHUNK_SIZE]))
            for start in range(0, len(edges), _WRITE_CHUNK_SIZE):
                self._execute(_edge_statement(edges[start : start + _WRITE_CHUNK_SIZE], _edge_type()))
        finally:
            self.close()


async def _member_targets(member: str, group_id: str, user_id: str, username: str) -> list[dict[str, Any]]:
    targets = await get_datasource_manager().list_authorized_execution_targets_async(
        group_id, user_id=user_id or None, username=username or None
    )
    selected = [target for target in targets if _text(target.get("member_datasource_id") or target.get("id")) == member]
    if not selected:
        raise GraphSyncError(f"当前用户无权访问 member: {member}")
    return selected


def _binding_column(binding: dict[str, Any], property_name: str) -> str:
    logical = _text((binding.get("column_mappings") or {}).get(property_name))
    return _text((binding.get("columns") or {}).get(logical))


def _member_table(binding: dict[str, Any], member: str) -> str:
    for item in binding.get("members") or []:
        if _text(item.get("member_datasource_id")) == member:
            return _text(item.get("physical_table"))
    return _text(binding.get("physical_table"))


def _object_batch_sql(
    object_type: dict[str, Any], properties: list[dict[str, Any]], member: str, *, offset: int = 0
) -> str:
    from ..object_query_builder import _quote_identifier

    binding = object_type.get("source_binding") or {}
    table = _member_table(binding, member)
    identifier = next((prop for prop in properties if bool(prop.get("is_identifier"))), None)
    title = next((prop for prop in properties if bool(prop.get("is_title"))), identifier)
    if not table or identifier is None or title is None:
        raise GraphSyncError(f"对象类型缺少批量同步所需标识/标题/表: {object_type.get('object_type_name')}")
    id_col = _binding_column(binding, _text(identifier.get("property_name")))
    title_col = _binding_column(binding, _text(title.get("property_name")))
    if not id_col:
        raise GraphSyncError(f"对象类型标识列未映射: {object_type.get('object_type_name')}")
    title_expr = _quote_identifier(title_col) if title_col and title_col != id_col else _quote_identifier(id_col)
    return (
        f"SELECT CAST({_quote_identifier(id_col)} AS STRING) AS `object_key`, "
        f"CAST({title_expr} AS STRING) AS `title` FROM {_quote_identifier(table)} "
        f"ORDER BY {_quote_identifier(id_col)} LIMIT {int(offset)}, {_SYNC_PAGE_SIZE}"
    )


def _relation_batch_sql(relation: dict[str, Any], object_type: dict[str, Any], member: str, *, offset: int = 0) -> str:
    from ..object_query_builder import _quote_identifier

    binding = object_type.get("source_binding") or {}
    table = _member_table(binding, member)
    from_columns = [_text(column) for column in relation.get("from_columns") or [] if _text(column)]
    if not table or len(from_columns) != 1:
        raise GraphSyncError(f"关系缺少单列 from_columns: {relation.get('relation_type_name')}")
    identifier = next((prop for prop in object_type.get("_properties") or [] if bool(prop.get("is_identifier"))), None)
    if identifier is None:
        raise GraphSyncError(f"关系起点对象类型缺少标识属性: {object_type.get('object_type_name')}")
    id_col = _binding_column(binding, _text(identifier.get("property_name")))
    fk_col = _binding_column(binding, from_columns[0])
    if not id_col or not fk_col:
        raise GraphSyncError(f"关系列未映射: {relation.get('relation_type_name')}")
    return (
        f"SELECT CAST({_quote_identifier(id_col)} AS STRING) AS `from_key`, "
        f"CAST({_quote_identifier(fk_col)} AS STRING) AS `to_key` FROM {_quote_identifier(table)} "
        f"WHERE {_quote_identifier(fk_col)} IS NOT NULL ORDER BY {_quote_identifier(id_col)} "
        f"LIMIT {int(offset)}, {_SYNC_PAGE_SIZE}"
    )


async def _fetch_pages(
    builder: Any,
    *,
    targets: list[dict[str, Any]],
    group_id: str,
    user_id: str,
    username: str,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    offset = 0
    while True:
        rows = await _query_rows(
            targets,
            builder(offset),
            group_id=group_id,
            user_id=user_id,
            username=username,
            limit=_SYNC_PAGE_SIZE,
        )
        collected.extend(rows)
        if len(rows) < _SYNC_PAGE_SIZE:
            return collected
        offset += _SYNC_PAGE_SIZE


def _nodes_from_object_rows(member: str, type_name: str, rows: list[dict[str, Any]]) -> dict[str, GraphNode]:
    nodes: dict[str, GraphNode] = {}
    for row in rows:
        key = _text(row.get("object_key"))
        if key:
            nodes[f"{member}:{type_name}:{key}"] = GraphNode(
                object_key=f"{member}:{type_name}:{key}",
                object_type=type_name,
                title=_text(row.get("title")) or key,
                member_id=member,
            )
    return nodes


async def _sync_batch_objects(
    bundle: dict[str, Any],
    *,
    member: str,
    targets: list[dict[str, Any]],
    group_id: str,
    user_id: str,
    username: str,
) -> tuple[dict[str, GraphNode], dict[int, dict[str, Any]], GraphSyncResult]:
    properties_by_type: dict[int, list[dict[str, Any]]] = {}
    for prop in bundle.get("properties") or []:
        properties_by_type.setdefault(int(prop.get("object_type_id") or 0), []).append(prop)
    nodes: dict[str, GraphNode] = {}
    object_by_id: dict[int, dict[str, Any]] = {}
    result = GraphSyncResult(member_id=member)
    for object_type in bundle.get("object_types") or []:
        object_type_id = int(object_type.get("id") or 0)
        binding = object_type.get("source_binding") or {}
        members = {_text(item.get("member_datasource_id")) for item in binding.get("members") or []}
        if member not in members:
            continue
        properties = properties_by_type.get(object_type_id, [])
        enriched = {**object_type, "_properties": properties}
        object_by_id[object_type_id] = enriched
        result.object_types += 1
        try:
            object_rows = await _fetch_pages(
                lambda offset, obj=enriched, props=properties: _object_batch_sql(obj, props, member, offset=offset),
                targets=targets,
                group_id=group_id,
                user_id=user_id,
                username=username,
            )
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{object_type.get('object_type_name')}: {exc}")
            continue
        nodes.update(_nodes_from_object_rows(member, _text(object_type.get("object_type_name")), object_rows))
    result.nodes = len(nodes)
    return nodes, object_by_id, result


def _relation_edges_from_rows(
    *,
    member: str,
    relation_name: str,
    from_type_name: str,
    to_type_name: str,
    nodes: dict[str, GraphNode],
    rows: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str, str], GraphEdge], int]:
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    skipped = 0
    for row in rows:
        from_key = _text(row.get("from_key"))
        to_key = _text(row.get("to_key"))
        if not from_key or not to_key:
            continue
        from_object_key = f"{member}:{from_type_name}:{from_key}"
        to_object_key = to_key if ":" in to_key else f"{member}:{to_type_name}:{to_key}"
        if from_object_key not in nodes or to_object_key not in nodes:
            skipped += 1
            continue
        edges[(from_object_key, to_object_key, relation_name)] = GraphEdge(
            from_object_key=from_object_key,
            to_object_key=to_object_key,
            relation_type=relation_name,
            member_id=member,
        )
    return edges, skipped


async def _sync_batch_relations(
    bundle: dict[str, Any],
    *,
    member: str,
    nodes: dict[str, GraphNode],
    object_by_id: dict[int, dict[str, Any]],
    targets: list[dict[str, Any]],
    group_id: str,
    user_id: str,
    username: str,
) -> tuple[dict[tuple[str, str, str], GraphEdge], GraphSyncResult]:
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    result = GraphSyncResult(member_id=member)
    for relation in bundle.get("relation_types") or []:
        relation_name = _text(relation.get("relation_type_name"))
        from_type = object_by_id.get(int(relation.get("from_object_type_id") or 0))
        if from_type is None:
            result.skipped_links += 1
            continue
        try:
            relation_rows = await _fetch_pages(
                lambda offset, rel=relation, obj=from_type: _relation_batch_sql(rel, obj, member, offset=offset),
                targets=targets,
                group_id=group_id,
                user_id=user_id,
                username=username,
            )
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{relation_name}: {exc}")
            continue
        from_type_name = _text(from_type.get("object_type_name"))
        to_type_name = _text(
            (object_by_id.get(int(relation.get("to_object_type_id") or 0)) or {}).get("object_type_name")
        )
        relation_edges, skipped = _relation_edges_from_rows(
            member=member,
            relation_name=relation_name,
            from_type_name=from_type_name,
            to_type_name=to_type_name,
            nodes=nodes,
            rows=relation_rows,
        )
        edges.update(relation_edges)
        result.skipped_links += skipped
    result.edges = len(edges)
    return edges, result


async def collect_member_graph_batch(
    *,
    member_id: str,
    datasource_group_id: str,
    user_id: str = "",
    username: str = "",
) -> tuple[list[GraphNode], list[GraphEdge], GraphSyncResult]:
    """批量版：每个对象类型/关系分页 SELECT，不逐对象读 links。"""
    member = _text(member_id)
    group_id = _text(datasource_group_id)
    if not member or not group_id:
        raise GraphSyncError("同步必须指定 member_id 和 datasource_group_id")
    bundle = await load_domain_ontology_bundle_async(group_id)
    targets = await _member_targets(member, group_id, user_id, username)
    nodes, object_by_id, object_result = await _sync_batch_objects(
        bundle, member=member, targets=targets, group_id=group_id, user_id=user_id, username=username
    )
    edges, relation_result = await _sync_batch_relations(
        bundle,
        member=member,
        nodes=nodes,
        object_by_id=object_by_id,
        targets=targets,
        group_id=group_id,
        user_id=user_id,
        username=username,
    )
    object_result.edges = len(edges)
    object_result.skipped_links = relation_result.skipped_links
    object_result.errors.extend(relation_result.errors)
    return list(nodes.values()), list(edges.values()), object_result


async def sync_member_graph(
    *,
    member_id: str,
    datasource_group_id: str,
    limit_per_type: int = _MAX_OBJECTS_PER_TYPE,
    user_id: str = "",
    username: str = "",
    writer: Any | None = None,
) -> GraphSyncResult:
    """逐对象 links 的抽样同步（保留给小规模/兼容入口）。"""
    nodes, edges, result = await collect_member_graph(
        member_id=member_id,
        datasource_group_id=datasource_group_id,
        limit_per_type=limit_per_type,
        user_id=user_id,
        username=username,
    )
    (writer or NebulaGraphWriter()).write(nodes, edges)
    return result


async def sync_member_graph_batch(
    *,
    member_id: str,
    datasource_group_id: str,
    user_id: str = "",
    username: str = "",
    writer: Any | None = None,
) -> GraphSyncResult:
    nodes, edges, result = await collect_member_graph_batch(
        member_id=member_id,
        datasource_group_id=datasource_group_id,
        user_id=user_id,
        username=username,
    )
    graph_writer = writer or NebulaGraphWriter()
    graph_writer.write(nodes, edges)
    prune = getattr(graph_writer, "prune", None)
    if callable(prune):
        try:
            result.pruned_nodes = prune(member_id, {node.object_key for node in nodes})
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"图残留清理失败: {exc}")
    return result
