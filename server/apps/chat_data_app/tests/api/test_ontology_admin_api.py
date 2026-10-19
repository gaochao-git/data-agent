"""本体一致性检查（Admin）接口契约测试：组合层打桩，纯函数行为不在此重复验证。"""

import pytest

from apps.chat_data_app.routers import ontology_admin as ontology_admin_routes

_URL = "/api/chatdata/admin/meta/ontology/consistency-check"


def _install_stubs(monkeypatch, *, domains, configs, bundle, findings=None):
    monkeypatch.setattr(
        ontology_admin_routes.ontology_service, "list_domains_async", _async_return(domains)
    )

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
        monkeypatch.setattr(ontology_admin_routes, "_run_consistency_check", lambda bundle, configs, *, config_group_ids=None: findings)
    return stub_dao


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def test_consistency_check_contract(client, auth_headers, monkeypatch):
    domains = [
        {"domain_name": "sales", "group_id": "group-1"},
        {"domain_name": "empty", "group_id": ""},
    ]
    configs = {
        "group-1": [
            {"id": "cfg-1", "name": "模型一", "dimensions": []},
            {"id": "cfg-user", "name": "私有模型", "dimensions": [], "scope_type": "user"},
        ]
    }
    bundle = {"object_types": [{"id": 1, "object_type_name": "customer"}], "properties": [], "relation_types": [], "mappings": []}
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
    stub_dao = _install_stubs(monkeypatch, domains=domains, configs=configs, bundle=bundle, findings=findings)

    response = client.get(_URL, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    data = payload["data"]
    # 公共配置收集：私有配置被过滤；空 group_id 的领域被跳过
    assert data["group_count"] == 1
    assert data["config_count"] == 1
    assert data["object_type_count"] == 1
    assert stub_dao.captured_ids == ["cfg-1"]
    assert data["findings"] == findings
    assert data["summary"] == {"error": 1, "warning": 0, "info": 1}
    assert data["healthy"] is False


def test_consistency_check_healthy(client, auth_headers, monkeypatch):
    _install_stubs(
        monkeypatch,
        domains=[{"domain_name": "sales", "group_id": "group-1"}],
        configs={"group-1": [{"id": "cfg-1", "name": "模型一", "dimensions": []}]},
        bundle={"object_types": [], "properties": [], "relation_types": [], "mappings": []},
        findings=[],
    )
    response = client.get(_URL, headers=auth_headers)
    payload = response.json()
    assert payload["data"]["healthy"] is True
    assert payload["data"]["summary"] == {"error": 0, "warning": 0, "info": 0}


def test_consistency_check_no_domains(client, auth_headers, monkeypatch):
    """无领域挂载时不触发任何模型加载，返回健康空报告。"""
    stub_dao = _install_stubs(
        monkeypatch,
        domains=[],
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
