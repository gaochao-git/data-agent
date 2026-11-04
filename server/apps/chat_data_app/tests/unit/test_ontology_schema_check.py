"""本体 schema 启动自检：缺失表/列必须显式失败，不能静默带病启动。"""

from __future__ import annotations

import asyncio

import pytest

from apps.chat_data_app.services.ontology import schema_check


def _table_columns(
    *,
    write_mode: bool = True,
    generation: bool = True,
    conflict: bool = True,
    applied_at: bool = True,
    baseline: bool = True,
) -> dict[str, set[str]]:
    object_type = {"id"}
    if write_mode:
        object_type.add("write_mode")
    if conflict:
        object_type.add("conflict_resolution")
    object_edit = {"id"}
    if generation:
        object_edit.add("object_generation")
    if applied_at:
        object_edit.add("applied_at")
    execution = {"id"}
    if baseline:
        execution.update({"baseline_json", "revert_of_execution_id"})
    return {
        "chat_data_ontology_object_type": object_type,
        "chat_data_ontology_object_set": {"id"},
        "chat_data_ontology_action_object": {"id"},
        "chat_data_ontology_action_execution": execution,
        "chat_data_ontology_object_edit": object_edit,
        "chat_data_ontology_object_instance": {"id", "generation", "lifecycle_status"},
    }


def test_missing_schema_issues_detects_missing_write_mode():
    issues = schema_check.missing_schema_issues(_table_columns(write_mode=False), action_execute_enabled=False)
    assert issues == ["表 chat_data_ontology_object_type 缺少列: write_mode"]


def test_missing_object_set_table_detected():
    columns = _table_columns()
    columns.pop("chat_data_ontology_object_set")
    assert schema_check.missing_schema_issues(columns, action_execute_enabled=False) == [
        "缺少表 chat_data_ontology_object_set"
    ]


def test_missing_schema_issues_action_tables_only_required_when_action_enabled():
    columns = _table_columns()
    assert schema_check.missing_schema_issues(columns, action_execute_enabled=False) == []
    columns.pop("chat_data_ontology_action_object")
    assert schema_check.missing_schema_issues(columns, action_execute_enabled=False) == []
    assert (
        "chat_data_ontology_action_object"
        in schema_check.missing_schema_issues(columns, action_execute_enabled=True)[0]
    )


def test_required_tables_include_action_tables_only_when_enabled():
    assert schema_check.required_tables(action_execute_enabled=False) == {
        "chat_data_ontology_object_type",
        "chat_data_ontology_object_set",
    }
    required = schema_check.required_tables(action_execute_enabled=True)
    assert "chat_data_ontology_action_object" in required
    # 删除/重建语义依赖实例表，必须随 Action 开关一起要求
    assert "chat_data_ontology_object_instance" in required


def test_missing_object_instance_table_detected_when_action_enabled():
    columns = _table_columns()
    columns.pop("chat_data_ontology_object_instance")
    assert schema_check.missing_schema_issues(columns, action_execute_enabled=True) == [
        "缺少表 chat_data_ontology_object_instance"
    ]


def test_missing_object_generation_column_detected_when_action_enabled():
    columns = _table_columns(generation=False)
    assert schema_check.missing_schema_issues(columns, action_execute_enabled=True) == [
        "表 chat_data_ontology_object_edit 缺少列: object_generation"
    ]


def test_missing_conflict_resolution_column_detected_even_without_action():
    columns = _table_columns(conflict=False)
    assert schema_check.missing_schema_issues(columns, action_execute_enabled=False) == [
        "表 chat_data_ontology_object_type 缺少列: conflict_resolution"
    ]


def test_missing_baseline_columns_detected_when_action_enabled():
    columns = _table_columns(baseline=False)
    assert schema_check.missing_schema_issues(columns, action_execute_enabled=True) == [
        "表 chat_data_ontology_action_execution 缺少列: baseline_json, revert_of_execution_id"
    ]


class _Conn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def run_sync(self, fn):
        return fn(object())


class _Engine:
    def connect(self):
        return _Conn()


class _Inspector:
    def __init__(self, table_columns):
        self.table_columns = table_columns

    def get_table_names(self):
        return list(self.table_columns)

    def get_columns(self, table):
        return [{"name": name} for name in self.table_columns[table]]


def _patch_schema(monkeypatch, table_columns, *, action_enabled=False):
    monkeypatch.setattr(schema_check, "ontology_injection_enabled", lambda: True)
    monkeypatch.setattr(schema_check, "ontology_action_execute_enabled", lambda: action_enabled)
    monkeypatch.setattr(schema_check, "get_engine", lambda: _Engine())
    monkeypatch.setattr(schema_check, "inspect", lambda _conn: _Inspector(table_columns))


def test_require_ontology_schema_passes_when_complete(monkeypatch):
    _patch_schema(monkeypatch, _table_columns())
    asyncio.run(schema_check.require_ontology_schema_async())


def test_require_ontology_schema_rejects_missing_generation_column(monkeypatch):
    _patch_schema(monkeypatch, _table_columns(generation=False), action_enabled=True)
    with pytest.raises(RuntimeError, match="object_generation"):
        asyncio.run(schema_check.require_ontology_schema_async())


def test_require_ontology_schema_rejects_missing_write_mode(monkeypatch):
    _patch_schema(monkeypatch, _table_columns(write_mode=False))
    with pytest.raises(RuntimeError, match="write_mode"):
        asyncio.run(schema_check.require_ontology_schema_async())


def test_require_ontology_schema_skips_when_injection_disabled(monkeypatch):
    monkeypatch.setattr(schema_check, "ontology_injection_enabled", lambda: False)
    monkeypatch.setattr(schema_check, "get_engine", lambda: pytest.fail("不应触库"))
    asyncio.run(schema_check.require_ontology_schema_async())
