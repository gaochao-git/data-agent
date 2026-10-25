"""SQL 图查询适配器测试：不依赖 Nebula，验证统一查询契约。"""

from __future__ import annotations

import pytest

from apps.chat_data_app.services.ontology.graph import sql_adapter
from apps.chat_data_app.services.ontology.graph.contracts import GraphQueryError


@pytest.mark.asyncio
async def test_sql_adapter_expands_one_hop(monkeypatch):
    async def fake_read(object_type, object_key, **_kwargs):
        assert object_type == "order"
        assert object_key == "M:order:1"
        return {
            "links": {
                "order_belongs_to_customer": [
                    {
                        "related_object_key": "M:customer:1",
                        "related_object_type": "customer",
                        "related_title": "客户",
                        "direction": "from",
                    }
                ],
                "order_contains_item": [
                    {
                        "related_object_key": "M:item:1",
                        "related_object_type": "order_item",
                        "related_title": "明细",
                        "direction": "from",
                    }
                ],
            }
        }

    monkeypatch.setattr(sql_adapter, "read_object_projection", fake_read)
    result = await sql_adapter.SqlGraphQueryAdapter().traverse_objects(
        "M:order:1",
        depth=1,
        limit=10,
        member_ids=["M"],
    )
    assert {node.object_key for node in result.nodes} == {"M:order:1", "M:customer:1", "M:item:1"}
    assert {(edge.from_object_key, edge.to_object_key) for edge in result.edges} == {
        ("M:order:1", "M:customer:1"),
        ("M:order:1", "M:item:1"),
    }
    assert result.paths == [["M:order:1", "M:customer:1"], ["M:order:1", "M:item:1"]]
    assert result.degraded_reason == ""


@pytest.mark.asyncio
async def test_sql_adapter_marks_neighbor_limit(monkeypatch):
    entries = [
        {
            "related_object_key": f"M:item:{index}",
            "related_object_type": "order_item",
            "related_title": f"明细{index}",
            "direction": "to",
        }
        for index in range(3)
    ]

    async def fake_read(_object_type, _object_key, **_kwargs):
        return {"links": {"order_contains_item": entries}}

    monkeypatch.setattr(sql_adapter, "read_object_projection", fake_read)
    result = await sql_adapter.SqlGraphQueryAdapter().traverse_objects(
        "M:order:1",
        depth=1,
        limit=2,
        member_ids=["M"],
    )
    assert len(result.paths) == 2
    assert "前 2 个邻居对象" in result.degraded_reason


@pytest.mark.asyncio
async def test_sql_adapter_rejects_multi_hop():
    with pytest.raises(GraphQueryError, match="只支持 1 跳"):
        await sql_adapter.SqlGraphQueryAdapter().traverse_objects(
            "M:order:1",
            depth=2,
            member_ids=["M"],
        )
