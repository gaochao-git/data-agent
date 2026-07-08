"""
ChatData 查询运行时服务。

该模块承接 SQL/Builder/Agent 查询进入执行阶段后的公共编排：
任务记录、执行明细、成员数据源执行、缓存、结果落库与任务状态更新。
"""

import asyncio
import hashlib
import inspect
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select

from utils.database import get_session
from utils.models import ChatDataQueryExecution, ChatDataQueryTask, ChatDataSqlResultCache
from utils.logger import get_logger

from ..schema import ExecuteSqlRequest, QueryTaskCreateRequest
from .datasource_manager import get_datasource_manager
from .runtime_config import DEFAULT_ROW_LIMIT, QUERY_CACHE_ENABLED, QUERY_CACHE_TTL_SECONDS
from .semantic_builder_compiler import compile_semantic_builder_sql
from .sql_concurrency_limiter import run_with_sql_user_datasource_limit
from .sql_executor import build_effective_sql, fetch_http_query_task_result, get_executor

logger = get_logger(__name__)

HTTP_REMOTE_REF_CACHE_TYPE = "http_remote_ref"


def is_http_execution_target(execution_target: Dict[str, Any]) -> bool:
    """判断执行目标是否走 HTTP 查询。"""
    return str((execution_target or {}).get("query_executor_type") or "").strip().lower() == "http"


def is_http_remote_ref_cache(payload: Optional[Dict[str, Any]]) -> bool:
    """判断缓存负载是否为 HTTP 远端任务引用。"""
    return (
        isinstance(payload, dict)
        and str(payload.get("cache_type") or "").strip().lower() == HTTP_REMOTE_REF_CACHE_TYPE
        and bool(str(payload.get("remote_task_id") or "").strip())
    )


def normalize_sql_cache_text(sql: str) -> str:
    """归一化 SQL 缓存键文本。"""
    return str(sql or "").strip()


def build_sql_result_cache_identity(execution_target: Dict[str, Any], effective_sql: str, datasource_id: Optional[str] = None) -> Dict[str, Any]:
    """构造 SQL 结果缓存身份字段。"""
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
    """判断本次 SQL 执行是否允许使用结果缓存。"""
    return QUERY_CACHE_ENABLED and not skip_query_cache


async def get_sql_result_cache(cache_identity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """读取未过期的 SQL 查询结果缓存。"""
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
    payload["cache_hit"] = True
    payload["execution_time_ms"] = int((time.perf_counter() - started_at) * 1000)
    payload["cache_age_seconds"] = cache_age_seconds
    payload["cache_expires_in_seconds"] = cache_expires_in_seconds
    return payload


async def _upsert_sql_result_cache_payload(
    cache_identity: Dict[str, Any],
    payload: Dict[str, Any],
    result_row_count: int,
    operator_id: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> None:
    """写入 SQL 查询缓存负载。"""
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


async def save_sql_result_cache(cache_identity: Dict[str, Any], result: Dict[str, Any], operator_id: Optional[str] = None, operator_name: Optional[str] = None) -> None:
    """保存成功的 SQL 查询结果缓存。"""
    if not result.get("success"):
        return
    if str((cache_identity or {}).get("executor_type") or "").strip().lower() == "http":
        await save_http_remote_ref_sql_result_cache(cache_identity, result, operator_id=operator_id, operator_name=operator_name)
        return
    payload = {
        "success": True,
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


async def save_http_remote_ref_sql_result_cache(cache_identity: Dict[str, Any], result: Dict[str, Any], operator_id: Optional[str] = None, operator_name: Optional[str] = None) -> None:
    """保存 HTTP 查询远端任务引用缓存，不落本地结果行。"""
    if not result.get("success"):
        return
    remote_task_id = str(result.get("remote_task_id") or "").strip()
    if not remote_task_id:
        return
    source_type = str(result.get("source_type") or cache_identity.get("source_type") or "").strip().lower() or "hive"
    payload = {
        "success": True,
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


async def fetch_http_remote_ref_cache_result(cached_result: Dict[str, Any], user_id: Optional[str] = None, username: Optional[str] = None) -> Dict[str, Any]:
    """按 HTTP 远端任务引用读取结果，并保留本次缓存命中元信息。"""
    remote_task_id = str((cached_result or {}).get("remote_task_id") or "").strip()
    source_type = str((cached_result or {}).get("source_type") or "").strip().lower() or "hive"
    if not remote_task_id:
        return {
            "_cache_ref_unavailable": True,
            "success": False,
            "error": "HTTP 缓存缺少远端任务 ID",
            "columns": [],
            "data": [],
            "total_count": 0,
        }
    try:
        remote_result = await fetch_http_query_task_result(
            remote_task_id,
            source_type=source_type,
            user_id=user_id,
            username=username,
        )
    except Exception as err:
        logger.warning("读取 HTTP 远端缓存结果失败: remote_task_id=%s error=%s", remote_task_id, err, exc_info=True)
        return {
            "_cache_ref_unavailable": True,
            "success": False,
            "error": str(err) or "HTTP 远端缓存结果读取失败",
            "columns": [],
            "data": [],
            "total_count": 0,
        }
    if not remote_result.get("success"):
        return {
            "_cache_ref_unavailable": True,
            "success": False,
            "error": remote_result.get("error") or "HTTP 远端缓存结果不可用",
            "status": remote_result.get("status"),
            "columns": remote_result.get("columns") or [],
            "data": remote_result.get("data") or [],
            "total_count": int(remote_result.get("total_count") or 0),
        }
    return {
        "success": True,
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


def get_execution_id_by_member(execution_map: Dict[str, str], execution_target: Dict[str, Any]) -> Optional[str]:
    """根据成员数据源获取执行记录 ID。"""
    member_datasource_id = str((execution_target or {}).get("member_datasource_id") or "").strip()
    if member_datasource_id and member_datasource_id in execution_map:
        return execution_map[member_datasource_id]
    return execution_map.get("")


def has_running_member_result(results: List[Dict[str, Any]]) -> bool:
    """判断成员结果中是否仍有未完成任务。"""
    return any(str((item or {}).get("status") or "").strip().lower() in {"pending", "running"} for item in results or [])


def normalize_execution_time_ms(value: Any) -> Optional[int]:
    """将执行耗时规整为非负毫秒值。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def _parse_datetime_value(value: Any) -> Optional[datetime]:
    """解析模型或接口中的时间值。"""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def resolve_execution_time_ms(value: Any = None, started_time: Any = None, finished_time: Any = None) -> Optional[int]:
    """优先使用显式耗时；缺失时用开始和结束时间计算。"""
    explicit_value = normalize_execution_time_ms(value)
    if explicit_value is not None:
        return explicit_value

    started = _parse_datetime_value(started_time)
    finished = _parse_datetime_value(finished_time)
    if not started or not finished:
        return None
    try:
        return max(0, int((finished - started).total_seconds() * 1000))
    except TypeError:
        return None


def attach_execution_time_to_payload(result_payload: Any, execution_time_ms: Optional[int]) -> Any:
    """将最终耗时同步写入结果负载。"""
    if not isinstance(result_payload, dict) or execution_time_ms is None or "execution_time_ms" not in result_payload:
        return result_payload
    payload = dict(result_payload)
    payload["execution_time_ms"] = execution_time_ms
    return payload


async def create_query_executions_for_task(task_id: str, request: ExecuteSqlRequest, operator_name: Optional[str] = None) -> Dict[str, str]:
    """为任务预创建每个成员数据源的 SQL 执行记录。"""
    ds_manager = get_datasource_manager()
    execution_targets = await ds_manager.get_execution_datasources_async(
        request.datasource_id,
        request.member_datasource_ids,
    )
    operator_user_id = str(request.user_id or "").strip()
    now = datetime.now()
    execution_map: Dict[str, str] = {}
    async with get_session() as db:
        for target in execution_targets:
            member_datasource_id = str((target or {}).get("member_datasource_id") or "").strip()
            member_name = str((target or {}).get("member_name") or (target or {}).get("group_name") or "").strip()
            executor_type = str((target or {}).get("query_executor_type") or "direct").strip().lower() or "direct"
            effective_sql = build_effective_sql(request.sql, request.limit or DEFAULT_ROW_LIMIT, source_type=(target or {}).get("source_type"))
            execution_id = uuid.uuid4().hex
            execution_map[member_datasource_id] = execution_id
            db.add(ChatDataQueryExecution(
                execution_id=execution_id,
                task_id=task_id,
                user_id=str(request.user_id or "").strip(),
                datasource_id=request.datasource_id,
                member_datasource_id=member_datasource_id,
                member_name=member_name,
                executor_type=executor_type,
                sql_text=effective_sql,
                sql_status="pending",
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=operator_user_id,
                create_by_name=str(operator_name or "").strip(),
                update_by=operator_user_id,
                update_by_name=str(operator_name or "").strip(),
            ))
        await db.commit()
    return execution_map


async def update_query_execution(
    execution_id: str,
    *,
    sql_status: Optional[str] = None,
    remote_task_id: Optional[str] = None,
    db_connection_id: Optional[str] = None,
    execution_time_ms: Optional[int] = None,
    result_row_count: Optional[int] = None,
    result_payload: Any = None,
    error_message: Optional[str] = None,
    started_time: Optional[datetime] = None,
    finished_time: Optional[datetime] = None,
) -> None:
    """更新 SQL 执行明细。"""
    if not execution_id:
        return
    async with get_session() as db:
        result = await db.execute(select(ChatDataQueryExecution).filter_by(execution_id=execution_id, delete_flag=0))
        execution = result.scalar_one_or_none()
        if not execution:
            return
        if execution.sql_status in {"cancelled", "kill_failed"} and sql_status in {"running", "succeeded", "failed"}:
            return
        resolved_execution_time_ms = None
        if execution_time_ms is not None or finished_time is not None:
            resolved_execution_time_ms = resolve_execution_time_ms(
                execution_time_ms,
                started_time or execution.started_time,
                finished_time or execution.finished_time,
            )
        if sql_status is not None:
            execution.sql_status = sql_status
        if remote_task_id is not None:
            execution.remote_task_id = remote_task_id
        if db_connection_id is not None:
            execution.db_connection_id = db_connection_id
        if resolved_execution_time_ms is not None:
            execution.execution_time_ms = resolved_execution_time_ms
        if result_row_count is not None:
            execution.result_row_count = result_row_count
        if result_payload is not None:
            execution.result_payload = json.dumps(
                attach_execution_time_to_payload(result_payload, resolved_execution_time_ms),
                ensure_ascii=False,
            )
        if error_message is not None:
            execution.error_message = error_message or None
        if started_time is not None:
            execution.started_time = started_time
        if finished_time is not None:
            execution.finished_time = finished_time
        execution.update_time = datetime.now()
        await db.commit()


async def update_query_execution_from_result(execution_id: str, result: Dict[str, Any], store_result_payload: bool = True) -> None:
    """按 SQL 执行结果更新执行明细。"""
    success = bool((result or {}).get("success"))
    status = "succeeded" if success else "failed"
    started_time = _parse_datetime_value((result or {}).get("started_time"))
    finished_time = _parse_datetime_value((result or {}).get("finished_time")) or datetime.now()
    result_payload = None
    if store_result_payload:
        result_payload = {
            "success": success,
            "columns": (result or {}).get("columns") or [],
            "data": (result or {}).get("data") or [],
            "total_count": int((result or {}).get("total_count") or 0),
            "execution_time_ms": (result or {}).get("execution_time_ms"),
            "error": (result or {}).get("error"),
            "cache_hit": bool((result or {}).get("cache_hit")),
            "cache_age_seconds": (result or {}).get("cache_age_seconds"),
            "cache_expires_in_seconds": (result or {}).get("cache_expires_in_seconds"),
        }
    await update_query_execution(
        execution_id,
        sql_status=status,
        execution_time_ms=(result or {}).get("execution_time_ms"),
        result_row_count=int((result or {}).get("total_count") or 0),
        result_payload=result_payload,
        error_message=None if success else str((result or {}).get("error") or "SQL 执行失败"),
        started_time=started_time,
        finished_time=finished_time,
    )


async def get_query_execution_status(execution_id: str) -> str:
    """读取 SQL 执行明细当前状态。"""
    if not execution_id:
        return ""
    async with get_session() as db:
        result = await db.execute(select(ChatDataQueryExecution.sql_status).filter_by(execution_id=execution_id, delete_flag=0))
        return str(result.scalar_one_or_none() or "").strip().lower()


async def append_query_task_remote_task(task_id: str, remote_task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """向异步任务中追加远端 HTTP 任务映射。"""
    normalized_remote_task = {
        "member_datasource_id": str((remote_task or {}).get("member_datasource_id") or "").strip(),
        "member_name": str((remote_task or {}).get("member_name") or "").strip(),
        "remote_task_id": str((remote_task or {}).get("remote_task_id") or "").strip(),
        "source_type": str((remote_task or {}).get("source_type") or "").strip().lower() or "hive",
    }
    if not normalized_remote_task["member_datasource_id"] or not normalized_remote_task["remote_task_id"]:
        return None

    async with get_session() as db:
        stmt = select(ChatDataQueryTask).filter_by(task_id=task_id, delete_flag=0).with_for_update()
        result = await db.execute(stmt)
        task = result.scalar_one_or_none()
        if not task:
            return None
        remote_tasks = load_remote_tasks(task.remote_task_payload)
        exists = any(
            item.get("member_datasource_id") == normalized_remote_task["member_datasource_id"]
            and item.get("remote_task_id") == normalized_remote_task["remote_task_id"]
            for item in remote_tasks
        )
        if not exists:
            remote_tasks.append(normalized_remote_task)
            task.remote_task_payload = json.dumps(remote_tasks, ensure_ascii=False)
            task.update_time = datetime.now()
            await db.commit()
            await db.refresh(task)
        return build_query_task_response_data(build_query_task_runtime_data(task))


async def execute_sql_for_member(
    execution_target: Dict[str, Any],
    sql: str,
    limit: int,
    timeout: Optional[int],
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    query_task_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    skip_query_cache: bool = False,
    datasource_id: Optional[str] = None,
) -> Dict[str, Any]:
    """执行单个成员数据源 SQL。"""
    member_datasource_id = execution_target.get("member_datasource_id")
    member_name = execution_target.get("member_name") or execution_target.get("group_name") or ""
    effective_sql = build_effective_sql(sql, limit, source_type=execution_target.get("source_type"))
    cache_identity = build_sql_result_cache_identity(execution_target, effective_sql, datasource_id=datasource_id)
    http_target = is_http_execution_target(execution_target)
    local_started_at = time.perf_counter()

    async def on_remote_task_created(payload: Dict[str, Any]) -> None:
        remote_task_id = str((payload or {}).get("remote_task_id") or "").strip()
        if not remote_task_id:
            return
        if execution_id:
            await update_query_execution(execution_id, remote_task_id=remote_task_id)
        if query_task_id:
            await append_query_task_remote_task(
                query_task_id,
                {
                    "member_datasource_id": member_datasource_id,
                    "member_name": member_name,
                    "remote_task_id": remote_task_id,
                    "source_type": str((payload or {}).get("source_type") or "").strip().lower() or "hive",
                },
            )

    async def on_db_connection_created(payload: Dict[str, Any]) -> None:
        if not execution_id:
            return
        db_connection_id = str((payload or {}).get("db_connection_id") or "").strip()
        if db_connection_id:
            await update_query_execution(execution_id, db_connection_id=db_connection_id)

    execute_kwargs = {
        "sql": sql,
        "limit": limit,
        "timeout": timeout,
        "user_id": user_id,
    }

    executor = None
    try:
        if execution_id:
            if await get_query_execution_status(execution_id) == "cancelled":
                return {
                    "member_datasource_id": member_datasource_id,
                    "member_name": member_name,
                    "sql": effective_sql,
                    "success": False,
                    "columns": [],
                    "data": [],
                    "total_count": 0,
                    "execution_time_ms": None,
                    "remote_task_id": None,
                    "db_connection_id": None,
                    "error": "已人工取消",
                }
            await update_query_execution(execution_id, sql_status="running", started_time=datetime.now(), error_message="")
            if await get_query_execution_status(execution_id) == "cancelled":
                return {
                    "member_datasource_id": member_datasource_id,
                    "member_name": member_name,
                    "sql": effective_sql,
                    "success": False,
                    "columns": [],
                    "data": [],
                    "total_count": 0,
                    "execution_time_ms": None,
                    "remote_task_id": None,
                    "db_connection_id": None,
                    "error": "已人工取消",
                }

        async def execute_query() -> Dict[str, Any]:
            nonlocal executor
            executor = get_executor(execution_target)
            if "username" in inspect.signature(executor.execute).parameters:
                execute_kwargs["username"] = username
            if "remote_task_callback" in inspect.signature(executor.execute).parameters:
                execute_kwargs["remote_task_callback"] = on_remote_task_created
            if "db_connection_callback" in inspect.signature(executor.execute).parameters:
                execute_kwargs["db_connection_callback"] = on_db_connection_created
            return await executor.execute(**execute_kwargs)

        async def execute_query_and_update_cache() -> Dict[str, Any]:
            query_result = await run_with_sql_user_datasource_limit(user_id, execution_target, execute_query)
            if not is_sql_result_cache_enabled(skip_query_cache):
                return query_result
            try:
                if http_target:
                    await save_http_remote_ref_sql_result_cache(cache_identity, query_result, operator_id=user_id, operator_name=username)
                else:
                    await save_sql_result_cache(cache_identity, query_result, operator_id=user_id, operator_name=username)
            except Exception as cache_err:
                logger.warning("保存 SQL 结果缓存失败，忽略缓存写入: cache_key=%s error=%s", cache_identity.get("cache_key"), cache_err, exc_info=True)
            return query_result

        if is_sql_result_cache_enabled(skip_query_cache):
            try:
                cached_result = await get_sql_result_cache(cache_identity)
            except Exception as cache_err:
                logger.warning("读取 SQL 结果缓存失败，降级查询数据源: cache_key=%s error=%s", cache_identity.get("cache_key"), cache_err, exc_info=True)
                cached_result = None
            if cached_result:
                if http_target:
                    if is_http_remote_ref_cache(cached_result):
                        result = await fetch_http_remote_ref_cache_result(cached_result, user_id=user_id, username=username)
                        if result.get("_cache_ref_unavailable"):
                            logger.info(
                                "HTTP 远端缓存结果不可用，重新提交查询: cache_key=%s remote_task_id=%s status=%s error=%s",
                                cache_identity.get("cache_key"),
                                cached_result.get("remote_task_id"),
                                result.get("status"),
                                result.get("error"),
                            )
                            result = await execute_query_and_update_cache()
                        else:
                            await on_remote_task_created({
                                "remote_task_id": result.get("remote_task_id"),
                                "source_type": cached_result.get("source_type") or execution_target.get("source_type"),
                            })
                    else:
                        result = await execute_query_and_update_cache()
                else:
                    if is_http_remote_ref_cache(cached_result):
                        result = await execute_query_and_update_cache()
                    else:
                        result = cached_result
            else:
                result = await execute_query_and_update_cache()
        else:
            result = await run_with_sql_user_datasource_limit(user_id, execution_target, execute_query)
        if execution_id and await get_query_execution_status(execution_id) == "cancelled":
            result = {"success": False, "error": "已人工取消", "columns": [], "data": [], "total_count": 0}
    except Exception as e:
        logger.error("成员 SQL 执行异常: member_datasource_id=%s error=%s", member_datasource_id, e, exc_info=True)
        result = {"success": False, "error": str(e) or "SQL 执行失败", "columns": [], "data": [], "total_count": 0}
    finally:
        if executor is not None:
            await executor.close()
    if result.get("execution_time_ms") is None and str(result.get("status") or "").strip().lower() not in {"pending", "running"}:
        result["execution_time_ms"] = int((time.perf_counter() - local_started_at) * 1000)
    if execution_id:
        await update_query_execution_from_result(execution_id, result, store_result_payload=not http_target)
    return {
        "member_datasource_id": member_datasource_id,
        "member_name": member_name,
        "sql": effective_sql,
        "success": bool(result.get("success")),
        "status": result.get("status"),
        "columns": result.get("columns") or [],
        "data": result.get("data") or [],
        "total_count": int(result.get("total_count") or 0),
        "execution_time_ms": result.get("execution_time_ms"),
        "started_time": result.get("started_time"),
        "finished_time": result.get("finished_time"),
        "remote_task_id": result.get("remote_task_id"),
        "db_connection_id": result.get("db_connection_id"),
        "error": result.get("error"),
        "cache_hit": bool(result.get("cache_hit")),
        "cache_age_seconds": result.get("cache_age_seconds"),
        "cache_expires_in_seconds": result.get("cache_expires_in_seconds"),
    }


async def build_execute_sql_result_data(
    request: ExecuteSqlRequest,
    query_task_id: Optional[str] = None,
    execution_map: Optional[Dict[str, str]] = None,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    """执行 SQL 并返回统一结果数据。"""
    start_time = time.time()
    ds_manager = get_datasource_manager()
    datasource = await ds_manager.get_datasource_async(request.datasource_id)

    if not datasource:
        raise ValueError(f"数据源不存在: {request.datasource_id}")

    execution_targets = await ds_manager.get_execution_datasources_async(
        request.datasource_id,
        request.member_datasource_ids,
    )
    results = await asyncio.gather(*[
        execute_sql_for_member(
            execution_target=target,
            sql=request.sql,
            limit=request.limit or DEFAULT_ROW_LIMIT,
            timeout=request.timeout,
            user_id=request.user_id,
            username=username,
            query_task_id=query_task_id,
            execution_id=get_execution_id_by_member(execution_map or {}, target),
            skip_query_cache=request.skip_query_cache,
            datasource_id=request.datasource_id,
        )
        for target in execution_targets
    ])

    execution_time_ms = int((time.time() - start_time) * 1000)
    primary_result = next((item for item in results if item.get("success")), results[0] if results else {})
    successful_results = [item for item in results if item.get("success")]
    has_running_results = has_running_member_result(results)
    overall_success = bool(successful_results) and not has_running_results
    if overall_success:
        error_message = None
    elif has_running_results:
        error_message = "部分执行对象仍在查询中"
    else:
        error_messages = [
            str(item.get("error") or "").strip()
            for item in results
            if str(item.get("error") or "").strip()
        ]
        error_message = "；".join(error_messages) if error_messages else "所有执行对象均执行失败"
    return {
        "success": overall_success,
        "sql": primary_result.get("sql") or request.sql,
        "columns": primary_result.get("columns") or [],
        "data": primary_result.get("data") or [],
        "total_count": primary_result.get("total_count") or 0,
        "execution_time_ms": primary_result.get("execution_time_ms") if primary_result.get("execution_time_ms") is not None else execution_time_ms,
        "member_datasource_id": primary_result.get("member_datasource_id"),
        "member_name": primary_result.get("member_name"),
        "error": error_message,
        "results": results,
        "cache_hit": bool(primary_result.get("cache_hit")),
        "cache_age_seconds": primary_result.get("cache_age_seconds"),
        "cache_expires_in_seconds": primary_result.get("cache_expires_in_seconds"),
    }


def build_query_task_runtime_data(task: ChatDataQueryTask) -> Dict[str, Any]:
    """将任务模型转换为可脱离 session 使用的字典。"""
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "datasource_id": task.datasource_id,
        "query_type": task.query_type,
        "status": task.task_status,
        "progress_text": task.progress_text,
        "request_payload": task.request_payload,
        "result_payload": task.result_payload,
        "remote_task_payload": task.remote_task_payload,
        "error_message": task.error_message,
        "started_time": task.started_time.isoformat() if task.started_time else None,
        "finished_time": task.finished_time.isoformat() if task.finished_time else None,
        "create_time": task.create_time.isoformat() if task.create_time else None,
        "update_time": task.update_time.isoformat() if task.update_time else None,
        "create_by": task.create_by,
        "update_by": task.update_by,
        "create_by_name": task.create_by_name,
        "update_by_name": task.update_by_name,
    }


def load_remote_tasks(raw_payload: Any) -> List[Dict[str, Any]]:
    """解析远端任务映射列表。"""
    if not raw_payload:
        return []
    if isinstance(raw_payload, list):
        payload = raw_payload
    else:
        try:
            payload = json.loads(raw_payload)
        except Exception:
            return []
    if not isinstance(payload, list):
        return []
    result: List[Dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        remote_task_id = str(item.get("remote_task_id") or "").strip()
        member_datasource_id = str(item.get("member_datasource_id") or "").strip()
        member_name = str(item.get("member_name") or "").strip()
        source_type = str(item.get("source_type") or "").strip().lower() or "hive"
        if not remote_task_id or not member_datasource_id:
            continue
        result.append({
            "member_datasource_id": member_datasource_id,
            "member_name": member_name,
            "remote_task_id": remote_task_id,
            "source_type": source_type,
        })
    return result


async def are_all_sql_execution_targets_http(request: ExecuteSqlRequest) -> bool:
    """判断 SQL 任务选中的执行对象是否全部为 HTTP 执行。"""
    ds_manager = get_datasource_manager()
    execution_targets = await ds_manager.get_execution_datasources_async(
        request.datasource_id,
        request.member_datasource_ids,
    )
    return bool(execution_targets) and all(
        str((target or {}).get("query_executor_type") or "").strip().lower() == "http"
        for target in execution_targets
    )


async def get_http_execution_member_ids(request: ExecuteSqlRequest) -> Set[str]:
    """读取本次 SQL 任务中走 HTTP 的成员数据源 ID。"""
    ds_manager = get_datasource_manager()
    execution_targets = await ds_manager.get_execution_datasources_async(
        request.datasource_id,
        request.member_datasource_ids,
    )
    return {
        str((target or {}).get("member_datasource_id") or "").strip()
        for target in execution_targets
        if is_http_execution_target(target)
    }


async def build_persistent_query_task_result_for_request(request: ExecuteSqlRequest, result_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """按执行配置构建任务落库结果，HTTP 结果不落库。"""
    try:
        all_http_targets = await are_all_sql_execution_targets_http(request)
    except Exception:
        all_http_targets = False
    if all_http_targets:
        return None
    try:
        http_member_ids = await get_http_execution_member_ids(request)
    except Exception:
        http_member_ids = set()
    return build_persistent_query_task_result_payload(result_data, http_member_ids)


def build_persistent_query_task_result_payload(result_data: Dict[str, Any], http_member_ids: Set[str]) -> Optional[Dict[str, Any]]:
    """构建可落库的任务结果，过滤 HTTP 返回数据。"""
    if not http_member_ids:
        return result_data
    local_results = [
        item for item in list((result_data or {}).get("results") or [])
        if str((item or {}).get("member_datasource_id") or "").strip() not in http_member_ids
    ]
    if not local_results:
        return None

    primary_result = next((item for item in local_results if item.get("success")), local_results[0])
    successful_results = [item for item in local_results if item.get("success")]
    if successful_results:
        error_message = None
    else:
        error_messages = [
            str(item.get("error") or "").strip()
            for item in local_results
            if str(item.get("error") or "").strip()
        ]
        error_message = "；".join(error_messages) if error_messages else (result_data or {}).get("error")

    persisted = dict(result_data or {})
    persisted.update({
        "success": bool(successful_results),
        "sql": primary_result.get("sql") or (result_data or {}).get("sql"),
        "columns": primary_result.get("columns") or [],
        "data": primary_result.get("data") or [],
        "total_count": int(primary_result.get("total_count") or 0),
        "execution_time_ms": primary_result.get("execution_time_ms"),
        "member_datasource_id": primary_result.get("member_datasource_id"),
        "member_name": primary_result.get("member_name"),
        "error": error_message,
        "results": local_results,
        "cache_hit": bool(primary_result.get("cache_hit")),
        "cache_age_seconds": primary_result.get("cache_age_seconds"),
        "cache_expires_in_seconds": primary_result.get("cache_expires_in_seconds"),
    })
    return persisted


def build_query_task_response_data(task_data: Dict[str, Any]) -> Dict[str, Any]:
    """将任务运行态字典转换为接口返回结构。"""
    result_payload = None
    if task_data.get("result_payload"):
        try:
            result_payload = json.loads(task_data["result_payload"])
        except Exception:
            result_payload = None
    request_payload = None
    if task_data.get("request_payload"):
        try:
            request_payload = json.loads(task_data["request_payload"])
        except Exception:
            request_payload = None
    remote_tasks = load_remote_tasks(task_data.get("remote_task_payload"))
    return {
        "task_id": task_data.get("task_id"),
        "task_type": task_data.get("task_type"),
        "datasource_id": task_data.get("datasource_id"),
        "query_type": task_data.get("query_type"),
        "status": task_data.get("status"),
        "progress_text": task_data.get("progress_text"),
        "request_payload": request_payload,
        "result_payload": result_payload,
        "remote_tasks": remote_tasks,
        "error_message": task_data.get("error_message"),
        "started_time": task_data.get("started_time"),
        "finished_time": task_data.get("finished_time"),
        "create_time": task_data.get("create_time"),
        "update_time": task_data.get("update_time"),
    }


def get_task_operator_name(task_data: Dict[str, Any]) -> str:
    """从任务请求负载中解析发起用户名。"""
    try:
        payload = json.loads(task_data.get("request_payload") or "{}")
    except Exception:
        payload = {}
    return str((payload or {}).get("operator_name") or "").strip()


async def get_query_executions(task_id: str) -> List[Dict[str, Any]]:
    """读取任务下的 SQL 执行明细。"""
    if not task_id:
        return []
    async with get_session() as db:
        result = await db.execute(
            select(ChatDataQueryExecution)
            .filter_by(task_id=task_id, delete_flag=0)
            .order_by(ChatDataQueryExecution.create_time.asc(), ChatDataQueryExecution.id.asc())
        )
        executions = [item.to_dict() for item in result.scalars().all()]
        if executions:
            task_result = await db.execute(select(ChatDataQueryTask).filter_by(task_id=task_id, delete_flag=0))
            task = task_result.scalar_one_or_none()
            operator_name = str((task.create_by_name or task.update_by_name) if task else "").strip()
            if not operator_name and task:
                operator_name = get_task_operator_name(build_query_task_runtime_data(task))
            if operator_name:
                for item in executions:
                    item["operator_name"] = item.get("create_by_name") or item.get("update_by_name") or operator_name
        return executions


async def create_task_record(
    *,
    task_type: str,
    datasource_id: str,
    query_type: str,
    request_payload: Dict[str, Any],
    progress_text: str = "任务已创建，等待执行",
) -> Dict[str, Any]:
    """创建异步任务记录。"""
    operator_user_id = str((request_payload or {}).get("user_id") or "").strip()
    operator_name = str((request_payload or {}).get("operator_name") or "").strip()
    task = ChatDataQueryTask(
        task_id=uuid.uuid4().hex,
        task_type=task_type,
        datasource_id=datasource_id,
        query_type=query_type or "sql",
        task_status="pending",
        progress_text=progress_text,
        request_payload=json.dumps(request_payload, ensure_ascii=False),
        delete_flag=0,
        create_time=datetime.now(),
        create_by=operator_user_id,
        create_by_name=operator_name,
        update_by=operator_user_id,
        update_by_name=operator_name,
    )
    async with get_session() as db:
        db.add(task)
        await db.flush()
        await db.refresh(task)
        return build_query_task_response_data(build_query_task_runtime_data(task))


async def create_query_task(payload: QueryTaskCreateRequest) -> Dict[str, Any]:
    """创建 SQL 异步查询任务。"""
    return await create_task_record(
        task_type=payload.task_type,
        datasource_id=payload.datasource_id,
        query_type=payload.query_type or "sql",
        request_payload=payload.model_dump(),
    )


async def prepare_sql_request_for_execution(request: ExecuteSqlRequest | QueryTaskCreateRequest, datasource: Optional[Dict[str, Any]] = None):
    """保留 SQL 原文；语义可视化查询在服务端重新编译，安全校验统一在执行器边界完成。"""
    query_type = str(getattr(request, "query_type", "") or "").strip().lower()
    semantic_builder_config = getattr(request, "semantic_builder_config", None)
    semantic_config_id = str(getattr(request, "semantic_config_id", "") or "").strip()
    if query_type == "builder" and not semantic_builder_config:
        raise ValueError("可视化查询必须基于指标集，请先选择指标集")
    if not semantic_builder_config:
        return request
    if not semantic_config_id:
        raise ValueError("语义可视化查询缺少 semantic_config_id")
    request_user_id = str(getattr(request, "user_id", "") or "").strip()
    if request_user_id:
        compiled = await compile_semantic_builder_sql(
            datasource_id=request.datasource_id,
            semantic_config_id=semantic_config_id,
            semantic_builder_config=semantic_builder_config,
            user_id=request_user_id,
        )
    else:
        compiled = await compile_semantic_builder_sql(
            datasource_id=request.datasource_id,
            semantic_config_id=semantic_config_id,
            semantic_builder_config=semantic_builder_config,
        )
    return request.model_copy(update={"sql": compiled["sql"], "builder_config": None})


async def create_task_for_execute_request(
    request: ExecuteSqlRequest,
    *,
    query_type: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> Dict[str, Any]:
    """为直接执行或智能体执行创建任务主记录。"""
    payload = QueryTaskCreateRequest(
        task_type="sql",
        datasource_id=request.datasource_id,
        member_datasource_ids=request.member_datasource_ids,
        user_id=request.user_id,
        operator_name=operator_name,
        conversation_id=request.conversation_id,
        query_text=request.query_text,
        semantic_config_id=request.semantic_config_id,
        sql=request.sql,
        limit=request.limit,
        timeout=request.timeout,
        query_type=query_type or request.query_type or "sql",
        builder_config=request.builder_config,
        semantic_builder_config=request.semantic_builder_config,
        skip_query_cache=request.skip_query_cache,
    )
    return await create_query_task(payload)


async def get_query_task(task_id: str) -> Optional[Dict[str, Any]]:
    """读取异步查询任务。"""
    async with get_session() as db:
        result = await db.execute(select(ChatDataQueryTask).filter_by(task_id=task_id, delete_flag=0))
        task = result.scalar_one_or_none()
        if not task:
            return None
        return build_query_task_runtime_data(task)


async def is_query_task_cancelled(task_id: str) -> bool:
    """判断异步查询任务是否已取消。"""
    task_data = await get_query_task(task_id)
    return str((task_data or {}).get("status") or "").strip().lower() == "cancelled"


async def update_query_task(
    task_id: str,
    *,
    status: Optional[str] = None,
    progress_text: Optional[str] = None,
    result_payload: Any = None,
    error_message: Optional[str] = None,
    started_time: Optional[datetime] = None,
    finished_time: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """更新异步查询任务。"""
    async with get_session() as db:
        result = await db.execute(select(ChatDataQueryTask).filter_by(task_id=task_id, delete_flag=0))
        task = result.scalar_one_or_none()
        if not task:
            return None
        if task.task_status == "cancelled" and status in {"running", "succeeded", "failed", "partial_success"}:
            return build_query_task_response_data(build_query_task_runtime_data(task))
        if status is not None:
            task.task_status = status
        if progress_text is not None:
            task.progress_text = progress_text
        if result_payload is not None:
            task.result_payload = json.dumps(result_payload, ensure_ascii=False)
        if error_message is not None:
            task.error_message = error_message
        if started_time is not None:
            task.started_time = started_time
        if finished_time is not None:
            task.finished_time = finished_time
        task.update_time = datetime.now()
        await db.commit()
        await db.refresh(task)
        return build_query_task_response_data(build_query_task_runtime_data(task))


async def run_sql_query_task(task_id: str, task: Dict[str, Any]) -> None:
    """后台执行 SQL 查询任务。"""
    if await is_query_task_cancelled(task_id):
        return
    await update_query_task(
        task_id,
        status="running",
        progress_text="SQL 执行中",
        started_time=datetime.now(),
        error_message=None,
    )
    if await is_query_task_cancelled(task_id):
        return
    request_payload = json.loads(task.get("request_payload") or "{}")
    execute_request = ExecuteSqlRequest(**request_payload)
    operator_name = str(request_payload.get("operator_name") or "").strip() or None
    try:
        execution_map = await create_query_executions_for_task(task_id, execute_request, operator_name=operator_name)
        result_data = await build_execute_sql_result_data(execute_request, query_task_id=task_id, execution_map=execution_map, username=operator_name)
    except Exception as e:
        logger.error("后台 SQL 查询任务执行异常: task_id=%s error=%s", task_id, e, exc_info=True)
        await update_query_task(
            task_id,
            status="failed",
            progress_text="执行失败",
            error_message=str(e) or "SQL 执行失败",
            finished_time=datetime.now(),
        )
        return
    if await is_query_task_cancelled(task_id):
        return
    persistent_result_payload = await build_persistent_query_task_result_for_request(execute_request, result_data)
    if not result_data.get("success"):
        await update_query_task(
            task_id,
            status="failed",
            progress_text="执行失败",
            result_payload=persistent_result_payload,
            error_message=result_data.get("error") or "SQL 执行失败",
            finished_time=datetime.now(),
        )
        return

    await update_query_task(
        task_id,
        status="succeeded",
        progress_text="执行完成",
        result_payload=persistent_result_payload,
        error_message=None,
        finished_time=datetime.now(),
    )


async def execute_sql_request_with_task(
    request: ExecuteSqlRequest,
    *,
    query_type: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> Dict[str, Any]:
    """创建任务并同步执行 SQL，供直接执行和智能体执行复用。"""
    task_info = await create_task_for_execute_request(request, query_type=query_type or request.query_type or "sql", operator_name=operator_name)
    try:
        execution_map = await create_query_executions_for_task(task_info["task_id"], request, operator_name=operator_name)
        await update_query_task(task_info["task_id"], status="running", progress_text="SQL 执行中", started_time=datetime.now(), error_message=None)
        result_data = await build_execute_sql_result_data(request, query_task_id=task_info["task_id"], execution_map=execution_map, username=operator_name)
    except Exception as e:
        await update_query_task(
            task_info["task_id"],
            status="failed",
            progress_text="执行失败",
            error_message=str(e) or "SQL 执行失败",
            finished_time=datetime.now(),
        )
        raise
    persistent_result_payload = await build_persistent_query_task_result_for_request(request, result_data)
    if not result_data.get("success"):
        await update_query_task(
            task_info["task_id"],
            status="failed",
            progress_text="执行失败",
            result_payload=persistent_result_payload,
            error_message=result_data.get("error") or "SQL 执行失败",
            finished_time=datetime.now(),
        )
        return result_data

    await update_query_task(
        task_info["task_id"],
        status="succeeded",
        progress_text="执行完成",
        result_payload=persistent_result_payload,
        error_message=None,
        finished_time=datetime.now(),
    )
    return result_data


async def execute_agent_sql(
    sql: str,
    datasource_id: Optional[str],
    member_datasource_ids: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    semantic_config_id: Optional[str] = None,
    query_text: Optional[str] = None,
    conversation_id: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> Dict[str, Any]:
    """智能体 SQL 执行入口，复用统一查询运行时。"""
    if not datasource_id:
        logger.error("未指定数据源ID，无法执行真实查询")
        return {
            "success": False,
            "sql": sql,
            "error": "未指定数据源ID，无法执行查询",
            "columns": [],
            "data": [],
            "total_count": 0,
        }

    ds_manager = get_datasource_manager()
    datasource = await ds_manager.get_datasource_async(datasource_id)
    if not datasource:
        logger.error("数据源不存在: %s", datasource_id)
        return {
            "success": False,
            "error": f"数据源不存在: {datasource_id}",
            "columns": [],
            "data": [],
            "total_count": 0,
        }

    execute_request = ExecuteSqlRequest(
        sql=sql,
        datasource_id=datasource_id,
        member_datasource_ids=member_datasource_ids or [],
        user_id=user_id,
        conversation_id=conversation_id,
        query_text=query_text,
        semantic_config_id=semantic_config_id,
        query_type="agent",
    )
    task_info = None
    execution_map: Dict[str, str] = {}
    try:
        task_info = await create_task_for_execute_request(execute_request, query_type="agent", operator_name=operator_name)
        execution_map = await create_query_executions_for_task(task_info["task_id"], execute_request, operator_name=operator_name)
        await update_query_task(task_info["task_id"], status="running", progress_text="SQL 执行中", started_time=datetime.now(), error_message=None)
    except Exception as task_err:
        logger.warning("创建智能体 SQL 查询任务失败，将直接执行查询: %s", task_err, exc_info=True)

    try:
        execution_targets = await ds_manager.get_execution_datasources_async(datasource_id, member_datasource_ids)
        member_results = await asyncio.gather(*[
            execute_sql_for_member(
                execution_target=target,
                sql=sql,
                limit=DEFAULT_ROW_LIMIT,
                timeout=None,
                user_id=user_id,
                username=operator_name,
                execution_id=get_execution_id_by_member(execution_map, target) if execution_map else None,
                datasource_id=datasource_id,
            )
            for target in execution_targets
        ])
        if len(member_results) == 1:
            final_result = member_results[0]
        else:
            primary_result = next((item for item in member_results if item.get("success")), member_results[0] if member_results else {})
            successful_results = [item for item in member_results if item.get("success")]
            has_running_results = has_running_member_result(member_results)
            final_result = {
                "success": bool(successful_results) and not has_running_results,
                "sql": primary_result.get("sql") or sql,
                "columns": primary_result.get("columns") or [],
                "data": primary_result.get("data") or [],
                "total_count": primary_result.get("total_count") or 0,
                "execution_time_ms": primary_result.get("execution_time_ms"),
                "error": "部分执行对象仍在查询中" if has_running_results else (None if successful_results else (primary_result.get("error") or "查询失败")),
                "member_datasource_id": primary_result.get("member_datasource_id"),
                "member_name": primary_result.get("member_name"),
                "results": member_results,
            }
        if task_info:
            persistent_result_payload = final_result
            try:
                persistent_result_payload = await build_persistent_query_task_result_for_request(execute_request, final_result)
            except Exception as persist_err:
                logger.warning("构建智能体 SQL 查询任务落库结果失败，跳过结果落库: %s", persist_err, exc_info=True)
                persistent_result_payload = None
            await update_query_task(
                task_info["task_id"],
                status="succeeded" if final_result.get("success") else "failed",
                progress_text="执行完成" if final_result.get("success") else "执行失败",
                result_payload=persistent_result_payload,
                error_message=None if final_result.get("success") else final_result.get("error") or "SQL 执行失败",
                finished_time=datetime.now(),
            )
        return final_result
    except ValueError as e:
        logger.error("不支持的数据源类型: %s", e)
        if task_info:
            try:
                await update_query_task(
                    task_info["task_id"],
                    status="failed",
                    progress_text="执行失败",
                    error_message=str(e) or "SQL 执行失败",
                    finished_time=datetime.now(),
                )
            except Exception as update_err:
                logger.error("更新智能体 SQL 查询任务失败状态异常: %s", update_err, exc_info=True)
        return {
            "success": False,
            "sql": sql,
            "error": str(e),
            "columns": [],
            "data": [],
            "total_count": 0,
        }
    except Exception as e:
        logger.error("执行 SQL 异常: %s", e, exc_info=True)
        if task_info:
            try:
                await update_query_task(
                    task_info["task_id"],
                    status="failed",
                    progress_text="执行失败",
                    error_message=str(e) or "SQL 执行失败",
                    finished_time=datetime.now(),
                )
            except Exception as update_err:
                logger.error("更新智能体 SQL 查询任务失败状态异常: %s", update_err, exc_info=True)
        return {
            "success": False,
            "sql": sql,
            "error": f"执行失败: {str(e)}",
            "columns": [],
            "data": [],
            "total_count": 0,
        }
