"""本体对象查询翻译器单测：DSL 组装、范围收敛报错与关系提示（纯函数，不触库）。"""

from __future__ import annotations

from typing import Any

import pytest

from apps.chat_data_app.services.ontology import object_query


def _bundle() -> dict[str, Any]:
    return {
        "objects": [
            {"id": 1, "object_name": "order", "display_name": "订单", "status": "certified"},
            {"id": 2, "object_name": "user", "display_name": "用户", "status": "experimental"},
        ],
        "properties": [
            {"object_id": 1, "property_name": "order_id", "is_identifier": True},
            {"object_id": 1, "property_name": "amount", "is_identifier": False},
            {"object_id": 1, "property_name": "city", "is_identifier": False},
            {"object_id": 2, "property_name": "user_id", "is_identifier": True},
        ],
        "relations": [
            {
                "relation_name": "order_belongs_to_user",
                "from_object_id": 1,
                "to_object_id": 2,
                "multiplicity": "many_to_one",
            }
        ],
        "mappings": [
            {
                "object_id": 1,
                "semantic_config_id": "sc_order",
                "column_mappings": {"order_id": "订单编号", "amount": "订单金额", "city": "城市"},
            },
            {
                "object_id": 2,
                "semantic_config_id": "sc_user",
                "column_mappings": {"user_id": "用户编号"},
            },
        ],
    }


def test_object_query_enabled_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_QUERY_ENABLED", raising=False)
    assert object_query.object_query_enabled() is False
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_QUERY_ENABLED", "1")
    assert object_query.object_query_enabled() is True


def test_plan_default_properties_and_dsl() -> None:
    plan = object_query.build_object_query_plan(_bundle(), object_name="order")
    assert plan.semantic_config_id == "sc_order"
    assert plan.object_display_name == "订单"
    # 缺省 = 全部已映射属性（升序）；displayName = 属性名（结果列别名）
    assert plan.columns == ["amount", "city", "order_id"]
    assert plan.dsl["dimensions"] == [
        {"name": "订单金额", "displayName": "amount"},
        {"name": "城市", "displayName": "city"},
        {"name": "订单编号", "displayName": "order_id"},
    ]
    assert plan.dsl["limit"] == 50
    assert plan.dsl["offset"] == 0
    assert "filters" not in plan.dsl
    assert "orderBy" not in plan.dsl
    assert plan.property_dimensions == {"amount": "订单金额", "city": "城市", "order_id": "订单编号"}


def test_plan_requested_properties_keep_order() -> None:
    plan = object_query.build_object_query_plan(_bundle(), object_name="order", properties=["city", "order_id"])
    assert plan.columns == ["city", "order_id"]
    assert plan.dsl["dimensions"] == [
        {"name": "城市", "displayName": "city"},
        {"name": "订单编号", "displayName": "order_id"},
    ]


def test_plan_dedup_same_dimension() -> None:
    bundle = _bundle()
    bundle["mappings"][0]["column_mappings"]["city"] = "订单金额"
    plan = object_query.build_object_query_plan(bundle, object_name="order", properties=["amount", "city"])
    # 两个属性映射到同一维度：维度只发一次，列以先到的属性命名
    assert plan.dsl["dimensions"] == [{"name": "订单金额", "displayName": "amount"}]
    assert plan.columns == ["amount", "city"]


def test_plan_unknown_property_error() -> None:
    with pytest.raises(ValueError, match="不存在属性: foo.*对象属性"):
        object_query.build_object_query_plan(_bundle(), object_name="order", properties=["foo"])


def test_plan_unmapped_property_error() -> None:
    bundle = _bundle()
    bundle["mappings"][0]["column_mappings"].pop("city")
    with pytest.raises(ValueError, match="未映射到语义模型维度: city"):
        object_query.build_object_query_plan(bundle, object_name="order", properties=["city"])


def test_plan_object_not_found_lists_candidates() -> None:
    with pytest.raises(ValueError, match="本体对象不存在或未绑定.*customer.*可查对象: order、user"):
        object_query.build_object_query_plan(_bundle(), object_name="customer")


def test_plan_empty_bundle_error() -> None:
    with pytest.raises(ValueError, match="没有绑定任何本体对象"):
        object_query.build_object_query_plan({"objects": [], "properties": [], "mappings": []}, object_name="order")


def test_plan_multi_mapping_requires_explicit_id() -> None:
    bundle = _bundle()
    bundle["mappings"].append(
        {"object_id": 1, "semantic_config_id": "sc_order_2", "column_mappings": {"order_id": "订单编号"}}
    )
    with pytest.raises(ValueError, match="绑定了多个语义模型.*sc_order、sc_order_2"):
        object_query.build_object_query_plan(bundle, object_name="order")
    plan = object_query.build_object_query_plan(bundle, object_name="order", semantic_config_id="sc_order_2")
    assert plan.semantic_config_id == "sc_order_2"
    assert plan.columns == ["order_id"]


def test_plan_explicit_mapping_not_bound_error() -> None:
    with pytest.raises(ValueError, match="对象未绑定语义模型 sc_x"):
        object_query.build_object_query_plan(_bundle(), object_name="order", semantic_config_id="sc_x")


def test_plan_no_usable_mapping_error() -> None:
    bundle = _bundle()
    bundle["mappings"][0]["column_mappings"] = {}
    with pytest.raises(ValueError, match="没有可用的语义模型映射"):
        object_query.build_object_query_plan(bundle, object_name="order")


def test_plan_filters_translation() -> None:
    plan = object_query.build_object_query_plan(
        _bundle(),
        object_name="order",
        filters=[
            {"property": "city", "operator": "=", "value": "上海"},
            {"and": [{"property": "amount", "operator": ">=", "value": 100}]},
            {
                "or": [
                    {"property": "order_id", "operator": "IS NULL"},
                    {"property": "city", "operator": "LIKE", "value": "%东%"},
                ]
            },
        ],
    )
    assert plan.dsl["filters"] == [
        {"name": "城市", "operator": "=", "value": "上海"},
        {"and": [{"name": "订单金额", "operator": ">=", "value": 100}]},
        {
            "or": [
                {"name": "订单编号", "operator": "IS NULL"},
                {"name": "城市", "operator": "LIKE", "value": "%东%"},
            ]
        },
    ]


def test_plan_filter_unmapped_property_rejected() -> None:
    bundle = _bundle()
    bundle["mappings"][0]["column_mappings"].pop("city")
    with pytest.raises(ValueError, match="过滤属性未映射到语义模型维度: city"):
        object_query.build_object_query_plan(
            bundle, object_name="order", filters=[{"property": "city", "operator": "=", "value": "上海"}]
        )


def test_plan_filter_unknown_key_rejected() -> None:
    with pytest.raises(ValueError, match="含不支持的字段: column"):
        object_query.build_object_query_plan(
            _bundle(),
            object_name="order",
            filters=[{"property": "city", "column": "city", "operator": "=", "value": "x"}],
        )


def test_plan_filter_empty_property_rejected() -> None:
    with pytest.raises(ValueError, match="property 不能为空"):
        object_query.build_object_query_plan(
            _bundle(), object_name="order", filters=[{"property": "", "operator": "=", "value": "x"}]
        )


def test_plan_order_by_appends_column() -> None:
    plan = object_query.build_object_query_plan(
        _bundle(),
        object_name="order",
        properties=["amount"],
        order_by=[{"property": "order_id", "direction": "desc"}],
    )
    assert plan.dsl["orderBy"] == [{"name": "订单编号", "direction": "DESC"}]
    # 排序属性自动并入返回列
    assert plan.columns == ["amount", "order_id"]
    # 追加列同样带 displayName=属性名，SQL 结果列别名与 columns 一一对应
    assert plan.dsl["dimensions"] == [
        {"name": "订单金额", "displayName": "amount"},
        {"name": "订单编号", "displayName": "order_id"},
    ]
    assert plan.property_dimensions == {"amount": "订单金额", "order_id": "订单编号"}


def test_plan_filter_depth_capped() -> None:
    # 构造 11 层嵌套 or 树：超过上限即拒绝
    node: dict[str, Any] = {"property": "city", "operator": "=", "value": "上海"}
    for _ in range(11):
        node = {"or": [node]}
    with pytest.raises(ValueError, match="filters 嵌套层级超过上限 10"):
        object_query.build_object_query_plan(_bundle(), object_name="order", filters=[node])
    # 恰好 10 层不报错
    node = {"property": "city", "operator": "=", "value": "上海"}
    for _ in range(9):
        node = {"or": [node]}
    plan = object_query.build_object_query_plan(_bundle(), object_name="order", filters=[node])
    assert "filters" in plan.dsl


def test_plan_order_by_invalid_direction_rejected() -> None:
    with pytest.raises(ValueError, match="direction 只能是 ASC 或 DESC"):
        object_query.build_object_query_plan(
            _bundle(), object_name="order", order_by=[{"property": "amount", "direction": "SIDEWAYS"}]
        )


def test_plan_limit_offset_clamped() -> None:
    plan = object_query.build_object_query_plan(_bundle(), object_name="order", limit=99999, offset=-5)
    assert plan.dsl["limit"] == 1000
    assert plan.dsl["offset"] == 0


def test_relation_hint_and_empty() -> None:
    hint = object_query.build_relation_hint(_bundle(), "order")
    assert "user(many_to_one)" in hint
    assert "分别调用" in hint
    # 关系是双向端点：user 也能看到 order
    assert "order(many_to_one)" in object_query.build_relation_hint(_bundle(), "user")
    assert object_query.build_relation_hint(_bundle(), "ghost") == ""
