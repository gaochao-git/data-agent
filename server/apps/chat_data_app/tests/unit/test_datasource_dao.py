import asyncio
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.daos.datasource_dao import (
    _ORM_SESSION,
    DataSourceDao,
)
from apps.chat_data_app.daos.semantic_config_dao import SemanticConfigDao
from apps.chat_data_app.daos import datasource_dao_helpers as datasource_dao_helper_module
from apps.chat_data_app.daos.datasource_dao_helpers import (
    _build_member_identity,
    _build_member_source_text_line,
    _build_source_line,
    _format_time,
    _load_json,
    _merge_table_indexes_for_sync,
    _normalize_column_dict,
    _normalize_table_indexes_payload,
    _normalize_query_executor_type_for_parse,
    _parse_member_source_entry,
    _parse_member_source_entries,
    _parse_member_sources_text,  # noqa: F401  # 测试按模块属性引用 _parse_member_sources_text，属有意重导出
    _parse_source_line,
    _unique_non_empty_texts,
)


def _group_model(group_id: str = "group_a", group_name: str = "订单组", is_default: int = 0):
    return SimpleNamespace(
        id=1,
        group_id=group_id,
        group_name=group_name,
        group_desc="描述",
        query_context="智能问数说明",
        recommended_questions_json='["查询订单总览"]',
        source_type="mysql",
        template_source="机构A|ORG_A|direct|127.0.0.1:3306:demo",
        member_sources_text="机构A|ORG_A|direct|127.0.0.1:3306:demo\n机构B|ORG_B|http|127.0.0.2:3306:demo",
        mapped_modules_json='["执法检查"]',
        is_default=is_default,
        delete_flag=0,
        create_time=datetime(2026, 6, 2, 10, 0, 0),
        update_time=datetime(2026, 6, 2, 11, 0, 0),
        create_by="u1",
        create_by_name="张三",
        update_by="u2",
        update_by_name="李四",
    )


def _table_model(group_id: str = "group_a", table_name: str = "orders", sort_no: int = 0):
    return SimpleNamespace(
        id=1 + sort_no,
        group_id=group_id,
        table_names=table_name,
        table_desc="订单表",
        table_business_desc="业务订单主表",
        columns_json='[{"name":"id"}]',
        indexes_json=None,
        sort_no=sort_no,
        delete_flag=0,
        create_time=None,
        update_time=None,
        create_by="",
        create_by_name="",
        update_by="",
        update_by_name="",
    )


def _semantic_model(config_id: str = "sc_a", scope_type: str = "public", create_by: str = "u1", name: str = "订单分析", sort_no: int = 0):
    return SimpleNamespace(
        id=1 + sort_no,
        semantic_config_id=config_id,
        group_id="group_a",
        semantic_config_name=name,
        semantic_config_desc="描述",
        business_notes="业务规则",
        enabled_flag=1,
        metrics_json='[{"name":"订单金额"}]',
        dimensions_json='[{"name":"城市"}]',
        relationships_json='[{"left_table":"orders"}]',
        scope_type=scope_type,
        sort_no=sort_no,
        delete_flag=0,
        create_time=datetime(2026, 6, 2, 10, 0, 0),
        update_time=datetime(2026, 6, 2, 11, 0, 0),
        create_by=create_by,
        create_by_name="张三",
        update_by="u2",
        update_by_name="李四",
    )


def _table_group_model(group_id: str, table_group_name: str, table_name: str):
    return SimpleNamespace(
        id=1,
        group_id=group_id,
        table_group_name=table_group_name,
        table_name=table_name,
        delete_flag=0,
        create_time=None,
        update_time=None,
        create_by="",
        create_by_name="",
        update_by="",
        update_by_name="",
    )


def _column_dict_model(group_id: str = "group_a", table_name: str = "orders", column_name: str = "status", dict_json: str | None = None):
    return SimpleNamespace(
        id=1,
        group_id=group_id,
        table_name=table_name,
        column_name=column_name,
        dict_json=dict_json or json.dumps({"enum_values": [{"value": "1", "label": "有效"}], "synonyms": ["状态"], "storage_format": "", "unit": ""}, ensure_ascii=False),
        delete_flag=0,
        create_time=None,
        update_time=None,
        create_by="",
        create_by_name="",
        update_by="",
        update_by_name="",
    )


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeExecuteResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return _FakeScalarResult(self._rows)

    def mappings(self):
        return _FakeScalarResult([
            {
                "semantic_config_id": row.semantic_config_id,
                "semantic_config_name": row.semantic_config_name,
                "semantic_config_desc": row.semantic_config_desc,
                "enabled_flag": row.enabled_flag,
                "metrics_json": row.metrics_json,
                "dimensions_json": row.dimensions_json,
            }
            for row in self._rows
        ])


class _FakeOrmSession:
    def __init__(self, rows_by_table=None, executed=None):
        self.rows_by_table = rows_by_table or {}
        self.executed = executed if executed is not None else []

    async def execute(self, stmt):
        self.executed.append(stmt)
        sql = str(stmt)
        values = self._stmt_values(stmt)
        if not sql.lstrip().upper().startswith("SELECT"):
            return _FakeExecuteResult([])
        if "FROM datasource_group" in sql:
            rows = self._filter_datasource_group(sql, values)
        elif "FROM datasource_column_dict" in sql:
            rows = self._filter_column_dict(sql, values)
        elif "FROM datasource_table_group_rel" in sql:
            rows = self._filter_table_group(sql, values)
        elif "FROM datasource_table" in sql:
            rows = self._filter_datasource_table(sql, values)
        elif "FROM semantic_config" in sql:
            rows = self._filter_semantic_config(sql, values)
        else:
            rows = []
        return _FakeExecuteResult(rows)

    def _stmt_values(self, stmt):
        try:
            raw_values = stmt.compile().params.values()
        except Exception:
            return set()
        values = set()
        for value in raw_values:
            if isinstance(value, (list, tuple, set)):
                values.update(value)
            else:
                values.add(value)
        return values

    def _filter_datasource_group(self, sql: str, values: set):
        rows = list(self.rows_by_table.get("datasource_group", []))
        if "group_id = " in sql:
            return [row for row in rows if row.group_id in values]
        if "group_name = " in sql:
            return [row for row in rows if row.group_name in values]
        if "is_default = " in sql:
            return [row for row in rows if row.is_default == 1]
        return sorted(rows, key=lambda row: (-int(row.is_default or 0), row.id))

    def _filter_datasource_table(self, sql: str, values: set):
        rows = list(self.rows_by_table.get("datasource_table", []))
        if "group_id = " in sql or "group_id IN " in sql:
            rows = [row for row in rows if row.group_id in values]
        if "table_names = " in sql or "table_names IN " in sql:
            rows = [row for row in rows if row.table_names in values]
        return rows

    def _filter_table_group(self, sql: str, values: set):
        rows = list(self.rows_by_table.get("datasource_table_group_rel", []))
        if "group_id = " in sql or "group_id IN " in sql:
            rows = [row for row in rows if row.group_id in values]
        if "table_name IN " in sql:
            rows = [row for row in rows if row.table_name in values]
        return sorted(rows, key=lambda row: (row.table_group_name, row.table_name, row.id))

    def _filter_column_dict(self, sql: str, values: set):
        rows = list(self.rows_by_table.get("datasource_column_dict", []))
        if "group_id = " in sql:
            rows = [row for row in rows if row.group_id in values]
        if "table_name = " in sql:
            rows = [row for row in rows if row.table_name in values]
        if "column_name = " in sql:
            rows = [row for row in rows if row.column_name in values]
        return sorted(rows, key=lambda row: row.id)

    def _filter_semantic_config(self, sql: str, values: set):
        rows = list(self.rows_by_table.get("semantic_config", []))
        if "group_id = " in sql:
            rows = [row for row in rows if row.group_id in values]
        if "semantic_config_id = " in sql:
            rows = [row for row in rows if row.semantic_config_id in values]
        if "scope_type = " in sql and {"public", "private"}.issubset(values):
            rows = [row for row in rows if row.scope_type == "public" or row.create_by in values]
        return sorted(rows, key=lambda row: (0 if row.scope_type == "public" else 1, row.semantic_config_name, row.id))


def _patch_store_session(monkeypatch, rows_by_table=None, executed=None):
    session = _FakeOrmSession(rows_by_table=rows_by_table, executed=executed)

    @asynccontextmanager
    async def fake_get_session():
        yield session

    monkeypatch.setattr("apps.chat_data_app.daos.dao_session.get_session", fake_get_session)
    return session


def test_parse_member_source_entries_keep_valid_members():
    entries = _parse_member_source_entries(
        "机构A|ORG_A|http|dw\n机构B|ORG_B|http|dw",
        "机构A|ORG_A|http|dw",
        "hive",
        "http",
    )

    assert [item["datasource_id"] for item in entries] == ["ORG_A", "ORG_B"]
    assert [item["source_line"] for item in entries] == ["dw", "dw"]
    assert entries[0]["host"] is None
    legacy_entries = _parse_member_source_entries(
        "机构A|ORG_A|http|127.0.0.1:10000:dw",
        "机构A|ORG_A|http|127.0.0.1:10000:dw",
        "hive",
        "http",
    )
    assert legacy_entries[0]["source_line"] == "dw"
    assert legacy_entries[0]["host"] == "127.0.0.1"
    assert _parse_member_source_entries("direct|127.0.0.1:3306:test_db", "direct|127.0.0.1:3306:test_db", "mysql", "direct") == []
    duplicate_entries = _parse_member_source_entries(
        "机构A|ORG_A|direct|127.0.0.1:3306:demo\n机构A|ORG_A|direct|127.0.0.1:3306:demo",
        "",
        "mysql",
        "direct",
    )
    fallback_template_entries = _parse_member_source_entries(
        "机构A|ORG_A|direct|127.0.0.1:3306:demo",
        "机构B|ORG_B|http|demo",
        "mysql",
        "direct",
    )
    assert [item["datasource_id"] for item in duplicate_entries] == ["ORG_A"]
    assert [item["datasource_id"] for item in fallback_template_entries] == ["ORG_B", "ORG_A"]


def test_parse_and_mapping_helpers_cover_invalid_branches(monkeypatch):
    warnings = []
    monkeypatch.setattr("apps.chat_data_app.daos.datasource_dao_helpers.logger.warning", lambda *args, **_kwargs: warnings.append(args))

    assert _format_time("2026-06-02") == "2026-06-02"
    assert _load_json(None, {"default": True}) == {"default": True}
    assert _load_json({"a": 1}, {}) == {"a": 1}
    assert _load_json("", ["default"]) == ["default"]
    assert _load_json("{bad}", ["default"]) == ["default"]
    assert _load_json(123, ["default"]) == ["default"]
    assert _load_json(b'["a"]', []) == ["a"]
    assert _build_source_line("", 3306, "demo") is None
    assert _build_member_source_text_line({"org_name": "机构A", "org_code": "ORG_A", "host": "", "port": 3306, "database": "demo"}) is None
    assert _build_member_source_text_line({"org_name": "", "org_code": "ORG_A", "host": "127.0.0.1", "port": 3306, "database": "demo"}) is None
    assert _build_member_source_text_line({"org_name": "机构A", "org_code": "ORG_A", "query_executor_type": "http", "host": "127.0.0.1", "port": 10000, "database": "dw"}) == "机构A|ORG_A|http|dw"
    assert _build_member_identity("mysql", "direct", " ORG_A ", "h:1:d") == "ORG_A"
    assert _build_member_identity("mysql", "direct", "", "h:1:d") is None
    assert _normalize_query_executor_type_for_parse("mysql", "bad") == "direct"
    assert _normalize_query_executor_type_for_parse("mysql", "HTTP") == "http"
    assert _parse_source_line("") is None
    assert _parse_source_line(":3306:demo") is None
    assert _parse_source_line("bad-source-line") is None
    assert _parse_source_line("bad-source-line") is None
    assert _parse_source_line("127.0.0.1:abc:demo") is None
    assert _parse_source_line("127.0.0.1:abc:demo") is None
    assert len(warnings) == 3
    assert _parse_member_source_entry("", "mysql", "direct") is None
    assert _parse_member_source_entry("机构A|ORG_A|bad|127.0.0.1:3306:demo", "mysql", "direct") is None
    assert _parse_member_source_entry("|ORG_A|direct|127.0.0.1:3306:demo", "mysql", "direct") is None
    assert _parse_member_source_entry("机构A||direct|127.0.0.1:3306:demo", "mysql", "direct") is None
    assert _parse_member_source_entry("机构A|ORG_A|direct|bad", "mysql", "direct") is None
    assert _parse_member_source_entry("机构A|ORG_A|direct|127.0.0.1:3306:demo", "mysql", "bad")["query_executor_type"] == "direct"
    monkeypatch.setattr(datasource_dao_helper_module, "_build_member_identity", lambda *_args: None)
    assert _parse_member_source_entry("机构A|ORG_A|direct|127.0.0.1:3306:demo", "mysql", "direct") is None
    assert _unique_non_empty_texts([" a ", "", "a", "b"]) == ["a", "b"]


def test_map_group_row_and_member_sources_preserve_metadata():
    store = DataSourceDao.__new__(DataSourceDao)
    row = store._group_model_to_row(_group_model(is_default=1))

    datasource = store._map_group_row(row)
    members = store._build_member_sources(row)

    assert datasource["template_datasource_id"] == "ORG_A"
    assert datasource["mapped_modules"] == ["执法检查"]
    assert datasource["recommended_questions"] == ["查询订单总览"]
    assert datasource["is_default"] is True
    assert datasource["source_config"] == {"host": "127.0.0.1", "port": 3306, "database": "demo"}
    assert [item["member_datasource_id"] for item in members] == ["ORG_A", "ORG_B"]
    assert members[1]["query_executor_type"] == "http"

    legacy_row = {
        "group_id": "legacy_group",
        "group_name": "旧格式",
        "group_desc": "",
        "source_type": "mysql",
        "template_source": "127.0.0.1:3306:demo",
        "member_sources_text": "",
        "mapped_modules_json": "",
        "is_default": 0,
        "create_time": None,
        "update_time": None,
        "create_by": "",
        "update_by": "",
    }
    legacy = store._map_group_row(legacy_row)
    assert legacy["template_datasource_id"] == "127.0.0.1:3306:demo"
    assert legacy["source_config"] == {"host": "127.0.0.1", "port": 3306, "database": "demo"}
    assert legacy["recommended_questions"] == []


def test_table_indexes_normalization_preserves_legacy_and_manual_indexes():
    columns = [
        {"name": "id", "primary_key": True},
        {"name": "code", "unique_key": True},
        {"name": "email", "unique_key": True},
        {"name": "status", "index_key": True},
        {"name": "created_at", "index_key": True},
    ]
    legacy = _normalize_table_indexes_payload(None, columns)
    assert legacy["physical"] == [
        {"index_type": "primary", "index_name": "PRIMARY", "columns": ["id"]},
        {"index_type": "unique", "index_name": "", "columns": ["code"]},
        {"index_type": "unique", "index_name": "", "columns": ["email"]},
        {"index_type": "secondary", "index_name": "", "columns": ["status"]},
        {"index_type": "secondary", "index_name": "", "columns": ["created_at"]},
    ]

    existing = {
        "physical": [{"index_type": "secondary", "index_name": "idx_old", "columns": ["status"]}],
        "manual": [{"index_type": "unique", "index_name": "uniq_code", "columns": ["code"]}],
    }
    merged = _merge_table_indexes_for_sync(
        {
            "columns": columns,
            "primary_keys": [["id"]],
            "unique_indexes": [{"name": "uniq_email", "columns": ["email"]}],
            "indexes": [{"name": "idx_created_at", "columns": ["created_at"]}],
        },
        existing,
    )
    assert merged["physical"][2] == {
        "index_type": "secondary",
        "index_name": "idx_created_at",
        "columns": ["created_at"],
    }
    assert merged["manual"] == existing["manual"]


def test_datasource_dao_table_index_read_and_update_boundaries(monkeypatch):
    store = DataSourceDao.__new__(DataSourceDao)
    table = _table_model()
    table.columns_json = '[{"name":"id"},{"name":"status"}]'
    table.indexes_json = json.dumps({
        "physical": [{"index_type": "primary", "index_name": "PRIMARY", "columns": ["id"]}],
        "manual": [{"index_type": "secondary", "index_name": "idx_old", "columns": ["status"]}],
    })
    executed = []
    _patch_store_session(monkeypatch, {"datasource_table": [table]}, executed=executed)
    manual = [{"index_type": "unique", "index_name": "uniq_status", "columns": ["status"]}]

    async def run_checks():
        return (
            await store.get_datasource_table_indexes_async("group_a", "orders"),
            await store.get_datasource_table_indexes_async("group_a", "missing"),
            await store.update_datasource_table_indexes_async("group_a", "orders", manual, update_by="u1", update_by_name="用户1"),
            await store.update_datasource_table_indexes_async("", "orders", manual),
        )

    indexes, missing, saved, invalid = asyncio.run(run_checks())
    assert indexes["physical"][0]["index_name"] == "PRIMARY"
    assert indexes["manual"][0]["index_name"] == "idx_old"
    assert missing is None
    assert saved == manual
    assert invalid is None
    update_stmt = next(stmt for stmt in executed if str(stmt).lstrip().upper().startswith("UPDATE DATASOURCE_TABLE"))
    params = update_stmt.compile().params
    persisted = json.loads(next(value for key, value in params.items() if key.startswith("indexes_json")))
    assert persisted["physical"] == indexes["physical"]
    assert persisted["manual"] == manual
    assert any("FOR UPDATE" in str(stmt).upper() for stmt in executed)


def test_map_group_row_normalizes_malformed_recommended_questions():
    store = DataSourceDao.__new__(DataSourceDao)
    row = store._group_model_to_row(_group_model())
    row["recommended_questions_json"] = json.dumps(
        [
            " 查询订单总览 ",
            "",
            "查询订单总览",
            "超" * 51,
            123,
            "按机构统计订单数",
        ],
        ensure_ascii=False,
    )

    datasource = store._map_group_row(row)
    assert datasource["recommended_questions"] == ["查询订单总览", "123", "按机构统计订单数"]

    row["recommended_questions_json"] = json.dumps({"question": "查询订单总览"}, ensure_ascii=False)
    assert store._map_group_row(row)["recommended_questions"] == []


def test_datasource_dao_group_and_table_queries_use_orm(monkeypatch):
    store = DataSourceDao.__new__(DataSourceDao)
    _patch_store_session(monkeypatch, {
        "datasource_group": [_group_model(is_default=1)],
        "datasource_table": [_table_model()],
        "datasource_column_dict": [_column_dict_model("group_a", "orders", "status")],
        "datasource_table_group_rel": [
            _table_group_model("group_a", "订单域", "orders"),
            _table_group_model("group_a", "订单域", "orders"),
        ],
    })

    async def run_checks():
        return (
            await store.has_datasource_async("group_a"),
            await store.has_datasource_async("missing"),
            await store.has_datasource_async(""),
            await store.get_all_datasources_async(),
            await store.get_all_datasources_async(include_tables=False),
            await store.get_datasource_async("group_a"),
            await store.get_datasource_meta_async("group_a"),
            await store.get_datasource_by_group_name_async(""),
            await store.get_datasource_by_group_name_async("订单组"),
            await store.get_default_datasource_async(),
            await store.get_datasource_tables_by_group_id_async("group_a"),
            await store.get_group_member_datasource_ids_async("group_a"),
            await store.get_column_dict_async("group_a", "orders", "status"),
        )

    exists, missing, empty, all_datasources, all_without_tables, datasource, datasource_meta, empty_name, by_name, default, tables, member_ids, column_dict = asyncio.run(run_checks())

    assert exists is True
    assert missing is False
    assert empty is False
    assert all_datasources[0]["ds_tables"][0]["business_desc"] == "业务订单主表"
    assert all_datasources[0]["ds_tables"][0]["groups"] == [{"table_group_name": "订单域"}]
    assert all_without_tables[0]["ds_tables"] == []
    assert datasource["member_datasource_ids"] == ["ORG_A", "ORG_B"]
    assert datasource_meta["member_datasource_ids"] == ["ORG_A", "ORG_B"]
    assert datasource_meta["ds_tables"] == []
    assert empty_name is None
    assert by_name["group_name"] == "订单组"
    assert default["is_default"] is True
    assert tables[0]["columns"] == [{"name": "id"}]
    assert tables[0]["primary_keys"] == []
    assert tables[0]["manual_indexes"] == []
    assert member_ids == ["ORG_A", "ORG_B"]
    assert column_dict == {"enum_values": [{"value": "1", "label": "有效"}], "synonyms": ["状态"], "storage_format": "", "unit": ""}
    assert asyncio.run(store.get_datasource_tables_by_group_id_async("")) == []
    assert asyncio.run(store.get_table_group_relations_async("")) == []
    assert asyncio.run(store.get_column_dict_async("", "orders", "status")) == {"enum_values": [], "synonyms": [], "storage_format": "", "unit": ""}
    assert asyncio.run(store._get_group_tables_map_async([])) == {}


def test_column_dict_normalize_and_batch_query_branches(monkeypatch):
    normalized = _normalize_column_dict({
        "enums": [
            "bad",
            {"enum_value": "", "enum_label": "空"},
            {"enum_value": "1", "enum_label": "有效"},
            {"value": "1", "label": "重复"},
            *[{"value": str(index), "label": f"值{index}"} for index in range(2, 105)],
        ],
        "synonyms": [" 状态 ", "", "状态", *[f"别名{index}" for index in range(60)]],
        "storage_format": "x" * 300,
        "unit": "元" * 120,
    })

    assert len(normalized["enum_values"]) == 100
    assert normalized["enum_values"][0] == {"value": "1", "label": "有效"}
    assert len(normalized["synonyms"]) == 50
    assert normalized["synonyms"][0] == "状态"
    assert len(normalized["storage_format"]) == 255
    assert len(normalized["unit"]) == 100
    assert _normalize_column_dict({"enum_values": "bad", "synonyms": "bad"}) == {"enum_values": [], "synonyms": [], "storage_format": "", "unit": ""}

    store = DataSourceDao.__new__(DataSourceDao)
    _patch_store_session(monkeypatch, {
        "datasource_column_dict": [
            _column_dict_model("group_a", "orders", "status", json.dumps({"enum_values": [{"value": "1", "label": "有效"}]}, ensure_ascii=False)),
            _column_dict_model("group_a", "orders", "", json.dumps({"unit": "元"}, ensure_ascii=False)),
            _column_dict_model("group_a", "", "amount", json.dumps({"unit": "元"}, ensure_ascii=False)),
            _column_dict_model("group_a", "orders", "empty", json.dumps({}, ensure_ascii=False)),
            _column_dict_model("group_a", "users", "name", json.dumps({"synonyms": ["用户"]}, ensure_ascii=False)),
        ],
    })

    async def run_checks():
        return (
            await store.get_column_dict_async("group_a", "orders", "missing"),
            await store.get_column_dicts_by_group_id_async(""),
            await store.get_column_dicts_by_group_id_async("group_a"),
            await store.get_column_dicts_by_table_async("", "orders"),
            await store.get_column_dicts_by_table_async("group_a", ""),
            await store.get_column_dicts_by_table_async("group_a", "orders"),
        )

    missing_single, empty_group, by_group, empty_table_group, empty_table_name, by_table = asyncio.run(run_checks())

    assert missing_single == {"enum_values": [], "synonyms": [], "storage_format": "", "unit": ""}
    assert empty_group == {}
    assert empty_table_group == {}
    assert empty_table_name == {}
    assert by_group == {
        ("orders", "status"): {"enum_values": [{"value": "1", "label": "有效"}], "synonyms": [], "storage_format": "", "unit": ""},
        ("users", "name"): {"enum_values": [], "synonyms": ["用户"], "storage_format": "", "unit": ""},
    }
    assert by_table == {
        "status": {"enum_values": [{"value": "1", "label": "有效"}], "synonyms": [], "storage_format": "", "unit": ""},
    }


def test_datasource_dao_empty_defaults_and_write_short_circuits(monkeypatch):
    store = DataSourceDao.__new__(DataSourceDao)
    executed = []
    _patch_store_session(monkeypatch, {"datasource_group": [], "datasource_column_dict": []}, executed=executed)

    async def run_checks():
        return (
            await store.get_default_datasource_async(),
            await store.update_datasource_table_business_desc_async("", "orders", "desc"),
            await store.update_datasource_table_business_desc_async("group_a", "", "desc"),
            await store.replace_column_dict_async("", "orders", "status", {"unit": "元"}),
            await store.replace_column_dict_async("group_a", "orders", "status", {}),
        )

    default_ds, empty_group_update, empty_table_update, invalid_dict, empty_dict = asyncio.run(run_checks())

    assert default_ds is None
    assert empty_group_update is None
    assert empty_table_update is None
    assert invalid_dict == {"enum_values": [], "synonyms": [], "storage_format": "", "unit": ""}
    assert empty_dict == {"enum_values": [], "synonyms": [], "storage_format": "", "unit": ""}
    assert any("UPDATE datasource_column_dict" in str(stmt) for stmt in executed)


def test_datasource_dao_table_group_queries_deduplicate(monkeypatch):
    store = DataSourceDao.__new__(DataSourceDao)
    _patch_store_session(monkeypatch, {
        "datasource_table_group_rel": [
            _table_group_model("group_a", "订单域", "orders"),
            _table_group_model("group_a", "订单域", "orders"),
            _table_group_model("group_a", "", "orders"),
            _table_group_model("group_b", "用户域", "users"),
        ],
    })

    async def run_checks():
        return (
            await store.get_table_groups_by_group_ids_async(["group_a", "group_a", "", "group_b"]),
            await store.get_table_groups_by_table_names_async("group_a", ["orders", "orders", ""]),
            await store.get_table_group_relations_async("group_a"),
        )

    by_group, by_table, relations = asyncio.run(run_checks())

    assert by_group["group_a"]["orders"] == [{"table_group_name": "订单域"}]
    assert by_group["group_b"]["users"] == [{"table_group_name": "用户域"}]
    assert by_table["orders"] == [{"table_group_name": "订单域"}]
    assert relations[0] == {"table_group_name": "订单域", "table_name": "orders"}
    assert asyncio.run(store.get_table_groups_by_group_ids_async([])) == {}
    assert asyncio.run(store.get_table_groups_by_table_names_async("", ["orders"])) == {}


def test_semantic_config_queries_and_visibility_use_orm(monkeypatch):
    store = SemanticConfigDao.__new__(SemanticConfigDao)
    _patch_store_session(monkeypatch, {
        "datasource_group": [_group_model()],
        "datasource_table": [],
        "semantic_config": [
            _semantic_model("sc_private", "private", "user_a", name="个人订单", sort_no=1),
            _semantic_model("sc_public_b", "public", "admin", name="订单分析", sort_no=3),
            _semantic_model("sc_hidden", "private", "other", name="其他个人", sort_no=0),
            _semantic_model("sc_public_a", "public", "admin", name="高规格", sort_no=2),
        ],
    })

    async def run_checks():
        return (
            await store.get_by_group_id_async("group_a"),
            await store.get_by_group_id_async("group_a", user_id="user_a"),
            await store.get_directory_by_group_id_async("group_a", user_id="user_a"),
            await store.get_by_id_async("group_a", "sc_public_b"),
            await store.get_by_id_async("missing", "sc_public_b"),
        )

    configs, visible_configs, directory, config, missing = asyncio.run(run_checks())

    assert [item["id"] for item in configs] == ["sc_public_b", "sc_public_a", "sc_private", "sc_hidden"]
    assert [item["id"] for item in visible_configs] == ["sc_public_b", "sc_public_a", "sc_private"]
    assert directory == [
        {"id": "sc_public_b", "name": "订单分析", "description": "描述", "enabled": True, "metrics_total": 1, "dimensions_total": 1},
        {"id": "sc_public_a", "name": "高规格", "description": "描述", "enabled": True, "metrics_total": 1, "dimensions_total": 1},
        {"id": "sc_private", "name": "个人订单", "description": "描述", "enabled": True, "metrics_total": 1, "dimensions_total": 1},
    ]
    assert config["name"] == "订单分析"
    assert config["metrics"] == [{"name": "订单金额"}]
    assert missing is None
    assert asyncio.run(store.get_by_group_id_async("")) == []
    assert asyncio.run(store.get_directory_by_group_id_async("")) == []
    assert asyncio.run(store.get_by_id_async("", "sc_public")) is None
    assert asyncio.run(store.get_by_id_async("group_a", "missing")) is None


def test_store_write_methods_emit_orm_statements(monkeypatch):
    store = DataSourceDao.__new__(DataSourceDao)
    semantic_store = SemanticConfigDao.__new__(SemanticConfigDao)
    executed = []
    _patch_store_session(monkeypatch, {"datasource_group": [_group_model()], "datasource_table": []}, executed=executed)

    async def run_checks():
        await store.upsert_datasource_group_async("group_a", "订单组", "描述", "mysql", "机构A|ORG_A|direct|127.0.0.1:3306:demo", create_by="u1", update_by="u2")
        await store.update_datasource_group_async("group_a", group_name="新名称", mapped_modules=["A"], is_default=True, update_by="u1")
        await store.clear_all_default_datasources_async(update_by="u3")
        await store.replace_datasource_tables_async("", [{"table_name": "orders"}])
        await store.replace_datasource_tables_async("group_a", [], update_by="u4")
        await store.replace_datasource_tables_async("group_a", [{"table_name": "orders", "description": "订单表", "columns": [{"name": "id"}]}, {"table_name": ""}], create_by="u5", update_by="u6")
        await store.update_datasource_table_business_desc_async("group_a", "orders", "业务说明", update_by="u7", update_by_name="用户7")
        saved_dict = await store.replace_column_dict_async(
            "group_a",
            "orders",
            "status",
            {
                "enum_values": [
                    {"value": "1", "label": "有效"},
                    {"value": "1", "label": "重复"},
                    {"value": "", "label": "空值"},
                    {"value": "0", "label": "无效"},
                ],
                "synonyms": ["状态", "状态"],
                "storage_format": "",
                "unit": "",
            },
            create_by="u11",
            update_by="u12",
            create_by_name="用户11",
            update_by_name="用户12",
        )
        await store.replace_table_group_relations_async("", [{"table_group_name": "订单域", "table_names": ["orders"]}])
        await store.replace_table_group_relations_async("group_a", [{"table_group_name": "订单域", "table_names": ["orders", "orders", "items"]}, {"table_group_name": "", "table_names": ["x"]}], create_by="u7", update_by="u8")
        await store.soft_delete_group_async("group_a", update_by="u9")
        assert await semantic_store.upsert_async("", {"id": "sc_missing"}) is False
        assert await semantic_store.upsert_many_async("group_a", [
            {"id": "sc_a", "name": "订单分析", "enabled": False, "metrics": [{"name": "销售额"}], "scope_type": "private"},
            {"id": "sc_b", "name": "用户分析", "enabled": False, "dimensions": [{"name": "用户"}], "scope_type": "public"},
        ]) is True
        await semantic_store.soft_delete_async("sc_a", update_by="u10")
        assert saved_dict == {"enum_values": [{"value": "1", "label": "有效"}, {"value": "0", "label": "无效"}], "synonyms": ["状态"], "storage_format": "", "unit": ""}

    asyncio.run(run_checks())

    # 软删除语句必须：① where 带 delete_flag == 0（幂等，重复删除不二次改名）；
    # ② 名字改成 concat(left(name,92),'__d',id,'_',日期)——delete_flag 只有两态，
    # 「删除→同名重建→再删」会让两条记录都落到 (name, 1) 上，改名保证三元组唯一。
    from sqlalchemy.sql.dml import Update as sa_Update

    # soft_delete_async 是本测试最后发出的 semantic_config 写操作，取最后一条 update 断言
    semantic_updates = [s for s in executed if isinstance(s, sa_Update) and s.table.name == "semantic_config"]
    assert semantic_updates
    delete_sql = str(semantic_updates[-1].compile(compile_kwargs={"literal_binds": True})).lower()
    assert "delete_flag = 0" in delete_sql
    assert "concat(left(" in delete_sql
    assert "'__d'" in delete_sql
    assert "date_format(now()" in delete_sql

    sql_texts = [str(stmt) for stmt in executed]
    assert any("INSERT INTO datasource_group" in sql for sql in sql_texts)
    assert any("UPDATE datasource_group" in sql for sql in sql_texts)
    assert any("UPDATE datasource_table" in sql for sql in sql_texts)
    assert sum(re.search(r"INSERT INTO datasource_table\s", sql) is not None for sql in sql_texts) == 1
    datasource_table_upserts = [sql for sql in sql_texts if re.search(r"INSERT INTO datasource_table\s", sql) is not None]
    duplicate_update_clause = datasource_table_upserts[0].split("ON DUPLICATE KEY UPDATE", 1)[1]
    assert "table_business_desc" not in duplicate_update_clause
    assert sum("INSERT INTO datasource_column_dict" in sql for sql in sql_texts) == 1
    assert sum("INSERT INTO datasource_table_group_rel" in sql for sql in sql_texts) == 1
    assert any("INSERT INTO semantic_config" in sql for sql in sql_texts)
    semantic_upserts = [sql for sql in sql_texts if re.search(r"INSERT INTO semantic_config\s", sql) is not None]
    assert len(semantic_upserts) == 1
    semantic_params = executed[sql_texts.index(semantic_upserts[0])].compile().params
    assert {value for key, value in semantic_params.items() if key.startswith("semantic_config_id")} == {"sc_a", "sc_b"}
    assert any("UPDATE semantic_config" in sql for sql in sql_texts)


def test_transaction_reuses_active_orm_session(monkeypatch):
    store = DataSourceDao.__new__(DataSourceDao)
    session = _patch_store_session(monkeypatch, {"datasource_group": [_group_model()]})

    async def run_checks():
        async with store.transaction_async() as active:
            assert active is session
            assert _ORM_SESSION.get() is session
            assert await store.has_datasource_async("group_a") is True
            async with store.transaction_async() as nested:
                assert nested is session
        assert _ORM_SESSION.get() is None

    asyncio.run(run_checks())


def test_attach_table_groups_short_circuits():
    store = DataSourceDao.__new__(DataSourceDao)

    assert asyncio.run(store._attach_table_groups_async("", [{"table_name": "orders"}])) == [{"table_name": "orders"}]
    assert asyncio.run(store._attach_table_groups_async("group_a", [{"table_name": ""}])) == [{"table_name": ""}]


def test_datasource_dao_remaining_read_branch_edges(monkeypatch):
    store = DataSourceDao.__new__(DataSourceDao)
    semantic_store = SemanticConfigDao.__new__(SemanticConfigDao)
    _patch_store_session(monkeypatch, {
        "datasource_group": [_group_model()],
        "datasource_table": [
            _table_model("", "orphan"),
            _table_model("group_a", "orders"),
        ],
        "semantic_config": [_semantic_model("sc_public", "public", "admin")],
        "datasource_table_group_rel": [],
    })

    async def run_checks():
        return (
            await store._get_group_row_by_id_async(""),
            await store.get_group_member_datasource_ids_async("missing"),
            await store._get_group_tables_map_async(["group_a", ""]),
            await semantic_store.get_by_id_async("missing", "sc_public"),
            await semantic_store.get_by_id_async("group_a", "sc_public"),
        )

    empty_group, missing_members, tables_map, missing_config, existing_config = asyncio.run(run_checks())

    assert empty_group is None
    assert missing_members == []
    assert [table["table_name"] for table in tables_map["group_a"]] == ["orders"]
    assert missing_config is None
    assert existing_config["id"] == "sc_public"


def test_update_datasource_group_all_optional_fields_and_duplicate_relations(monkeypatch):
    store = DataSourceDao.__new__(DataSourceDao)
    executed = []
    _patch_store_session(monkeypatch, {"datasource_group": [_group_model()]}, executed=executed)

    async def run_checks():
        await store.update_datasource_group_async(
            "group_a",
            group_desc="只改描述",
            update_by="u0",
        )
        await store.update_datasource_group_async(
            "group_a",
            group_name="新名称",
            group_desc="新描述",
            source_type="hive",
            template_source="机构A|ORG_A|http|127.0.0.1:10000:dw",
            member_sources_text="机构A|ORG_A|http|127.0.0.1:10000:dw",
            mapped_modules=[],
            is_default=False,
            update_by="u1",
            update_by_name="用户1",
        )
        await store.replace_table_group_relations_async(
            "group_a",
            [
                {"table_group_name": "订单域", "table_names": ["orders"]},
                {"table_group_name": "订单域", "table_names": ["orders"]},
            ],
            create_by="u2",
            update_by="u3",
        )

    asyncio.run(run_checks())

    sql_texts = [str(stmt) for stmt in executed]
    assert any("group_desc" in sql and "source_type" in sql and "template_source" in sql and "member_sources_text" in sql for sql in sql_texts)
    assert sum("INSERT INTO datasource_table_group_rel" in sql for sql in sql_texts) == 1


# ============ 仅填空写字典（Ossie 导入闭环 P1-3） ============


def test_fill_column_dict_only_fills_empty_fields(monkeypatch):
    """仅填空：现有值非空的字段保留原值，空缺字段用导入值填充并合并成一次 upsert。"""
    store = DataSourceDao.__new__(DataSourceDao)
    executed = []
    _patch_store_session(monkeypatch, {"datasource_column_dict": [
        _column_dict_model(dict_json=json.dumps({
            "enum_values": [{"value": "1", "label": "已有值"}],
            "synonyms": [],
            "storage_format": "",
            "unit": "",
        }, ensure_ascii=False)),
    ]}, executed=executed)

    filled = asyncio.run(store.fill_column_dict_async(
        "group_a", "orders", "status",
        {"enum_values": [{"value": "2", "label": "应忽略"}], "synonyms": ["付款"], "storage_format": "0/1", "unit": "元"},
    ))

    assert filled == ["synonyms", "storage_format", "unit"]
    upserts = [stmt for stmt in executed if "INSERT" in str(stmt).upper()]
    assert len(upserts) == 1
    merged = json.loads(upserts[0].compile().params["dict_json"])
    assert merged["enum_values"] == [{"value": "1", "label": "已有值"}]
    assert merged["synonyms"] == ["付款"]
    assert merged["storage_format"] == "0/1"
    assert merged["unit"] == "元"


def test_fill_column_dict_creates_row_when_missing(monkeypatch):
    """无存量行：导入值全部为「空缺」，整行新建并返回全部填充字段。"""
    store = DataSourceDao.__new__(DataSourceDao)
    executed = []
    _patch_store_session(monkeypatch, {"datasource_column_dict": []}, executed=executed)

    filled = asyncio.run(store.fill_column_dict_async(
        "group_a", "orders", "status", {"unit": "元"},
        create_by="user_a", update_by="user_a", create_by_name="张三", update_by_name="张三",
    ))

    assert filled == ["unit"]
    assert any("INSERT" in str(stmt).upper() for stmt in executed)


def test_fill_column_dict_noop_when_all_fields_covered(monkeypatch):
    """现有字典已全覆盖：不做任何写入，返回空清单。"""
    store = DataSourceDao.__new__(DataSourceDao)
    executed = []
    _patch_store_session(monkeypatch, {"datasource_column_dict": [
        _column_dict_model(dict_json=json.dumps({
            "enum_values": [{"value": "1", "label": "有效"}],
            "synonyms": ["状态"],
            "storage_format": "0/1",
            "unit": "元",
        }, ensure_ascii=False)),
    ]}, executed=executed)

    filled = asyncio.run(store.fill_column_dict_async(
        "group_a", "orders", "status", {"enum_values": [{"value": "2", "label": "应忽略"}], "unit": "元"},
    ))

    assert filled == []
    assert not [stmt for stmt in executed if "INSERT" in str(stmt).upper()]


def test_fill_column_dict_short_circuits_on_invalid_args(monkeypatch):
    """空键或空内容：直接短路返回空清单，不产生任何写入。"""
    store = DataSourceDao.__new__(DataSourceDao)
    executed = []
    _patch_store_session(monkeypatch, {"datasource_column_dict": []}, executed=executed)

    async def run_checks():
        return (
            await store.fill_column_dict_async("", "orders", "status", {"unit": "元"}),
            await store.fill_column_dict_async("group_a", "", "status", {"unit": "元"}),
            await store.fill_column_dict_async("group_a", "orders", "", {"unit": "元"}),
            await store.fill_column_dict_async("group_a", "orders", "status", {}),
        )

    assert asyncio.run(run_checks()) == ([], [], [], [])
    assert not [stmt for stmt in executed if "INSERT" in str(stmt).upper()]
