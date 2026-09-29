import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.services.semantic.run import semantic_config_service as semantic_config_service_module
from apps.chat_data_app.services.semantic.run.chat_data_ossie_importer import OssieSemanticModelImport
from apps.chat_data_app.services.semantic.run.semantic_config_service import SemanticConfigService


class _FakeDatasourceManager:
    def __init__(self):
        self.datasource = {
            "id": "ds_a",
            "group_id": "group_a",
            "source_type": "hive",
            "ds_tables": [
                {
                    "table_name": "orders",
                    "columns": [{"name": "amount"}, {"name": "status"}],
                    "primary_keys": [["id"]],
                    "unique_indexes": [{"name": "uniq_order_no", "columns": ["order_no"]}],
                    "manual_indexes": [
                        {
                            "index_type": "unique",
                            "index_name": "uniq_external_id",
                            "columns": ["external_id"],
                        }
                    ],
                }
            ],
        }
        self.column_dicts = {}
        self.column_dict_error = None
        self.column_dict_calls = 0

    async def get_datasource_async(self, datasource_id):
        return dict(self.datasource) if datasource_id == "ds_a" else None

    async def get_datasource_meta_async(self, datasource_id):
        if datasource_id != "ds_a":
            return None
        return {"id": "ds_a", "group_id": "group_a"}

    async def get_datasource_column_dicts_async(self, datasource_id):
        assert datasource_id == "ds_a"
        self.column_dict_calls += 1
        if self.column_dict_error:
            raise self.column_dict_error
        return self.column_dicts


class _FakeSemanticConfigDao:
    def __init__(self, configs=None):
        self.configs = [dict(config) for config in (configs or [])]
        self.upsert_result = True
        self.upsert_many_result = True
        self.upsert_calls = 0
        self.upsert_many_calls = 0
        self.fail_names = set()
        self.deleted = None

    @staticmethod
    def _visible(config, user_id):
        return not user_id or config.get("scope_type", "public") == "public" or config.get("create_by") == user_id

    async def get_by_group_id_async(self, group_id, user_id=""):
        if group_id != "group_a":
            return []
        return [dict(config) for config in self.configs if self._visible(config, user_id)]

    async def get_directory_by_group_id_async(self, group_id, user_id=""):
        return [
            {
                "id": config["id"],
                "name": config.get("name", ""),
                "description": config.get("description", ""),
                "enabled": config.get("enabled", True),
                "metrics_total": len(config.get("metrics") or []),
                "dimensions_total": len(config.get("dimensions") or []),
            }
            for config in await self.get_by_group_id_async(group_id, user_id)
        ]

    async def get_by_id_async(self, group_id, config_id, user_id=""):
        configs = await self.get_by_group_id_async(group_id, user_id)
        return next((config for config in configs if config.get("id") == config_id), None)

    async def upsert_async(self, group_id, config):
        self.upsert_calls += 1
        if config.get("name") in self.fail_names:
            raise RuntimeError("write failed")
        if not self.upsert_result:
            return False
        self._store(group_id, config)
        return True

    async def upsert_many_async(self, group_id, configs):
        self.upsert_many_calls += 1
        if not self.upsert_many_result:
            raise RuntimeError("batch write failed")
        for config in configs:
            self._store(group_id, config)
        return True

    def _store(self, group_id, config):
        next_config = dict(config)
        next_config["group_id"] = group_id
        for index, current in enumerate(self.configs):
            if current.get("id") == next_config.get("id"):
                self.configs[index] = next_config
                break
        else:
            self.configs.append(next_config)

    async def batch_update_enabled_async(self, group_id, config_ids, enabled, update_by="", update_by_name=""):
        self.batch_toggle_calls = getattr(self, "batch_toggle_calls", [])
        self.batch_toggle_calls.append((group_id, list(config_ids), enabled, update_by, update_by_name))
        if getattr(self, "batch_update_enabled_result", None) is not None:
            return self.batch_update_enabled_result
        target_ids = set(config_ids)
        updated = 0
        for index, config in enumerate(self.configs):
            if config.get("id") in target_ids and config.get("group_id", "group_a") == group_id:
                self.configs[index] = {**config, "enabled": enabled}
                updated += 1
        return updated

    async def soft_delete_async(self, config_id, update_by="", update_by_name=""):
        self.deleted = (config_id, update_by, update_by_name)
        self.configs = [config for config in self.configs if config.get("id") != config_id]


def _base_config(**overrides):
    config = {
        "id": "sc_a",
        "group_id": "group_a",
        "name": "订单分析",
        "scope_type": "public",
        "create_by": "admin",
        "enabled": True,
        "metrics": [{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        "dimensions": [{"name": "订单状态", "table": "orders", "column": "status"}],
        "relationships": [],
    }
    config.update(overrides)
    return config


def _build_service(configs=None):
    datasource_manager = _FakeDatasourceManager()
    dao = _FakeSemanticConfigDao(configs)
    return SemanticConfigService(datasource_manager, dao), datasource_manager, dao


def test_query_enriches_model_context_and_applies_visibility():
    public = _base_config(
        relationships=[
            {
                "left_table": "orders",
                "right_table": "items",
                "left_column": "order_id",
                "right_column": "order_id",
                "relationship_type": "one_to_many",
            }
        ]
    )
    private = _base_config(id="sc_private", name="个人分析", scope_type="private", create_by="u1")
    hidden = _base_config(id="sc_hidden", name="他人分析", scope_type="private", create_by="u2")
    service, _, _ = _build_service([public, private, hidden])

    configs = asyncio.run(service.get_configs_async("ds_a", "u1"))
    selected = asyncio.run(service.get_config_async("ds_a", "sc_a", "u1"))

    assert [config["id"] for config in configs] == ["sc_a", "sc_private"]
    assert selected["source_type"] == "hive"
    assert selected["tables"][0]["table_name"] == "orders"
    assert selected["tables"][0]["primary_keys"] == [["id"]]
    assert selected["tables"][0]["unique_indexes"][0]["columns"] == ["order_no"]
    assert selected["tables"][0]["manual_indexes"][0]["columns"] == ["external_id"]
    assert selected["relationships"][0]["join_conditions"] == [{"left_column": "order_id", "right_column": "order_id"}]
    assert selected["joins"] == selected["relationships"]
    assert asyncio.run(service.get_configs_async("missing", "u1")) == []
    assert asyncio.run(service.get_config_async("missing", "sc_a", "u1")) is None


def test_directory_uses_lightweight_group_lookup():
    service, datasource_manager, _ = _build_service([_base_config()])

    directory = asyncio.run(service.get_directory_async("ds_a", "u1"))

    assert directory == [
        {
            "id": "sc_a",
            "name": "订单分析",
            "description": "",
            "enabled": True,
            "metrics_total": 1,
            "dimensions_total": 1,
        }
    ]
    assert asyncio.run(service.get_directory_async("missing", "u1")) == []
    assert datasource_manager.datasource["ds_tables"]


def test_runtime_config_merges_column_dictionary_without_mutating_persisted_config():
    persisted = _base_config(
        metrics=[
            {
                "name": "销售额",
                "table": "orders",
                "expression": "SUM(amount)",
                "metadata_supplement": "统计实付金额",
            }
        ]
    )
    service, datasource_manager, dao = _build_service([persisted])
    datasource_manager.column_dicts = {
        ("orders", "status"): {
            "enum_values": [{"value": "1", "label": "有效"}, {"value": "0", "label": "无效"}],
            "storage_format": "tinyint",
            "unit": "",
        },
        ("orders", "amount"): {"enum_values": [], "storage_format": "", "unit": "元"},
    }

    runtime = asyncio.run(service.get_runtime_config_async("ds_a", "sc_a"))

    assert runtime["dimensions"][0]["allowed_values"] == ["有效", "无效"]
    assert runtime["dimensions"][0]["mapping"] == {"有效": "1", "无效": "0"}
    assert runtime["dimensions"][0]["logical_type"] == "enum"
    assert runtime["dimensions"][0]["metadata_supplement"] == "枚举值：1=有效；0=无效\n存储格式：tinyint"
    assert runtime["metrics"][0]["metadata_supplement"] == "统计实付金额\n单位：元"
    assert "allowed_values" not in dao.configs[0]["dimensions"][0]


def test_runtime_config_falls_back_when_column_dictionary_fails():
    service, datasource_manager, _ = _build_service([_base_config()])
    datasource_manager.column_dict_error = RuntimeError("dictionary unavailable")

    configs = asyncio.run(service.get_runtime_configs_async("ds_a"))

    assert configs[0]["dimensions"][0]["logical_type"] == "text"
    assert asyncio.run(service.get_runtime_config_async("ds_a", "missing")) is None


def test_runtime_bundle_returns_configs_and_dictionary_in_one_pass():
    """批量入口只查一次字典：批量物化依赖它避免字典被重复拉取。"""
    service, datasource_manager, _ = _build_service([_base_config()])
    datasource_manager.column_dicts = {("orders", "city"): {"unit": "个"}}

    configs, column_dicts = asyncio.run(service.get_runtime_bundle_async("ds_a"))

    assert [config["id"] for config in configs] == ["sc_a"]
    assert column_dicts == {("orders", "city"): {"unit": "个"}}
    assert datasource_manager.column_dict_calls == 1


def test_runtime_bundle_configs_match_per_model_runtime_config():
    """批量入口返回的配置必须与逐条加载同构：都已合并字段字典。

    物化链路直接复用 bundle 的配置渲染 YAML。若少了 _merge_column_dict，
    枚举取值 / logical_type / 补充说明会丢失，模型读到的维度退化成裸字段；
    导出用的 annotate_export_column_dicts 只挂 _column_dict 原文，不能替代。
    """
    service, datasource_manager, _ = _build_service([_base_config()])
    datasource_manager.column_dicts = {
        ("orders", "status"): {
            "enum_values": [{"label": "已支付", "value": "PAID"}],
            "storage_format": "编码",
        },
    }

    async def run():
        configs, column_dicts = await service.get_runtime_bundle_async("ds_a")
        return configs, await service.get_runtime_config_async("ds_a", "sc_a", column_dicts=column_dicts)

    configs, per_model = asyncio.run(run())

    assert configs[0] == per_model, "bundle 配置必须与逐条加载结果一致，否则物化 YAML 会丢字典合并结果"
    dimension = configs[0]["dimensions"][0]
    # 枚举取值取 label（展示口径），映射 label -> value
    assert dimension["allowed_values"] == ["已支付"]
    assert dimension["mapping"] == {"已支付": "PAID"}
    assert dimension["logical_type"] == "enum"
    assert "编码" in dimension["metadata_supplement"]


def test_runtime_bundle_short_circuits_without_configs():
    """没有可见配置时不再查字典，避免空数据源组白白多一次 DB 往返。"""
    service, datasource_manager, _ = _build_service([])

    assert asyncio.run(service.get_runtime_bundle_async("ds_a")) == ([], {})
    assert datasource_manager.column_dict_calls == 0


def test_column_dictionary_helper_branches():
    assert SemanticConfigService._extract_metric_source_column({"expression": "SUM(orders.amount)"}) == "amount"
    assert SemanticConfigService._extract_metric_source_column({"expression": "`orders.status`"}) == "status"
    assert (
        SemanticConfigService._extract_metric_source_column({"expression": "amount + tax", "field": "`orders.amount`"})
        == "amount"
    )
    assert SemanticConfigService._extract_metric_source_column({"expression": "amount + tax"}) == ""
    assert (
        SemanticConfigService._format_column_dict_supplement(
            {
                "enum_values": [],
                "storage_format": "yyyyMMdd",
                "unit": "万元",
            }
        )
        == "存储格式：yyyyMMdd\n单位：万元"
    )
    assert SemanticConfigService._merge_supplement("已有说明", "单位：元") == "已有说明\n单位：元"
    assert SemanticConfigService._merge_supplement("已有说明\n单位：元", "单位：元") == "已有说明\n单位：元"


def test_create_update_delete_lifecycle_and_name_scope_rules():
    public = _base_config()
    private = _base_config(id="sc_private", name="个人分析", scope_type="private", create_by="u1")
    service, _, dao = _build_service([public, private])

    async def run_checks():
        assert await service.create_async("missing", name="新配置") is None
        with pytest.raises(ValueError, match="名称不能为空"):
            await service.create_async("ds_a", name="")
        with pytest.raises(ValueError, match="只能是 public 或 private"):
            await service.create_async("ds_a", name="新配置", scope_type="invalid")
        with pytest.raises(ValueError, match="名称已存在"):
            await service.create_async("ds_a", name="订单分析", scope_type="private", create_by="u1")
        # 同组内模型名全局唯一（与唯一索引 (group_id, name, delete_flag) 对齐）：
        # 不同 owner 的 private 同名同样拒绝，否则应用层放行后 DB 唯一键拒绝报 IntegrityError。
        with pytest.raises(ValueError, match="名称已存在"):
            await service.create_async("ds_a", name="个人分析", scope_type="private", create_by="u2")

        created = await service.create_async(
            "ds_a",
            name="新配置",
            scope_type="private",
            create_by="u1",
            relationships=[
                {
                    "left_table": "orders",
                    "right_table": "items",
                    "left_column": "order_id",
                    "right_column": "order_id",
                }
            ],
        )
        assert created["relationships"][0]["join_conditions"] == [
            {"left_column": "order_id", "right_column": "order_id"}
        ]
        assert await service.update_async("ds_a", "missing", name="x") is None
        with pytest.raises(ValueError, match="名称不能为空"):
            await service.update_async("ds_a", "sc_private", name="")
        with pytest.raises(ValueError, match="名称已存在"):
            await service.update_async("ds_a", "sc_private", name="订单分析")

        updated = await service.update_async("ds_a", "sc_private", description="更新", enabled=False, update_by="u3")
        assert updated["description"] == "更新"
        assert updated["enabled"] is False
        assert updated["update_by"] == "u3"
        assert await service.delete_async("ds_a", "missing") is False
        assert await service.delete_async("ds_a", "sc_private", update_by="u4", update_by_name="用户4") is True

    asyncio.run(run_checks())
    assert dao.deleted == ("sc_private", "u4", "用户4")


def test_persistence_failure_is_reported_as_empty_result():
    service, _, dao = _build_service([_base_config()])
    dao.upsert_result = False

    assert asyncio.run(service.create_async("ds_a", name="新配置")) is None
    assert asyncio.run(service.update_async("ds_a", "sc_a", description="更新")) is None


def test_generate_basic_single_table_configs_creates_enabled_models_and_is_idempotent(monkeypatch):
    service, datasource_manager, dao = _build_service()
    build_map_calls = 0
    original_build_table_columns_map = semantic_config_service_module.build_table_columns_map

    def tracked_build_table_columns_map(datasource):
        nonlocal build_map_calls
        build_map_calls += 1
        return original_build_table_columns_map(datasource)

    monkeypatch.setattr(semantic_config_service_module, "build_table_columns_map", tracked_build_table_columns_map)
    datasource_manager.datasource["ds_tables"] = [
        {
            "table_name": "orders",
            "columns": [
                {"name": "id", "mysql_type": "bigint", "description": "订单ID"},
                {"name": "created_at", "mysql_type": "datetime", "description": "创建时间"},
                {"name": "status", "mysql_type": "varchar(20)", "description": "状态"},
                {"name": "status_code", "mysql_type": "int", "description": "状态"},
            ],
        },
        {"table_name": "empty_table", "columns": []},
    ]

    first = asyncio.run(
        service.generate_basic_public_single_table_configs_async(
            "ds_a",
            create_by="u1",
            create_by_name="用户1",
        )
    )
    second = asyncio.run(
        service.generate_basic_public_single_table_configs_async(
            "ds_a",
            create_by="u1",
            create_by_name="用户1",
        )
    )

    assert first["created"] == 2
    assert first["failed"] == 0
    assert [item["table"] for item in first["items"]] == ["orders", "empty_table"]
    assert second == {
        "created": 0,
        "skipped": 2,
        "failed": 0,
        "items": [
            {"table": "orders", "status": "skipped", "reason": "已有语义模型"},
            {"table": "empty_table", "status": "skipped", "reason": "已有语义模型"},
        ],
    }
    orders = next(config for config in dao.configs if config["name"] == "orders基础模型")
    assert orders["enabled"] is True
    assert orders["scope_type"] == "public"
    assert orders["create_by"] == "u1"
    assert orders["metrics"] == []
    assert orders["relationships"] == []
    assert [item["name"] for item in orders["dimensions"]] == ["订单ID", "创建时间", "状态", "状态2"]
    assert [item["logical_type"] for item in orders["dimensions"]] == ["number", "datetime", "text", "number"]
    assert all(item["table_alias"] == "orders" for item in orders["dimensions"])
    assert all(item["description"] == "" for item in orders["dimensions"])
    assert dao.upsert_many_calls == 1
    assert dao.upsert_calls == 0
    assert build_map_calls == 2


def test_generate_basic_single_table_configs_skips_existing_table_and_reports_failure():
    service, datasource_manager, dao = _build_service([_base_config(scope_type="public")])
    datasource_manager.datasource["ds_tables"] = [
        datasource_manager.datasource["ds_tables"][0],
        {"table_name": "users", "columns": [{"name": "id", "type": "int"}]},
    ]
    dao.upsert_many_result = False
    dao.fail_names.add("users基础模型")
    result = asyncio.run(
        service.generate_basic_public_single_table_configs_async(
            "ds_a",
            create_by="admin",
        )
    )

    assert result["created"] == 0
    assert result["skipped"] == 1
    assert result["failed"] == 1
    assert result["items"][0] == {"table": "orders", "status": "skipped", "reason": "已有语义模型"}
    assert result["items"][1] == {"table": "users", "status": "failed", "reason": "写入失败"}
    assert len(dao.configs) == 1
    assert dao.upsert_many_calls == 1
    assert dao.upsert_calls == 1


def test_generate_basic_single_table_configs_rejects_invalid_inputs():
    service, _, _ = _build_service()

    with pytest.raises(ValueError, match="数据源不存在"):
        asyncio.run(service.generate_basic_public_single_table_configs_async("missing"))


def test_basic_single_table_text_is_stable_and_within_database_limits():
    long_table_name = "table_" + "x" * 140
    another_table_name = "table_" + "x" * 139 + "y"

    generated_name = SemanticConfigService._basic_single_table_name(long_table_name)
    another_name = SemanticConfigService._basic_single_table_name(another_table_name)
    description = SemanticConfigService._basic_single_table_description(long_table_name)

    assert len(generated_name) == 128
    assert generated_name.endswith("基础模型")
    assert generated_name == SemanticConfigService._basic_single_table_name(long_table_name)
    assert generated_name != another_name
    assert len(description) == 128
    assert description.startswith("基于物理表 ")
    assert description.endswith(" 自动生成，建议复核")


def test_dedupe_semantic_config_ids_keeps_order_and_drops_blank():
    assert semantic_config_service_module.dedupe_semantic_config_ids(["b", "a", "b", "  ", "", None]) == ["b", "a"]
    assert semantic_config_service_module.dedupe_semantic_config_ids([]) == []
    assert semantic_config_service_module.dedupe_semantic_config_ids(None) == []
    assert semantic_config_service_module.empty_batch_toggle_result() == {
        "updated_ids": [],
        "skipped_ids": [],
        "not_found_ids": [],
        "updated_count": 0,
    }


def test_batch_toggle_enabled_async_splits_updated_skipped_and_not_found():
    public = _base_config(id="sc_public", name="公共模型")
    own = _base_config(id="sc_own", name="本人私有", scope_type="private", create_by="u1")
    service, _, dao = _build_service([public, own])

    result = asyncio.run(
        service.batch_toggle_enabled_async(
            "ds_a",
            ["sc_own", "sc_public", "sc_missing", "sc_own", "  "],
            False,
            user_id="u1",
            username="张三",
            admin_manage=False,
        )
    )

    assert result["updated_ids"] == ["sc_own"]
    assert result["skipped_ids"] == ["sc_public"]
    assert result["not_found_ids"] == ["sc_missing"]
    assert result["updated_count"] == 1
    assert dao.batch_toggle_calls[0] == ("group_a", ["sc_own"], False, "u1", "张三")
    assert next(config for config in dao.configs if config["id"] == "sc_own")["enabled"] is False
    assert next(config for config in dao.configs if config["id"] == "sc_public")["enabled"] is True


def test_batch_toggle_enabled_async_treats_same_state_as_idempotent_success():
    """已处于目标状态的模型算成功但不写库。

    MySQL 的 UPDATE rowcount 只统计「值真的变了」的行。若把同态模型一起发到库里，
    全选批量操作会得到 rowcount=0：前端成功数与 updated_ids 对不上，后端还会刷
    一条误导性的行数不一致告警。
    """
    service, _, dao = _build_service(
        [
            _base_config(id="sc_already", enabled=True),
            _base_config(id="sc_off", enabled=False),
        ]
    )

    result = asyncio.run(
        service.batch_toggle_enabled_async(
            "ds_a",
            ["sc_already", "sc_off"],
            True,
            admin_manage=True,
        )
    )

    assert result["updated_ids"] == ["sc_already", "sc_off"], "幂等项也要计入成功，保持请求顺序"
    assert result["updated_count"] == 2
    assert dao.batch_toggle_calls == [("group_a", ["sc_off"], True, "", "")], "只把需要变更的 ID 发到库里"


def test_batch_toggle_enabled_async_admin_only_mutates_public_configs():
    public = _base_config(id="sc_public", name="公共模型", enabled=False)
    own = _base_config(id="sc_own", name="本人私有", scope_type="private", create_by="u1")
    service, _, dao = _build_service([public, own])

    result = asyncio.run(
        service.batch_toggle_enabled_async(
            "ds_a",
            ["sc_public", "sc_own"],
            True,
            user_id="u1",
            username="张三",
            admin_manage=True,
        )
    )

    assert result["updated_ids"] == ["sc_public"]
    assert result["skipped_ids"] == ["sc_own"]
    assert result["not_found_ids"] == []
    assert dao.batch_toggle_calls[0][2] is True


def test_batch_toggle_enabled_async_returns_empty_result_for_missing_inputs():
    service, _, dao = _build_service([_base_config(id="sc_public")])

    missing_datasource = asyncio.run(service.batch_toggle_enabled_async("missing", ["sc_public"], True))
    empty_ids = asyncio.run(service.batch_toggle_enabled_async("ds_a", ["  "], True))

    assert missing_datasource == semantic_config_service_module.empty_batch_toggle_result()
    assert empty_ids == semantic_config_service_module.empty_batch_toggle_result()
    assert not getattr(dao, "batch_toggle_calls", [])


def test_batch_toggle_enabled_async_warns_on_rowcount_mismatch():
    service, _, dao = _build_service([_base_config(id="sc_public", scope_type="public")])
    dao.batch_update_enabled_result = 0

    result = asyncio.run(service.batch_toggle_enabled_async("ds_a", ["sc_public"], False, admin_manage=True))

    assert result["updated_ids"] == ["sc_public"]
    assert result["updated_count"] == 0


# ---------------------------------------------------------------------------
# 跨模型口径检查 helper（cross_model_findings_for_imports）
# ---------------------------------------------------------------------------


class _FakeCrossService:
    """只提供 get_configs_async 的最小 service 替身。"""

    def __init__(self, configs):
        self._configs = configs

    async def get_configs_async(self, datasource_id, user_id=""):
        return list(self._configs)


def _import_model(name: str, metric_name: str, expression: str, *, importable: bool = True):
    return OssieSemanticModelImport(
        name=name,
        metrics=[{"name": metric_name, "expression": expression, "table": "orders"}],
        errors=[] if importable else ["blocked"],
    )


def _public_config(name: str, metrics: list, scope_type: str = "public"):
    return {"id": f"sc_{name}", "name": name, "scope_type": scope_type, "metrics": metrics}


def test_cross_model_findings_for_imports_attributes_and_excludes_replace_target():
    """公共存量参与比较、他人私有不泄露、同名被替换的存量被排除、不可导入模型不归因。"""
    imports = [
        _import_model("新模型", "销售额", "SUM(orders.new_amount)"),
        _import_model("坏模型", "销售额", "SUM(orders.x)", importable=False),
    ]
    existing = [
        _public_config("订单分析", [{"name": "销售额", "expression": "SUM(orders.amount)"}]),
        _public_config("他人私有", [{"name": "销售额", "expression": "SUM(orders.private)"}], scope_type="private"),
        # 与导入模型同名（replace 目标）：即使口径不同也不应误报
        _public_config("新模型", [{"name": "销售额", "expression": "SUM(orders.old)"}]),
    ]
    result = asyncio.run(
        semantic_config_service_module.cross_model_findings_for_imports(
            imports, "ds_a", semantic_service=_FakeCrossService(existing)
        )
    )
    assert set(result) == {"新模型"}
    assert [item["code"] for item in result["新模型"]] == ["CON_CROSS_MODEL_NAME_CONFLICT"]
    assert "订单分析" in result["新模型"][0]["targets"][0]


def test_cross_model_findings_for_imports_compares_within_batch_and_tolerates_service_failure(monkeypatch):
    """同批导入模型之间互相比较；存量读取失败降级为空报告。"""
    imports = [
        _import_model("模型A", "销售额", "SUM(orders.amount)"),
        _import_model("模型B", "销售额", "SUM(orders.amount)"),
    ]

    class _BoomService:
        async def get_configs_async(self, datasource_id):
            raise RuntimeError("db down")

    within_batch = asyncio.run(
        semantic_config_service_module.cross_model_findings_for_imports(
            imports, "ds_a", semantic_service=_FakeCrossService([])
        )
    )
    assert set(within_batch) == {"模型A", "模型B"}
    assert [item["code"] for item in within_batch["模型A"]] == ["CON_CROSS_MODEL_METRIC_DUPLICATE"]

    degraded = asyncio.run(
        semantic_config_service_module.cross_model_findings_for_imports(
            imports, "ds_a", semantic_service=_BoomService()
        )
    )
    assert degraded == {}


def test_cross_model_findings_for_imports_empty_imports_returns_empty():
    assert (
        asyncio.run(
            semantic_config_service_module.cross_model_findings_for_imports(
                [], "ds_a", semantic_service=_FakeCrossService([])
            )
        )
        == {}
    )


# ---------------------------------------------------------------------------
# 导入链路质量检查编排（quality_findings_for_imports：preview 与 check_yaml 共用）
# ---------------------------------------------------------------------------


def test_quality_findings_for_imports_merges_single_and_cross():
    """编排入口：importable 模型合并单模型规则与跨模型口径两层 findings。"""
    imports = [_import_model("模型A", "销售额", "SUM(orders.amount)")]
    existing = [_public_config("模型B", [{"name": "销售额", "expression": "SUM(orders.other)"}])]
    result = asyncio.run(
        semantic_config_service_module.quality_findings_for_imports(
            imports, "ds_a", semantic_service=_FakeCrossService(existing)
        )
    )
    assert set(result) == {"模型A"}
    codes = {item["code"] for item in result["模型A"]}
    assert "DOC_METRIC_NO_DESCRIPTION" in codes  # 单模型层（指标缺描述）
    assert "CON_CROSS_MODEL_NAME_CONFLICT" in codes  # 跨模型层（同名异式）


def test_quality_findings_for_imports_skips_non_importable():
    """不可导入模型不产出 findings（checklist 由调用方基于 validation_error 生成）。"""
    imports = [_import_model("坏模型", "销售额", "SUM(orders.x)", importable=False)]
    assert (
        asyncio.run(
            semantic_config_service_module.quality_findings_for_imports(
                imports, "ds_a", semantic_service=_FakeCrossService([])
            )
        )
        == {}
    )


# ---------------------------------------------------------------------------
# 编辑器保存链路质量检查（quality_findings_for_save：多处校验统一的单一入口）
# ---------------------------------------------------------------------------


def _save_kwargs(**overrides):
    kwargs = {
        "name": "订单分析",
        "metrics": [
            {
                "name": "销售额",
                "expression": "SUM(orders.amount)",
                "table": "orders",
                "description": "已支付订单金额合计",
            }
        ],
        "dimensions": [],
        "relationships": [],
    }
    kwargs.update(overrides)
    return kwargs


def test_quality_findings_for_save_merges_single_and_cross(monkeypatch):
    """单模型规则与跨模型口径同源合并：缺描述触发 DOC 规则，撞名触发跨模型 error。"""
    existing = [_public_config("销售主题", [{"name": "销售额", "expression": "SUM(orders.pay_amount)"}])]

    async def _fake_load(ds, ds_id):
        return None

    monkeypatch.setattr(semantic_config_service_module, "load_semantic_physical_columns", _fake_load)
    findings = asyncio.run(
        semantic_config_service_module.quality_findings_for_save(
            object(), "ds_a", semantic_service=_FakeCrossService(existing), **_save_kwargs()
        )
    )
    codes = {item["code"] for item in findings}
    assert "DOC_METRIC_NO_DESCRIPTION" not in codes  # 该指标有描述
    assert "CON_CROSS_MODEL_NAME_CONFLICT" in codes  # 与销售主题同名异式


def test_quality_findings_for_save_excludes_self_by_id(monkeypatch):
    """update 场景排除自身：库里的旧版本与自己同名同式不再误报。"""
    existing = [
        {"id": "sc_self", "name": "订单分析", "metrics": [{"name": "销售额", "expression": "SUM(orders.amount)"}]}
    ]

    async def _fake_load(ds, ds_id):
        return None

    monkeypatch.setattr(semantic_config_service_module, "load_semantic_physical_columns", _fake_load)
    findings = asyncio.run(
        semantic_config_service_module.quality_findings_for_save(
            object(),
            "ds_a",
            semantic_service=_FakeCrossService(existing),
            exclude_config_id="sc_self",
            **_save_kwargs(),
        )
    )
    assert not any(item["code"].startswith("CON_CROSS_MODEL_") for item in findings)


def test_quality_findings_for_save_degrades_on_physical_failure(monkeypatch):
    """物理列加载失败降级为空列表：质量提示不挡保存。"""

    async def _boom(ds, ds_id):
        raise RuntimeError("ds down")

    monkeypatch.setattr(semantic_config_service_module, "load_semantic_physical_columns", _boom)
    findings = asyncio.run(
        semantic_config_service_module.quality_findings_for_save(
            object(), "ds_a", semantic_service=_FakeCrossService([]), **_save_kwargs()
        )
    )
    assert findings == []


def test_cross_model_comparison_pool_includes_own_private_only():
    """比较池=公共+本人私有：他人私有永不入选，user_id 未知时只留公共。"""
    configs = [
        _public_config("公共", []),
        _public_config("本人私有", [], scope_type="private"),
        _public_config("他人私有", [], scope_type="private"),
    ]
    configs[1]["create_by"] = "u_1"
    configs[2]["create_by"] = "u_2"

    pool_owner = semantic_config_service_module._cross_model_comparison_pool(configs, "u_1")
    assert [c["name"] for c in pool_owner] == ["公共", "本人私有"]

    pool_unknown = semantic_config_service_module._cross_model_comparison_pool(configs, "")
    assert [c["name"] for c in pool_unknown] == ["公共"]


def test_quality_findings_for_save_includes_own_private_conflict(monkeypatch):
    """保存个人模型时与自己另一个个人模型撞名也应提示（review 修正场景）。"""
    existing = [
        {
            "id": "sc_own",
            "name": "我的另一模型",
            "scope_type": "private",
            "create_by": "u_1",
            "metrics": [{"name": "销售额", "expression": "SUM(orders.other)"}],
        }
    ]

    async def _fake_load(ds, ds_id):
        return None

    monkeypatch.setattr(semantic_config_service_module, "load_semantic_physical_columns", _fake_load)
    findings = asyncio.run(
        semantic_config_service_module.quality_findings_for_save(
            object(),
            "ds_a",
            semantic_service=_FakeCrossService(existing),
            user_id="u_1",
            **_save_kwargs(),
        )
    )
    assert "CON_CROSS_MODEL_NAME_CONFLICT" in {item["code"] for item in findings}


def test_quality_findings_for_save_strips_self_name_suffix_only(monkeypatch):
    """配置级提示（目标=模型名）去掉尾部模型名；指标级 target 与跨模型目标保留。"""

    async def _fake_load(ds, ds_id):
        return None

    monkeypatch.setattr(semantic_config_service_module, "load_semantic_physical_columns", _fake_load)
    findings = asyncio.run(
        semantic_config_service_module.quality_findings_for_save(
            object(),
            "ds_a",
            semantic_service=_FakeCrossService([]),
            user_id="u_1",
            name="auth_group_permissions基础模型",
            metrics=[{"name": "集群数量1", "expression": "SUM(t.cnt)", "table": "t"}],
            dimensions=[],
            relationships=[],
        )
    )
    by_code = {item["code"]: item for item in findings}
    assert by_code["DOC_NO_BUSINESS_NOTES"]["message"] == "配置缺少业务说明 business_notes"
    assert by_code["DOC_METRIC_NO_DESCRIPTION"]["message"].endswith(": 集群数量1")
