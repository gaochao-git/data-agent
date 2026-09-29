"""datasource_dao_helpers 分支补测。

覆盖目标：`daos/datasource_dao_helpers.py` 中未被触达的分支（原 12 条缺失分支弧）。

本模块全是不依赖数据库的纯解析/标准化函数，直接构造字典即可驱动。
`_warn_parse_once` 会把 warning key 写进模块级集合，用例之间用 monkeypatch 换成
干净集合，避免"_集合里已有该 key"影响后续断言。
"""

from __future__ import annotations

import pytest

from apps.chat_data_app.daos import datasource_dao_helpers as helpers


@pytest.fixture(autouse=True)
def _isolate_warning_keys(monkeypatch):
    """每个用例使用独立的去重集合，避免跨用例污染。"""
    monkeypatch.setattr(helpers, "_PARSE_WARNING_KEYS", set())


# ---------------------------------------------------------------------------
# _normalize_table_index_items
# （114->115 非数组 / 119->120 非 dict / 122->123 非法类型 / 129->130 无字段 / 133->134 重复）
# ---------------------------------------------------------------------------


def test_normalize_table_index_items_rejects_non_list():
    assert helpers._normalize_table_index_items(None) == []
    assert helpers._normalize_table_index_items("physical") == []
    assert helpers._normalize_table_index_items({"index_type": "primary"}) == []


def test_normalize_table_index_items_skips_non_dict_entries():
    items = ["primary", None, 42, {"index_type": "primary", "columns": ["id"]}]

    assert helpers._normalize_table_index_items(items) == [
        {"index_type": "primary", "index_name": "PRIMARY", "columns": ["id"]}
    ]


def test_normalize_table_index_items_skips_unknown_index_type():
    items = [
        {"index_type": "FULLTEXT", "index_name": "ft_name", "columns": ["name"]},
        {"index_type": "", "columns": ["id"]},
        {"index_type": "  PRIMARY  ", "columns": ["id"]},  # 大小写 + 空格归一化后合法
    ]

    assert helpers._normalize_table_index_items(items) == [
        {"index_type": "primary", "index_name": "PRIMARY", "columns": ["id"]}
    ]


def test_normalize_table_index_items_skips_entries_without_columns():
    items = [
        {"index_type": "unique", "index_name": "uk_missing", "columns": []},
        {"index_type": "unique", "index_name": "uk_blank", "columns": ["  ", ""]},
        {"index_type": "unique", "index_name": "uk_not_list", "columns": "id"},
    ]

    assert helpers._normalize_table_index_items(items) == []


def test_normalize_table_index_items_dedupes_by_type_and_identity():
    """同类型 + 同标识（索引名优先，无名称时用字段组合）只保留一条。"""
    items = [
        {"index_type": "unique", "index_name": "UK_A", "columns": ["a"]},
        {"index_type": "unique", "index_name": "uk_a", "columns": ["b"]},  # 名称大小写不敏感 → 重复
        {"index_type": "secondary", "index_name": "UK_A", "columns": ["a"]},  # 类型不同 → 保留
        {"index_type": "unique", "index_name": "", "columns": ["a"]},  # 无名称 → 用字段兜底
        {"index_type": "unique", "index_name": "", "columns": ["a"]},  # 同上 → 重复
    ]

    result = helpers._normalize_table_index_items(items)

    assert [item["index_name"] for item in result] == ["UK_A", "UK_A", ""]
    assert [item["index_type"] for item in result] == ["unique", "secondary", "unique"]


def test_normalize_table_index_items_forces_primary_name_and_normalizes_columns():
    items = [{"index_type": "primary", "index_name": "my_pk", "columns": [" id ", "id", "name"]}]

    assert helpers._normalize_table_index_items(items) == [
        {"index_type": "primary", "index_name": "PRIMARY", "columns": ["id", "name"]}
    ]


# ---------------------------------------------------------------------------
# _legacy_physical_indexes（146->147 非数组 / 157->158 非 dict / 160->161 空字段名）
# ---------------------------------------------------------------------------


def test_legacy_physical_indexes_rejects_non_list():
    assert helpers._legacy_physical_indexes(None) == []
    assert helpers._legacy_physical_indexes("columns") == []


def test_legacy_physical_indexes_skips_non_dict_and_blank_columns():
    columns = [
        "id",
        None,
        {"name": "  ", "primary_key": True},
        {"primary_key": True},  # 无 name
    ]

    assert helpers._legacy_physical_indexes(columns) == []


def test_legacy_physical_indexes_builds_primary_unique_and_secondary():
    columns = [
        {"name": "id", "primary_key": True},
        {"name": "org_code", "primary_key": True},
        {"name": "phone", "unique_key": True},
        {"name": "city", "index_key": True},
        {"name": "both", "unique_key": True, "index_key": True},
        {"name": "plain"},
    ]

    assert helpers._legacy_physical_indexes(columns) == [
        {"index_type": "primary", "index_name": "PRIMARY", "columns": ["id", "org_code"]},
        {"index_type": "unique", "index_name": "", "columns": ["phone"]},
        {"index_type": "secondary", "index_name": "", "columns": ["city"]},
        {"index_type": "unique", "index_name": "", "columns": ["both"]},
        {"index_type": "secondary", "index_name": "", "columns": ["both"]},
    ]


# ---------------------------------------------------------------------------
# _build_source_line（220->222 三段齐全）
# ---------------------------------------------------------------------------


def test_build_source_line_requires_host_port_and_database():
    assert helpers._build_source_line("127.0.0.1", 3306, "fxcjg") == "127.0.0.1:3306:fxcjg"
    # 数值端口与非字符串入参都要归一化
    assert helpers._build_source_line(" 127.0.0.1 ", " 3306 ", " fxcjg ") == "127.0.0.1:3306:fxcjg"


def test_build_source_line_returns_none_when_any_part_missing():
    assert helpers._build_source_line("", 3306, "fxcjg") is None
    assert helpers._build_source_line("127.0.0.1", None, "fxcjg") is None
    assert helpers._build_source_line("127.0.0.1", 3306, "") is None
    assert helpers._build_source_line("127.0.0.1", 0, "fxcjg") is None


# ---------------------------------------------------------------------------
# _build_member_source_text_line（233->234 http 缺库名 / 242->244 直连成功）
# ---------------------------------------------------------------------------


def test_build_member_source_text_line_requires_org_name_and_code():
    assert helpers._build_member_source_text_line({"org_name": "", "org_code": "ORG1"}) is None
    assert helpers._build_member_source_text_line({"org_name": "财付通", "org_code": ""}) is None
    assert helpers._build_member_source_text_line(None) is None


def test_build_member_source_text_line_http_requires_database():
    """HTTP 执行方式必须显式给出库名（233->234 真分支）。"""
    assert (
        helpers._build_member_source_text_line(
            {"org_name": "财付通", "org_code": "ORG1", "query_executor_type": "http"}
        )
        is None
    )
    assert (
        helpers._build_member_source_text_line(
            {"org_name": "财付通", "org_code": "ORG1", "query_executor_type": "http", "database": "  "}
        )
        is None
    )


def test_build_member_source_text_line_http_uses_database():
    assert (
        helpers._build_member_source_text_line(
            {"org_name": "财付通", "org_code": "ORG1", "query_executor_type": "http", "database": "risk_db"}
        )
        == "财付通|ORG1|http|risk_db"
    )


def test_build_member_source_text_line_direct_uses_source_line():
    """直连方式走 host:port:database（242->244 假分支）。"""
    assert (
        helpers._build_member_source_text_line(
            {
                "org_name": "财付通",
                "org_code": "ORG1",
                "host": "127.0.0.1",
                "port": 3306,
                "database": "fxcjg",
            }
        )
        == "财付通|ORG1|direct|127.0.0.1:3306:fxcjg"
    )


def test_build_member_source_text_line_direct_returns_none_without_source_line():
    """直连但连不上 host:port:database 时返回 None（242->243 真分支）。"""
    assert (
        helpers._build_member_source_text_line(
            {"org_name": "财付通", "org_code": "ORG1", "host": "127.0.0.1", "port": 3306}
        )
        is None
    )


def test_build_member_source_text_line_falls_back_to_direct_for_unknown_executor():
    assert (
        helpers._build_member_source_text_line(
            {
                "org_name": "财付通",
                "org_code": "ORG1",
                "query_executor_type": "unknown",
                "host": "127.0.0.1",
                "port": 3306,
                "database": "fxcjg",
            }
        )
        == "财付通|ORG1|unknown|127.0.0.1:3306:fxcjg"
    )


# ---------------------------------------------------------------------------
# _parse_member_source_entry（311->312 HTTP 库名为空）
# ---------------------------------------------------------------------------


def test_parse_member_source_entry_http_requires_non_empty_database():
    """HTTP 成员源没给库名时丢弃（311->312 真分支）。"""
    assert helpers._parse_member_source_entry("财付通|ORG1|http|", None, "http") is None
    assert helpers._parse_member_source_entry("财付通|ORG1|http|   ", None, "http") is None


def test_parse_member_source_entry_http_takes_database_from_legacy_source_line():
    """HTTP 行里塞了 ip:port:db 时，只取库名并保留 host/port。"""
    parsed = helpers._parse_member_source_entry("财付通|ORG1|http|127.0.0.1:3306:risk_db", None, "http")

    assert parsed == {
        "source_line": "risk_db",
        "host": "127.0.0.1",
        "port": 3306,
        "database": "risk_db",
        "datasource_id": "ORG1",
        "org_name": "财付通",
        "org_code": "ORG1",
        "query_executor_type": "http",
        "raw_line": "财付通|ORG1|http|127.0.0.1:3306:risk_db",
    }


def test_parse_member_source_entry_http_keeps_plain_database():
    parsed = helpers._parse_member_source_entry("财付通|ORG1|http|risk_db", None, "http")

    assert parsed["database"] == "risk_db"
    assert parsed["host"] is None
    assert parsed["port"] is None
    assert parsed["query_executor_type"] == "http"


def test_parse_member_source_entry_direct_parses_source_line():
    parsed = helpers._parse_member_source_entry("财付通|ORG1|direct|127.0.0.1:3306:fxcjg", None, "direct")

    assert parsed["host"] == "127.0.0.1"
    assert parsed["port"] == 3306
    assert parsed["database"] == "fxcjg"
    assert parsed["query_executor_type"] == "direct"
    assert parsed["datasource_id"] == "ORG1"


def test_parse_member_source_entry_direct_drops_invalid_source_line():
    assert helpers._parse_member_source_entry("财付通|ORG1|direct|127.0.0.1:notaport:fxcjg", None, "direct") is None
    assert helpers._parse_member_source_entry("财付通|ORG1|direct|127.0.0.1:3306", None, "direct") is None


def test_parse_member_source_entry_rejects_malformed_lines():
    assert helpers._parse_member_source_entry("", None, "direct") is None
    assert helpers._parse_member_source_entry("   ", None, "direct") is None
    assert helpers._parse_member_source_entry("财付通|ORG1|direct", None, "direct") is None
    assert helpers._parse_member_source_entry("财付通|ORG1|direct|a|b", None, "direct") is None
    assert helpers._parse_member_source_entry("|ORG1|direct|127.0.0.1:3306:fxcjg", None, "direct") is None
    assert helpers._parse_member_source_entry("财付通||direct|127.0.0.1:3306:fxcjg", None, "direct") is None
    assert helpers._parse_member_source_entry("财付通|ORG1|grpc|127.0.0.1:3306:fxcjg", None, "direct") is None


# ---------------------------------------------------------------------------
# _parse_member_source_entries / _parse_member_sources_text 的模板排序
# ---------------------------------------------------------------------------


def test_parse_member_source_entries_puts_template_first():
    text = "\n".join(
        [
            "支付宝|ORG2|direct|10.0.0.2:3306:alipay",
            "财付通|ORG1|direct|10.0.0.1:3306:tenpay",
        ]
    )

    entries = helpers._parse_member_source_entries(text, "10.0.0.1:3306:tenpay", None, "direct")

    assert [entry["org_code"] for entry in entries] == ["ORG1", "ORG2"]


def test_parse_member_source_entries_dedupes_by_org_code():
    text = "\n".join(
        [
            "财付通|ORG1|direct|10.0.0.1:3306:tenpay",
            "财付通新|ORG1|direct|10.0.0.9:3306:tenpay",  # 同 org_code → 丢弃
            "无效行|",
            "支付宝|ORG2|http|alipay_db",
        ]
    )

    ids = helpers._parse_member_sources_text(text, None, None, "direct")

    assert ids == ["ORG1", "ORG2"]


def test_parse_member_source_entries_falls_back_to_first_entry_without_template():
    text = "财付通|ORG1|direct|10.0.0.1:3306:tenpay\n支付宝|ORG2|direct|10.0.0.2:3306:alipay"

    assert helpers._parse_member_sources_text(text, None, None, "direct") == ["ORG1", "ORG2"]
    assert helpers._parse_member_sources_text("", None, None, "direct") == []
