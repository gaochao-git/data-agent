"""
ChatData 查询运行时服务。

该模块承接 SQL/Builder/Agent 查询进入执行阶段后的公共编排：
任务记录、执行明细、成员数据源执行、缓存、结果落库与任务状态更新。
"""

import asyncio
import functools
import inspect
import json
import time
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from utils.database import get_session
from utils.models import ChatDataQueryExecution, ChatDataQueryTask
from utils.logger import get_logger
from utils.alarm import emit_dangerous_sql_alarm

from ...daos.query_execution_dao import QueryExecutionDao
from ...daos.query_task_dao import QueryTaskDao
from ...schema import ExecuteSqlRequest, InternalQueryTaskCreateRequest, QueryTaskCreateRequest
from . import cache as query_cache
from ..datasource.manager import get_datasource_manager
from ..semantic.run.semantic_query_service import compile_semantic_builder_sql
from ..setting_service import get_runtime_setting
from .concurrency import run_with_sql_user_datasource_limit
from .executor import build_effective_sql, fetch_http_query_task_result, get_executor
from .sql_guard import run_sql_guard
from .result import (
    QUERY_SUCCEEDED_STATUS,
    build_query_result_payload,
    query_result_status,
    query_result_succeeded,
)

logger = get_logger(__name__)

MAX_EXECUTION_TIME_MS = 86_400_000

query_task_dao = QueryTaskDao(session_context=lambda: get_session())
query_execution_dao = QueryExecutionDao(session_context=lambda: get_session())


def _datasource_source_type(datasource: Optional[Dict[str, Any]]) -> Optional[str]:
    """读取数据源组方言，用于任务创建阶段的快速 SQL 预校验。"""
    source_type = str((datasource or {}).get("source_type") or "").strip().lower()
    return source_type or None


def _validate_sql_before_task_submit(
    request: ExecuteSqlRequest | QueryTaskCreateRequest,
    datasource: Optional[Dict[str, Any]],
) -> None:
    """创建任务前仅当场拒绝多语句，其余违规延迟到执行期统一拦截与展示。

    多语句可能绕过只读限制，必须在任务创建前拒绝。语法错误、命令类型、
    副作用构造和 LIMIT 超限与语法错误同构：任务正常创建，由执行器边界的
    sql_guard 复检拦截并生成成员失败结果，页面在结果面板统一展示错误、
    边界发出高危报警——避免同类 SQL 内容错误一部分弹全局提示、一部分进面板。
    """
    if datasource is None:
        return
    sql = str(getattr(request, "sql", "") or "").strip()
    if not sql:
        return
    source_type = _datasource_source_type(datasource)
    violation = run_sql_guard(sql, source_type=source_type, defer_syntax_error=True)
    if violation is None or violation.rule != "single_statement":
        return
    emit_dangerous_sql_alarm(
        sql,
        violation.message,
        source_type=source_type,
        datasource_id=str(getattr(request, "datasource_id", "") or ""),
        user_id=str(getattr(request, "user_id", "") or ""),
        execution_scope="task_submit",
        reason_code=violation.reason_code,
    )
    raise ValueError(violation.message)


async def fetch_http_remote_ref_cache_result(cached_result: Dict[str, Any], user_id: Optional[str] = None, username: Optional[str] = None) -> Dict[str, Any]:
    """按 HTTP 远端任务引用读取结果，并保留本次缓存命中元信息。"""
    return await query_cache.fetch_http_remote_ref_cache_result(
        cached_result,
        fetch_remote_result=fetch_http_query_task_result,
        user_id=user_id,
        username=username,
    )


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


def execution_time_ms_for_storage(value: Optional[int], execution_id: str) -> Optional[int]:
    """将异常执行耗时限制在 24 小时，避免污染历史展示和落库失败。"""
    if value is None or value <= MAX_EXECUTION_TIME_MS:
        return value
    logger.warning(
        "SQL 执行耗时超过 24 小时，按上限存储: execution_id=%s execution_time_ms=%s max=%s",
        execution_id,
        value,
        MAX_EXECUTION_TIME_MS,
    )
    return MAX_EXECUTION_TIME_MS


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
    execution_targets = await ds_manager.get_authorized_execution_datasources_async(
        request.datasource_id,
        request.member_datasource_ids,
        user_id=request.user_id,
        username=operator_name,
    )
    operator_user_id = str(request.user_id or "").strip()
    member_ids: List[str] = []
    specs: List[Dict[str, str]] = []
    for target in execution_targets:
        member_datasource_id = str((target or {}).get("member_datasource_id") or "").strip()
        member_name = str((target or {}).get("member_name") or (target or {}).get("group_name") or "").strip()
        executor_type = str((target or {}).get("query_executor_type") or "direct").strip().lower() or "direct"
        member_ids.append(member_datasource_id)
        specs.append({
            "execution_id": uuid.uuid4().hex,
            "task_id": task_id,
            "user_id": operator_user_id,
            "datasource_id": request.datasource_id,
            "member_datasource_id": member_datasource_id,
            "member_name": member_name,
            "executor_type": executor_type,
            # 执行阶段再生成实际 SQL，语法错误也需要拥有独立的历史明细。
            "sql_text": request.sql,
            "operator_name": str(operator_name or "").strip(),
        })
    execution_ids = await query_execution_dao.create_pending_executions(specs)
    return dict(zip(member_ids, execution_ids))


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

    def apply_execution_updates(execution: ChatDataQueryExecution) -> bool:
        # 行锁读取后校验终态守卫：取消操作与执行线程并发写时，先锁定再校验，
        # 避免 REPEATABLE READ 快照读读到旧的 cancelled 前状态、回写覆盖终态。
        if execution.sql_status in {"cancelled", "kill_failed"} and sql_status in {"running", "succeeded", "failed"}:
            return False
        resolved_execution_time_ms = _resolve_execution_time_for_update(
            execution, execution_id,
            execution_time_ms=execution_time_ms, started_time=started_time, finished_time=finished_time,
        )
        _apply_execution_field_updates(
            execution,
            sql_status=sql_status,
            remote_task_id=remote_task_id,
            db_connection_id=db_connection_id,
            resolved_execution_time_ms=resolved_execution_time_ms,
            result_row_count=result_row_count,
            result_payload=result_payload,
            error_message=error_message,
            started_time=started_time,
            finished_time=finished_time,
        )
        return True

    await query_execution_dao.update_execution_locked(execution_id, apply_execution_updates)


def _resolve_execution_time_for_update(
    execution: ChatDataQueryExecution,
    execution_id: str,
    *,
    execution_time_ms: Optional[int],
    started_time: Optional[datetime],
    finished_time: Optional[datetime],
) -> Optional[int]:
    """有结束时间或显式耗时入参时，归一化执行耗时（回退到行内已存时间）。"""
    if execution_time_ms is None and finished_time is None:
        return None
    return execution_time_ms_for_storage(
        resolve_execution_time_ms(
            execution_time_ms,
            started_time or execution.started_time,
            finished_time or execution.finished_time,
        ),
        execution_id,
    )


def _apply_execution_field_updates(
    execution: ChatDataQueryExecution,
    *,
    sql_status: Optional[str],
    remote_task_id: Optional[str],
    db_connection_id: Optional[str],
    resolved_execution_time_ms: Optional[int],
    result_row_count: Optional[int],
    result_payload: Any,
    error_message: Optional[str],
    started_time: Optional[datetime],
    finished_time: Optional[datetime],
) -> None:
    """仅更新显式传入（非 None）的字段。"""
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


def _execution_result_payload(status: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """构造执行明细的 result_payload 落库快照。"""
    return {
        "status": status,
        "columns": result.get("columns") or [],
        "data": result.get("data") or [],
        "total_count": int(result.get("total_count") or 0),
        "execution_time_ms": result.get("execution_time_ms"),
        "error": result.get("error"),
        "cache_hit": bool(result.get("cache_hit")),
        "cache_age_seconds": result.get("cache_age_seconds"),
        "cache_expires_in_seconds": result.get("cache_expires_in_seconds"),
    }


async def update_query_execution_from_result(execution_id: str, result: Dict[str, Any], store_result_payload: bool = True) -> None:
    """按 SQL 执行结果更新执行明细。"""
    result = result or {}
    status = query_result_status(result)
    success = status == QUERY_SUCCEEDED_STATUS
    result_payload = _execution_result_payload(status, result) if store_result_payload else None
    await update_query_execution(
        execution_id,
        sql_status=status,
        execution_time_ms=result.get("execution_time_ms"),
        result_row_count=int(result.get("total_count") or 0),
        result_payload=result_payload,
        error_message=None if success else str(result.get("error") or "SQL 执行失败"),
        started_time=_parse_datetime_value(result.get("started_time")),
        finished_time=_parse_datetime_value(result.get("finished_time")) or datetime.now(),
    )


async def get_query_execution_status(execution_id: str) -> str:
    """读取 SQL 执行明细当前状态。"""
    if not execution_id:
        return ""
    return await query_execution_dao.get_execution_status(execution_id)


def _normalize_remote_task_entry(remote_task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """归一化远端任务映射，缺关键字段时返回 None。"""
    normalized = {
        "member_datasource_id": str((remote_task or {}).get("member_datasource_id") or "").strip(),
        "member_name": str((remote_task or {}).get("member_name") or "").strip(),
        "remote_task_id": str((remote_task or {}).get("remote_task_id") or "").strip(),
        "source_type": str((remote_task or {}).get("source_type") or "").strip().lower() or "hive",
    }
    if (remote_task or {}).get("statement_index") is not None:
        normalized["statement_index"] = (remote_task or {}).get("statement_index")
    if not normalized["member_datasource_id"] or not normalized["remote_task_id"]:
        return None
    return normalized


def _remote_task_entry_exists(remote_tasks: list, entry: Dict[str, Any]) -> bool:
    return any(
        item.get("member_datasource_id") == entry["member_datasource_id"]
        and item.get("remote_task_id") == entry["remote_task_id"]
        and item.get("statement_index") == entry.get("statement_index")
        for item in remote_tasks
    )


async def append_query_task_remote_task(task_id: str, remote_task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """向异步任务中追加远端 HTTP 任务映射。"""
    normalized_remote_task = _normalize_remote_task_entry(remote_task)
    if normalized_remote_task is None:
        return None

    def append_remote_entry(task: ChatDataQueryTask) -> bool:
        remote_tasks = load_remote_tasks(task.remote_task_payload)
        if _remote_task_entry_exists(remote_tasks, normalized_remote_task):
            return False
        remote_tasks.append(normalized_remote_task)
        task.remote_task_payload = json.dumps(remote_tasks, ensure_ascii=False)
        return True

    task = await query_task_dao.update_task_locked(task_id, append_remote_entry)
    if task is None:
        return None
    return build_query_task_response_data(build_query_task_runtime_data(task))


def _coerce_member_result(item: Any) -> Dict[str, Any]:
    """gather(return_exceptions=True) 的异常项隔离为失败结果，保证聚合形状一致。

    execute_sql_for_member 内部已全捕获，这里兜底的是收尾阶段（关闭/落库）
    的意外异常：该成员按失败处理，绝不让单个成员异常取消其他成员的在途查询。
    """
    if isinstance(item, dict):
        return item
    logger.error("成员执行返回异常（已隔离为失败结果）: %s", item, exc_info=item if isinstance(item, BaseException) else None)
    return {"status": "failed", "error": str(item) or "SQL 执行失败", "columns": [], "data": [], "total_count": 0}


async def _notify_remote_task_created(
    payload: Dict[str, Any],
    *,
    execution_id: Optional[str],
    query_task_id: Optional[str],
    member_datasource_id: Any,
    member_name: str,
    statement_index: Optional[int],
) -> None:
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
                **({"statement_index": statement_index} if statement_index is not None else {}),
            },
        )


async def _notify_db_connection_created(payload: Dict[str, Any], execution_id: Optional[str]) -> None:
    if not execution_id:
        return
    db_connection_id = str((payload or {}).get("db_connection_id") or "").strip()
    if db_connection_id:
        await update_query_execution(execution_id, db_connection_id=db_connection_id)


async def _guard_running_execution(execution_id: str) -> bool:
    """执行前置检查：置 running 前后各复核一次取消状态，已取消返回 True。"""
    if await get_query_execution_status(execution_id) == "cancelled":
        return True
    await update_query_execution(execution_id, sql_status="running", started_time=datetime.now(), error_message="")
    return await get_query_execution_status(execution_id) == "cancelled"


def _cancelled_member_result(member_datasource_id: Any, member_name: str, effective_sql: str) -> Dict[str, Any]:
    return {
        "member_datasource_id": member_datasource_id,
        "member_name": member_name,
        "sql": effective_sql,
        "status": "cancelled",
        "columns": [],
        "data": [],
        "total_count": 0,
        "execution_time_ms": None,
        "remote_task_id": None,
        "db_connection_id": None,
        "error": "已人工取消",
    }


async def _fetch_sql_cached_result(cache_identity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """读取 SQL 结果缓存，读缓存失败时降级为未命中。"""
    try:
        return await query_cache.get_sql_result_cache(cache_identity)
    except Exception as cache_err:
        logger.warning("读取 SQL 结果缓存失败，降级查询数据源: cache_key=%s error=%s", cache_identity.get("cache_key"), cache_err, exc_info=True)
        return None


async def _resolve_cached_query_result(
    http_target: bool,
    cached_result: Dict[str, Any],
    cache_identity: Dict[str, Any],
    execution_target: Dict[str, Any],
    user_id: Optional[str],
    username: Optional[str],
    execute_query_and_update_cache,
    on_remote_task_created,
) -> Dict[str, Any]:
    """命中缓存时按执行目标类型解析结果；远端引用不可用时重新提交查询。"""
    if http_target:
        if query_cache.is_http_remote_ref_cache(cached_result):
            result = await fetch_http_remote_ref_cache_result(cached_result, user_id=user_id, username=username)
            if result.get("_cache_ref_unavailable"):
                logger.info(
                    "HTTP 远端缓存结果不可用，重新提交查询: cache_key=%s remote_task_id=%s status=%s error=%s",
                    cache_identity.get("cache_key"),
                    cached_result.get("remote_task_id"),
                    result.get("status"),
                    result.get("error"),
                )
                return await execute_query_and_update_cache()
            await on_remote_task_created({
                "remote_task_id": result.get("remote_task_id"),
                "source_type": cached_result.get("source_type") or execution_target.get("source_type"),
            })
            return result
        return await execute_query_and_update_cache()
    if query_cache.is_http_remote_ref_cache(cached_result):
        return await execute_query_and_update_cache()
    return cached_result


def _build_member_query_response(
    member_datasource_id: Any,
    member_name: str,
    effective_sql: str,
    result: Dict[str, Any],
    status: str,
) -> Dict[str, Any]:
    return {
        "member_datasource_id": member_datasource_id,
        "member_name": member_name,
        "sql": effective_sql,
        "status": status,
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


async def _execute_query_and_save_cache(
    execute_query,
    user_id: Optional[str],
    execution_target: Dict[str, Any],
    skip_query_cache: bool,
    http_target: bool,
    cache_identity: Dict[str, Any],
    username: Optional[str],
) -> Dict[str, Any]:
    query_result = await run_with_sql_user_datasource_limit(user_id, execution_target, execute_query)
    if not await query_cache.is_sql_result_cache_enabled(skip_query_cache):
        return query_result
    try:
        if http_target:
            await query_cache.save_http_remote_ref_sql_result_cache(cache_identity, query_result, operator_id=user_id, operator_name=username)
        else:
            await query_cache.save_sql_result_cache(cache_identity, query_result, operator_id=user_id, operator_name=username)
    except Exception as cache_err:
        logger.warning("保存 SQL 结果缓存失败，忽略缓存写入: cache_key=%s error=%s", cache_identity.get("cache_key"), cache_err, exc_info=True)
    return query_result


async def _finalize_member_query_result(
    execution_id: Optional[str],
    result: Dict[str, Any],
    http_target: bool,
    member_datasource_id: Any,
    local_started_at: float,
) -> str:
    """补充执行耗时并落库执行留痕，返回最终状态。"""
    status = query_result_status(result)
    if result.get("execution_time_ms") is None and status not in {"pending", "running"}:
        result["execution_time_ms"] = int((time.perf_counter() - local_started_at) * 1000)
    if execution_id:
        try:
            await update_query_execution_from_result(execution_id, result, store_result_payload=not http_target)
        except Exception:
            # 落库失败只损失该成员的执行留痕，不向 gather 冒泡引发级联取消
            logger.error("执行结果落库失败: execution_id=%s member_datasource_id=%s", execution_id, member_datasource_id, exc_info=True)
    return status


async def _notify_remote_task_created_for_member(
    payload: Dict[str, Any],
    *,
    execution_id: Optional[str],
    query_task_id: Optional[str],
    member_datasource_id: Any,
    member_name: Any,
    statement_index: Optional[int],
) -> None:
    """成员远程任务创建回调：透传执行上下文通知上游。"""
    await _notify_remote_task_created(
        payload,
        execution_id=execution_id,
        query_task_id=query_task_id,
        member_datasource_id=member_datasource_id,
        member_name=member_name,
        statement_index=statement_index,
    )


async def _notify_db_connection_created_for_member(
    payload: Dict[str, Any],
    *,
    execution_id: Optional[str],
) -> None:
    """成员数据库连接创建回调：透传执行上下文通知上游。"""
    await _notify_db_connection_created(payload, execution_id)


def _create_member_executor(
    execution_target: Dict[str, Any],
    execute_kwargs: Dict[str, Any],
    username: Optional[str],
    on_remote_task_created: Callable[[Dict[str, Any]], Awaitable[None]],
    on_db_connection_created: Callable[[Dict[str, Any]], Awaitable[None]],
) -> Any:
    """创建执行器并按签名绑定回调，返回执行器实例。"""
    executor = get_executor(execution_target)
    if "username" in inspect.signature(executor.execute).parameters:
        execute_kwargs["username"] = username
    if "remote_task_callback" in inspect.signature(executor.execute).parameters:
        execute_kwargs["remote_task_callback"] = on_remote_task_created
    if "db_connection_callback" in inspect.signature(executor.execute).parameters:
        execute_kwargs["db_connection_callback"] = on_db_connection_created
    return executor


async def execute_sql_for_member(
    execution_target: Dict[str, Any],
    sql: str,
    limit: int,
    timeout: Optional[int],
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    query_task_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    statement_index: Optional[int] = None,
    skip_query_cache: bool = False,
    datasource_id: Optional[str] = None,
    max_rows: Optional[int] = None,
) -> Dict[str, Any]:
    """执行单个成员数据源 SQL。"""
    member_datasource_id = execution_target.get("member_datasource_id")
    member_name = execution_target.get("member_name") or execution_target.get("group_name") or ""
    effective_sql = sql
    http_target = query_cache.is_http_execution_target(execution_target)
    local_started_at = time.perf_counter()

    on_remote_task_created = functools.partial(
        _notify_remote_task_created_for_member,
        execution_id=execution_id,
        query_task_id=query_task_id,
        member_datasource_id=member_datasource_id,
        member_name=member_name,
        statement_index=statement_index,
    )
    on_db_connection_created = functools.partial(
        _notify_db_connection_created_for_member,
        execution_id=execution_id,
    )

    execute_kwargs = {
        "sql": sql,
        "limit": limit,
        "timeout": timeout,
        "user_id": user_id,
        "datasource_id": datasource_id,
    }

    executor = None

    async def execute_query() -> Dict[str, Any]:
        nonlocal executor
        # 先赋值再执行：execute 抛异常时 finally 仍能关闭执行器资源
        executor = _create_member_executor(
            execution_target, execute_kwargs, username, on_remote_task_created, on_db_connection_created
        )
        return await executor.execute(**execute_kwargs)

    try:
        effective_sql = build_effective_sql(
            sql, limit, source_type=execution_target.get("source_type"), datasource_id=datasource_id, user_id=user_id,
            max_rows=max_rows,
        )
        cache_identity = query_cache.build_sql_result_cache_identity(execution_target, effective_sql, datasource_id=datasource_id)
        if execution_id and await _guard_running_execution(execution_id):
            return _cancelled_member_result(member_datasource_id, member_name, effective_sql)

        execute_query_and_update_cache = functools.partial(
            _execute_query_and_save_cache,
            execute_query,
            user_id,
            execution_target,
            skip_query_cache,
            http_target,
            cache_identity,
            username,
        )

        if await query_cache.is_sql_result_cache_enabled(skip_query_cache):
            cached_result = await _fetch_sql_cached_result(cache_identity)
            if cached_result:
                result = await _resolve_cached_query_result(
                    http_target,
                    cached_result,
                    cache_identity,
                    execution_target,
                    user_id,
                    username,
                    execute_query_and_update_cache,
                    on_remote_task_created,
                )
            else:
                result = await execute_query_and_update_cache()
        else:
            result = await run_with_sql_user_datasource_limit(user_id, execution_target, execute_query)
        if execution_id and await get_query_execution_status(execution_id) == "cancelled":
            result = {"status": "cancelled", "error": "已人工取消", "columns": [], "data": [], "total_count": 0}
    except Exception as e:
        logger.error("成员 SQL 执行异常: member_datasource_id=%s error=%s", member_datasource_id, e, exc_info=True)
        result = {"status": "failed", "error": str(e) or "SQL 执行失败", "columns": [], "data": [], "total_count": 0}
    finally:
        if executor is not None:
            try:
                await executor.close()
            except Exception:
                # 关闭失败不能让本成员的收尾异常冒泡取消其他成员的在途查询
                logger.warning("执行器资源关闭失败: member_datasource_id=%s", member_datasource_id, exc_info=True)
    status = await _finalize_member_query_result(execution_id, result, http_target, member_datasource_id, local_started_at)
    return _build_member_query_response(member_datasource_id, member_name, effective_sql, result, status)


async def build_execute_sql_result_data(
    request: ExecuteSqlRequest,
    query_task_id: Optional[str] = None,
    execution_map: Optional[Dict[str, str]] = None,
    username: Optional[str] = None,
    statement_index: Optional[int] = None,
) -> Dict[str, Any]:
    """执行 SQL 并返回统一结果数据。"""
    start_time = time.time()
    ds_manager = get_datasource_manager()
    datasource = await ds_manager.get_datasource_async(request.datasource_id)

    if not datasource:
        raise ValueError(f"数据源不存在: {request.datasource_id}")

    execution_targets = await ds_manager.get_authorized_execution_datasources_async(
        request.datasource_id,
        request.member_datasource_ids,
        user_id=request.user_id,
        username=username,
    )
    # 行数上限热值：未显式传 limit 的请求用其作默认值，同时作为 SQL LIMIT 校验上限
    row_limit_cap = await get_runtime_setting("CHATDATA_ROW_LIMIT")
    results = [
        _coerce_member_result(item)
        for item in await asyncio.gather(*[
            execute_sql_for_member(
                execution_target=target,
                sql=request.sql,
                limit=request.limit or row_limit_cap,
                timeout=request.timeout,
                user_id=request.user_id,
                username=username,
                query_task_id=query_task_id,
                execution_id=get_execution_id_by_member(execution_map or {}, target),
                statement_index=statement_index,
                skip_query_cache=request.skip_query_cache,
                datasource_id=request.datasource_id,
                max_rows=row_limit_cap,
            )
            for target in execution_targets
        ], return_exceptions=True)
    ]

    execution_time_ms = int((time.time() - start_time) * 1000)
    primary_result = next((item for item in results if query_result_succeeded(item)), results[0] if results else {})
    return build_query_result_payload(
        sql=primary_result.get("sql") or request.sql,
        member_results=results,
        fallback_error="所有执行对象均执行失败",
        fallback_execution_time_ms=execution_time_ms,
    )


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


def _normalize_remote_task_item(item: Any) -> Dict[str, Any] | None:
    """归一化单个远端任务项；缺 remote_task_id/member_datasource_id 返回 None。"""
    if not isinstance(item, dict):
        return None
    remote_task_id = str(item.get("remote_task_id") or "").strip()
    member_datasource_id = str(item.get("member_datasource_id") or "").strip()
    member_name = str(item.get("member_name") or "").strip()
    source_type = str(item.get("source_type") or "").strip().lower() or "hive"
    if not remote_task_id or not member_datasource_id:
        return None
    normalized_remote_task: Dict[str, Any] = {
        "member_datasource_id": member_datasource_id,
        "member_name": member_name,
        "remote_task_id": remote_task_id,
        "source_type": source_type,
    }
    if item.get("statement_index") is not None:
        normalized_remote_task["statement_index"] = item.get("statement_index")
    return normalized_remote_task


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
        normalized = _normalize_remote_task_item(item)
        if normalized is not None:
            result.append(normalized)
    return result


async def are_all_sql_execution_targets_http(request: ExecuteSqlRequest, operator_name: Optional[str] = None) -> bool:
    """判断 SQL 任务选中的执行对象是否全部为 HTTP 执行。"""
    ds_manager = get_datasource_manager()
    execution_targets = await ds_manager.get_authorized_execution_datasources_async(
        request.datasource_id,
        request.member_datasource_ids,
        user_id=request.user_id,
        username=operator_name,
    )
    return bool(execution_targets) and all(
        str((target or {}).get("query_executor_type") or "").strip().lower() == "http"
        for target in execution_targets
    )


async def get_http_execution_member_ids(request: ExecuteSqlRequest, operator_name: Optional[str] = None) -> Set[str]:
    """读取本次 SQL 任务中走 HTTP 的成员数据源 ID。"""
    ds_manager = get_datasource_manager()
    execution_targets = await ds_manager.get_authorized_execution_datasources_async(
        request.datasource_id,
        request.member_datasource_ids,
        user_id=request.user_id,
        username=operator_name,
    )
    return {
        str((target or {}).get("member_datasource_id") or "").strip()
        for target in execution_targets
        if query_cache.is_http_execution_target(target)
    }


async def build_persistent_query_task_result_for_request(request: ExecuteSqlRequest, result_data: Dict[str, Any], operator_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """按执行配置构建任务落库结果，HTTP 结果不落库。"""
    try:
        all_http_targets = await are_all_sql_execution_targets_http(request, operator_name=operator_name)
    except Exception:
        logger.warning("判定全 HTTP 执行对象失败，按混合模式降级处理", exc_info=True)
        all_http_targets = False
    if all_http_targets:
        return None
    try:
        http_member_ids = await get_http_execution_member_ids(request, operator_name=operator_name)
    except Exception:
        logger.warning("获取 HTTP 执行成员失败，按无过滤降级处理", exc_info=True)
        http_member_ids = set()
    return build_persistent_query_task_result_payload(result_data, http_member_ids)


def build_persistent_query_task_result_payload(result_data: Dict[str, Any], http_member_ids: Set[str]) -> Optional[Dict[str, Any]]:
    """构建可落库的任务结果，过滤 HTTP 返回数据。"""
    if not http_member_ids:
        return result_data
    local_results = _filter_local_member_results(result_data, http_member_ids)
    if not local_results:
        return None

    primary_result = next((item for item in local_results if query_result_succeeded(item)), local_results[0])
    return build_query_result_payload(
        sql=primary_result.get("sql") or (result_data or {}).get("sql") or "",
        member_results=local_results,
        fallback_error=(result_data or {}).get("error"),
        fallback_execution_time_ms=(result_data or {}).get("execution_time_ms"),
    )


def _filter_local_member_results(result_data: Dict[str, Any], http_member_ids: Set[str]) -> list:
    """从结果集中剔除 HTTP 成员的子结果。"""
    return [
        item for item in list((result_data or {}).get("results") or [])
        if str((item or {}).get("member_datasource_id") or "").strip() not in http_member_ids
    ]


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
    execution_models, task = await query_execution_dao.list_executions_with_task(task_id)
    executions = [item.to_dict() for item in execution_models]
    if executions:
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
    task = await query_task_dao.create_task(
        task_id=uuid.uuid4().hex,
        task_type=task_type,
        datasource_id=datasource_id,
        query_type=query_type or "sql",
        request_payload_json=json.dumps(request_payload, ensure_ascii=False),
        progress_text=progress_text,
        operator_user_id=operator_user_id,
        operator_name=operator_name,
    )
    return build_query_task_response_data(build_query_task_runtime_data(task))


async def create_query_task(payload: QueryTaskCreateRequest) -> Dict[str, Any]:
    """创建 SQL 异步查询任务。"""
    statements = list(payload.sql)
    task_type = "sql_batch" if len(statements) > 1 else "sql"
    request_payload = payload.model_dump()
    # 任务运行时始终只向单 SQL 执行器传递字符串，批次任务保留数组供顺序调度。
    request_payload["sql"] = statements if task_type == "sql_batch" else statements[0]
    return await create_task_record(
        task_type=task_type,
        datasource_id=payload.datasource_id,
        query_type=payload.query_type or "sql",
        request_payload=request_payload,
    )


async def prepare_sql_request_for_execution(request: ExecuteSqlRequest | QueryTaskCreateRequest, datasource: Optional[Dict[str, Any]] = None):
    """保留 SQL 原文；创建任务前快速校验，执行器边界按成员数据源复检。"""
    query_type = str(getattr(request, "query_type", "") or "").strip().lower()
    semantic_builder_config = getattr(request, "semantic_builder_config", None)
    semantic_config_id = str(getattr(request, "semantic_config_id", "") or "").strip()
    if query_type == "builder" and not semantic_builder_config:
        raise ValueError("可视化查询必须基于指标集，请先选择指标集")
    if not semantic_builder_config:
        _validate_sql_before_task_submit(request, datasource)
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
    prepared_request = request.model_copy(update={"sql": compiled["sql"], "builder_config": None})
    _validate_sql_before_task_submit(prepared_request, datasource)
    return prepared_request


async def prepare_sql_batch_request_for_execution(
    request: QueryTaskCreateRequest,
    datasource: Optional[Dict[str, Any]] = None,
) -> QueryTaskCreateRequest:
    """逐条校验批次 SQL，保持每个实际执行单元只能是一条安全 SQL。"""
    if str(request.query_type or "").strip().lower() == "builder":
        prepared = await prepare_sql_request_for_execution(
            request.model_copy(update={"sql": request.sql[0] if request.sql else ""}),
            datasource,
        )
        return prepared.model_copy(update={"sql": [prepared.sql]})
    statements = list(request.sql)
    if len(statements) == 1:
        prepared = await prepare_sql_request_for_execution(request.model_copy(update={"sql": statements[0]}), datasource)
        return prepared.model_copy(update={"sql": [prepared.sql]})

    for statement in statements:
        await prepare_sql_request_for_execution(request.model_copy(update={"sql": statement}), datasource)
    return request


async def create_task_for_execute_request(
    request: ExecuteSqlRequest,
    *,
    query_type: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> Dict[str, Any]:
    """为直接执行或智能体执行创建任务主记录。"""
    payload = InternalQueryTaskCreateRequest(
        datasource_id=request.datasource_id,
        member_datasource_ids=request.member_datasource_ids,
        user_id=request.user_id,
        operator_name=operator_name,
        conversation_id=request.conversation_id,
        query_text=request.query_text,
        semantic_config_id=request.semantic_config_id,
        sql=[request.sql],
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
    task = await query_task_dao.get_task(task_id)
    if not task:
        return None
    return build_query_task_runtime_data(task)


async def is_query_task_cancelled(task_id: str) -> bool:
    """判断异步查询任务是否已取消。"""
    task_data = await get_query_task(task_id)
    return str((task_data or {}).get("status") or "").strip().lower() == "cancelled"


def _apply_query_task_updates(
    task: ChatDataQueryTask,
    status: Optional[str],
    progress_text: Optional[str],
    result_payload: Any,
    error_message: Optional[str],
    started_time: Optional[datetime],
    finished_time: Optional[datetime],
) -> bool:
    """行锁读取后校验终态守卫：与取消操作互斥，防止快照读守卫失效导致终态被回写覆盖。"""
    if task.task_status == "cancelled" and status in {"running", "succeeded", "failed", "partial_success"}:
        return False
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
    return True


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
    updater = functools.partial(
        _apply_query_task_updates,
        status=status,
        progress_text=progress_text,
        result_payload=result_payload,
        error_message=error_message,
        started_time=started_time,
        finished_time=finished_time,
    )
    task = await query_task_dao.update_task_locked(task_id, updater)
    if task is None:
        return None
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
    persistent_result_payload = await build_persistent_query_task_result_for_request(execute_request, result_data, operator_name=operator_name)
    if not query_result_succeeded(result_data):
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


async def mark_sql_batch_remaining_cancelled(
    task_id: str,
    statement_results: List[Dict[str, Any]],
    execution_maps: Optional[Dict[int, Dict[str, str]]] = None,
    start_index: int = 0,
) -> None:
    """保留已完成 SQL 结果，仅把未完成语句标记为已停止。"""
    changed = False
    now = datetime.now()
    for item in statement_results[max(0, start_index):]:
        status = query_result_status(item)
        if status in {"pending", "running"}:
            item.update({
                "status": "cancelled",
                "error": item.get("error") or "已人工取消",
            })
            changed = True
            statement_index = int(item.get("statement_index") or 0)
            for execution_id in (execution_maps or {}).get(statement_index, {}).values():
                await update_query_execution(
                    execution_id,
                    sql_status="cancelled",
                    error_message="已人工取消",
                    finished_time=now,
                )
    if changed:
        await update_query_task(
            task_id,
            result_payload={"statement_results": statement_results},
        )


async def mark_sql_batch_precreation_failed(
    statement_results: List[Dict[str, Any]],
    execution_maps: Dict[int, Dict[str, str]],
    start_index: int,
    error_message: str,
) -> None:
    """终态化预创建失败的批次，避免父任务失败但明细长期等待。"""
    now = datetime.now()
    for item in statement_results[max(0, start_index):]:
        if query_result_status(item) in {"pending", "running"}:
            item.update({"status": "failed", "error": error_message, "results": []})
    for execution_map in execution_maps.values():
        for execution_id in execution_map.values():
            try:
                await update_query_execution(
                    execution_id,
                    sql_status="failed",
                    error_message=error_message,
                    finished_time=now,
                )
            except Exception as update_err:
                logger.error(
                    "SQL 批次预创建失败状态回写异常: execution_id=%s error=%s",
                    execution_id,
                    update_err,
                    exc_info=True,
                )


async def _precreate_sql_batch_executions(
    task_id: str,
    request_payload: Dict[str, Any],
    statements: List[str],
    operator_name: Optional[str],
    statement_results: List[Dict[str, Any]],
) -> Dict[int, Dict[str, str]] | None:
    """预创建全部语句的执行记录；失败时落库失败态并返回 None。"""
    execution_maps: Dict[int, Dict[str, str]] = {}
    for index, statement in enumerate(statements):
        execute_request = ExecuteSqlRequest(**{**request_payload, "sql": statement})
        try:
            execution_maps[index] = await create_query_executions_for_task(task_id, execute_request, operator_name=operator_name)
        except Exception as err:
            logger.error("SQL 批次执行记录创建异常: task_id=%s statement_index=%s error=%s", task_id, index, err, exc_info=True)
            error_message = str(err) or "SQL 执行记录创建失败"
            await mark_sql_batch_precreation_failed(statement_results, execution_maps, 0, error_message)
            await update_query_task(
                task_id,
                status="failed",
                progress_text="执行失败",
                result_payload={"statement_results": statement_results},
                error_message=error_message,
                finished_time=datetime.now(),
            )
            return None
    return execution_maps


async def _execute_batch_statement(
    request_payload: Dict[str, Any],
    statement: str,
    *,
    task_id: str,
    execution_map: Dict[str, str],
    operator_name: Optional[str],
    statement_index: int,
) -> Dict[str, Any]:
    """执行单条批次语句，返回 statement_results[index] 的增量更新。"""
    execute_request = ExecuteSqlRequest(**{**request_payload, "sql": statement})
    try:
        result_data = await build_execute_sql_result_data(
            execute_request,
            query_task_id=task_id,
            execution_map=execution_map,
            username=operator_name,
            statement_index=statement_index,
        )
        persistent_result = await build_persistent_query_task_result_for_request(
            execute_request,
            result_data,
            operator_name=operator_name,
        )
        statement_member_results = (persistent_result or {}).get("results") or []
        if not statement_member_results and query_result_status(result_data) == "failed":
            statement_member_results = result_data.get("results") or []
        return {
            "status": query_result_status(result_data),
            "error": result_data.get("error"),
            "results": statement_member_results,
        }
    except Exception as err:
        logger.error("SQL 批次语句执行异常: task_id=%s statement_index=%s error=%s", task_id, statement_index, err, exc_info=True)
        return {"status": "failed", "error": str(err) or "SQL 执行失败", "results": []}


def _resolve_sql_batch_final_status(
    statement_results: List[Dict[str, Any]],
) -> tuple[str, str, Optional[str]]:
    """按各语句结果聚合批次终态，返回（status, progress_text, error_message）。"""
    statement_statuses = [query_result_status(item) for item in statement_results]
    has_succeeded = QUERY_SUCCEEDED_STATUS in statement_statuses
    has_failures = any(status != QUERY_SUCCEEDED_STATUS for status in statement_statuses)
    all_cancelled = bool(statement_statuses) and all(status == "cancelled" for status in statement_statuses)
    if all_cancelled:
        return "cancelled", "已停止", "已人工取消"
    if has_succeeded and has_failures:
        only_cancelled_failures = all(status in {QUERY_SUCCEEDED_STATUS, "cancelled"} for status in statement_statuses)
        progress_text = "执行完成，部分 SQL 已停止" if only_cancelled_failures else "执行完成，部分 SQL 失败"
        error_message = "部分 SQL 已停止" if only_cancelled_failures else "部分 SQL 执行失败"
        return "partial_success", progress_text, error_message
    if has_succeeded:
        return "succeeded", "执行完成", None
    return "failed", "执行失败", "SQL 执行失败"


def _parse_batch_statements(request_payload: Dict[str, Any]) -> List[str]:
    return [str(item or "").strip() for item in request_payload.get("sql") or [] if str(item or "").strip()]


async def run_sql_batch_query_task(task_id: str, task: Dict[str, Any]) -> None:
    """顺序执行 SQL 批次；启动时预创建全部语句的历史执行记录。"""
    if await is_query_task_cancelled(task_id):
        return
    request_payload = json.loads(task.get("request_payload") or "{}")
    statements = _parse_batch_statements(request_payload)
    if not statements:
        await update_query_task(task_id, status="failed", progress_text="执行失败", error_message="SQL 批次为空", finished_time=datetime.now())
        return

    operator_name = str(request_payload.get("operator_name") or "").strip() or None
    statement_results: List[Dict[str, Any]] = [
        {"statement_index": index, "sql": statement, "status": "pending", "results": []}
        for index, statement in enumerate(statements)
    ]
    await update_query_task(
        task_id,
        status="running",
        progress_text=f"正在执行 SQL 1/{len(statements)}",
        result_payload={"statement_results": statement_results},
        started_time=datetime.now(),
        error_message=None,
    )

    execution_maps = await _precreate_sql_batch_executions(
        task_id, request_payload, statements, operator_name, statement_results,
    )
    if execution_maps is None:
        return

    for index, statement in enumerate(statements):
        if await is_query_task_cancelled(task_id):
            await mark_sql_batch_remaining_cancelled(task_id, statement_results, execution_maps, index)
            return
        statement_results[index]["status"] = "running"
        await update_query_task(
            task_id,
            progress_text=f"正在执行 SQL {index + 1}/{len(statements)}",
            result_payload={"statement_results": statement_results},
        )
        statement_results[index].update(await _execute_batch_statement(
            request_payload,
            statement,
            task_id=task_id,
            execution_map=execution_maps.get(index) or {},
            operator_name=operator_name,
            statement_index=index,
        ))

        await update_query_task(task_id, result_payload={"statement_results": statement_results})
        if await is_query_task_cancelled(task_id):
            await mark_sql_batch_remaining_cancelled(task_id, statement_results, execution_maps, index + 1)
            return

    final_status, final_progress_text, final_error_message = _resolve_sql_batch_final_status(statement_results)
    await update_query_task(
        task_id,
        status=final_status,
        progress_text=final_progress_text,
        result_payload={"statement_results": statement_results},
        error_message=final_error_message,
        finished_time=datetime.now(),
    )


async def _fail_agent_sql_task(task_info: Optional[Dict[str, Any]], error_message: str) -> None:
    """智能体 SQL 任务失败落库；尽力而为，不覆盖原始异常。"""
    if not task_info:
        return
    try:
        await update_query_task(
            task_info["task_id"],
            status="failed",
            progress_text="执行失败",
            error_message=error_message,
            finished_time=datetime.now(),
        )
    except Exception as update_err:
        logger.error("更新智能体 SQL 查询任务失败状态异常: %s", update_err, exc_info=True)


async def _create_agent_sql_task(
    execute_request: ExecuteSqlRequest,
    operator_name: Optional[str],
) -> tuple[Optional[Dict[str, Any]], Dict[str, str], Optional[str]]:
    """创建查询任务与执行记录，返回 (task_info, execution_map, error_message)。"""
    task_info: Optional[Dict[str, Any]] = None
    execution_map: Dict[str, str] = {}
    try:
        task_info = await create_task_for_execute_request(execute_request, query_type="agent", operator_name=operator_name)
        execution_map = await create_query_executions_for_task(task_info["task_id"], execute_request, operator_name=operator_name)
        await update_query_task(task_info["task_id"], status="running", progress_text="SQL 执行中", started_time=datetime.now(), error_message=None)
        return task_info, execution_map, None
    except Exception as task_err:
        logger.error("创建智能体 SQL 查询任务失败: %s", task_err, exc_info=True)
        return task_info, execution_map, str(task_err) or "创建智能体 SQL 查询任务失败"


async def _finalize_agent_sql_task(
    task_info: Dict[str, Any],
    final_result: Dict[str, Any],
    persistent_result_payload: Any,
) -> None:
    succeeded = query_result_succeeded(final_result)
    await update_query_task(
        task_info["task_id"],
        status="succeeded" if succeeded else "failed",
        progress_text="执行完成" if succeeded else "执行失败",
        result_payload=persistent_result_payload,
        error_message=None if succeeded else final_result.get("error") or "SQL 执行失败",
        finished_time=datetime.now(),
    )


async def _run_agent_sql_execution(
    execute_request: ExecuteSqlRequest,
    sql: str,
    *,
    datasource_id: Optional[str],
    member_datasource_ids: Optional[List[str]],
    user_id: Optional[str],
    operator_name: Optional[str],
    task_info: Optional[Dict[str, Any]],
    execution_map: Dict[str, str],
    ds_manager: Any,
) -> Dict[str, Any]:
    execution_targets = await ds_manager.get_authorized_execution_datasources_async(
        datasource_id,
        member_datasource_ids,
        user_id=user_id,
        username=operator_name,
    )
    row_limit_cap = await get_runtime_setting("CHATDATA_ROW_LIMIT")
    member_results = [
        _coerce_member_result(item)
        for item in await asyncio.gather(*[
            execute_sql_for_member(
                execution_target=target,
                sql=sql,
                limit=row_limit_cap,
                timeout=None,
                user_id=user_id,
                username=operator_name,
                execution_id=get_execution_id_by_member(execution_map, target) if execution_map else None,
                datasource_id=datasource_id,
                max_rows=row_limit_cap,
            )
            for target in execution_targets
        ], return_exceptions=True)
    ]
    primary_result = next((item for item in member_results if query_result_succeeded(item)), member_results[0] if member_results else {})
    final_result = build_query_result_payload(
        sql=primary_result.get("sql") or sql,
        member_results=member_results,
        fallback_error="查询失败",
    )
    if task_info:
        persistent_result_payload = final_result
        try:
            persistent_result_payload = await build_persistent_query_task_result_for_request(execute_request, final_result, operator_name=operator_name)
        except Exception as persist_err:
            logger.warning("构建智能体 SQL 查询任务落库结果失败，跳过结果落库: %s", persist_err, exc_info=True)
            persistent_result_payload = None
        await _finalize_agent_sql_task(task_info, final_result, persistent_result_payload)
    return final_result


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
        return build_query_result_payload(sql=sql, member_results=[], fallback_error="未指定数据源ID，无法执行查询")

    ds_manager = get_datasource_manager()
    datasource = await ds_manager.get_datasource_async(datasource_id)
    if not datasource:
        logger.error("数据源不存在: %s", datasource_id)
        return build_query_result_payload(sql=sql, member_results=[], fallback_error=f"数据源不存在: {datasource_id}")

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
    try:
        execute_request = await prepare_sql_request_for_execution(execute_request, datasource)
    except ValueError as e:
        logger.error("智能体 SQL 预校验失败: %s", e)
        return build_query_result_payload(sql=sql, member_results=[], fallback_error=str(e))

    task_info, execution_map, create_error = await _create_agent_sql_task(execute_request, operator_name)
    if create_error:
        await _fail_agent_sql_task(task_info, create_error)
        return build_query_result_payload(sql=sql, member_results=[], fallback_error=create_error)

    try:
        return await _run_agent_sql_execution(
            execute_request,
            sql,
            datasource_id=datasource_id,
            member_datasource_ids=member_datasource_ids,
            user_id=user_id,
            operator_name=operator_name,
            task_info=task_info,
            execution_map=execution_map,
            ds_manager=ds_manager,
        )
    except ValueError as e:
        logger.error("不支持的数据源类型: %s", e)
        await _fail_agent_sql_task(task_info, str(e) or "SQL 执行失败")
        return build_query_result_payload(sql=sql, member_results=[], fallback_error=str(e))
    except Exception as e:
        logger.error("执行 SQL 异常: %s", e, exc_info=True)
        await _fail_agent_sql_task(task_info, str(e) or "SQL 执行失败")
        return build_query_result_payload(sql=sql, member_results=[], fallback_error=f"执行失败: {str(e)}")
