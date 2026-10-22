"""对象实例只读投影：查询构造与对象契约单测。"""

from __future__ import annotations

import asyncio

import pytest

from apps.chat_data_app.services.ontology.object_projection import (
    ObjectProjectionError,
    build_object_query,
    build_related_key_query,
)


def _detail(**overrides):
    detail = {
        "object_type_name": "Order",
        "properties": [
            {"property_name": "order_id", "is_identifier": True},
            {"property_name": "amount", "is_identifier": False},
        ],
        "mappings": [
            {"semantic_config_id": "cfg-order", "column_mappings": {"order_id": "订单编号", "amount": "金额"}}
        ],
    }
    detail.update(overrides)
    return detail


def _config(**overrides):
    config = {
        "dimensions": [
            {"name": "订单编号", "table": "orders", "column": "order_id"},
            {"name": "金额", "table": "orders", "column": "amount"},
        ]
    }
    config.update(overrides)
    return config


def test_build_object_query_uses_mapping_and_escapes_key():
    query = build_object_query(_detail(), _config(), object_key="A'1")
    assert query["table"] == "orders"
    assert query["object_type"] == "Order"
    assert query["properties"] == ["order_id", "amount"]
    assert "FROM `orders`" in query["sql"]
    assert "WHERE `order_id` = 'A\\'1'" in query["sql"]
    assert "LIMIT 1" in query["sql"]


def test_build_object_query_rejects_composite_identifier():
    detail = _detail()
    detail["properties"][1]["is_identifier"] = True
    with pytest.raises(ObjectProjectionError, match="单个标识属性"):
        build_object_query(detail, _config(), object_key="A001")


def test_build_object_query_rejects_cross_table_properties():
    config = _config()
    config["dimensions"][1]["table"] = "order_items"
    with pytest.raises(ObjectProjectionError, match="同一张表"):
        build_object_query(_detail(), config, object_key="A001")


def test_build_object_query_requires_semantic_model_choice_when_multiple():
    detail = _detail()
    detail["mappings"].append(
        {"semantic_config_id": "cfg-other", "column_mappings": {"order_id": "编号", "amount": "金额"}}
    )
    with pytest.raises(ObjectProjectionError, match="指定 semantic_config_id"):
        build_object_query(detail, _config(), object_key="A001")


def test_build_related_key_query_selects_related_identifier():
    detail = _detail()
    query = build_related_key_query(
        detail,
        _config(),
        filter_property="order_id",
        filter_value="ORD'1",
    )
    assert "FROM `orders`" in query["sql"]
    assert "AS `related_key`" in query["sql"]
    assert "WHERE `order_id` = 'ORD\\'1'" in query["sql"]
    assert query["related_object_type"] == "Order"


def test_read_links_builds_foreign_key_link(monkeypatch):
    from apps.chat_data_app.services.ontology import object_projection

    current_detail = {"id": 1, "object_type_name": "Order"}
    related_detail = {
        "id": 2,
        "object_type_name": "Customer",
        "properties": [{"property_name": "customer_id", "is_identifier": True}],
        "mappings": [{"semantic_config_id": "cfg-c", "column_mappings": {"customer_id": "客户ID"}}],
    }
    relation = {
        "relation_type_name": "order_belongs_to_customer",
        "from_object_type_id": 1,
        "to_object_type_id": 2,
        "from_object_type_name": "Order",
        "to_object_type_name": "Customer",
        "from_columns": ["customer_id"],
        "to_columns": ["customer_id"],
        "status": "certified",
    }
    config = {"dimensions": [{"name": "客户ID", "table": "customers", "column": "customer_id"}]}

    async def fake_relations():
        return [relation]

    async def fake_get_object_type(name):
        return related_detail if name == "Customer" else None

    async def fake_context(mapping, **_kwargs):
        return config, "g1", [{"member_datasource_id": "m1"}]

    async def fake_query_rows(*_args, **_kwargs):
        return [{"related_key": "C001"}]

    monkeypatch.setattr(object_projection.ontology_service, "list_relations_async", fake_relations)
    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(object_projection, "_mapping_context", fake_context)
    monkeypatch.setattr(object_projection, "_query_rows", fake_query_rows)
    links = asyncio.run(
        object_projection._read_links(current_detail, {"customer_id": "C001"}, user_id="u", username="n")
    )
    assert links["order_belongs_to_customer"][0] == {
        "relation": "order_belongs_to_customer",
        "direction": "from",
        "related_object_type": "Customer",
        "related_object_key": "Customer:C001",
    }


def test_object_projection_applies_action_overrides_when_enabled(monkeypatch):
    from apps.chat_data_app.services.ontology import object_projection

    class FakeExecutionDao:
        async def latest_object_values_async(self, object_type_id, object_key):
            return {"pay_status": "paid"}

    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    monkeypatch.setattr(object_projection, "OntologyActionExecutionDao", FakeExecutionDao)
    result = asyncio.run(
        object_projection._apply_object_edits({"id": 2}, "ORD2024000001", {"pay_status": "unpaid", "amount": "10"})
    )
    assert result == {"pay_status": "paid", "amount": "10"}


def test_object_projection_skips_action_overrides_when_disabled(monkeypatch):
    from apps.chat_data_app.services.ontology import object_projection

    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", raising=False)
    assert asyncio.run(object_projection._apply_object_edits({"id": 2}, "ORD2024000001", {"pay_status": "unpaid"})) == {
        "pay_status": "unpaid"
    }
