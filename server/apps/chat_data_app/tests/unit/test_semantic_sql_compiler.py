"""语义 SQL 编译器门面测试。"""

from apps.chat_data_app.services.semantic.core.semantic_sql import query_plan_compiler
from apps.chat_data_app.services.semantic.core.semantic_sql.compiler import SemanticSQLCompiler, compile_semantic_sql


def test_semantic_sql_compiler_keeps_dialect_context_per_instance(monkeypatch):
    calls = []

    def fake_compile(config, dialect="mysql", max_rows=100):
        calls.append((config, dialect, max_rows))
        return f"SELECT '{dialect}'"

    monkeypatch.setattr(query_plan_compiler, "compile_query_plan_sql", fake_compile)
    hive_compiler = SemanticSQLCompiler(dialect="hive")
    mysql_compiler = SemanticSQLCompiler(dialect="mysql")

    assert hive_compiler.compile({"mainTable": "orders"}) == "SELECT 'hive'"
    assert mysql_compiler.compile({"mainTable": "orders"}) == "SELECT 'mysql'"
    assert calls == [({"mainTable": "orders"}, "hive", 100), ({"mainTable": "orders"}, "mysql", 100)]


def test_compile_semantic_sql_is_the_functional_facade(monkeypatch):
    calls = []

    def fake_compile(config, dialect="mysql", max_rows=100):
        calls.append(max_rows)
        return dialect

    monkeypatch.setattr(query_plan_compiler, "compile_query_plan_sql", fake_compile)
    assert compile_semantic_sql({}, dialect="hive", max_rows=10000) == "hive"
    assert calls == [10000]


def test_semantic_sql_compiler_rejects_unknown_dialect():
    try:
        SemanticSQLCompiler(dialect="unknown")
    except ValueError as exc:
        assert "未知的 SQL 方言" in str(exc)
    else:
        raise AssertionError("unknown dialect should be rejected")
