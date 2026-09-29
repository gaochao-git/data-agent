from types import SimpleNamespace

from apps.chat_data_app.routers import datasource_admin as datasource_admin_routes  # noqa: F401  # 测试按模块属性引用 datasource_admin，属有意重导出
from apps.chat_data_app.services.datasource import column_dict_generate
from utils.error_codes import ErrorCode


def test_admin_list_datasources_uses_unfiltered_list(client, patch_datasource_manager, auth_headers):
    class Manager:
        def __init__(self):
            self.all_include_tables = None
            self.authorized_called = False

        async def get_all_datasources_async(self, include_tables: bool = True):
            self.all_include_tables = include_tables
            return [{"id": "ds_admin", "group_name": "后台数据源", "ds_tables": []}]

        async def get_authorized_datasources_async(self, include_tables: bool = True, user_id: str = "", username: str = ""):
            self.authorized_called = True
            return []

    manager = Manager()
    patch_datasource_manager(manager)

    response = client.get("/api/chatdata/admin/datasources", params={"include_tables": False}, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["datasources"] == [{"id": "ds_admin", "group_name": "后台数据源", "ds_tables": []}]
    assert manager.all_include_tables is False
    assert manager.authorized_called is False


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

    list_response = client.get("/api/chatdata/admin/datasources/ds_a/table-groups", headers=auth_headers)
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


def test_admin_datasource_read_routes_require_auth(client, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    list_response = client.get("/api/chatdata/admin/datasources")
    table_groups_response = client.get("/api/chatdata/admin/datasources/ds_a/table-groups")
    semantic_health_response = client.get("/api/chatdata/admin/datasources/ds_a/semantic-health")

    assert list_response.status_code == 401
    assert list_response.json()["status"] == "error"
    assert list_response.json()["code"] == ErrorCode.CHATDATA_AUTHENTICATION_FAILED
    assert table_groups_response.status_code == 401
    assert table_groups_response.json()["status"] == "error"
    assert table_groups_response.json()["code"] == ErrorCode.CHATDATA_AUTHENTICATION_FAILED
    assert semantic_health_response.status_code == 401
    assert semantic_health_response.json()["status"] == "error"
    assert semantic_health_response.json()["code"] == ErrorCode.CHATDATA_AUTHENTICATION_FAILED


def test_column_dict_api_contract(client, fake_datasource_manager, monkeypatch, patch_datasource_manager, auth_headers):
    class FakeLlm:
        async def ainvoke(self, messages):
            assert "1 表示有效" in messages[0].content
            return SimpleNamespace(content='{"dict":{"enum_values":[{"value":"1","label":"有效"},{"value":"0","label":"无效"}],"synonyms":["订单状态"],"storage_format":"","unit":""}}')

    patch_datasource_manager(fake_datasource_manager)
    monkeypatch.setattr(column_dict_generate, "get_chat_data_llm", lambda **_kwargs: FakeLlm())

    public_get_response = client.get("/api/chatdata/datasources/ds_a/tables/orders/columns/status/dict", headers=auth_headers)
    admin_get_response = client.get("/api/chatdata/admin/datasources/ds_a/tables/orders/columns/status/dict", headers=auth_headers)
    public_table_response = client.get("/api/chatdata/datasources/ds_a/tables/orders/column-dicts", headers=auth_headers)
    admin_table_response = client.get("/api/chatdata/admin/datasources/ds_a/tables/orders/column-dicts", headers=auth_headers)
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

    assert public_get_response.status_code == 200
    assert public_get_response.json()["data"]["dict"]["enum_values"] == [{"value": "1", "label": "有效"}]
    assert admin_get_response.status_code == 200
    assert admin_get_response.json()["data"]["dict"]["enum_values"] == [{"value": "1", "label": "有效"}]
    assert public_table_response.status_code == 200
    assert public_table_response.json()["data"]["dicts"]["status"]["enum_values"] == [{"value": "1", "label": "有效"}]
    assert admin_table_response.status_code == 200
    assert admin_table_response.json()["data"]["dicts"]["status"]["enum_values"] == [{"value": "1", "label": "有效"}]
    assert save_response.status_code == 200
    assert save_response.json()["data"]["dict"]["enum_values"] == [{"value": "1", "label": "有效"}, {"value": "0", "label": "无效"}]
    assert fake_datasource_manager.replaced_column_dict_operator == "user_a"
    assert generate_response.status_code == 200
    assert generate_response.json()["data"]["dict"]["enum_values"] == [{"value": "1", "label": "有效"}, {"value": "0", "label": "无效"}]
    assert generate_response.json()["data"]["dict"]["synonyms"] == []


def test_column_dict_routes_require_auth(client, fake_datasource_manager, patch_datasource_manager):
    patch_datasource_manager(fake_datasource_manager)

    get_response = client.get("/api/chatdata/datasources/ds_a/tables/orders/columns/status/dict")
    admin_get_response = client.get("/api/chatdata/admin/datasources/ds_a/tables/orders/columns/status/dict")
    generate_response = client.post(
        "/api/chatdata/admin/datasources/ds_a/tables/orders/columns/status/dict/generate",
        json={"text": "订单状态：1 表示有效"},
    )

    assert get_response.status_code == 401
    assert get_response.json()["status"] == "error"
    assert get_response.json()["code"] == ErrorCode.CHATDATA_AUTHENTICATION_FAILED
    assert admin_get_response.status_code == 401
    assert admin_get_response.json()["code"] == ErrorCode.CHATDATA_AUTHENTICATION_FAILED
    assert generate_response.status_code == 401
    assert generate_response.json()["status"] == "error"
    assert generate_response.json()["code"] == ErrorCode.CHATDATA_AUTHENTICATION_FAILED


def test_public_column_dict_read_enforces_datasource_permission(client, fake_datasource_manager, patch_datasource_manager, auth_headers):
    patch_datasource_manager(fake_datasource_manager)

    async def deny_access(*_args, **_kwargs):
        return False

    fake_datasource_manager.can_access_datasource_async = deny_access

    response = client.get(
        "/api/chatdata/datasources/ds_a/tables/orders/columns/status/dict",
        headers=auth_headers,
    )
    table_response = client.get(
        "/api/chatdata/datasources/ds_a/tables/orders/column-dicts",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["code"] == ErrorCode.CHATDATA_PERMISSION_DENIED
    assert table_response.status_code == 200
    assert table_response.json()["code"] == ErrorCode.CHATDATA_PERMISSION_DENIED


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
