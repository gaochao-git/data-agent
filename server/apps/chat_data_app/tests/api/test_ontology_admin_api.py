"""本体一致性检查（Admin）接口契约测试：组合层打桩，纯函数行为不在此重复验证。"""

import pytest

from apps.chat_data_app.routers import ontology_admin as ontology_admin_routes

_URL = "/api/chatdata/admin/meta/ontology/consistency-check"


def _install_stubs(monkeypatch, *, groups, configs, bundle, findings=None):
    class _StubDataSourceDao:
        async def get_all_datasources_async(self, include_tables=True):
            return groups

    monkeypatch.setattr(ontology_admin_routes, "DataSourceDao", _StubDataSourceDao)

    class _StubService:
        async def get_configs_async(self, datasource_id, user_id=""):
            return configs.get(datasource_id, [])

    monkeypatch.setattr(ontology_admin_routes, "get_semantic_config_service", lambda: _StubService())

    class _StubDao:
        def __init__(self):
            self.captured_ids = None

        async def load_injection_bundle_async(self, config_ids):
            self.captured_ids = sorted(config_ids)
            return bundle

    stub_dao = _StubDao()
    monkeypatch.setattr(ontology_admin_routes, "OntologyDao", lambda: stub_dao)
    if findings is not None:
        monkeypatch.setattr(
            ontology_admin_routes, "_run_consistency_check", lambda bundle, configs, *, config_group_ids=None: findings
        )
    return stub_dao


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def test_consistency_check_contract(client, auth_headers, monkeypatch):
    groups = [
        {"group_id": "group-1"},
        {"group_id": "group-2"},
    ]
    configs = {
        "group-1": [
            {"id": "cfg-1", "name": "模型一", "dimensions": []},
            {"id": "cfg-user", "name": "私有模型", "dimensions": [], "scope_type": "user"},
        ]
    }
    bundle = {
        "object_types": [{"id": 1, "object_type_name": "customer"}],
        "properties": [],
        "relation_types": [],
        "mappings": [],
    }
    findings = [
        {
            "code": "ONT_MAPPING_DIM_MISSING",
            "severity": "error",
            "category": "ontology",
            "message": "对象类型「customer」到模型 cfg-1 的映射存在 1 个失效维度引用",
            "targets": ["customer", "cfg-1"],
        },
        {
            "code": "ONT_PROP_UNMAPPED",
            "severity": "info",
            "category": "ontology",
            "message": "对象类型「customer」有 1 个属性未映射到模型维度",
            "targets": ["customer"],
        },
    ]
    stub_dao = _install_stubs(monkeypatch, groups=groups, configs=configs, bundle=bundle, findings=findings)

    response = client.get(_URL, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    data = payload["data"]
    # 公共配置收集：私有配置被过滤；无配置的组被跳过
    assert data["group_count"] == 2
    assert data["config_count"] == 1
    assert data["object_type_count"] == 1
    assert stub_dao.captured_ids == ["cfg-1"]
    assert data["findings"] == findings
    assert data["summary"] == {"error": 1, "warning": 0, "info": 1}
    assert data["healthy"] is False


def test_consistency_check_healthy(client, auth_headers, monkeypatch):
    _install_stubs(
        monkeypatch,
        groups=[{"group_id": "group-1"}],
        configs={"group-1": [{"id": "cfg-1", "name": "模型一", "dimensions": []}]},
        bundle={"object_types": [], "properties": [], "relation_types": [], "mappings": []},
        findings=[],
    )
    response = client.get(_URL, headers=auth_headers)
    payload = response.json()
    assert payload["data"]["healthy"] is True
    assert payload["data"]["summary"] == {"error": 0, "warning": 0, "info": 0}


def test_consistency_check_no_groups(client, auth_headers, monkeypatch):
    """无数据源组时不触发任何模型加载，返回健康空报告。"""
    stub_dao = _install_stubs(
        monkeypatch,
        groups=[],
        configs={"group-1": [{"id": "cfg-1", "name": "模型一", "dimensions": []}]},
        bundle={"object_types": [], "properties": [], "relation_types": [], "mappings": []},
    )
    response = client.get(_URL, headers=auth_headers)
    payload = response.json()
    assert payload["data"]["config_count"] == 0
    assert payload["data"]["healthy"] is True
    assert stub_dao.captured_ids is None


def test_consistency_check_requires_auth(client, missing_header):
    response = client.get(_URL, **missing_header)
    assert response.status_code in (401, 403)


@pytest.fixture
def missing_header():
    return {"headers": {}}


def test_read_ontology_object_contract(client, auth_headers, monkeypatch):
    async def fake_read(object_type_name, object_key, **kwargs):
        return {
            "object_type": object_type_name,
            "object_key": f"{object_type_name}:{object_key}",
            "properties": {"order_id": object_key, "amount": 100},
            "links": {},
        }

    monkeypatch.setattr(ontology_admin_routes, "read_object_projection", fake_read)
    response = client.get(
        "/api/chatdata/admin/meta/ontology/object-types/Order/objects/read",
        params={"object_key": "A001"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["object_key"] == "Order:A001"
    assert payload["data"]["properties"]["amount"] == 100


_ACTION_BASE = "/api/chatdata/admin/meta/ontology/action-types"


def _action_payload():
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


def test_action_type_crud_and_test_run_contract(client, auth_headers, monkeypatch):
    class FakeService:
        def __init__(self):
            self.saved = None

        async def upsert_action_type_async(self, name, payload, **kwargs):
            self.saved = {"action_type_name": name, "object_type_name": payload["object_type_name"], **payload}
            return self.saved

        async def list_action_types_async(self, object_type_name=""):
            return [self.saved] if self.saved else []

        async def get_action_type_async(self, name):
            return self.saved if self.saved and self.saved["action_type_name"] == name else None

        async def delete_action_type_async(self, name, **kwargs):
            return None

        async def test_run_action_type_async(self, name, parameters):
            return {"action_type_name": name, "parameters": parameters, "edits": [], "executed": False}

        async def list_executions_async(self, name, *, object_key="", limit=50):
            return [{"id": 7, "action_type_name": name, "object_key": object_key or "ORD2024000001"}]

        async def execute_action_type_async(self, name, *, object_key, parameters, **kwargs):
            return {
                "execution": {"id": 99, "action_type_name": name, "object_key": object_key},
                "object_type_name": "sales_order",
                "object_key": object_key,
                "edits": [],
            }

    fake = FakeService()
    monkeypatch.setattr(ontology_admin_routes, "_action_type_service", fake)

    saved = client.put(f"{_ACTION_BASE}/update_order_status", json=_action_payload(), headers=auth_headers)
    assert saved.status_code == 200
    assert saved.json()["data"]["action_type_name"] == "update_order_status"

    listed = client.get(_ACTION_BASE, headers=auth_headers)
    assert listed.json()["data"]["action_types"][0]["object_type_name"] == "sales_order"

    detail = client.get(f"{_ACTION_BASE}/update_order_status", headers=auth_headers)
    assert detail.json()["data"]["display_name"] == "更新订单状态"

    execution_list = client.get(
        f"{_ACTION_BASE}/update_order_status/executions",
        headers=auth_headers,
    )
    assert execution_list.json()["data"]["executions"][0]["id"] == 7

    test_run = client.post(
        f"{_ACTION_BASE}/update_order_status/test-run",
        json={"parameters": {"status": "paid"}},
        headers=auth_headers,
    )
    assert test_run.json()["data"]["executed"] is False

    executed = client.post(
        f"{_ACTION_BASE}/update_order_status/execute",
        json={"object_key": "ORD2024000001", "parameters": {"status": "paid"}},
        headers=auth_headers,
    )
    assert executed.json()["data"]["execution"]["id"] == 99
    assert executed.json()["data"]["object_key"] == "ORD2024000001"

    deleted = client.delete(f"{_ACTION_BASE}/update_order_status", headers=auth_headers)
    assert deleted.json()["status"] == "success"
