"""查询层契约：QuerySpec/QueryResult 与编排门面。"""

from __future__ import annotations

import asyncio

from apps.chat_data_app.services.query import QueryOrchestrator, QuerySource, QuerySpec


def test_query_spec_is_serializable():
    spec = QuerySpec(
        source=QuerySource.ONTOLOGY,
        datasource_group_id="g1",
        member_datasource_ids=("m1", "m2"),
        sql="SELECT 1",
        object_type="shipment",
        dataset_id=7,
        query_mode="ontology_find_objects",
    )
    payload = spec.to_dict()
    assert payload["source"] == "ontology"
    assert payload["member_datasource_ids"] == ["m1", "m2"]
    assert payload["object_type"] == "shipment"


def test_execute_member_normalizes_runtime_result(monkeypatch):
    from apps.chat_data_app.services.sql_run import runtime

    captured = {}

    async def fake_execute(*, execution_target, sql, **kwargs):
        captured.update(target=execution_target, sql=sql, kwargs=kwargs)
        return {
            "member_datasource_id": "m1",
            "status": "succeeded",
            "sql": sql,
            "columns": ["a"],
            "data": [{"a": 1}],
            "total_count": 1,
            "execution_time_ms": 12,
        }

    monkeypatch.setattr(runtime, "execute_sql_for_member", fake_execute)
    spec = QuerySpec(
        source=QuerySource.ONTOLOGY,
        datasource_group_id="g1",
        sql="SELECT 1",
        limit=5,
        timeout=20,
        object_type="shipment",
    )
    result = asyncio.run(QueryOrchestrator().execute_member_async(spec, {"member_datasource_id": "m1"}))
    assert result.members[0]["status"] == "succeeded"
    assert result.total_count == 1
    assert result.lineage["object_type"] == "shipment"
    assert captured["kwargs"]["datasource_id"] == "g1"
    assert captured["kwargs"]["limit"] == 5


def test_execute_agent_normalizes_results(monkeypatch):
    from apps.chat_data_app.services.sql_run import runtime

    captured = {}

    async def fake_execute(sql, **kwargs):
        captured.update(sql=sql, kwargs=kwargs)
        return {
            "sql": sql,
            "query_mode": "ontology_find_objects",
            "results": [
                {"member_datasource_id": "m1", "status": "succeeded", "total_count": 2, "execution_time_ms": 5},
                {"member_datasource_id": "m2", "status": "succeeded", "total_count": 3, "execution_time_ms": 7},
            ],
        }

    monkeypatch.setattr(runtime, "execute_agent_sql", fake_execute)
    spec = QuerySpec(
        source=QuerySource.SEMANTIC,
        datasource_group_id="g1",
        member_datasource_ids=("m1", "m2"),
        sql="SELECT 1",
        query_mode="semantic",
    )
    result = asyncio.run(QueryOrchestrator().execute_agent_async(spec))
    assert result.total_count == 5
    assert result.execution_time_ms == 7
    assert [item["member_datasource_id"] for item in result.members] == ["m1", "m2"]
    assert captured["kwargs"]["member_datasource_ids"] == ["m1", "m2"]


def test_execute_task_delegates_to_runtime(monkeypatch):
    from apps.chat_data_app.services.sql_run import runtime

    calls = []

    async def fake_run(task_id, task):
        calls.append((task_id, task))

    monkeypatch.setattr(runtime, "run_sql_query_task", fake_run)
    asyncio.run(QueryOrchestrator().execute_task_async("t1", {"task_id": "t1"}))
    assert calls == [("t1", {"task_id": "t1"})]


def test_legacy_payload_keeps_lineage_and_warnings():
    from apps.chat_data_app.services.query import QueryResult

    result = QueryResult(
        source="ontology",
        sql="SELECT 1",
        members=[{"member_datasource_id": "m1", "status": "succeeded", "total_count": 1}],
        lineage={"object_type": "shipment"},
        warnings=["w1"],
    )
    payload = result.to_legacy_payload()
    assert payload["lineage"] == {"object_type": "shipment"}
    assert payload["warnings"] == ["w1"]
