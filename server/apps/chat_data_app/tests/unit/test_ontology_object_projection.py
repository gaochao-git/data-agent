"""对象实例只读投影：查询构造与对象契约单测。"""

from __future__ import annotations

import asyncio

import pytest

from apps.chat_data_app.services.ontology.object_projection import (
    ObjectProjectionError,
    build_object_aggregate_query,
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
            {"property_name": "amount", "is_identifier": False, "value_type": "Decimal"},
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

    async def fake_context(_detail, **_kwargs):
        return _detail, _config(), [{"member_datasource_id": "m1"}], "g1"

    async def fake_query_rows(_targets, sql, **_kwargs):
        return [] if "COUNT(*)" in sql else []

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(object_projection, "_projection_context", fake_context)
    monkeypatch.setattr(object_projection, "_query_rows", fake_query_rows)
    result = asyncio.run(object_projection.search_object_projection("Order"))
    assert result["total"] == 0
    assert result["objects"] == []


def test_build_object_aggregate_query_groups_and_aggregates_mapped_properties():
    query = build_object_aggregate_query(
        _detail(),
        _config(),
        group_by=["amount"],
        metrics=[
            {"operation": "count"},
            {"operation": "sum", "property": "amount"},
            {"operation": "count_distinct", "property": "order_id"},
        ],
        filters=[{"property": "amount", "operator": "gte", "value": "10"}],
        limit=500,
    )
    assert "COUNT(*) AS `count`" in query["sql"]
    assert "SUM(`amount`) AS `sum_amount`" in query["sql"]
    assert "COUNT(DISTINCT `order_id`) AS `count_distinct_order_id`" in query["sql"]
    assert "WHERE `amount` >= '10'" in query["sql"]
    assert "GROUP BY `amount`" in query["sql"]
    assert "LIMIT 200" in query["sql"]
    assert query["metric_keys"] == ["count", "sum_amount", "count_distinct_order_id"]


def test_build_object_aggregate_query_without_group_by_and_search_fallback():
    detail = _detail()
    detail["properties"].append({"property_name": "title", "is_identifier": False, "is_title": True})
    query = build_object_aggregate_query(
        detail,
        _config(),
        metrics=[{"operation": "count"}],
        keyword="A",
    )
    assert "GROUP BY" not in query["sql"]
    assert "ORDER BY" not in query["sql"]
    assert "`order_id` LIKE '%A%'" in query["sql"]


def test_build_object_aggregate_query_requires_mapping_columns_and_same_table():
    detail = _detail()
    detail["mappings"][0]["column_mappings"] = None
    with pytest.raises(ObjectProjectionError, match="column_mappings"):
        build_object_aggregate_query(detail, _config(), metrics=[{"operation": "count"}])
    broken = _detail()
    broken["properties"].append({"property_name": "other", "is_identifier": False})
    broken["mappings"][0]["column_mappings"]["other"] = "other"
    config = _config()
    config["dimensions"].append({"name": "other", "table": "other_table", "column": "other"})
    with pytest.raises(ObjectProjectionError, match="同一张表"):
        build_object_aggregate_query(broken, config, metrics=[{"operation": "count"}])


def test_build_object_aggregate_query_rejects_bad_metrics_and_group_by():
    with pytest.raises(ObjectProjectionError, match="数值属性"):
        build_object_aggregate_query(
            _detail(),
            _config(),
            group_by=["amount"],
            metrics=[{"operation": "sum", "property": "order_id"}],
        )
    with pytest.raises(ObjectProjectionError, match="分组属性未映射"):
        build_object_aggregate_query(
            _detail(),
            _config(),
            group_by=["missing"],
            metrics=[{"operation": "count"}],
        )
    with pytest.raises(ObjectProjectionError, match="至少需要一个聚合指标"):
        build_object_aggregate_query(_detail(), _config(), metrics=[])
    with pytest.raises(ObjectProjectionError, match="不支持的聚合操作"):
        build_object_aggregate_query(
            _detail(),
            _config(),
            metrics=[{"operation": "median", "property": "amount"}],
        )


def test_aggregate_object_projection_assembles_groups(monkeypatch):
    from apps.chat_data_app.services.ontology import object_projection

    async def fake_get_object_type(_name):
        return _detail()

    async def fake_context(_detail, **_kwargs):
        return _detail, _config(), [{"member_datasource_id": "m1"}], "g1"

    async def fake_query_rows(_targets, _sql, **_kwargs):
        return [
            {"amount": 10, "count": 2},
            {"amount": 20, "count": 1},
        ]

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(object_projection, "_projection_context", fake_context)
    monkeypatch.setattr(object_projection, "_query_rows", fake_query_rows)
    result = asyncio.run(
        object_projection.aggregate_object_projection(
            "Order",
            group_by=["amount"],
            metrics=[{"operation": "count"}],
        )
    )
    assert result["group_by"] == ["amount"]
    assert result["metric_keys"] == ["count"]
    assert result["groups"] == [
        {"keys": {"amount": 10}, "metrics": {"count": 2}},
        {"keys": {"amount": 20}, "metrics": {"count": 1}},
    ]


def test_build_object_search_query_requires_mapping_columns():
    detail = _detail()
    detail["mappings"][0]["column_mappings"] = None
    with pytest.raises(ObjectProjectionError, match="column_mappings"):
        build_object_search_query(detail, _config())


def test_search_object_projection_returns_rows_and_total(monkeypatch):
    from apps.chat_data_app.services.ontology import object_projection

    async def fake_get_object_type(_name):
        return _detail()

    async def fake_context(_detail, **_kwargs):
        return _detail, _config(), [{"member_datasource_id": "m1"}], "g1"

    async def fake_query_rows(_targets, sql, **_kwargs):
        if "COUNT(*)" in sql:
            return [{"total": 2}]
        return [{"order_id": "A001", "amount": 10}, {"order_id": "A002", "amount": 20}]

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(object_projection, "_projection_context", fake_context)
    monkeypatch.setattr(object_projection, "_query_rows", fake_query_rows)
    result = asyncio.run(object_projection.search_object_projection("Order", limit=2))
    assert result["total"] == 2
    assert [item["object_key"] for item in result["objects"]] == ["m1:Order:A001", "m1:Order:A002"]


def test_build_related_key_query_rejects_missing_dimension():
    with pytest.raises(ObjectProjectionError, match="缺少映射维度"):
        build_related_key_query(
            _detail(),
            {"dimensions": [{"name": "订单编号", "table": "orders", "column": "order_id"}]},
            filter_property="amount",
            filter_value="1",
        )


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

    async def fake_context(_detail, **_kwargs):
        return _detail, _config(), [{"member_datasource_id": "m1"}], "g1"

    async def fake_query_rows(*_args, **_kwargs):
        return [
            {"order_id": "A001", "amount": 10},
            {"order_id": "A002", "amount": 20},
            {"order_id": "A003", "amount": 30},
        ]

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(object_projection, "_projection_context", fake_context)
    monkeypatch.setattr(object_projection, "_query_rows", fake_query_rows)
    result = asyncio.run(object_projection.list_object_projection("Order", limit=2, offset=0))
    assert result["has_more"] is True
    assert [item["object_key"] for item in result["objects"]] == ["m1:Order:A001", "m1:Order:A002"]
    assert result["title_property"] == "order_id"


def test_list_object_projection_without_extra_row_has_no_more(monkeypatch):
    from apps.chat_data_app.services.ontology import object_projection

    async def fake_get_object_type(_name):
        return _detail()

    async def fake_context(_detail, **_kwargs):
        return _detail, _config(), [{"member_datasource_id": "m1"}], "g1"

    async def fake_query_rows(*_args, **_kwargs):
        return [{"order_id": "A001", "amount": 10}, {"order_id": "A002", "amount": 20}]

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(object_projection, "_projection_context", fake_context)
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


def test_build_derived_value_sql_variants():
    from apps.chat_data_app.services.ontology.object_projection import _build_derived_value_sql

    assert _build_derived_value_sql("orders", "customer_id", "C1", operator="count") == (
        "SELECT COUNT(*) AS `derived_value` FROM `orders` WHERE `customer_id` = 'C1'"
    )
    assert _build_derived_value_sql("orders", "customer_id", "C1", operator="sum", target_column="amount") == (
        "SELECT SUM(`amount`) AS `derived_value` FROM `orders` WHERE `customer_id` = 'C1'"
    )
    assert _build_derived_value_sql("users", "user_id", "C1", target_column="name") == (
        "SELECT `name` AS `derived_value` FROM `users` WHERE `user_id` = 'C1' LIMIT 1"
    )
    escaped = _build_derived_value_sql("orders", "customer_id", "C'1", operator="count")
    assert escaped.endswith("WHERE `customer_id` = 'C\\'1'")


def test_derived_property_values_compute_and_best_effort(monkeypatch):
    """派生属性计算：正常算出聚合值；单条失败（关系缺失）置空不影响整体（对齐 links 最佳努力）。"""
    from apps.chat_data_app.services.ontology import object_projection

    relation = {
        "relation_type_name": "r1",
        "from_object_type_id": 1,
        "to_object_type_id": 2,
        "from_columns": ["customer_id"],
        "to_columns": ["customer_id"],
        "from_object_type_name": "Order",
        "to_object_type_name": "Customer",
        "status": "certified",
    }
    order_detail = {
        "id": 1,
        "object_type_name": "Order",
        "properties": [
            {"property_name": "order_id", "is_identifier": True},
            {"property_name": "customer_id"},
        ],
    }
    customer_detail = {
        "id": 2,
        "object_type_name": "Customer",
        "properties": [
            {"property_name": "customer_id", "is_identifier": True},
            {
                "property_name": "order_count",
                "derivation": {"kind": "aggregate", "relation_type_name": "r1", "operator": "count"},
            },
            {
                "property_name": "ghost_count",
                "derivation": {"kind": "aggregate", "relation_type_name": "ghost", "operator": "count"},
            },
        ],
    }

    class _FakeService:
        async def list_relations_async(self):
            return [relation]

        async def get_object_type_async(self, name):
            return order_detail if name == "Order" else None

    async def fake_mapping_context(detail, member_ids, *, user_id, username):
        config = {
            "dimensions": [
                {"name": "订单编号", "table": "orders", "column": "order_id"},
                {"name": "客户编号", "table": "orders", "column": "customer_id"},
            ]
        }
        query_detail = {
            "mappings": [
                {
                    "semantic_config_id": "dataset",
                    "column_mappings": {"order_id": "订单编号", "customer_id": "客户编号"},
                }
            ]
        }
        return config, query_detail, [{"member_datasource_id": "M1"}], "g1"

    executed = []

    async def fake_query_rows(targets, sql, *, group_id, user_id, username, limit):
        executed.append(sql)
        return [{"derived_value": 7}]

    monkeypatch.setattr(object_projection, "ontology_service", _FakeService())
    monkeypatch.setattr(object_projection, "_dataset_mapping_context", fake_mapping_context)
    monkeypatch.setattr(object_projection, "_query_rows", fake_query_rows)

    values = asyncio.run(
        object_projection._derived_property_values(
            customer_detail, {"customer_id": "C1"}, member_id="M1", user_id="", username=""
        )
    )
    assert values == {"order_count": 7, "ghost_count": None}
    assert len(executed) == 1
    assert "COUNT(*)" in executed[0]
    assert "`customer_id` = 'C1'" in executed[0]


def _derived_detail():
    detail = _detail()
    detail["properties"].append(
        {
            "property_name": "order_count",
            "is_identifier": False,
            "value_type": "Integer",
            "derivation": {"kind": "aggregate", "relation_type_name": "order_belongs_to_customer", "operator": "count"},
        }
    )
    return detail


def test_search_query_rejects_derived_filter_and_sort():
    with pytest.raises(ObjectProjectionError, match="派生属性不支持过滤/排序"):
        build_object_search_query(
            _derived_detail(),
            _config(),
            filters=[{"property": "order_count", "operator": "eq", "value": "1"}],
        )
    with pytest.raises(ObjectProjectionError, match="派生属性不支持过滤/排序"):
        build_object_search_query(
            _derived_detail(),
            _config(),
            sorts=[{"property": "order_count", "direction": "desc"}],
        )


def test_aggregate_query_rejects_derived_group_by_and_filter():
    with pytest.raises(ObjectProjectionError, match="派生属性不支持聚合分组/过滤"):
        build_object_aggregate_query(
            _derived_detail(),
            _config(),
            group_by=["order_count"],
            metrics=[{"name": "cnt", "operation": "count"}],
        )
    with pytest.raises(ObjectProjectionError, match="派生属性不支持聚合分组/过滤"):
        build_object_aggregate_query(
            _derived_detail(),
            _config(),
            metrics=[{"name": "cnt", "operation": "count"}],
            filters=[{"property": "order_count", "operator": "eq", "value": "1"}],
        )


def test_related_key_query_rejects_derived_filter_property():
    with pytest.raises(ObjectProjectionError, match="派生属性不支持关联查询"):
        build_related_key_query(_derived_detail(), _config(), filter_property="order_count", filter_value="1")


def test_search_projection_includes_derived_values_in_page(monkeypatch):
    """列表页返回派生属性值（Return in results），但过滤/排序仍显式拒绝。"""
    from apps.chat_data_app.services.ontology import object_projection

    detail = _derived_detail()

    async def fake_get_object_type(_name):
        return detail

    async def fake_context(*_args, **_kwargs):
        return detail, _config(), [{"member_datasource_id": "M1"}], "g1"

    calls = []

    async def fake_query_rows(_targets, sql, **_kwargs):
        calls.append(sql)
        if "COUNT(*)" in sql and "total" in sql:
            return [{"total": 1}]
        return [{"order_id": "O1", "amount": 10}]

    async def fake_derived(*_args, **_kwargs):
        return {"order_count": 3}

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(object_projection, "_projection_context", fake_context)
    monkeypatch.setattr(object_projection, "_query_rows", fake_query_rows)
    monkeypatch.setattr(object_projection, "_derived_property_values", fake_derived)
    monkeypatch.setattr(
        object_projection, "_member_scope", lambda _targets: {"member_datasource_id": "M1", "member_name": "测试"}
    )

    result = asyncio.run(object_projection.search_object_projection("Order", limit=10))
    assert result["objects"][0]["properties"]["order_count"] == 3
    assert "order_count" in result["properties"]
    assert len(calls) == 2


def test_projection_column_and_identifier_guards():
    from apps.chat_data_app.services.ontology import object_projection

    with pytest.raises(ObjectProjectionError, match="没有可投影"):
        object_projection._projection_columns([], {}, {})
    with pytest.raises(ObjectProjectionError, match="跨表"):
        object_projection._projection_columns(
            [
                {"property_name": "a"},
                {"property_name": "b"},
            ],
            {"a": "A", "b": "B"},
            {
                "A": {"table": "t1", "column": "a"},
                "B": {"table": "t2", "column": "b"},
            },
        )
    with pytest.raises(ObjectProjectionError, match="标识属性"):
        object_projection._identifier_property([{"property_name": "a"}], {"a": "A"})
    with pytest.raises(ObjectProjectionError, match="标识属性"):
        object_projection._identifier_property(
            [{"property_name": "a", "is_identifier": True}, {"property_name": "b", "is_identifier": True}],
            {"a": "A", "b": "B"},
        )


def test_projection_entrypoints_reject_missing_object_type(monkeypatch):
    from apps.chat_data_app.services.ontology import object_projection

    async def no_detail(_name):
        return None

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", no_detail)
    with pytest.raises(ObjectProjectionError, match="不存在"):
        asyncio.run(object_projection.search_object_projection("Ghost"))
    with pytest.raises(ObjectProjectionError, match="不存在"):
        asyncio.run(object_projection.aggregate_object_projection("Ghost"))
    with pytest.raises(ObjectProjectionError, match="不存在"):
        asyncio.run(object_projection.list_object_projection("Ghost"))


def test_projection_mapping_and_guard_branches():
    from apps.chat_data_app.services.ontology import object_projection

    with pytest.raises(ObjectProjectionError, match="非法表/列"):
        object_projection._quote_identifier("bad;col")
    with pytest.raises(ObjectProjectionError, match="未绑定语义模型"):
        object_projection._mapping_for_projection({}, None)
    with pytest.raises(ObjectProjectionError, match="未绑定语义模型: cfg-x"):
        object_projection._mapping_for_projection({"mappings": [{"semantic_config_id": "cfg-a"}]}, "cfg-x")
    with pytest.raises(ObjectProjectionError, match="多个语义模型"):
        object_projection._mapping_for_projection(
            {"mappings": [{"semantic_config_id": "cfg-a"}, {"semantic_config_id": "cfg-b"}]}, None
        )
    with pytest.raises(ObjectProjectionError, match="关系属性未映射"):
        object_projection._mapping_for_property({"mappings": []}, "missing")
    assert object_projection._dimension_index({"dimensions": [None]}) == {}
    with pytest.raises(ObjectProjectionError, match="映射维度不存在"):
        object_projection._projection_columns([{"property_name": "a"}], {"a": "A"}, {})
    with pytest.raises(ObjectProjectionError, match="object_key"):
        object_projection.build_object_query(_detail(), _config(), object_key="")
    with pytest.raises(ObjectProjectionError, match="column_mappings"):
        object_projection.build_object_query(
            {"object_type_name": "Order", "mappings": [{"semantic_config_id": "x"}], "properties": []},
            _config(),
            object_key="1",
        )
    with pytest.raises(ObjectProjectionError, match="标识属性未映射"):
        object_projection._identifier_property([{"property_name": "a", "is_identifier": True}], {})
