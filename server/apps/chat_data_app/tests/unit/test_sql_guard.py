import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

import pytest
from pydantic import ValidationError

from apps.chat_data_app.schema import ExecuteSqlRequest, QueryTaskCreateRequest
from apps.chat_data_app.services.sql_run import executor as sql_executor
from apps.chat_data_app.services.sql_run import runtime as query_runtime
from apps.chat_data_app.services.sql_run.runtime import _coerce_member_result
from apps.chat_data_app.services.sql_run.sql_guard import (
    check_effective_limit_gate,
    run_sql_guard,
    validate_sql,
)


# ============ 管线规则：单语句与命令类型 ============

def test_clean_select_and_metadata_commands_pass():
    assert run_sql_guard("SELECT * FROM orders LIMIT 50", "mysql") is None
    assert run_sql_guard("SELECT 1", "hive") is None


def test_multi_statement_blocked_with_security_reason():
    violation = run_sql_guard("SELECT 1; DROP TABLE t", "mysql")
    assert violation.rule == "single_statement"
    assert violation.reason_code == "MULTI_STATEMENT_BLOCKED"
    assert violation.syntax_error is False


def test_syntax_error_deferrable_at_submit():
    blocked = run_sql_guard("SELEC 1", "mysql")
    assert blocked.syntax_error is True
    assert blocked.reason_code is None
    # 提交阶段延迟到执行期处理
    assert run_sql_guard("SELEC 1", "mysql", defer_syntax_error=True) is None


def test_command_type_whitelist_rejects_writes():
    for sql in ("UPDATE t SET a=1", "DELETE FROM t", "INSERT INTO t VALUES (1)", "DROP TABLE t", "CALL do_x()"):
        violation = run_sql_guard(sql, "mysql")
        assert violation.rule == "command_type", sql
        assert violation.reason_code == "SQL_TYPE_NOT_ALLOWED"


# ============ 管线规则：副作用构造 ============

def test_select_into_family_blocked_as_side_effect():
    for sql in (
        "SELECT * FROM t INTO OUTFILE '/tmp/x'",
        "SELECT * FROM t INTO DUMPFILE '/tmp/x'",
        "SELECT * FROM t INTO @a, @b",
    ):
        violation = run_sql_guard(sql, "mysql")
        assert violation.rule == "select_into", sql
        assert violation.reason_code == "SELECT_INTO_BLOCKED"


def test_select_into_not_triggered_by_string_literal_or_comment():
    assert run_sql_guard("SELECT 'INTO OUTFILE' AS note FROM t", "mysql") is None
    assert run_sql_guard("SELECT 1 -- INTO OUTFILE", "mysql") is None


def test_dangerous_functions_blocked():
    for expression in ("LOAD_FILE('/etc/passwd')", "SLEEP(5)", "BENCHMARK(1, MD5('x'))", "GET_LOCK('a', 1)", "RELEASE_LOCK('a')"):
        violation = run_sql_guard(f"SELECT {expression}", "mysql")
        assert violation.rule == "dangerous_functions", expression
        assert violation.reason_code == "DANGEROUS_FUNCTION_BLOCKED"


def test_ordinary_functions_not_blocked():
    assert run_sql_guard("SELECT SUM(amount), DATE_FORMAT(a, '%Y'), MD5(id) FROM t", "mysql") is None


def test_locking_reads_blocked():
    violation = run_sql_guard("SELECT * FROM t WHERE id=1 FOR UPDATE", "mysql")
    assert violation.rule == "locking_reads"
    assert violation.reason_code == "LOCKING_READ_BLOCKED"
    assert run_sql_guard("SELECT * FROM t WHERE id=1", "mysql") is None


# ============ 管线规则：LIMIT 与方言 ============

def test_explicit_limit_over_cap_rejected_with_stable_message():
    violation = run_sql_guard("SELECT * FROM t LIMIT 150", "mysql")
    assert violation.rule == "limit_bound"
    assert violation.reason_code == "ROW_LIMIT_EXCEEDED"
    assert violation.message == "查询结果最多返回 100 条，请调整 SQL LIMIT"


def test_limit_bound_dialect_quirks():
    # MySQL 逗号写法提取行数而非 offset；Hive OFFSET 形态同理
    assert run_sql_guard("SELECT * FROM t LIMIT 2, 5", "mysql") is None
    assert run_sql_guard("SELECT * FROM t LIMIT 5 OFFSET 2", "hive") is None
    assert run_sql_guard("SELECT * FROM t LIMIT 2, 500", "mysql") is not None
    assert run_sql_guard("SELECT * FROM t LIMIT 500 OFFSET 2", "hive") is not None


def test_subquery_limit_does_not_count_as_top_level():
    # 子查询 LIMIT 不约束最外层结果：无顶层 LIMIT → 走补全语义，不算超限
    assert run_sql_guard("SELECT a FROM (SELECT * FROM t LIMIT 999) x", "mysql") is None


# ============ 终检门与 build_effective_sql ============

def test_effective_limit_gate_requires_top_level_limit():
    assert check_effective_limit_gate("SELECT * FROM t LIMIT 100", "mysql") is None
    assert check_effective_limit_gate("SELECT * FROM t", "mysql") is not None
    assert check_effective_limit_gate("SELECT * FROM t LIMIT 150", "mysql") is not None
    # 非查询型语句无行数语义
    assert check_effective_limit_gate("SHOW TABLES", "mysql") is None


def test_build_effective_sql_appends_limit_for_clean_queries():
    effective = sql_executor.build_effective_sql("SELECT * FROM t", 100, source_type="mysql")
    assert effective.endswith("LIMIT 100")
    assert "LIMIT 50" in sql_executor.build_effective_sql("SELECT * FROM t LIMIT 50", 100, source_type="mysql")


def test_build_effective_sql_hash_comment_rejected_with_actionable_message():
    with pytest.raises(ValueError, match="末尾的注释"):
        sql_executor.build_effective_sql("SELECT * FROM t #备注", 100, source_type="mysql")


def test_build_effective_sql_dash_comment_limit_lands_on_new_line():
    # -- 注释是补全阶段已覆盖的形态：换行追加后 LIMIT 生效，不触发终检失败
    effective = sql_executor.build_effective_sql("SELECT * FROM t -- 备注", 100, source_type="mysql")
    assert "LIMIT 100" in effective.splitlines()[-1]


def test_build_effective_sql_internal_gate_failure_alarms(monkeypatch):
    alarms = []

    def fake_alarm(sql, reason, *, source_type=None, execution_scope=None):
        alarms.append((sql, reason, execution_scope))

    monkeypatch.setattr(sql_executor, "_emit_sql_execution_validation_alarm", fake_alarm)
    # 模拟补全逻辑缺陷：append 未写入 LIMIT → 终检按内部缺陷拒绝并报警
    monkeypatch.setattr(sql_executor, "_append_limit", lambda sql, limit: sql)
    with pytest.raises(ValueError, match="行数上限校验失败"):
        sql_executor.build_effective_sql("SELECT * FROM t", 100, source_type="mysql")
    assert len(alarms) == 1
    assert alarms[0][2] == "limit_gate"


def test_violation_messages_round_trip_through_alarm_inference():
    """拒绝文案与报警细分码的契约：每条规则的 message 必须被推断表映射回原码。

    这是防止"改文案弄断报警"的护栏；新增规则时把它的代表性 SQL 加进本表。
    """
    from utils.alarm import _infer_sql_block_reason

    representative_cases = [
        ("SELECT 1; DROP TABLE t", "mysql", "MULTI_STATEMENT_BLOCKED"),
        ("UPDATE t SET a=1", "mysql", "SQL_TYPE_NOT_ALLOWED"),
        ("SELECT * FROM t INTO OUTFILE '/tmp/x'", "mysql", "SELECT_INTO_BLOCKED"),
        ("SELECT LOAD_FILE('/etc/passwd')", "mysql", "DANGEROUS_FUNCTION_BLOCKED"),
        ("SELECT * FROM t WHERE id=1 FOR UPDATE", "mysql", "LOCKING_READ_BLOCKED"),
        ("SELECT * FROM t LIMIT 150", "mysql", "ROW_LIMIT_EXCEEDED"),
    ]
    for sql, dialect, expected_code in representative_cases:
        violation = run_sql_guard(sql, dialect)
        assert violation is not None, sql
        assert _infer_sql_block_reason(violation.message) == expected_code, violation.message


def test_validate_sql_compat_surface():
    assert validate_sql("SELECT 1", "mysql") == (True, "")
    ok, message = validate_sql("DROP TABLE t", "mysql")
    assert ok is False
    assert message.startswith("只允许执行以下类型的 SQL")
    assert sql_executor.validate_sql("SELECT 1", "mysql") == (True, "")


# ============ 超时上限 ============

def test_resolve_query_timeout_clamps_request_value():
    assert sql_executor.resolve_query_timeout("mysql", 10**9) == 3600
    assert sql_executor.resolve_query_timeout("mysql", 30) == 30
    assert sql_executor.resolve_query_timeout("mysql", None) == 60
    assert sql_executor.resolve_query_timeout("hive", None) == 600


def test_schema_rejects_timeout_over_cap():
    with pytest.raises(ValidationError):
        ExecuteSqlRequest(datasource_id="ds_a", sql="select 1", timeout=7200)
    with pytest.raises(ValidationError):
        QueryTaskCreateRequest(datasource_id="ds_a", sql=["select 1"], timeout=7200)
    assert ExecuteSqlRequest(datasource_id="ds_a", sql="select 1", timeout=3600).timeout == 3600


# ============ 成员级联隔离 ============

def test_coerce_member_result_isolates_exceptions():
    ok = {"status": "succeeded", "columns": ["a"], "data": [[1]], "total_count": 1}
    assert _coerce_member_result(ok) is ok
    failure = _coerce_member_result(RuntimeError("db jitter"))
    assert failure["status"] == "failed"
    assert "db jitter" in failure["error"]
    assert failure["columns"] == []


def test_execute_sql_for_member_survives_close_and_persist_failures(monkeypatch):
    """关闭/落库异常只损失留痕，不得改变成员结果或向 gather 冒泡。"""
    import asyncio

    target = {"member_datasource_id": "m1", "member_name": "成员1", "source_type": "mysql"}
    calls = []

    async def record_running(execution_id, **kwargs):
        calls.append(("running", execution_id))

    async def failing_persist(execution_id, result, **kwargs):
        calls.append(("final", execution_id))
        raise RuntimeError("db jitter")

    async def running_status(_execution_id):
        return "running"

    class FakeExecutor:
        async def execute(self, *args, **kwargs):
            return {"status": "succeeded", "columns": ["a"], "data": [[1]], "total_count": 1}

        async def close(self):
            raise RuntimeError("close boom")

    monkeypatch.setattr(query_runtime, "get_executor", lambda _target: FakeExecutor())
    monkeypatch.setattr(query_runtime, "get_query_execution_status", running_status)
    monkeypatch.setattr(query_runtime, "update_query_execution", record_running)
    monkeypatch.setattr(query_runtime, "update_query_execution_from_result", failing_persist)

    result = asyncio.run(query_runtime.execute_sql_for_member(
        execution_target=target,
        sql="SELECT * FROM t",
        limit=10,
        timeout=None,
        execution_id="ex_1",
    ))
    assert result["status"] == "succeeded"
    assert ("final", "ex_1") in calls
