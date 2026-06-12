import os
import sys
from contextlib import nullcontext

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")

from apps.chat_data_app.services.datasource_manager import DataSourceManager


class _FakeStore:
    def __init__(self):
        self.datasources = {
            "ds_a": {
                "id": "ds_a",
                "group_id": "group_ds_a",
                "template_datasource_id": "phy_shared",
                "ds_name": "数据源A",
                "ds_description": "描述A",
                "source_type": "mysql",
                "query_executor_type": "direct",
                "source_config": {"host": "127.0.0.1", "port": 3306, "database": "db_a"},
                "ds_tables": [],
                "mapped_modules": [],
                "is_default": True,
            },
            "ds_b": {
                "id": "ds_b",
                "group_id": "group_ds_b",
                "template_datasource_id": "phy_shared",
                "ds_name": "数据源B",
                "ds_description": "描述B",
                "source_type": "mysql",
                "query_executor_type": "direct",
                "source_config": {"host": "127.0.0.1", "port": 3306, "database": "db_a"},
                "ds_tables": [],
                "mapped_modules": [],
                "is_default": False,
            },
        }
        self.semantic_configs = {"group_ds_a": [], "group_ds_b": []}
        self.group_member_count_map = {"phy_shared": 2}
        self.updated_groups = []
        self.soft_deleted_groups = []
        self.soft_deleted_datasources = []
        self.cleared_default_calls = []

    def get_all_datasources(self, include_tables: bool = True):
        result = []
        for item in self.datasources.values():
            next_item = dict(item)
            next_item["ds_tables"] = list(item.get("ds_tables") or []) if include_tables else []
            result.append(next_item)
        return result

    def has_datasource(self, datasource_id: str):
        return datasource_id in self.datasources

    def get_datasource(self, datasource_id: str):
        return self.datasources.get(datasource_id)

    def get_datasource_by_name(self, name: str):
        for item in self.datasources.values():
            if item["ds_name"] == name:
                return item
        return None

    def get_semantic_configs(self, datasource_id: str):
        datasource = self.get_datasource(datasource_id)
        if not datasource:
            return []
        return self.get_semantic_configs_by_group_id(datasource["group_id"])

    def get_semantic_config(self, datasource_id: str, config_id: str):
        for item in self.get_semantic_configs(datasource_id):
            if item["id"] == config_id:
                return item
        return None

    def get_semantic_configs_by_group_id(self, group_id: str):
        return list(self.semantic_configs.get(group_id, []))

    def clear_all_default_datasources(self, update_by: str = ""):
        self.cleared_default_calls.append(update_by)
        for item in self.datasources.values():
            item["is_default"] = False

    def update_datasource_group(self, group_id: str, **kwargs):
        self.updated_groups.append((group_id, kwargs))
        for item in self.datasources.values():
            if item["group_id"] == group_id:
                if "is_default" in kwargs:
                    item["is_default"] = bool(kwargs["is_default"])
                if "mapped_modules" in kwargs:
                    item["mapped_modules"] = kwargs["mapped_modules"]

    def get_default_datasource(self):
        for item in self.datasources.values():
            if item.get("is_default"):
                return item
        return None

    def get_group_member_count(self, datasource_id: str):
        return self.group_member_count_map.get(datasource_id, 0)

    def soft_delete_group(self, group_id: str):
        self.soft_deleted_groups.append(group_id)
        for datasource_id, item in list(self.datasources.items()):
            if item["group_id"] == group_id:
                del self.datasources[datasource_id]

    def soft_delete_datasource(self, datasource_id: str):
        self.soft_deleted_datasources.append(datasource_id)


def _build_manager(fake_store: _FakeStore) -> DataSourceManager:
    manager = DataSourceManager.__new__(DataSourceManager)
    manager.base_dir = None
    manager.db_store = fake_store
    return manager


class _SaveStore:
    def __init__(self):
        self.saved_kwargs = None

    def transaction(self):
        return nullcontext()

    def upsert_datasource_group(self, **kwargs):
        self.saved_kwargs = kwargs

    def update_datasource_group(self, **kwargs):
        return None

    def replace_datasource_tables(self, **kwargs):
        return None

    def get_datasource(self, datasource_id: str):
        return {
            "id": datasource_id,
            "group_id": datasource_id,
            "ds_name": "测试组",
            "ds_description": "",
            "source_type": "hive",
            "query_executor_type": "http",
            "source_config": {"host": "127.0.0.1", "port": 10000, "database": "dw"},
            "member_sources": [
                {
                    "member_datasource_id": "ns_a",
                    "datasource_name": "ns_a",
                    "namespace": "ns_a",
                    "source_type": "hive",
                    "host": "127.0.0.1",
                    "port": 10000,
                    "database": "dw",
                },
                {
                    "member_datasource_id": "ns_b",
                    "datasource_name": "ns_b",
                    "namespace": "ns_b",
                    "source_type": "hive",
                    "host": "127.0.0.1",
                    "port": 10000,
                    "database": "dw",
                },
            ],
            "ds_tables": [],
            "mapped_modules": [],
            "is_default": False,
            "create_by": "",
            "update_by": "",
        }

    def get_semantic_configs_by_group_id(self, group_id: str):
        return []


def test_set_default_datasource_updates_group_flag():
    fake_store = _FakeStore()
    manager = _build_manager(fake_store)

    assert manager.set_default_datasource("ds_b") is True
    assert fake_store.datasources["ds_b"]["is_default"] is True
    assert fake_store.datasources["ds_a"]["is_default"] is False
    assert fake_store.updated_groups[-1][0] == "group_ds_b"


def test_clear_default_datasource_updates_group_flag():
    fake_store = _FakeStore()
    manager = _build_manager(fake_store)

    assert manager.clear_default_datasource("ds_a") is True
    assert fake_store.datasources["ds_a"]["is_default"] is False
    assert fake_store.updated_groups[-1][0] == "group_ds_a"


def test_get_all_datasources_does_not_attach_semantic_configs():
    fake_store = _FakeStore()
    manager = _build_manager(fake_store)

    datasources = manager.get_all_datasources()

    assert len(datasources) == 2
    assert all("semantic_configs" not in item for item in datasources)


def test_get_all_datasources_supports_lightweight_mode():
    fake_store = _FakeStore()
    manager = _build_manager(fake_store)

    datasources = manager.get_all_datasources(include_tables=False)

    assert len(datasources) == 2
    assert all(item["ds_tables"] == [] for item in datasources)


def test_has_datasource_delegates_to_store():
    fake_store = _FakeStore()
    manager = _build_manager(fake_store)

    assert manager.has_datasource("ds_a") is True
    assert manager.has_datasource("missing") is False


def test_delete_datasource_only_soft_deletes_group_when_member_source_is_shared():
    fake_store = _FakeStore()
    manager = _build_manager(fake_store)

    assert manager.delete_datasource("ds_a") is True
    assert fake_store.soft_deleted_groups == ["group_ds_a"]
    assert fake_store.soft_deleted_datasources == []
    assert "ds_b" in fake_store.datasources


def test_get_execution_datasources_injects_unified_mysql_credentials(monkeypatch):
    fake_store = _FakeStore()
    fake_store.datasources["ds_a"]["member_sources"] = [
        {
            "member_datasource_id": "phy_a",
            "datasource_name": "成员A",
            "source_type": "mysql",
            "host": "127.0.0.1",
            "port": 3306,
            "database": "db_a",
        }
    ]
    manager = _build_manager(fake_store)
    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.resolve_mysql_credentials",
        lambda _config: {"user": "db_query_ro", "password": "secret"},
    )

    execution_targets = manager.get_execution_datasources("ds_a")

    assert len(execution_targets) == 1
    assert execution_targets[0]["query_executor_type"] == "direct"
    assert execution_targets[0]["source_config"]["user"] == "db_query_ro"
    assert execution_targets[0]["source_config"]["password"] == "secret"


def test_get_execution_datasources_keeps_hive_http_executor_type(monkeypatch):
    fake_store = _FakeStore()
    fake_store.datasources["ds_a"]["source_type"] = "hive"
    fake_store.datasources["ds_a"]["query_executor_type"] = "http"
    fake_store.datasources["ds_a"]["member_sources"] = [
        {
            "member_datasource_id": "phy_hive",
            "datasource_name": "hive-localhost",
            "namespace": "hive-localhost",
            "source_type": "hive",
            "host": "127.0.0.2",
            "port": 10000,
            "database": "dw_a",
        }
    ]
    manager = _build_manager(fake_store)
    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.resolve_hive_credentials",
        lambda _config: {"username": "hive_user"},
    )

    execution_targets = manager.get_execution_datasources("ds_a")

    assert len(execution_targets) == 1
    assert execution_targets[0]["query_executor_type"] == "http"
    assert execution_targets[0]["member_name"] == "hive-localhost"
    assert execution_targets[0]["source_config"]["namespace"] == "hive-localhost"
    assert "username" not in execution_targets[0]["source_config"]


def test_get_execution_datasources_keeps_mysql_http_executor_type(monkeypatch):
    fake_store = _FakeStore()
    fake_store.datasources["ds_a"]["source_type"] = "mysql"
    fake_store.datasources["ds_a"]["query_executor_type"] = "http"
    fake_store.datasources["ds_a"]["member_sources"] = [
        {
            "member_datasource_id": "mysql-http-a",
            "datasource_name": "mysql-http-a",
            "source_type": "mysql",
            "query_executor_type": "http",
            "host": "127.0.0.2",
            "port": 3306,
            "database": "db_http",
        }
    ]
    manager = _build_manager(fake_store)
    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.resolve_mysql_credentials",
        lambda _config: {"user": "db_query_ro", "password": "secret"},
    )

    execution_targets = manager.get_execution_datasources("ds_a")

    assert len(execution_targets) == 1
    assert execution_targets[0]["query_executor_type"] == "http"
    assert execution_targets[0]["member_name"] == "mysql-http-a"
    assert execution_targets[0]["source_config"]["namespace"] == "mysql-http-a"
    assert "user" not in execution_targets[0]["source_config"]


def test_save_datasource_rejects_duplicate_member_connections():
    manager = DataSourceManager.__new__(DataSourceManager)
    manager.base_dir = None
    manager.db_store = None

    with pytest.raises(ValueError, match="第 2 个数据源连接重复"):
        manager._save_datasource(
            datasource_id="ds_test",
            group_name="测试组",
            group_desc="",
            source_type="mysql",
            query_executor_type="direct",
            template_source="127.0.0.1:3306:test_db",
            member_source_configs=[
                {
                    "datasource_name": "模板库",
                    "host": "127.0.0.1",
                    "port": 3306,
                    "database": "test_db",
                },
                {
                    "datasource_name": "重复库",
                    "host": "127.0.0.1",
                    "port": 3306,
                    "database": "test_db",
                },
            ],
            tables=[],
            mapped_modules=[],
            is_default=False,
            create_by="",
            update_by="",
        )


def test_save_datasource_rejects_missing_member_name():
    manager = DataSourceManager.__new__(DataSourceManager)
    manager.base_dir = None
    manager.db_store = None

    with pytest.raises(ValueError, match="数据源名称不能为空"):
        manager._save_datasource(
            datasource_id="ds_test",
            group_name="测试组",
            group_desc="",
            source_type="mysql",
            query_executor_type="direct",
            template_source="127.0.0.1:3306:test_db",
            member_source_configs=[
                {
                    "host": "127.0.0.1",
                    "port": 3306,
                    "database": "test_db",
                },
            ],
            tables=[],
            mapped_modules=[],
            is_default=False,
            create_by="",
            update_by="",
        )


def test_save_datasource_keeps_hive_http_members_with_shared_connection():
    fake_store = _SaveStore()
    manager = _build_manager(fake_store)

    saved = manager._save_datasource(
        datasource_id="ds_hive_http",
        group_name="HiveHTTP组",
        group_desc="",
        source_type="hive",
        query_executor_type="http",
        template_source="127.0.0.1:10000:dw",
        member_source_configs=[
            {
                "datasource_name": "ns_a",
                "host": "127.0.0.1",
                "port": 10000,
                "database": "dw",
            },
            {
                "datasource_name": "ns_b",
                "host": "127.0.0.1",
                "port": 10000,
                "database": "dw",
            },
        ],
        tables=[],
        mapped_modules=[],
        is_default=False,
        create_by="",
        update_by="",
    )

    assert fake_store.saved_kwargs is not None
    assert fake_store.saved_kwargs["member_sources_text"] == "ns_a|http|127.0.0.1:10000:dw\nns_b|http|127.0.0.1:10000:dw"
    assert [item["member_datasource_id"] for item in saved["member_sources"]] == ["ns_a", "ns_b"]


def test_save_datasource_keeps_mysql_http_members_with_shared_connection():
    fake_store = _SaveStore()
    manager = _build_manager(fake_store)

    saved = manager._save_datasource(
        datasource_id="ds_mysql_http",
        group_name="MySQLHTTP组",
        group_desc="",
        source_type="mysql",
        query_executor_type="http",
        template_source="127.0.0.1:3306:test_db",
        member_source_configs=[
            {
                "datasource_name": "mysql_http_a",
                "host": "127.0.0.1",
                "port": 3306,
                "database": "test_db",
            },
            {
                "datasource_name": "mysql_http_b",
                "host": "127.0.0.1",
                "port": 3306,
                "database": "test_db",
            },
        ],
        tables=[],
        mapped_modules=[],
        is_default=False,
        create_by="",
        update_by="",
    )

    assert fake_store.saved_kwargs is not None
    assert fake_store.saved_kwargs["member_sources_text"] == "mysql_http_a|http|127.0.0.1:3306:test_db\nmysql_http_b|http|127.0.0.1:3306:test_db"


def test_save_datasource_rejects_duplicate_hive_http_names():
    manager = DataSourceManager.__new__(DataSourceManager)
    manager.base_dir = None
    manager.db_store = None

    with pytest.raises(ValueError, match="第 2 个数据源名称重复"):
        manager._save_datasource(
            datasource_id="ds_hive_http",
            group_name="HiveHTTP组",
            group_desc="",
            source_type="hive",
            query_executor_type="http",
            template_source="127.0.0.1:10000:dw",
            member_source_configs=[
                {
                    "datasource_name": "ns_a",
                    "host": "127.0.0.1",
                    "port": 10000,
                    "database": "dw",
                },
                {
                    "datasource_name": "ns_a",
                    "host": "127.0.0.2",
                    "port": 10000,
                    "database": "dw_b",
                },
            ],
            tables=[],
            mapped_modules=[],
            is_default=False,
            create_by="",
            update_by="",
        )


def test_get_execution_datasources_supports_hive_http_namespace_selection(monkeypatch):
    fake_store = _FakeStore()
    fake_store.datasources["ds_a"]["source_type"] = "hive"
    fake_store.datasources["ds_a"]["query_executor_type"] = "http"
    fake_store.datasources["ds_a"]["template_datasource_id"] = "ns_a"
    fake_store.datasources["ds_a"]["member_sources"] = [
        {
            "member_datasource_id": "ns_a",
            "datasource_name": "ns_a",
            "namespace": "ns_a",
            "source_type": "hive",
            "host": "127.0.0.1",
            "port": 10000,
            "database": "dw",
        },
        {
            "member_datasource_id": "ns_b",
            "datasource_name": "ns_b",
            "namespace": "ns_b",
            "source_type": "hive",
            "host": "127.0.0.1",
            "port": 10000,
            "database": "dw",
        },
    ]
    manager = _build_manager(fake_store)
    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.resolve_hive_credentials",
        lambda _config: {"username": "hive_user"},
    )

    execution_targets = manager.get_execution_datasources("ds_a", ["ns_b"])

    assert len(execution_targets) == 1
    assert execution_targets[0]["member_datasource_id"] == "ns_b"
    assert execution_targets[0]["member_name"] == "ns_b"
    assert execution_targets[0]["source_config"]["namespace"] == "ns_b"


def test_update_datasource_preserves_template_member_name_when_refreshing_tables():
    fake_store = _SaveStore()
    manager = _build_manager(fake_store)

    manager.update_datasource(
        "ds_refresh",
        tables=[{"table_name": "orders", "description": "orders", "columns": []}],
        source_config={"host": "127.0.0.1", "port": 10000, "database": "dw"},
    )

    assert fake_store.saved_kwargs["template_source"] == "ns_a|http|127.0.0.1:10000:dw"
    assert fake_store.saved_kwargs["member_sources_text"].splitlines()[0] == "ns_a|http|127.0.0.1:10000:dw"
