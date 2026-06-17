import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "test")

from apps.chat_data_app.services import sql_parser


def test_strip_comments_scan_handles_quotes_escapes_and_unclosed_comments():
    sql = "select 'a\\'--b', \"#c\", `/*d*/` from orders # mysql comment\nwhere note = 'it''s ok' /* unclosed"

    cleaned = sql_parser._strip_comments_by_scan(sql)

    assert "'a\\'--b'" in cleaned
    assert '"#c"' in cleaned
    assert "`/*d*/`" in cleaned
    assert "# mysql comment" not in cleaned
    assert "/* unclosed" not in cleaned
    assert cleaned.endswith(" ")


def test_strip_comments_for_validation_respects_mysql_dash_comment_rules():
    mysql_sql = "select 1--1; drop table orders"
    hive_sql = "select 1--1; drop table orders"

    assert "drop table orders" in sql_parser._strip_comments_for_validation(mysql_sql, "mysql")
    assert "drop table orders" not in sql_parser._strip_comments_for_validation(hive_sql, "hive")
    assert sql_parser._strip_comments_for_validation("select 1 # comment", "mysql").strip() == "select 1"
    assert sql_parser._strip_comments_for_validation("select 1 # not-comment", "hive") == "select 1 # not-comment"
    assert sql_parser._strip_comments_for_validation("select 'a\\'--b', \"#c\", `/*d*/`", "mysql") == "select 'a\\'--b', \"#c\", `/*d*/`"
    assert sql_parser._strip_comments_for_validation("select 'it''s -- ok'", "mysql") == "select 'it''s -- ok'"
    assert sql_parser._strip_comments_for_validation("select 1 -- comment\nfrom dual", "mysql") == "select 1 \nfrom dual"
    assert sql_parser._strip_comments_for_validation("select 1 # comment\nfrom dual", "mysql") == "select 1 \nfrom dual"
    assert sql_parser._strip_comments_for_validation("select 1 /* comment */ from dual", "mysql") == "select 1   from dual"
    assert sql_parser._strip_comments_for_validation("select 1 /* unclosed", "mysql") == "select 1  "


def test_validation_dash_comment_helper_branches():
    assert sql_parser._is_validation_dash_comment("select 1", 0, "mysql") is False
    assert sql_parser._is_validation_dash_comment("-- comment", 0, "mysql") is True
    assert sql_parser._is_validation_dash_comment("--comment", 0, "mysql") is False
    assert sql_parser._is_validation_dash_comment("--comment", 0, "hive") is True


def test_split_sql_statements_handles_quoted_semicolons_and_escaped_quotes():
    statements = sql_parser._split_sql_statements(
        "select ';' as semi, 'it''s; ok' as txt; select `semi;col` from t;"
    )

    assert len(statements) == 2
    assert statements[0].startswith("select ';' as semi")
    assert statements[1] == "select `semi;col` from t"


@pytest.mark.parametrize(
    ("sql", "source_type", "expected_success", "expected_sql", "expected_error"),
    [
        ("   ", "mysql", False, "", "SQL 为空"),
        ("-- only comment", "mysql", False, "", "SQL 注释移除后为空"),
        ("select 1; select 2", "mysql", False, "", "一次只能执行一条 SQL"),
        ("show tables", "mysql", True, "show tables", ""),
        ("select /* comment */ 1", "mysql", True, "SELECT 1", ""),
    ],
)
def test_prepare_sql_for_execution_error_and_command_branches(sql, source_type, expected_success, expected_sql, expected_error):
    result = sql_parser.prepare_sql_for_execution(sql, source_type)

    assert result.success is expected_success
    if expected_success:
        assert result.sql == expected_sql
    else:
        assert expected_error in result.error


def test_prepare_sql_for_execution_parse_exception_and_empty_normalized(monkeypatch):
    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse boom")))
    failed = sql_parser.prepare_sql_for_execution("select 1", "mysql")
    assert failed.success is False
    assert failed.error == "parse boom"

    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: [])
    empty = sql_parser.prepare_sql_for_execution("select 1", "mysql")
    assert empty.success is False
    assert empty.error == "SQL 解析失败"

    class BlankExpression:
        def sql(self, **_kwargs):
            return "   "

    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: [BlankExpression()])
    blank = sql_parser.prepare_sql_for_execution("select 1", "mysql")
    assert blank.success is False
    assert "注释移除后为空" in blank.error


def test_validate_single_sql_statement_error_and_non_query_branches(monkeypatch):
    assert sql_parser.validate_single_sql_statement("", "mysql").error == "SQL 为空"
    assert "注释移除后为空" in sql_parser.validate_single_sql_statement("-- only comment", "mysql").error
    assert "一次只能执行一条 SQL" in sql_parser.validate_single_sql_statement("select 1; select 2", "mysql").error

    command = sql_parser.validate_single_sql_statement("show tables", "mysql")
    assert command.success is True
    assert command.sql == "show tables"

    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse boom")))
    failed = sql_parser.validate_single_sql_statement("select 1", "mysql")
    assert failed.success is False
    assert failed.error == "parse boom"

    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: [])
    empty = sql_parser.validate_single_sql_statement("select 1", "mysql")
    assert empty.success is False
    assert empty.error == "SQL 解析失败"

    monkeypatch.setattr(sql_parser, "_split_sql_statements", lambda _sql: [])
    split_empty = sql_parser.validate_single_sql_statement("select 1", "mysql")
    assert split_empty.success is False
    assert "注释移除后为空" in split_empty.error

    monkeypatch.setattr(sql_parser, "_split_sql_statements", lambda sql: [sql])
    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: [object(), object()])
    parsed_multi = sql_parser.validate_single_sql_statement("select 1", "mysql")
    assert parsed_multi.success is False
    assert "一次只能执行一条 SQL" in parsed_multi.error


def test_validate_raw_single_sql_statement_branches(monkeypatch):
    assert sql_parser.validate_raw_single_sql_statement("").error == "SQL 为空"
    assert sql_parser.validate_raw_single_sql_statement(";").error == "SQL 为空"
    assert sql_parser.validate_raw_single_sql_statement(" ; ; ").error == "SQL 为空"
    assert "一次只能执行一条 SQL" in sql_parser.validate_raw_single_sql_statement("select 1; select 2").error

    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse boom")))
    failed = sql_parser.validate_raw_single_sql_statement("select 1")
    assert failed.success is False
    assert failed.error == "parse boom"

    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: [])
    empty = sql_parser.validate_raw_single_sql_statement("select 1")
    assert empty.success is False
    assert empty.error == "SQL 解析失败"

    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: [object(), object()])
    multi = sql_parser.validate_raw_single_sql_statement("select 1")
    assert multi.success is False
    assert "一次只能执行一条 SQL" in multi.error


def test_extract_top_level_limit_count_branches(monkeypatch):
    assert sql_parser.extract_top_level_limit_count("") is None
    assert sql_parser.extract_top_level_limit_count("select * from orders") is None
    assert sql_parser.extract_top_level_limit_count("select * from orders limit 12") == 12
    assert sql_parser.extract_top_level_limit_count("select * from orders limit 'x'") is None

    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse boom")))
    assert sql_parser.extract_top_level_limit_count("select * from orders limit 1") is None

    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: [object(), object()])
    assert sql_parser.extract_top_level_limit_count("select 1; select 2") is None

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "sqlglot":
            raise RuntimeError("import boom")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert sql_parser.extract_top_level_limit_count("select * from orders limit 1") is None


def test_extract_physical_tables_branches(monkeypatch):
    assert sql_parser.extract_physical_tables("").error == "SQL 为空"

    result = sql_parser.extract_physical_tables(
        "with recent_orders as (select * from orders) select * from recent_orders join users on recent_orders.user_id = users.user_id",
        "mysql",
    )
    assert result.success is True
    assert result.tables == {"orders", "users"}

    monkeypatch.setattr(sql_parser, "_parse_sql", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse boom")))
    failed = sql_parser.extract_physical_tables("select * from orders")
    assert failed.success is False
    assert failed.error == "parse boom"
