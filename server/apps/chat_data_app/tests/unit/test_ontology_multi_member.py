"""多机构本体查询：按 member 并行下发 SQL、统一 results[] 契约与失败隔离。"""

from __future__ import annotations

import asyncio

from apps.chat_data_app.services.ontology import object_projection as projection
from apps.chat_data_app.services.ontology.object_projection import ObjectProjectionError


def _detail(write_mode: str = "read_only") -> dict:
    return {
        "id": 9,
        "object_type_name": "Order",
        "status": "certified",
        "write_mode": write_mode,
        "properties": [
            {"property_name": "order_id", "is_identifier": True},
            {"property_name": "amount", "is_identifier": False, "value_type": "Decimal"},
        ],
    }


def _search_result(member_id: str) -> dict:
    return {
        "objects": [
            {
                "object_type": "Order",
                "object_key": f"{member_id}:Order:1",
                "properties": {"order_id": "1", "amount": "10.00"},
            }
        ],
        "has_more": False,
        "limit": 20,
        "offset": 0,
        "total": 1,
        "title_property": "order_id",
        "properties": ["order_id", "amount"],
        "sql": f"SELECT * FROM {member_id}.orders LIMIT 21",
        "execution_time_ms": 5,
        "member_datasource_id": member_id,
        "member_name": member_id,
    }


def _aggregate_result(member_id: str) -> dict:
    return {
        "object_type": "Order",
        "group_by": ["status"],
        "metric_keys": ["count"],
        "groups": [{"keys": {"status": "paid"}, "metrics": {"count": 2}}],
        "limit": 100,
        "sql": f"SELECT status, COUNT(*) FROM {member_id}.orders GROUP BY status",
        "execution_time_ms": 4,
        "member_datasource_id": member_id,
        "member_name": member_id,
    }


def test_search_multi_projection_fans_out_and_isolates_failure(monkeypatch):
    calls: list[str] = []

    async def fake_get_object_type(_name):
        return _detail()

    async def fake_search(_object_type, **kwargs):
        member_id = (kwargs.get("member_datasource_ids") or [""])[0]
        calls.append(member_id)
        if member_id == "ORG_CODE2":
            raise ObjectProjectionError("当前用户无权访问 Dataset member: ORG_CODE2")
        return _search_result(member_id)

    monkeypatch.setattr(projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(projection, "search_object_projection", fake_search)

    payload = asyncio.run(
        projection.search_objects_multi_projection("Order", member_datasource_ids=["ORG_CODE1", "ORG_CODE2"])
    )

    assert calls == ["ORG_CODE1", "ORG_CODE2"]
    assert payload["query_mode"] == "ontology_find_objects"
    assert payload["status"] == "succeeded"
    assert payload["total_count"] == 1
    assert [item["member_datasource_id"] for item in payload["results"]] == ["ORG_CODE1", "ORG_CODE2"]
    assert payload["results"][0]["status"] == "succeeded"
    assert payload["results"][0]["data"][0]["object_key"] == "ORG_CODE1:Order:1"
    assert payload["results"][1]["status"] == "failed"
    assert "ORG_CODE2" in payload["results"][1]["error"]
    assert payload["has_more"] is False


def test_search_multi_projection_without_members_uses_single_member(monkeypatch):
    async def fake_get_object_type(_name):
        return _detail()

    async def fake_search(_object_type, **kwargs):
        assert kwargs.get("member_datasource_ids") is None
        return _search_result("ORG_CODE1")

    monkeypatch.setattr(projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(projection, "search_object_projection", fake_search)

    payload = asyncio.run(projection.search_objects_multi_projection("Order"))
    assert len(payload["results"]) == 1
    assert payload["results"][0]["member_datasource_id"] == "ORG_CODE1"
    assert payload["total_count"] == 1


def test_action_created_type_queries_once(monkeypatch):
    calls: list[object] = []

    async def fake_get_object_type(_name):
        return _detail(write_mode="action_created")

    async def fake_search(_object_type, **kwargs):
        calls.append(kwargs.get("member_datasource_ids"))
        return _search_result("ORG_CODE1")

    monkeypatch.setattr(projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(projection, "search_object_projection", fake_search)

    payload = asyncio.run(
        projection.search_objects_multi_projection("Order", member_datasource_ids=["ORG_CODE1", "ORG_CODE2"])
    )
    assert calls == [["ORG_CODE1", "ORG_CODE2"]]
    assert len(payload["results"]) == 1


def test_aggregate_multi_projection_fans_out(monkeypatch):
    async def fake_get_object_type(_name):
        return _detail()

    async def fake_aggregate(_object_type, **kwargs):
        member_id = (kwargs.get("member_datasource_ids") or [""])[0]
        return _aggregate_result(member_id)

    monkeypatch.setattr(projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(projection, "aggregate_object_projection", fake_aggregate)

    payload = asyncio.run(
        projection.aggregate_objects_multi_projection(
            "Order",
            member_datasource_ids=["ORG_CODE1", "ORG_CODE2"],
            group_by=["status"],
            metrics=[{"operation": "count"}],
        )
    )
    assert payload["query_mode"] == "ontology_aggregate_objects"
    assert payload["status"] == "succeeded"
    assert payload["total_count"] == 2
    assert [item["member_datasource_id"] for item in payload["results"]] == ["ORG_CODE1", "ORG_CODE2"]
    assert payload["results"][0]["columns"] == ["status", "count"]
    assert payload["results"][0]["data"] == [{"status": "paid", "count": 2}]
