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
