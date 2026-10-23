"""Dataset 投影上下文：member binding、执行对象和列映射分支。"""

from __future__ import annotations

import asyncio

import pytest

from apps.chat_data_app.services.dataset import dataset_service as dataset_module
from apps.chat_data_app.services.ontology import object_projection


def _detail():
    return {
        "id": 1,
        "object_type_name": "Order",
        "properties": [{"property_name": "order_id", "is_identifier": True}],
        "source_binding": {
            "dataset_id": 1,
            "column_mappings": {"order_id": "order_id", "amount": "amount"},
        },
    }


def _dataset():
    return {
        "id": 1,
        "bindings": [
            {"datasource_group_id": "g1", "member_datasource_id": "ORG1", "physical_table": "orders"},
        ],
        "columns": [
            {"name": "order_id", "physical_column": "order_id"},
            {"name": "amount", "physical_column": "amount"},
        ],
    }


class _FakeManager:
    def __init__(self, targets):
        self._targets = targets

    async def list_authorized_execution_targets_async(self, *_args, **_kwargs):
        return self._targets


def _patch(monkeypatch, dataset, targets=None):
    async def get_dataset(_dataset_id):
        return dataset

    fake_service = type("FakeDatasetService", (), {"get_dataset_async_by_id_async": staticmethod(get_dataset)})
    monkeypatch.setattr(dataset_module, "logical_dataset_service", fake_service)
    monkeypatch.setattr(
        object_projection,
        "get_datasource_manager",
        lambda: _FakeManager(targets if targets is not None else [{"member_datasource_id": "ORG1"}]),
    )


def test_dataset_context_selects_member_and_builds_columns(monkeypatch):
    _patch(monkeypatch, _dataset())
    config, detail, targets, group_id = asyncio.run(
        object_projection._dataset_mapping_context(_detail(), ["ORG1"], user_id="u", username="n")
    )
    assert group_id == "g1"
    assert targets[0]["member_datasource_id"] == "ORG1"
    assert [item["column"] for item in config["dimensions"]] == ["order_id", "amount"]
    assert detail["mappings"][0]["column_mappings"] == {"order_id": "order_id", "amount": "amount"}


@pytest.mark.parametrize(
    ("detail", "dataset", "requested", "message"),
    [
        ({"id": 1}, _dataset(), None, "source binding"),
        (_detail(), None, None, "Dataset 不存在"),
        (_detail(), {"bindings": [], "columns": []}, None, "member binding"),
        (
            _detail(),
            {
                "bindings": [
                    {"datasource_group_id": "g1", "member_datasource_id": "A", "physical_table": "t"},
                    {"datasource_group_id": "g1", "member_datasource_id": "B", "physical_table": "t"},
                ],
                "columns": [{"name": "order_id", "physical_column": "order_id"}],
            },
            None,
            "多个 member",
        ),
    ],
)
def test_dataset_context_errors(monkeypatch, detail, dataset, requested, message):
    _patch(monkeypatch, dataset)
    with pytest.raises(object_projection.ObjectProjectionError, match=message):
        asyncio.run(object_projection._dataset_mapping_context(detail, requested, user_id="u", username="n"))


def test_dataset_context_errors_for_binding_and_target(monkeypatch):
    bad_binding = _dataset()
    bad_binding["bindings"][0]["datasource_group_id"] = ""
    _patch(monkeypatch, bad_binding)
    with pytest.raises(object_projection.ObjectProjectionError, match="datasource_group_id"):
        asyncio.run(object_projection._dataset_mapping_context(_detail(), None, user_id="u", username="n"))

    _patch(monkeypatch, _dataset(), targets=[{"member_datasource_id": "OTHER"}])
    with pytest.raises(object_projection.ObjectProjectionError, match="无权访问"):
        asyncio.run(object_projection._dataset_mapping_context(_detail(), None, user_id="u", username="n"))


def test_dataset_context_rejects_empty_dimensions(monkeypatch):
    dataset = _dataset()
    dataset["columns"] = [{"name": "other", "physical_column": "other"}]
    _patch(monkeypatch, dataset)
    with pytest.raises(object_projection.ObjectProjectionError, match="没有有效列映射"):
        asyncio.run(object_projection._dataset_mapping_context(_detail(), None, user_id="u", username="n"))


def test_select_dataset_binding_filters_requested_member():
    dataset = {
        "bindings": [
            {"member_datasource_id": "A"},
            {"member_datasource_id": "B"},
        ]
    }
    assert object_projection._select_dataset_binding(dataset, ["B"])["member_datasource_id"] == "B"


def test_dataset_dimensions_skips_unknown_and_empty_physical(monkeypatch):
    dataset = {
        "columns": [
            {"name": "order_id", "physical_column": "order_id"},
            {"name": "empty", "physical_column": ""},
        ]
    }
    binding = {"column_mappings": {"order_id": "order_id", "missing": "missing", "empty": "empty"}}
    dims, mappings = object_projection._dataset_dimensions(dataset, binding, "orders")
    assert [item["name"] for item in dims] == ["order_id", "empty"]
    assert mappings == {"order_id": "order_id", "empty": "empty"}


def test_projection_context_rejects_missing_binding():
    with pytest.raises(object_projection.ObjectProjectionError, match="Dataset 数据源绑定"):
        asyncio.run(
            object_projection._projection_context(
                {"id": 1},
                semantic_config_id=None,
                member_datasource_ids=None,
                user_id="u",
                username="n",
            )
        )


def test_relation_side_handles_both_directions_and_skips_self_or_multi_column():
    base = {
        "from_object_type_id": 1,
        "to_object_type_id": 2,
        "from_object_type_name": "Order",
        "to_object_type_name": "Customer",
        "from_columns": ["customer_id"],
        "to_columns": ["customer_id"],
    }
    assert object_projection._relation_side(base, 1)["direction"] == "from"
    assert object_projection._relation_side(base, 2)["direction"] == "to"
    assert object_projection._relation_side({**base, "to_object_type_id": 1}, 1) is None
    assert object_projection._relation_side({**base, "from_columns": ["a", "b"]}, 1) is None


def test_read_links_builds_and_skips(monkeypatch):
    detail = {"id": 1}
    relations = [
        {
            "relation_type_name": "order_customer",
            "status": "certified",
            "from_object_type_id": 1,
            "to_object_type_id": 2,
            "from_object_type_name": "Order",
            "to_object_type_name": "Customer",
            "from_columns": ["customer_id"],
            "to_columns": ["customer_id"],
        },
        {
            "relation_type_name": "skipped",
            "status": "deprecated",
            "from_object_type_id": 1,
            "to_object_type_id": 2,
            "from_columns": ["customer_id"],
            "to_columns": ["customer_id"],
        },
    ]

    async def fake_relations():
        return relations

    async def fake_search(*_args, **_kwargs):
        return {"objects": [{"object_key": "m1:Customer:C1", "properties": {"name": "张三"}}], "title_property": "name"}

    monkeypatch.setattr(object_projection.ontology_service, "list_relations_async", fake_relations)
    monkeypatch.setattr(object_projection, "search_object_projection", fake_search)
    links = asyncio.run(
        object_projection._read_links(detail, {"customer_id": "C1"}, member_id="m1", user_id="u", username="n")
    )
    assert links["order_customer"][0]["related_object_key"] == "m1:Customer:C1"
    assert "skipped" not in links


def test_read_object_projection_uses_dataset_scope(monkeypatch):
    detail = _detail()
    query = {"sql": "SELECT 1", "object_type": "Order", "object_key": "A001", "properties": ["order_id"]}

    async def fake_get(_name):
        return detail

    async def fake_context(*_args, **_kwargs):
        return detail, {"dimensions": []}, [{"member_datasource_id": "m1"}], "g1"

    async def fake_rows(*_args, **_kwargs):
        return [{"order_id": "A001"}]

    async def fake_links(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get)
    monkeypatch.setattr(object_projection, "_projection_context", fake_context)
    monkeypatch.setattr(object_projection, "build_object_query", lambda *_args, **_kwargs: query)
    monkeypatch.setattr(object_projection, "_query_rows", fake_rows)

    async def fake_edits(_detail, _key, row):
        return row

    monkeypatch.setattr(object_projection, "_apply_object_edits", fake_edits)
    monkeypatch.setattr(object_projection, "_read_links", fake_links)
    result = asyncio.run(object_projection.read_object_projection("Order", "m1:Order:A001", user_id="u", username="n"))
    assert result["object_key"] == "m1:Order:A001"
    assert result["member_datasource_id"] == "m1"


def test_read_object_projection_rejects_missing_row(monkeypatch):
    async def fake_get(_name):
        return _detail()

    async def fake_context(*_args, **_kwargs):
        return _detail(), {"dimensions": []}, [{"member_datasource_id": "m1"}], "g1"

    async def fake_rows(*_args, **_kwargs):
        return []

    monkeypatch.setattr(object_projection.ontology_service, "get_object_type_async", fake_get)
    monkeypatch.setattr(object_projection, "_projection_context", fake_context)
    monkeypatch.setattr(object_projection, "build_object_query", lambda *_args, **_kwargs: {"sql": "SELECT 1"})
    monkeypatch.setattr(object_projection, "_query_rows", fake_rows)
    with pytest.raises(object_projection.ObjectProjectionError, match="对象不存在"):
        asyncio.run(object_projection.read_object_projection("Order", "m1:Order:A001"))


def test_parse_object_key_branches():
    assert object_projection._parse_object_key("m1:Order:A001", "Order") == ("m1", "A001")
    assert object_projection._parse_object_key("Order:A001", "Order") == ("", "A001")
    assert object_projection._parse_object_key("A001", "Order") == ("", "A001")


def test_relation_link_entries_skips_and_handles_errors(monkeypatch):
    base = {
        "relation_type_name": "rel",
        "status": "certified",
        "from_object_type_id": 1,
        "to_object_type_id": 2,
        "from_object_type_name": "Order",
        "to_object_type_name": "Customer",
        "from_columns": ["customer_id"],
        "to_columns": ["customer_id"],
    }
    assert (
        asyncio.run(
            object_projection._relation_link_entries(
                {**base, "status": "deprecated"},
                {"customer_id": "C1"},
                current_id=1,
                member_id="m1",
                user_id="",
                username="",
            )
        )
        is None
    )
    assert (
        asyncio.run(
            object_projection._relation_link_entries(base, {}, current_id=1, member_id="m1", user_id="", username="")
        )
        is None
    )

    async def fail(*_args, **_kwargs):
        raise object_projection.ObjectProjectionError("boom")

    monkeypatch.setattr(object_projection, "search_object_projection", fail)
    assert (
        asyncio.run(
            object_projection._relation_link_entries(
                base, {"customer_id": "C1"}, current_id=1, member_id="m1", user_id="", username=""
            )
        )
        is None
    )

    async def result(*_args, **_kwargs):
        return {
            "objects": [
                {"object_key": "", "properties": {}},
                {"object_key": "m1:Customer:C1", "properties": {"name": "张三"}},
            ],
            "title_property": "name",
        }

    monkeypatch.setattr(object_projection, "search_object_projection", result)
    entries = asyncio.run(
        object_projection._relation_link_entries(
            base, {"customer_id": "C1"}, current_id=1, member_id="m1", user_id="", username=""
        )
    )
    assert entries == [
        {
            "relation": "rel",
            "direction": "from",
            "related_object_type": "Customer",
            "related_object_key": "m1:Customer:C1",
            "related_title": "张三",
        }
    ]


def test_dataset_service_helpers_cover_short_branches(monkeypatch):
    from apps.chat_data_app.services.dataset.dataset_service import LogicalDatasetService

    service = LogicalDatasetService()
    assert service._column_item("bad") is None
    assert service._column_item({"name": ""}) is None
    with pytest.raises(dataset_module.DatasetInvalidError):
        service._select_group_tables({"ds_tables": []}, None)
    payload = service._table_dataset_payload(
        {"table_name": "orders", "columns": ["bad", {"name": ""}], "primary_keys": ["id"]}, "g1", ["m1"]
    )
    assert payload["columns"] == []
    assert payload["primary_key"] == ["id"]


def test_pipeline_transform_validation_branches():
    from apps.chat_data_app.services.dataset import pipeline_transforms

    with pytest.raises(pipeline_transforms.TransformError, match="必须是对象"):
        pipeline_transforms.normalize_transform_config("bad")
    assert pipeline_transforms._numeric_values([True, None, "3", "bad"]) == [3.0]
