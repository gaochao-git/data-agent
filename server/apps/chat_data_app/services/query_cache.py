"""SQL query result cache helpers."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, Optional

from sqlalchemy import select

from utils.database import get_session
from utils.logger import get_logger
from utils.models import ChatDataSqlResultCache

from .query_result import query_result_status, query_result_succeeded
from .runtime_config import QUERY_CACHE_ENABLED, QUERY_CACHE_TTL_SECONDS

logger = get_logger(__name__)

HTTP_REMOTE_REF_CACHE_TYPE = "http_remote_ref"


def is_http_execution_target(execution_target: Dict[str, Any]) -> bool:
    """Return whether the execution target uses the HTTP query executor."""
    return str((execution_target or {}).get("query_executor_type") or "").strip().lower() == "http"


def is_http_remote_ref_cache(payload: Optional[Dict[str, Any]]) -> bool:
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
    execution_target: Dict[str, Any],
    effective_sql: str,
    datasource_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build identity fields for SQL result cache lookup."""
    normalized_datasource_id = str(datasource_id or (execution_target or {}).get("datasource_id") or (execution_target or {}).get("id") or "").strip()
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


def is_sql_result_cache_enabled(skip_query_cache: bool) -> bool:
    """Return whether SQL result cache is enabled for this execution."""
    return QUERY_CACHE_ENABLED and not skip_query_cache


async def get_sql_result_cache(cache_identity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Read a non-expired SQL result cache payload."""
    started_at = time.perf_counter()
    cache_key = str(cache_identity.get("cache_key") or "").strip()
    if not cache_key:
        return None
    now = datetime.now()
    async with get_session() as db:
        result = await db.execute(
            select(ChatDataSqlResultCache)
            .filter_by(cache_key=cache_key, delete_flag=0)
            .where(ChatDataSqlResultCache.expire_time > now)
        )
        cache_item = result.scalar_one_or_none()
        if not cache_item:
            return None
        try:
            payload = json.loads(cache_item.result_payload or "{}")
        except Exception as err:
            logger.warning("SQL 结果缓存解析失败: cache_key=%s error=%s", cache_key, err)
            cache_item.delete_flag = 1
            cache_item.update_time = now
            await db.commit()
            return None
        cache_item.hit_count = int(cache_item.hit_count or 0) + 1
        cache_item.last_hit_time = now
        cache_item.update_time = now
        await db.commit()
    cache_age_seconds = max(0, int((now - cache_item.create_time).total_seconds())) if cache_item.create_time else None
    cache_expires_in_seconds = max(0, int((cache_item.expire_time - now).total_seconds())) if cache_item.expire_time else None
    payload["status"] = query_result_status(payload)
    payload.pop("success", None)
    payload["cache_hit"] = True
    payload["execution_time_ms"] = int((time.perf_counter() - started_at) * 1000)
    payload["cache_age_seconds"] = cache_age_seconds
    payload["cache_expires_in_seconds"] = cache_expires_in_seconds
    return payload


async def save_sql_result_cache(
    cache_identity: Dict[str, Any],
    result: Dict[str, Any],
    operator_id: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> None:
    """Save a successful SQL result cache payload."""
    if not query_result_succeeded(result):
        return
    if str((cache_identity or {}).get("executor_type") or "").strip().lower() == "http":
        await save_http_remote_ref_sql_result_cache(cache_identity, result, operator_id=operator_id, operator_name=operator_name)
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
    cache_identity: Dict[str, Any],
    result: Dict[str, Any],
    operator_id: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> None:
    """Save an HTTP remote task reference instead of local rows."""
    if not query_result_succeeded(result):
        return
    remote_task_id = str(result.get("remote_task_id") or "").strip()
    if not remote_task_id:
        return
    source_type = str(result.get("source_type") or cache_identity.get("source_type") or "").strip().lower() or "hive"
    payload = {
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


async def fetch_http_remote_ref_cache_result(
    cached_result: Dict[str, Any],
    *,
    fetch_remote_result: Callable[..., Awaitable[Dict[str, Any]]],
    user_id: Optional[str] = None,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    """Read a cached HTTP remote task result and preserve cache hit metadata."""
    remote_task_id = str((cached_result or {}).get("remote_task_id") or "").strip()
    source_type = str((cached_result or {}).get("source_type") or "").strip().lower() or "hive"
    if not remote_task_id:
        return {
            "_cache_ref_unavailable": True,
            "status": "failed",
            "error": "HTTP 缓存缺少远端任务 ID",
            "columns": [],
            "data": [],
            "total_count": 0,
        }
    try:
        remote_result = await fetch_remote_result(
            remote_task_id,
            source_type=source_type,
            user_id=user_id,
            username=username,
        )
    except Exception as err:
        logger.warning("读取 HTTP 远端缓存结果失败: remote_task_id=%s error=%s", remote_task_id, err, exc_info=True)
        return {
            "_cache_ref_unavailable": True,
            "status": "failed",
            "error": str(err) or "HTTP 远端缓存结果读取失败",
            "columns": [],
            "data": [],
            "total_count": 0,
        }
    if not query_result_succeeded(remote_result):
        return {
            "_cache_ref_unavailable": True,
            "error": remote_result.get("error") or "HTTP 远端缓存结果不可用",
            "status": query_result_status(remote_result),
            "columns": remote_result.get("columns") or [],
            "data": remote_result.get("data") or [],
            "total_count": int(remote_result.get("total_count") or 0),
        }
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


async def _upsert_sql_result_cache_payload(
    cache_identity: Dict[str, Any],
    payload: Dict[str, Any],
    result_row_count: int,
    operator_id: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> None:
    """Upsert a SQL query cache payload."""
    cache_key = str(cache_identity.get("cache_key") or "").strip()
    if not cache_key:
        return
    now = datetime.now()
    operator_user_id = str(operator_id or "").strip()
    operator_username = str(operator_name or "").strip()
    async with get_session() as db:
        db_result = await db.execute(select(ChatDataSqlResultCache).filter_by(cache_key=cache_key))
        cache_item = db_result.scalar_one_or_none()
        if cache_item:
            cache_item.datasource_id = str(cache_identity.get("datasource_id") or "")
            cache_item.member_datasource_id = str(cache_identity.get("member_datasource_id") or "")
            cache_item.executor_type = str(cache_identity.get("executor_type") or "")
            cache_item.source_type = str(cache_identity.get("source_type") or "")
            cache_item.sql_hash = str(cache_identity.get("sql_hash") or "")
            cache_item.sql_text = str(cache_identity.get("sql_text") or "")
            cache_item.result_payload = json.dumps(payload, ensure_ascii=False)
            cache_item.result_row_count = int(result_row_count or 0)
            cache_item.expire_time = now + timedelta(seconds=QUERY_CACHE_TTL_SECONDS)
            cache_item.delete_flag = 0
            cache_item.update_time = now
            cache_item.update_by = operator_user_id
            cache_item.update_by_name = operator_username
        else:
            db.add(ChatDataSqlResultCache(
                cache_key=cache_key,
                datasource_id=str(cache_identity.get("datasource_id") or ""),
                member_datasource_id=str(cache_identity.get("member_datasource_id") or ""),
                executor_type=str(cache_identity.get("executor_type") or ""),
                source_type=str(cache_identity.get("source_type") or ""),
                sql_hash=str(cache_identity.get("sql_hash") or ""),
                sql_text=str(cache_identity.get("sql_text") or ""),
                result_payload=json.dumps(payload, ensure_ascii=False),
                result_row_count=int(result_row_count or 0),
                expire_time=now + timedelta(seconds=QUERY_CACHE_TTL_SECONDS),
                hit_count=0,
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=operator_user_id,
                create_by_name=operator_username,
                update_by=operator_user_id,
                update_by_name=operator_username,
            ))
        await db.commit()
