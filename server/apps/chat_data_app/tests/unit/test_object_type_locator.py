"""本体对象类型定位器单测：报告组装、范围收敛与关系类型条目（纯函数，不触库）。"""

from __future__ import annotations

from typing import Any

import pytest

from apps.chat_data_app.services.ontology import object_type_locator


def _bundle() -> dict[str, Any]:
    return {
        "object_types": [
            {"id": 1, "object_type_name": "order", "display_name": "订单", "status": "certified"},
            {"id": 2, "object_type_name": "user", "display_name": "用户", "status": "experimental"},
        ],
        "properties": [
            {
                "object_type_id": 1,
                "property_name": "order_id",
                "display_name": "订单编号",
                "value_type": "String",
                "is_identifier": True,
            },
            {
                "object_type_id": 1,
                "property_name": "amount",
                "display_name": "订单金额",
                "value_type": "Decimal",
                "is_identifier": False,
            },
            {
                "object_type_id": 1,
                "property_name": "city",
                "display_name": "城市",
                "value_type": "String",
                "is_identifier": False,
            },
            {
                "object_type_id": 2,
                "property_name": "user_id",
                "display_name": "用户编号",
                "value_type": "String",
                "is_identifier": True,
            },
        ],
        "relation_types": [
            {
                "relation_type_name": "order_belongs_to_user",
                "from_object_type_id": 1,
                "to_object_type_id": 2,
                "multiplicity": "many_to_one",
                "from_verbalize": "订单归属用户",
                "to_verbalize": "用户拥有订单",
            }
        ],
        "mappings": [
            {
                "object_type_id": 1,
                "semantic_config_id": "sc_order",
                "column_mappings": {"order_id": "订单编号", "city": "城市"},
            },
            {
                "object_type_id": 2,
                "semantic_config_id": "sc_user",
                "column_mappings": {"user_id": "用户编号"},
            },
        ],
    }


def _report(object_type_names: list[str], **kwargs: Any) -> dict[str, Any]:
    return object_type_locator.build_object_type_locator_report(
        _bundle(),
        object_type_names=object_type_names,
        config_names={"sc_order": "订单模型", "sc_user": "用户模型"},
        model_files={"sc_order": "订单模型__1234.yaml", "sc_user": "用户模型__5678.yaml"},
        **kwargs,
    )


def test_locate_enabled_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", raising=False)
    assert object_type_locator.object_type_locator_enabled() is False
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    assert object_type_locator.object_type_locator_enabled() is True


def test_report_single_object_full_shape() -> None:
    report = _report(["order"])
    assert report["not_found"] == []
    entry = report["object_types"][0]
    assert entry["object_type"] == "order"
    assert entry["display_name"] == "订单"
    assert entry["status"] == "certified"
    # 属性按名排序，携带 value_type / is_identifier
    assert entry["properties"] == [
        {"name": "amount", "display_name": "订单金额", "value_type": "Decimal", "is_identifier": False},
        {"name": "city", "display_name": "城市", "value_type": "String", "is_identifier": False},
        {"name": "order_id", "display_name": "订单编号", "value_type": "String", "is_identifier": True},
    ]
    # 绑定：模型名 + 快照文件名 + 属性→维度对照（按键排序）；无比对基准时不标失修
    assert entry["bindings"] == [
        {
            "semantic_config_id": "sc_order",
            "model_name": "订单模型",
            "model_file": "订单模型__1234.yaml",
            "column_mappings": {"city": "城市", "order_id": "订单编号"},
            "healthy": True,
            "stale_dimensions": [],
        }
    ]
    # 关系：本对象为 from 端，取 from_verbalize；无比对基准时不标失修
    assert entry["relation_types"] == [
        {
            "relation": "order_belongs_to_user",
            "related_object": "user",
            "direction": "from",
            "multiplicity": "many_to_one",
            "verbalize": "订单归属用户",
        }
    ]


def test_report_marks_stale_dimensions_when_basis_provided() -> None:
    """提供维度名基准后，映射右值不在基准内的条目标 stale（healthy=false）。"""
    report = _report(
        ["order"],
        dimension_names={"sc_order": {"订单编号"}, "sc_user": {"用户编号"}},
    )
    binding = report["object_types"][0]["bindings"][0]
    assert binding["healthy"] is False
    assert binding["stale_dimensions"] == [{"property": "city", "dimension": "城市"}]
    # 健康条目不标注
    assert binding["column_mappings"] == {"city": "城市", "order_id": "订单编号"}


def test_report_marks_stale_relation_for_unmapped_attribute() -> None:
    """本侧关系列属性存在但未映射（未出现在任何绑定左值）→ relation 标 stale。"""
    bundle = _bundle()
    bundle["relation_types"][0]["from_columns"] = ["order_id"]
    del bundle["mappings"][0]["column_mappings"]["order_id"]
    report = object_type_locator.build_object_type_locator_report(
        bundle,
        object_type_names=["order"],
        dimension_names={"sc_order": {"订单编号", "城市"}},
    )
    relation = report["object_types"][0]["relation_types"][0]
    assert relation["stale"] is True
    assert "order_id" in relation["stale_reason"]


def test_report_marks_stale_relation_for_missing_attribute() -> None:
    """本侧关系列属性已不存在于对象属性清单 → relation 标 stale。"""
    bundle = _bundle()
    bundle["relation_types"][0]["from_columns"] = ["ghost_column"]
    report = object_type_locator.build_object_type_locator_report(
        bundle,
        object_type_names=["order"],
        dimension_names={"sc_order": {"订单编号", "城市"}},
    )
    relation = report["object_types"][0]["relation_types"][0]
    assert relation["stale"] is True
    assert "ghost_column" in relation["stale_reason"]


def test_report_healthy_relation_not_marked_stale() -> None:
    """关系列存在且已映射 → 不标 stale。"""
    bundle = _bundle()
    bundle["relation_types"][0]["from_columns"] = ["order_id"]
    report = object_type_locator.build_object_type_locator_report(
        bundle,
        object_type_names=["order"],
        dimension_names={"sc_order": {"订单编号", "城市"}},
    )
    relation = report["object_types"][0]["relation_types"][0]
    assert "stale" not in relation


def test_report_relation_to_direction_verbalize() -> None:
    entry = _report(["user"])["object_types"][0]
    assert entry["relation_types"][0]["direction"] == "to"
    assert entry["relation_types"][0]["related_object"] == "order"
    assert entry["relation_types"][0]["verbalize"] == "用户拥有订单"


def test_report_multiple_objects_preserve_order_and_dedup() -> None:
    report = _report(["user", "order", "user"])
    assert [item["object_type"] for item in report["object_types"]] == ["user", "order"]
    assert report["not_found"] == []


def test_report_multi_binding_and_config_filter() -> None:
    bundle = _bundle()
    bundle["mappings"].append(
        {"object_type_id": 1, "semantic_config_id": "sc_order_2", "column_mappings": {"order_id": "订单编号2"}}
    )
    report = object_type_locator.build_object_type_locator_report(
        bundle,
        object_type_names=["order"],
        config_names={"sc_order": "订单模型", "sc_order_2": "订单模型二"},
        model_files={"sc_order": "订单模型__1234.yaml", "sc_order_2": "订单模型二__5678.yaml"},
    )
    # 多绑定全部返回（导航视角），按 config_id 升序
    assert [item["semantic_config_id"] for item in report["object_types"][0]["bindings"]] == ["sc_order", "sc_order_2"]
    # config 过滤后只留指定绑定
    filtered = object_type_locator.build_object_type_locator_report(
        bundle,
        object_type_names=["order"],
        semantic_config_id="sc_order_2",
    )
    assert [item["semantic_config_id"] for item in filtered["object_types"][0]["bindings"]] == ["sc_order_2"]


def test_report_not_found_lists_available() -> None:
    report = _report(["ghost", "customer"])
    assert report["object_types"] == []
    assert [item["object_type"] for item in report["not_found"]] == ["ghost", "customer"]
    assert "order、user" in report["not_found"][0]["available"]


def test_report_empty_mappings_binding_dropped() -> None:
    bundle = _bundle()
    bundle["mappings"][0]["column_mappings"] = {}
    report = object_type_locator.build_object_type_locator_report(bundle, object_type_names=["order"])
    assert report["object_types"][0]["bindings"] == []
    # 属性与关系不受映射影响
    assert report["object_types"][0]["properties"]


def test_report_object_without_mappings_still_listed() -> None:
    bundle = _bundle()
    bundle["mappings"] = [m for m in bundle["mappings"] if m["object_type_id"] != 2]
    report = object_type_locator.build_object_type_locator_report(bundle, object_type_names=["user"])
    entry = report["object_types"][0]
    assert entry["object_type"] == "user"
    assert entry["bindings"] == []


def test_report_empty_objects_rejected() -> None:
    with pytest.raises(ValueError, match="object_types 不能为空"):
        object_type_locator.build_object_type_locator_report(_bundle(), object_type_names=[])
    with pytest.raises(ValueError, match="object_types 不能为空"):
        object_type_locator.build_object_type_locator_report(_bundle(), object_type_names=["", "  "])


def test_report_over_batch_limit_rejected() -> None:
    with pytest.raises(ValueError, match="单次最多定位 10 个对象"):
        object_type_locator.build_object_type_locator_report(
            _bundle(), object_type_names=[f"obj{i}" for i in range(11)]
        )


def test_usage_hint_mentions_execute_query() -> None:
    hint = object_type_locator.build_locator_usage_hint()
    assert "execute_query" in hint
    assert "semantic/<model_file>" in hint


# ---------------------------------------------------------------------------
# build_object_list_report（list_object_types 轻量清单）
# ---------------------------------------------------------------------------


def test_object_list_lightweight_sorted_with_model_binding() -> None:
    """清单按对象名升序；只含 名字/显示名/描述 + 绑定模型一跳，无详情。"""
    report = object_type_locator.build_object_list_report(
        _bundle(),
        config_names={"sc_order": "订单模型", "sc_user": "用户模型"},
        model_files={"sc_order": "订单模型__1234.yaml", "sc_user": "用户模型__5678.yaml"},
    )
    assert report["count"] == 2
    assert [item["object_type"] for item in report["object_types"]] == ["order", "user"]
    order_entry = report["object_types"][0]
    assert order_entry["display_name"] == "订单"
    assert order_entry["description"] == ""
    assert order_entry["models"] == [
        {"semantic_config_id": "sc_order", "model_name": "订单模型", "model_file": "订单模型__1234.yaml"}
    ]
    assert "properties" not in order_entry
    assert "bindings" not in order_entry
    assert "relation_types" not in order_entry
    # user 绑定 sc_user，模型名/文件名取传入索引
    assert report["object_types"][1]["models"] == [
        {"semantic_config_id": "sc_user", "model_name": "用户模型", "model_file": "用户模型__5678.yaml"}
    ]


def test_object_list_multi_binding_sorted_by_config_id() -> None:
    """对象多模型绑定时按 semantic_config_id 升序；缺索引的模型名留空。"""
    bundle = _bundle()
    bundle["mappings"].append(
        {"object_type_id": 1, "semantic_config_id": "sc_order_2", "column_mappings": {"order_id": "订单编号2"}}
    )
    report = object_type_locator.build_object_list_report(bundle)
    order_models = report["object_types"][0]["models"]
    assert [item["semantic_config_id"] for item in order_models] == ["sc_order", "sc_order_2"]
    assert order_models[1]["model_name"] == ""
    assert order_models[1]["model_file"] == ""


def test_object_list_skips_blank_object_type_names() -> None:
    """bundle 里对象名空白的条目跳过，不进清单。"""
    bundle = _bundle()
    bundle["object_types"].append({"id": 3, "object_type_name": "  ", "display_name": "空白"})
    report = object_type_locator.build_object_list_report(_bundle())
    assert report["count"] == 2


def test_object_list_empty_bundle() -> None:
    """空 bundle（无启用模型绑定对象）→ 空清单不报错。"""
    report = object_type_locator.build_object_list_report({})
    assert report == {"count": 0, "object_types": []}


def test_object_list_usage_hint_guides_next_steps() -> None:
    """清单 usage 指引：单对象直查，详情升级 locate_object_type。"""
    hint = object_type_locator.build_object_list_usage_hint()
    assert "execute_query" in hint
    assert "locate_object_type" in hint
