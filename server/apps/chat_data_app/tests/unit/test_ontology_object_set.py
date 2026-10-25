"""对象集合（static/temporary）服务单测：主键归一化、过期、分页与缺失对象处理。"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from apps.chat_data_app.services.ontology import object_set_service as service_module
from apps.chat_data_app.services.ontology.errors import (
    OntologyAlreadyExistsError,
    OntologyInvalidError,
    OntologyNotFoundError,
)
from apps.chat_data_app.services.ontology.object_set_service import (
    MAX_OBJECT_SET_KEYS,
    OntologyObjectSetService,
    build_default_object_set_name,
)


class _FakeDao:
    def __init__(self):
        self.items: dict[str, dict] = {}

    async def list_object_sets_async(self, object_type_id=None, *, include_expired=False):
        items = [dict(item) for item in self.items.values() if not item.get("delete_flag")]
        if object_type_id is not None:
            items = [item for item in items if item["object_type_id"] == object_type_id]
        if not include_expired:
            items = [item for item in items if not item.get("expired")]
        return items

    async def get_object_set_async(self, name, *, include_deleted=False):
        item = self.items.get(name)
        if item is None or (item.get("delete_flag") and not include_deleted):
            return None
        return dict(item)

    async def upsert_object_set_async(self, name, payload, *, allow_overwrite, **_kwargs):
        existing = self.items.get(name)
        if existing is not None and not existing.get("delete_flag") and not allow_overwrite:
            return None
        item = {
            "id": len(self.items) + 1,
            "set_name": name,
            "display_name": payload.get("display_name") or name,
            "description": payload.get("description") or "",
            "object_type_id": payload.get("object_type_id"),
            "object_type_name": payload.get("object_type_name"),
            "member_datasource_id": payload.get("member_datasource_id"),
            "definition_kind": "static",
            "object_key_count": len(payload.get("object_keys") or []),
            "object_keys": list(payload.get("object_keys") or []),
            "temporary": bool(payload.get("temporary")),
            "expires_at": payload.get("expires_at").strftime("%Y-%m-%d %H:%M:%S") if payload.get("expires_at") else "",
            "expired": False,
            "delete_flag": 0,
        }
        self.items[name] = item
        return dict(item)

    async def soft_delete_object_set_async(self, name, **_kwargs):
        item = self.items.get(name)
        if item is None or item.get("delete_flag"):
            return False
        item["delete_flag"] = 1
        return True


def _object_type(status: str = "certified", write_mode: str = "read_only") -> dict:
    return {
        "id": 7,
        "object_type_name": "customer",
        "status": status,
        "write_mode": write_mode,
        "properties": [
            {"property_name": "customer_id", "is_identifier": True, "is_title": True, "value_type": "String"},
            {"property_name": "username", "is_identifier": False, "value_type": "String"},
        ],
    }


@pytest.fixture(autouse=True)
def _fake_object_type(monkeypatch):
    async def fake_get_object_type(_name):
        return _object_type()

    monkeypatch.setattr(service_module.ontology_service, "get_object_type_async", fake_get_object_type)


def _service(dao: Any = None) -> OntologyObjectSetService:
    return OntologyObjectSetService(dao=cast(Any, dao or _FakeDao()))


def _payload(**overrides) -> dict:
    payload = {
        "display_name": "杜佑的订单",
        "description": "",
        "member_datasource_id": "ORG_CODE1",
        "object_keys": ["ORD1", "ORD2"],
        "temporary": True,
    }
    payload.update(overrides)
    return payload


def test_save_static_set_normalizes_keys_and_sets_expiry():
    dao = _FakeDao()
    service = _service(dao)
    saved = asyncio.run(
        service.upsert_object_set_async(
            "customer",
            "duyou_customers",
            _payload(object_keys=["ORG_CODE1:customer:ORD1", "ORD2", "ORD1"]),
        )
    )
    assert saved["object_keys"] == ["ORD1", "ORD2"]
    assert saved["object_key_count"] == 2
    assert saved["expires_at"]
    assert dao.items["duyou_customers"]["object_type_id"] == 7


def test_save_permanent_set_has_no_expiry():
    saved = asyncio.run(_service().upsert_object_set_async("customer", "duyou_customers", _payload(temporary=False)))
    assert saved["expires_at"] == ""


def test_save_rejects_key_from_other_member_or_type():
    with pytest.raises(OntologyInvalidError, match="不一致"):
        asyncio.run(
            _service().upsert_object_set_async(
                "customer",
                "duyou_customers",
                _payload(object_keys=["ORG_CODE2:customer:ORD1"]),
            )
        )
    with pytest.raises(OntologyInvalidError, match="不一致"):
        asyncio.run(
            _service().upsert_object_set_async(
                "customer",
                "duyou_customers",
                _payload(object_keys=["ORG_CODE1:order:ORD1"]),
            )
        )


def test_save_rejects_empty_and_oversized_key_list():
    with pytest.raises(OntologyInvalidError, match="至少需要一个"):
        asyncio.run(_service().upsert_object_set_async("customer", "duyou_customers", _payload(object_keys=[])))
    with pytest.raises(OntologyInvalidError, match=f"最多 {MAX_OBJECT_SET_KEYS}"):
        asyncio.run(
            _service().upsert_object_set_async(
                "customer",
                "duyou_customers",
                _payload(object_keys=[f"ORD{index}" for index in range(MAX_OBJECT_SET_KEYS + 1)]),
            )
        )


def test_save_requires_member_datasource_id():
    with pytest.raises(OntologyInvalidError, match="member_datasource_id"):
        asyncio.run(
            _service().upsert_object_set_async("customer", "duyou_customers", _payload(member_datasource_id=""))
        )


def test_save_existing_active_set_requires_overwrite():
    service = _service()
    asyncio.run(service.upsert_object_set_async("customer", "duyou_customers", _payload()))
    with pytest.raises(OntologyAlreadyExistsError):
        asyncio.run(service.upsert_object_set_async("customer", "duyou_customers", _payload()))
    saved = asyncio.run(
        service.upsert_object_set_async(
            "customer", "duyou_customers", _payload(object_keys=["ORD9"]), allow_overwrite=True
        )
    )
    assert saved["object_keys"] == ["ORD9"]


def test_list_object_sets_filters_by_object_type():
    dao = _FakeDao()
    service = _service(dao)
    asyncio.run(service.upsert_object_set_async("customer", "duyou_customers", _payload()))
    dao.items["other"] = {"set_name": "other", "object_type_id": 99, "delete_flag": 0, "expired": False}
    items = asyncio.run(service.list_object_sets_async("customer"))
    assert [item["set_name"] for item in items] == ["duyou_customers"]


def test_expired_set_is_not_found():
    dao = _FakeDao()
    dao.items["expired_set"] = {
        "set_name": "expired_set",
        "object_type_id": 7,
        "object_type_name": "customer",
        "member_datasource_id": "ORG_CODE1",
        "object_keys": ["ORD1"],
        "expired": True,
        "delete_flag": 0,
    }
    with pytest.raises(OntologyNotFoundError, match="过期"):
        asyncio.run(_service(dao).get_object_set_async("customer", "expired_set"))


def test_load_pages_keys_and_skips_missing_objects(monkeypatch):
    dao = _FakeDao()
    service = _service(dao)
    keys = ["ORD1", "ORD2", "ORD3"]
    asyncio.run(service.upsert_object_set_async("customer", "duyou_customers", _payload(object_keys=keys)))

    captured: dict = {}

    async def fake_load(detail, *, member_datasource_id, local_keys, **_kwargs):
        captured["member"] = member_datasource_id
        captured["page_keys"] = list(local_keys)
        return [{"object_key": "ORG_CODE1:customer:ORD1", "properties": {"customer_id": "ORD1"}}]

    monkeypatch.setattr(service_module, "load_objects_by_keys", fake_load)
    result = asyncio.run(service.load_object_set_objects_async("customer", "duyou_customers", limit=2))
    assert captured == {"member": "ORG_CODE1", "page_keys": ["ORD1", "ORD2"]}
    assert result["total"] == 3
    assert result["returned"] == 1
    assert result["missing_count"] == 1
    assert result["has_more"] is True


def test_load_rejects_set_of_other_object_type():
    dao = _FakeDao()
    dao.items["duyou_customers"] = {
        "set_name": "duyou_customers",
        "object_type_id": 99,
        "object_type_name": "order",
        "member_datasource_id": "ORG_CODE1",
        "object_keys": ["ORD1"],
        "expired": False,
        "delete_flag": 0,
    }
    with pytest.raises(OntologyNotFoundError):
        asyncio.run(_service(dao).get_object_set_async("customer", "duyou_customers"))


def test_delete_missing_set_raises():
    with pytest.raises(OntologyNotFoundError):
        asyncio.run(_service().delete_object_set_async("customer", "missing"))


def test_non_queryable_status_rejected():
    class _DraftDao(_FakeDao):
        pass

    async def fake_get_object_type(_name):
        return _object_type(status="draft")

    original = service_module.ontology_service.get_object_type_async
    service_module.ontology_service.get_object_type_async = fake_get_object_type
    try:
        with pytest.raises(OntologyInvalidError, match="certified/experimental"):
            asyncio.run(_service(_DraftDao()).list_object_sets_async("customer"))
    finally:
        service_module.ontology_service.get_object_type_async = original


def test_default_set_name_is_valid_api_name():
    name = build_default_object_set_name("order_item")
    assert name.startswith("oset_order_item_")
    assert len(name) <= 128
    assert all(char.isalnum() or char == "_" for char in name)
