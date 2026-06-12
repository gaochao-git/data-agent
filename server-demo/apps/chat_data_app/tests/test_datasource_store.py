import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")

from apps.chat_data_app.services.datasource_store import _parse_member_source_entries, DataSourceDBStore


def test_parse_member_source_entries_keeps_hive_http_shared_connection_members():
    entries = _parse_member_source_entries(
        "ns_a|http|127.0.0.1:10000:dw\nns_b|http|127.0.0.1:10000:dw",
        "ns_a|http|127.0.0.1:10000:dw",
        "hive",
        "http",
    )

    assert [item["datasource_id"] for item in entries] == ["ns_a", "ns_b"]
    assert [item["source_line"] for item in entries] == ["127.0.0.1:10000:dw", "127.0.0.1:10000:dw"]


def test_parse_member_source_entries_keeps_mysql_http_members():
    entries = _parse_member_source_entries(
        "mysql_http_a|http|127.0.0.1:3306:test_db\nmysql_http_b|http|127.0.0.1:3306:test_db",
        "mysql_http_a|http|127.0.0.1:3306:test_db",
        "mysql",
        "http",
    )

    assert [item["datasource_id"] for item in entries] == ["mysql_http_a", "mysql_http_b"]
    assert [item["query_executor_type"] for item in entries] == ["http", "http"]


def test_parse_member_source_entries_rejects_legacy_format():
    entries = _parse_member_source_entries(
        "direct|127.0.0.1:3306:test_db\n127.0.0.1:3306:test_db",
        "direct|127.0.0.1:3306:test_db",
        "mysql",
        "direct",
    )

    assert entries == []


def test_map_group_row_uses_template_namespace_for_hive_http():
    store = DataSourceDBStore.__new__(DataSourceDBStore)

    datasource = store._map_group_row({
        "group_id": "ds_hive_http",
        "group_name": "HiveHTTP组",
        "group_desc": "",
        "source_type": "hive",
        "query_executor_type": "http",
        "template_source": "ns_a|http|127.0.0.1:10000:dw",
        "member_sources_text": "ns_a|http|127.0.0.1:10000:dw\nns_b|http|127.0.0.1:10000:dw",
        "mapped_modules_json": "[]",
        "is_default": 0,
        "create_time": None,
        "update_time": None,
        "create_by": "",
        "update_by": "",
    })

    assert datasource["template_datasource_id"] == "ns_a"
    assert datasource["source_config"] == {"host": "127.0.0.1", "port": 10000, "database": "dw"}


def test_soft_delete_datasource_keeps_new_template_raw_line():
    store = DataSourceDBStore.__new__(DataSourceDBStore)
    updated_groups = []

    store._fetchall = lambda _sql: [{
        "group_id": "ds_group_a",
        "source_type": "mysql",
        "template_source": "mysql_http_a|http|127.0.0.1:3306:test_db",
        "member_sources_text": "mysql_http_a|http|127.0.0.1:3306:test_db\nmysql_http_b|http|127.0.0.1:3306:test_db",
    }]
    store.update_datasource_group = lambda **kwargs: updated_groups.append(kwargs)

    store.soft_delete_datasource("mysql_http_a")

    assert updated_groups == [{
        "group_id": "ds_group_a",
        "template_source": "mysql_http_b|http|127.0.0.1:3306:test_db",
        "member_sources_text": "mysql_http_b|http|127.0.0.1:3306:test_db",
        "update_by": "",
    }]


def test_has_datasource_checks_group_row_existence():
    store = DataSourceDBStore.__new__(DataSourceDBStore)
    calls = []

    def _fake_fetchone(sql, params):
        calls.append((sql, params))
        return {"1": 1} if params == ("group_a",) else None

    store._fetchone = _fake_fetchone

    assert store.has_datasource("group_a") is True
    assert store.has_datasource("missing") is False
    assert store.has_datasource("") is False
    assert len(calls) == 2


def test_get_semantic_configs_uses_group_row_directly():
    store = DataSourceDBStore.__new__(DataSourceDBStore)
    store._get_group_row_by_id = lambda datasource_id: {"group_id": "group_a"} if datasource_id == "ds_a" else None
    store.get_datasource = lambda _datasource_id: (_ for _ in ()).throw(AssertionError("should not load datasource detail"))
    store.get_semantic_configs_by_group_id = lambda group_id: [{"group_id": group_id, "semantic_config_id": "cfg_1"}]

    configs = store.get_semantic_configs("ds_a")

    assert configs == [{"group_id": "group_a", "semantic_config_id": "cfg_1"}]
    assert store.get_semantic_configs("missing") == []
