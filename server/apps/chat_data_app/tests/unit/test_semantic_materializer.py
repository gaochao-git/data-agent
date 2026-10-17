import asyncio
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parents[4]))

from apps.chat_data_app.services.semantic.run import semantic_materializer


class _FakeRuntime:
    backend_name = "local"

    def __init__(self):
        self.entries = []
        self.files: set[str] = set()
        self.deleted: list[str] = []

    async def write_internal_text_files(self, entries):
        self.entries.extend(entries)
        for path, _ in entries:
            self.files.add(path)
        return [{"file_path": path, "bytes": len(content.encode("utf-8"))} for path, content in entries]

    async def list_dir(self, *, path, max_depth):
        children = sorted(f for f in self.files if f.startswith(f"{path}/"))
        if not children:
            raise FileNotFoundError(f"目录不存在: {path}")
        return [{"type": "file", "path": f} for f in children]

    async def delete_internal_text_files(self, *, path):
        self.deleted.append(path)
        self.files.discard(path)
        return {"path": path, "type": "file"}


class _FakeDatasourceManager:
    def __init__(self, configs):
        self.configs = configs
        self.column_dicts = {}
        self.column_dict_calls = 0
        self.bundle_calls = 0
        self.single_loads = 0

    async def get_column_dicts_async(self, datasource_id):
        self.column_dict_calls += 1
        return self.column_dicts

    async def get_runtime_config_async(self, datasource_id, config_id, user_id="", *, column_dicts=None):
        self.single_loads += 1
        assert datasource_id == "ds_1"
        assert user_id == "user_1"
        assert column_dicts is not None, "materializer 必须预取字段字典并下发，禁止逐模型重复拉取"
        return next((config for config in self.configs if config["id"] == config_id), None)

    async def get_runtime_configs_async(self, datasource_id, user_id=""):
        assert datasource_id == "ds_1"
        return self.configs

    async def get_runtime_bundle_async(self, datasource_id, user_id=""):
        """批量入口：一次返回全量配置与字段字典，禁止批量链路再逐模型回源。"""
        self.bundle_calls += 1
        assert datasource_id == "ds_1"
        return self.configs, await self.get_column_dicts_async(datasource_id)


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
    return asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
            semantic_config_ids=["sc_order", "sc_disabled"],
        )
    )


def test_materializer_overwrites_selected_models_and_reuses_conversion_cache(monkeypatch):
    semantic_materializer._YAML_CACHE.clear()
    configs = [_config(), _config("sc_disabled", enabled=False)]
    manager = _FakeDatasourceManager(configs)
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: manager,
    )
    calls = []

    def fake_convert(self, config, *, source_type=""):
        calls.append((config["id"], source_type))
        return f"semantic_model: {config['id']}\n"

    monkeypatch.setattr(semantic_materializer.ChatDataToOssieConverter, "convert", fake_convert)
    first_runtime = _FakeRuntime()
    second_runtime = _FakeRuntime()

    first = _materialize(first_runtime)
    _materialize(second_runtime)

    assert len(first["directory"]) == 1
    assert first["directory"][0]["metrics_total"] == 1
    assert first["directory"][0]["dimensions_total"] == 1
    assert first["directory"][0]["path"] == first["paths"]["sc_order"]
    assert first_runtime.entries[0][0] == "/mnt/user-data/workspace/semantic/订单模型__order.yaml"
    assert second_runtime.entries[0][1] == first_runtime.entries[0][1]
    assert calls == [("sc_order", "mysql")]
    # 每次 materialize（含 disabled 模型）只预取一次字段字典，两次调用共 2 次
    assert manager.column_dict_calls == 2


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
    config["tables"] = [
        {
            "table_name": "orders",
            "primary_keys": [],
            "unique_indexes": [],
            "manual_indexes": [],
        }
    ]
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
    config["tables"][0]["manual_indexes"] = [
        {
            "index_type": "unique",
            "index_name": "uniq_order_id",
            "columns": ["order_id"],
        }
    ]
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

    result = asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
            semantic_config_ids=[],
        )
    )

    assert result["directory"] == []
    assert result["paths"] == {}
    assert result["configs"] == {}
    assert runtime.entries == []
    assert runtime.deleted == []


def test_materializer_clears_stale_catalogs_when_scope_empty():
    """空启用集 + prune_stale：上一轮残留的本体/语义目录必须被清掉。"""
    runtime = _FakeRuntime()
    stale_paths = {
        f"{semantic_materializer.SEMANTIC_SNAPSHOT_DIR}/_ontology_catalog.yaml",
        f"{semantic_materializer.SEMANTIC_SNAPSHOT_DIR}/_semantic_catalog.yaml",
        f"{semantic_materializer.SEMANTIC_SNAPSHOT_DIR}/订单模型__a.yaml",
    }
    runtime.files.update(stale_paths)

    result = asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
            semantic_config_ids=[],
            prune_stale=True,
        )
    )

    assert result == {"directory": [], "paths": {}, "configs": {}, "failed_ids": []}
    assert runtime.files == set()
    assert set(runtime.deleted) == stale_paths


def test_prematerialize_empty_enabled_models_clears_stale_catalogs(monkeypatch):
    """数据源组没有启用模型时预物化也要清目录，不能把上一轮目录留给后续 run。"""
    semantic_materializer._YAML_CACHE.clear()
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager([]),
    )
    runtime = _FakeRuntime()
    stale_paths = {
        f"{semantic_materializer.SEMANTIC_SNAPSHOT_DIR}/_ontology_catalog.yaml",
        f"{semantic_materializer.SEMANTIC_SNAPSHOT_DIR}/_semantic_catalog.yaml",
    }
    runtime.files.update(stale_paths)

    result = asyncio.run(
        semantic_materializer.prematerialize_enabled_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
        )
    )

    assert result == {"total": 0, "failed_ids": []}
    assert runtime.entries == []
    assert runtime.files == set()
    assert set(runtime.deleted) == stale_paths


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
    result = asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
            semantic_config_ids=["sc_keep"],
        )
    )

    assert [path for path, _ in runtime.entries] == ["/mnt/user-data/workspace/semantic/订单模型__keep.yaml"]
    assert result["paths"] == {"sc_keep": "/mnt/user-data/workspace/semantic/订单模型__keep.yaml"}


def test_prematerialize_skips_without_datasource_or_runtime():
    assert asyncio.run(
        semantic_materializer.prematerialize_enabled_semantic_models(
            workspace_runtime=_FakeRuntime(),
            datasource_id=None,
            user_id="user_1",
        )
    ) == {"total": 0, "failed_ids": []}
    assert asyncio.run(
        semantic_materializer.prematerialize_enabled_semantic_models(
            workspace_runtime=None,
            datasource_id="ds_1",
            user_id="user_1",
        )
    ) == {"total": 0, "failed_ids": []}


def test_materializer_reports_failed_ids_instead_of_silently_skipping(monkeypatch):
    """加载或转换失败的模型必须出现在 failed_ids 里，不能只静默跳过。"""
    semantic_materializer._YAML_CACHE.clear()
    configs = [_config("sc_ok"), _config("sc_load_fail"), _config("sc_render_fail")]
    original_load = _FakeDatasourceManager.get_runtime_config_async

    async def failing_load(self, datasource_id, config_id, user_id="", *, column_dicts=None):
        if config_id == "sc_load_fail":
            raise RuntimeError("加载炸了")
        return await original_load(self, datasource_id, config_id, user_id, column_dicts=column_dicts)

    def failing_convert(self, config, *, source_type=""):
        if config["id"] == "sc_render_fail":
            raise RuntimeError(f"转换炸了: {config['id']}")
        return f"semantic_model: {config['id']}\n"

    monkeypatch.setattr(_FakeDatasourceManager, "get_runtime_config_async", failing_load)
    monkeypatch.setattr(semantic_materializer, "get_semantic_config_service", lambda: _FakeDatasourceManager(configs))
    monkeypatch.setattr(semantic_materializer.ChatDataToOssieConverter, "convert", failing_convert)

    result = asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=_FakeRuntime(),
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
            semantic_config_ids=["sc_ok", "sc_load_fail", "sc_render_fail"],
        )
    )

    assert result["failed_ids"] == ["sc_load_fail", "sc_render_fail"]
    assert list(result["paths"]) == ["sc_ok"]


def test_materializer_returns_empty_failed_ids_for_empty_scope():
    result = asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=_FakeRuntime(),
            datasource_id="ds_1",
            user_id="user_1",
            semantic_config_ids=[],
        )
    )

    assert result["failed_ids"] == []


def test_prematerialize_writes_all_enabled_models(monkeypatch):
    semantic_materializer._YAML_CACHE.clear()
    configs = [_config("sc_a"), _config("sc_b"), _config("sc_off", enabled=False)]
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager(configs),
    )
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter,
        "convert",
        lambda self, config, *, source_type="": f"semantic_model: {config['id']}\n",
    )
    runtime = _FakeRuntime()

    result = asyncio.run(
        semantic_materializer.prematerialize_enabled_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
        )
    )

    assert result == {"total": 2, "failed_ids": []}
    assert [path for path, _ in runtime.entries] == [
        "/mnt/user-data/workspace/semantic/订单模型__a.yaml",
        "/mnt/user-data/workspace/semantic/订单模型__b.yaml",
        "/mnt/user-data/workspace/semantic/_semantic_catalog.yaml",
    ]
    assert runtime.deleted == []


def test_prematerialize_loads_once_instead_of_per_model(monkeypatch):
    """预物化必须 O(1) 次查库：复用批量加载结果，禁止按 ID 逐个重载。

    逐个重载会让每个模型都重新拉一遍数据源全量表结构，模型数上百时是主要瓶颈。
    """
    semantic_materializer._YAML_CACHE.clear()
    configs = [_config(f"sc_{index}") for index in range(20)]
    manager = _FakeDatasourceManager(configs)
    monkeypatch.setattr(semantic_materializer, "get_semantic_config_service", lambda: manager)
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter,
        "convert",
        lambda self, config, *, source_type="": f"semantic_model: {config['id']}\n",
    )
    runtime = _FakeRuntime()

    result = asyncio.run(
        semantic_materializer.prematerialize_enabled_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
        )
    )

    assert result == {"total": 20, "failed_ids": []}
    assert manager.bundle_calls == 1
    assert manager.single_loads == 0, "禁止按模型 ID 逐个回源加载（N+1）"
    assert manager.column_dict_calls == 1
    assert len(runtime.entries) == 21  # 20 个模型快照 + 1 份语义资产目录


def test_materializer_reuses_caller_supplied_configs(monkeypatch):
    """传入 configs/column_dicts 时完全不走服务层，避免重复查库。"""
    semantic_materializer._YAML_CACHE.clear()
    configs = [_config("sc_a", enabled=True), _config("sc_off", enabled=False)]

    class _NoService:
        async def get_column_dicts_async(self, datasource_id):
            raise AssertionError("已提供 column_dicts，不应再查库")

        async def get_runtime_config_async(self, *args, **kwargs):
            raise AssertionError("已提供 configs，不应按 ID 回源")

    monkeypatch.setattr(semantic_materializer, "get_semantic_config_service", _NoService)
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter,
        "convert",
        lambda self, config, *, source_type="": f"semantic_model: {config['id']}\n",
    )
    runtime = _FakeRuntime()

    result = asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
            semantic_config_ids=["sc_a", "sc_off", "sc_missing"],
            configs=configs,
            column_dicts={},
        )
    )

    assert list(result["paths"]) == ["sc_a"]
    assert result["failed_ids"] == []
    assert [path for path, _ in runtime.entries] == ["/mnt/user-data/workspace/semantic/订单模型__a.yaml"]


def test_cache_key_uses_update_time_and_batch_salt(monkeypatch):
    """库表模型走轻量键：update_time 变化、字段字典变化、表结构变化都要失效。"""
    semantic_materializer._YAML_CACHE.clear()

    def _db_config(**overrides):
        config = _config()
        config.update({"group_id": "g_1", "update_time": "2026-09-24 10:00:00"})
        config.update(overrides)
        return config

    config = _db_config()
    monkeypatch.setattr(semantic_materializer, "get_semantic_config_service", lambda: _FakeDatasourceManager([config]))
    calls = []
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter,
        "convert",
        lambda self, current, *, source_type="": calls.append(current["id"]) or "yaml",
    )
    _materialize(_FakeRuntime())
    # update_time 未变 → 命中缓存，即使 description 变了也不重转
    config["description"] = "改了描述"
    _materialize(_FakeRuntime())
    assert len(calls) == 1

    config["update_time"] = "2026-09-24 11:00:00"
    _materialize(_FakeRuntime())
    assert len(calls) == 2, "update_time 变化必须让缓存失效"

    shared_tables = [{"table_name": "orders", "manual_indexes": []}]
    config["tables"] = shared_tables
    semantic_materializer._YAML_CACHE.clear()
    calls.clear()
    _materialize(_FakeRuntime())
    # 表结构原地变更（数据源改索引不更新模型 update_time）也必须失效
    shared_tables[0]["manual_indexes"] = [{"index_name": "uniq_order_id"}]
    _materialize(_FakeRuntime())
    assert len(calls) == 2


def test_batch_cache_salt_deduplicates_shared_tables():
    """共享同一 tables 对象的多个模型只应参与一次盐计算（整批只算一次哈希）。"""
    tables = [{"table_name": "orders"}]
    configs = [dict(_config(f"sc_{index}"), tables=tables) for index in range(10)]
    shared = semantic_materializer._batch_cache_salt(configs, {})
    single = semantic_materializer._batch_cache_salt([dict(_config("sc_0"), tables=tables)], {})
    assert shared == single
    other = semantic_materializer._batch_cache_salt([dict(_config("sc_0"), tables=[{"table_name": "users"}])], {})
    assert other != shared
    # 字段字典进盐：字典变更要让整批缓存失效
    assert semantic_materializer._batch_cache_salt(configs, {("orders", "city"): {"unit": "个"}}) != shared


def test_normalize_loaded_config_backfills_id_and_joins_and_drops_disabled():
    normalize = semantic_materializer._normalize_loaded_config
    assert normalize(None, "sc_fallback") is None
    assert normalize({"enabled": False}, "sc_fallback") is None
    # 缺 id 用请求 id 兜底；relationships 兼容映射成 joins
    assert normalize({"enabled": True, "relationships": [{"from": "a"}]}, "sc_fallback") == {
        "enabled": True,
        "relationships": [{"from": "a"}],
        "joins": [{"from": "a"}],
        "id": "sc_fallback",
    }
    assert normalize(
        {"enabled": True, "id": "sc_own", "joins": [{"from": "b"}], "relationships": [{"from": "a"}]}, "x"
    ) == {
        "enabled": True,
        "id": "sc_own",
        "joins": [{"from": "b"}],
        "relationships": [{"from": "a"}],
    }


def test_load_selected_semantic_config_requires_datasource_and_id():
    assert asyncio.run(semantic_materializer._load_selected_semantic_config(None, "sc_1", "user_1")) is None
    assert asyncio.run(semantic_materializer._load_selected_semantic_config("ds_1", "  ", "user_1")) is None


def test_cache_eviction_respects_batch_size(monkeypatch):
    """模型数超过默认上限时按批扩容，避免整批互相淘汰导致每轮全量重转。"""
    semantic_materializer._YAML_CACHE.clear()
    monkeypatch.setattr(semantic_materializer, "_CACHE_LIMIT", 2)
    configs = [_config(f"sc_{index}") for index in range(5)]
    monkeypatch.setattr(semantic_materializer, "get_semantic_config_service", lambda: _FakeDatasourceManager(configs))
    calls = []
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter,
        "convert",
        lambda self, config, *, source_type="": calls.append(config["id"]) or "yaml",
    )

    asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=_FakeRuntime(),
            datasource_id="ds_1",
            user_id="user_1",
            semantic_config_ids=[config["id"] for config in configs],
        )
    )
    assert len(calls) == 5
    # 容量按批扩到 5，第二轮全部命中
    asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=_FakeRuntime(),
            datasource_id="ds_1",
            user_id="user_1",
            semantic_config_ids=[config["id"] for config in configs],
        )
    )
    assert len(calls) == 5


def test_cache_expansion_is_capped_by_hard_limit(monkeypatch):
    """按批扩容有上界：超大批量不会把进程缓存撑爆。"""
    semantic_materializer._YAML_CACHE.clear()
    monkeypatch.setattr(semantic_materializer, "_CACHE_LIMIT", 1)
    monkeypatch.setattr(semantic_materializer, "_CACHE_HARD_LIMIT", 3)
    configs = [_config(f"sc_{index}") for index in range(5)]
    monkeypatch.setattr(semantic_materializer, "get_semantic_config_service", lambda: _FakeDatasourceManager(configs))
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter,
        "convert",
        lambda self, config, *, source_type="": "yaml",
    )

    asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=_FakeRuntime(),
            datasource_id="ds_1",
            user_id="user_1",
            semantic_config_ids=[config["id"] for config in configs],
        )
    )

    assert len(semantic_materializer._YAML_CACHE) == 3


def test_materializer_prunes_stale_files_only_for_full_scope(monkeypatch):
    semantic_materializer._YAML_CACHE.clear()
    configs = [_config("sc_keep")]
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager(configs),
    )
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter,
        "convert",
        lambda self, config, *, source_type="": f"semantic_model: {config['id']}\n",
    )
    stale = "/mnt/user-data/workspace/semantic/已停用模型__deadbeef.yaml"

    # 全量范围（prune_stale=True）：残留文件被清理，有效文件保留
    runtime = _FakeRuntime()
    runtime.files.add(stale)
    result = asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
            semantic_config_ids=["sc_keep"],
            prune_stale=True,
        )
    )
    assert runtime.deleted == [stale]
    assert result["paths"] == {"sc_keep": "/mnt/user-data/workspace/semantic/订单模型__keep.yaml"}

    # 单模型范围（默认）：即使目录有残留也不清理，避免误删其它模型文件
    single_runtime = _FakeRuntime()
    single_runtime.files.add(stale)
    asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=single_runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
            semantic_config_ids=["sc_keep"],
        )
    )
    assert single_runtime.deleted == []


def test_prune_stale_survives_list_dir_failure(monkeypatch):
    semantic_materializer._YAML_CACHE.clear()
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager([_config("sc_keep")]),
    )
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter,
        "convert",
        lambda self, config, *, source_type="": f"semantic_model: {config['id']}\n",
    )

    class _BrokenListRuntime(_FakeRuntime):
        async def list_dir(self, *, path, max_depth):
            raise RuntimeError("backend unavailable")

    runtime = _BrokenListRuntime()
    result = asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
            semantic_config_ids=["sc_keep"],
            prune_stale=True,
        )
    )
    # 清理失败不影响物化结果本身
    assert result["paths"] == {"sc_keep": "/mnt/user-data/workspace/semantic/订单模型__keep.yaml"}
    assert runtime.deleted == []


def test_prune_stale_survives_delete_failure(monkeypatch):
    """删除通道被拒（如后端不支持）时只告警，不中断物化。"""
    semantic_materializer._YAML_CACHE.clear()
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager([_config("sc_keep")]),
    )
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter,
        "convert",
        lambda self, config, *, source_type="": f"semantic_model: {config['id']}\n",
    )
    stale = "/mnt/user-data/workspace/semantic/已停用模型__deadbeef.yaml"

    class _BlockedDeleteRuntime(_FakeRuntime):
        async def delete_internal_text_files(self, *, path):
            raise ValueError("语义模型残留清理被后端拒绝")

    runtime = _BlockedDeleteRuntime()
    runtime.files.add(stale)
    result = asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
            semantic_config_ids=["sc_keep"],
            prune_stale=True,
        )
    )
    assert result["paths"] == {"sc_keep": "/mnt/user-data/workspace/semantic/订单模型__keep.yaml"}
    assert runtime.deleted == []


def test_safe_file_name_semantics_and_collisions():
    # 中文名保留，id 后缀保唯一
    name = semantic_materializer._safe_file_name({"id": "sc_20260116115442_907669", "name": "订单模型"})
    assert name == "订单模型__907669.yaml"
    # 清洗后同名（模型A/B 与 模型A B 都折成 模型A_B），id 后缀区分
    a = semantic_materializer._safe_file_name({"id": "sc_a_11111111", "name": "模型A/B"})
    b = semantic_materializer._safe_file_name({"id": "sc_b_22222222", "name": "模型A B"})
    assert a != b
    assert a.startswith("模型A_B__") and b.startswith("模型A_B__")
    # 路径穿越字符被折叠
    traversal = semantic_materializer._safe_file_name({"id": "sc_x_33333333", "name": "../../etc"})
    assert "/" not in traversal and traversal.endswith("__33333333.yaml")
    # 名字清洗为空 → 退化为纯 id 后缀，不再抛异常
    assert semantic_materializer._safe_file_name({"id": "sc_y_44444444", "name": "///"}) == "44444444.yaml"
    # 超长名字截断后仍带 id 后缀
    long_name = "很" * 60
    truncated = semantic_materializer._safe_file_name({"id": "sc_z_55555555", "name": long_name})
    assert truncated == f"{'很' * 40}__55555555.yaml"


def test_materializer_annotates_column_dicts_into_conversion_input(monkeypatch):
    """字典四项闭环（导出侧）：渲染前把命中字典标注进转换副本，进缓存键；原配置不被污染。"""
    semantic_materializer._YAML_CACHE.clear()
    config = _config()
    config["dimensions"][0].update({"table": "orders", "column": "city"})
    manager = _FakeDatasourceManager([config])
    manager.column_dicts = {("orders", "city"): {"unit": "个"}}
    monkeypatch.setattr(semantic_materializer, "get_semantic_config_service", lambda: manager)
    captured = []

    def fake_convert(self, config, *, source_type=""):
        captured.append(config)
        return f"semantic_model: {config['id']}\n"

    monkeypatch.setattr(semantic_materializer.ChatDataToOssieConverter, "convert", fake_convert)
    _materialize(_FakeRuntime())

    assert captured[0]["dimensions"][0]["_column_dict"] == {"unit": "个"}
    assert "_column_dict" not in config["dimensions"][0]


def _prematerialize(runtime, configs, monkeypatch):
    semantic_materializer._YAML_CACHE.clear()
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager(configs),
    )
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter,
        "convert",
        lambda self, config, *, source_type="": f"semantic_model: {config['id']}\n",
    )
    return asyncio.run(
        semantic_materializer.prematerialize_enabled_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
        )
    )


def _catalog_content(runtime) -> str | None:
    return next(
        (
            content
            for path, content in runtime.entries
            if path.endswith(semantic_materializer.SEMANTIC_CATALOG_FILE_NAME)
        ),
        None,
    )


def test_prematerialize_writes_semantic_catalog(monkeypatch):
    """全量物化生成语义资产目录：file/description/business_notes 字段，停用模型不出现。"""
    configs = [
        dict(_config("sc_a"), business_notes="【业务运转说明】订单口径说明"),
        _config("sc_off", enabled=False),
    ]
    runtime = _FakeRuntime()
    result = _prematerialize(runtime, configs, monkeypatch)

    assert result == {"total": 1, "failed_ids": []}
    catalog_entry = _catalog_content(runtime)
    assert catalog_entry is not None
    payload = yaml.safe_load(catalog_entry)
    assert payload["generated_at"]
    assert [model["file"] for model in payload["models"]] == ["订单模型__a.yaml"]
    model = payload["models"][0]
    assert model["description"] == "订单分析"
    assert model["business_notes"] == "【业务运转说明】订单口径说明"
    assert "config_id" not in model


def test_single_model_materialization_skips_catalog(monkeypatch):
    """单模型物化不写目录：拿本次选择集重写会把全量目录覆盖成残缺版。"""
    semantic_materializer._YAML_CACHE.clear()
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager([_config("sc_keep")]),
    )
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter,
        "convert",
        lambda self, config, *, source_type="": f"semantic_model: {config['id']}\n",
    )
    runtime = _FakeRuntime()
    _materialize(runtime)

    assert _catalog_content(runtime) is None


def test_prune_keeps_semantic_catalog(monkeypatch):
    """残留清理只删停用模型遗留文件，语义资产目录始终在保留集内。"""
    stale = "/mnt/user-data/workspace/semantic/已停用模型__deadbeef.yaml"
    runtime = _FakeRuntime()
    runtime.files.add(
        semantic_materializer.SEMANTIC_SNAPSHOT_DIR + "/" + semantic_materializer.SEMANTIC_CATALOG_FILE_NAME
    )
    runtime.files.add(stale)
    _prematerialize(runtime, [_config("sc_keep")], monkeypatch)

    assert runtime.deleted == [stale]


def test_catalog_render_failure_does_not_block_models(monkeypatch):
    """目录渲染失败只告警：模型快照照常写盘、残留清理照常执行。"""
    semantic_materializer._YAML_CACHE.clear()
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager([_config("sc_keep")]),
    )
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter,
        "convert",
        lambda self, config, *, source_type="": f"semantic_model: {config['id']}\n",
    )
    monkeypatch.setattr(
        semantic_materializer,
        "_render_semantic_catalog",
        lambda configs: (_ for _ in ()).throw(RuntimeError("render broken")),
    )
    stale = "/mnt/user-data/workspace/semantic/已停用模型__deadbeef.yaml"
    runtime = _FakeRuntime()
    runtime.files.add(stale)

    result = asyncio.run(
        semantic_materializer.materialize_runtime_semantic_models(
            workspace_runtime=runtime,
            datasource_id="ds_1",
            user_id="user_1",
            source_type="mysql",
            semantic_config_ids=["sc_keep"],
            prune_stale=True,
        )
    )

    assert result["paths"] == {"sc_keep": "/mnt/user-data/workspace/semantic/订单模型__keep.yaml"}
    assert _catalog_content(runtime) is None
    assert runtime.deleted == [stale]
