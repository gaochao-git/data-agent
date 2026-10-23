"""对象类型 source binding：Dataset 校验、详情补全和错误分支。"""

from __future__ import annotations

import asyncio
import importlib

import pytest

from apps.chat_data_app.services.ontology.ontology_service import OntologyInvalidError, OntologyService

service_module = importlib.import_module("apps.chat_data_app.services.ontology.ontology_service")
from apps.chat_data_app.services.ontology.errors import OntologyNotFoundError


def _detail():
    return {
        "id": 7,
        "properties": [{"property_name": "order_id"}, {"property_name": "amount"}],
        "source_binding": None,
    }


def _dataset():
    return {
        "id": 3,
        "dataset_name": "orders",
        "columns": [{"name": "order_id"}, {"name": "amount"}],
        "bindings": [{"member_datasource_id": "ORG1"}, {"member_datasource_id": "ORG2"}],
    }


class _FakeDao:
    async def upsert_source_binding_async(self, object_type_id, **kwargs):
        return {"object_type_id": object_type_id, **kwargs}


def _patch(monkeypatch, dataset=None):
    dataset = dataset if dataset is not None else _dataset()

    async def get_detail(_name):
        return _detail()

    async def get_dataset(_dataset_id):
        return dataset

    fake_dataset_service = type("FakeDatasetService", (), {"get_dataset_async_by_id_async": staticmethod(get_dataset)})
    monkeypatch.setattr(service_module, "logical_dataset_service", fake_dataset_service)
    service = OntologyService()
    monkeypatch.setattr(service, "get_object_type_async", get_detail)
    monkeypatch.setattr(service, "_dao", _FakeDao())
    return service


def test_set_source_binding_success(monkeypatch):
    service = _patch(monkeypatch)
    result = asyncio.run(
        service.set_source_binding_async(
            "Order",
            {"dataset_id": 3, "column_mappings": {"order_id": "order_id"}},
            operator="u",
            operator_name="n",
        )
    )
    assert result["dataset_id"] == 3
    assert result["column_mappings"] == {"order_id": "order_id"}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"dataset_id": 0}, "dataset_id"),
        ({"dataset_id": 3, "column_mappings": ["bad"]}, "column_mappings"),
        ({"dataset_id": 3, "column_mappings": {"ghost": "order_id"}}, "对象属性不存在"),
        ({"dataset_id": 3, "column_mappings": {"order_id": "ghost"}}, "Dataset 列不存在"),
    ],
)
def test_set_source_binding_validation(monkeypatch, payload, message):
    service = _patch(monkeypatch)
    with pytest.raises(OntologyInvalidError, match=message):
        asyncio.run(service.set_source_binding_async("Order", payload))


def test_set_source_binding_rejects_missing_object_or_dataset(monkeypatch):
    service = _patch(monkeypatch)
    monkeypatch.setattr(service, "get_object_type_async", lambda _name: _none())
    with pytest.raises(OntologyNotFoundError):
        asyncio.run(service.set_source_binding_async("Order", {"dataset_id": 3}))
    service = _patch(monkeypatch)
    monkeypatch.setattr(
        service_module,
        "logical_dataset_service",
        type("F", (), {"get_dataset_async_by_id_async": staticmethod(_none)}),
    )
    with pytest.raises(OntologyInvalidError, match="Dataset 不存在"):
        asyncio.run(service.set_source_binding_async("Order", {"dataset_id": 3}))


async def _none(*_args, **_kwargs):
    return None


def test_get_object_type_enriches_binding(monkeypatch):
    class Dao:
        async def get_object_type_row_async(self, _name):
            return {"id": 7, "object_type_name": "Order"}

        async def list_properties_async(self, _id):
            return []

        async def get_source_binding_async(self, _id):
            return {"dataset_id": 3, "column_mappings": {"order_id": "order_id"}}

    async def get_dataset(_id):
        return _dataset()

    fake_service = type("F", (), {"get_dataset_async_by_id_async": staticmethod(get_dataset)})
    monkeypatch.setattr(service_module, "logical_dataset_service", fake_service)
    service = OntologyService()
    monkeypatch.setattr(service, "_dao", Dao())
    detail = asyncio.run(service.get_object_type_async("Order"))
    assert detail["source_binding"]["dataset_name"] == "orders"
    assert detail["source_binding"]["binding_count"] == 2
    assert detail["source_binding"]["member_datasource_ids"] == ["ORG1", "ORG2"]
