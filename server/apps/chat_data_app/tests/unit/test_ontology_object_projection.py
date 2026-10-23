"""对象实例只读投影：查询构造与对象契约单测。"""

from __future__ import annotations

import asyncio

import pytest

from apps.chat_data_app.services.ontology.object_projection import (
    ObjectProjectionError,
    build_object_list_query,
    build_object_query,
    build_object_search_query,
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


def test_build_object_list_query_paginates_and_searches_title_or_key():
    detail = _detail()
    detail["properties"][0]["is_title"] = True
    query = build_object_list_query(
        detail,
        _config(),
        keyword="ORD'1",
        limit=200,
        offset=20,
    )
    assert query["limit"] == 100
    assert query["offset"] == 20
    assert query["title_property"] == "order_id"
    assert "LIKE '%ORD" in query["sql"]
    assert "ORDER BY `order_id`" in query["sql"]
    assert "LIMIT 101 OFFSET 20" in query["sql"]


def test_build_object_list_query_clamps_and_falls_back_to_identifier():
    query = build_object_list_query(_detail(), _config(), limit=0, offset=-5)
    assert query["limit"] == 1
    assert query["offset"] == 0
    assert query["title_property"] == "order_id"
    assert "WHERE" not in query["sql"]
    with pytest.raises(ObjectProjectionError, match="200"):
        build_object_list_query(_detail(), _config(), keyword="x" * 201)


def test_build_object_list_query_falls_back_to_identifier_search_column():
    detail = _detail()
    detail["properties"].append({"property_name": "title", "is_title": True})
    query = build_object_list_query(detail, _config(), keyword="A")
    assert query["title_property"] == "title"
    assert "`order_id` LIKE" in query["sql"]


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


def test_build_object_search_query_applies_filters_sort_and_count():
    query = build_object_search_query(
        _detail(),
        _config(),
        keyword="A",
        filters=[{"property": "amount", "operator": "gte", "value": "10"}],
        sorts=[{"property": "amount", "direction": "desc"}],
        limit=10,
        offset=5,
    )
    assert "`amount` >= '10'" in query["sql"]
    assert "ORDER BY `amount` DESC" in query["sql"]
    assert "LIMIT 11 OFFSET 5" in query["sql"]
    assert "COUNT(*) AS `total`" in query["count_sql"]
    assert "`amount` >= '10'" in query["count_sql"]


def test_build_object_search_query_supports_in_and_contains():
    query = build_object_search_query(
        _detail(),
        _config(),
        filters=[
            {"property": "amount", "operator": "in", "values": ["10", "20"]},
            {"property": "order_id", "operator": "contains", "value": "A0"},
        ],
    )
    assert "`amount` IN ('10', '20')" in query["sql"]
    assert "`order_id` LIKE '%A0%'" in query["sql"]


def test_build_object_search_query_supports_all_comparison_operators():
    for operator, symbol in {
        "eq": "=",
        "ne": "<>",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
    }.items():
        query = build_object_search_query(
            _detail(),
            _config(),
            filters=[{"property": "amount", "operator": operator, "value": "10"}],
        )
        assert f"`amount` {symbol} '10'" in query["sql"]
    empty_in = build_object_search_query(
        _detail(),
        _config(),
        filters=[{"property": "amount", "operator": "in", "values": []}],
    )
    assert "1 = 0" in empty_in["sql"]


def test_build_object_search_query_rejects_unmapped_filter_and_sort():
    with pytest.raises(ObjectProjectionError, match="过滤属性未映射"):
        build_object_search_query(
            _detail(), _config(), filters=[{"property": "missing", "operator": "eq", "value": "1"}]
        )
    with pytest.raises(ObjectProjectionError, match="排序属性未映射"):
        build_object_search_query(_detail(), _config(), sorts=[{"property": "missing", "direction": "asc"}])


def test_build_object_search_query_rejects_long_keyword_and_bad_operator():
    with pytest.raises(ObjectProjectionError, match="200"):
        build_object_search_query(_detail(), _config(), keyword="x" * 201)
    with pytest.raises(ObjectProjectionError, match="不支持"):
        build_object_search_query(
            _detail(),
            _config(),
            filters=[{"property": "amount", "operator": "bogus", "value": "1"}],
        )


def test_search_object_projection_handles_empty_count(monkeypatch):
    from apps.chat_data_app.services.ontology import object_projection

    async def fake_get_object_type(_name):
        return _detail()

    async def fake_context(_mapping, **_kwargs):
        return _config(), "g1", [{"member_datasource_id": "m1"}]

    async def fake_query_rows(_targets, sql, **_kwargs):
        return [] if "COUNT(*)" in sql else []

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(object_projection, "_mapping_context", fake_context)
    monkeypatch.setattr(object_projection, "_query_rows", fake_query_rows)
    result = asyncio.run(object_projection.search_object_projection("Order"))
    assert result["total"] == 0
    assert result["objects"] == []


def test_build_object_search_query_requires_mapping_columns():
    detail = _detail()
    detail["mappings"][0]["column_mappings"] = None
    with pytest.raises(ObjectProjectionError, match="column_mappings"):
        build_object_search_query(detail, _config())


def test_search_object_projection_returns_rows_and_total(monkeypatch):
    from apps.chat_data_app.services.ontology import object_projection

    async def fake_get_object_type(_name):
        return _detail()

    async def fake_context(_mapping, **_kwargs):
        return _config(), "g1", [{"member_datasource_id": "m1"}]

    async def fake_query_rows(_targets, sql, **_kwargs):
        if "COUNT(*)" in sql:
            return [{"total": 2}]
        return [{"order_id": "A001", "amount": 10}, {"order_id": "A002", "amount": 20}]

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(object_projection, "_mapping_context", fake_context)
    monkeypatch.setattr(object_projection, "_query_rows", fake_query_rows)
    result = asyncio.run(object_projection.search_object_projection("Order", limit=2))
    assert result["total"] == 2
    assert [item["object_key"] for item in result["objects"]] == ["Order:A001", "Order:A002"]


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


def test_build_related_key_query_includes_title_column():
    detail = _detail()
    detail["properties"][0]["is_title"] = True
    query = build_related_key_query(
        detail,
        _config(),
        filter_property="order_id",
        filter_value="ORD1",
    )
    assert "AS `related_title`" in query["sql"]
    assert query["title_property"] == "order_id"


def test_build_related_key_query_skips_unmapped_title():
    detail = _detail()
    detail["properties"].append({"property_name": "title", "is_title": True})
    query = build_related_key_query(
        detail,
        _config(),
        filter_property="order_id",
        filter_value="ORD1",
    )
    assert "related_title" not in query["sql"]
    assert query["title_property"] == ""


def test_build_related_key_query_rejects_cross_table_filter():
    detail = _detail()
    detail["properties"].append({"property_name": "customer_id", "is_identifier": False})
    detail["mappings"][0]["column_mappings"]["customer_id"] = "客户ID"
    config = _config()
    config["dimensions"].append({"name": "客户ID", "table": "other_table", "column": "customer_id"})
    with pytest.raises(ObjectProjectionError, match="同一张表"):
        build_related_key_query(
            detail,
            config,
            filter_property="customer_id",
            filter_value="C001",
        )


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


def test_query_rows_raises_when_execution_fails(monkeypatch):
    from apps.chat_data_app.services.ontology import object_projection

    async def fake_execute(*_args, **_kwargs):
        return {"status": "failed", "error": "boom"}

    monkeypatch.setattr(object_projection, "execute_sql_for_member", fake_execute)
    with pytest.raises(ObjectProjectionError, match="boom"):
        asyncio.run(
            object_projection._query_rows(
                [{"member_datasource_id": "m1"}],
                "SELECT 1",
                group_id="g1",
                user_id="u",
                username="n",
                limit=1,
            )
        )


def test_list_object_projection_returns_page_and_has_more(monkeypatch):
    from apps.chat_data_app.services.ontology import object_projection

    async def fake_get_object_type(_name):
        return _detail()

    async def fake_context(_mapping, **_kwargs):
        return _config(), "g1", [{"member_datasource_id": "m1"}]

    async def fake_query_rows(*_args, **_kwargs):
        return [
            {"order_id": "A001", "amount": 10},
            {"order_id": "A002", "amount": 20},
            {"order_id": "A003", "amount": 30},
        ]

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(object_projection, "_mapping_context", fake_context)
    monkeypatch.setattr(object_projection, "_query_rows", fake_query_rows)
    result = asyncio.run(object_projection.list_object_projection("Order", limit=2, offset=0))
    assert result["has_more"] is True
    assert [item["object_key"] for item in result["objects"]] == ["Order:A001", "Order:A002"]
    assert result["title_property"] == "order_id"


def test_list_object_projection_without_extra_row_has_no_more(monkeypatch):
    from apps.chat_data_app.services.ontology import object_projection

    async def fake_get_object_type(_name):
        return _detail()

    async def fake_context(_mapping, **_kwargs):
        return _config(), "g1", [{"member_datasource_id": "m1"}]

    async def fake_query_rows(*_args, **_kwargs):
        return [{"order_id": "A001", "amount": 10}, {"order_id": "A002", "amount": 20}]

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(object_projection, "_mapping_context", fake_context)
    monkeypatch.setattr(object_projection, "_query_rows", fake_query_rows)
    result = asyncio.run(object_projection.list_object_projection("Order", limit=2))
    assert result["has_more"] is False


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
