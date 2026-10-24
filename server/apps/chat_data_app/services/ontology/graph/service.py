"""NebulaGraph 只读图查询服务。

契约：
- 节点 VID = ``member:object_type:local_key``；
- 边方向 = 关系类型 from -> to，反向用 ``REVERSELY`` / ``BIDIRECT``；
- 所有查询强制 member 范围，不做跨机构图查询；
- 只返回对象身份/标题/关系，不返回业务属性。
"""

from __future__ import annotations

import os
import re
import sys
import threading
from typing import Any, Literal

from utils.logger import get_logger

from .contracts import GraphEdge, GraphNode, GraphQueryError, GraphQueryResult, GraphUnavailableError

logger = get_logger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
_EDGE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_DEPTH = 3
_MAX_LIMIT = 200
_DEFAULT_EDGE = "belongs_to"


def ensure_nebula_dependency_path() -> None:
    """本地可按 CHAT_DATA_NEBULA_DEPS 指定 nebula3 客户端目录；生产由 requirements 安装。"""
    extra = (os.getenv("CHAT_DATA_NEBULA_DEPS") or "").strip()
    if not extra:
        return
    for part in extra.split(os.pathsep):
        if part and part not in sys.path:
            sys.path.insert(0, part)


def graph_query_enabled() -> bool:
    """图查询总开关；默认关闭，避免未部署 Nebula 的实例启动失败。"""
    return str(os.getenv("CHAT_DATA_NEBULA_ENABLED") or "").strip().lower() in _TRUTHY


def _text(value: Any) -> str:
    normalized = str(value or "").strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
        return normalized[1:-1]
    return normalized


def _quote(value: str) -> str:
    """nGQL 字符串字面量转义（只用于 VID / member，不拼接任意片段）。"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _edge_types() -> list[str]:
    raw = os.getenv("CHAT_DATA_NEBULA_EDGE_TYPES") or _DEFAULT_EDGE
    edges = [part.strip() for part in raw.split(",") if part.strip()]
    if not edges:
        edges = [_DEFAULT_EDGE]
    for edge in edges:
        if not _EDGE_RE.fullmatch(edge):
            raise GraphQueryError(f"非法图边类型: {edge!r}")
    return edges


def _member_clause(member_ids: list[str]) -> str:
    members = [member for member in member_ids if _text(member)]
    if not members:
        raise GraphQueryError("图查询必须指定 member_datasource_id（禁止全局图查询）")
    literals = ", ".join(_quote(member) for member in members)
    return f"all(n IN nodes(p) WHERE n.Object.member_id IN [{literals}])"


def build_match_query(
    *,
    object_key: str,
    depth: int,
    direction: Literal["out", "in", "both"],
    member_ids: list[str],
    limit: int,
    target_object_key: str | None = None,
) -> str:
    """构造按对象起点/终点的路径 MATCH 查询（参数均经过白名单/转义）。"""
    key = _text(object_key)
    if not key or len(key) > 512:
        raise GraphQueryError("object_key 不能为空且长度不能超过 512")
    if depth < 1 or depth > _MAX_DEPTH:
        raise GraphQueryError(f"图查询深度必须在 1~{_MAX_DEPTH}")
    if limit < 1 or limit > _MAX_LIMIT:
        raise GraphQueryError(f"图查询 limit 必须在 1~{_MAX_LIMIT}")
    if direction not in {"out", "in", "both"}:
        raise GraphQueryError(f"不支持的方向: {direction}")
    edges = _edge_types()
    edge_pattern = "|".join(edges)
    if direction == "out":
        pattern = f"-[:{edge_pattern}*1..{depth}]->"
    elif direction == "in":
        pattern = f"<-[:{edge_pattern}*1..{depth}]-"
    else:
        pattern = f"-[:{edge_pattern}*1..{depth}]-"
    target = ""
    if target_object_key is not None:
        target_key = _text(target_object_key)
        if not target_key or len(target_key) > 512:
            raise GraphQueryError("target_object_key 不合法")
        target = f" AND id(b) == {_quote(target_key)}"
    member_filter = _member_clause(member_ids)
    return (
        f"MATCH p=(a:Object){pattern}(b:Object) "
        f"WHERE id(a) == {_quote(key)}{target} AND {member_filter} "
        f"RETURN p LIMIT {int(limit)}"
    )


def _node_from(value: Any) -> GraphNode | None:
    try:
        node_id = value.get_id().as_string()
        props = value.properties() or {}
    except Exception:  # noqa: BLE001 - 脏数据不阻塞整条路径
        return None
    return GraphNode(
        object_key=str(node_id),
        object_type=_text(props.get("object_type")),
        title=_text(props.get("title")),
        member_id=_text(props.get("member_id")),
    )


def _edge_from(value: Any) -> GraphEdge | None:
    try:
        props = value.properties() or {}
        return GraphEdge(
            from_object_key=str(value.start_vertex_id().as_string()),
            to_object_key=str(value.end_vertex_id().as_string()),
            relation_type=_text(props.get("relation_type")) or str(value.edge_name()),
            member_id=_text(props.get("member_id")),
        )
    except Exception:  # noqa: BLE001 - 脏边跳过
        return None


def _parse_one_path(row: Any) -> tuple[list[GraphNode], list[GraphEdge]] | None:
    """解析单行 path；脏数据返回 None，不阻塞整批结果。"""
    try:
        path = row[0].as_path()
    except Exception:  # noqa: BLE001 - 单行格式异常只跳过
        logger.warning("解析 Nebula 路径失败，跳过该行", exc_info=True)
        return None
    nodes = []
    for node in path.nodes():
        parsed = _node_from(node)
        if parsed is not None:
            nodes.append(parsed)
    if not nodes:
        return None
    edges = []
    for rel in path.relationships():
        edge = _edge_from(rel)
        if edge is not None:
            edges.append(edge)
    return nodes, edges


def parse_path_rows(rows: list[Any]) -> GraphQueryResult:
    """把 ResultSet 的 path 列解析成去重后的 nodes/edges/paths。"""
    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    paths: list[list[str]] = []
    for row in rows:
        parsed = _parse_one_path(row)
        if parsed is None:
            continue
        path_nodes, path_edges = parsed
        paths.append([node.object_key for node in path_nodes])
        nodes.update({node.object_key: node for node in path_nodes})
        for edge in path_edges:
            edges[(edge.from_object_key, edge.to_object_key, edge.relation_type)] = edge
    member_by_key = {node.object_key: node.member_id for node in nodes.values()}
    parsed_edges = []
    for _key, edge in sorted(edges.items()):
        member_id = (
            edge.member_id or member_by_key.get(edge.from_object_key, "") or member_by_key.get(edge.to_object_key, "")
        )
        parsed_edges.append(
            GraphEdge(
                from_object_key=edge.from_object_key,
                to_object_key=edge.to_object_key,
                relation_type=edge.relation_type,
                member_id=member_id,
            )
        )
    return GraphQueryResult(
        nodes=sorted(nodes.values(), key=lambda node: node.object_key),
        edges=parsed_edges,
        paths=paths,
        graph_as_of="",  # 同步任务上报前不伪造「截至时间」
    )


class GraphQueryService:
    """Nebula 查询服务；executor 可注入以便单测，默认使用 nebula3 连接池。"""

    def __init__(self, executor: Any | None = None) -> None:
        self._executor = executor

    def _execute(self, ngql: str) -> list[Any]:
        if self._executor is not None:
            return self._executor(ngql)
        try:
            ensure_nebula_dependency_path()
            from nebula3.Config import Config
            from nebula3.gclient.net import ConnectionPool
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise GraphUnavailableError("Nebula 客户端未安装：需要 nebula3-python") from exc
        hosts = []
        for item in (_text(os.getenv("CHAT_DATA_NEBULA_HOSTS")) or "127.0.0.1:9669").split(","):
            host, _, port = item.partition(":")
            if host:
                hosts.append((host, int(port or "9669")))
        pool = ConnectionPool()
        config = Config()
        config.timeout = int(os.getenv("CHAT_DATA_NEBULA_TIMEOUT_SECONDS") or "10")
        if not pool.init(hosts, config):
            raise GraphUnavailableError("Nebula 连接失败")
        space = _text(os.getenv("CHAT_DATA_NEBULA_SPACE")) or "chatdata_graph"
        session = pool.get_session(
            _text(os.getenv("CHAT_DATA_NEBULA_USER")) or "root",
            _text(os.getenv("CHAT_DATA_NEBULA_PASSWORD")) or "nebula",
        )
        try:
            use_result = session.execute(f"USE {space}")
            if not use_result.is_succeeded():
                raise GraphQueryError(f"Nebula space 不可用: {use_result.error_msg()}")
            result = session.execute(ngql)
            if not result.is_succeeded():
                raise GraphQueryError(f"Nebula 查询失败: {result.error_msg()}")
            return [result.row_values(index) for index in range(result.row_size())]
        finally:
            session.release()
            pool.close()

    def traverse_objects(
        self,
        object_key: str,
        *,
        depth: int = 1,
        direction: Literal["out", "in", "both"] = "both",
        limit: int = 50,
        member_ids: list[str],
        target_object_key: str | None = None,
    ) -> GraphQueryResult:
        query = build_match_query(
            object_key=object_key,
            depth=depth,
            direction=direction,
            member_ids=member_ids,
            limit=limit,
            target_object_key=target_object_key,
        )
        result = parse_path_rows(self._execute(query))
        if len(result.paths) >= limit:
            result.degraded_reason = (
                f"当前节点关系较多，仅展示前 {limit} 条路径；请点击更具体的节点继续，或按需使用「展开一跳」"
            )
        return result

    def find_paths(
        self,
        from_object_key: str,
        to_object_key: str,
        *,
        max_depth: int = 3,
        limit: int = 10,
        member_ids: list[str],
    ) -> GraphQueryResult:
        return self.traverse_objects(
            from_object_key,
            depth=max_depth,
            direction="both",
            limit=limit,
            member_ids=member_ids,
            target_object_key=to_object_key,
        )


_service: GraphQueryService | None = None
_service_lock = threading.Lock()


def get_graph_query_service() -> GraphQueryService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = GraphQueryService()
    return _service


def reset_graph_query_service() -> None:
    global _service
    _service = None
