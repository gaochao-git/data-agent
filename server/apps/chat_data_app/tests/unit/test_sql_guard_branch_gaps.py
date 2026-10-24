"""sql_guard 覆盖率补充：解析失败兜底、字面量剥离转义与注释诊断。"""

from apps.chat_data_app.services.sql_run import sql_guard


def test_check_select_into_ignores_empty_sql():
    assert sql_guard.check_select_into("") is None
    assert sql_guard.check_select_into(None) is None


def test_dangerous_functions_returns_none_when_parse_fails(monkeypatch):
    def failing_parse(_sql, _source_type=None):
        raise ValueError("cannot parse")

    monkeypatch.setattr(sql_guard, "_parse_sql", failing_parse)
    assert sql_guard.check_dangerous_functions("SELECT 1", "mysql") is None


def test_dangerous_functions_returns_none_for_multiple_statements(monkeypatch):
    monkeypatch.setattr(sql_guard, "_parse_sql", lambda _sql, _source_type=None: [])
    assert sql_guard.check_dangerous_functions("SELECT 1", "mysql") is None


def test_locking_reads_returns_none_when_parse_fails(monkeypatch):
    def failing_parse(_sql, _source_type=None):
        raise ValueError("cannot parse")

    monkeypatch.setattr(sql_guard, "_parse_sql", failing_parse)
    assert sql_guard.check_locking_reads("SELECT 1", "mysql") is None


def test_locking_reads_returns_none_for_unexpected_expression_count(monkeypatch):
    monkeypatch.setattr(sql_guard, "_parse_sql", lambda _sql, _source_type=None: [])
    assert sql_guard.check_locking_reads("SELECT 1", "mysql") is None


def test_text_without_literals_handles_escaped_and_doubled_quotes():
    stripped = sql_guard._text_without_literals("SELECT 'a\\'b' FROM t")
    assert "FROM" in stripped

    doubled = sql_guard._text_without_literals("SELECT 'it''s' INTO OUTFILE '/tmp/x'")
    assert "INTO OUTFILE" in doubled
    assert "it" not in doubled and "/tmp/x" not in doubled


def test_has_line_comment_marker_handles_empty_line():
    assert sql_guard.has_line_comment_marker("") is False
    assert sql_guard.has_line_comment_marker("SELECT 1 -- note") is True
    assert sql_guard.has_line_comment_marker("SELECT '#' AS tag") is False
