"""统一查询计划到 SQL 的编译门面。"""

from __future__ import annotations

from typing import Any, Dict

from . import query_plan_compiler
from .dialect import get_dialect


class SemanticSQLCompiler:
    """将标准化查询计划编译为指定方言的 SQL。"""

    def __init__(self, *, dialect: str = "mysql"):
        self.dialect = str(dialect or "mysql").strip().lower() or "mysql"
        self.dialect_adapter = get_dialect(self.dialect)

    def compile(self, query_plan: Dict[str, Any]) -> str:
        """编译一次查询计划，不跨请求保存可变状态。"""
        return query_plan_compiler.compile_query_plan_sql(query_plan, dialect=self.dialect)


def compile_semantic_sql(query_plan: Dict[str, Any], *, dialect: str = "mysql") -> str:
    """独立编译器入口。"""
    return SemanticSQLCompiler(dialect=dialect).compile(query_plan)
