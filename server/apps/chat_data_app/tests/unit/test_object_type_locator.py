"""本体对象类型导航器单测：报告组装、域内收敛与关系物理列翻译（纯函数，不触库）。

2026-10-23 对象取数迁移 Logical Dataset source binding：导航报告只依赖
Dataset 数据源绑定（属性→逻辑列→物理列），不再读取语义模型/维度。
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.chat_data_app.services.ontology import object_type_locator


def _binding(
    dataset_id: int,
    dataset_name: str,
    table: str,
    column_mappings: dict[str, str],
    columns: dict[str, str],
) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "dataset_display_name": f"{dataset_name}-展示",
        "physical_table": table,
        "members": [{"member_datasource_id": f"m{dataset_id}", "physical_table": table}],
        "column_mappings": column_mappings,
        "columns": columns,
    }


def _bundle() -> dict[str, Any]:
    return {
        "datasource_group_id": "ds_a",
        "object_types": [
            {
                "id": 1,
                "object_type_name": "order",
                "display_name": "订单",
                "status": "certified",
                "description": "销售订单",
                "source_binding": _binding(
                    11,
                    "order_dataset",
                    "DWD_ORDER",
                    {"order_id": "order_key", "amount": "amount_value", "user_id": "buyer_id"},
                    {"order_key": "ORDER_ID", "amount_value": "AMOUNT", "buyer_id": "USER_ID"},
                ),
            },
            {
                "id": 2,
                "object_type_name": "user",
                "display_name": "用户",
                "status": "experimental",
                "source_binding": _binding(
                    12,
                    "user_dataset",
                    "DWD_USER",
                    {"user_id": "uid"},
                    {"uid": "UID"},
                ),
            },
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
                "property_name": "user_id",
                "display_name": "下单用户",
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
                "from_columns": ["user_id"],
                "to_columns": ["user_id"],
            }
        ],
    }


def _report(object_type_names: list[str]) -> dict[str, Any]:
    return object_type_locator.build_object_type_locator_report(_bundle(), object_type_names=object_type_names)


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
    properties = {prop["name"]: prop for prop in entry["properties"]}
    assert properties["order_id"]["is_identifier"] is True
    assert properties["amount"]["value_type"] == "Decimal"
    binding = entry["source_binding"]
    assert binding["dataset"] == "order_dataset"
    assert binding["dataset_display_name"] == "order_dataset-展示"
    assert binding["physical_table"] == "DWD_ORDER"
    assert binding["members"] == ["m11"]
    assert binding["column_mappings"]["user_id"] == "buyer_id"
    assert binding["columns"]["buyer_id"] == "USER_ID"
    relation = entry["relation_types"][0]
    assert relation == {
        "relation": "order_belongs_to_user",
        "related_object": "user",
        "direction": "from",
        "multiplicity": "many_to_one",
        "verbalize": "订单归属用户",
    }


def test_report_relation_to_direction_verbalize() -> None:
    """对端视角：user 侧的同一关系方向为 to，口语化取 to_verbalize。"""
    entry = _report(["user"])["object_types"][0]
    relation = entry["relation_types"][0]
    assert relation["related_object"] == "order"
    assert relation["direction"] == "to"
    assert relation["verbalize"] == "用户拥有订单"


def test_report_multiple_objects_preserve_order_and_dedup() -> None:
    report = _report(["user", "order", "user", " "])
    assert [item["object_type"] for item in report["object_types"]] == ["user", "order"]
    assert report["not_found"] == []


def test_report_not_found_lists_available() -> None:
    report = _report(["ghost"])
    assert report["object_types"] == []
    assert report["not_found"] == [
        {"object_type": "ghost", "reason": "对象类型不在当前业务域本体范围", "available": "order、user"}
    ]


def test_report_empty_bundle_not_found_reason() -> None:
    report = object_type_locator.build_object_type_locator_report(
        {"object_types": [], "properties": [], "relation_types": []}, object_type_names=["order"]
    )
    assert report["not_found"][0]["reason"] == "当前业务域没有可用本体对象类型"
    assert report["not_found"][0]["available"] == ""


def test_report_rejects_empty_names() -> None:
    with pytest.raises(ValueError, match="object_types 不能为空"):
        object_type_locator.build_object_type_locator_report(_bundle(), object_type_names=["  "])


def test_report_over_batch_limit_rejected() -> None:
    with pytest.raises(ValueError, match="单次最多定位 10 个对象"):
        object_type_locator.build_object_type_locator_report(
            _bundle(), object_type_names=[f"obj{index}" for index in range(11)]
        )


def test_report_rejects_non_dict_bundle() -> None:
    with pytest.raises(ValueError, match="bundle 必须是对象"):
        object_type_locator.build_object_type_locator_report([], object_type_names=["order"])  # type: ignore[arg-type]


def test_object_list_reports_binding_summary_sorted() -> None:
    report = object_type_locator.build_object_list_report(_bundle())
    assert report["count"] == 2
    assert [item["object_type"] for item in report["object_types"]] == ["order", "user"]
    order = report["object_types"][0]
    assert order["dataset"] == "order_dataset"
    assert order["physical_table"] == "DWD_ORDER"
    assert order["member_datasource_ids"] == ["m11"]
    assert order["property_count"] == 3
    assert "properties" not in order
    assert "source_binding" not in order
    assert "relation_types" not in order


def test_object_list_skips_blank_object_type_names() -> None:
    bundle = _bundle()
    bundle["object_types"] = [*bundle["object_types"], {"id": 3, "object_type_name": "  "}]
    report = object_type_locator.build_object_list_report(bundle)
    assert [item["object_type"] for item in report["object_types"]] == ["order", "user"]


def test_object_list_empty_bundle() -> None:
    report = object_type_locator.build_object_list_report({"object_types": []})
    assert report == {"count": 0, "object_types": []}


def test_object_without_source_binding_is_listed_with_empty_binding() -> None:
    bundle = _bundle()
    bundle["object_types"] = [{"id": 9, "object_type_name": "loose", "status": "certified"}]
    bundle["properties"] = []
    bundle["relation_types"] = []
    entry = object_type_locator.build_object_type_locator_report(bundle, object_type_names=["loose"])["object_types"][0]
    assert entry["source_binding"]["dataset"] == ""
    assert entry["source_binding"]["column_mappings"] == {}


# ---------------------------------------------------------------------------
# search_around（一跳展开）
# ---------------------------------------------------------------------------


def test_search_around_translates_join_columns_via_dataset_bindings() -> None:
    report = object_type_locator.build_search_around_report(_bundle(), object_type="order")
    assert report["center"] == {"object_type": "order", "display_name": "订单"}
    assert report["not_found"] == []
    (hop,) = report["hops"]
    assert hop["relation"] == "order_belongs_to_user"
    assert hop["direction"] == "from"
    assert hop["join"] == {"from_columns": ["USER_ID"], "to_columns": ["UID"]}
    neighbor = hop["neighbor"]
    assert neighbor["object_type"] == "user"
    assert neighbor["source_binding"]["dataset"] == "user_dataset"
    assert neighbor["relations"] == ["order_belongs_to_user"]
    assert "relation_types" not in neighbor


def test_search_around_marks_unresolved_columns() -> None:
    bundle = _bundle()
    bundle["relation_types"][0]["from_columns"] = ["user_id", "ghost_attr"]
    (hop,) = object_type_locator.build_search_around_report(bundle, object_type="order")["hops"]
    assert hop["join"]["from_columns"] == ["USER_ID", "ghost_attr"]
    assert hop["join"]["unresolved"] is True


def test_search_around_relation_filter() -> None:
    report = object_type_locator.build_search_around_report(
        _bundle(), object_type="order", relation="order_belongs_to_user"
    )
    assert len(report["hops"]) == 1
    report = object_type_locator.build_search_around_report(_bundle(), object_type="order", relation="nope")
    assert report["hops"] == []


def test_search_around_center_not_found() -> None:
    report = object_type_locator.build_search_around_report(_bundle(), object_type="ghost")
    assert report["center"] is None
    assert report["hops"] == []
    assert report["not_found"][0]["object_type"] == "ghost"


def test_search_around_rejects_blank_object_type() -> None:
    with pytest.raises(ValueError, match="object_type 不能为空"):
        object_type_locator.build_search_around_report(_bundle(), object_type=" ")


def test_usage_hints_guide_ontology_query_only() -> None:
    locator_hint = object_type_locator.build_locator_usage_hint()
    list_hint = object_type_locator.build_object_list_usage_hint()
    around_hint = object_type_locator.build_search_around_usage_hint()
    for hint in (locator_hint, list_hint, around_hint):
        assert "execute_query" not in hint
    # 明确禁止回退语义查询（不再是「本体缺失则走语义链路」的旧口径）
    assert "回退语义查询" in locator_hint
    assert "不要回退语义模型查询" in around_hint
    assert "find_objects" in locator_hint
    assert "locate_object_type" in list_hint
    assert "search_around" in around_hint
