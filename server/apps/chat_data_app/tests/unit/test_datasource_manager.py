import os
import sys
import asyncio

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services.datasource_manager import (
    _build_member_source_identity,
    _build_member_source_text_line,
    _build_meta_source_config,
    _build_source_line,
    _format_time,
    _get_template_member_source_config,
    _normalize_member_source_config,
    _normalize_query_executor_type,
    _read_positive_int_env,
    _merge_member_source_lines,
    _validate_member_source_configs,
    DataSourceManager,
)


class _FakeStore:
    def __init__(self):
        self.datasources = {
            "ds_a": {
                "id": "ds_a",
                "group_id": "group_ds_a",
                "template_datasource_id": "phy_shared",
                "group_name": "数据源A",
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
                "group_name": "数据源B",
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

    async def get_all_datasources_async(self, include_tables: bool = True):
        return self.get_all_datasources(include_tables=include_tables)

    def has_datasource(self, datasource_id: str):
        return datasource_id in self.datasources

    async def has_datasource_async(self, datasource_id: str):
        return self.has_datasource(datasource_id)

    def get_datasource(self, datasource_id: str):
        return self.datasources.get(datasource_id)

    async def get_datasource_async(self, datasource_id: str):
        return self.get_datasource(datasource_id)

    def get_datasource_by_group_name(self, name: str):
        for item in self.datasources.values():
            if item["group_name"] == name:
                return item
        return None

    async def get_datasource_by_group_name_async(self, name: str):
        return self.get_datasource_by_group_name(name)

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

    async def get_semantic_configs_by_group_id_async(self, group_id: str):
        return self.get_semantic_configs_by_group_id(group_id)

    async def get_column_dicts_by_group_id_async(self, group_id: str):
        return {}

    async def get_column_dicts_by_table_async(self, group_id: str, table_name: str):
        return {}

    def upsert_semantic_config(self, datasource_id: str, config: dict):
        group_id = config.get("group_id")
        config_id = config.get("id") or config.get("semantic_config_id")
        items = self.semantic_configs.setdefault(group_id, [])
        for index, item in enumerate(items):
            if item.get("id") == config_id or item.get("semantic_config_id") == config_id:
                items[index] = dict(config)
                return True
        items.append(dict(config))
        return True

    async def upsert_semantic_config_async(self, datasource_id: str, config: dict):
        return self.upsert_semantic_config(datasource_id, config)

    def clear_all_default_datasources(self, update_by: str = "", update_by_name: str = ""):
        self.cleared_default_calls.append(update_by)
        for item in self.datasources.values():
            item["is_default"] = False

    async def clear_all_default_datasources_async(self, update_by: str = "", update_by_name: str = ""):
        self.clear_all_default_datasources(update_by=update_by, update_by_name=update_by_name)

    def update_datasource_group(self, group_id: str, **kwargs):
        self.updated_groups.append((group_id, kwargs))
        for item in self.datasources.values():
            if item["group_id"] == group_id:
                if "is_default" in kwargs:
                    item["is_default"] = bool(kwargs["is_default"])
                if "mapped_modules" in kwargs:
                    item["mapped_modules"] = kwargs["mapped_modules"]

    async def update_datasource_group_async(self, group_id: str, **kwargs):
        self.update_datasource_group(group_id, **kwargs)

    def get_default_datasource(self):
        for item in self.datasources.values():
            if item.get("is_default"):
                return item
        return None

    async def get_default_datasource_async(self):
        return self.get_default_datasource()

    def get_group_member_count(self, datasource_id: str):
        return self.group_member_count_map.get(datasource_id, 0)

    def soft_delete_group(self, group_id: str, update_by: str = "", update_by_name: str = ""):
        self.soft_deleted_groups.append(group_id)
        for datasource_id, item in list(self.datasources.items()):
            if item["group_id"] == group_id:
                del self.datasources[datasource_id]

    def soft_delete_datasource(self, datasource_id: str):
        self.soft_deleted_datasources.append(datasource_id)

    async def soft_delete_group_async(self, group_id: str, update_by: str = "", update_by_name: str = ""):
        self.soft_delete_group(group_id, update_by=update_by, update_by_name=update_by_name)

    def get_table_group_relations(self, group_id: str):
        return [
            {"table_group_name": "订单域", "table_name": "orders"},
            {"table_group_name": "订单域", "table_name": "orders"},
            {"table_group_name": "", "table_name": "ignored"},
        ] if group_id == "group_ds_a" else []

    async def get_table_group_relations_async(self, group_id: str):
        return self.get_table_group_relations(group_id)

    def replace_table_group_relations(self, group_id: str, groups: list[dict], create_by: str = "", update_by: str = "", create_by_name: str = "", update_by_name: str = ""):
        self.replaced_table_groups = {"group_id": group_id, "groups": groups, "create_by": create_by, "update_by": update_by}

    async def replace_table_group_relations_async(self, group_id: str, groups: list[dict], create_by: str = "", update_by: str = "", create_by_name: str = "", update_by_name: str = ""):
        self.replace_table_group_relations(group_id, groups, create_by=create_by, update_by=update_by, create_by_name=create_by_name, update_by_name=update_by_name)

    def soft_delete_semantic_config(self, config_id: str, update_by: str = "", update_by_name: str = ""):
        self.deleted_config_id = config_id

    async def soft_delete_semantic_config_async(self, config_id: str, update_by: str = "", update_by_name: str = ""):
        self.soft_delete_semantic_config(config_id, update_by=update_by, update_by_name=update_by_name)


def _build_manager(fake_store: _FakeStore) -> DataSourceManager:
    manager = DataSourceManager.__new__(DataSourceManager)
    manager.base_dir = None
    manager.db_store = fake_store
    return manager


def test_create_semantic_config_rejects_active_duplicate_name():
    store = _FakeStore()
    store.semantic_configs["group_ds_a"] = [{"id": "sc_old", "name": "订单分析基线", "enabled": True}]
    manager = _build_manager(store)

    with pytest.raises(ValueError, match="语义配置名称已存在"):
        asyncio.run(manager.create_semantic_config_async("ds_a", "订单分析基线"))


def test_create_semantic_config_allows_name_after_soft_delete():
    store = _FakeStore()
    store.semantic_configs["group_ds_a"] = []
    manager = _build_manager(store)

    config = asyncio.run(manager.create_semantic_config_async("ds_a", "订单分析基线"))

    assert config["name"] == "订单分析基线"


class _SaveStore:
    def __init__(self):
        self.saved_kwargs = None
        self.updated_kwargs = None

    def _get_datasource_payload(self, datasource_id: str):
        return {
            "id": datasource_id,
            "group_id": datasource_id,
            "template_datasource_id": "ORG_A",
            "group_name": "测试组",
            "ds_description": "",
            "source_type": "hive",
            "query_executor_type": "http",
            "source_config": {"host": "127.0.0.1", "port": 10000, "database": "dw"},
            "member_sources": [
                {
                    "member_datasource_id": "ORG_A",
                    "org_name": "机构A",
                    "org_code": "ORG_A",
                    "source_type": "hive",
                    "host": "127.0.0.1",
                    "port": 10000,
                    "database": "dw",
                },
                {
                    "member_datasource_id": "ORG_B",
                    "org_name": "机构B",
                    "org_code": "ORG_B",
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

    async def get_datasource_async(self, datasource_id: str):
        return self._get_datasource_payload(datasource_id)

    async def get_semantic_configs_by_group_id_async(self, group_id: str):
        return []

    def transaction_async(self):
        return _AsyncNullContext()

    async def upsert_datasource_group_async(self, **kwargs):
        self.saved_kwargs = kwargs

    async def update_datasource_group_async(self, **kwargs):
        self.updated_kwargs = kwargs
        return None

    async def replace_datasource_tables_async(self, **kwargs):
        return None


def test_set_default_datasource_updates_group_flag():
    fake_store = _FakeStore()
    manager = _build_manager(fake_store)

    assert asyncio.run(manager.set_default_datasource_async("ds_b")) is True
    assert fake_store.datasources["ds_b"]["is_default"] is True
    assert fake_store.datasources["ds_a"]["is_default"] is False
    assert fake_store.updated_groups[-1][0] == "group_ds_b"


def test_clear_default_datasource_updates_group_flag():
    fake_store = _FakeStore()
    manager = _build_manager(fake_store)

    assert asyncio.run(manager.clear_default_datasource_async("ds_a")) is True
    assert fake_store.datasources["ds_a"]["is_default"] is False
    assert fake_store.updated_groups[-1][0] == "group_ds_a"


def test_get_all_datasources_does_not_attach_semantic_configs():
    fake_store = _FakeStore()
    manager = _build_manager(fake_store)

    datasources = asyncio.run(manager.get_all_datasources_async())

    assert len(datasources) == 2
    assert all("semantic_configs" not in item for item in datasources)


def test_get_all_datasources_supports_lightweight_mode():
    fake_store = _FakeStore()
    manager = _build_manager(fake_store)

    datasources = asyncio.run(manager.get_all_datasources_async(include_tables=False))

    assert len(datasources) == 2
    assert all(item["ds_tables"] == [] for item in datasources)


def test_get_semantic_config_enriches_source_type_from_datasource():
    fake_store = _FakeStore()
    fake_store.datasources["ds_a"]["source_type"] = "hive"
    fake_store.semantic_configs["group_ds_a"] = [{"id": "sc_a", "metrics": [], "dimensions": [], "relationships": []}]
    manager = _build_manager(fake_store)

    config = asyncio.run(manager.get_semantic_config_async("ds_a", "sc_a"))

    assert config["source_type"] == "hive"


def test_runtime_semantic_config_merges_column_dict_without_persisting():
    class DictStore(_FakeStore):
        async def get_column_dicts_by_group_id_async(self, group_id: str):
            assert group_id == "group_ds_a"
            return {
                ("orders", "status"): {
                    "enum_values": [{"value": "1", "label": "有效"}, {"value": "0", "label": "无效"}],
                    "synonyms": ["订单状态"],
                    "storage_format": "",
                    "unit": "",
                },
                ("orders", "amount"): {
                    "enum_values": [],
                    "synonyms": ["销售额"],
                    "storage_format": "",
                    "unit": "元",
                },
            }

    fake_store = DictStore()
    fake_store.datasources["ds_a"]["ds_tables"] = [{"table_name": "orders", "columns": [{"name": "status"}, {"name": "amount"}]}]
    fake_store.semantic_configs["group_ds_a"] = [{
        "id": "sc_a",
        "group_id": "group_ds_a",
        "metrics": [{"name": "销售额", "table": "orders", "expression": "SUM(amount)", "metadata_supplement": "统计实付金额"}],
        "dimensions": [{"name": "订单状态", "table": "orders", "column": "status"}],
        "relationships": [],
    }]
    manager = _build_manager(fake_store)

    normal_config = asyncio.run(manager.get_semantic_config_async("ds_a", "sc_a"))
    runtime_config = asyncio.run(manager.get_runtime_semantic_config_async("ds_a", "sc_a"))

    assert normal_config["dimensions"][0].get("allowed_values") is None
    assert runtime_config["dimensions"][0]["allowed_values"] == ["1", "0"]
    assert "枚举值：1=有效；0=无效" in runtime_config["dimensions"][0]["metadata_supplement"]
    assert "同义词：订单状态" not in runtime_config["dimensions"][0]["metadata_supplement"]
    assert runtime_config["metrics"][0]["metadata_supplement"] == "统计实付金额\n单位：元"
    assert "allowed_values" not in fake_store.semantic_configs["group_ds_a"][0]["dimensions"][0]


def test_runtime_semantic_configs_merge_column_dicts_for_selector_context():
    class DictStore(_FakeStore):
        async def get_column_dicts_by_group_id_async(self, group_id: str):
            assert group_id == "group_ds_a"
            return {
                ("orders", "status"): {
                    "enum_values": [{"value": "1", "label": "有效"}, {"value": "0", "label": "无效"}],
                    "synonyms": [],
                    "storage_format": "tinyint",
                    "unit": "",
                },
                ("orders", "amount"): {
                    "enum_values": [],
                    "synonyms": [],
                    "storage_format": "",
                    "unit": "元",
                },
            }

    fake_store = DictStore()
    fake_store.semantic_configs["group_ds_a"] = [{
        "id": "sc_a",
        "group_id": "group_ds_a",
        "enabled": True,
        "metrics": [{"name": "销售额", "table": "orders", "expression": "SUM(amount)"}],
        "dimensions": [{"name": "订单状态", "table": "orders", "column": "status"}],
        "relationships": [],
    }]
    manager = _build_manager(fake_store)

    configs = asyncio.run(manager.get_runtime_semantic_configs_async("ds_a"))

    assert configs[0]["dimensions"][0]["allowed_values"] == ["1", "0"]
    assert configs[0]["dimensions"][0]["metadata_supplement"] == "枚举值：1=有效；0=无效\n存储格式：tinyint"
    assert configs[0]["metrics"][0]["metadata_supplement"] == "单位：元"
    assert "allowed_values" not in fake_store.semantic_configs["group_ds_a"][0]["dimensions"][0]


def test_runtime_semantic_configs_fallback_when_column_dict_read_fails():
    class FailingDictStore(_FakeStore):
        async def get_column_dicts_by_group_id_async(self, group_id: str):
            raise RuntimeError("dict unavailable")

    fake_store = FailingDictStore()
    fake_store.semantic_configs["group_ds_a"] = [{
        "id": "sc_a",
        "group_id": "group_ds_a",
        "enabled": True,
        "metrics": [],
        "dimensions": [{"name": "订单状态", "table": "orders", "column": "status"}],
        "relationships": [],
    }]
    manager = _build_manager(fake_store)

    configs = asyncio.run(manager.get_runtime_semantic_configs_async("ds_a"))

    assert configs == fake_store.semantic_configs["group_ds_a"]
    assert "allowed_values" not in configs[0]["dimensions"][0]


def test_datasource_manager_column_dict_helper_branches():
    manager = DataSourceManager.__new__(DataSourceManager)

    assert manager._extract_metric_source_column({"expression": "SUM(orders.amount)"}) == "amount"
    assert manager._extract_metric_source_column({"expression": "`orders.status`"}) == "status"
    assert manager._extract_metric_source_column({"expression": "amount + tax", "field": "`orders.amount`"}) == "amount"
    assert manager._extract_metric_source_column({"expression": "amount + tax"}) == ""
    assert manager._format_column_dict_supplement({"enum_values": [], "synonyms": [], "storage_format": "yyyyMMdd", "unit": "万元"}) == "存储格式：yyyyMMdd\n单位：万元"
    assert manager._merge_metadata_supplement("", "枚举值：1=有效") == "枚举值：1=有效"
    assert manager._merge_metadata_supplement("已有说明", "") == "已有说明"
    assert manager._merge_metadata_supplement("已有说明\n枚举值：1=有效", "枚举值：1=有效") == "已有说明\n枚举值：1=有效"
    assert manager._merge_metadata_supplement("已有说明", "单位：元") == "已有说明\n单位：元"

    merged = manager._merge_column_dict_into_semantic_config(
        {
            "dimensions": [
                {"table": "orders", "column": "status", "allowed_values": ["Y"], "metadata_supplement": "已有说明"},
                {"table": "orders", "field_name": "city"},
            ],
            "metrics": [
                {"table": "orders", "expression": "SUM(amount)"},
                {"table": "orders", "expression": "amount + tax"},
            ],
        },
        {
            ("orders", "status"): {"enum_values": [{"value": "1", "label": "有效"}], "synonyms": [], "storage_format": "tinyint", "unit": ""},
            ("orders", "amount"): {"enum_values": [], "synonyms": [], "storage_format": "", "unit": "元"},
        },
    )

    assert merged["dimensions"][0]["allowed_values"] == ["Y"]
    assert merged["dimensions"][0]["metadata_supplement"] == "已有说明\n枚举值：1=有效\n存储格式：tinyint"
    assert "metadata_supplement" not in merged["dimensions"][1]
    assert merged["metrics"][0]["metadata_supplement"] == "单位：元"
    assert "metadata_supplement" not in merged["metrics"][1]

    empty_supplement_merged = manager._merge_column_dict_into_semantic_config(
        {
            "dimensions": [{"table": "orders", "column": "status"}],
            "metrics": [{"table": "orders", "expression": "SUM(amount)"}],
        },
        {
            ("orders", "status"): {"enum_values": [], "storage_format": "", "unit": ""},
            ("orders", "amount"): {"enum_values": [], "storage_format": "", "unit": ""},
        },
    )
    assert "metadata_supplement" not in empty_supplement_merged["dimensions"][0]
    assert "metadata_supplement" not in empty_supplement_merged["metrics"][0]


def test_datasource_manager_column_dict_async_boundaries():
    class DictStore(_FakeStore):
        async def get_column_dict_async(self, group_id: str, table_name: str, column_name: str):
            return {"enum_values": [{"value": "1", "label": "有效"}], "synonyms": [], "storage_format": "", "unit": ""}

        async def get_column_dicts_by_table_async(self, group_id: str, table_name: str):
            return {
                "status": {"enum_values": [{"value": "1", "label": "有效"}], "synonyms": [], "storage_format": "", "unit": ""},
                "ghost": {"enum_values": [{"value": "X", "label": "不存在"}], "synonyms": [], "storage_format": "", "unit": ""},
            }

        async def replace_column_dict_async(self, group_id: str, table_name: str, column_name: str, column_dict: dict, **kwargs):
            self.saved_column_dict = {"group_id": group_id, "table_name": table_name, "column_name": column_name, "column_dict": column_dict, **kwargs}
            return dict(column_dict)

    store = DictStore()
    store.datasources["ds_a"]["ds_tables"] = [
        {"table_name": "orders", "columns": [{"name": "status"}, {"name": ""}]},
    ]
    manager = _build_manager(store)

    async def run_checks():
        return (
            await manager.get_column_dict_async("missing", "orders", "status"),
            await manager.get_column_dict_async("ds_a", "orders", "missing"),
            await manager.get_column_dict_async("ds_a", "orders", "status"),
            await manager.get_table_column_dicts_async("ds_a", ""),
            await manager.get_table_column_dicts_async("missing", "orders"),
            await manager.get_table_column_dicts_async("ds_a", "missing"),
            await manager.get_table_column_dicts_async("ds_a", "orders"),
            await manager.replace_column_dict_async("ds_a", "orders", "missing", {}),
            await manager.replace_column_dict_async("ds_a", "orders", "status", {"unit": "元"}, operator="u1", operator_name="用户1"),
        )

    missing_ds, missing_column, existing, blank_table, missing_table_ds, missing_table, table_dicts, replace_missing, saved = asyncio.run(run_checks())

    assert missing_ds is None
    assert missing_column is None
    assert existing["enum_values"] == [{"value": "1", "label": "有效"}]
    assert blank_table is None
    assert missing_table_ds is None
    assert missing_table is None
    assert list(table_dicts) == ["status"]
    assert replace_missing is None
    assert saved == {"unit": "元"}
    assert store.saved_column_dict["create_by"] == "u1"
    assert store.saved_column_dict["update_by_name"] == "用户1"


def test_datasource_manager_runtime_column_dict_fallbacks():
    manager = DataSourceManager.__new__(DataSourceManager)

    class NoGroupStore:
        async def get_datasource_async(self, datasource_id: str):
            return {}

    manager.db_store = NoGroupStore()
    config = {"id": "sc_a", "metrics": [], "dimensions": []}
    assert asyncio.run(manager._enrich_semantic_config_column_dict_async("ds_a", config)) is config

    class NoColumnDictMethodStore:
        async def get_datasource_async(self, datasource_id: str):
            return {"group_id": "group_a"}

    manager.db_store = NoColumnDictMethodStore()
    assert asyncio.run(manager._enrich_semantic_config_column_dict_async("ds_a", config)) is config

    manager = _build_manager(_FakeStore())
    assert asyncio.run(manager.get_runtime_semantic_config_async("ds_a", "missing")) is None
    assert asyncio.run(manager._enrich_semantic_config_column_dict_async("ds_a", {"id": "sc_a", "group_id": "group_ds_a"})) == {"id": "sc_a", "group_id": "group_ds_a"}


def test_has_datasource_delegates_to_store():
    fake_store = _FakeStore()
    manager = _build_manager(fake_store)

    assert asyncio.run(manager.has_datasource_async("ds_a")) is True
    assert asyncio.run(manager.has_datasource_async("missing")) is False


def test_delete_datasource_only_soft_deletes_group_when_member_source_is_shared():
    fake_store = _FakeStore()
    manager = _build_manager(fake_store)

    assert asyncio.run(manager.delete_datasource_async("ds_a")) is True
    assert fake_store.soft_deleted_groups == ["group_ds_a"]
    assert fake_store.soft_deleted_datasources == []
    assert "ds_b" in fake_store.datasources


def test_get_execution_datasources_injects_unified_mysql_credentials(monkeypatch):
    fake_store = _FakeStore()
    fake_store.datasources["ds_a"]["member_sources"] = [
        {
            "member_datasource_id": "phy_a",
            "org_name": "成员A",
            "org_code": "ORG_A",
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

    execution_targets = asyncio.run(manager.get_execution_datasources_async("ds_a"))

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
            "org_name": "hive-localhost",
            "org_code": "ORG_HIVE",
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

    execution_targets = asyncio.run(manager.get_execution_datasources_async("ds_a"))

    assert len(execution_targets) == 1
    assert execution_targets[0]["query_executor_type"] == "http"
    assert execution_targets[0]["member_name"] == "hive-localhost"
    assert execution_targets[0]["source_config"]["org_code"] == "ORG_HIVE"
    assert "username" not in execution_targets[0]["source_config"]


def test_get_execution_datasources_keeps_mysql_http_executor_type(monkeypatch):
    fake_store = _FakeStore()
    fake_store.datasources["ds_a"]["source_type"] = "mysql"
    fake_store.datasources["ds_a"]["query_executor_type"] = "http"
    fake_store.datasources["ds_a"]["member_sources"] = [
        {
            "member_datasource_id": "mysql-http-a",
            "org_name": "mysql-http-a",
            "org_code": "ORG_MYSQL",
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

    execution_targets = asyncio.run(manager.get_execution_datasources_async("ds_a"))

    assert len(execution_targets) == 1
    assert execution_targets[0]["query_executor_type"] == "http"
    assert execution_targets[0]["member_name"] == "mysql-http-a"
    assert execution_targets[0]["source_config"]["org_code"] == "ORG_MYSQL"
    assert "user" not in execution_targets[0]["source_config"]


def test_save_datasource_rejects_duplicate_member_connections():
    manager = DataSourceManager.__new__(DataSourceManager)
    manager.base_dir = None
    manager.db_store = None

    with pytest.raises(ValueError, match="第 2 个机构编码重复"):
        asyncio.run(manager._save_datasource_async(
            datasource_id="ds_test",
            group_name="测试组",
            group_desc="",
            source_type="mysql",
            query_executor_type="direct",
            template_source="127.0.0.1:3306:test_db",
            member_source_configs=[
                {
                    "org_name": "模板库",
                    "org_code": "ORG_A",
                    "host": "127.0.0.1",
                    "port": 3306,
                    "database": "test_db",
                },
                {
                    "org_name": "重复库",
                    "org_code": "ORG_A",
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
        ))


def test_save_datasource_rejects_missing_member_name():
    manager = DataSourceManager.__new__(DataSourceManager)
    manager.base_dir = None
    manager.db_store = None

    with pytest.raises(ValueError, match="机构名称不能为空"):
        asyncio.run(manager._save_datasource_async(
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
        ))


def test_save_datasource_keeps_hive_http_members_with_shared_connection():
    fake_store = _SaveStore()
    manager = _build_manager(fake_store)

    saved = asyncio.run(manager._save_datasource_async(
        datasource_id="ds_hive_http",
        group_name="HiveHTTP组",
        group_desc="",
        source_type="hive",
        query_executor_type="http",
        template_source="127.0.0.1:10000:dw",
        member_source_configs=[
            {
                "org_name": "机构A",
                "org_code": "ORG_A",
                "host": "127.0.0.1",
                "port": 10000,
                "database": "dw",
            },
            {
                "org_name": "机构B",
                "org_code": "ORG_B",
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
    ))

    assert fake_store.saved_kwargs is not None
    assert fake_store.saved_kwargs["member_sources_text"] == "机构A|ORG_A|http|127.0.0.1:10000:dw\n机构B|ORG_B|http|127.0.0.1:10000:dw"
    assert [item["member_datasource_id"] for item in saved["member_sources"]] == ["ORG_A", "ORG_B"]


def test_save_datasource_keeps_mysql_http_members_with_shared_connection():
    fake_store = _SaveStore()
    manager = _build_manager(fake_store)

    saved = asyncio.run(manager._save_datasource_async(
        datasource_id="ds_mysql_http",
        group_name="MySQLHTTP组",
        group_desc="",
        source_type="mysql",
        query_executor_type="http",
        template_source="127.0.0.1:3306:test_db",
        member_source_configs=[
            {
                "org_name": "机构A",
                "org_code": "ORG_A",
                "host": "127.0.0.1",
                "port": 3306,
                "database": "test_db",
            },
            {
                "org_name": "机构B",
                "org_code": "ORG_B",
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
    ))

    assert fake_store.saved_kwargs is not None
    assert fake_store.saved_kwargs["member_sources_text"] == "机构A|ORG_A|http|127.0.0.1:3306:test_db\n机构B|ORG_B|http|127.0.0.1:3306:test_db"


def test_save_datasource_rejects_duplicate_hive_http_names():
    manager = DataSourceManager.__new__(DataSourceManager)
    manager.base_dir = None
    manager.db_store = None

    with pytest.raises(ValueError, match="第 2 个机构编码重复"):
        asyncio.run(manager._save_datasource_async(
            datasource_id="ds_hive_http",
            group_name="HiveHTTP组",
            group_desc="",
            source_type="hive",
            query_executor_type="http",
            template_source="127.0.0.1:10000:dw",
            member_source_configs=[
                {
                    "org_name": "机构A",
                    "org_code": "ORG_A",
                    "host": "127.0.0.1",
                    "port": 10000,
                    "database": "dw",
                },
                {
                    "org_name": "机构A重名",
                    "org_code": "ORG_A",
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
        ))


def test_get_execution_datasources_supports_hive_http_org_code_selection(monkeypatch):
    fake_store = _FakeStore()
    fake_store.datasources["ds_a"]["source_type"] = "hive"
    fake_store.datasources["ds_a"]["query_executor_type"] = "http"
    fake_store.datasources["ds_a"]["template_datasource_id"] = "ORG_A"
    fake_store.datasources["ds_a"]["member_sources"] = [
        {
            "member_datasource_id": "ORG_A",
            "org_name": "机构A",
            "org_code": "ORG_A",
            "source_type": "hive",
            "host": "127.0.0.1",
            "port": 10000,
            "database": "dw",
        },
        {
            "member_datasource_id": "ORG_B",
            "org_name": "机构B",
            "org_code": "ORG_B",
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

    execution_targets = asyncio.run(manager.get_execution_datasources_async("ds_a", ["ORG_B"]))

    assert len(execution_targets) == 1
    assert execution_targets[0]["member_datasource_id"] == "ORG_B"
    assert execution_targets[0]["member_name"] == "机构B"
    assert execution_targets[0]["source_config"]["org_code"] == "ORG_B"


def test_update_datasource_preserves_template_member_name_when_refreshing_tables():
    fake_store = _SaveStore()
    manager = _build_manager(fake_store)

    asyncio.run(manager.update_datasource_async(
        "ds_refresh",
        tables=[{"table_name": "orders", "description": "orders", "columns": []}],
        source_config={"host": "127.0.0.1", "port": 10000, "database": "dw"},
    ))

    assert fake_store.saved_kwargs["template_source"] == "机构A|ORG_A|http|127.0.0.1:10000:dw"
    assert fake_store.saved_kwargs["member_sources_text"].splitlines()[0] == "机构A|ORG_A|http|127.0.0.1:10000:dw"


def test_validate_member_source_configs_rejects_duplicate_org_code():
    configs = [
        {"org_name": "机构A", "org_code": "ORG_A", "query_executor_type": "direct", "host": "127.0.0.1", "port": 3306, "database": "demo"},
        {"org_name": "机构A2", "org_code": "ORG_A", "query_executor_type": "http", "host": "127.0.0.2", "port": 3306, "database": "demo"},
    ]

    with pytest.raises(ValueError, match="机构编码重复"):
        _validate_member_source_configs(configs, "mysql", "direct")


def test_merge_member_source_lines_keeps_new_template_first_and_existing_members():
    current = {
        "template_datasource_id": "ORG_OLD",
        "member_sources": [
            {"org_name": "旧机构", "org_code": "ORG_OLD", "query_executor_type": "direct", "host": "127.0.0.1", "port": 3306, "database": "demo"},
            {"org_name": "机构B", "org_code": "ORG_B", "query_executor_type": "http", "host": "127.0.0.2", "port": 3306, "database": "demo"},
        ],
    }
    template = {"org_name": "新机构", "org_code": "ORG_NEW", "query_executor_type": "direct", "host": "127.0.0.3", "port": 3306, "database": "demo"}

    merged = _merge_member_source_lines(current, template, "mysql", "direct", member_source_configs=None)

    assert [item["org_code"] for item in merged] == ["ORG_NEW", "ORG_B"]


def test_build_execution_datasource_targets_defaults_template_and_adds_credentials(monkeypatch):
    monkeypatch.setattr("apps.chat_data_app.services.datasource_manager.resolve_mysql_credentials", lambda cfg: {"user": "mysql_user", "password": "mysql_pwd"})
    manager = DataSourceManager.__new__(DataSourceManager)
    datasource = {
        "template_datasource_id": "ORG_A",
        "source_type": "mysql",
        "query_executor_type": "direct",
        "member_sources": [
            {"member_datasource_id": "ORG_A", "org_name": "机构A", "org_code": "ORG_A", "source_type": "mysql", "query_executor_type": "direct", "host": "127.0.0.1", "port": 3306, "database": "demo"},
            {"member_datasource_id": "ORG_B", "org_name": "机构B", "org_code": "ORG_B", "source_type": "mysql", "query_executor_type": "http", "host": "127.0.0.2", "port": 3306, "database": "demo"},
        ],
    }

    default_target = manager._build_execution_datasource_targets(datasource, None)[0]
    http_target = manager._build_execution_datasource_targets(datasource, ["ORG_B"])[0]

    assert default_target["member_datasource_id"] == "ORG_A"
    assert default_target["source_config"]["user"] == "mysql_user"
    assert http_target["query_executor_type"] == "http"
    assert http_target["source_config"]["org_code"] == "ORG_B"
    assert http_target["source_config"]["org_name"] == "机构B"


def test_build_execution_datasource_targets_rejects_invalid_members():
    manager = DataSourceManager.__new__(DataSourceManager)

    cases = [
        ({}, None, "没有可执行的数据源"),
        ({"member_sources": [{"member_datasource_id": ""}]}, None, "成员配置无效"),
        ({"member_sources": [{"member_datasource_id": "m1"}]}, ["m2"], "成员数据源不存在"),
        ({"source_type": "hive", "member_sources": [{"member_datasource_id": "m1", "query_executor_type": "http", "host": "h", "port": 1, "database": "d"}]}, ["m1"], "缺少机构编码"),
    ]
    for datasource, selected, expected in cases:
        with pytest.raises(ValueError, match=expected):
            manager._build_execution_datasource_targets(datasource, selected)


def test_replace_table_groups_filters_missing_tables_and_dedupes():
    manager = DataSourceManager.__new__(DataSourceManager)
    captured = {}

    class FakeStore:
        async def get_datasource_async(self, datasource_id):
            return {"group_id": "group_a", "ds_tables": [{"table_name": "orders"}, {"table_name": "items"}]}

        async def replace_table_group_relations_async(self, group_id, groups, create_by="", update_by="", create_by_name="", update_by_name=""):
            captured.update({"group_id": group_id, "groups": groups, "create_by": create_by, "update_by": update_by})

    manager.db_store = FakeStore()

    assert asyncio.run(manager.replace_table_groups_async("ds_a", [
        {"table_group_name": "订单域", "table_names": ["orders", "missing", "orders"]},
        {"table_group_name": "", "table_names": ["items"]},
    ], operator="u1")) is True
    assert captured == {
        "group_id": "group_a",
        "groups": [{"table_group_name": "订单域", "table_names": ["orders"]}],
        "create_by": "u1",
        "update_by": "u1",
    }


def test_create_and_update_semantic_config_enforce_name_scope_and_relationships():
    manager = DataSourceManager.__new__(DataSourceManager)
    configs = []
    datasource = {"group_id": "group_a", "semantic_configs": configs}

    class FakeStore:
        async def upsert_semantic_config_async(self, datasource_id, config):
            configs[:] = [item for item in configs if item["id"] != config["id"]]
            configs.append(dict(config))

    manager.db_store = FakeStore()
    manager.get_datasource_async = lambda _datasource_id: asyncio.sleep(0, result=datasource)
    manager.get_semantic_configs_async = lambda _datasource_id, user_id="": asyncio.sleep(0, result=list(configs))
    manager.get_semantic_config_async = lambda _datasource_id, config_id: asyncio.sleep(0, result=next((item for item in configs if item["id"] == config_id), None))

    created = asyncio.run(manager.create_semantic_config_async(
        "ds_a",
        "订单分析",
        relationships=[{"left_table": "orders", "right_table": "items", "left_column": "order_id", "right_column": "order_id"}],
        create_by="u1",
    ))
    updated = asyncio.run(manager.update_semantic_config_async("ds_a", created["id"], name="订单分析新版", enabled=False, update_by="u2"))

    assert created["scope_type"] == "private"
    assert created["relationships"][0]["join_conditions"] == [{"left_column": "order_id", "right_column": "order_id"}]
    assert updated["name"] == "订单分析新版"
    assert updated["enabled"] is False
    assert updated["update_by"] == "u2"


def test_save_datasource_persists_group_and_tables_in_transaction():
    manager = DataSourceManager.__new__(DataSourceManager)
    calls = []

    class FakeStore:
        def transaction_async(self):
            return _AsyncNullContext()

        async def upsert_datasource_group_async(self, **kwargs):
            calls.append(("upsert_group", kwargs))

        async def update_datasource_group_async(self, **kwargs):
            calls.append(("update_group", kwargs))

        async def replace_datasource_tables_async(self, **kwargs):
            calls.append(("replace_tables", kwargs))

    manager.db_store = FakeStore()
    manager._load_datasource_async = lambda datasource_id: asyncio.sleep(0, result={"id": datasource_id})

    result = asyncio.run(manager._save_datasource_async(
        datasource_id="ds_a",
        group_name="订单组",
        group_desc="描述",
        source_type="mysql",
        query_executor_type="direct",
        template_source="127.0.0.1:3306:demo",
        member_source_configs=[{"org_name": "机构A", "org_code": "ORG_A", "query_executor_type": "direct", "host": "127.0.0.1", "port": 3306, "database": "demo"}],
        tables=[{"table_name": "orders"}],
        mapped_modules=["执法检查"],
        is_default=True,
        create_by="u1",
        update_by="u2",
    ))

    assert result == {"id": "ds_a"}
    assert calls[0][0] == "upsert_group"
    assert calls[0][1]["template_source"] == "机构A|ORG_A|direct|127.0.0.1:3306:demo"
    assert calls[1][1]["mapped_modules"] == ["执法检查"]
    assert calls[2][1]["tables"] == [{"table_name": "orders"}]


class _AsyncNullContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return None


def test_datasource_manager_table_groups_and_visibility_filters():
    store = _FakeStore()
    store.datasources["ds_a"]["ds_tables"] = [{"table_name": "orders"}, {"table_name": "items"}]
    store.semantic_configs["group_ds_a"] = [
        {"id": "public", "name": "公共", "scope_type": "public", "enabled": True},
        {"id": "mine", "name": "我的", "scope_type": "private", "create_by": "u1", "enabled": False},
        {"id": "other", "name": "别人的", "scope_type": "private", "create_by": "u2", "enabled": True},
    ]
    manager = _build_manager(store)

    assert asyncio.run(manager.get_table_groups_async("ds_a")) == [{"table_group_name": "订单域", "table_names": ["orders"]}]
    assert asyncio.run(manager.replace_table_groups_async("ds_a", [
        {"table_group_name": "订单域", "table_names": ["orders", "missing", "orders"]},
    ], operator="u1")) is True
    assert store.replaced_table_groups["groups"] == [{"table_group_name": "订单域", "table_names": ["orders"]}]
    assert [item["id"] for item in asyncio.run(manager.get_semantic_configs_async("ds_a", user_id="u1"))] == ["public", "mine"]
    enabled_configs = [item for item in asyncio.run(manager.get_semantic_configs_async("ds_a")) if item.get("enabled", True)]
    assert [item["id"] for item in enabled_configs] == ["public", "other"]
    tables = asyncio.run(manager.get_datasource_async("ds_a"))["ds_tables"]
    assert next((table for table in tables if table.get("table_name") == "items"), None) == {"table_name": "items"}
    assert next((table for table in tables if table.get("table_name") == "missing"), None) is None


def test_datasource_manager_semantic_config_validation_delete_and_default_failures():
    store = _FakeStore()
    manager = _build_manager(store)

    assert asyncio.run(manager.create_semantic_config_async("missing", "订单分析")) is None
    with pytest.raises(ValueError, match="语义配置名称不能为空"):
        asyncio.run(manager.create_semantic_config_async("ds_a", ""))
    with pytest.raises(ValueError, match="只能是 public 或 private"):
        asyncio.run(manager.create_semantic_config_async("ds_a", "订单分析", scope_type="bad"))

    store.semantic_configs["group_ds_a"] = [
        {"id": "sc_a", "name": "订单分析", "scope_type": "public", "enabled": True, "metrics": [], "dimensions": [], "relationships": []},
        {"id": "sc_b", "name": "销售分析", "scope_type": "public", "enabled": True, "metrics": [], "dimensions": [], "relationships": []},
    ]
    with pytest.raises(ValueError, match="语义配置名称已存在"):
        asyncio.run(manager.update_semantic_config_async("ds_a", "sc_a", name="销售分析"))
    with pytest.raises(ValueError, match="语义配置名称不能为空"):
        asyncio.run(manager.update_semantic_config_async("ds_a", "sc_a", name=""))

    assert asyncio.run(manager.update_semantic_config_async("ds_a", "missing", name="x")) is None
    assert asyncio.run(manager.delete_semantic_config_async("ds_a", "missing")) is False
    assert asyncio.run(manager.delete_semantic_config_async("ds_a", "sc_a")) is True
    assert store.deleted_config_id == "sc_a"
    assert asyncio.run(manager.set_default_datasource_async("missing")) is False
    store.datasources["ds_a"]["is_default"] = False
    assert asyncio.run(manager.clear_default_datasource_async("ds_a")) is False


def test_datasource_manager_create_update_group_from_sources(monkeypatch):
    manager = DataSourceManager.__new__(DataSourceManager)
    saved_calls = []
    current = {
        "id": "ds_a",
        "group_id": "group_a",
        "group_name": "旧名称",
        "ds_description": "旧描述",
        "source_type": "mysql",
        "query_executor_type": "direct",
        "template_datasource_id": "ORG_A",
        "template_source": "机构A|ORG_A|direct|127.0.0.1:3306:old",
        "member_sources": [
            {"member_datasource_id": "ORG_A", "org_name": "机构A", "org_code": "ORG_A", "query_executor_type": "direct", "host": "127.0.0.1", "port": 3306, "database": "old"},
            {"member_datasource_id": "ORG_B", "org_name": "机构B", "org_code": "ORG_B", "query_executor_type": "direct", "host": "127.0.0.2", "port": 3306, "database": "old"},
        ],
        "ds_tables": [{"table_name": "orders"}],
        "mapped_modules": ["执法检查"],
        "is_default": True,
        "create_by": "creator",
        "update_by": "old_updater",
    }

    async def fake_save(**kwargs):
        saved_calls.append(kwargs)
        return {"id": kwargs["datasource_id"], "name": kwargs["group_name"]}

    monkeypatch.setattr("apps.chat_data_app.services.datasource_manager.datetime", _FixedDatetime)
    manager._save_datasource_async = fake_save
    manager.get_datasource_async = lambda datasource_id: asyncio.sleep(0, result=current if datasource_id == "ds_a" else None)

    created = asyncio.run(manager.create_datasource_group_from_sources_async(
        group_name="新组",
        group_desc="描述",
        source_type="mysql",
        template_source_config={"org_name": "机构A", "org_code": "ORG_A", "host": "127.0.0.1", "port": 3306, "database": "demo"},
        member_source_configs=[{"org_name": "机构B", "org_code": "ORG_B", "host": "127.0.0.2", "port": 3306, "database": "demo"}],
        tables=[{"table_name": "orders"}],
        create_by="u1",
    ))
    updated = asyncio.run(manager.update_datasource_group_from_sources_async(
        "ds_a",
        "更新组",
        "新描述",
        "mysql",
        {"org_name": "机构A", "org_code": "ORG_A", "host": "127.0.0.3", "port": 3306, "database": "new"},
        member_source_configs=None,
        update_by="u2",
    ))

    assert created["id"].startswith("ds_20260602101112_345678")
    assert saved_calls[0]["member_source_configs"][0]["org_code"] == "ORG_A"
    assert updated["name"] == "更新组"
    assert len(saved_calls[1]["member_source_configs"]) == 2
    assert saved_calls[1]["mapped_modules"] == ["执法检查"]
    assert asyncio.run(manager.update_datasource_group_from_sources_async("missing", "x", "", "mysql", {})) is None


def test_datasource_manager_update_datasource_ignores_semantic_payloads(monkeypatch):
    store = _SaveStore()
    manager = _build_manager(store)
    manager._load_datasource_async = lambda datasource_id: asyncio.sleep(0, result={"id": datasource_id, "updated": True})

    result = asyncio.run(manager.update_datasource_async(
        "ds_refresh",
        name="新名称",
        description="新描述",
        source_config={"host": "127.0.0.2", "port": 10000, "database": "dw2"},
        semantic_config={"deprecated": True},
        semantic_configs=[{"deprecated": True}],
        mapped_modules=["执法检查"],
        update_by="u1",
    ))

    assert result == {"id": "ds_refresh", "updated": True}
    assert store.saved_kwargs["group_name"] == "新名称"
    assert store.updated_kwargs["mapped_modules"] == ["执法检查"]
    assert store.saved_kwargs["member_sources_text"].startswith("机构A|ORG_A|http|127.0.0.2:10000:dw2")


def test_datasource_manager_async_crud_paths(monkeypatch):
    store = _FakeStore()
    store.datasources["ds_a"]["ds_tables"] = [{"table_name": "orders"}]
    store.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_shared", "org_name": "成员A", "org_code": "ORG_A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"}
    ]
    store.semantic_configs["group_ds_a"] = [
        {"id": "sc_a", "name": "订单分析", "scope_type": "public", "enabled": True, "metrics": [], "dimensions": [], "relationships": []}
    ]
    manager = _build_manager(store)

    async def run_checks():
        assert await manager.has_datasource_async("ds_a") is True
        assert (await manager.get_all_datasources_async(include_tables=False))[0]["ds_tables"] == []
        assert (await manager.get_datasource_async("ds_a"))["semantic_configs"][0]["source_type"] == "mysql"
        assert (await manager.get_datasource_by_group_name_async("数据源A"))["id"] == "ds_a"
        assert (await manager.get_execution_datasources_async("ds_a"))[0]["member_datasource_id"] == "phy_shared"
        assert await manager.get_table_groups_async("ds_a") == [{"table_group_name": "订单域", "table_names": ["orders"]}]
        assert await manager.replace_table_groups_async("ds_a", [{"table_group_name": "订单域", "table_names": ["orders", "missing"]}], operator="u1") is True
        assert (await manager.get_default_datasource_async())["id"] == "ds_a"
        assert await manager.set_default_datasource_async("ds_b") is True
        store.datasources["ds_b"]["is_default"] = True
        assert await manager.clear_default_datasource_async("ds_b") is True
        assert await manager.delete_semantic_config_async("ds_a", "missing") is False
        assert await manager.delete_semantic_config_async("ds_a", "sc_a") is True
        assert await manager.delete_datasource_async("missing") is False
        assert await manager.delete_datasource_async("ds_a") is True

    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.resolve_mysql_credentials",
        lambda _config: {"user": "u", "password": "p"},
    )
    asyncio.run(run_checks())


def test_datasource_manager_get_schema_cache_mysql_hive_http_and_errors(monkeypatch):
    store = _FakeStore()
    store.datasources["ds_a"]["ds_tables"] = [{"table_name": "orders"}, {"table_name": "users"}]
    manager = _build_manager(store)

    class FakeMySQLSyncService:
        def __init__(self, **kwargs):
            FakeMySQLSyncService.kwargs = kwargs

        async def get_full_schema_async(self, table_names):
            return [{"table_name": table_names[0] if table_names else "orders"}]

    class FakeHiveSyncService:
        def __init__(self, **kwargs):
            FakeHiveSyncService.kwargs = kwargs

        async def get_full_schema_async(self, table_names):
            return [{"table_name": table_names[0] if table_names else "hive_orders"}]

    async def fake_hive_http_schema(source_config, table_names, user_id=None, username=None):
        assert user_id == "user_a"
        assert username == "测试用户"
        return [{"table_name": table_names[0], "org_code": source_config["org_code"], "org_name": source_config["org_name"]}]

    monkeypatch.setattr("apps.chat_data_app.services.datasource_manager.resolve_mysql_credentials", lambda _config: {"user": "mysql_user", "password": "mysql_pwd"})
    monkeypatch.setattr("apps.chat_data_app.services.datasource_manager.resolve_hive_credentials", lambda _config: {"username": "hive_user", "password": "hive_pwd"})
    monkeypatch.setattr("apps.chat_data_app.services.mysql_sync.MySQLSyncService", FakeMySQLSyncService)
    monkeypatch.setattr("apps.chat_data_app.services.hive_sync.HiveSyncService", FakeHiveSyncService)

    async def run_checks():
        assert await manager.get_schema_async("ds_a", source="cache", table_names=["users"]) == [{"table_name": "users"}]
        mysql_schema = await manager.get_schema_async("ds_a", source="live", table_names=["orders"])
        assert mysql_schema == [{"table_name": "orders"}]
        assert FakeMySQLSyncService.kwargs["user"] == "mysql_user"

        store.datasources["ds_a"]["source_type"] = "hive"
        store.datasources["ds_a"]["query_executor_type"] = "direct"
        store.datasources["ds_a"]["source_config"] = {"host": "127.0.0.1", "port": 10000, "database": "dw"}
        hive_schema = await manager.get_schema_async("ds_a", source="live", table_names=["orders"])
        assert hive_schema == [{"table_name": "orders"}]
        assert FakeHiveSyncService.kwargs["username"] == "hive_user"

        store.datasources["ds_a"]["query_executor_type"] = "http"
        store.datasources["ds_a"]["member_sources"] = [{"member_datasource_id": "ORG_A", "org_name": "机构A", "org_code": "ORG_A", "query_executor_type": "http"}]
        store.datasources["ds_a"]["template_datasource_id"] = "ORG_A"
        monkeypatch.setattr("apps.chat_data_app.services.datasource_manager.get_hive_meta_schema_async", fake_hive_http_schema)
        assert await manager.get_schema_async("ds_a", source="live", table_names=["orders"], user_id="user_a", username="测试用户") == [{"table_name": "orders", "org_code": "ORG_A", "org_name": "机构A"}]

        assert await manager.get_schema_async("ds_a", source="cache", table_names=["missing"]) == []
        with pytest.raises(ValueError, match="数据源不存在"):
            await manager.get_schema_async("missing")
        store.datasources["ds_a"]["source_type"] = "sqlite"
        with pytest.raises(ValueError, match="不支持实时获取"):
            await manager.get_schema_async("ds_a", source="live")
        store.datasources["ds_a"]["source_type"] = "mysql"
        store.datasources["ds_a"]["source_config"] = {}
        with pytest.raises(ValueError, match="连接配置不完整"):
            await manager.get_schema_async("ds_a", source="live")

    asyncio.run(run_checks())


def test_datasource_manager_get_schema_timeout_and_connection_error(monkeypatch):
    store = _FakeStore()
    manager = _build_manager(store)

    class TimeoutMySQLSyncService:
        def __init__(self, **_kwargs):
            pass

        async def get_full_schema_async(self, table_names):
            await asyncio.sleep(0.01)
            return []

    class FailingMySQLSyncService:
        def __init__(self, **_kwargs):
            pass

        async def get_full_schema_async(self, table_names):
            raise RuntimeError("连接失败")

    async def run_checks():
        monkeypatch.setattr("apps.chat_data_app.services.datasource_manager.DATASOURCE_SYNC_TIMEOUT_SECONDS", 0.001)
        monkeypatch.setattr("apps.chat_data_app.services.datasource_manager.resolve_mysql_credentials", lambda _config: {"user": "u", "password": "p"})
        monkeypatch.setattr("apps.chat_data_app.services.mysql_sync.MySQLSyncService", TimeoutMySQLSyncService)
        with pytest.raises(ConnectionError, match="数据库连接超时"):
            await manager.get_schema_async("ds_a", source="live")

        monkeypatch.setattr("apps.chat_data_app.services.datasource_manager.DATASOURCE_SYNC_TIMEOUT_SECONDS", 30)
        monkeypatch.setattr("apps.chat_data_app.services.mysql_sync.MySQLSyncService", FailingMySQLSyncService)
        with pytest.raises(ConnectionError, match="数据库连接失败"):
            await manager.get_schema_async("ds_a", source="live")

    asyncio.run(run_checks())


class _FixedDatetime:
    @classmethod
    def now(cls):
        class _Now:
            microsecond = 345678

            def strftime(self, pattern):
                return "20260602101112"

        return _Now()


def test_datasource_manager_helper_edge_cases(monkeypatch):
    monkeypatch.delenv("CHATDATA_INT_ENV", raising=False)
    assert _read_positive_int_env("CHATDATA_INT_ENV", 9) == 9
    monkeypatch.setenv("CHATDATA_INT_ENV", "bad")
    assert _read_positive_int_env("CHATDATA_INT_ENV", 9) == 9
    monkeypatch.setenv("CHATDATA_INT_ENV", "0")
    assert _read_positive_int_env("CHATDATA_INT_ENV", 9) == 9
    monkeypatch.setenv("CHATDATA_INT_ENV", "7")
    assert _read_positive_int_env("CHATDATA_INT_ENV", 9) == 7

    assert _format_time(_FixedDatetime.now()) == "20260602101112"
    with pytest.raises(ValueError, match="不支持的查询执行方式"):
        _normalize_query_executor_type("mysql", "bad")
    with pytest.raises(ValueError, match="数据源连接信息不完整"):
        _build_source_line({"host": "", "port": 3306, "database": "demo"})
    with pytest.raises(ValueError, match="机构编码不能为空"):
        _normalize_member_source_config("mysql", {"org_name": "机构A", "host": "h", "port": 1, "database": "d"})
    assert _build_member_source_identity({"org_name": "机构A", "org_code": "ORG_A", "host": "h", "port": 1, "database": "d"}, "mysql") == "ORG_A"
    assert _build_member_source_text_line("mysql", {"org_name": "机构A", "org_code": "ORG_A", "host": "h", "port": 1, "database": "d"}) == "机构A|ORG_A|direct|h:1:d"

    datasource = {
        "template_datasource_id": "ORG_B",
        "query_executor_type": "http",
        "source_config": {"host": "h", "port": 1, "database": "d"},
        "member_sources": [
            {"member_datasource_id": "ORG_A", "org_name": "机构A", "org_code": "ORG_A", "host": "h1", "port": 1, "database": "d1"},
            {"member_datasource_id": "ORG_B", "org_name": "机构B", "org_code": "ORG_B", "host": "h2", "port": 2, "database": "d2"},
        ],
    }
    assert _get_template_member_source_config(datasource)["org_code"] == "ORG_B"
    meta_source_config = _build_meta_source_config(datasource)
    assert meta_source_config["org_code"] == "ORG_B"
    assert meta_source_config["query_executor_type"] == "http"
    assert _get_template_member_source_config({"member_sources": []}) == {}


def test_datasource_manager_async_save_and_group_from_sources(monkeypatch):
    manager = DataSourceManager.__new__(DataSourceManager)
    calls = []

    class AsyncSaveStore:
        def transaction_async(self):
            return _AsyncNullContext()

        async def upsert_datasource_group_async(self, **kwargs):
            calls.append(("upsert_group", kwargs))

        async def update_datasource_group_async(self, **kwargs):
            calls.append(("update_group", kwargs))

        async def replace_datasource_tables_async(self, **kwargs):
            calls.append(("replace_tables", kwargs))

    manager.db_store = AsyncSaveStore()
    manager._load_datasource_async = lambda datasource_id: asyncio.sleep(0, result={"id": datasource_id, "loaded": True})

    async def run_checks():
        saved = await manager._save_datasource_async(
            datasource_id="ds_async",
            group_name="异步组",
            group_desc="描述",
            source_type="mysql",
            query_executor_type="direct",
            template_source="h:3306:d",
            member_source_configs=[{"org_name": "机构A", "org_code": "ORG_A", "host": "h", "port": 3306, "database": "d"}],
            tables=[{"table_name": "orders"}],
            mapped_modules=["智能问数"],
            is_default=True,
            create_by="u1",
            update_by="u2",
        )
        assert saved == {"id": "ds_async", "loaded": True}
        assert calls[0][1]["member_sources_text"] == "机构A|ORG_A|direct|h:3306:d"
        assert calls[1][1]["mapped_modules"] == ["智能问数"]
        assert calls[1][1]["is_default"] is True
        assert calls[2][1]["tables"] == [{"table_name": "orders"}]

    asyncio.run(run_checks())

    calls.clear()
    monkeypatch.setattr("apps.chat_data_app.services.datasource_manager.datetime", _FixedDatetime)
    manager._save_datasource_async = lambda **kwargs: asyncio.sleep(0, result={"id": kwargs["datasource_id"], "members": kwargs["member_source_configs"]})

    created = asyncio.run(manager.create_datasource_group_from_sources_async(
        "异步新组",
        "描述",
        "mysql",
        {"org_name": "机构A", "org_code": "ORG_A", "query_executor_type": "http", "host": "h", "port": 3306, "database": "d"},
        member_source_configs=[{"org_name": "机构B", "org_code": "ORG_B", "host": "h2", "port": 3306, "database": "d2"}],
        create_by="u1",
    ))
    assert created["id"] == "ds_20260602101112_345678"
    assert [item["org_code"] for item in created["members"]] == ["ORG_A", "ORG_B"]


def test_datasource_manager_async_update_datasource_group_and_datasource(monkeypatch):
    store = _FakeStore()
    manager = _build_manager(store)
    saved_calls = []
    current = {
        "id": "ds_a",
        "group_id": "group_ds_a",
        "group_name": "旧组",
        "ds_description": "",
        "source_type": "mysql",
        "query_executor_type": "direct",
        "template_datasource_id": "ORG_A",
        "template_source": "机构A|ORG_A|direct|127.0.0.1:3306:old",
        "member_sources": [
            {"member_datasource_id": "ORG_A", "org_name": "机构A", "org_code": "ORG_A", "query_executor_type": "direct", "host": "127.0.0.1", "port": 3306, "database": "old"},
            {"member_datasource_id": "ORG_B", "org_name": "机构B", "org_code": "ORG_B", "query_executor_type": "direct", "host": "127.0.0.2", "port": 3306, "database": "old"},
        ],
        "ds_tables": [{"table_name": "orders"}],
        "mapped_modules": ["智能问数"],
        "is_default": False,
        "create_by": "creator",
        "update_by": "old",
        "semantic_configs": [],
    }
    manager.get_datasource_async = lambda datasource_id: asyncio.sleep(0, result=current if datasource_id == "ds_a" else None)
    manager._save_datasource_async = lambda **kwargs: saved_calls.append(kwargs) or asyncio.sleep(0, result={"id": kwargs["datasource_id"], "name": kwargs["group_name"]})

    async def run_checks():
        assert await manager.update_datasource_group_from_sources_async("missing", "x", "", "mysql", {}) is None
        updated_group = await manager.update_datasource_group_from_sources_async(
            "ds_a",
            "新组",
            "新描述",
            "mysql",
            {"org_name": "机构A", "org_code": "ORG_A", "host": "127.0.0.3", "port": 3306, "database": "new"},
            member_source_configs=None,
            update_by="u2",
        )
        assert updated_group["name"] == "新组"
        assert len(saved_calls[-1]["member_source_configs"]) == 2
        assert saved_calls[-1]["update_by"] == "u2"

        assert await manager.update_datasource_async("missing") is None
        updated_ds = await manager.update_datasource_async(
            "ds_a",
            name="数据源新名",
            description="新描述",
            source_config={"host": "127.0.0.4", "port": 3306, "database": "newer"},
            semantic_config={"ignored": True},
            semantic_configs=[{"ignored": True}],
            mapped_modules=["可视化查询"],
            update_by="u3",
        )
        assert updated_ds["name"] == "数据源新名"
        assert saved_calls[-1]["mapped_modules"] == ["可视化查询"]
        assert saved_calls[-1]["template_source"] == "127.0.0.4:3306:newer"

    asyncio.run(run_checks())


def test_datasource_manager_async_semantic_config_lifecycle(monkeypatch):
    store = _FakeStore()
    manager = _build_manager(store)
    configs = [
        {"id": "public", "group_id": "group_ds_a", "name": "公共", "scope_type": "public", "create_by": "admin", "enabled": True, "metrics": [], "dimensions": [], "relationships": []},
        {"id": "mine", "group_id": "group_ds_a", "name": "我的", "scope_type": "private", "create_by": "u1", "enabled": True, "metrics": [], "dimensions": [], "relationships": []},
        {"id": "other", "group_id": "group_ds_a", "name": "别人的", "scope_type": "private", "create_by": "u2", "enabled": True, "metrics": [], "dimensions": [], "relationships": []},
    ]
    store.semantic_configs["group_ds_a"] = configs

    async def run_checks():
        assert [item["id"] for item in await manager.get_semantic_configs_async("ds_a", user_id="u1")] == ["public", "mine"]
        assert await manager.get_semantic_config_async("ds_a", "missing") is None
        assert await manager._semantic_config_name_exists_async("ds_a", "", scope_type="public") is False
        assert await manager._semantic_config_name_exists_async("ds_a", "公共", scope_type="private", create_by="u1") is True
        assert await manager._semantic_config_name_exists_async("ds_a", "我的", exclude_config_id="mine", scope_type="private", create_by="u1") is False
        assert await manager.create_semantic_config_async("missing", "新配置") is None
        with pytest.raises(ValueError, match="语义配置名称不能为空"):
            await manager.create_semantic_config_async("ds_a", "")
        with pytest.raises(ValueError, match="只能是 public 或 private"):
            await manager.create_semantic_config_async("ds_a", "新配置", scope_type="bad")
        with pytest.raises(ValueError, match="语义配置名称已存在"):
            await manager.create_semantic_config_async("ds_a", "公共", scope_type="private", create_by="u1")

        created = await manager.create_semantic_config_async(
            "ds_a",
            "新配置",
            relationships=[{"left_table": "orders", "right_table": "items", "left_column": "order_id", "right_column": "order_id"}],
            create_by="u1",
        )
        assert created["name"] == "新配置"
        assert created["relationships"][0]["join_conditions"] == [{"left_column": "order_id", "right_column": "order_id"}]

        assert await manager.update_semantic_config_async("ds_a", "missing", name="x") is None
        with pytest.raises(ValueError, match="语义配置名称不能为空"):
            await manager.update_semantic_config_async("ds_a", "mine", name="")
        with pytest.raises(ValueError, match="语义配置名称已存在"):
            await manager.update_semantic_config_async("ds_a", "mine", name="公共")
        updated = await manager.update_semantic_config_async("ds_a", "mine", description="更新", enabled=False, update_by="u3")
        assert updated["description"] == "更新"
        assert updated["enabled"] is False
        assert updated["update_by"] == "u3"

    asyncio.run(run_checks())


def test_datasource_manager_get_default_enriches_semantic_configs():
    store = _FakeStore()
    store.semantic_configs["group_ds_a"] = [{"id": "sc_a", "name": "订单分析"}]
    manager = _build_manager(store)

    default = asyncio.run(manager.get_default_datasource_async())
    assert default["id"] == "ds_a"
    assert default["semantic_configs"] == [{"id": "sc_a", "name": "订单分析"}]


def test_datasource_manager_remaining_execution_and_table_desc_branches(monkeypatch):
    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.resolve_hive_credentials",
        lambda _config: {"username": "hive_user", "password": "hive_pwd"},
    )
    manager = DataSourceManager.__new__(DataSourceManager)
    hive_direct = manager._build_execution_datasource_targets({
        "source_type": "hive",
        "query_executor_type": "direct",
        "template_datasource_id": "hive_a",
        "member_sources": [
            {"member_datasource_id": "hive_a", "host": "hive-host", "port": 10000, "database": "dw", "source_type": "hive", "query_executor_type": "direct"},
        ],
    })[0]
    fallback_default = manager._build_execution_datasource_targets({
        "source_type": "mysql",
        "query_executor_type": "http",
        "template_datasource_id": "missing",
        "member_sources": [
            {"member_datasource_id": "m1", "org_code": "ORG1", "host": "h", "port": 1, "database": "d"},
        ],
    })[0]
    dedup_selected = manager._build_execution_datasource_targets({
        "source_type": "mysql",
        "query_executor_type": "http",
        "member_sources": [
            {"member_datasource_id": "m1", "org_code": "ORG1", "host": "h", "port": 1, "database": "d"},
        ],
    }, ["", "m1", "m1"])[0]

    assert hive_direct["source_config"]["username"] == "hive_user"
    assert hive_direct["source_config"]["password"] == "hive_pwd"
    assert fallback_default["member_datasource_id"] == "m1"
    assert dedup_selected["member_datasource_id"] == "m1"

    class DescStore:
        def __init__(self):
            self.calls = []
            self.datasource = {
                "group_id": "group_a",
                "ds_tables": [
                    {"table_name": "orders", "business_desc": "old"},
                    {"table_name": "items", "business_desc": ""},
                ],
            }

        async def get_datasource_async(self, datasource_id):
            return self.datasource if datasource_id == "ds_a" else None

        async def update_datasource_table_business_desc_async(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    store = DescStore()
    manager.db_store = store

    async def run_desc_checks():
        return (
            await manager.update_table_business_desc_async("ds_a", "", "x"),
            await manager.update_table_business_desc_async("missing", "orders", "x"),
            await manager.update_table_business_desc_async("ds_a", "missing", "x"),
            await manager.update_table_business_desc_async("ds_a", "orders", "  " + "a" * 300, operator="u1", operator_name="用户1"),
        )

    blank_table, missing_ds, missing_table, updated = asyncio.run(run_desc_checks())

    assert blank_table is None
    assert missing_ds is None
    assert missing_table is None
    assert updated["business_desc"] == "a" * 255
    assert store.calls[0][0][:3] == ("group_a", "orders", "a" * 255)
    assert store.calls[0][1]["update_by"] == "u1"


def test_datasource_manager_more_execution_and_group_edge_branches(monkeypatch):
    manager = DataSourceManager.__new__(DataSourceManager)

    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.resolve_hive_credentials",
        lambda _config: {"username": "", "password": ""},
    )
    no_credential_target = manager._build_execution_datasource_targets({
        "source_type": "hive",
        "query_executor_type": "direct",
        "member_sources": [{"member_datasource_id": "hive_a", "host": "h", "port": 10000, "database": "dw"}],
    })[0]
    assert "username" not in no_credential_target["source_config"]
    assert "password" not in no_credential_target["source_config"]

    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.resolve_hive_credentials",
        lambda _config: {"username": "hive_user", "password": ""},
    )
    username_only_target = manager._build_execution_datasource_targets({
        "source_type": "hive",
        "query_executor_type": "direct",
        "member_sources": [{"member_datasource_id": "hive_b", "host": "h", "port": 10000, "database": "dw"}],
    })[0]
    assert username_only_target["source_config"]["username"] == "hive_user"
    assert "password" not in username_only_target["source_config"]

    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.resolve_hive_credentials",
        lambda _config: {"username": "", "password": "hive_pwd"},
    )
    password_only_target = manager._build_execution_datasource_targets({
        "source_type": "hive",
        "query_executor_type": "direct",
        "member_sources": [{"member_datasource_id": "hive_c", "host": "h", "port": 10000, "database": "dw"}],
    })[0]
    assert "username" not in password_only_target["source_config"]
    assert password_only_target["source_config"]["password"] == "hive_pwd"

    captured = {}

    class GroupStore:
        async def get_datasource_async(self, datasource_id):
            return {"group_id": "group_a", "ds_tables": [{"table_name": "orders"}]} if datasource_id == "ds_a" else None

        async def replace_table_group_relations_async(self, group_id, groups, **kwargs):
            captured.update({"group_id": group_id, "groups": groups, "kwargs": kwargs})

    manager.db_store = GroupStore()
    assert asyncio.run(manager.replace_table_groups_async("ds_a", [
        {"table_group_name": "空分组", "table_names": ["missing"]},
        {"table_group_name": "  ", "table_names": ["orders"]},
    ])) is True
    assert captured["groups"] == []

    table = manager._find_table_column(
        {"ds_tables": [{"table_name": "users", "columns": [{"name": "id"}]}, {"table_name": "orders", "columns": [{"name": "id"}]}]},
        "orders",
        "id",
    )
    assert table["table_name"] == "orders"


def test_datasource_manager_name_scope_and_column_finder_edges():
    store = _FakeStore()
    store.semantic_configs["group_ds_a"] = [
        {"id": "public", "name": "公共", "scope_type": "public", "create_by": "admin"},
        {"id": "private_other", "name": "私有", "scope_type": "private", "create_by": "u2"},
    ]
    manager = _build_manager(store)

    async def run_checks():
        return (
            await manager._semantic_config_name_exists_async("ds_a", "私有", scope_type="public", create_by="u1"),
            await manager._semantic_config_name_exists_async("ds_a", "新名称", scope_type="public", create_by="u1"),
        )

    public_scope_sees_private_name, missing_name = asyncio.run(run_checks())

    assert public_scope_sees_private_name is True
    assert missing_name is False
    assert manager._find_table_column(None, "orders", "id") is None
    assert manager._find_table_column({"ds_tables": [{"table_name": "orders", "columns": []}]}, "orders", "id") is None

    async_default = asyncio.run(manager.get_default_datasource_async())
    assert [item["id"] for item in async_default["semantic_configs"]] == ["public", "private_other"]

    store.datasources["ds_a"]["is_default"] = False
    assert asyncio.run(manager.get_default_datasource_async()) is None


def test_datasource_manager_init_and_singleton_use_db_store(monkeypatch, tmp_path):
    created = {}

    class FakeDBStore:
        def __init__(self):
            created["called"] = True

    monkeypatch.setattr("apps.chat_data_app.services.datasource_manager.DataSourceDBStore", FakeDBStore)
    monkeypatch.setattr("apps.chat_data_app.services.datasource_manager._datasource_manager_instance", None)

    manager = DataSourceManager(base_dir=str(tmp_path))

    assert created["called"] is True
    assert manager.base_dir == tmp_path

    first = __import__("apps.chat_data_app.services.datasource_manager", fromlist=["get_datasource_manager"]).get_datasource_manager()
    second = __import__("apps.chat_data_app.services.datasource_manager", fromlist=["get_datasource_manager"]).get_datasource_manager()
    assert first is second
    assert isinstance(first.db_store, FakeDBStore)


def test_datasource_manager_relationship_and_helper_fallback_branches(monkeypatch):
    manager = DataSourceManager.__new__(DataSourceManager)
    datasource = {
        "semantic_configs": [
            {
                "relationships": [
                    {
                        "left_table": "orders",
                        "right_table": "items",
                        "join_conditions": [
                            {"left_column": "order_id"},
                            {"left_column": "order_id", "right_column": "order_id"},
                        ],
                    },
                    {"left_column": "user_id", "right_column": "user_id"},
                    {"left_column": "", "right_column": ""},
                ]
            }
        ]
    }

    manager._normalize_semantic_relationships(datasource)

    relationships = datasource["semantic_configs"][0]["relationships"]
    assert len(relationships) == 2
    assert relationships[0]["join_conditions"] == [{"left_column": "order_id", "right_column": "order_id"}]
    assert relationships[1]["join_conditions"] == [{"left_column": "user_id", "right_column": "user_id"}]

    merged = _merge_member_source_lines(
        current={},
        next_template_source_config={"org_name": "机构A", "org_code": "ORG_A", "host": "h", "port": 1, "database": "d"},
        source_type="mysql",
        member_source_configs=[
            {"org_name": "机构A重复", "org_code": "ORG_A", "host": "h2", "port": 2, "database": "d2"},
            {"org_name": "机构B", "org_code": "ORG_B", "host": "h3", "port": 3, "database": "d3"},
        ],
    )
    assert [item["org_code"] for item in merged] == ["ORG_A", "ORG_B"]

    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager._normalize_member_source_config",
        lambda _source_type, source_config, _default=None: dict(source_config or {}),
    )
    assert _build_member_source_identity({"host": "h", "port": 3306, "database": "d"}, "mysql") == "h:3306:d"

    meta_config = _build_meta_source_config({
        "source_config": {"host": "h", "port": 10000, "database": "d"},
        "member_sources": [{"member_datasource_id": "", "org_name": "机构A", "org_code": "", "host": "h", "port": 10000, "database": "d"}],
    })
    assert "org_code" not in meta_config
    assert meta_config["org_name"] == "机构A"
    assert meta_config["query_executor_type"] == "direct"


def test_datasource_manager_load_and_raise_paths():
    store = _FakeStore()
    manager = _build_manager(store)

    async def run_checks():
        assert (await manager._get_datasource_or_raise_async("ds_a"))["id"] == "ds_a"
        with pytest.raises(ValueError, match="数据源不存在"):
            await manager._get_datasource_or_raise_async("missing")
        loaded = await manager._load_datasource_async("ds_a")
        assert loaded["id"] == "ds_a"
        with pytest.raises(ValueError, match="写入后读取失败"):
            await manager._load_datasource_async("missing")

    asyncio.run(run_checks())


def test_datasource_manager_async_save_datasource_supports_minimal_payloads():
    manager = DataSourceManager.__new__(DataSourceManager)
    calls = []

    class MinimalAsyncStore:
        def transaction_async(self):
            return _AsyncNullContext()

        async def upsert_datasource_group_async(self, **kwargs):
            calls.append(("upsert", kwargs))

        async def update_datasource_group_async(self, **kwargs):
            calls.append(("update", kwargs))

        async def replace_datasource_tables_async(self, **kwargs):
            calls.append(("replace_tables", kwargs))

        async def get_datasource_async(self, datasource_id):
            return {"id": datasource_id, "group_id": datasource_id}

        async def get_semantic_configs_by_group_id_async(self, group_id):
            return []

    manager.db_store = MinimalAsyncStore()

    result = asyncio.run(manager._save_datasource_async(
        datasource_id="ds_async_min",
        group_name="异步最小数据源",
        group_desc="",
        source_type="mysql",
        query_executor_type="direct",
        template_source="h:3306:d",
        member_source_configs=[],
    ))

    assert result["id"] == "ds_async_min"
    assert calls[0][1]["template_source"] == "h:3306:d"
    assert calls[1][1] == {"group_id": "ds_async_min", "mapped_modules": [], "update_by": "", "update_by_name": ""}
    assert not any(name == "replace_tables" for name, _ in calls)


def test_datasource_manager_lookup_and_table_failure_paths(monkeypatch):
    store = _FakeStore()
    manager = _build_manager(store)

    store.datasources["ds_a"]["member_sources"] = [
        {
            "member_datasource_id": "ORG_HIVE",
            "org_name": "Hive机构",
            "org_code": "ORG_HIVE",
            "source_type": "hive",
            "query_executor_type": "direct",
            "host": "127.0.0.1",
            "port": 10000,
            "database": "dw",
        }
    ]
    store.datasources["ds_a"]["template_datasource_id"] = "ORG_HIVE"
    monkeypatch.setattr(
        "apps.chat_data_app.services.datasource_manager.resolve_hive_credentials",
        lambda _config: {"username": "hive_user", "password": "hive_pwd"},
    )

    async def run_async_checks():
        assert (await manager.get_datasource_by_group_name_async("数据源A"))["id"] == "ds_a"
        assert await manager.get_datasource_by_group_name_async("不存在") is None
        assert await manager.get_table_groups_async("missing") == []
        assert await manager.replace_table_groups_async("missing", []) is False
        assert await manager.get_semantic_configs_async("missing") == []
        assert await manager.set_default_datasource_async("missing") is False
        assert await manager.clear_default_datasource_async("missing") is False
        assert await manager.delete_datasource_async("missing") is False
        target = (await manager.get_execution_datasources_async("ds_a"))[0]
        assert target["source_config"]["username"] == "hive_user"
        assert target["source_config"]["password"] == "hive_pwd"

    asyncio.run(run_async_checks())


def test_datasource_manager_update_datasource_without_live_source_change():
    store = _FakeStore()
    store.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "ORG_A", "org_name": "机构A", "org_code": "ORG_A", "query_executor_type": "direct", "host": "127.0.0.1", "port": 3306, "database": "db_a"}
    ]
    store.datasources["ds_a"]["template_datasource_id"] = "ORG_A"
    manager = _build_manager(store)
    saved_calls = []
    manager._save_datasource_async = lambda **kwargs: asyncio.sleep(0, result=(saved_calls.append(kwargs) or {"id": kwargs["datasource_id"], "name": kwargs["group_name"]}))

    assert asyncio.run(manager.update_datasource_async("missing")) is None
    updated = asyncio.run(manager.update_datasource_async("ds_a", update_by="u2"))

    assert updated == {"id": "ds_a", "name": "数据源A"}
    assert saved_calls[-1]["template_source"] == "ORG_A"
    assert saved_calls[-1]["member_source_configs"][0]["org_code"] == "ORG_A"


def test_datasource_manager_async_default_and_cache_schema_branches():
    store = _FakeStore()
    store.datasources["ds_a"]["ds_tables"] = [{"table_name": "orders"}, {"table_name": "users"}]
    manager = _build_manager(store)

    async def run_checks():
        assert await manager.get_schema_async("ds_a", source="cache") == [{"table_name": "orders"}, {"table_name": "users"}]
        store.datasources["ds_a"]["is_default"] = False
        assert await manager.clear_default_datasource_async("ds_a") is False
        assert await manager.set_default_datasource_async("ds_b") is True
        assert store.datasources["ds_b"]["is_default"] is True

    asyncio.run(run_checks())


def test_datasource_manager_semantic_name_scope_helpers():
    store = _FakeStore()
    store.semantic_configs["group_ds_a"] = [
        {"id": "public_a", "name": "订单分析", "scope_type": "public", "create_by": "admin"},
        {"id": "private_a", "name": "个人分析", "scope_type": "private", "create_by": "u1"},
        {"id": "private_b", "name": "个人分析", "scope_type": "private", "create_by": "u2"},
    ]
    manager = _build_manager(store)

    async def run_checks():
        assert await manager._semantic_config_name_exists_async("ds_a", "") is False
        assert await manager._semantic_config_name_exists_async("ds_a", "不存在") is False
        assert await manager._semantic_config_name_exists_async("ds_a", "订单分析", scope_type="private", create_by="u1") is True
        assert await manager._semantic_config_name_exists_async("ds_a", "个人分析", scope_type="private", create_by="u1") is True
        assert await manager._semantic_config_name_exists_async("ds_a", "个人分析", scope_type="private", create_by="u3") is False
        assert await manager._semantic_config_name_exists_async("ds_a", "个人分析", scope_type="public") is True

    asyncio.run(run_checks())


def test_datasource_manager_semantic_name_private_ignores_other_private_until_public():
    store = _FakeStore()
    store.semantic_configs["group_ds_a"] = [
        {"id": "private_other", "name": "同名", "scope_type": "private", "create_by": "u2"},
        {"id": "public_same", "name": "同名", "scope_type": "public", "create_by": "admin"},
    ]
    manager = _build_manager(store)

    assert asyncio.run(manager._semantic_config_name_exists_async("ds_a", "同名", scope_type="private", create_by="u1")) is True
