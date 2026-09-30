"""Unified query result contract helpers."""

from __future__ import annotations

from typing import Any

QUERY_SUCCEEDED_STATUS = "succeeded"
QUERY_FAILED_STATUS = "failed"
QUERY_RUNNING_STATUS = "running"
QUERY_CANCELLED_STATUS = "cancelled"


def query_result_status(result: dict[str, Any]) -> str:
    """Return the normalized status for a query result payload."""
    raw_status = str((result or {}).get("status") or "").strip().lower()
    if raw_status in {
        "pending",
        QUERY_RUNNING_STATUS,
        QUERY_SUCCEEDED_STATUS,
        QUERY_FAILED_STATUS,
        QUERY_CANCELLED_STATUS,
    }:
        return raw_status
    # 读侧兼容（勿删）：生产端已全部只写 status，但持久化数据仍可能只有
    # success 旧字段——如 status 契约上线前写入的 SQL 结果缓存（cache.py
    # 读取时经此处映射），以及滚动部署窗口内旧版本节点返回的成员结果。
    if (result or {}).get("success"):
        return QUERY_SUCCEEDED_STATUS
    if raw_status in {"canceled", "cancel"}:
        return QUERY_CANCELLED_STATUS
    return QUERY_FAILED_STATUS


def query_result_succeeded(result: dict[str, Any]) -> bool:
    """Return whether a member or aggregate query result succeeded."""
    return query_result_status(result) == QUERY_SUCCEEDED_STATUS


def _normalize_member_results(member_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_results: list[dict[str, Any]] = []
    for item in member_results or []:
        member_result = dict(item or {})
        member_result["status"] = query_result_status(member_result)
        member_result.pop("success", None)
        normalized_results.append(member_result)
    return normalized_results


def _resolve_query_result_status(
    normalized_results: list[dict[str, Any]],
    succeeded_results: list[dict[str, Any]],
    fallback_error: str | None,
) -> tuple[str, str | None]:
    """根据成员结果推导聚合状态与错误信息。"""
    has_running = any(query_result_status(item) in {"pending", QUERY_RUNNING_STATUS} for item in normalized_results)
    has_results = bool(normalized_results)
    all_cancelled = has_results and all(
        query_result_status(item) == QUERY_CANCELLED_STATUS for item in normalized_results
    )

    if has_running:
        return QUERY_RUNNING_STATUS, "部分执行对象仍在查询中"
    if succeeded_results:
        return QUERY_SUCCEEDED_STATUS, None
    if all_cancelled:
        return QUERY_CANCELLED_STATUS, _join_member_errors(normalized_results) or fallback_error or "已停止"
    return QUERY_FAILED_STATUS, _join_member_errors(normalized_results) or fallback_error or "查询失败"


def _aggregate_execution_time_ms(
    normalized_results: list[dict[str, Any]],
    fallback_execution_time_ms: int | None,
) -> int | None:
    execution_times = [
        int(item.get("execution_time_ms") or 0)
        for item in normalized_results
        if item.get("execution_time_ms") is not None
    ]
    return max(execution_times) if execution_times else fallback_execution_time_ms


def build_query_result_payload(
    *,
    sql: str,
    member_results: list[dict[str, Any]],
    fallback_error: str | None = None,
    fallback_execution_time_ms: int | None = None,
) -> dict[str, Any]:
    """Build the public query result payload; row data only lives in results[].

    聚合约定（消费方必读），状态按序判定：
    - 任一成员仍在 ``pending``/``running``：顶层 ``status=running``，
      ``error`` 为固定提示“部分执行对象仍在查询中”（不聚合成员错误）。
    - 无运行中成员且有任一成员成功：顶层 ``status=succeeded`` 且 ``error``
      为 ``None``；部分成员失败的信息只在对应 ``results[]`` 成员的
      ``status``/``error`` 上。消费方判断整体成败必须遍历 ``results[]``，
      不能只看顶层字段。
    - 全部成员取消：顶层 ``status=cancelled``；其余情况为 ``failed``，
      这两种情形顶层 ``error`` 聚合各成员错误。
    - 成员成功与否由 ``status`` 单一表达（``succeeded``/``failed``/
      ``cancelled``/``running``），不使用 ``success`` 布尔字段。
    """
    normalized_results = _normalize_member_results(member_results)
    succeeded_results = [item for item in normalized_results if query_result_succeeded(item)]
    status, error_message = _resolve_query_result_status(normalized_results, succeeded_results, fallback_error)
    return {
        "status": status,
        "sql": sql,
        "total_count": sum(int(item.get("total_count") or 0) for item in succeeded_results),
        "execution_time_ms": _aggregate_execution_time_ms(normalized_results, fallback_execution_time_ms),
        "error": error_message,
        "results": normalized_results,
    }


def _join_member_errors(results: list[dict[str, Any]]) -> str:
    """Join member errors into a single user-facing message."""
    return "；".join(text for text in (str(item.get("error") or "").strip() for item in results) if text)
