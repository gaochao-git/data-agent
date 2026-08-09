import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[4]))

from apps.chat_data_app.services import semantic_materializer


class _FakeRuntime:
    backend_name = "local"

    def __init__(self):
        self.entries = []

    async def write_internal_text_files(self, entries):
        self.entries.extend(entries)
        return [{"file_path": path, "bytes": len(content.encode("utf-8"))} for path, content in entries]


class _FakeDatasourceManager:
    def __init__(self, configs):
        self.configs = configs

    async def get_runtime_config_async(self, datasource_id, config_id, user_id=""):
        assert datasource_id == "ds_1"
        assert user_id == "user_1"
        return next((config for config in self.configs if config["id"] == config_id), None)


def _config(config_id="sc_order", enabled=True):
    return {
        "id": config_id,
        "name": "订单模型",
        "description": "订单分析",
        "enabled": enabled,
        "metrics": [{"name": "销售额"}],
        "dimensions": [{"name": "城市"}],
    }


def _materialize(runtime):
    return asyncio.run(semantic_materializer.materialize_runtime_semantic_models(
        workspace_runtime=runtime,
        datasource_id="ds_1",
        user_id="user_1",
        source_type="mysql",
        semantic_config_ids=["sc_order", "sc_disabled"],
    ))


def test_materializer_overwrites_selected_models_and_reuses_conversion_cache(monkeypatch):
    semantic_materializer._YAML_CACHE.clear()
    configs = [_config(), _config("sc_disabled", enabled=False)]
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager(configs),
    )
    calls = []

    def fake_convert(self, config, *, source_type=""):
        calls.append((config["id"], source_type))
        return f"semantic_model: {config['id']}\n"

    monkeypatch.setattr(semantic_materializer.ChatDataToOssieConverter, "convert", fake_convert)
    first_runtime = _FakeRuntime()
    second_runtime = _FakeRuntime()

    first = _materialize(first_runtime)
    second = _materialize(second_runtime)

    assert len(first["directory"]) == 1
    assert first["directory"][0]["metrics_total"] == 1
    assert first["directory"][0]["dimensions_total"] == 1
    assert first["directory"][0]["path"] == first["paths"]["sc_order"]
    assert first_runtime.entries[0][0] == "workspace/semantic/sc_order.yaml"
    assert second_runtime.entries[0][1] == first_runtime.entries[0][1]
    assert calls == [("sc_order", "mysql")]


def test_materializer_cache_key_includes_config_content(monkeypatch):
    semantic_materializer._YAML_CACHE.clear()
    configs = [_config()]
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager(configs),
    )
    calls = []

    def fake_convert(self, config, *, source_type=""):
        calls.append(config["description"])
        return config["description"]

    monkeypatch.setattr(semantic_materializer.ChatDataToOssieConverter, "convert", fake_convert)
    _materialize(_FakeRuntime())
    configs[0]["description"] = "订单模型（已更新）"
    runtime = _FakeRuntime()
    semantic_materializer._YAML_CACHE.clear()
    _materialize(runtime)

    assert calls == ["订单分析", "订单模型（已更新）"]


def test_materializer_cache_key_includes_runtime_table_indexes(monkeypatch):
    semantic_materializer._YAML_CACHE.clear()
    config = _config()
    config["tables"] = [{
        "table_name": "orders",
        "primary_keys": [],
        "unique_indexes": [],
        "manual_indexes": [],
    }]
    configs = [config]
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager(configs),
    )
    calls = []

    def fake_convert(self, current, *, source_type=""):
        calls.append(current["tables"][0]["manual_indexes"])
        return str(current["tables"][0]["manual_indexes"])

    monkeypatch.setattr(semantic_materializer.ChatDataToOssieConverter, "convert", fake_convert)
    _materialize(_FakeRuntime())
    config["tables"][0]["manual_indexes"] = [{
        "index_type": "unique",
        "index_name": "uniq_order_id",
        "columns": ["order_id"],
    }]
    runtime = _FakeRuntime()
    _materialize(runtime)

    assert len(calls) == 2
    assert "uniq_order_id" in runtime.entries[0][1]


def test_materializer_returns_empty_catalog_without_writing(monkeypatch):
    semantic_materializer._YAML_CACHE.clear()
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager([]),
    )
    runtime = _FakeRuntime()

    result = asyncio.run(semantic_materializer.materialize_runtime_semantic_models(
        workspace_runtime=runtime,
        datasource_id="ds_1",
        user_id="user_1",
        source_type="mysql",
        semantic_config_ids=[],
    ))

    assert result["directory"] == []
    assert result["paths"] == {}
    assert result["configs"] == {}
    assert runtime.entries == []


def test_materializer_only_overwrites_selected_models(monkeypatch):
    semantic_materializer._YAML_CACHE.clear()
    configs = [_config("sc_old"), _config("sc_keep")]
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager(configs),
    )

    def fake_convert(self, config, *, source_type=""):
        return f"semantic_model: {config['id']}\n"

    monkeypatch.setattr(semantic_materializer.ChatDataToOssieConverter, "convert", fake_convert)
    runtime = _FakeRuntime()
    result = asyncio.run(semantic_materializer.materialize_runtime_semantic_models(
        workspace_runtime=runtime,
        datasource_id="ds_1",
        user_id="user_1",
        source_type="mysql",
        semantic_config_ids=["sc_keep"],
    ))

    assert [path for path, _ in runtime.entries] == ["workspace/semantic/sc_keep.yaml"]
    assert result["paths"] == {"sc_keep": "/mnt/user-data/workspace/semantic/sc_keep.yaml"}
