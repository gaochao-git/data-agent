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
        existing = self.items.get(name)
        version = 1 if existing is None else int(existing.get("config_version") or 1) + 1
        row = {
            "id": len(self.items) + 1,
            "action_type_name": name,
            "status": "draft",
            **payload,
            "config_version": version,
        }
        self.items[name] = row
        return dict(row)

    async def soft_delete_action_type_async(self, name, **_kwargs):
        return self.items.pop(name, None) is not None


def _object_type():
    return {
        "id": 2,
        "object_type_name": "sales_order",
        "status": "certified",
        "write_mode": "action_writable",
        "properties": [
            {"property_name": "order_no", "is_identifier": True, "value_type": "String"},
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


class _FakeInstanceDao:
    """对象实例可见性与代际：默认 active/generation=1，支持删除与重建。"""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def _row(self, object_key):
        return self.rows.setdefault(object_key, {"generation": 1, "lifecycle_status": "active", "last_execution_id": 0})

    async def get_instance_async(self, object_type_id, object_key):
        row = self.rows.get(object_key)
        return dict(row) if row else None

    async def ensure_active_async(self, *, object_type_id, object_key, execution_id=0, operator="", operator_name=""):
        row = self._row(object_key)
        row["last_execution_id"] = int(execution_id or 0)
        return dict(row)

    async def mark_deleted_async(self, *, object_type_id, object_key, execution_id=0, operator="", operator_name=""):
        row = self._row(object_key)
        row["lifecycle_status"] = "deleted"
        row["last_execution_id"] = int(execution_id or 0)
        return dict(row)

    async def restore_async(self, *, object_type_id, object_key, execution_id=0, operator="", operator_name=""):
        row = self._row(object_key)
        row["lifecycle_status"] = "active"
        row["last_execution_id"] = int(execution_id or 0)
        return dict(row)

    async def revive_async(self, *, object_type_id, object_key, execution_id=0, operator="", operator_name=""):
        row = self._row(object_key)
        row["generation"] = int(row.get("generation") or 1) + 1
        row["lifecycle_status"] = "active"
        row["last_execution_id"] = int(execution_id or 0)
        return dict(row)


def _service(*, execution_dao=None, action_object_dao=None, instance_dao=None):
    return OntologyActionTypeService(
        _FakeDao(),
        execution_dao if execution_dao is not None else _FakeExecutionDao(),
        action_object_dao,
        instance_dao if instance_dao is not None else _FakeInstanceDao(),
    )


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
        self.by_key: dict[str, dict] = {}

    async def create_execution_async(self, **kwargs):
        self.execution = {"id": 99, **kwargs}
        key = kwargs.get("idempotency_key") or ""
        if key:
            self.by_key[key] = self.execution
        return self.execution

    async def find_execution_by_idempotency_async(self, action_type_name, idempotency_key):
        return self.by_key.get(idempotency_key)

    async def append_edit_async(self, **kwargs):
        self.edits.append(kwargs)

    async def list_executions_async(self, *, action_type_name="", object_key="", limit=50):
        self.list_args = {"action_type_name": action_type_name, "object_key": object_key, "limit": limit}
        return [{"id": 1, "action_type_name": action_type_name, "object_key": object_key or "ORD2024000001"}]


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
    service = OntologyActionTypeService(_FakeDao(), fake_execution, None, _FakeInstanceDao())
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


def test_list_executions_returns_audit_rows():
    fake_execution = _FakeExecutionDao()
    service = OntologyActionTypeService(_FakeDao(), fake_execution, None, _FakeInstanceDao())
    asyncio.run(service.upsert_action_type_async("update_order_status", _payload()))
    rows = asyncio.run(service.list_executions_async("update_order_status", object_key="ORD2024000001", limit=20))
    assert rows[0]["id"] == 1
    assert fake_execution.list_args == {
        "action_type_name": "update_order_status",
        "object_key": "ORD2024000001",
        "limit": 20,
    }


def test_list_executions_rejects_missing_action_type():
    service = OntologyActionTypeService(_FakeDao(), _FakeExecutionDao(), None, _FakeInstanceDao())
    with pytest.raises(OntologyNotFoundError, match="Action Type 不存在"):
        asyncio.run(service.list_executions_async("missing"))


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
    payload["edits"][0]["operation"] = "delete_link"
    with pytest.raises(OntologyInvalidError, match="仅支持 set_property/create_object/delete_object"):
        asyncio.run(service.upsert_action_type_async("update_order_status", payload))


def test_upsert_accepts_delete_object_for_writable_type():
    service = _service()
    payload = _payload()
    payload["edits"] = [{"operation": "delete_object"}]
    result = asyncio.run(service.upsert_action_type_async("delete_order", payload))
    assert result["edits"] == [{"operation": "delete_object"}]


def test_upsert_rejects_delete_object_for_read_only_type(monkeypatch):
    async def read_only_object(_name):
        return {**_object_type(), "write_mode": "read_only"}

    monkeypatch.setattr(service_module.ontology_service, "get_object_type_async", read_only_object)
    payload = _payload()
    payload["edits"] = [{"operation": "delete_object"}]
    with pytest.raises(OntologyInvalidError, match="read_only 对象类型禁止删除"):
        asyncio.run(_service().upsert_action_type_async("delete_order", payload))


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


# ---------- 写性门禁与 create_object（对齐 Palantir Edits opt-in） ----------


class _GateActionObjectDao:
    def __init__(self, existing_keys: set[str] | None = None):
        self.existing_keys = existing_keys or set()
        self.created: list[dict] = []

    async def create_object_async(self, **kwargs):
        key = kwargs["object_key"]
        if key in self.existing_keys:
            return None
        self.existing_keys.add(key)
        row = {"id": len(self.created) + 1, **kwargs}
        self.created.append(row)
        return row

    async def get_object_async(self, object_type_id, object_key):
        for row in self.created:
            if row["object_key"] == object_key:
                return row
        return None


class _GateExecutionDao:
    def __init__(self):
        self.executions: list[dict] = []

    async def create_execution_async(self, **kwargs):
        row = {"id": len(self.executions) + 1, **kwargs}
        self.executions.append(row)
        return row

    async def append_edit_async(self, **_kwargs):
        return None

    async def list_executions_async(self, **_kwargs):
        return []


def _execution_dao_of(service: OntologyActionTypeService):
    return service._execution_dao


def test_write_mode_gate_rejects_set_property_on_read_only(monkeypatch):
    async def read_only_object(_name):
        return {**_object_type(), "write_mode": "read_only"}

    monkeypatch.setattr(service_module.ontology_service, "get_object_type_async", read_only_object)
    with pytest.raises(OntologyInvalidError, match="set_property 仅支持写性为 action_writable"):
        asyncio.run(_service().upsert_action_type_async("update_order_status", _payload()))


def test_write_mode_gate_rejects_create_object_on_writable_type(monkeypatch):
    async def writable_object(_name):
        return _object_type()

    monkeypatch.setattr(service_module.ontology_service, "get_object_type_async", writable_object)
    payload = _payload()
    payload["edits"] = [
        {
            "operation": "create_object",
            "properties": [{"property": "order_no", "value": {"kind": "static", "value": "A1"}}],
        }
    ]
    with pytest.raises(OntologyInvalidError, match="create_object 仅支持写性为 action_created"):
        asyncio.run(_service().upsert_action_type_async("create_order", payload))


def test_create_object_upsert_requires_identifier_value(monkeypatch):
    async def action_object_type(_name):
        return {
            **_object_type(),
            "object_type_name": "supervision_order",
            "write_mode": "action_created",
        }

    monkeypatch.setattr(service_module.ontology_service, "get_object_type_async", action_object_type)
    payload = _payload()
    payload["object_type_name"] = "supervision_order"
    payload["edits"] = [
        {
            "operation": "create_object",
            "properties": [{"property": "pay_status", "value": {"kind": "static", "value": "x"}}],
        }
    ]
    with pytest.raises(OntologyInvalidError, match="必须提供标识属性值"):
        asyncio.run(_service().upsert_action_type_async("create_supervision", payload))


def test_execute_create_object_writes_action_object(monkeypatch):
    async def action_object_type(_name):
        return {
            **_object_type(),
            "object_type_name": "supervision_order",
            "write_mode": "action_created",
        }

    monkeypatch.setattr(service_module.ontology_service, "get_object_type_async", action_object_type)
    monkeypatch.setattr(service_module, "ontology_action_execute_enabled", lambda: True)

    class _NullTransaction:
        def __init__(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(service_module, "transaction_scope", _NullTransaction)
    action_dao = _GateActionObjectDao()
    execution_dao = _GateExecutionDao()
    service = OntologyActionTypeService(
        dao=_service()._dao,
        execution_dao=execution_dao,
        action_object_dao=action_dao,
        instance_dao=_FakeInstanceDao(),
    )
    payload = _payload()
    payload["object_type_name"] = "supervision_order"
    payload["edits"] = [
        {
            "operation": "create_object",
            "properties": [
                {"property": "order_no", "value": {"kind": "parameter", "parameter": "status"}},
                {"property": "pay_status", "value": {"kind": "static", "value": "pending"}},
            ],
        }
    ]
    asyncio.run(service.upsert_action_type_async("create_supervision", payload))
    service._dao.items["create_supervision"]["status"] = "certified"
    result = asyncio.run(
        service.execute_action_type_async("create_supervision", object_key="", parameters={"status": "SO-001"})
    )
    assert result["object_key"] == "SO-001"
    assert action_dao.created[0]["properties"] == {"order_no": "SO-001", "pay_status": "pending"}
    assert execution_dao.executions[0]["object_key"] == "SO-001"
    # 重复创建同一标识值被拒
    with pytest.raises(OntologyAlreadyExistsError, match="动作对象已存在"):
        asyncio.run(
            service.execute_action_type_async("create_supervision", object_key="", parameters={"status": "SO-001"})
        )


def test_execute_set_property_rejects_read_only_object(monkeypatch):
    async def read_only_object(_name):
        return {**_object_type(), "write_mode": "read_only"}

    monkeypatch.setattr(service_module.ontology_service, "get_object_type_async", read_only_object)
    monkeypatch.setattr(service_module, "ontology_action_execute_enabled", lambda: True)
    service = _service()
    service._dao.items["legacy_action"] = {
        "id": 1,
        "action_type_name": "legacy_action",
        "status": "certified",
        "object_type_id": 2,
        "object_type_name": "sales_order",
        "parameters": [{"name": "status", "type": "String", "required": True}],
        "edits": [
            {
                "operation": "set_property",
                "property": "pay_status",
                "value": {"kind": "parameter", "parameter": "status"},
            }
        ],
    }
    with pytest.raises(OntologyInvalidError, match="read_only"):
        asyncio.run(service.execute_action_type_async("legacy_action", object_key="A1", parameters={"status": "paid"}))


# ---------- L2.1：test run 结构化输出（对齐官方 proposed changes / execution log） ----------


def _payload_with_criteria(logic="all", operator="is", value="unpaid"):
    payload = _payload()
    payload["submission_criteria"] = {
        "logic": logic,
        "conditions": [
            {
                "subject": {"kind": "object_property", "property": "pay_status"},
                "operator": operator,
                "value": {"kind": "static", "value": value},
                "failure_message": "只有未支付订单可以改状态",
            }
        ],
    }
    return payload


def _stub_object(monkeypatch, properties, object_key="member:sales_order:ORD2024000001"):
    async def fake_read_object(*_args, **_kwargs):
        return {"object_key": object_key, "properties": properties}

    monkeypatch.setattr(service_module, "read_object_projection", fake_read_object)


def _stub_transaction(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_transaction():
        yield None

    monkeypatch.setattr(service_module, "transaction_scope", fake_transaction)


def test_test_run_returns_proposed_changes_and_log(monkeypatch):
    _stub_object(monkeypatch, {"pay_status": "unpaid"})
    service = _service()
    asyncio.run(service.upsert_action_type_async("update_order_status", _payload()))
    result = asyncio.run(
        service.test_run_action_type_async("update_order_status", {"status": "paid"}, object_key="ORD2024000001")
    )
    assert result["executed"] is False
    changes = result["proposed_changes"]["objects"][0]["property_changes"]
    assert changes == [{"property": "pay_status", "current": "unpaid", "proposed": "paid"}]
    assert [step["step"] for step in result["execution_log"]] == [
        "metadata_loading",
        "dependency_validation",
        "submission_criteria",
        "parameter_validation",
        "edits_calculation",
    ]
    assert result["errors"]["end_user_facing"] == []


def test_test_run_writes_nothing(monkeypatch):
    _stub_object(monkeypatch, {"pay_status": "unpaid"})
    fake_execution = _FakeExecutionDao()
    service = OntologyActionTypeService(_FakeDao(), fake_execution, None, _FakeInstanceDao())
    asyncio.run(service.upsert_action_type_async("update_order_status", _payload()))
    asyncio.run(
        service.test_run_action_type_async("update_order_status", {"status": "paid"}, object_key="ORD2024000001")
    )
    assert fake_execution.execution is None
    assert fake_execution.edits == []


def test_test_run_reports_submission_criteria_failure(monkeypatch):
    _stub_object(monkeypatch, {"pay_status": "paid"})
    service = _service()
    asyncio.run(service.upsert_action_type_async("update_order_status", _payload_with_criteria()))
    result = asyncio.run(
        service.test_run_action_type_async("update_order_status", {"status": "unpaid"}, object_key="ORD2024000001")
    )
    criteria_step = next(step for step in result["execution_log"] if step["step"] == "submission_criteria")
    assert criteria_step["status"] == "failed"
    assert result["errors"]["end_user_facing"] == ["只有未支付订单可以改状态"]


def test_upsert_rejects_criteria_without_failure_message():
    payload = _payload_with_criteria()
    payload["submission_criteria"]["conditions"][0]["failure_message"] = ""
    with pytest.raises(OntologyInvalidError, match="failure_message"):
        asyncio.run(_service().upsert_action_type_async("update_order_status", payload))


def test_upsert_rejects_unknown_criteria_subject():
    payload = _payload_with_criteria()
    payload["submission_criteria"]["conditions"][0]["subject"] = {"kind": "object_property", "property": "missing"}
    with pytest.raises(OntologyInvalidError, match="不存在的属性"):
        asyncio.run(_service().upsert_action_type_async("update_order_status", payload))


def test_execute_rejects_when_submission_criteria_fail(monkeypatch):
    _stub_object(monkeypatch, {"pay_status": "paid"})
    _stub_transaction(monkeypatch)
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    fake_execution = _FakeExecutionDao()
    service = OntologyActionTypeService(_FakeDao(), fake_execution, None, _FakeInstanceDao())
    asyncio.run(
        service.upsert_action_type_async("update_order_status", {**_payload_with_criteria(), "status": "certified"})
    )
    with pytest.raises(OntologyInvalidError, match="只有未支付订单"):
        asyncio.run(
            service.execute_action_type_async(
                "update_order_status", object_key="ORD2024000001", parameters={"status": "unpaid"}
            )
        )
    assert fake_execution.execution is None
    assert fake_execution.edits == []


def test_execute_passes_criteria_and_records_version(monkeypatch):
    _stub_object(monkeypatch, {"pay_status": "unpaid"})
    _stub_transaction(monkeypatch)
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    fake_execution = _FakeExecutionDao()
    service = OntologyActionTypeService(_FakeDao(), fake_execution, None, _FakeInstanceDao())
    asyncio.run(
        service.upsert_action_type_async("update_order_status", {**_payload_with_criteria(), "status": "certified"})
    )
    result = asyncio.run(
        service.execute_action_type_async(
            "update_order_status", object_key="ORD2024000001", parameters={"status": "paid"}
        )
    )
    assert result["execution"]["action_type_version"] == 1
    assert result["execution"]["edited_object_keys"] == ["member:sales_order:ORD2024000001"]
    assert result["compiled_edits"]["property_changes"] == {"pay_status": "paid"}


def test_execute_is_idempotent_with_key(monkeypatch):
    _stub_object(monkeypatch, {"pay_status": "unpaid"})
    _stub_transaction(monkeypatch)
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    fake_execution = _FakeExecutionDao()
    service = OntologyActionTypeService(_FakeDao(), fake_execution, None, _FakeInstanceDao())
    asyncio.run(service.upsert_action_type_async("update_order_status", {**_payload(), "status": "certified"}))
    first = asyncio.run(
        service.execute_action_type_async(
            "update_order_status", object_key="ORD2024000001", parameters={"status": "paid"}, idempotency_key="k1"
        )
    )
    second = asyncio.run(
        service.execute_action_type_async(
            "update_order_status", object_key="ORD2024000001", parameters={"status": "paid"}, idempotency_key="k1"
        )
    )
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert len(fake_execution.edits) == 1


def test_config_version_increments_on_update():
    service = _service()
    first = asyncio.run(service.upsert_action_type_async("update_order_status", _payload()))
    second = asyncio.run(service.upsert_action_type_async("update_order_status", _payload(), allow_overwrite=True))
    assert first["config_version"] == 1
    assert second["config_version"] == 2


# ---------- L2.2：删除不是编辑 / 重建不继承（对齐 how-edits-applied.md:86） ----------


def _deleted_payload():
    payload = _payload()
    payload["edits"] = [{"operation": "delete_object"}]
    return payload


def test_test_run_delete_object_reports_deleted_without_writing(monkeypatch):
    async def fake_read_object(*_args, **_kwargs):
        return {"object_key": "member:sales_order:ORD2024000001", "properties": {"pay_status": "paid"}}

    monkeypatch.setattr(service_module, "read_object_projection", fake_read_object)
    fake_execution = _FakeExecutionDao()
    service = _service(execution_dao=fake_execution)
    asyncio.run(service.upsert_action_type_async("delete_order", _deleted_payload()))
    result = asyncio.run(
        service.test_run_action_type_async("delete_order", {"status": "x"}, object_key="ORD2024000001")
    )
    assert result["executed"] is False
    assert result["proposed_changes"]["objects"][0]["kind"] == "deleted"
    assert fake_execution.execution is None
    assert fake_execution.edits == []


def test_execute_delete_object_marks_instance_deleted_and_writes_no_overlay(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_transaction():
        yield None

    async def fake_read_object(*_args, **_kwargs):
        return {"object_key": "member:sales_order:ORD2024000001", "properties": {"pay_status": "paid"}}

    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    monkeypatch.setattr(service_module, "transaction_scope", fake_transaction)
    monkeypatch.setattr(service_module, "read_object_projection", fake_read_object)

    instance_dao = _FakeInstanceDao()
    fake_execution = _FakeExecutionDao()
    service = _service(execution_dao=fake_execution, instance_dao=instance_dao)
    asyncio.run(service.upsert_action_type_async("delete_order", {**_deleted_payload(), "status": "certified"}))
    result = asyncio.run(
        service.execute_action_type_async("delete_order", object_key="ORD2024000001", parameters={"status": "x"})
    )
    assert result["lifecycle_status"] == "deleted"
    assert instance_dao.rows["member:sales_order:ORD2024000001"]["lifecycle_status"] == "deleted"
    # 删除不是编辑：不追加覆盖值（既有覆盖值也不删除）
    assert fake_execution.edits == []


def test_execute_delete_object_rejects_invisible_object(monkeypatch):
    from contextlib import asynccontextmanager

    from apps.chat_data_app.services.ontology.object_errors import ObjectProjectionError

    @asynccontextmanager
    async def fake_transaction():
        yield None

    async def invisible_object(*_args, **_kwargs):
        # 已删除对象在读取层表现为「对象不存在」，因此再次执行必然失败（官方 T14）
        raise ObjectProjectionError("对象不存在: sales_order:ORD2024000001")

    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    monkeypatch.setattr(service_module, "transaction_scope", fake_transaction)
    monkeypatch.setattr(service_module, "read_object_projection", invisible_object)

    fake_execution = _FakeExecutionDao()
    service = _service(execution_dao=fake_execution)
    asyncio.run(service.upsert_action_type_async("delete_order", {**_deleted_payload(), "status": "certified"}))
    with pytest.raises(ObjectProjectionError, match="对象不存在"):
        asyncio.run(
            service.execute_action_type_async("delete_order", object_key="ORD2024000001", parameters={"status": "x"})
        )
    assert fake_execution.execution is None


def test_recreate_after_delete_bumps_generation_and_does_not_inherit(monkeypatch):
    from contextlib import asynccontextmanager

    async def action_object_type(_name):
        return {**_object_type(), "object_type_name": "supervision_order", "write_mode": "action_created"}

    @asynccontextmanager
    async def fake_transaction():
        yield None

    class _RecreateDao:
        """同一主键重建：复用已软删的行，不新建行（避免唯一约束冲突）。"""

        def __init__(self):
            self.row = {"object_key": "SO-001", "delete_flag": 1, "properties": {"order_no": "SO-001"}}
            self.revived: dict | None = None

        async def get_object_including_deleted_async(self, object_type_id, object_key):
            return dict(self.row)

        async def revive_object_async(self, **kwargs):
            self.revived = kwargs
            self.row.update(kwargs)
            self.row["delete_flag"] = 0
            return dict(self.row)

        async def create_object_async(self, **_kwargs):  # pragma: no cover - 重建路径不应新建
            raise AssertionError("重建已删除对象不应新建行")

    monkeypatch.setattr(service_module.ontology_service, "get_object_type_async", action_object_type)
    monkeypatch.setattr(service_module, "ontology_action_execute_enabled", lambda: True)
    monkeypatch.setattr(service_module, "transaction_scope", fake_transaction)

    instance_dao = _FakeInstanceDao()
    instance_dao.rows["supervision_order:SO-001"] = {"generation": 1, "lifecycle_status": "deleted"}
    action_dao = _RecreateDao()
    service = _service(action_object_dao=action_dao, instance_dao=instance_dao)
    payload = _payload()
    payload["object_type_name"] = "supervision_order"
    payload["edits"] = [
        {
            "operation": "create_object",
            "properties": [{"property": "order_no", "value": {"kind": "parameter", "parameter": "status"}}],
        }
    ]
    asyncio.run(service.upsert_action_type_async("create_supervision", {**payload, "status": "certified"}))
    result = asyncio.run(
        service.execute_action_type_async("create_supervision", object_key="", parameters={"status": "SO-001"})
    )
    assert result["recreated"] is True
    assert result["object_generation"] == 2
    assert instance_dao.rows["supervision_order:SO-001"]["generation"] == 2
    assert instance_dao.rows["supervision_order:SO-001"]["lifecycle_status"] == "active"
    assert action_dao.revived is not None


# ---------- L2.2-b：提交时冲突检测（对象级写写冲突） ----------


def _baseline_object():
    return {
        "object_key": "sales_order:ORD2024000001",
        "properties": {"pay_status": "unpaid", "order_no": "ORD2024000001"},
        "source_properties": {"pay_status": "unpaid", "order_no": "ORD2024000001"},
        "object_generation": 1,
    }


def _patch_execute_env(monkeypatch, projection):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_transaction():
        yield None

    async def fake_read_object(*_args, **_kwargs):
        return projection

    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    monkeypatch.setattr(service_module, "transaction_scope", fake_transaction)
    monkeypatch.setattr(service_module, "read_object_projection", fake_read_object)


def test_execute_rejects_stale_baseline_and_writes_nothing(monkeypatch):
    _patch_execute_env(monkeypatch, _baseline_object())
    fake_execution = _FakeExecutionDao()
    service = _service(execution_dao=fake_execution)
    asyncio.run(service.upsert_action_type_async("update_order_status", {**_payload(), "status": "certified"}))
    with pytest.raises(OntologyInvalidError, match="写写冲突"):
        asyncio.run(
            service.execute_action_type_async(
                "update_order_status",
                object_key="ORD2024000001",
                parameters={"status": "paid"},
                expected_baseline="sha256:object:stale",
            )
        )
    assert fake_execution.execution is None
    assert fake_execution.edits == []


def test_execute_accepts_matching_baseline_and_records_it(monkeypatch):
    projection = _baseline_object()
    _patch_execute_env(monkeypatch, projection)
    expected = service_module._row_fingerprint(projection["source_properties"], scope="object")
    fake_execution = _FakeExecutionDao()
    service = _service(execution_dao=fake_execution)
    asyncio.run(service.upsert_action_type_async("update_order_status", {**_payload(), "status": "certified"}))
    result = asyncio.run(
        service.execute_action_type_async(
            "update_order_status",
            object_key="ORD2024000001",
            parameters={"status": "paid"},
            expected_baseline=expected,
        )
    )
    assert result["execution"]["id"] == 99
    assert fake_execution.execution["baseline"]["row_hash"] == expected
    # 撤销要靠执行前有效值回填：基线必须记下被编辑属性的当时值
    assert fake_execution.execution["baseline"]["effective_values"] == {"pay_status": "unpaid"}


def test_execute_conflict_check_off_skips_baseline_comparison(monkeypatch):
    _patch_execute_env(monkeypatch, _baseline_object())
    fake_execution = _FakeExecutionDao()
    service = _service(execution_dao=fake_execution)
    asyncio.run(service.upsert_action_type_async("update_order_status", {**_payload(), "status": "certified"}))
    asyncio.run(
        service.execute_action_type_async(
            "update_order_status",
            object_key="ORD2024000001",
            parameters={"status": "paid"},
            expected_baseline="sha256:object:stale",
            conflict_check_level="off",
        )
    )
    assert fake_execution.execution is not None


def test_check_baseline_scope_mismatch_is_reported_explicitly():
    current = {"row_hash": service_module._row_fingerprint({"pay_status": "unpaid"}, scope="property")}
    with pytest.raises(OntologyInvalidError, match="基线口径不一致"):
        service_module._check_baseline("sha256:object:whatever", current, level="property")
    # 同口径不匹配 → 报真冲突，而不是口径错误
    with pytest.raises(OntologyInvalidError, match="写写冲突"):
        service_module._check_baseline("sha256:object:whatever", current, level="object")


def test_fingerprint_normalizes_decimal_and_datetime():
    from datetime import datetime
    from decimal import Decimal

    left = service_module._row_fingerprint(
        {"amount": Decimal("100.00"), "at": datetime(2026, 1, 1, 8, 0, 0)}, scope="object"
    )
    right = service_module._row_fingerprint({"amount": 100, "at": datetime(2026, 1, 1, 8, 0, 0)}, scope="object")
    assert left == right
    # 字符串按原值比较：语义上不同的"100.00"与"100"不该被抹平
    assert service_module._row_fingerprint({"amount": "100.00"}, scope="object") != service_module._row_fingerprint(
        {"amount": "100"}, scope="object"
    )


# ---------- L2.2-b：撤销（仅执行者、对象级最近一次、追加反向编辑） ----------


class _FakeRevertDao:
    def __init__(self, execution, *, latest=None):
        self.execution = execution
        self.latest = latest
        self.created = None
        self.edits = []
        self.marked = None

    async def get_execution_async(self, execution_id):
        return dict(self.execution) if self.execution else None

    async def create_execution_async(self, **kwargs):
        self.created = {"id": 500, **kwargs}
        return self.created

    async def append_edit_async(self, **kwargs):
        self.edits.append(kwargs)

    async def latest_edit_async(self, object_type_id, object_key, object_generation=1):
        return self.latest

    async def mark_reverted_async(self, execution_id, *, operator=""):
        self.marked = (execution_id, operator)
        return True


def _execution_row(**overrides):
    row = {
        "id": 42,
        "action_type_name": "update_order_status",
        "status": "succeeded",
        "create_by": "u1",
        "object_key": "sales_order:ORD2024000001",
        "edited_object_keys": ["sales_order:ORD2024000001"],
        "action_type_version": 3,
        "revert_of_execution_id": 0,
        "edits": [
            {"operation": "set_property", "property": "pay_status", "value": {"kind": "static", "value": "paid"}}
        ],
        "baseline": {
            "object_key": "sales_order:ORD2024000001",
            "object_generation": 1,
            "row_hash": "",
            "row_hash_scope": "object",
            "effective_values": {"pay_status": "unpaid"},
        },
    }
    row.update(overrides)
    return row


def _revert_service(dao, instance_dao=None):
    service = OntologyActionTypeService(_FakeDao(), dao, None, instance_dao or _FakeInstanceDao())
    asyncio.run(service.upsert_action_type_async("update_order_status", {**_payload(), "status": "certified"}))
    return service


def test_revert_appends_inverse_edit_and_marks_original(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_transaction():
        yield None

    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    monkeypatch.setattr(service_module, "transaction_scope", fake_transaction)
    dao = _FakeRevertDao(_execution_row(), latest={"execution_id": 42})
    service = _revert_service(dao)
    result = asyncio.run(service.revert_execution_async("update_order_status", 42, operator="u1"))
    assert result["reverted_execution_id"] == 42
    assert result["reversed_edits"][0]["resolved_value"] == "unpaid"
    assert dao.edits[0]["value"] == "unpaid"
    assert dao.edits[0]["object_generation"] == 1
    assert dao.edits[0]["action_execution_id"] == 500
    assert dao.created["options"].revert_of_execution_id == 42
    assert dao.marked == (42, "u1")


def test_revert_rejects_non_executor(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    dao = _FakeRevertDao(_execution_row(), latest={"execution_id": 42})
    service = _revert_service(dao)
    with pytest.raises(OntologyInvalidError, match="执行者本人"):
        asyncio.run(service.revert_execution_async("update_order_status", 42, operator="u2"))
    assert dao.created is None


def test_revert_rejects_when_object_has_later_edit(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    dao = _FakeRevertDao(_execution_row(), latest={"execution_id": 43})
    service = _revert_service(dao)
    with pytest.raises(OntologyInvalidError, match="又有新的编辑"):
        asyncio.run(service.revert_execution_async("update_order_status", 42, operator="u1"))


def test_revert_rejects_already_reverted_execution(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    dao = _FakeRevertDao(_execution_row(status="reverted"), latest={"execution_id": 42})
    service = _revert_service(dao)
    with pytest.raises(OntologyInvalidError, match="只有 succeeded"):
        asyncio.run(service.revert_execution_async("update_order_status", 42, operator="u1"))


def test_revert_rejects_revert_record_itself(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    dao = _FakeRevertDao(_execution_row(revert_of_execution_id=7), latest={"execution_id": 42})
    service = _revert_service(dao)
    with pytest.raises(OntologyInvalidError, match="不能再被撤销"):
        asyncio.run(service.revert_execution_async("update_order_status", 42, operator="u1"))


def test_revert_rejects_execution_without_baseline(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    dao = _FakeRevertDao(_execution_row(baseline=None), latest={"execution_id": 42})
    service = _revert_service(dao)
    with pytest.raises(OntologyInvalidError, match="缺少基线快照"):
        asyncio.run(service.revert_execution_async("update_order_status", 42, operator="u1"))


def test_revert_delete_restores_visibility(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_transaction():
        yield None

    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    monkeypatch.setattr(service_module, "transaction_scope", fake_transaction)
    instance_dao = _FakeInstanceDao()
    instance_dao.rows["sales_order:ORD2024000001"] = {
        "generation": 1,
        "lifecycle_status": "deleted",
        "last_execution_id": 42,
    }
    dao = _FakeRevertDao(
        _execution_row(
            edits=[{"operation": "delete_object"}],
            baseline={"object_generation": 1, "effective_values": {}},
        )
    )
    service = _revert_service(dao, instance_dao=instance_dao)
    result = asyncio.run(service.revert_execution_async("update_order_status", 42, operator="u1"))
    assert result["operation"] == "delete_object"
    assert instance_dao.rows["sales_order:ORD2024000001"]["lifecycle_status"] == "active"
    # 撤销删除不是重建：代际不变，旧编辑继续生效
    assert instance_dao.rows["sales_order:ORD2024000001"]["generation"] == 1


def test_revert_delete_rejects_when_object_was_recreated(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    instance_dao = _FakeInstanceDao()
    instance_dao.rows["sales_order:ORD2024000001"] = {
        "generation": 2,
        "lifecycle_status": "active",
        "last_execution_id": 44,
    }
    dao = _FakeRevertDao(_execution_row(edits=[{"operation": "delete_object"}], baseline={"object_generation": 1}))
    service = _revert_service(dao, instance_dao=instance_dao)
    with pytest.raises(OntologyInvalidError, match="对象状态已变化"):
        asyncio.run(service.revert_execution_async("update_order_status", 42, operator="u1"))


def test_revert_create_removes_object(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_transaction():
        yield None

    async def action_object_type(_name):
        return {**_object_type(), "object_type_name": "supervision_order", "write_mode": "action_created"}

    class _ActionDao:
        def __init__(self):
            self.deleted = None

        async def soft_delete_object_async(self, **kwargs):
            self.deleted = kwargs
            return True

    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    monkeypatch.setattr(service_module.ontology_service, "get_object_type_async", action_object_type)
    monkeypatch.setattr(service_module, "transaction_scope", fake_transaction)

    instance_dao = _FakeInstanceDao()
    instance_dao.rows["supervision_order:SO-001"] = {
        "generation": 1,
        "lifecycle_status": "active",
        "last_execution_id": 42,
    }
    action_dao = _ActionDao()
    dao = _FakeRevertDao(
        _execution_row(
            action_type_name="create_supervision",
            object_key="SO-001",
            edits=[{"operation": "create_object", "properties": []}],
            baseline={"object_generation": 0, "effective_values": {}},
        ),
        latest={"execution_id": 42},
    )
    service = OntologyActionTypeService(_FakeDao(), dao, action_dao, instance_dao)
    asyncio.run(service.upsert_action_type_async("create_supervision", {**_payload(), "status": "certified"}))
    asyncio.run(service.revert_execution_async("create_supervision", 42, operator="u1"))
    assert action_dao.deleted["object_key"] == "SO-001"
    assert instance_dao.rows["supervision_order:SO-001"]["lifecycle_status"] == "deleted"
