"""Shared semantic query helpers used by chat and analysis agents."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from . import query_runtime
from .query_result import build_query_result_payload, query_result_status
from .semantic_sql_generator import SemanticSQLGenerator


def normalize_member_datasource_ids(member_datasource_ids: Any) -> List[str]:
    """Normalize member datasource ids while preserving the first-seen order."""
    normalized_ids: List[str] = []
    for item in member_datasource_ids or []:
        member_id = str(item or "").strip()
        if member_id and member_id not in normalized_ids:
            normalized_ids.append(member_id)
    return normalized_ids


def compile_semantic_query_dsl(
    semantic_config: Dict[str, Any],
    dsl: Dict[str, Any],
    *,
    user_query: Optional[str] = None,
    generator_cls: Callable[[Dict[str, Any]], Any] = SemanticSQLGenerator,
) -> Dict[str, Any]:
    """Compile a semantic DSL payload into SQL using the shared generator contract."""
    compile_result = generator_cls(semantic_config).generate_sql(
        metrics=dsl.get("metrics") or [],
        dimensions=dsl.get("dimensions") or [],
        filters=dsl.get("filters") or [],
        having=dsl.get("having") or [],
        order_by=dsl.get("order_by") or [],
        limit=dsl.get("limit"),
        offset=dsl.get("offset"),
        user_query=user_query,
    )
    return {
        "success": bool(compile_result.get("success")),
        "sql": str(compile_result.get("sql") or "").strip(),
        "error": compile_result.get("error"),
    }


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
