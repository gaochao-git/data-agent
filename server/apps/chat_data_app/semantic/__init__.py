"""ChatData semantic package.

当前语义层只保留一条主链路：
DSL 输入 -> semantic 编译器 -> SQL 方言/过滤/JOIN -> SQL。
同比/环比不属于 DSL2SQL 能力；请拆成基础 DSL 查询，复杂同比/环比使用智能分析。
"""

__version__ = "1.0.0"

from .builder_sql_compiler import compile_builder_sql
from .dialect import (
    DialectRegistry,
    HiveDialect,
    MySQLDialect,
    PostgreSQLDialect,
    SQLDialect,
    get_dialect,
)
from .formatter import (
    format_error_message,
    format_params_for_display,
    format_sql_for_display,
)
from .input_validator import InputValidator, SemanticValidator
from .json_parser import (
    JSONParseError,
    parse_llm_json,
    safe_get,
    safe_get_dict,
    safe_get_list,
)
from .semantic_sql_generator import SemanticSQLGenerator


__all__ = [
    "__version__",
    "SemanticSQLGenerator",
    "compile_builder_sql",
    "InputValidator",
    "SemanticValidator",
    "SQLDialect",
    "MySQLDialect",
    "HiveDialect",
    "PostgreSQLDialect",
    "DialectRegistry",
    "get_dialect",
    "parse_llm_json",
    "JSONParseError",
    "safe_get",
    "safe_get_list",
    "safe_get_dict",
    "format_params_for_display",
    "format_sql_for_display",
    "format_error_message",
]
