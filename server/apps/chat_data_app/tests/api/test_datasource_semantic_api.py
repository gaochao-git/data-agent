from apps.chat_data_app.routers import semantic_config as semantic_routes


def test_list_datasources_api_contract(client, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.get("/api/chatdata/datasources")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["code"] == 2000
    assert "datasources" in payload["data"]
    assert payload["data"]["datasources"][0]["id"] == "ds_a"
    assert payload["data"]["datasources"][0]["ds_tables"]


def test_get_datasource_schema_api_contract(client, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    response = client.get("/api/chatdata/datasources/ds_a/schema", params={"tables": "orders"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["count"] == 1
    assert payload["data"]["source"] == "cache"
    assert payload["data"]["tables"][0]["table_name"] == "orders"


def test_list_semantic_configs_api_contract(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(semantic_routes, "get_datasource_manager", lambda: fake_datasource_manager)

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


def test_list_my_semantic_configs_api_contract(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(semantic_routes, "get_datasource_manager", lambda: fake_datasource_manager)

    response = client.get("/api/chatdata/datasources/ds_a/my-semantic-configs", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert [item["id"] for item in payload["data"]["configs"]] == ["cfg_a"]
    assert payload["data"]["configs"][0]["can_manage"] is True


def test_list_admin_semantic_configs_api_contract(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(semantic_routes, "get_datasource_manager", lambda: fake_datasource_manager)

    response = client.get("/api/chatdata/admin/datasources/ds_a/semantic-configs", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert [item["id"] for item in payload["data"]["configs"]] == ["cfg_public"]
    assert payload["data"]["configs"][0]["can_manage"] is True


def test_create_semantic_config_sets_private_scope_and_creator(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(semantic_routes, "get_datasource_manager", lambda: fake_datasource_manager)
    body = {
        "name": "个人订单分析",
        "description": "个人指标集",
        "scope_type": "public",
        "enabled": True,
        "metrics": [
            {"name": "销售额", "expression": "SUM(amount)", "table": "orders"}
        ],
        "dimensions": [
            {"name": "城市", "column": "city", "table": "orders", "type": "categorical"}
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
    assert fake_datasource_manager.created_semantic_kwargs["scope_type"] == "private"
    assert fake_datasource_manager.created_semantic_kwargs["create_by"] == "user_a"


def test_admin_create_semantic_config_sets_public_scope(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(semantic_routes, "get_datasource_manager", lambda: fake_datasource_manager)
    body = {
        "name": "公共订单分析",
        "description": "公共指标集",
        "enabled": True,
        "metrics": [
            {"name": "销售额", "expression": "SUM(amount)", "table": "orders"}
        ],
        "dimensions": [
            {"name": "城市", "column": "city", "table": "orders", "type": "categorical"}
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
    assert fake_datasource_manager.created_semantic_kwargs["scope_type"] == "public"
    assert fake_datasource_manager.created_semantic_kwargs["create_by"] == "user_a"


def test_update_semantic_config_allows_private_owner(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(semantic_routes, "get_datasource_manager", lambda: fake_datasource_manager)

    response = client.put(
        "/api/chatdata/datasources/ds_a/semantic-configs/cfg_a",
        json={"name": "个人订单分析V2"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["name"] == "个人订单分析V2"
    assert fake_datasource_manager.updated_semantic_kwargs["update_by"] == "user_a"


def test_update_semantic_config_denies_public_in_normal_endpoint(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(semantic_routes, "get_datasource_manager", lambda: fake_datasource_manager)

    response = client.put(
        "/api/chatdata/datasources/ds_a/semantic-configs/cfg_public",
        json={"name": "不允许"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == 40304


def test_admin_update_semantic_config_allows_public(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(semantic_routes, "get_datasource_manager", lambda: fake_datasource_manager)

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


def test_delete_semantic_config_allows_private_owner_only(client, auth_headers, fake_datasource_manager, monkeypatch):
    monkeypatch.setattr(semantic_routes, "get_datasource_manager", lambda: fake_datasource_manager)

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
    assert denied_public.json()["code"] == 40304
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "success"
    assert allowed.json()["data"]["config_id"] == "cfg_a"
    assert fake_datasource_manager.deleted_config_id == "cfg_a"
