"""对象类型 ↔ 语义指标关联单测：解析、派生过滤翻译、投影 SQL 与工具行为。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.tools import ontology_tool
from apps.chat_data_app.services.ontology import object_metrics as object_metrics_module
from apps.chat_data_app.services.ontology.object_metrics import (
    ObjectMetricError,
    ObjectMetricNotExecutableError,
    list_object_metrics_async,
    resolve_metric_expressions,
    resolve_metric_projection_async,
    translate_metric_filters,
)
from apps.chat_data_app.services.ontology.object_projection import (
    ObjectProjectionError,
    build_object_metric_query,
)

DETAIL = {
    "id": 7,
    "object_type_name": "shipment",
    "display_name": "发货单",
    "properties": [
        {"property_name": "shipment_id", "is_identifier": True, "is_title": False, "value_type": "String"},
        {"property_name": "carrier", "is_identifier": False, "is_title": False, "value_type": "String"},
        {"property_name": "delivered_at", "is_identifier": False, "is_title": False, "value_type": "DateTime"},
        {"property_name": "amount", "is_identifier": False, "is_title": False, "value_type": "Decimal"},
    ],
    "source_binding": {
        "dataset_id": 1,
        "column_mappings": {
            "shipment_id": "shipment_id",
            "carrier": "carrier",
            "delivered_at": "delivered_at",
            "amount": "amount",
        },
    },
    "mappings": [
        {
            "semantic_config_id": "dataset",
            "column_mappings": {
                "shipment_id": "shipment_id",
                "carrier": "carrier",
                "delivered_at": "delivered_at",
                "amount": "amount",
            },
        }
    ],
}

DATASET = {
    "id": 1,
    "dataset_name": "shipments",
    "columns": [
        {"name": "shipment_id", "physical_column": "shipment_id", "data_type": "string"},
        {"name": "carrier", "physical_column": "carrier", "data_type": "string"},
        {"name": "delivered_at", "physical_column": "delivered_at", "data_type": "datetime"},
        {"name": "amount", "physical_column": "amount", "data_type": "number"},
    ],
    "bindings": [{"datasource_group_id": "ds_a", "member_datasource_id": "ORG_CODE1", "physical_table": "shipments"}],
}

LINK = {
    "id": 1,
    "dataset_id": 1,
    "dataset_name": "shipments",
    "semantic_config_id": "sc_ship",
    "semantic_config_name": "发货模型",
    "datasource_group_id": "ds_a",
    "table_name": "shipments",
    "column_mappings": {
        "shipment_id": "shipment_id",
        "carrier": "carrier",
        "delivered_at": "delivered_at",
        "amount": "amount",
    },
    "metric_scope": ["发货单数量", "近7日签收量", "客单价"],
    "lifecycle_status": "active",
}

CONFIG = {
    "id": "sc_ship",
    "name": "发货模型",
    "metrics": [
        {"name": "发货单数量", "expression": "COUNT(*)", "table": "shipments"},
        {"name": "发货金额", "expression": "SUM(amount)", "table": "shipments"},
        {
            "name": "近7日签收量",
            "metric_kind": "derived",
            "base_metric": "发货单数量",
            "period": {"kind": "last_n_days", "n": 7, "time_dimension": "delivered_at"},
            "table": "shipments",
        },
        {
            "name": "客单价",
            "metric_kind": "composite",
            "formula": "{发货金额} / {发货单数量}",
            "table": "shipments",
        },
    ],
    "dimensions": [
        {"name": "shipment_id", "table": "shipments", "column": "shipment_id"},
        {"name": "carrier", "table": "shipments", "column": "carrier"},
        {"name": "delivered_at", "table": "shipments", "column": "delivered_at", "logical_type": "datetime"},
        {"name": "amount", "table": "shipments", "column": "amount"},
    ],
}


def test_resolve_expressions_atomic_and_composite():
    resolved = resolve_metric_expressions(
        ["发货单数量", "客单价"],
        config=CONFIG,
        link=LINK,
        binding=DETAIL["source_binding"],
    )
    terms = {item["name"]: item for item in resolved["metric_terms"]}
    assert terms["发货单数量"]["expression"] == "COUNT(*)"
    assert "NULLIF(COUNT(*), 0)" in terms["客单价"]["expression"]
    assert resolved["metric_filters"] == []
    assert [item["kind"] for item in resolved["metric_meta"]] == ["atomic", "composite"]


def test_resolve_expressions_derived_alone_translates_filters():
    resolved = resolve_metric_expressions(
        ["近7日签收量"],
        config=CONFIG,
        link=LINK,
        binding=DETAIL["source_binding"],
    )
    terms = {item["name"]: item for item in resolved["metric_terms"]}
    assert terms["近7日签收量"]["expression"] == "COUNT(*)"
    # 派生指标时间窗翻译成对象属性过滤（语义维度 → Dataset 逻辑列 → 对象属性）
    operators = {(item["property"], item["operator"]) for item in resolved["metric_filters"]}
    assert ("delivered_at", "gte") in operators
    assert ("delivered_at", "lt") in operators


def test_resolve_expressions_rejects_mixed_derived_metrics():
    with pytest.raises(ObjectMetricNotExecutableError):
        resolve_metric_expressions(
            ["发货单数量", "近7日签收量"],
            config=CONFIG,
            link=LINK,
            binding=DETAIL["source_binding"],
        )


def test_translate_metric_filters_rejects_unsupported_operator():
    with pytest.raises(ObjectMetricNotExecutableError):
        translate_metric_filters(
            [{"dimension": "delivered_at", "operator": "like", "value": "2026-01"}],
            link=LINK,
            binding=DETAIL["source_binding"],
        )


def test_translate_metric_filters_rejects_unmapped_dimension():
    with pytest.raises(ObjectMetricNotExecutableError):
        translate_metric_filters(
            [{"dimension": "unknown_dim", "operator": "=", "value": "x"}],
            link=LINK,
            binding=DETAIL["source_binding"],
        )


def _patch_resolver(monkeypatch, link=None, config=None):
    async def fake_detail(_name: str):
        return dict(DETAIL)

    async def fake_dataset_by_id(dataset_id: int):
        return dict(DATASET) if int(dataset_id) == 1 else None

    async def fake_links(*_args, **_kwargs):
        return [dict(link or LINK)]

    async def fake_config(*_args, **_kwargs):
        return dict(config or CONFIG)

    async def fake_active_link(*_args, **_kwargs):
        return dict(link or LINK)

    monkeypatch.setattr(object_metrics_module.ontology_service, "get_object_type_async", fake_detail)
    monkeypatch.setattr(
        object_metrics_module.logical_dataset_service, "get_dataset_async_by_id_async", fake_dataset_by_id
    )
    monkeypatch.setattr(
        object_metrics_module.dataset_semantic_link_service, "list_links_for_dataset_id_async", fake_links
    )
    monkeypatch.setattr(object_metrics_module.dataset_semantic_link_service, "load_config_for_link_async", fake_config)
    monkeypatch.setattr(object_metrics_module.dataset_semantic_link_service, "get_active_link_async", fake_active_link)


def test_list_object_metrics_marks_executable(monkeypatch):
    _patch_resolver(monkeypatch)

    data = asyncio.run(list_object_metrics_async("shipment", datasource_group_id="ds_a"))
    assert data["dataset_name"] == "shipments"
    names = {item["name"] for item in data["metrics"]}
    assert {"发货单数量", "近7日签收量", "客单价"} <= names
    assert all(item["executable_in_scope"] for item in data["metrics"])


def test_list_object_metrics_draft_link_not_executable(monkeypatch):
    draft = {**LINK, "lifecycle_status": "draft"}
    _patch_resolver(monkeypatch, link=draft)

    data = asyncio.run(list_object_metrics_async("shipment", datasource_group_id="ds_a"))
    assert data["metrics"]
    assert all(not item["executable_in_scope"] for item in data["metrics"])
    assert all("active" in item["unavailable_reason"] for item in data["metrics"])


def test_resolve_projection_success(monkeypatch):
    _patch_resolver(monkeypatch)

    resolved = asyncio.run(resolve_metric_projection_async("shipment", ["发货单数量"], datasource_group_id="ds_a"))
    assert resolved["metric_terms"][0]["expression"] == "COUNT(*)"
    assert resolved["link"]["semantic_config_id"] == "sc_ship"
    assert resolved["datasource_group_id"] == "ds_a"


def test_resolve_projection_rejects_unknown_metric(monkeypatch):
    _patch_resolver(monkeypatch)

    with pytest.raises(ObjectMetricError):
        asyncio.run(resolve_metric_projection_async("shipment", ["不存在指标"], datasource_group_id="ds_a"))


def test_resolve_projection_rejects_draft_link(monkeypatch):
    _patch_resolver(monkeypatch, link={**LINK, "lifecycle_status": "draft"})

    with pytest.raises(ObjectMetricNotExecutableError):
        asyncio.run(resolve_metric_projection_async("shipment", ["发货单数量"], datasource_group_id="ds_a"))


def test_build_object_metric_query_sql():
    query = build_object_metric_query(
        DETAIL,
        {"dimensions": CONFIG["dimensions"]},
        group_by=["carrier"],
        metric_items=[{"name": "发货单数量", "expression": "COUNT(*)"}],
        filters=[{"property": "carrier", "operator": "eq", "value": "顺丰"}],
    )
    assert "(COUNT(*)) AS `发货单数量`" in query["sql"]
    assert "GROUP BY" in query["sql"]
    assert "WHERE" in query["sql"]
    assert query["metric_keys"] == ["发货单数量"]


def test_build_object_metric_query_rejects_unsafe_expression():
    with pytest.raises(ObjectProjectionError):
        build_object_metric_query(
            DETAIL,
            {"dimensions": CONFIG["dimensions"]},
            metric_items=[{"name": "x", "expression": "COUNT(*); DROP TABLE t"}],
        )


def _fake_payload(**overrides):
    payload = SimpleNamespace(
        object_type="shipment",
        group_by=["carrier"],
        filters=[],
        keyword="",
        metric_names=["发货单数量"],
    )
    for key, value in overrides.items():
        setattr(payload, key, value)
    return payload


def test_tool_aggregate_metrics_returns_ontology_mode(monkeypatch):
    async def fake_resolve(*_args, **_kwargs):
        return {
            "metric_terms": [{"name": "发货单数量", "expression": "COUNT(*)", "table": "shipments"}],
            "metric_filters": [{"property": "delivered_at", "operator": "gte", "value": "2026-10-01"}],
            "metric_meta": [{"name": "发货单数量", "kind": "atomic"}],
            "link": {"semantic_config_id": "sc_ship"},
        }

    async def fake_aggregate(*_args, **kwargs):
        assert kwargs["filters"][0]["property"] == "delivered_at"
        return {
            "object_type": "shipment",
            "group_by": ["carrier"],
            "metric_keys": ["发货单数量"],
            "groups": [{"keys": {"carrier": "顺丰"}, "metrics": {"发货单数量": 88}}],
            "sql": "SELECT ...",
            "execution_time_ms": 12,
            "member_datasource_id": "ORG_CODE1",
            "member_name": "财付通",
        }

    monkeypatch.setattr(ontology_tool, "resolve_metric_projection_async", fake_resolve)
    monkeypatch.setattr(ontology_tool, "aggregate_metric_projection", fake_aggregate)

    raw = asyncio.run(
        ontology_tool._run_aggregate_metrics(
            _fake_payload(),
            state={"execution_context": {"datasource_id": "ds_a"}, "user_id": "u1"},
            member_ids=["ORG_CODE1"],
            user_id="u1",
        )
    )
    payload = json.loads(raw)["payload"]
    assert payload["query_mode"] == "ontology_metric_aggregate"
    assert payload["results"][0]["member_name"] == "财付通"
    assert payload["results"][0]["total_count"] == 1
    assert payload["preview"]["metrics"] == ["发货单数量"]


def test_tool_input_literal_covers_all_payload_models():
    from apps.chat_data_app.agent.tools.ontology_tool import (
        ONTOLOGY_ACTION_PAYLOAD_MODELS,
        OntologyToolInput,
    )

    actions = set(OntologyToolInput.model_fields["action"].annotation.__args__)
    assert set(ONTOLOGY_ACTION_PAYLOAD_MODELS) <= actions
    validated = OntologyToolInput(action="list_object_metrics", action_payload={})
    assert validated.action == "list_object_metrics"


def test_tool_list_object_metrics(monkeypatch):
    async def fake_list(_name, **_kwargs):
        return {
            "object_type": "shipment",
            "dataset_name": "shipments",
            "datasource_group_id": "ds_a",
            "metrics": [{"name": "发货单数量", "kind": "atomic", "executable_in_scope": True}],
            "reason": "",
        }

    monkeypatch.setattr(ontology_tool, "list_object_metrics_async", fake_list)
    raw = asyncio.run(
        ontology_tool._run_list_object_metrics(
            payload=SimpleNamespace(object_type="shipment"),
            state={"user_id": "u1"},
            bound_datasource_id="ds_a",
            bound_user_id="u1",
        )
    )
    body = raw.split("\n", 1)[1] if raw.startswith("[ontology_tool]") else raw
    payload = json.loads(body)["payload"] if "payload" in json.loads(body) else json.loads(body)
    assert payload["object_types"][0]["metrics"][0]["name"] == "发货单数量"


def test_object_metric_helpers_cover_group_and_reasons():
    assert object_metrics_module._resolve_group_id(DATASET, "ds_x", None) == "ds_x"
    assert object_metrics_module._resolve_group_id(DATASET, "", ["ORG_CODE1"]) == "ds_a"
    assert object_metrics_module._resolve_group_id(DATASET, "", []) == "ds_a"
    assert object_metrics_module._resolve_group_id({}, "", []) == ""
    metric = CONFIG["metrics"][0]
    assert (
        object_metrics_module._metric_unavailable_reason(metric, None, LINK, DATASET, "ds_a")
        == "语义模型不存在或不可访问"
    )
    assert (
        object_metrics_module._metric_unavailable_reason(
            metric, CONFIG, {**LINK, "lifecycle_status": "draft"}, DATASET, "ds_a"
        ).startswith("关联未启用")
    )
    assert object_metrics_module._metric_unavailable_reason(
        {**metric, "table": "orders"}, CONFIG, LINK, DATASET, "ds_a"
    ).startswith("指标归属表")
    assert object_metrics_module._metric_unavailable_reason(metric, CONFIG, LINK, DATASET, "ds_a") == ""


def test_translate_filter_leaf_variants():
    link = {"column_mappings": {"carrier": "承运商"}}
    binding = {"column_mappings": {"carrier": "carrier"}}
    assert translate_metric_filters(
        [{"dimension": "承运商", "operator": "in", "value": ["顺丰", "中通"]}], link=link, binding=binding
    ) == [{"property": "carrier", "operator": "in", "values": ["顺丰", "中通"]}]
    assert translate_metric_filters(
        [{"dimension": "承运商", "operator": "=", "value": "顺丰"}], link=link, binding=binding
    ) == [{"property": "carrier", "operator": "eq", "value": "顺丰"}]
    with pytest.raises(ObjectMetricNotExecutableError, match="未映射到 Dataset 逻辑列"):
        translate_metric_filters(
            [{"dimension": "承运商", "operator": "=", "value": "顺丰"}],
            link={"column_mappings": {}},
            binding=binding,
        )


def test_resolve_metric_term_errors():
    base = {
        "metrics_map": {item["name"]: item for item in CONFIG["metrics"]},
        "dimensions_map": {item["name"]: item for item in CONFIG["dimensions"]},
        "scope": [],
        "config": CONFIG,
        "link": LINK,
    }
    with pytest.raises(ObjectMetricError, match="不存在指标"):
        object_metrics_module._resolve_metric_term("ghost", **base)
    with pytest.raises(ObjectMetricNotExecutableError, match="metric_scope"):
        object_metrics_module._resolve_metric_term("发货金额", **{**base, "scope": ["发货单数量"]})
    empty = {**base, "metrics_map": {"空指标": {"name": "空指标", "table": "shipments"}}}
    with pytest.raises(ObjectMetricNotExecutableError, match="缺少聚合表达式"):
        object_metrics_module._resolve_metric_term("空指标", **empty)


def test_resolve_projection_error_paths(monkeypatch):
    async def no_detail(_name):
        return None

    monkeypatch.setattr(object_metrics_module.ontology_service, "get_object_type_async", no_detail)
    with pytest.raises(ObjectMetricError, match="不存在"):
        asyncio.run(resolve_metric_projection_async("shipment", ["发货单数量"]))
    with pytest.raises(ObjectMetricError, match="至少需要"):
        asyncio.run(resolve_metric_projection_async("shipment", []))

    async def detail_no_binding(_name):
        return {**DETAIL, "source_binding": None}

    monkeypatch.setattr(object_metrics_module.ontology_service, "get_object_type_async", detail_no_binding)
    with pytest.raises(ObjectMetricNotExecutableError, match="未绑定 Dataset"):
        asyncio.run(resolve_metric_projection_async("shipment", ["发货单数量"]))

    async def detail(_name):
        return DETAIL

    async def no_dataset(_id):
        return None

    monkeypatch.setattr(object_metrics_module.ontology_service, "get_object_type_async", detail)
    monkeypatch.setattr(object_metrics_module.logical_dataset_service, "get_dataset_async_by_id_async", no_dataset)
    with pytest.raises(ObjectMetricNotExecutableError, match="Dataset 不存在"):
        asyncio.run(resolve_metric_projection_async("shipment", ["发货单数量"]))

    async def dataset(_id):
        return DATASET

    no_group_dataset = {**DATASET, "bindings": []}
    monkeypatch.setattr(
        object_metrics_module.logical_dataset_service,
        "get_dataset_async_by_id_async",
        lambda _id: _async_value(no_group_dataset),
    )
    with pytest.raises(ObjectMetricNotExecutableError, match="无法确定数据源组"):
        asyncio.run(resolve_metric_projection_async("shipment", ["发货单数量"]))
    monkeypatch.setattr(
        object_metrics_module.logical_dataset_service,
        "get_dataset_async_by_id_async",
        lambda _id: _async_value(DATASET),
    )

    async def no_link(*_args, **_kwargs):
        return None

    monkeypatch.setattr(object_metrics_module.dataset_semantic_link_service, "get_active_link_async", no_link)
    with pytest.raises(ObjectMetricNotExecutableError, match="未关联语义模型"):
        asyncio.run(resolve_metric_projection_async("shipment", ["发货单数量"], datasource_group_id="ds_a"))

    async def draft_link(*_args, **_kwargs):
        return {**LINK, "lifecycle_status": "draft"}

    monkeypatch.setattr(object_metrics_module.dataset_semantic_link_service, "get_active_link_async", draft_link)
    with pytest.raises(ObjectMetricNotExecutableError, match="关联未启用"):
        asyncio.run(resolve_metric_projection_async("shipment", ["发货单数量"], datasource_group_id="ds_a"))

    async def active_link(*_args, **_kwargs):
        return LINK

    async def no_config(*_args, **_kwargs):
        return None

    monkeypatch.setattr(object_metrics_module.dataset_semantic_link_service, "get_active_link_async", active_link)
    monkeypatch.setattr(object_metrics_module.dataset_semantic_link_service, "load_config_for_link_async", no_config)
    with pytest.raises(ObjectMetricNotExecutableError, match="语义模型不存在"):
        asyncio.run(resolve_metric_projection_async("shipment", ["发货单数量"], datasource_group_id="ds_a"))


def test_load_object_metrics_map_degrades(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(object_metrics_module, "list_object_metrics_async", boom)
    result = asyncio.run(object_metrics_module.load_object_metrics_map_async(["shipment"]))
    assert result == {"shipment": []}


async def _async_value(value):
    return value
