"""图查询门面测试：前端接口和智能体共用同一后端选择逻辑。"""

from __future__ import annotations

import pytest

from apps.chat_data_app.services.ontology.graph import facade
from apps.chat_data_app.services.ontology.graph.contracts import GraphQueryError, GraphQueryResult


@pytest.mark.asyncio
async def test_facade_uses_sql_backend(monkeypatch):
    class _Adapter:
        def __init__(self, **_kwargs):
            pass

        async def traverse_objects(self, object_key, **_kwargs):
            return GraphQueryResult(paths=[[object_key]])

    monkeypatch.setenv("CHAT_DATA_GRAPH_BACKEND", "sql")
    monkeypatch.setattr(facade, "SqlGraphQueryAdapter", _Adapter)
    result = await facade.GraphQueryFacade().traverse_objects(
        "M:order:1",
        depth=1,
        member_ids=["M"],
    )
    assert result.paths == [["M:order:1"]]


@pytest.mark.asyncio
async def test_facade_blocks_path_query_on_sql(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_GRAPH_BACKEND", "sql")
    with pytest.raises(GraphQueryError, match="暂不支持路径查询"):
        await facade.GraphQueryFacade().find_paths("M:a:1", "M:b:1", member_ids=["M"])


def test_facade_requires_nebula_enabled_when_selected(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_GRAPH_BACKEND", "nebula")
    monkeypatch.delenv("CHAT_DATA_NEBULA_ENABLED", raising=False)
    with pytest.raises(GraphQueryError, match="CHAT_DATA_NEBULA_ENABLED"):
        facade.GraphQueryFacade()._require_available()
