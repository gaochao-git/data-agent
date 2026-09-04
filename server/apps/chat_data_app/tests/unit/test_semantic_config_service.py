import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.services.semantic.core import config_validator as config_validator_module
from apps.chat_data_app.services.semantic.run import semantic_config_service as semantic_config_service_module
from apps.chat_data_app.services.semantic.run.semantic_config_service import SemanticConfigService


class _FakeDatasourceManager:
    def __init__(self):
        self.datasource = {
            "id": "ds_a",
            "group_id": "group_a",
            "source_type": "hive",
            "ds_tables": [{
                "table_name": "orders",
                "columns": [{"name": "amount"}, {"name": "status"}],
                "primary_keys": [["id"]],
                "unique_indexes": [{"name": "uniq_order_no", "columns": ["order_no"]}],
                "manual_indexes": [{
                    "index_type": "unique",
                    "index_name": "uniq_external_id",
                    "columns": ["external_id"],
                }],
            }],
        }
        self.column_dicts = {}
        self.column_dict_error = None

    async def get_datasource_async(self, datasource_id):
        return dict(self.datasource) if datasource_id == "ds_a" else None

    async def get_datasource_meta_async(self, datasource_id):
        if datasource_id != "ds_a":
            return None
        return {"id": "ds_a", "group_id": "group_a"}

    async def get_datasource_column_dicts_async(self, datasource_id):
        assert datasource_id == "ds_a"
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
    public = _base_config(relationships=[{
        "left_table": "orders",
        "right_table": "items",
        "left_column": "order_id",
        "right_column": "order_id",
        "relationship_type": "one_to_many",
    }])
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

    assert directory == [{
        "id": "sc_a",
        "name": "订单分析",
        "description": "",
        "enabled": True,
        "metrics_total": 1,
        "dimensions_total": 1,
    }]
    assert asyncio.run(service.get_directory_async("missing", "u1")) == []
    assert datasource_manager.datasource["ds_tables"]


def test_runtime_config_merges_column_dictionary_without_mutating_persisted_config():
    persisted = _base_config(metrics=[{
        "name": "销售额",
        "table": "orders",
        "expression": "SUM(amount)",
        "metadata_supplement": "统计实付金额",
    }])
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


def test_column_dictionary_helper_branches():
    assert SemanticConfigService._extract_metric_source_column({"expression": "SUM(orders.amount)"}) == "amount"
    assert SemanticConfigService._extract_metric_source_column({"expression": "`orders.status`"}) == "status"
    assert SemanticConfigService._extract_metric_source_column({"expression": "amount + tax", "field": "`orders.amount`"}) == "amount"
    assert SemanticConfigService._extract_metric_source_column({"expression": "amount + tax"}) == ""
    assert SemanticConfigService._format_column_dict_supplement({
        "enum_values": [],
        "storage_format": "yyyyMMdd",
        "unit": "万元",
    }) == "存储格式：yyyyMMdd\n单位：万元"
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
            relationships=[{
                "left_table": "orders",
                "right_table": "items",
                "left_column": "order_id",
                "right_column": "order_id",
            }],
        )
        assert created["relationships"][0]["join_conditions"] == [{"left_column": "order_id", "right_column": "order_id"}]
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


def test_generate_basic_single_table_configs_creates_disabled_models_and_is_idempotent(monkeypatch):
    service, datasource_manager, dao = _build_service()
    build_map_calls = 0
    original_build_table_columns_map = semantic_config_service_module.build_table_columns_map

    def tracked_build_table_columns_map(datasource):
        nonlocal build_map_calls
        build_map_calls += 1
        return original_build_table_columns_map(datasource)

    monkeypatch.setattr(semantic_config_service_module, "build_table_columns_map", tracked_build_table_columns_map)
    monkeypatch.setattr(
        config_validator_module,
        "build_table_columns_map",
        lambda _datasource: pytest.fail("批量校验不应重复构建字段映射"),
    )
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

    first = asyncio.run(service.generate_basic_public_single_table_configs_async(
        "ds_a",
        create_by="u1",
        create_by_name="用户1",
    ))
    second = asyncio.run(service.generate_basic_public_single_table_configs_async(
        "ds_a",
        create_by="u1",
        create_by_name="用户1",
    ))

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
    assert orders["enabled"] is False
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
    result = asyncio.run(service.generate_basic_public_single_table_configs_async(
        "ds_a",
        create_by="admin",
    ))

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
    assert description.endswith(" 自动生成，待人工复核后启用")
