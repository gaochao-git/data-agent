"""Shared semantic query helpers used by chat and analysis agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import query_runtime
from .query_result import build_query_result_payload, query_result_status
from .semantic_ai_query_adapter import SemanticAIQueryAdapter
from .semantic_bi_query_adapter import SemanticBIQueryAdapter


@dataclass(frozen=True)
class SemanticCompileOptions:
    """语义 DSL 编译选项；应用层负责注入，不由编译器读取环境变量。"""

    user_query: Optional[str] = None
    max_rows: Optional[int] = None
    offset: Optional[int] = None


def compile_semantic_ai_dsl(
    semantic_config: Dict[str, Any],
    dsl: Dict[str, Any],
    *,
    options: Optional[SemanticCompileOptions] = None,
    generator_cls: Optional[Callable[[Dict[str, Any]], Any]] = None,
    generator: Any = None,
) -> Dict[str, Any]:
    """统一语义 DSL 编译门面，所有应用入口应通过本函数编译。"""
    if not isinstance(semantic_config, dict):
        return {"success": False, "sql": "", "error": "语义模型必须是对象"}
    if not isinstance(dsl, dict):
        return {"success": False, "sql": "", "error": "语义 DSL 必须是对象"}

    compile_options = options or SemanticCompileOptions()
    compiler_factory = generator_cls or SemanticAIQueryAdapter
    compiler = generator or compiler_factory(semantic_config)
    compile_result = compiler.generate_sql(
        metrics=dsl.get("metrics") or [],
        dimensions=dsl.get("dimensions") or [],
        filters=dsl.get("filters") or [],
        having=dsl.get("having") or [],
        order_by=dsl.get("order_by") or [],
        limit=dsl.get("limit", compile_options.max_rows),
        offset=dsl.get("offset", compile_options.offset),
        user_query=compile_options.user_query,
    )
    return {
        "success": bool(compile_result.get("success")),
        "sql": str(compile_result.get("sql") or "").strip(),
        "error": compile_result.get("error"),
        "warnings": compile_result.get("warnings") or [],
    }


def compile_semantic_bi_dsl(
    semantic_config: Dict[str, Any],
    dsl: Dict[str, Any],
) -> Dict[str, Any]:
    """编译 BI 可视化语义 DSL，返回 SQL 和内部查询计划。"""
    if not isinstance(semantic_config, dict):
        return {"success": False, "sql": "", "error": "语义模型必须是对象"}
    if not isinstance(dsl, dict):
        return {"success": False, "sql": "", "error": "BI 语义 DSL 必须是对象"}
    try:
        result = SemanticBIQueryAdapter(semantic_config).compile(dsl)
    except (TypeError, ValueError) as exc:
        return {"success": False, "sql": "", "error": str(exc)}
    return {
        "success": True,
        "sql": str(result.get("sql") or "").strip(),
        "query_plan": result.get("builder_config") or {},
        # 保留已有可视化响应字段，新的公共门面内部使用 query_plan。
        "builder_config": result.get("builder_config") or {},
        "error": None,
    }


def normalize_member_datasource_ids(member_datasource_ids: Any) -> List[str]:
    """Normalize member datasource ids while preserving the first-seen order."""
    normalized_ids: List[str] = []
    for item in member_datasource_ids or []:
        member_id = str(item or "").strip()
        if member_id and member_id not in normalized_ids:
            normalized_ids.append(member_id)
    return normalized_ids


async def execute_semantic_query_sql(
    *,
    sql: str,
    datasource_id: Optional[str],
    member_datasource_ids: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    semantic_config_id: Optional[str] = None,
    query_text: Optional[str] = None,
    conversation_id: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute semantic query SQL through the unified query runtime."""
    return await query_runtime.execute_agent_sql(
        sql=sql,
        datasource_id=datasource_id,
        member_datasource_ids=member_datasource_ids,
        user_id=user_id,
        semantic_config_id=semantic_config_id,
        query_text=query_text,
        conversation_id=conversation_id,
        operator_name=operator_name,
    )


def build_member_query_result(
    base_result: Dict[str, Any],
    sql: str,
    member_datasource_id: Optional[str] = None,
    member_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a normalized member query result."""
    return {
        "status": query_result_status(base_result),
        "sql": sql,
        "columns": base_result.get("columns", []),
        "data": base_result.get("data", []),
        "total_count": base_result.get("total_count", 0),
        "execution_time_ms": base_result.get("execution_time_ms"),
        "error": base_result.get("error"),
        "member_datasource_id": member_datasource_id,
        "member_name": member_name,
    }


def build_multi_member_query_result(sql: str, member_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a normalized multi-member query result payload."""
    return build_query_result_payload(sql=sql, member_results=member_results)
