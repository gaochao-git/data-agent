from types import SimpleNamespace

from apps.chat_data_app.routers import datasource_admin as datasource_admin_routes


def test_update_datasource_meta_api_contract(client, fake_datasource_manager, patch_datasource_manager, auth_headers):
    patch_datasource_manager(fake_datasource_manager)

    response = client.patch(
        "/api/chatdata/admin/datasources/ds_a",
        json={"name": "数据源A-改名", "description": "新的描述"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["id"] == "ds_a"
    assert payload["data"]["group_name"] == "数据源A-改名"
    assert payload["data"]["description"] == "新的描述"


def test_update_datasource_mapped_modules_api_contract(client, fake_datasource_manager, patch_datasource_manager, auth_headers):
    patch_datasource_manager(fake_datasource_manager)

    response = client.patch(
        "/api/chatdata/admin/datasources/ds_a/mapped-modules",
        json={"mapped_modules": ["智能问数", "可视化查询"]},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["datasource_id"] == "ds_a"
    assert payload["data"]["mapped_modules"] == ["智能问数", "可视化查询"]


def test_table_groups_read_and_save_api_contract(client, fake_datasource_manager, patch_datasource_manager, auth_headers):
    patch_datasource_manager(fake_datasource_manager)

    list_response = client.get("/api/chatdata/admin/datasources/ds_a/table-groups")
    save_response = client.put(
        "/api/chatdata/admin/datasources/ds_a/table-groups",
        json={"groups": [{"table_group_name": "订单域", "table_names": ["orders"]}]},
        headers=auth_headers,
    )

    assert list_response.status_code == 200
    assert list_response.json()["data"]["groups"][0]["table_group_name"] == "交易域"
    assert save_response.status_code == 200
    payload = save_response.json()
    assert payload["status"] == "success"
    assert payload["data"]["groups"] == [{"table_group_name": "订单域", "table_names": ["orders"]}]
    assert fake_datasource_manager.replaced_table_groups == [{"table_group_name": "订单域", "table_names": ["orders"]}]
    assert fake_datasource_manager.replaced_table_groups_operator == "user_a"


def test_column_dict_api_contract(client, fake_datasource_manager, monkeypatch, patch_datasource_manager, auth_headers):
    class FakeLlm:
        async def ainvoke(self, messages):
            assert "1 表示有效" in messages[0].content
            return SimpleNamespace(content='{"dict":{"enum_values":[{"value":"1","label":"有效"},{"value":"0","label":"无效"}],"synonyms":["订单状态"],"storage_format":"","unit":""}}')

    patch_datasource_manager(fake_datasource_manager)
    monkeypatch.setattr(datasource_admin_routes, "get_chat_data_llm", lambda **_kwargs: FakeLlm())

    list_response = client.get("/api/chatdata/datasources/ds_a/tables/orders/columns/status/dict", headers=auth_headers)
    save_response = client.put(
        "/api/chatdata/admin/datasources/ds_a/tables/orders/columns/status/dict",
        json={"dict": {"enum_values": [{"value": "1", "label": "有效"}, {"value": "0", "label": "无效"}], "synonyms": ["状态"], "storage_format": "", "unit": ""}},
        headers=auth_headers,
    )
    generate_response = client.post(
        "/api/chatdata/admin/datasources/ds_a/tables/orders/columns/status/dict/generate",
        json={"text": "订单状态：1 表示有效，0 表示无效"},
        headers=auth_headers,
    )

    assert list_response.status_code == 200
    assert list_response.json()["data"]["dict"]["enum_values"] == [{"value": "1", "label": "有效"}]
    assert save_response.status_code == 200
    assert save_response.json()["data"]["dict"]["enum_values"] == [{"value": "1", "label": "有效"}, {"value": "0", "label": "无效"}]
    assert fake_datasource_manager.replaced_column_dict_operator == "user_a"
    assert generate_response.status_code == 200
    assert generate_response.json()["data"]["dict"]["enum_values"] == [{"value": "1", "label": "有效"}, {"value": "0", "label": "无效"}]
    assert generate_response.json()["data"]["dict"]["synonyms"] == []


def test_column_dict_routes_require_auth(client, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    get_response = client.get("/api/chatdata/datasources/ds_a/tables/orders/columns/status/dict")
    generate_response = client.post(
        "/api/chatdata/admin/datasources/ds_a/tables/orders/columns/status/dict/generate",
        json={"text": "订单状态：1 表示有效"},
    )

    assert get_response.status_code == 401
    assert get_response.json()["status"] == "error"
    assert get_response.json()["code"] == 401
    assert generate_response.status_code == 401
    assert generate_response.json()["status"] == "error"
    assert generate_response.json()["code"] == 401


def test_set_and_clear_default_datasource_api_contract(client, fake_datasource_manager, patch_datasource_manager, auth_headers):
    patch_datasource_manager(fake_datasource_manager)

    set_response = client.post("/api/chatdata/admin/datasources/ds_a/set-default", headers=auth_headers)
    clear_response = client.delete("/api/chatdata/admin/datasources/ds_a/set-default", headers=auth_headers)

    assert set_response.status_code == 200
    assert set_response.json()["data"] == {"datasource_id": "ds_a", "is_default": True}
    assert clear_response.status_code == 200
    assert clear_response.json()["data"] == {"datasource_id": "ds_a", "is_default": False}
    assert fake_datasource_manager.default_update_by == "user_a"


def test_delete_datasource_api_contract(client, fake_datasource_manager, patch_datasource_manager, auth_headers):
    patch_datasource_manager(fake_datasource_manager)

    response = client.delete("/api/chatdata/admin/datasources/ds_a", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["datasource_id"] == "ds_a"
    assert fake_datasource_manager.deleted_datasource_id == "ds_a"
    assert fake_datasource_manager.delete_update_by == "user_a"
