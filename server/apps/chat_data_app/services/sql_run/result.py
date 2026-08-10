"""Unified query result contract helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

QUERY_SUCCEEDED_STATUS = "succeeded"
QUERY_FAILED_STATUS = "failed"
QUERY_RUNNING_STATUS = "running"
QUERY_CANCELLED_STATUS = "cancelled"


def query_result_status(result: Dict[str, Any]) -> str:
    """Return the normalized status for a query result payload."""
    raw_status = str((result or {}).get("status") or "").strip().lower()
    if raw_status in {"pending", QUERY_RUNNING_STATUS, QUERY_SUCCEEDED_STATUS, QUERY_FAILED_STATUS, QUERY_CANCELLED_STATUS}:
        return raw_status
    if (result or {}).get("success"):
        return QUERY_SUCCEEDED_STATUS
    if raw_status in {"canceled", "cancel"}:
        return QUERY_CANCELLED_STATUS
    return QUERY_FAILED_STATUS


def query_result_succeeded(result: Dict[str, Any]) -> bool:
    """Return whether a member or aggregate query result succeeded."""
    return query_result_status(result) == QUERY_SUCCEEDED_STATUS


def build_query_result_payload(
    *,
    sql: str,
    member_results: List[Dict[str, Any]],
    fallback_error: Optional[str] = None,
    fallback_execution_time_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the public query result payload; row data only lives in results[]."""
    normalized_results: List[Dict[str, Any]] = []
    for item in member_results or []:
        member_result = dict(item or {})
        member_result["status"] = query_result_status(member_result)
        member_result.pop("success", None)
        normalized_results.append(member_result)

    succeeded_results = [item for item in normalized_results if query_result_succeeded(item)]
    has_running = any(query_result_status(item) in {"pending", QUERY_RUNNING_STATUS} for item in normalized_results)
    has_results = bool(normalized_results)
    all_cancelled = has_results and all(query_result_status(item) == QUERY_CANCELLED_STATUS for item in normalized_results)

    if has_running:
        status = QUERY_RUNNING_STATUS
        error_message = "部分执行对象仍在查询中"
    elif succeeded_results:
        status = QUERY_SUCCEEDED_STATUS
        error_message = None
    elif all_cancelled:
        status = QUERY_CANCELLED_STATUS
        error_message = _join_member_errors(normalized_results) or fallback_error or "已停止"
    else:
        status = QUERY_FAILED_STATUS
        error_message = _join_member_errors(normalized_results) or fallback_error or "查询失败"

    execution_times = [
        int(item.get("execution_time_ms") or 0)
        for item in normalized_results
        if item.get("execution_time_ms") is not None
    ]
    execution_time_ms = max(execution_times) if execution_times else fallback_execution_time_ms
    return {
        "status": status,
        "sql": sql,
        "total_count": sum(int(item.get("total_count") or 0) for item in succeeded_results),
        "execution_time_ms": execution_time_ms,
        "error": error_message,
        "results": normalized_results,
    }


def _join_member_errors(results: List[Dict[str, Any]]) -> str:
    """Join member errors into a single user-facing message."""
    return "；".join(
        text
        for text in (str(item.get("error") or "").strip() for item in results)
        if text
    )
