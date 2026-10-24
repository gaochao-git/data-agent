"""派生属性 P0 收口：结构校验、只读入口、过滤/排序/聚合拦截、DDL 兜底。"""

from __future__ import annotations

import pytest

from apps.chat_data_app.services.ontology import ontology_validator as validator
from apps.chat_data_app.services.ontology.action_type_service import _normalize_edit_item
from apps.chat_data_app.services.ontology.errors import OntologyInvalidError
from apps.chat_data_app.services.ontology.object_projection import (
    ObjectProjectionError,
    _reject_derived_property_operations,
)
from utils import schema


def _identifier() -> dict:
    return {
        "property_name": "customer_id",
        "display_name": "客户ID",
        "value_type": "String",
        "is_identifier": True,
        "is_title": True,
    }


def _items(*properties: dict) -> list[dict]:
    """对象类型至少需要一个标识属性，属性级红线用「标识属性 + 被测属性」组合验证。"""
    return [_identifier(), *properties]


def _property(name: str = "order_count", **overrides) -> dict:
    payload = {
        "property_name": name,
        "display_name": "订单数",
        "value_type": "String",
        "is_identifier": False,
        "is_title": False,
    }
    payload.update(overrides)
    return payload


def _aggregate(name: str = "order_count", **overrides) -> dict:
    derivation = {
        "kind": "aggregate",
        "relation_type_name": "order_belongs_to_customer",
        "operator": "count",
        "target_property": "",
    }
    derivation.update(overrides)
    return _property(name, derivation=derivation)


# ---------- 结构校验 ----------


def test_validator_marks_derivation_provided_only_when_key_present():
    omitted = validator.validate_property_items(_items(_property("order_count")))[1]
    assert omitted["derivation"] is None
    assert omitted["derivation_provided"] is False

    explicit_null = validator.validate_property_items(_items(_property("order_count", derivation=None)))[1]
    assert explicit_null["derivation"] is None
    assert explicit_null["derivation_provided"] is True

    derived = validator.validate_property_items(_items(_aggregate()))[1]
    assert derived["derivation"]["kind"] == "aggregate"
    assert derived["derivation_provided"] is True


def test_validator_rejects_derived_property_reserved_flags():
    derivation = {
        "kind": "aggregate",
        "relation_type_name": "order_belongs_to_customer",
        "operator": "count",
        "target_property": "",
    }
    with pytest.raises(OntologyInvalidError, match="标识或标题"):
        validator.validate_property_items(_items(_property("order_count", derivation=derivation, is_identifier=True)))
    with pytest.raises(OntologyInvalidError, match="enum_ref"):
        validator.validate_property_items(
            _items(_property("order_count", derivation=derivation, enum_ref="order_status"))
        )
    with pytest.raises(OntologyInvalidError, match="render_hints"):
        validator.validate_property_items(
            _items(_property("order_count", derivation=derivation, render_hints={"searchable": True}))
        )


def test_validator_rejects_invalid_derivation_structures():
    with pytest.raises(OntologyInvalidError, match="kind"):
        validator.validate_property_items(_items(_property(derivation={"kind": "unknown"})))
    with pytest.raises(OntologyInvalidError, match="operator"):
        validator.validate_property_items(
            _items(_property(derivation={"kind": "aggregate", "relation_type_name": "r"}))
        )
    with pytest.raises(OntologyInvalidError, match="target_property"):
        validator.validate_property_items(_items(_aggregate(operator="sum", target_property="")))
    with pytest.raises(OntologyInvalidError, match="operator"):
        validator.validate_property_items(
            _items(
                {
                    "property_name": "name_copy",
                    "value_type": "String",
                    "derivation": {
                        "kind": "select",
                        "relation_type_name": "r",
                        "operator": "sum",
                        "target_property": "name",
                    },
                }
            )
        )


# ---------- 只读入口 ----------


def test_action_edit_rejects_derived_property():
    properties = {"order_count": {"property_name": "order_count", "derivation": {"kind": "aggregate"}}}
    with pytest.raises(Exception, match="派生属性只读"):
        _normalize_edit_item(
            {"operation": "set_property", "property": "order_count", "value": {"kind": "static", "value": 1}},
            properties=properties,
            parameter_names=set(),
            edited_properties=set(),
        )


def test_projection_rejects_derived_filters_sorts_and_group_by():
    detail = {
        "object_type_name": "customer",
        "properties": [
            {"property_name": "customer_id", "value_type": "String", "is_identifier": True},
            {"property_name": "order_count", "value_type": "Integer", "derivation": {"kind": "aggregate"}},
        ],
    }
    with pytest.raises(ObjectProjectionError, match="派生属性不支持过滤/排序"):
        _reject_derived_property_operations(detail, ["order_count"], "过滤/排序")
    with pytest.raises(ObjectProjectionError, match="派生属性不支持聚合分组/过滤"):
        _reject_derived_property_operations(detail, ["order_count"], "聚合分组/过滤")
    # 普通属性不受影响
    _reject_derived_property_operations(detail, ["customer_id"], "过滤/排序")


# ---------- DDL 兜底 ----------


def test_derivation_column_present_in_table_ddl_and_migration():
    assert "derivation_json" in schema.MYSQL_CHAT_DATA_ONTOLOGY_PROPERTY_TABLE
    assert "derivation_json" in schema.MYSQL_CHAT_DATA_ONTOLOGY_PROPERTY_DERIVATION_MIGRATION
    assert schema.MYSQL_CHAT_DATA_ONTOLOGY_PROPERTY_TABLE in schema.MYSQL_TABLES


def test_validator_count_distinct_filters_and_period():
    derived = validator.validate_property_items(
        _items(
            _aggregate(
                operator="count_distinct",
                target_property="order_id",
                filters=[{"property": "status", "operator": "eq", "value": "paid"}],
                period={"days": 7, "time_property": "created_at"},
            )
        )
    )[1]
    assert derived["derivation"]["operator"] == "count_distinct"
    assert derived["derivation"]["filters"][0]["value"] == "paid"
    assert derived["derivation"]["period"] == {"days": 7, "time_property": "created_at"}

    with pytest.raises(OntologyInvalidError, match="target_property"):
        validator.validate_property_items(_items(_aggregate(operator="count_distinct", target_property="")))
    with pytest.raises(OntologyInvalidError, match="仅支持 aggregate"):
        validator.validate_property_items(
            _items(
                _aggregate(
                    kind="select",
                    operator="",
                    target_property="name",
                    filters=[{"property": "status"}],
                )
            )
        )
    with pytest.raises(OntologyInvalidError, match="过滤操作符"):
        validator.validate_property_items(_items(_aggregate(filters=[{"property": "status", "operator": "like"}])))
    with pytest.raises(OntologyInvalidError, match="in 过滤"):
        validator.validate_property_items(_items(_aggregate(filters=[{"property": "status", "operator": "in"}])))
    with pytest.raises(OntologyInvalidError, match="period.days"):
        validator.validate_property_items(_items(_aggregate(period={"days": 0, "time_property": "created_at"})))
    with pytest.raises(OntologyInvalidError, match="period"):
        validator.validate_property_items(_items(_aggregate(period="bad")))


def test_derived_sql_count_distinct_period_and_filters():
    from apps.chat_data_app.services.ontology.object_projection import (
        _build_derived_value_sql,
        _derived_extra_where,
    )

    sql = _build_derived_value_sql("orders", "customer_id", "C1", operator="count_distinct", target_column="order_id")
    assert "COUNT(DISTINCT `order_id`)" in sql

    mappings = {"status": "status", "created_at": "created_at"}
    dimensions = {
        "status": {"table": "orders", "column": "status"},
        "created_at": {"table": "orders", "column": "created_at"},
    }
    extra = _derived_extra_where(
        {
            "filters": [{"property": "status", "operator": "eq", "value": "paid"}],
            "period": {"days": 7, "time_property": "created_at"},
        },
        mappings,
        dimensions,
        "orders",
    )
    assert "`status` = 'paid'" in extra
    assert "`created_at` >=" in extra


def test_validator_derivation_filter_and_period_edge_cases():
    with pytest.raises(OntologyInvalidError, match="不超过 5"):
        validator.validate_property_items(_items(_aggregate(filters=[{}] * 6)))
    with pytest.raises(OntologyInvalidError, match="条目必须是对象"):
        validator.validate_property_items(_items(_aggregate(filters=["bad"])))
    with pytest.raises(OntologyInvalidError, match="仅支持 aggregate"):
        validator.validate_property_items(
            _items(
                _aggregate(kind="select", operator="", target_property="name", period={"days": 1, "time_property": "x"})
            )
        )
    with pytest.raises(OntologyInvalidError, match="必须是对象"):
        validator.validate_property_items(_items(_aggregate(period="bad")))
    with pytest.raises(OntologyInvalidError, match="period.days"):
        validator.validate_property_items(_items(_aggregate(period={"days": "x", "time_property": "created_at"})))
    with pytest.raises(Exception, match="时间窗属性"):
        validator.validate_property_items(_items(_aggregate(period={"days": 7, "time_property": ""})))


def test_derived_extra_where_guards():
    from apps.chat_data_app.services.ontology.object_projection import ObjectProjectionError, _derived_extra_where

    with pytest.raises(ObjectProjectionError, match="过滤操作符"):
        _derived_extra_where(
            {"filters": [{"property": "status", "operator": "like"}]},
            {"status": "status"},
            {"status": {"table": "orders", "column": "status"}},
            "orders",
        )
    with pytest.raises(ObjectProjectionError, match="过滤属性与关联表"):
        _derived_extra_where(
            {"filters": [{"property": "status", "operator": "eq", "value": "x"}]},
            {"status": "status"},
            {"status": {"table": "other", "column": "status"}},
            "orders",
        )
    assert _derived_extra_where({}, {}, {}, "orders") == ""


def test_service_derivation_filter_and_period_validation():
    import asyncio

    from apps.chat_data_app.services.ontology.ontology_service import OntologyService

    class FakeDao:
        async def list_properties_async(self, _object_type_id):
            return [
                {"property_name": "status", "value_type": "String"},
                {"property_name": "created_at", "value_type": "DateTime"},
                {"property_name": "bad_time", "value_type": "String"},
                {"property_name": "derived", "value_type": "Integer", "derivation": {"kind": "aggregate"}},
            ]

    service = OntologyService(dao=FakeDao())
    cache: dict = {}
    asyncio.run(
        service._validate_derivation_filters(
            1,
            {
                "filters": [{"property": "status", "operator": "eq", "value": "paid"}],
                "period": {"days": 7, "time_property": "created_at"},
            },
            cache,
            "派生属性 count",
        )
    )
    with pytest.raises(OntologyInvalidError, match="过滤属性不存在"):
        asyncio.run(
            service._validate_derivation_filters(1, {"filters": [{"property": "missing"}]}, {}, "派生属性 count")
        )
    with pytest.raises(OntologyInvalidError, match="过滤属性不能是派生"):
        asyncio.run(
            service._validate_derivation_filters(1, {"filters": [{"property": "derived"}]}, {}, "派生属性 count")
        )
    with pytest.raises(OntologyInvalidError, match="时间窗属性不存在"):
        asyncio.run(
            service._validate_derivation_filters(
                1, {"period": {"days": 7, "time_property": "missing"}}, {}, "派生属性 count"
            )
        )
    with pytest.raises(OntologyInvalidError, match="Date/DateTime"):
        asyncio.run(
            service._validate_derivation_filters(
                1, {"period": {"days": 7, "time_property": "bad_time"}}, {}, "派生属性 count"
            )
        )


def test_derived_extra_where_period_table_mismatch():
    from apps.chat_data_app.services.ontology.object_projection import ObjectProjectionError, _derived_extra_where

    with pytest.raises(ObjectProjectionError, match="时间窗属性与关联表"):
        _derived_extra_where(
            {"period": {"days": 7, "time_property": "created_at"}},
            {"created_at": "created_at"},
            {"created_at": {"table": "other", "column": "created_at"}},
            "orders",
        )


def test_preserve_ontology_id_on_edit():
    from apps.chat_data_app.services.ontology.ontology_service import _preserve_ontology_id

    assert _preserve_ontology_id({"display_name": "x"}, {"ontology_id": 2}, True)["ontology_id"] == 2
    assert _preserve_ontology_id({"display_name": "x"}, {"ontology_id": 2}, False).get("ontology_id") is None
    assert _preserve_ontology_id({"ontology_id": None}, {"ontology_id": 2}, True)["ontology_id"] is None


def test_validator_extra_derivation_and_mapping_guards():
    with pytest.raises(OntologyInvalidError, match="derivation 必须是对象"):
        validator.validate_property_items(_items(_property("x", derivation="bad")))
    with pytest.raises(OntologyInvalidError, match="必须提供 target_property"):
        validator.validate_property_items(
            _items(_property("x", derivation={"kind": "select", "relation_type_name": "r"}))
        )
    with pytest.raises(OntologyInvalidError, match="不支持字段"):
        validator.validate_property_items(_items(_property("x", render_hints={"bad": True})))
