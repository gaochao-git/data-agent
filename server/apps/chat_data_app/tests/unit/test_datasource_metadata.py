from __future__ import annotations

import pytest

from apps.chat_data_app.services.datasource import metadata as datasource_metadata
from apps.chat_data_app.services.datasource.metadata import (
    DataSourceMetadataAdapter,
    DataSourceMetadataAdapterRegistry,
    HiveDirectMetadataAdapter,
    HiveHttpMetadataAdapter,
    MetadataAdapterNotFoundError,
    MySQLMetadataAdapter,
    get_metadata_adapter_registry,
)


class DemoMetadataAdapter(DataSourceMetadataAdapter):
    async def list_tables(self):
        return ["demo"]

    async def load_schema(self, table_names=None):
        return [{"table_name": name} for name in table_names or ["demo"]]


def test_metadata_adapter_registry_controls_supported_implementations():
    registry = DataSourceMetadataAdapterRegistry()
    registry.register("demo", "direct", DemoMetadataAdapter)

    adapter = registry.create(" DEMO ", " DIRECT ", {"database": "db"})

    assert isinstance(adapter, DemoMetadataAdapter)
    assert registry.supported_keys() == [("demo", "direct")]
    with pytest.raises(ValueError, match="已注册"):
        registry.register("demo", "direct", DemoMetadataAdapter)
    with pytest.raises(TypeError, match="必须继承"):
        registry.register("bad", "direct", object)
    with pytest.raises(MetadataAdapterNotFoundError, match="oracle/direct"):
        registry.create("oracle", "direct", {})


def test_default_metadata_adapter_registry_contains_current_backends():
    assert get_metadata_adapter_registry().supported_keys() == [
        ("hive", "direct"),
        ("hive", "http"),
        ("mysql", "direct"),
    ]


@pytest.mark.asyncio
async def test_mysql_metadata_adapter_delegates_with_resolved_credentials(monkeypatch):
    captured = {}

    class FakeSyncService:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        async def get_tables_async(self):
            return ["orders"]

        async def get_full_schema_async(self, table_names):
            captured["table_names"] = table_names
            return [{"table_name": "orders"}]

    monkeypatch.setattr(
        datasource_metadata, "resolve_mysql_credentials", lambda _config: {"user": "u", "password": "p"}
    )
    monkeypatch.setattr(datasource_metadata, "MySQLSyncService", FakeSyncService)

    adapter = MySQLMetadataAdapter({"host": "db", "port": 3307, "database": "demo"})

    assert await adapter.list_tables() == ["orders"]
    assert await adapter.load_schema(["orders"]) == [{"table_name": "orders"}]
    assert captured["init"] == {
        "host": "db",
        "port": 3307,
        "database": "demo",
        "user": "u",
        "password": "p",
    }
    assert captured["table_names"] == ["orders"]


@pytest.mark.asyncio
async def test_hive_direct_metadata_adapter_delegates_with_resolved_credentials(monkeypatch):
    captured = {}

    class FakeSyncService:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        async def get_tables_async(self):
            return ["orders"]

        async def get_full_schema_async(self, table_names):
            return [{"table_name": table_names[0]}]

    monkeypatch.setattr(
        datasource_metadata, "resolve_hive_credentials", lambda _config: {"username": "u", "password": "p"}
    )
    monkeypatch.setattr(datasource_metadata, "HiveSyncService", FakeSyncService)

    adapter = HiveDirectMetadataAdapter({"host": "hive", "port": 10001, "database": "dw"})

    assert await adapter.list_tables() == ["orders"]
    assert await adapter.load_schema(["orders"]) == [{"table_name": "orders"}]
    assert captured["init"] == {
        "host": "hive",
        "port": 10001,
        "database": "dw",
        "username": "u",
        "password": "p",
    }


@pytest.mark.asyncio
async def test_hive_http_metadata_adapter_forwards_identity(monkeypatch):
    calls = []

    async def fake_tables(source_config, user_id=None, username=None):
        calls.append(("tables", source_config, user_id, username))
        return ["orders"]

    async def fake_schema(source_config, table_names, user_id=None, username=None):
        calls.append(("schema", source_config, table_names, user_id, username))
        return [{"table_name": "orders"}]

    monkeypatch.setattr(datasource_metadata, "get_hive_meta_tables_async", fake_tables)
    monkeypatch.setattr(datasource_metadata, "get_hive_meta_schema_async", fake_schema)
    source_config = {"org_code": "ORG_A", "database": "dw"}
    adapter = HiveHttpMetadataAdapter(source_config, user_id="u1", username="用户一")

    assert await adapter.list_tables() == ["orders"]
    assert await adapter.load_schema(["orders"]) == [{"table_name": "orders"}]
    assert calls == [
        ("tables", source_config, "u1", "用户一"),
        ("schema", source_config, ["orders"], "u1", "用户一"),
    ]
