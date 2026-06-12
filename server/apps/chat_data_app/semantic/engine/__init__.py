"""
SQL 引擎层

提供 SQL 生成的核心能力。
"""

from .dialect import (
    SQLDialect,
    MySQLDialect,
    HiveDialect,
    PostgreSQLDialect,
    DialectRegistry,
    get_dialect,
)
from .join_builder import JoinBuilder
from .filter_builder import (
    FilterBuilder,
    FilterValidationError,
    build_metric_filter,
    SQL_SAFE_FUNCTIONS,
)

__all__ = [
    # 方言
    "SQLDialect",
    "MySQLDialect",
    "HiveDialect",
    "PostgreSQLDialect",
    "DialectRegistry",
    "get_dialect",
    # 构建器
    "JoinBuilder",
    "FilterBuilder",
    "FilterValidationError",
    "build_metric_filter",
    "SQL_SAFE_FUNCTIONS",
]
