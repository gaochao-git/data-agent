"""SQL 执行前安全拦截管线（唯一权威入口）。

所有执行路径（agent 工具、SQL 编辑器任务、执行器边界复检）的执行前校验
都经由 run_sql_guard / check_effective_limit_gate，不允许散落关键词检查。
新增拦截 = 新增一个 check_* 纯函数并挂入管线，不要打开既有函数往里塞。

约束：
- 拒绝文案是报警细分码推断（utils.alarm._infer_sql_block_reason）的契约，
  修改措辞必须同步 utils/alarm.py 的映射。
- 本模块保持纯净：只判定不产生副作用（不报警、不落日志），报警由调用方
  按 violation.internal / violation.reason_code 决定。
"""

import os
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from sqlglot import exp

from utils.alarm import (
    SQL_BLOCK_DANGEROUS_FUNCTION,
    SQL_BLOCK_LIMIT_GATE,
    SQL_BLOCK_LOCKING_READ,
    SQL_BLOCK_MULTI_STATEMENT,
    SQL_BLOCK_ROW_LIMIT,
    SQL_BLOCK_SELECT_INTO,
    SQL_BLOCK_TYPE_NOT_ALLOWED,
)
from ..runtime_config import DEFAULT_ROW_LIMIT  # noqa: F401  同步签名兜底；热值由调用方经 max_rows 传入
from .parser import _parse_sql, _strip_comments_by_scan, extract_top_level_limit_count, validate_raw_single_sql_statement

# 命令类型白名单：默认仅 SELECT，可按部署需要放开 SHOW/DESCRIBE 等只读命令。
_ALLOWED_SQL_TYPES_RAW = os.getenv("CHATDATA_ALLOWED_SQL", "SELECT")
ALLOWED_SQL_TYPES = [s.strip().upper() for s in _ALLOWED_SQL_TYPES_RAW.split(",") if s.strip()]

# 高危函数黑名单（按威胁类别，非穷举；不在名单内的函数不做限制）：
# - LOAD_FILE：读取数据库服务器本地文件（数据外泄）
# - SLEEP / BENCHMARK：人为拖慢查询（资源攻击）
# - GET_LOCK / RELEASE_LOCK：操纵服务器级锁（干扰其他会话）
DANGEROUS_FUNCTIONS = frozenset({"LOAD_FILE", "SLEEP", "BENCHMARK", "GET_LOCK", "RELEASE_LOCK"})

_SELECT_INTO_PATTERN = re.compile(r"\bINTO\s+(?:OUTFILE|DUMPFILE)\b|\bINTO\s+@", re.IGNORECASE)
_LIMIT_MESSAGE_TEMPLATE = "查询结果最多返回 {max_rows} 条，请调整 SQL LIMIT"
_GATE_INTERNAL_MESSAGE = "行数上限校验失败，请稍后重试或联系管理员"


@dataclass(frozen=True)
class SqlGuardViolation:
    """一次拦截的结构化结果。

    internal=True 表示拒绝原因是系统内部缺陷信号（如 LIMIT 终检失败），
    调用方必须记 error 日志并报警；False 表示输入形态问题，拒绝即可。
    syntax_error=True 表示单条语句语法解析失败，提交阶段可选择延迟到
    执行期处理（保留每个执行对象独立失败结果的现有语义）。
    """

    rule: str
    message: str
    internal: bool = False
    syntax_error: bool = False
    reason_code: Optional[str] = None


def _violation(rule: str, message: str, **kwargs) -> SqlGuardViolation:
    return SqlGuardViolation(rule=rule, message=message, **kwargs)


def sql_operation_type(sql: str, source_type: Optional[str] = None) -> str:
    """解析 SQL 命令类型；解析失败返回空串（由单语句规则负责拦截）。"""
    try:
        expressions = _parse_sql(sql, source_type)
    except Exception:
        return ""
    if len(expressions) != 1:
        return ""
    expression = expressions[0]
    if isinstance(expression, exp.Query):
        return "SELECT"
    if isinstance(expression, exp.Show):
        return "SHOW"
    if isinstance(expression, exp.Describe):
        return "EXPLAIN" if str(sql or "").lstrip().upper().startswith("EXPLAIN") else "DESCRIBE"
    return str(getattr(expression, "key", "") or "").strip().upper()


def check_single_statement(sql: str, source_type: Optional[str] = None) -> Optional[SqlGuardViolation]:
    """恰好一条语句；解析失败先分类 INTO 副作用族（安全拦截），否则记为语法错误。"""
    result = validate_raw_single_sql_statement(sql, source_type=source_type)
    if result.success:
        return None
    error = result.error or "SQL 解析失败"
    if "一次只能执行一条 SQL" in error:
        return _violation("single_statement", error, reason_code=SQL_BLOCK_MULTI_STATEMENT)
    into_violation = check_select_into(sql)
    if into_violation is not None:
        return into_violation
    return _violation("single_statement", error, syntax_error=True)


def check_select_into(sql: str, source_type: Optional[str] = None, max_rows: Optional[int] = None) -> Optional[SqlGuardViolation]:
    """SELECT ... INTO OUTFILE/DUMPFILE/@var：写出服务器文件或服务端变量。

    sqlglot 对该族语法解析失败，因此基于剥离注释与字面量后的文本识别，
    避免字符串常量里的 'INTO OUTFILE' 造成误判。
    """
    if not sql:
        return None
    if _SELECT_INTO_PATTERN.search(_text_without_literals(_strip_comments_by_scan(str(sql)))):
        return _violation(
            "select_into",
            "SELECT 语句不允许使用 INTO OUTFILE/DUMPFILE 写出服务器文件",
            reason_code=SQL_BLOCK_SELECT_INTO,
        )
    return None


def check_command_type(sql: str, source_type: Optional[str] = None, max_rows: Optional[int] = None) -> Optional[SqlGuardViolation]:
    """命令类型白名单（默认仅 SELECT，可按部署放开只读命令）。"""
    operation_type = sql_operation_type(sql, source_type)
    if operation_type and operation_type.upper() in ALLOWED_SQL_TYPES:
        return None
    return _violation(
        "command_type",
        f"只允许执行以下类型的 SQL: {', '.join(ALLOWED_SQL_TYPES)}",
        reason_code=SQL_BLOCK_TYPE_NOT_ALLOWED,
    )


def check_dangerous_functions(sql: str, source_type: Optional[str] = None, max_rows: Optional[int] = None) -> Optional[SqlGuardViolation]:
    """高危函数黑名单：LOAD_FILE/SLEEP/BENCHMARK/GET_LOCK/RELEASE_LOCK。"""
    try:
        expressions = _parse_sql(sql, source_type)
    except Exception:
        return None  # 解析失败由 check_single_statement 负责拦截
    if len(expressions) != 1:
        return None
    for node in expressions[0].walk():
        if not isinstance(node, exp.Func):
            continue
        name = str(node.this).strip().upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
        if name in DANGEROUS_FUNCTIONS:
            return _violation(
                "dangerous_functions",
                f"不允许使用高危函数: {name}",
                reason_code=SQL_BLOCK_DANGEROUS_FUNCTION,
            )
    return None


def check_locking_reads(sql: str, source_type: Optional[str] = None, max_rows: Optional[int] = None) -> Optional[SqlGuardViolation]:
    """锁定读（FOR UPDATE/FOR SHARE）属于写意图，只读链路一律拒绝。"""
    try:
        expressions = _parse_sql(sql, source_type)
    except Exception:
        return None
    if len(expressions) != 1:
        return None
    if expressions[0].args.get("locks"):
        return _violation(
            "locking_reads",
            "只读查询不允许使用 FOR UPDATE/FOR SHARE 锁定读",
            reason_code=SQL_BLOCK_LOCKING_READ,
        )
    return None


def check_limit_bound(sql: str, source_type: Optional[str] = None, max_rows: Optional[int] = None) -> Optional[SqlGuardViolation]:
    """显式 LIMIT 超过系统行数上限：响亮拒绝，不做静默截断。

    max_rows 由调用方传入运行时热值；未传时回退导入期兜底值。
    """
    effective_max_rows = DEFAULT_ROW_LIMIT if max_rows is None else max_rows
    count = extract_top_level_limit_count(sql, source_type=source_type)
    if count is not None and count > effective_max_rows:
        return _violation(
            "limit_bound",
            _LIMIT_MESSAGE_TEMPLATE.format(max_rows=effective_max_rows),
            reason_code=SQL_BLOCK_ROW_LIMIT,
        )
    return None


def check_effective_limit_gate(effective_sql: str, source_type: Optional[str] = None, max_rows: Optional[int] = None) -> Optional[SqlGuardViolation]:
    """终检门：进入执行的 SELECT 产物必须带合法最外层 LIMIT。

    与 check_limit_bound 的差别：LIMIT 缺失在这里是违规（补全阶段应已写入；
    缺失意味着补全逻辑存在缺陷），属于系统内部缺陷信号。
    """
    if sql_operation_type(effective_sql, source_type) != "SELECT":
        return None  # SHOW/DESCRIBE 等无行数语义
    effective_max_rows = DEFAULT_ROW_LIMIT if max_rows is None else max_rows
    count = extract_top_level_limit_count(effective_sql, source_type=source_type)
    if count is None or count > effective_max_rows:
        return _violation("limit_gate", _GATE_INTERNAL_MESSAGE, internal=True, reason_code=SQL_BLOCK_LIMIT_GATE)
    return None


# 当前 sqlglot 对 INTO 族解析失败、由 check_single_statement 的解析失败分支拦截；
# check_select_into 同时挂入管线兜底：未来 sqlglot 支持该语法解析时依然不漏拦。
_GUARD_RULES = (
    check_select_into,
    check_command_type,
    check_dangerous_functions,
    check_locking_reads,
    check_limit_bound,
)


def run_sql_guard(
    sql: str,
    source_type: Optional[str] = None,
    max_rows: Optional[int] = None,
    *,
    defer_syntax_error: bool = False,
) -> Optional[SqlGuardViolation]:
    """按序执行全部规则，首个违规即返回；全部通过返回 None。

    max_rows 为行数上限热值（调用方从配置变量表解析后传入；未传回退兜底值）。
    defer_syntax_error=True 时单条语法解析失败不视为违规（提交阶段语义：
    语法错误由每个执行对象生成独立失败结果），多语句仍当场拦截。
    """
    violation = check_single_statement(sql, source_type)
    if violation is not None:
        if violation.syntax_error and defer_syntax_error:
            return None
        return violation
    for rule in _GUARD_RULES:
        violation = rule(sql, source_type=source_type, max_rows=max_rows)
        if violation is not None:
            return violation
    return None


def validate_sql(sql: str, source_type: Optional[str] = None) -> Tuple[bool, str]:
    """历史兼容门面：(is_valid, error_message) 二元组形式执行全部规则。"""
    violation = run_sql_guard(sql, source_type=source_type)
    if violation is not None:
        return False, violation.message
    return True, ""


def _consume_guard_literal_quote(sql: str, i: int, in_quote: Optional[str], result: list[str]) -> tuple[int, Optional[str]]:
    """处理 _text_without_literals 引号内字符，返回 (新索引, 引号状态)。"""
    char = sql[i]
    next_char = sql[i + 1] if i + 1 < len(sql) else ""
    if char == "\\":
        return i + 2, in_quote
    if char == in_quote:
        if in_quote == "'" and next_char == "'":
            result.append(" ")
            return i + 2, in_quote
        return i + 1, None
    return i + 1, in_quote


def _text_without_literals(sql: str) -> str:
    """剥离字符串/标识符字面量，保留 SQL 结构，供注释后文本做关键词判定。"""
    result: list[str] = []
    in_quote: Optional[str] = None
    i = 0
    n = len(sql)
    while i < n:
        char = sql[i]
        if in_quote:
            i, in_quote = _consume_guard_literal_quote(sql, i, in_quote, result)
            continue
        if char in {"'", '"', "`"}:
            in_quote = char
            result.append(" ")
            i += 1
            continue
        result.append(char)
        i += 1
    return "".join(result)


def has_line_comment_marker(line: str) -> bool:
    """判断单行非字符串位置是否存在行注释标记（-- 或 #，MySQL 方言）。

    仅供 LIMIT 终检失败时诊断根因、分叉报错文案使用；识别口径比补全阶段
    （仅 --）宽，因为这里只影响报错文案，不影响 SQL 生成。
    """
    if not line:
        return False
    stripped = _text_without_literals(line)
    return "--" in stripped or "#" in stripped
