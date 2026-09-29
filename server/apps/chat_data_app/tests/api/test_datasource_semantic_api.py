from utils.error_codes import ErrorCode
from apps.chat_data_app.routers import semantic_config as semantic_routes  # noqa: F401  # 测试按模块属性引用 semantic_config，属有意重导出


def test_list_datasources_api_contract(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.get("/api/chatdata/datasources", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["code"] == ErrorCode.SUCCESS
    assert "datasources" in payload["data"]
    assert payload["data"]["datasources"][0]["id"] == "ds_a"
    assert payload["data"]["datasources"][0]["ds_tables"]


def test_get_datasource_schema_api_contract(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.get("/api/chatdata/datasources/ds_a/schema", params={"tables": "orders"}, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["count"] == 1
    assert payload["data"]["source"] == "cache"
    assert payload["data"]["tables"][0]["table_name"] == "orders"


def test_get_datasource_schema_requires_auth(client, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.get("/api/chatdata/datasources/ds_a/schema", params={"tables": "orders"})

    assert response.status_code == 401
    assert response.json()["status"] == "error"


def test_get_datasource_schema_denies_unauthorized_datasource(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.get("/api/chatdata/datasources/ds_unknown/schema", params={"tables": "orders"}, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_PERMISSION_DENIED


def test_list_semantic_configs_api_contract(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.get("/api/chatdata/datasources/ds_a/semantic-configs", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    configs = payload["data"]["configs"]
    assert payload["data"]["total"] == 2
    own_config = next(item for item in configs if item["id"] == "cfg_a")
    public_config = next(item for item in configs if item["id"] == "cfg_public")
    assert own_config["scope_type"] == "private"
    assert own_config["can_manage"] is True
    assert public_config["scope_type"] == "public"
    assert public_config["can_manage"] is False


def test_list_my_semantic_configs_api_contract(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.get("/api/chatdata/datasources/ds_a/my-semantic-configs", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert [item["id"] for item in payload["data"]["configs"]] == ["cfg_a"]
    assert payload["data"]["configs"][0]["can_manage"] is True


def test_list_admin_semantic_configs_api_contract(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.get("/api/chatdata/admin/datasources/ds_a/semantic-configs", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert [item["id"] for item in payload["data"]["configs"]] == ["cfg_public"]
    assert payload["data"]["configs"][0]["can_manage"] is True


def test_create_semantic_config_sets_private_scope_and_creator(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    semantic_service = patch_datasource_manager(fake_datasource_manager)
    body = {
        "name": "个人订单分析",
        "description": "个人指标集",
        "scope_type": "public",
        "enabled": True,
        "metrics": [
            {"name": "销售额", "expression": "SUM(amount)", "table": "orders"}
        ],
        "dimensions": [
            {"name": "城市", "column": "city", "table": "orders", "logical_type": "text"}
        ],
        "relationships": [],
    }

    response = client.post("/api/chatdata/datasources/ds_a/semantic-configs", json=body, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["id"] == "cfg_new"
    assert payload["data"]["scope_type"] == "private"
    assert payload["data"]["create_by"] == "user_a"
    assert payload["data"]["can_manage"] is True
    assert semantic_service.created_kwargs["scope_type"] == "private"
    assert semantic_service.created_kwargs["create_by"] == "user_a"


def test_admin_create_semantic_config_sets_public_scope(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    semantic_service = patch_datasource_manager(fake_datasource_manager)
    body = {
        "name": "公共订单分析",
        "description": "公共指标集",
        "enabled": True,
        "metrics": [
            {"name": "销售额", "expression": "SUM(amount)", "table": "orders"}
        ],
        "dimensions": [
            {"name": "城市", "column": "city", "table": "orders", "logical_type": "text"}
        ],
        "relationships": [],
    }

    response = client.post("/api/chatdata/admin/datasources/ds_a/semantic-configs", json=body, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["id"] == "cfg_new"
    assert payload["data"]["scope_type"] == "public"
    assert payload["data"]["create_by"] == "user_a"
    assert payload["data"]["can_manage"] is True
    assert semantic_service.created_kwargs["scope_type"] == "public"
    assert semantic_service.created_kwargs["create_by"] == "user_a"


def test_admin_generate_basic_semantic_configs_uses_public_scope(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    semantic_service = patch_datasource_manager(fake_datasource_manager)

    response = client.post(
        "/api/chatdata/admin/datasources/ds_a/semantic-configs/generate-basic",
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["items"][0]["status"] == "created"
    assert "scope_type" not in semantic_service.created_kwargs
    assert semantic_service.created_kwargs["create_by"] == "user_a"


def test_admin_generate_basic_semantic_configs_hides_internal_error(client, auth_headers, fake_datasource_manager, patch_datasource_manager, monkeypatch):
    semantic_service = patch_datasource_manager(fake_datasource_manager)

    async def raise_internal_error(*_args, **_kwargs):
        raise RuntimeError("database password leaked")

    monkeypatch.setattr(semantic_service, "generate_basic_public_single_table_configs_async", raise_internal_error)
    response = client.post(
        "/api/chatdata/admin/datasources/ds_a/semantic-configs/generate-basic",
        headers=auth_headers,
    )

    payload = response.json()
    assert payload["status"] == "error"
    assert payload["msg"] == "生成失败，请稍后重试"
    assert "password" not in payload["msg"]


def test_generate_basic_semantic_configs_has_no_user_route(client, auth_headers):
    response = client.post(
        "/api/chatdata/datasources/ds_a/semantic-configs/generate-basic",
        headers=auth_headers,
    )

    assert response.status_code == 405


def test_update_semantic_config_allows_private_owner(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    semantic_service = patch_datasource_manager(fake_datasource_manager)

    response = client.put(
        "/api/chatdata/datasources/ds_a/semantic-configs/cfg_a",
        json={"name": "个人订单分析V2"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["name"] == "个人订单分析V2"
    assert semantic_service.updated_kwargs["update_by"] == "user_a"


def test_update_semantic_config_denies_public_in_normal_endpoint(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.put(
        "/api/chatdata/datasources/ds_a/semantic-configs/cfg_public",
        json={"name": "不允许"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED


def test_admin_update_semantic_config_allows_public(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.put(
        "/api/chatdata/admin/datasources/ds_a/semantic-configs/cfg_public",
        json={"name": "公共指标V2"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["name"] == "公共指标V2"
    assert payload["data"]["can_manage"] is True


def test_delete_semantic_config_allows_private_owner_only(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    semantic_service = patch_datasource_manager(fake_datasource_manager)

    denied_public = client.delete(
        "/api/chatdata/datasources/ds_a/semantic-configs/cfg_public",
        headers=auth_headers,
    )
    allowed = client.delete(
        "/api/chatdata/datasources/ds_a/semantic-configs/cfg_a",
        headers=auth_headers,
    )

    assert denied_public.status_code == 200
    assert denied_public.json()["status"] == "error"
    assert denied_public.json()["code"] == ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "success"
    assert allowed.json()["data"]["config_id"] == "cfg_a"
    assert semantic_service.deleted_config_id == "cfg_a"


def test_admin_semantic_quality_check_api_contract(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.get("/api/chatdata/admin/datasources/ds_a/semantic-configs/quality-check", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    data = payload["data"]
    # 后台质量检查只面向公共配置：私有 cfg_a 不参与（不泄露其元信息），空配置 cfg_public 触发 COV 类 finding
    assert [item["config_id"] for item in data["configs"]] == ["cfg_public"]
    assert data["overall"]["config_count"] == 1
    assert data["overall"]["inventory_available"] is True
    assert data["overall"]["table_total"] == 1
    assert data["overall"]["table_modeled"] == 0
    assert 90 <= data["overall"]["avg_score"] <= 100
    cfg_public = data["configs"][0]
    assert cfg_public["score"] >= 90
    assert all(finding["severity"] != "error" for finding in cfg_public["findings"])
    assert cfg_public["stats"]["metric_count"] == 0
    assert cfg_public["stats"]["dimension_count"] == 0
    # 空配置不触发 COV_NO_METRICS/COV_NO_DIMENSIONS（前提是存在另一侧字段），只有文档完备性提示
    codes = {finding["code"] for finding in cfg_public["findings"]}
    assert "DOC_NO_BUSINESS_NOTES" in codes


def test_admin_semantic_quality_check_unknown_datasource(client, auth_headers, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.get("/api/chatdata/admin/datasources/ds_unknown/semantic-configs/quality-check", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_admin_semantic_quality_check_requires_auth(client, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.get("/api/chatdata/admin/datasources/ds_a/semantic-configs/quality-check")

    assert response.status_code == 401
    assert response.json()["status"] == "error"
