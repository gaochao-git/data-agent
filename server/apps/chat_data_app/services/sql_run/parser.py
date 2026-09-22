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


def _strip_comment_next_index(text: str, i: int, char: str, next_char: str, result: list[str]) -> int | None:
    """判定并吞掉注释，返回新的扫描索引；无注释返回 None。"""
    if char == "-" and next_char == "-":
        return _consume_validation_line_comment(text, i, search_offset=2, result=result)
    if char == "#":
        return _consume_validation_line_comment(text, i, search_offset=1, result=result)
    if char == "/" and next_char == "*":
        return _consume_validation_block_comment(text, i, result)
    return None


def _strip_comments_by_scan(sql: str) -> str:
    """删除真实 SQL 注释，保留字符串和反引号内的注释符。"""
    text = str(sql or "")
    result: list[str] = []
    in_quote = ""
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        next_char = text[i + 1] if i + 1 < n else ""
        if in_quote:
            i, in_quote = _consume_quoted_char(text, i, in_quote, result)
            continue
        if char in {"'", '"', "`"}:
            in_quote = char
            result.append(char)
            i += 1
            continue
        consumed = _strip_comment_next_index(text, i, char, next_char, result)
        if consumed is not None:
            i = consumed
            continue
        result.append(char)
        i += 1
    return "".join(result)


def _char_at(text: str, index: int) -> str:
    """越界安全的单字符取值。"""
    return text[index] if index < len(text) else ""


def _is_validation_block_comment_start(text: str, i: int) -> bool:
    """判断 i 处是否为块注释起始 ``/*``。"""
    return text[i] == "/" and _char_at(text, i + 1) == "*"


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
        if in_quote:
            i, in_quote = _consume_quoted_char(text, i, in_quote, result)
            continue
        if char in {"'", '"', "`"}:
            in_quote = char
            result.append(char)
            i += 1
            continue
        if char == "-" and _is_validation_dash_comment(text, i, dialect):
            i = _consume_validation_line_comment(text, i, search_offset=2, result=result)
            continue
        if char == "#" and dialect == "mysql":
            i = _consume_validation_line_comment(text, i, search_offset=1, result=result)
            continue
        if _is_validation_block_comment_start(text, i):
            i = _consume_validation_block_comment(text, i, result)
            continue
        result.append(char)
        i += 1
    return "".join(result)


def _consume_quoted_char(text: str, i: int, in_quote: str, result: list[str]) -> tuple[int, str]:
    """处理引号内字符：转义、成对单引号与引号闭合；返回 (新索引, 引号状态)。"""
    char = text[i]
    result.append(char)
    if char == "\\":
        if i + 1 < len(text):
            result.append(text[i + 1])
            return i + 2, in_quote
        return i + 1, in_quote
    if char == in_quote:
        if in_quote == "'" and text[i + 1:i + 2] == "'":
            result.append("'")
            return i + 2, in_quote
        return i + 1, ""
    return i + 1, in_quote


def _consume_validation_line_comment(text: str, i: int, *, search_offset: int, result: list[str]) -> int:
    """吞掉行注释：保留换行符；注释到结尾时停止扫描（等价 break）。"""
    newline_index = text.find("\n", i + search_offset)
    if newline_index == -1:
        return len(text)
    result.append("\n")
    return newline_index + 1


def _consume_validation_block_comment(text: str, i: int, result: list[str]) -> int:
    """吞掉块注释：以空格占位；未闭合时吞到文本末尾。"""
    comment_end = text.find("*/", i + 2)
    result.append(" ")
    return len(text) if comment_end == -1 else comment_end + 2


def _is_query_sql(sql: str) -> bool:
    """判断是否为查询类 SQL。"""
    normalized = str(sql or "").strip().lstrip("(").strip().lower()
    return normalized.startswith("select ") or normalized.startswith("with ")


def _consume_split_quoted_char(text: str, i: int, in_quote: str, current: list[str]) -> tuple[int, str]:
    """处理 _split_sql_statements 引号内字符，返回 (新索引, 引号状态)。"""
    char = text[i]
    next_char = text[i + 1] if i + 1 < len(text) else ""
    current.append(char)
    if char == "\\":
        if i + 1 < len(text):
            current.append(next_char)
            return i + 2, in_quote
        return i + 1, in_quote
    if char == in_quote:
        if in_quote == "'" and next_char == "'":
            current.append("'")
            return i + 2, in_quote
        return i + 1, ""
    return i + 1, in_quote


def _split_sql_statements(sql: str) -> list[str]:
    """按非字符串位置的分号拆分 SQL。"""
    statements: list[str] = []
    current: list[str] = []
    in_quote = ""
    i = 0
    n = len(sql)
    while i < n:
        char = sql[i]
        if in_quote:
            i, in_quote = _consume_split_quoted_char(sql, i, in_quote, current)
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


def validate_raw_single_sql_statement(sql: str, source_type: str | None = None) -> SqlValidateResult:
    """按 SQL 原文校验仅包含一条语句，不移除注释或改写 SQL。"""
    text = str(sql or "").strip()
    if not text:
        return SqlValidateResult(success=False, error="SQL 为空")

    candidate = text.rstrip(";").strip()
    if not candidate:
        return SqlValidateResult(success=False, error="SQL 为空")

    statements = _split_sql_statements(candidate)
    if not statements:
        return SqlValidateResult(success=False, error="SQL 为空")
    if len(statements) != 1:
        return SqlValidateResult(success=False, error="一次只能执行一条 SQL，请拆分后分别执行")
    single_sql = statements[0]

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


def _collect_physical_tables_from_expression(expression, tables: Set[str]) -> None:
    """从单条 SQL 表达式中收集物理表名，排除 CTE 名称，写入 tables。"""
    from sqlglot import exp

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
        _collect_physical_tables_from_expression(expression, tables)

    return SqlParseResult(success=True, tables=tables)
