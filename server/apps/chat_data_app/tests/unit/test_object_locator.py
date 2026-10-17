"""本体对象定位器单测：报告组装、范围收敛与关系条目（纯函数，不触库）。"""

from __future__ import annotations

from typing import Any

import pytest

from apps.chat_data_app.services.ontology import object_locator


def _bundle() -> dict[str, Any]:
    return {
        "objects": [
            {"id": 1, "object_name": "order", "display_name": "订单", "status": "certified"},
            {"id": 2, "object_name": "user", "display_name": "用户", "status": "experimental"},
        ],
        "properties": [
            {
                "object_id": 1,
                "property_name": "order_id",
                "display_name": "订单编号",
                "value_type": "String",
                "is_identifier": True,
            },
            {
                "object_id": 1,
                "property_name": "amount",
                "display_name": "订单金额",
                "value_type": "Decimal",
                "is_identifier": False,
            },
            {
                "object_id": 1,
                "property_name": "city",
                "display_name": "城市",
                "value_type": "String",
                "is_identifier": False,
            },
            {
                "object_id": 2,
                "property_name": "user_id",
                "display_name": "用户编号",
                "value_type": "String",
                "is_identifier": True,
            },
        ],
        "relations": [
            {
                "relation_name": "order_belongs_to_user",
                "from_object_id": 1,
                "to_object_id": 2,
                "multiplicity": "many_to_one",
                "from_verbalize": "订单归属用户",
                "to_verbalize": "用户拥有订单",
            }
        ],
        "mappings": [
            {
                "object_id": 1,
                "semantic_config_id": "sc_order",
                "column_mappings": {"order_id": "订单编号", "city": "城市"},
            },
            {
                "object_id": 2,
                "semantic_config_id": "sc_user",
                "column_mappings": {"user_id": "用户编号"},
            },
        ],
    }


def _report(object_names: list[str], **kwargs: Any) -> dict[str, Any]:
    return object_locator.build_object_locator_report(
        _bundle(),
        object_names=object_names,
        config_names={"sc_order": "订单模型", "sc_user": "用户模型"},
        model_files={"sc_order": "订单模型__1234.yaml", "sc_user": "用户模型__5678.yaml"},
        **kwargs,
    )


def test_locate_enabled_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", raising=False)
    assert object_locator.object_locator_enabled() is False
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    assert object_locator.object_locator_enabled() is True


def test_report_single_object_full_shape() -> None:
    report = _report(["order"])
    assert report["not_found"] == []
    entry = report["objects"][0]
    assert entry["object"] == "order"
    assert entry["display_name"] == "订单"
    assert entry["status"] == "certified"
    # 属性按名排序，携带 value_type / is_identifier
    assert entry["properties"] == [
        {"name": "amount", "display_name": "订单金额", "value_type": "Decimal", "is_identifier": False},
        {"name": "city", "display_name": "城市", "value_type": "String", "is_identifier": False},
        {"name": "order_id", "display_name": "订单编号", "value_type": "String", "is_identifier": True},
    ]
    # 绑定：模型名 + 快照文件名 + 属性→维度对照（按键排序）
    assert entry["bindings"] == [
        {
            "semantic_config_id": "sc_order",
            "model_name": "订单模型",
            "model_file": "订单模型__1234.yaml",
            "column_mappings": {"city": "城市", "order_id": "订单编号"},
        }
    ]
    # 关系：本对象为 from 端，取 from_verbalize
    assert entry["relations"] == [
        {
            "relation": "order_belongs_to_user",
            "related_object": "user",
            "direction": "from",
            "multiplicity": "many_to_one",
            "verbalize": "订单归属用户",
        }
    ]


def test_report_relation_to_direction_verbalize() -> None:
    entry = _report(["user"])["objects"][0]
    assert entry["relations"][0]["direction"] == "to"
    assert entry["relations"][0]["related_object"] == "order"
    assert entry["relations"][0]["verbalize"] == "用户拥有订单"


def test_report_multiple_objects_preserve_order_and_dedup() -> None:
    report = _report(["user", "order", "user"])
    assert [item["object"] for item in report["objects"]] == ["user", "order"]
    assert report["not_found"] == []


def test_report_multi_binding_and_config_filter() -> None:
    bundle = _bundle()
    bundle["mappings"].append(
        {"object_id": 1, "semantic_config_id": "sc_order_2", "column_mappings": {"order_id": "订单编号2"}}
    )
    report = object_locator.build_object_locator_report(
        bundle,
        object_names=["order"],
        config_names={"sc_order": "订单模型", "sc_order_2": "订单模型二"},
        model_files={"sc_order": "订单模型__1234.yaml", "sc_order_2": "订单模型二__5678.yaml"},
    )
    # 多绑定全部返回（导航视角），按 config_id 升序
    assert [item["semantic_config_id"] for item in report["objects"][0]["bindings"]] == ["sc_order", "sc_order_2"]
    # config 过滤后只留指定绑定
    filtered = object_locator.build_object_locator_report(
        bundle,
        object_names=["order"],
        semantic_config_id="sc_order_2",
    )
    assert [item["semantic_config_id"] for item in filtered["objects"][0]["bindings"]] == ["sc_order_2"]


def test_report_not_found_lists_available() -> None:
    report = _report(["ghost", "customer"])
    assert report["objects"] == []
    assert [item["object"] for item in report["not_found"]] == ["ghost", "customer"]
    assert "order、user" in report["not_found"][0]["available"]


def test_report_empty_mappings_binding_dropped() -> None:
    bundle = _bundle()
    bundle["mappings"][0]["column_mappings"] = {}
    report = object_locator.build_object_locator_report(bundle, object_names=["order"])
    assert report["objects"][0]["bindings"] == []
    # 属性与关系不受映射影响
    assert report["objects"][0]["properties"]


def test_report_object_without_mappings_still_listed() -> None:
    bundle = _bundle()
    bundle["mappings"] = [m for m in bundle["mappings"] if m["object_id"] != 2]
    report = object_locator.build_object_locator_report(bundle, object_names=["user"])
    entry = report["objects"][0]
    assert entry["object"] == "user"
    assert entry["bindings"] == []


def test_report_empty_objects_rejected() -> None:
    with pytest.raises(ValueError, match="objects 不能为空"):
        object_locator.build_object_locator_report(_bundle(), object_names=[])
    with pytest.raises(ValueError, match="objects 不能为空"):
        object_locator.build_object_locator_report(_bundle(), object_names=["", "  "])


def test_report_over_batch_limit_rejected() -> None:
    with pytest.raises(ValueError, match="单次最多定位 10 个对象"):
        object_locator.build_object_locator_report(_bundle(), object_names=[f"obj{i}" for i in range(11)])


def test_usage_hint_mentions_execute_query() -> None:
    hint = object_locator.build_locator_usage_hint()
    assert "execute_query" in hint
    assert "semantic/<model_file>" in hint
