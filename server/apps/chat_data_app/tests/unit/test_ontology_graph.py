"""Nebula 图查询层契约测试（不依赖真实 Nebula）。"""

from __future__ import annotations

import pytest

from apps.chat_data_app.services.ontology.graph import contracts, service


class _Id:
    def __init__(self, value: str):
        self._value = value

    def as_string(self) -> str:
        return self._value


class _Node:
    def __init__(self, key: str, object_type: str, member_id: str, title: str):
        self._key = key
        self._props = {"object_type": object_type, "member_id": member_id, "title": title}

    def get_id(self) -> _Id:
        return _Id(self._key)

    def properties(self) -> dict:
        return self._props


class _Rel:
    def __init__(self, src: str, dst: str, name: str, member_id: str):
        self._src = src
        self._dst = dst
        self._name = name
        self._props = {"member_id": member_id}

    def start_vertex_id(self) -> _Id:
        return _Id(self._src)

    def end_vertex_id(self) -> _Id:
        return _Id(self._dst)

    def edge_name(self) -> str:
        return self._name

    def properties(self) -> dict:
        return self._props


class _Path:
    def __init__(self, nodes: list[_Node], rels: list[_Rel]):
        self._nodes = nodes
        self._rels = rels

    def nodes(self) -> list[_Node]:
        return self._nodes

    def relationships(self) -> list[_Rel]:
        return self._rels


class _Value:
    def __init__(self, path: _Path):
        self._path = path

    def as_path(self) -> _Path:
        return self._path


def test_graph_requires_member_scope():
    with pytest.raises(contracts.GraphQueryError, match="member"):
        service.build_match_query(object_key="ORG1:order:1", depth=1, direction="both", member_ids=[], limit=10)


def test_build_match_query_direction_and_escape(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_NEBULA_EDGE_TYPES", "belongs_to")
    q = service.build_match_query(
        object_key='ORG1:order:1" OR 1=1',
        depth=2,
        direction="in",
        member_ids=["ORG1"],
        limit=10,
    )
    assert "<-[:belongs_to*1..2]-" in q
    assert '\\"' in q
    assert 'n.Object.member_id IN ["ORG1"]' in q

    q2 = service.build_match_query(
        object_key="ORG1:order:1",
        depth=1,
        direction="out",
        member_ids=["ORG1"],
        limit=10,
    )
    assert "-[:belongs_to*1..1]->" in q2


def test_build_match_query_rejects_bad_depth_and_edge(monkeypatch):
    with pytest.raises(contracts.GraphQueryError, match="深度"):
        service.build_match_query(object_key="a", depth=4, direction="both", member_ids=["M"], limit=10)
    monkeypatch.setenv("CHAT_DATA_NEBULA_EDGE_TYPES", "bad;edge")
    with pytest.raises(contracts.GraphQueryError, match="非法图边类型"):
        service.build_match_query(object_key="a", depth=1, direction="both", member_ids=["M"], limit=10)


def test_parse_path_rows_deduplicates_nodes_and_edges():
    path1 = _Path(
        [_Node("M:order:1", "order", "M", "SO-1"), _Node("M:customer:1", "customer", "M", "客户")],
        [_Rel("M:order:1", "M:customer:1", "belongs_to", "M")],
    )
    path2 = _Path(
        [_Node("M:order:2", "order", "M", "SO-2"), _Node("M:customer:1", "customer", "M", "客户")],
        [_Rel("M:order:2", "M:customer:1", "belongs_to", "M")],
    )
    result = service.parse_path_rows([[_Value(path1)], [_Value(path2)]])
    assert len(result.nodes) == 3
    assert len(result.edges) == 2
    assert result.paths == [
        ["M:order:1", "M:customer:1"],
        ["M:order:2", "M:customer:1"],
    ]
    assert result.graph_as_of == ""


def test_service_uses_injected_executor(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_NEBULA_EDGE_TYPES", "belongs_to")
    path = _Path([_Node("M:order:1", "order", "M", "SO-1")], [])
    captured: list[str] = []

    def executor(ngql: str):
        captured.append(ngql)
        return [[_Value(path)]]

    result = service.GraphQueryService(executor=executor).traverse_objects(
        "M:order:1", depth=1, direction="both", limit=10, member_ids=["M"]
    )
    assert len(result.nodes) == 1
    assert "MATCH p=" in captured[0]


def test_graph_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_NEBULA_ENABLED", raising=False)
    assert service.graph_query_enabled() is False
    monkeypatch.setenv("CHAT_DATA_NEBULA_ENABLED", "true")
    assert service.graph_query_enabled() is True


@pytest.mark.asyncio
async def test_collect_member_graph_from_projection(monkeypatch):
    from apps.chat_data_app.services.ontology.graph import sync

    async def fake_bundle(_group):
        return {
            "object_types": [
                {
                    "object_type_name": "order",
                    "source_binding": {"members": [{"member_datasource_id": "M"}]},
                }
            ]
        }

    async def fake_search(name, **kwargs):
        assert name == "order"
        assert kwargs["member_datasource_ids"] == ["M"]
        return {
            "title_property": "order_no",
            "objects": [
                {
                    "object_key": "M:order:1",
                    "properties": {"order_no": "SO-1"},
                }
            ],
        }

    async def fake_read(name, key, **kwargs):
        return {
            "links": {
                "belongs_to": [
                    {
                        "related_object_type": "customer",
                        "related_object_key": "M:customer:1",
                        "related_title": "客户",
                        "direction": "from",
                    },
                    {
                        "related_object_type": "shipment",
                        "related_object_key": "M:shipment:1",
                        "related_title": "运单",
                        "direction": "to",
                    },
                ]
            }
        }

    monkeypatch.setattr(sync, "load_domain_ontology_bundle_async", fake_bundle)
    monkeypatch.setattr(sync, "search_object_projection", fake_search)
    monkeypatch.setattr(sync, "read_object_projection", fake_read)
    nodes, edges, result = await sync.collect_member_graph(member_id="M", datasource_group_id="ds_a")
    assert result.nodes == 3
    assert result.edges == 2
    assert {node.object_key for node in nodes} == {"M:order:1", "M:customer:1", "M:shipment:1"}
    edge_set = {(edge.from_object_key, edge.to_object_key) for edge in edges}
    assert ("M:order:1", "M:customer:1") in edge_set
    assert ("M:shipment:1", "M:order:1") in edge_set


@pytest.mark.asyncio
async def test_sync_member_graph_writes_nodes_and_edges(monkeypatch):
    from apps.chat_data_app.services.ontology.graph import sync

    async def fake_collect(**_kwargs):
        node = contracts.GraphNode("M:order:1", "order", "SO-1", "M")
        edge = contracts.GraphEdge("M:order:1", "M:customer:1", "belongs_to", "M")
        return [node], [edge], sync.GraphSyncResult(member_id="M", object_types=1, nodes=1, edges=1)

    class _Writer:
        def __init__(self):
            self.calls = []

        def write(self, nodes, edges):
            self.calls.append((len(nodes), len(edges)))

    monkeypatch.setattr(sync, "collect_member_graph", fake_collect)
    writer = _Writer()
    result = await sync.sync_member_graph(member_id="M", datasource_group_id="ds_a", writer=writer)
    assert result.nodes == 1
    assert writer.calls == [(1, 1)]


def test_sync_statements_safe_quote(monkeypatch):
    from apps.chat_data_app.services.ontology.graph import sync

    monkeypatch.setenv("CHAT_DATA_NEBULA_EDGE_TYPES", "belongs_to")
    node = contracts.GraphNode('M:order:1"', "order", 'SO"1', "M")
    edge = contracts.GraphEdge("M:order:1", "M:customer:1", "belongs_to", "M")
    vertex = sync._vertex_statement([node])
    edge_sql = sync._edge_statement([edge], "belongs_to")
    assert 'M:order:1\\"' in vertex
    assert "INSERT VERTEX Object(object_type, member_id, title)" in vertex
    assert 'M:order:1"->' in edge_sql.replace('\\"', '"')
