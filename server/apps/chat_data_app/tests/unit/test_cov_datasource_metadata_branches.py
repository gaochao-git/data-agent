"""datasource/metadata 缺失分支：索引统计归一化、Hive 注释选取与响应摘要。"""

import asyncio

import pytest

from apps.chat_data_app.services.datasource import metadata as meta_module
from apps.chat_data_app.services.datasource.metadata import (
    HiveSyncService,
    MySQLSyncService,
    _meta_response_summary,
)


def test_group_index_stats_skips_blank_and_unrequested_tables():
    rows = [
        {"TABLE_NAME": "", "INDEX_NAME": "idx_blank"},
        {"TABLE_NAME": "other", "INDEX_NAME": "idx_other"},
        {"TABLE_NAME": "users", "INDEX_NAME": "idx_users", "COLUMN_NAME": "id"},
    ]
    grouped = MySQLSyncService._group_index_stats(rows, ["users"])
    assert list(grouped) == ["users"]


def test_build_index_groups_skips_blank_index_or_column():
    grouped = MySQLSyncService._build_index_groups(
        [
            {"index_name": "", "column_name": "id", "non_unique": False, "seq": 1},
            {"index_name": "idx_a", "column_name": "", "non_unique": False, "seq": 1},
            {"index_name": "idx_b", "column_name": "name", "non_unique": True, "seq": 1},
        ]
    )
    assert list(grouped) == ["idx_b"]


def test_apply_index_stats_ignores_unknown_tables():
    tables = {"users": {"primary_keys": [], "unique_indexes": [], "indexes": []}}
    MySQLSyncService._apply_index_stats(
        tables,
        {"ghost": [{"index_name": "idx", "non_unique": False, "seq": 1, "column_name": "id"}]},
    )
    assert tables["users"]["primary_keys"] == []


def test_resolve_hive_table_comment_fallbacks():
    assert HiveSyncService._resolve_hive_table_comment("", "comment", "列注释", "表注释") == "列注释"
    assert HiveSyncService._resolve_hive_table_comment("comment", "varchar(10)", "", "表注释") == "varchar(10)"
    assert HiveSyncService._resolve_hive_table_comment("name", "string", "", "表注释") == "表注释"


@pytest.mark.asyncio
async def test_get_full_schema_async_reads_worker_setting(monkeypatch):
    async def _setting(_key):
        return 3

    def _schema(_self, table_names, max_workers):
        return [{"table_name": "t1", "workers": max_workers, "tables": table_names}]

    monkeypatch.setattr(meta_module, "get_runtime_setting", _setting)
    monkeypatch.setattr(HiveSyncService, "_get_full_schema", _schema)

    service = HiveSyncService("hive.local")
    result = await service.get_full_schema_async(["t1"])
    assert result == [{"table_name": "t1", "workers": 3, "tables": ["t1"]}]
    assert asyncio.iscoroutinefunction(service.get_full_schema_async)


def test_meta_response_summary_handles_tables_and_non_dict_payloads():
    assert _meta_response_summary(["raw"]) == {"payload_type": "list"}
    summary = _meta_response_summary({"code": 0, "msg": None, "message": "ok", "data": {"tables": [1, 2]}})
    assert summary == {"code": 0, "msg": "ok", "table_count": 2}
    assert _meta_response_summary({"code": 0, "data": {"tables": "bad"}}) == {"code": 0}
