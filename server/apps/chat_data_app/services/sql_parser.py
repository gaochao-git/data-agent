"""SQL 解析工具。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Set


@dataclass
class SqlParseResult:
    """SQL 解析结果。"""

    success: bool
    tables: Set[str] = field(default_factory=set)
    error: str = ""


@dataclass
class SqlPrepareResult:
    """SQL 预处理结果。"""

    success: bool
    sql: str = ""
    error: str = ""


@dataclass
class SqlValidateResult:
    """SQL 校验结果。"""

    success: bool
    sql: str = ""
    error: str = ""


def _normalize_dialect(source_type: str | None) -> str:
    """标准化 sqlglot 方言。"""
    normalized = str(source_type or "").strip().lower()
    if normalized in {"hive", "mysql"}:
        return normalized
    return "mysql"


def _parse_sql(sql: str, source_type: str | None = None):
    """使用 sqlglot 解析 SQL。"""
    import sqlglot

    dialect = _normalize_dialect(source_type)
    expressions = []
    for expr in sqlglot.parse(sql, read=dialect):
        if expr is None:
            continue
        if not expr.sql(dialect=dialect, comments=False).strip():
            continue
        expressions.append(expr)
    return expressions


def _strip_comments_by_scan(sql: str) -> str:
    """删除真实 SQL 注释，保留字符串和反引号内的注释符。"""
    text = str(sql or "")
    result: list[str] = []
    in_quote = ""
    i = 0
    while i < len(text):
        char = text[i]
        next_char = text[i + 1] if i + 1 < len(text) else ""
        if in_quote:
            result.append(char)
            if char == "\\":
                if i + 1 < len(text):
                    result.append(text[i + 1])
                    i += 2
                    continue
            elif char == in_quote:
                if in_quote == "'" and next_char == "'":
                    result.append(next_char)
                    i += 2
                    continue
                in_quote = ""
            i += 1
            continue

        if char in {"'", '"', "`"}:
            in_quote = char
            result.append(char)
            i += 1
            continue
        if char == "-" and next_char == "-":
            newline_index = text.find("\n", i + 2)
            if newline_index == -1:
                break
            result.append("\n")
            i = newline_index + 1
            continue
        if char == "#":
            newline_index = text.find("\n", i + 1)
            if newline_index == -1:
                break
            result.append("\n")
            i = newline_index + 1
            continue
        if char == "/" and next_char == "*":
            comment_end = text.find("*/", i + 2)
            result.append(" ")
            i = len(text) if comment_end == -1 else comment_end + 2
            continue
        result.append(char)
        i += 1
    return "".join(result)


def _is_validation_dash_comment(text: str, index: int, dialect: str) -> bool:
    """按方言判断当前位置是否为校验用 -- 行注释。"""
    next_char = text[index + 1] if index + 1 < len(text) else ""
    if next_char != "-":
        return False
    if dialect != "mysql":
        return True
    following = text[index + 2] if index + 2 < len(text) else ""
    return not following or following.isspace()


def _strip_comments_for_validation(sql: str, source_type: str | None = None) -> str:
    """删除校验用注释，避免按过宽注释规则吞掉真实语句分隔符。"""
    text = str(sql or "")
    dialect = _normalize_dialect(source_type)
    result: list[str] = []
    in_quote = ""
    i = 0
    while i < len(text):
        char = text[i]
        next_char = text[i + 1] if i + 1 < len(text) else ""
        if in_quote:
            result.append(char)
            if char == "\\":
                if i + 1 < len(text):
                    result.append(text[i + 1])
                    i += 2
                    continue
            elif char == in_quote:
                if in_quote == "'" and next_char == "'":
                    result.append(next_char)
                    i += 2
                    continue
                in_quote = ""
            i += 1
            continue

        if char in {"'", '"', "`"}:
            in_quote = char
            result.append(char)
            i += 1
            continue
        if char == "-" and _is_validation_dash_comment(text, i, dialect):
            newline_index = text.find("\n", i + 2)
            if newline_index == -1:
                break
            result.append("\n")
            i = newline_index + 1
            continue
        if char == "#" and dialect == "mysql":
            newline_index = text.find("\n", i + 1)
            if newline_index == -1:
                break
            result.append("\n")
            i = newline_index + 1
            continue
        if char == "/" and next_char == "*":
            comment_end = text.find("*/", i + 2)
            result.append(" ")
            i = len(text) if comment_end == -1 else comment_end + 2
            continue
        result.append(char)
        i += 1
    return "".join(result)


def _is_query_sql(sql: str) -> bool:
    """判断是否为查询类 SQL。"""
    normalized = str(sql or "").strip().lstrip("(").strip().lower()
    return normalized.startswith("select ") or normalized.startswith("with ")


def _split_sql_statements(sql: str) -> list[str]:
    """按非字符串位置的分号拆分 SQL。"""
    statements: list[str] = []
    current: list[str] = []
    in_quote = ""
    i = 0
    while i < len(sql):
        char = sql[i]
        next_char = sql[i + 1] if i + 1 < len(sql) else ""
        if in_quote:
            current.append(char)
            if char == "\\":
                if i + 1 < len(sql):
                    current.append(next_char)
                    i += 2
                    continue
            elif char == in_quote:
                if in_quote == "'" and next_char == "'":
                    current.append(next_char)
                    i += 2
                    continue
                in_quote = ""
            i += 1
            continue
        if char in {"'", '"', "`"}:
            in_quote = char
            current.append(char)
            i += 1
            continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


def prepare_sql_for_execution(sql: str, source_type: str | None = None) -> SqlPrepareResult:
    """生成执行 SQL：查询类 SQL 使用 sqlglot 去注释并标准化，命令类 SQL 仅去注释。"""
    text = str(sql or "").strip()
    if not text:
        return SqlPrepareResult(success=False, error="SQL 为空")

    cleaned_text = _strip_comments_by_scan(text).strip().rstrip(";").strip()
    if not cleaned_text:
        return SqlPrepareResult(success=False, error="SQL 注释移除后为空，请输入有效 SQL")

    statements = _split_sql_statements(cleaned_text)
    if not statements:
        return SqlPrepareResult(success=False, error="SQL 注释移除后为空，请输入有效 SQL")
    if len(statements) != 1:
        return SqlPrepareResult(success=False, error="一次只能执行一条 SQL，请拆分后分别执行")
    single_sql = statements[0]

    if not _is_query_sql(single_sql):
        return SqlPrepareResult(success=True, sql=single_sql)

    try:
        parsed_expressions = _parse_sql(single_sql, source_type)
    except Exception as err:
        return SqlPrepareResult(success=False, error=str(err) or "SQL 解析失败")
    if not parsed_expressions:
        return SqlPrepareResult(success=False, error="SQL 解析失败")
    if len(parsed_expressions) != 1:
        return SqlPrepareResult(success=False, error="一次只能执行一条 SQL，请拆分后分别执行")

    normalized_items = [
        expr.sql(dialect=_normalize_dialect(source_type), comments=False).strip()
        for expr in parsed_expressions
        if expr is not None and expr.sql(dialect=_normalize_dialect(source_type), comments=False).strip()
    ]
    if not normalized_items:
        return SqlPrepareResult(success=False, error="SQL 注释移除后为空，请输入有效 SQL")
    return SqlPrepareResult(success=True, sql="; ".join(normalized_items))


def validate_single_sql_statement(sql: str, source_type: str | None = None) -> SqlValidateResult:
    """校验 SQL 仅包含一条语句；查询类 SQL 只解析不改写。"""
    text = str(sql or "").strip()
    if not text:
        return SqlValidateResult(success=False, error="SQL 为空")

    cleaned_text = _strip_comments_for_validation(text, source_type=source_type).strip().rstrip(";").strip()
    if not cleaned_text:
        return SqlValidateResult(success=False, error="SQL 注释移除后为空，请输入有效 SQL")

    statements = _split_sql_statements(cleaned_text)
    if not statements:
        return SqlValidateResult(success=False, error="SQL 注释移除后为空，请输入有效 SQL")
    if len(statements) != 1:
        return SqlValidateResult(success=False, error="一次只能执行一条 SQL，请拆分后分别执行")
    single_sql = statements[0]

    if not _is_query_sql(single_sql):
        return SqlValidateResult(success=True, sql=single_sql)

    try:
        parsed_expressions = _parse_sql(single_sql, source_type)
    except Exception as err:
        return SqlValidateResult(success=False, error=str(err) or "SQL 解析失败")
    if not parsed_expressions:
        return SqlValidateResult(success=False, error="SQL 解析失败")
    if len(parsed_expressions) != 1:
        return SqlValidateResult(success=False, error="一次只能执行一条 SQL，请拆分后分别执行")
    return SqlValidateResult(success=True, sql=single_sql)


def extract_top_level_limit_count(sql: str, source_type: str | None = None) -> int | None:
    """解析最外层 LIMIT 返回行数，仅返回字面量整数。"""
    text = str(sql or "").strip()
    if not text:
        return None
    try:
        expressions = _parse_sql(text, source_type)
    except Exception:
        return None
    if len(expressions) != 1:
        return None

    try:
        from sqlglot import exp
    except Exception:
        return None

    limit = expressions[0].args.get("limit")
    if not isinstance(limit, exp.Limit):
        return None
    limit_expression = limit.expression
    if not isinstance(limit_expression, exp.Literal) or limit_expression.is_string:
        return None
    try:
        return int(str(limit_expression.this).strip())
    except (TypeError, ValueError):
        return None


def extract_physical_tables(sql: str, source_type: str | None = None) -> SqlParseResult:
    """提取 SQL 涉及的物理表名，排除 CTE 名称。"""
    text = str(sql or "").strip()
    if not text:
        return SqlParseResult(success=False, error="SQL 为空")

    try:
        from sqlglot import exp

        expressions = _parse_sql(text, source_type)
    except Exception as err:
        return SqlParseResult(success=False, error=str(err) or "SQL 解析失败")

    tables: Set[str] = set()
    for expression in expressions:
        cte_names = {
            str(cte.alias_or_name or "").strip().lower()
            for cte in expression.find_all(exp.CTE)
            if str(cte.alias_or_name or "").strip()
        }
        for table in expression.find_all(exp.Table):
            table_name = str(table.name or "").strip()
            if not table_name:
                continue
            if table_name.lower() in cte_names:
                continue
            tables.add(table_name)

    return SqlParseResult(success=True, tables=tables)
