"""datasource/manager.py 分支覆盖补测。

覆盖 2026-09-18 审计出的 16 条未覆盖分支弧中可达的 14 条：
成员源配置校验出口、不存在数据源的各 async 出口、字段字典 group_id 兜底、
手工索引项校验、执行对象权限过滤的 HTTP/无权限分支。
（`if member_line:` 两条弧为防御性死分支——_build_member_source_text_line 恒返回非空或抛错，不追。）
"""

import os
import sys
from types import SimpleNamespace  # noqa: F401  # 测试按模块属性引用 SimpleNamespace，属有意重导出

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.services.datasource import manager as datasource_manager_module
from apps.chat_data_app.services.datasource.manager import (
    DataSourceManager,
    _normalize_member_source_config,
)

_find_table = DataSourceManager._find_table
_validate_and_normalize_index_item = DataSourceManager._validate_and_normalize_index_item


class _FakeDao:
    def __init__(self, **returns):
        self.returns = returns
        self.calls: list = []

    def __getattr__(self, name):
        async def _call(*args, **kwargs):
            self.calls.append(name)
            value = self.returns.get(name)
            return value(*args, **kwargs) if callable(value) else value
        return _call


def _manager(**dao_returns) -> DataSourceManager:
    manager = DataSourceManager.__new__(DataSourceManager)
    manager.datasource_dao = _FakeDao(**dao_returns)
    return manager


# ---------------------------------------------------------------- 纯函数

def test_normalize_member_source_config_requires_database():
    """org/org_code 齐全但缺 database → 报错（156->157）。"""
    with pytest.raises(ValueError, match="数据库名不能为空"):
        _normalize_member_source_config("mysql", {"org_name": "机构A", "org_code": "ORG_A"})


def test_find_table_returns_none_on_empty_input():
    assert _find_table(None, "t") is None
    assert _find_table({"ds_tables": []}, "  ") is None


def _table() -> dict:
    return {
        "name": "orders",
        "columns": [{"name": "id"}, {"name": "amount"}],
        "unique_indexes": [],
        "indexes": [],
    }


def test_validate_index_item_rejects_non_dict():
    """索引项不是对象 → 报错（899->900）。"""
    with pytest.raises(ValueError, match="索引项必须是对象"):
        _validate_and_normalize_index_item(
            "junk", _table(), {"id", "amount"}, set(), set(), False)


def test_validate_index_item_rejects_duplicate_name():
    """同名索引重复 → 报错（913->914）。"""
    seen: set = set()
    table = _table()
    first, _ = _validate_and_normalize_index_item(
        {"index_type": "secondary", "index_name": "idx_amount", "columns": ["amount"]},
        table, {"id", "amount"}, set(), seen, False)
    assert first["index_name"] == "idx_amount"
    with pytest.raises(ValueError, match="索引名称重复"):
        _validate_and_normalize_index_item(
            {"index_type": "secondary", "index_name": "idx_amount", "columns": ["amount"]},
            table, {"id", "amount"}, set(), seen, False)


# ---------------------------------------------------------------- async 出口

@pytest.mark.asyncio
async def test_get_datasource_meta_or_raise_rejects_missing():
    manager = _manager()  # dao 方法默认返回 None
    with pytest.raises(ValueError, match="数据源不存在"):
        await manager._get_datasource_meta_or_raise_async("ds_x")


@pytest.mark.asyncio
async def test_authorize_execution_datasources_http_path_and_empty_filter(monkeypatch):
    """无 member_sources 的数据源走保守 HTTP 查询（648->651），过滤后为空 → 报错（657->658）。"""
    calls = {}

    async def _fake_allowed(user_id, username, **kwargs):
        calls["kwargs"] = kwargs
        return {"direct": set(), "http": set()}

    monkeypatch.setattr(datasource_manager_module, "get_allowed_org_codes_by_executor", _fake_allowed)
    manager = _manager()
    with pytest.raises(ValueError, match="无可用执行对象权限"):
        await manager.authorize_execution_datasources_async({}, user_id="u1", username="张三")
    assert "include_http_permission" not in calls["kwargs"]  # 走的是 HTTP 分支


@pytest.mark.asyncio
async def test_get_column_dicts_returns_empty_without_group_id():
    """元信息缺 group_id → 空 dict（790->791）。"""
    manager = _manager(get_datasource_meta_async={"id": "ds_a"})
    assert await manager.get_datasource_column_dicts_async("ds_a") == {}


@pytest.mark.asyncio
async def test_get_column_dicts_delegates_to_dao_with_group_id():
    """有 group_id → 透传 dao（790->792）。"""
    manager = _manager(
        get_datasource_meta_async={"id": "ds_a", "group_id": "g1"},
        get_column_dicts_by_group_id_async={("orders", "amount"): {}},
    )
    result = await manager.get_datasource_column_dicts_async("ds_a")
    assert ("orders", "amount") in result
    assert "get_column_dicts_by_group_id_async" in manager.datasource_dao.calls


@pytest.mark.asyncio
async def test_replace_column_dict_returns_none_when_datasource_missing():
    manager = _manager()  # get_datasource_async → None
    assert await manager.replace_column_dict_async("ds_x", "orders", "amount", {}) is None


@pytest.mark.asyncio
async def test_replace_column_dict_returns_none_when_column_missing():
    manager = _manager(get_datasource_async={"id": "ds_a", "ds_tables": [_table()]})
    assert await manager.replace_column_dict_async("ds_a", "orders", "not_exists", {}) is None


@pytest.mark.asyncio
async def test_sync_datasource_tables_returns_none_when_missing():
    manager = _manager()  # get_datasource_async → None
    assert await manager.sync_datasource_tables_async("ds_x", []) is None


@pytest.mark.asyncio
async def test_get_live_schema_rejects_missing_datasource():
    manager = _manager()
    with pytest.raises(ValueError, match="数据源不存在"):
        await manager.get_live_schema_async("ds_x")


@pytest.mark.asyncio
async def test_get_authorized_datasources_skips_http_lookup_when_all_direct(monkeypatch):
    """全部成员为 direct 执行源 → 不带 HTTP 权限查询（521->524 else 分支）。"""
    calls = {}

    async def _fake_allowed(user_id, username, **kwargs):
        calls["kwargs"] = kwargs
        return {"direct": set(), "http": set()}

    async def _fake_targets(filtered, member_ids=None):
        return []

    async def _fake_all(include_tables=True):
        return [{
            "id": "ds_a",
            "source_type": "mysql",
            "query_executor_type": "direct",
            "member_sources": [{"org_code": "ORG_A", "query_executor_type": "direct"}],
        }]

    monkeypatch.setattr(datasource_manager_module, "get_allowed_org_codes_by_executor", _fake_allowed)
    manager = _manager()
    manager.get_all_datasources_async = _fake_all
    manager._build_execution_datasource_targets = _fake_targets
    await manager.get_authorized_datasources_async(user_id="u1", username="张三")
    assert calls["kwargs"].get("include_http_permission") is False
