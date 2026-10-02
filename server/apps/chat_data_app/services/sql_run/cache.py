"""SQL query result cache helpers."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from utils.database import get_session
from utils.logger import get_logger

from ...daos.sql_result_cache_dao import SqlResultCacheDao
from ..setting_service import get_runtime_setting
from .result import query_result_status, query_result_succeeded

logger = get_logger(__name__)

HTTP_REMOTE_REF_CACHE_TYPE = "http_remote_ref"

sql_result_cache_dao = SqlResultCacheDao(session_context=lambda: get_session())


def is_http_execution_target(execution_target: dict[str, Any]) -> bool:
    """Return whether the execution target uses the HTTP query executor."""
    return str((execution_target or {}).get("query_executor_type") or "").strip().lower() == "http"


def is_http_remote_ref_cache(payload: dict[str, Any] | None) -> bool:
    """Return whether a cache payload stores an HTTP remote task reference."""
    return (
        isinstance(payload, dict)
        and str(payload.get("cache_type") or "").strip().lower() == HTTP_REMOTE_REF_CACHE_TYPE
        and bool(str(payload.get("remote_task_id") or "").strip())
    )


def normalize_sql_cache_text(sql: str) -> str:
    """Normalize SQL text before cache identity hashing."""
    return str(sql or "").strip()


def build_sql_result_cache_identity(
    execution_target: dict[str, Any],
    effective_sql: str,
    datasource_id: str | None = None,
) -> dict[str, Any]:
    """Build identity fields for SQL result cache lookup."""
    normalized_datasource_id = str(
        datasource_id or (execution_target or {}).get("datasource_id") or (execution_target or {}).get("id") or ""
    ).strip()
    member_datasource_id = str((execution_target or {}).get("member_datasource_id") or "").strip()
    executor_type = str((execution_target or {}).get("query_executor_type") or "direct").strip().lower() or "direct"
    source_type = str((execution_target or {}).get("source_type") or "").strip().lower()
    normalized_sql = normalize_sql_cache_text(effective_sql)
    sql_hash = hashlib.sha256(normalized_sql.encode("utf-8")).hexdigest()
    raw_cache_key = json.dumps(
        {
            "datasource_id": normalized_datasource_id,
            "member_datasource_id": member_datasource_id,
            "sql_hash": sql_hash,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "cache_key": hashlib.sha256(raw_cache_key.encode("utf-8")).hexdigest(),
        "datasource_id": normalized_datasource_id,
        "member_datasource_id": member_datasource_id,
        "executor_type": executor_type,
        "source_type": source_type,
        "sql_hash": sql_hash,
        "sql_text": effective_sql,
    }


async def is_sql_result_cache_enabled(skip_query_cache: bool) -> bool:
    """Return whether SQL result cache is enabled for this execution（运行期经配置服务读取）."""
    cache_enabled = await get_runtime_setting("CHAT_DATA_QUERY_CACHE_ENABLED")
    return bool(cache_enabled) and not skip_query_cache


async def get_sql_result_cache(cache_identity: dict[str, Any]) -> dict[str, Any] | None:
    """Read a non-expired SQL result cache payload."""
    started_at = time.perf_counter()
    cache_key = str(cache_identity.get("cache_key") or "").strip()
    if not cache_key:
        return None
    now = datetime.now()
    read_result = await sql_result_cache_dao.read_and_touch_cache(cache_key, now=now)
    if read_result is None:
        return None
    cache_item, payload = read_result
    if payload is None:
        return None
    cache_age_seconds = max(0, int((now - cache_item.create_time).total_seconds())) if cache_item.create_time else None
    cache_expires_in_seconds = (
        max(0, int((cache_item.expire_time - now).total_seconds())) if cache_item.expire_time else None
    )
    payload["status"] = query_result_status(payload)
    payload.pop("success", None)
    payload["cache_hit"] = True
    payload["execution_time_ms"] = int((time.perf_counter() - started_at) * 1000)
    payload["cache_age_seconds"] = cache_age_seconds
    payload["cache_expires_in_seconds"] = cache_expires_in_seconds
    return payload


async def save_sql_result_cache(
    cache_identity: dict[str, Any],
    result: dict[str, Any],
    operator_id: str | None = None,
    operator_name: str | None = None,
) -> None:
    """Save a successful SQL result cache payload."""
    if not query_result_succeeded(result):
        return
    if str((cache_identity or {}).get("executor_type") or "").strip().lower() == "http":
        await save_http_remote_ref_sql_result_cache(
            cache_identity, result, operator_id=operator_id, operator_name=operator_name
        )
        return
    payload = {
        "status": "succeeded",
        "columns": result.get("columns") or [],
        "data": result.get("data") or [],
        "total_count": int(result.get("total_count") or 0),
        "execution_time_ms": result.get("execution_time_ms"),
        "error": None,
    }
    await _upsert_sql_result_cache_payload(
        cache_identity,
        payload,
        int(result.get("total_count") or 0),
        operator_id=operator_id,
        operator_name=operator_name,
    )


async def save_http_remote_ref_sql_result_cache(
    cache_identity: dict[str, Any],
    result: dict[str, Any],
    operator_id: str | None = None,
    operator_name: str | None = None,
) -> None:
    """Save an HTTP remote task reference instead of local rows."""
    if not query_result_succeeded(result):
        return
    remote_task_id = str(result.get("remote_task_id") or "").strip()
    if not remote_task_id:
        return
    source_type = str(result.get("source_type") or cache_identity.get("source_type") or "").strip().lower() or "hive"
    payload: dict[str, Any] = {
        "status": "succeeded",
        "cache_type": HTTP_REMOTE_REF_CACHE_TYPE,
        "remote_task_id": remote_task_id,
        "source_type": source_type,
        "columns": [],
        "data": [],
        "total_count": 0,
        "execution_time_ms": result.get("execution_time_ms"),
        "error": None,
    }
    await _upsert_sql_result_cache_payload(
        cache_identity,
        payload,
        0,
        operator_id=operator_id,
        operator_name=operator_name,
    )


def _http_cache_ref_identifiers(cached_result: dict[str, Any] | None) -> tuple[str, str]:
    """从缓存 payload 提取远端任务 ID 与来源类型（缺省 hive）。"""
    cached_result = cached_result or {}
    remote_task_id = str(cached_result.get("remote_task_id") or "").strip()
    source_type = str(cached_result.get("source_type") or "").strip().lower() or "hive"
    return remote_task_id, source_type


def _http_cache_ref_unavailable(
    error: str,
    *,
    status: str = "failed",
    columns: Any = None,
    data: Any = None,
    total_count: int = 0,
) -> dict[str, Any]:
    """缓存远端引用不可用的统一返回形态。"""
    return {
        "_cache_ref_unavailable": True,
        "status": status,
        "error": error,
        "columns": columns or [],
        "data": data or [],
        "total_count": total_count,
    }


async def fetch_http_remote_ref_cache_result(
    cached_result: dict[str, Any],
    *,
    fetch_remote_result: Callable[..., Awaitable[dict[str, Any]]],
    user_id: str | None = None,
    username: str | None = None,
) -> dict[str, Any]:
    """Read a cached HTTP remote task result and preserve cache hit metadata."""
    remote_task_id, source_type = _http_cache_ref_identifiers(cached_result)
    if not remote_task_id:
        return _http_cache_ref_unavailable("HTTP 缓存缺少远端任务 ID")
    try:
        remote_result = await fetch_remote_result(
            remote_task_id,
            source_type=source_type,
            user_id=user_id,
            username=username,
        )
    except Exception as err:
        logger.warning("读取 HTTP 远端缓存结果失败: remote_task_id=%s error=%s", remote_task_id, err, exc_info=True)
        return _http_cache_ref_unavailable(str(err) or "HTTP 远端缓存结果读取失败")
    if not query_result_succeeded(remote_result):
        return _http_cache_ref_unavailable(
            remote_result.get("error") or "HTTP 远端缓存结果不可用",
            status=query_result_status(remote_result),
            columns=remote_result.get("columns") or [],
            data=remote_result.get("data") or [],
            total_count=int(remote_result.get("total_count") or 0),
        )
    return {
        "status": "succeeded",
        "columns": remote_result.get("columns") or [],
        "data": remote_result.get("data") or [],
        "total_count": int(remote_result.get("total_count") or 0),
        "execution_time_ms": remote_result.get("execution_time_ms"),
        "started_time": remote_result.get("started_time"),
        "finished_time": remote_result.get("finished_time"),
        "remote_task_id": remote_task_id,
        "error": None,
        "cache_hit": True,
        "cache_age_seconds": cached_result.get("cache_age_seconds"),
        "cache_expires_in_seconds": cached_result.get("cache_expires_in_seconds"),
    }


def _cache_identity_fields(cache_identity: dict[str, Any]) -> dict[str, str]:
    """提取缓存身份五元组，供更新与新建分支共用。"""
    return {
        "datasource_id": str(cache_identity.get("datasource_id") or ""),
        "member_datasource_id": str(cache_identity.get("member_datasource_id") or ""),
        "executor_type": str(cache_identity.get("executor_type") or ""),
        "source_type": str(cache_identity.get("source_type") or ""),
        "sql_hash": str(cache_identity.get("sql_hash") or ""),
    }


async def _upsert_sql_result_cache_payload(
    cache_identity: dict[str, Any],
    payload: dict[str, Any],
    result_row_count: int,
    operator_id: str | None = None,
    operator_name: str | None = None,
) -> None:
    """Upsert a SQL query cache payload."""
    cache_key = str(cache_identity.get("cache_key") or "").strip()
    if not cache_key:
        return
    now = datetime.now()
    await sql_result_cache_dao.upsert_cache(
        cache_key=cache_key,
        identity_fields=_cache_identity_fields(cache_identity),
        sql_text=str(cache_identity.get("sql_text") or ""),
        payload_json=json.dumps(payload, ensure_ascii=False),
        result_row_count=int(result_row_count or 0),
        now=now,
        expire_time=now + timedelta(seconds=await get_runtime_setting("CHAT_DATA_QUERY_CACHE_TTL_SECONDS")),
        operator_user_id=str(operator_id or "").strip(),
        operator_name=str(operator_name or "").strip(),
    )
