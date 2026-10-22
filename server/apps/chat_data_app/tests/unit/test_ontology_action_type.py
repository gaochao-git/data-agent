"""Action Type 定义服务单测：参数/编辑校验、同名覆盖、试运行。"""

from __future__ import annotations

import asyncio

import pytest

from apps.chat_data_app.services.ontology import action_type_service as service_module
from apps.chat_data_app.services.ontology.action_type_service import OntologyActionTypeService
from apps.chat_data_app.services.ontology.errors import (
    OntologyAlreadyExistsError,
    OntologyInvalidError,
    OntologyNotFoundError,
)


class _FakeDao:
    def __init__(self):
        self.items: dict[str, dict] = {}

    async def list_action_types_async(self, object_type_id=None):
        return [
            dict(item)
            for item in self.items.values()
            if object_type_id is None or item["object_type_id"] == object_type_id
        ]

    async def get_action_type_async(self, name, *, include_deleted=False):
        item = self.items.get(name)
        return dict(item) if item else None

    async def upsert_action_type_async(self, name, payload, *, allow_overwrite, **_kwargs):
        if name in self.items and not allow_overwrite:
            return None
        row = {"id": len(self.items) + 1, "action_type_name": name, "status": "draft", **payload}
        self.items[name] = row
        return dict(row)

    async def soft_delete_action_type_async(self, name, **_kwargs):
        return self.items.pop(name, None) is not None


def _object_type():
    return {
        "id": 2,
        "object_type_name": "sales_order",
        "status": "certified",
        "properties": [
            {"property_name": "order_no", "is_identifier": True},
            {"property_name": "pay_status", "is_identifier": False, "value_type": "String"},
        ],
    }


def _payload():
    return {
        "object_type_name": "sales_order",
        "display_name": "更新订单状态",
        "parameters": [{"name": "status", "type": "String", "required": True}],
        "edits": [
            {
                "operation": "set_property",
                "property": "pay_status",
                "value": {"kind": "parameter", "parameter": "status"},
            }
        ],
        "status": "draft",
    }


@pytest.fixture(autouse=True)
def _fake_object_type(monkeypatch):
    async def fake_get_object_type(_name):
        return _object_type()

    async def fake_attach(item):
        return {**item, "object_type_name": "sales_order"}

    monkeypatch.setattr(service_module.ontology_service, "get_object_type_async", fake_get_object_type)
    monkeypatch.setattr(service_module, "_attach_object_type_name", fake_attach)


def _service():
    return OntologyActionTypeService(_FakeDao())


def test_upsert_and_get_action_type():
    service = _service()
    result = asyncio.run(service.upsert_action_type_async("update_order_status", _payload()))
    assert result["object_type_name"] == "sales_order"
    assert result["parameters"][0]["name"] == "status"
    assert asyncio.run(service.get_action_type_async("update_order_status"))["edits"][0]["property"] == "pay_status"


def test_upsert_duplicate_requires_overwrite():
    service = _service()
    asyncio.run(service.upsert_action_type_async("update_order_status", _payload()))
    with pytest.raises(OntologyAlreadyExistsError):
        asyncio.run(service.upsert_action_type_async("update_order_status", _payload()))


def test_identifier_property_cannot_be_edited():
    payload = _payload()
    payload["edits"][0]["property"] = "order_no"
    with pytest.raises(OntologyInvalidError, match="标识属性"):
        asyncio.run(_service().upsert_action_type_async("update_order_status", payload))


def test_edit_must_reference_known_parameter():
    payload = _payload()
    payload["edits"][0]["value"]["parameter"] = "missing"
    with pytest.raises(OntologyInvalidError, match="不存在的参数"):
        asyncio.run(_service().upsert_action_type_async("update_order_status", payload))


def test_test_run_resolves_parameter_and_static_values():
    service = _service()
    asyncio.run(service.upsert_action_type_async("update_order_status", _payload()))
    result = asyncio.run(service.test_run_action_type_async("update_order_status", {"status": "paid"}))
    assert result["executed"] is False
    assert result["edits"][0]["resolved_value"] == "paid"


def test_test_run_rejects_unknown_and_missing_parameters():
    service = _service()
    asyncio.run(service.upsert_action_type_async("update_order_status", _payload()))
    with pytest.raises(OntologyInvalidError, match="未知参数"):
        asyncio.run(service.test_run_action_type_async("update_order_status", {"status": "paid", "x": 1}))
    with pytest.raises(OntologyInvalidError, match="缺少必填参数"):
        asyncio.run(service.test_run_action_type_async("update_order_status", {}))


def test_delete_missing_action_type_raises():
    with pytest.raises(OntologyNotFoundError):
        asyncio.run(_service().delete_action_type_async("missing"))


def test_test_run_validates_parameter_type():
    service = _service()
    asyncio.run(service.upsert_action_type_async("update_order_status", _payload()))
    with pytest.raises(OntologyInvalidError, match="类型应为 String"):
        asyncio.run(service.test_run_action_type_async("update_order_status", {"status": 1}))


# ---------- 执行：写覆盖层 + 审计 ----------


class _FakeExecutionDao:
    def __init__(self):
        self.execution = None
        self.edits = []

    async def create_execution_async(self, **kwargs):
        self.execution = {"id": 99, **kwargs}
        return self.execution

    async def append_edit_async(self, **kwargs):
        self.edits.append(kwargs)


def test_execute_action_writes_overlay_and_execution(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_transaction():
        yield None

    async def fake_read_object(*_args, **_kwargs):
        return {"object_key": "sales_order:ORD2024000001", "properties": {"pay_status": "unpaid"}}

    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    monkeypatch.setattr(service_module, "transaction_scope", fake_transaction)
    monkeypatch.setattr(service_module, "read_object_projection", fake_read_object)

    fake_execution = _FakeExecutionDao()
    service = OntologyActionTypeService(_FakeDao(), fake_execution)
    asyncio.run(service.upsert_action_type_async("update_order_status", {**_payload(), "status": "certified"}))
    result = asyncio.run(
        service.execute_action_type_async(
            "update_order_status",
            object_key="ORD2024000001",
            parameters={"status": "paid"},
            operator="u1",
            operator_name="测试用户",
        )
    )
    assert result["execution"]["id"] == 99
    assert fake_execution.edits[0]["property_name"] == "pay_status"
    assert fake_execution.edits[0]["value"] == "paid"


def test_execute_action_rejects_draft_status(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    service = _service()
    asyncio.run(service.upsert_action_type_async("update_order_status", _payload()))
    with pytest.raises(OntologyInvalidError, match="experimental/certified"):
        asyncio.run(
            service.execute_action_type_async(
                "update_order_status", object_key="ORD2024000001", parameters={"status": "paid"}
            )
        )


def test_upsert_rejects_invalid_status_and_object_type(monkeypatch):
    service = _service()
    payload = _payload()
    payload["status"] = "deprecated"
    with pytest.raises(OntologyInvalidError, match="状态不支持"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))
    payload = _payload()
    payload["object_type_name"] = ""
    with pytest.raises(OntologyInvalidError, match="目标对象类型"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))


def test_upsert_validates_parameters_and_edit_operation():
    service = _service()
    payload = _payload()
    payload["parameters"] = [{"name": "status", "type": "String"}, {"name": "status", "type": "String"}]
    with pytest.raises(OntologyInvalidError, match="参数名重复"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))
    payload = _payload()
    payload["parameters"] = [{"name": "status", "type": "Unknown"}]
    with pytest.raises(OntologyInvalidError, match="参数类型不支持"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))
    payload = _payload()
    payload["edits"][0]["operation"] = "delete_object"
    with pytest.raises(OntologyInvalidError, match="只支持 set_property"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))


def test_upsert_validates_static_value_and_duplicate_property():
    service = _service()
    payload = _payload()
    payload["edits"][0]["value"] = {"kind": "static"}
    with pytest.raises(OntologyInvalidError, match="缺少 value"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))
    payload = _payload()
    payload["edits"].append(dict(payload["edits"][0]))
    with pytest.raises(OntologyInvalidError, match="重复编辑属性"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))


def test_list_and_delete_action_type():
    service = _service()
    asyncio.run(service.upsert_action_type_async("update_order_status", _payload()))
    listed = asyncio.run(service.list_action_types_async("sales_order"))
    assert listed[0]["action_type_name"] == "update_order_status"
    asyncio.run(service.delete_action_type_async("update_order_status"))
    assert asyncio.run(service.list_action_types_async()) == []


def test_upsert_rejects_empty_or_oversized_parameters():
    service = _service()
    payload = _payload()
    payload["parameters"] = []
    with pytest.raises(OntologyInvalidError, match="至少需要一个参数"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))
    payload = _payload()
    payload["parameters"] = [{"name": f"p{i}", "type": "String"} for i in range(21)]
    with pytest.raises(OntologyInvalidError, match="不能超过"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))


def test_upsert_rejects_invalid_parameter_name_and_description():
    service = _service()
    payload = _payload()
    payload["parameters"][0]["name"] = "bad-name"
    with pytest.raises(OntologyInvalidError, match="参数名"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))
    payload = _payload()
    payload["parameters"][0]["description"] = "x" * 513
    with pytest.raises(OntologyInvalidError, match="参数描述"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))


def test_upsert_rejects_missing_edit_property():
    service = _service()
    payload = _payload()
    payload["edits"][0]["property"] = "missing"
    with pytest.raises(OntologyInvalidError, match="目标属性不存在"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))


def test_upsert_rejects_deprecated_target(monkeypatch):
    async def deprecated_object(_name):
        return {**_object_type(), "status": "deprecated"}

    monkeypatch.setattr(service_module.ontology_service, "get_object_type_async", deprecated_object)
    with pytest.raises(OntologyInvalidError, match="deprecated"):
        asyncio.run(_service().upsert_action_type_async("update_order_status", _payload()))
