import asyncio
import sys
from pathlib import Path

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

    async def delete_path(self, *, path, recursive, expected_type):
        self.deleted.append(path)
        self.files.discard(path)
        return {"path": path}


class _FakeDatasourceManager:
    def __init__(self, configs):
        self.configs = configs
        self.column_dicts = {}
        self.column_dict_calls = 0

    async def get_column_dicts_async(self, datasource_id):
        self.column_dict_calls += 1
        return self.column_dicts

    async def get_runtime_config_async(self, datasource_id, config_id, user_id="", *, column_dicts=None):
        assert datasource_id == "ds_1"
        assert user_id == "user_1"
        assert column_dicts is not None, "materializer 必须预取字段字典并下发，禁止逐模型重复拉取"
        return next((config for config in self.configs if config["id"] == config_id), None)

    async def get_runtime_configs_async(self, datasource_id, user_id=""):
        assert datasource_id == "ds_1"
        return self.configs


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
    second = _materialize(second_runtime)

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

    assert [path for path, _ in runtime.entries] == ["/mnt/user-data/workspace/semantic/订单模型__keep.yaml"]
    assert result["paths"] == {"sc_keep": "/mnt/user-data/workspace/semantic/订单模型__keep.yaml"}


def test_prematerialize_skips_without_datasource_or_runtime():
    assert asyncio.run(semantic_materializer.prematerialize_enabled_semantic_models(
        workspace_runtime=_FakeRuntime(), datasource_id=None, user_id="user_1",
    )) == {"total": 0, "failed_ids": []}
    assert asyncio.run(semantic_materializer.prematerialize_enabled_semantic_models(
        workspace_runtime=None, datasource_id="ds_1", user_id="user_1",
    )) == {"total": 0, "failed_ids": []}


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

    result = asyncio.run(semantic_materializer.materialize_runtime_semantic_models(
        workspace_runtime=_FakeRuntime(),
        datasource_id="ds_1",
        user_id="user_1",
        source_type="mysql",
        semantic_config_ids=["sc_ok", "sc_load_fail", "sc_render_fail"],
    ))

    assert result["failed_ids"] == ["sc_load_fail", "sc_render_fail"]
    assert list(result["paths"]) == ["sc_ok"]


def test_materializer_returns_empty_failed_ids_for_empty_scope():
    result = asyncio.run(semantic_materializer.materialize_runtime_semantic_models(
        workspace_runtime=_FakeRuntime(),
        datasource_id="ds_1",
        user_id="user_1",
        semantic_config_ids=[],
    ))

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
        semantic_materializer.ChatDataToOssieConverter, "convert",
        lambda self, config, *, source_type="": f"semantic_model: {config['id']}\n",
    )
    runtime = _FakeRuntime()

    result = asyncio.run(semantic_materializer.prematerialize_enabled_semantic_models(
        workspace_runtime=runtime,
        datasource_id="ds_1",
        user_id="user_1",
        source_type="mysql",
    ))

    assert result == {"total": 2, "failed_ids": []}
    assert [path for path, _ in runtime.entries] == [
        "/mnt/user-data/workspace/semantic/订单模型__a.yaml",
        "/mnt/user-data/workspace/semantic/订单模型__b.yaml",
    ]
    assert runtime.deleted == []


def test_materializer_prunes_stale_files_only_for_full_scope(monkeypatch):
    semantic_materializer._YAML_CACHE.clear()
    configs = [_config("sc_keep")]
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager(configs),
    )
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter, "convert",
        lambda self, config, *, source_type="": f"semantic_model: {config['id']}\n",
    )
    stale = "/mnt/user-data/workspace/semantic/已停用模型__deadbeef.yaml"

    # 全量范围（prune_stale=True）：残留文件被清理，有效文件保留
    runtime = _FakeRuntime()
    runtime.files.add(stale)
    result = asyncio.run(semantic_materializer.materialize_runtime_semantic_models(
        workspace_runtime=runtime,
        datasource_id="ds_1",
        user_id="user_1",
        source_type="mysql",
        semantic_config_ids=["sc_keep"],
        prune_stale=True,
    ))
    assert runtime.deleted == [stale]
    assert result["paths"] == {"sc_keep": "/mnt/user-data/workspace/semantic/订单模型__keep.yaml"}

    # 单模型范围（默认）：即使目录有残留也不清理，避免误删其它模型文件
    single_runtime = _FakeRuntime()
    single_runtime.files.add(stale)
    asyncio.run(semantic_materializer.materialize_runtime_semantic_models(
        workspace_runtime=single_runtime,
        datasource_id="ds_1",
        user_id="user_1",
        source_type="mysql",
        semantic_config_ids=["sc_keep"],
    ))
    assert single_runtime.deleted == []


def test_prune_stale_survives_list_dir_failure(monkeypatch):
    semantic_materializer._YAML_CACHE.clear()
    monkeypatch.setattr(
        semantic_materializer,
        "get_semantic_config_service",
        lambda: _FakeDatasourceManager([_config("sc_keep")]),
    )
    monkeypatch.setattr(
        semantic_materializer.ChatDataToOssieConverter, "convert",
        lambda self, config, *, source_type="": f"semantic_model: {config['id']}\n",
    )

    class _BrokenListRuntime(_FakeRuntime):
        async def list_dir(self, *, path, max_depth):
            raise RuntimeError("backend unavailable")

    runtime = _BrokenListRuntime()
    result = asyncio.run(semantic_materializer.materialize_runtime_semantic_models(
        workspace_runtime=runtime,
        datasource_id="ds_1",
        user_id="user_1",
        source_type="mysql",
        semantic_config_ids=["sc_keep"],
        prune_stale=True,
    ))
    # 清理失败不影响物化结果本身
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
