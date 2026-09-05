"""Dashboard execution runtime and task helpers."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from utils.database import get_session
from utils.models import ChatDataQueryExecution
from utils.logger import get_logger

from .service import get_dashboard_service
from ..sql_run.result import build_query_result_payload, query_result_succeeded
from ..runtime_config import DEFAULT_ROW_LIMIT, read_positive_int_env
from ..sql_run.executor import resolve_query_timeout

logger = get_logger(__name__)
DASHBOARD_PANEL_MAX_CONCURRENCY = read_positive_int_env("CHAT_DATA_DASHBOARD_PANEL_MAX_CONCURRENCY", 8)
_dashboard_sql_semaphore: ContextVar[Optional[asyncio.Semaphore]] = ContextVar("dashboard_sql_semaphore", default=None)


@dataclass
class DashboardRuntime:
    get_datasource_manager: Callable[[], Any]
    execute_sql_for_member: Callable[..., Awaitable[Dict[str, Any]]]
    create_task_record: Callable[..., Awaitable[Dict[str, Any]]]
    update_query_task: Callable[..., Awaitable[Optional[Dict[str, Any]]]]
    is_query_task_cancelled: Callable[[str], Awaitable[bool]]
    schedule_query_task: Callable[[str], None]
    query_task_poll_interval_ms: int


_dashboard_runtime_value: Optional[DashboardRuntime] = None


def configure_dashboard_runtime(runtime: DashboardRuntime) -> None:
    """注入看板执行依赖，避免看板模块反向导入主路由。"""
    global _dashboard_runtime_value
    _dashboard_runtime_value = runtime


def dashboard_runtime() -> DashboardRuntime:
    if _dashboard_runtime_value is None:
        raise RuntimeError("Dashboard runtime is not configured")
    return _dashboard_runtime_value


async def run_dashboard_sql_operation(
    operation: Callable[[], Awaitable[Dict[str, Any]]],
    fallback_semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """执行看板 SQL，批量任务内共用同一个并发槽，避免面板和成员并发相乘。"""
    semaphore = _dashboard_sql_semaphore.get() or fallback_semaphore
    if semaphore is None:
        return await operation()
    async with semaphore:
        return await operation()


async def execute_dashboard_panel_sql(
    panel: Dict[str, Any],
    member_datasource_id: Optional[str] = None,
    user_id: Optional[str] = None,
    operator_name: Optional[str] = None,
    query_task_id: Optional[str] = None,
    execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """执行看板面板 SQL，仅针对一个成员数据源。"""
    datasource_id = panel.get("datasource_id")
    sql = panel.get("sql") or ""

    if not datasource_id:
        member_result = {
            "member_datasource_id": None,
            "member_name": None,
            "sql": sql,
            "status": "failed",
            "columns": [],
            "data": [],
            "total_count": 0,
            "execution_time_ms": None,
            "error": "面板缺少数据源配置",
        }
        return build_query_result_payload(sql=sql, member_results=[member_result], fallback_error="面板缺少数据源配置")

    ds_manager = dashboard_runtime().get_datasource_manager()
    effective_member_ids = resolve_panel_member_datasource_ids(panel, member_datasource_id)
    selected_member_ids = effective_member_ids or None
    execution_targets = await ds_manager.get_authorized_execution_datasources_async(
        datasource_id,
        selected_member_ids,
        user_id=user_id,
        username=operator_name,
    )
    if len(execution_targets) > 1:
        return await execute_dashboard_panel_sql_for_targets(
            panel=panel,
            execution_targets=execution_targets,
            user_id=user_id,
            operator_name=operator_name,
            query_task_id=query_task_id,
        )
    execution_target = execution_targets[0]
    async def execute_single() -> Dict[str, Any]:
        return await dashboard_runtime().execute_sql_for_member(
            execution_target=execution_target,
            sql=sql,
            limit=DEFAULT_ROW_LIMIT,
            timeout=resolve_query_timeout(execution_target.get("source_type")),
            user_id=user_id,
            username=operator_name,
            query_task_id=query_task_id,
            execution_id=execution_id,
            datasource_id=datasource_id,
        )

    result = await run_dashboard_sql_operation(execute_single)
    return build_query_result_payload(sql=result.get("sql") or sql, member_results=[result], fallback_error=result.get("error") or "查询失败")


async def execute_dashboard_panel_sql_with_target(
    panel: Dict[str, Any],
    member_datasource_id: Optional[str] = None,
    execution_target: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    operator_name: Optional[str] = None,
    query_task_id: Optional[str] = None,
    execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """执行看板面板 SQL，支持复用预解析执行对象。"""
    if execution_target is None or is_panel_multi_member_execution(panel, member_datasource_id):
        return await execute_dashboard_panel_sql(
            panel,
            member_datasource_id,
            user_id=user_id,
            operator_name=operator_name,
            query_task_id=query_task_id,
            execution_id=execution_id,
        )
    sql = panel.get("sql") or ""

    async def execute_single() -> Dict[str, Any]:
        return await dashboard_runtime().execute_sql_for_member(
            execution_target=execution_target,
            sql=sql,
            limit=DEFAULT_ROW_LIMIT,
            timeout=resolve_query_timeout(execution_target.get("source_type")),
            user_id=user_id,
            username=operator_name,
            query_task_id=query_task_id,
            execution_id=execution_id,
            datasource_id=panel.get("datasource_id"),
        )

    result = await run_dashboard_sql_operation(execute_single)
    return build_query_result_payload(sql=result.get("sql") or sql, member_results=[result], fallback_error=result.get("error") or "查询失败")


async def execute_dashboard_panel_sql_for_targets(
    *,
    panel: Dict[str, Any],
    execution_targets: List[Dict[str, Any]],
    user_id: Optional[str] = None,
    operator_name: Optional[str] = None,
    query_task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """同一面板 SQL 在多个执行对象上执行，并合并为图表友好的结果。"""
    sql = panel.get("sql") or ""
    semaphore = asyncio.Semaphore(DASHBOARD_PANEL_MAX_CONCURRENCY)

    async def execute_one(target: Dict[str, Any]) -> Dict[str, Any]:
        async def execute_member() -> Dict[str, Any]:
            execution_id = await create_dashboard_panel_execution(
                task_id=query_task_id or "",
                panel=panel,
                execution_target=target,
                user_id=user_id,
                operator_name=operator_name,
            )
            return await dashboard_runtime().execute_sql_for_member(
                execution_target=target,
                sql=sql,
                limit=DEFAULT_ROW_LIMIT,
                timeout=resolve_query_timeout(target.get("source_type")),
                user_id=user_id,
                username=operator_name,
                query_task_id=query_task_id,
                execution_id=execution_id,
                datasource_id=panel.get("datasource_id"),
            )
        return await run_dashboard_sql_operation(execute_member, semaphore)

    raw_results = await asyncio.gather(
        *(execute_one(target) for target in execution_targets),
        return_exceptions=True,
    )
    member_results = [
        normalize_dashboard_member_execution_result(target, result)
        for target, result in zip(execution_targets, raw_results)
    ]
    return build_multi_member_dashboard_result(sql, member_results)


async def resolve_dashboard_panel_execution_target(
    panel: Dict[str, Any],
    member_datasource_id: Optional[str],
    user_id: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """解析单成员面板执行对象，用于创建 execution 记录和复用执行配置。"""
    datasource_id = str((panel or {}).get("datasource_id") or "").strip()
    if not datasource_id:
        return None
    selected_member_ids = [member_datasource_id] if member_datasource_id else None
    execution_targets = await dashboard_runtime().get_datasource_manager().get_authorized_execution_datasources_async(
        datasource_id,
        selected_member_ids,
        user_id=user_id,
        username=operator_name,
    )
    return execution_targets[0] if execution_targets else None


async def build_dashboard_panel_execution_result(
    panel: Dict[str, Any],
    member_datasource_id: Optional[str] = None,
    execution_target: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    operator_name: Optional[str] = None,
    query_task_id: Optional[str] = None,
    execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构造看板面板执行返回。"""
    result = await execute_dashboard_panel_sql_with_target(
        panel,
        member_datasource_id,
        execution_target,
        user_id=user_id,
        operator_name=operator_name,
        query_task_id=query_task_id,
        execution_id=execution_id,
    )
    result_members = result.get("results") if isinstance(result.get("results"), list) else []
    primary_member = result_members[0] if result_members else {}
    return {
        "panel_id": panel["id"],
        "name": panel["name"],
        "chart_type": panel["chart_type"],
        "chart_config": panel["chart_config"],
        "member_datasource_id": primary_member.get("member_datasource_id"),
        "member_name": primary_member.get("member_name"),
        "result": result,
    }


async def execute_dashboard_panel_task_payload(
    panel_id: str,
    member_datasource_id: Optional[str] = None,
    *,
    panel: Optional[Dict[str, Any]] = None,
    execution_target: Optional[Dict[str, Any]] = None,
    query_task_id: Optional[str] = None,
    user_id: Optional[str] = None,
    operator_name: Optional[str] = None,
    validate_success: bool = True,
) -> Dict[str, Any]:
    """执行单个看板面板并返回 payload；需要时保留异常给路由映射错误码。"""
    resolved_panel = panel or await load_dashboard_panel_or_raise(panel_id, user_id=user_id, include_public=True)
    resolved_target = execution_target
    execution_id = None
    if not is_panel_multi_member_execution(resolved_panel, member_datasource_id):
        if resolved_target is None:
            resolved_target = await resolve_dashboard_panel_execution_target(
                resolved_panel,
                member_datasource_id,
                user_id=user_id,
                operator_name=operator_name,
            )
        execution_id = await create_dashboard_panel_execution(
            task_id=query_task_id or "",
            panel=resolved_panel,
            execution_target=resolved_target,
            user_id=user_id,
            operator_name=operator_name,
        )
    payload = await build_dashboard_panel_execution_result(
        panel=resolved_panel,
        member_datasource_id=member_datasource_id,
        execution_target=resolved_target,
        user_id=user_id,
        operator_name=operator_name,
        query_task_id=query_task_id,
        execution_id=execution_id,
    )
    if validate_success:
        raise_if_dashboard_panel_execution_failed(payload)
    return payload


def raise_if_dashboard_panel_execution_failed(payload: Dict[str, Any]) -> None:
    """校验面板执行结果，失败时抛错，统一单面板异步任务和批量执行口径。"""
    result = (payload or {}).get("result") or {}
    if query_result_succeeded(result):
        return
    error_message = (
        result.get("error")
        or payload.get("error")
        or f"面板执行失败: {(payload or {}).get('panel_id') or 'unknown'}"
    )
    raise RuntimeError(str(error_message))


def _panel_execution_fields(
    panel: Optional[Dict[str, Any]],
    execution_target: Optional[Dict[str, Any]],
    user_id: Optional[str],
    operator_name: Optional[str],
) -> Dict[str, str]:
    """归一化面板执行记录的全部字段（目标成员、执行器类型与操作人）。"""
    target = execution_target or {}
    return {
        "datasource_id": str((panel or {}).get("datasource_id") or "").strip(),
        "sql_text": str((panel or {}).get("sql") or "").strip(),
        "member_datasource_id": str(target.get("member_datasource_id") or "").strip(),
        "member_name": str(target.get("member_name") or target.get("group_name") or "").strip(),
        "executor_type": str(target.get("query_executor_type") or "direct").strip().lower() or "direct",
        "operator_user_id": str(user_id or "").strip(),
        "operator_name": str(operator_name or "").strip(),
    }


async def create_dashboard_panel_execution(
    *,
    task_id: str,
    panel: Dict[str, Any],
    execution_target: Optional[Dict[str, Any]],
    user_id: Optional[str],
    operator_name: Optional[str],
) -> Optional[str]:
    """为看板面板执行创建 SQL 执行记录。"""
    fields = _panel_execution_fields(panel, execution_target, user_id, operator_name)
    if not task_id or not fields["datasource_id"] or not fields["sql_text"]:
        return None
    execution_id = uuid.uuid4().hex
    now = datetime.now()
    async with get_session() as db:
        db.add(ChatDataQueryExecution(
            execution_id=execution_id,
            task_id=task_id,
            user_id=fields["operator_user_id"],
            datasource_id=fields["datasource_id"],
            member_datasource_id=fields["member_datasource_id"],
            member_name=fields["member_name"],
            executor_type=fields["executor_type"],
            sql_text=fields["sql_text"],
            sql_status="pending",
            delete_flag=0,
            create_time=now,
            update_time=now,
            create_by=fields["operator_user_id"],
            create_by_name=fields["operator_name"],
            update_by=fields["operator_user_id"],
            update_by_name=fields["operator_name"],
        ))
        await db.commit()
    return execution_id


async def load_dashboard_panel_or_raise(
    panel_id: str,
    dashboard_id: str = "",
    user_id: Optional[str] = None,
    include_public: bool = True,
) -> Dict[str, Any]:
    """读取面板，不存在则抛错。"""
    service = get_dashboard_service()
    panel = await service.get_panel(panel_id, dashboard_id=dashboard_id or None, user_id=user_id, include_public=include_public)
    if not panel:
        raise ValueError(f"面板不存在: {panel_id}")
    return panel


def build_dashboard_execution_target_cache_key(datasource_id: str, member_datasource_id: Optional[str]) -> tuple[str, str]:
    """构造看板执行对象缓存键。"""
    return str(datasource_id or "").strip(), str(member_datasource_id or "").strip()


def resolve_panel_member_datasource_id(panel: Optional[Dict[str, Any]], override_member_datasource_id: Optional[str] = None) -> str:
    """解析面板本次执行成员：本次显式指定优先，其次使用面板保存的成员。"""
    return str(override_member_datasource_id or (panel or {}).get("member_datasource_id") or "").strip()


def resolve_panel_member_datasource_ids(
    panel: Optional[Dict[str, Any]],
    override_member_datasource_id: Optional[str] = None,
) -> List[str]:
    """解析面板本次执行成员列表：面板多选配置优先，其次本次显式指定，最后兼容旧单选字段。"""
    chart_config = (panel or {}).get("chart_config") or {}
    multi_source = chart_config.get("multiSource") if isinstance(chart_config, dict) else None
    if isinstance(multi_source, dict) and multi_source.get("enabled"):
        member_ids = normalize_dashboard_member_ids(multi_source.get("memberDatasourceIds"))
        if member_ids:
            return member_ids
    override_member_id = str(override_member_datasource_id or "").strip()
    if override_member_id:
        return [override_member_id]
    member_datasource_id = resolve_panel_member_datasource_id(panel)
    return [member_datasource_id] if member_datasource_id else []


def normalize_dashboard_member_ids(value: Any) -> List[str]:
    """标准化看板成员数据源 ID 列表。"""
    result: List[str] = []
    for item in value or []:
        member_id = str(item or "").strip()
        if member_id and member_id not in result:
            result.append(member_id)
    return result


def is_panel_multi_member_execution(panel: Optional[Dict[str, Any]], override_member_datasource_id: Optional[str] = None) -> bool:
    """判断本次面板执行是否需要多个执行对象。"""
    return len(resolve_panel_member_datasource_ids(panel, override_member_datasource_id)) > 1


def normalize_dashboard_member_execution_result(target: Dict[str, Any], result: Any) -> Dict[str, Any]:
    """把单成员执行异常归一成结果项，保证多成员面板可以部分成功。"""
    if isinstance(result, Exception):
        return {
            "member_datasource_id": target.get("member_datasource_id"),
            "member_name": target.get("member_name") or target.get("group_name") or "",
            "status": "failed",
            "columns": [],
            "data": [],
            "total_count": 0,
            "execution_time_ms": None,
            "error": str(result) or "查询失败",
        }
    return result if isinstance(result, dict) else {
        "member_datasource_id": target.get("member_datasource_id"),
        "member_name": target.get("member_name") or target.get("group_name") or "",
        "status": "failed",
        "columns": [],
        "data": [],
        "total_count": 0,
        "execution_time_ms": None,
        "error": "查询结果格式错误",
    }


def build_multi_member_dashboard_result(sql: str, member_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """构造看板查询统一结果，真实数据只保留在 results[] 成员内。"""
    return build_query_result_payload(sql=sql, member_results=member_results, fallback_error="查询失败")


def build_effective_panel_member_datasource_ids(
    panel_ids: List[str],
    panel_map: Dict[str, Dict[str, Any]],
    requested_member_datasource_ids: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """构造本次看板执行的面板成员映射：请求覆盖优先，其次面板保存成员。"""
    result: Dict[str, str] = {}
    requested_member_datasource_ids = requested_member_datasource_ids or {}
    for panel_id in panel_ids:
        requested_member_id = str(requested_member_datasource_ids.get(panel_id) or "").strip()
        panel = panel_map.get(panel_id)
        member_datasource_id = resolve_panel_member_datasource_id(panel, requested_member_id)
        if member_datasource_id:
            result[panel_id] = member_datasource_id
    return result


def build_panel_execution_plan(
    panel_ids: List[str],
    panel_map: Dict[str, Dict[str, Any]],
    requested_member_datasource_ids: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """构造面板执行计划，供任务记录和历史排查展示完整执行对象信息。"""
    plan: Dict[str, Dict[str, Any]] = {}
    requested_member_datasource_ids = requested_member_datasource_ids or {}
    for panel_id in panel_ids:
        panel = panel_map.get(panel_id) or {}
        requested_member_id = str(requested_member_datasource_ids.get(panel_id) or "").strip()
        member_datasource_ids = resolve_panel_member_datasource_ids(panel, requested_member_id)
        if not member_datasource_ids:
            continue
        primary_member_datasource_id = resolve_panel_member_datasource_id(panel, requested_member_id)
        if not primary_member_datasource_id:
            primary_member_datasource_id = member_datasource_ids[0]
        plan[panel_id] = {
            "mode": "multi" if len(member_datasource_ids) > 1 else "single",
            "member_datasource_ids": member_datasource_ids,
            "primary_member_datasource_id": primary_member_datasource_id,
        }
    return plan


def build_dashboard_panel_sql_preview(sql: Any, max_length: int = 160) -> str:
    """构造面板 SQL 日志预览。"""
    preview = " ".join(str(sql or "").split())
    if len(preview) <= max_length:
        return preview
    return f"{preview[:max_length]}..."


def log_dashboard_panel_timing(
    panel: Optional[Dict[str, Any]],
    elapsed_ms: int,
    status: str,
    member_datasource_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """记录看板面板执行耗时。"""
    panel_data = panel or {}
    log_func = logger.debug if status == "succeeded" else logger.warning
    log_func(
        "看板面板执行耗时: panel_id=%s name=%s datasource_id=%s member_datasource_id=%s status=%s elapsed_ms=%s sql=%s error=%s",
        str(panel_data.get("id") or "").strip() or "unknown",
        str(panel_data.get("name") or "").strip() or "unknown",
        str(panel_data.get("datasource_id") or "").strip() or "",
        str(member_datasource_id or "").strip(),
        status,
        elapsed_ms,
        build_dashboard_panel_sql_preview(panel_data.get("sql")),
        str(error or "").strip(),
    )


def _build_dashboard_panel_map(dashboard: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """按面板 ID 为大屏面板建索引。"""
    return {
        str(panel.get("id") or "").strip(): panel
        for panel in (dashboard.get("panels") or [])
        if str(panel.get("id") or "").strip()
    }


async def _load_batch_execution_targets(
    panel_ids: List[str],
    panel_map: Dict[str, Dict[str, Any]],
    panel_member_datasource_ids: Dict[str, str],
    user_id: Optional[str],
    operator_name: Optional[str],
) -> Dict[tuple[str, str], Dict[str, Any]]:
    """为各面板预取授权执行对象，按 (datasource_id, member_id) 缓存去重。"""
    ds_manager = dashboard_runtime().get_datasource_manager()
    execution_target_map: Dict[tuple[str, str], Dict[str, Any]] = {}
    for panel_id in panel_ids:
        panel = panel_map[panel_id]
        datasource_id = str(panel.get("datasource_id") or "").strip()
        if not datasource_id:
            continue
        member_datasource_id = resolve_panel_member_datasource_id(panel, panel_member_datasource_ids.get(panel_id))
        cache_key = build_dashboard_execution_target_cache_key(datasource_id, member_datasource_id)
        if cache_key in execution_target_map:
            continue
        selected_member_ids = [member_datasource_id] if member_datasource_id else None
        execution_targets = await ds_manager.get_authorized_execution_datasources_async(
            datasource_id,
            selected_member_ids,
            user_id=user_id,
            username=operator_name,
        )
        execution_target_map[cache_key] = execution_targets[0]
    return execution_target_map


async def prepare_dashboard_batch_context(
    dashboard_id: str,
    panel_ids: List[str],
    panel_member_datasource_ids: Dict[str, str],
    preloaded_dashboard: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    operator_name: Optional[str] = None,
    include_public: bool = True,
) -> tuple[Dict[str, Dict[str, Any]], Dict[tuple[str, str], Dict[str, Any]]]:
    """预加载批量执行需要的面板与执行对象，减少每个卡片的重复查库。"""
    service = get_dashboard_service()
    dashboard = preloaded_dashboard
    if dashboard is None:
        dashboard = await service.get_dashboard(dashboard_id, user_id=user_id, include_public=include_public)
    if not dashboard:
        raise ValueError(f"大屏不存在: {dashboard_id}")

    panel_map = _build_dashboard_panel_map(dashboard)
    missing_panel_ids = [panel_id for panel_id in panel_ids if panel_id not in panel_map]
    if missing_panel_ids:
        raise ValueError(f"面板不存在: {missing_panel_ids[0]}")

    execution_target_map = await _load_batch_execution_targets(
        panel_ids, panel_map, panel_member_datasource_ids, user_id, operator_name,
    )
    return panel_map, execution_target_map


async def run_dashboard_panel_task(task_id: str, task: Dict[str, Any]) -> None:
    """后台执行单面板任务。"""
    await dashboard_runtime().update_query_task(
        task_id,
        status="running",
        progress_text="面板执行中",
        started_time=datetime.now(),
        error_message=None,
    )
    request_payload = json_loads_dict(task.get("request_payload"))
    dashboard_id = str(request_payload.get("dashboard_id") or "").strip()
    user_id = str(request_payload.get("user_id") or "").strip()
    operator_name = str(request_payload.get("operator_name") or "").strip()
    panel = await load_dashboard_panel_or_raise(
        str(request_payload.get("panel_id") or ""),
        dashboard_id=dashboard_id,
        user_id=user_id,
        include_public=True,
    )
    effective_member_datasource_id = resolve_panel_member_datasource_id(
        panel,
        request_payload.get("member_datasource_id"),
    )
    _, panel_entry = await execute_dashboard_panel_task_item(
        str(panel.get("id") or request_payload.get("panel_id") or ""),
        effective_member_datasource_id,
        panel=panel,
        user_id=user_id,
        operator_name=operator_name,
        query_task_id=task_id,
    )
    result_data = panel_entry.get("payload") if panel_entry.get("status") == "succeeded" else {
        "panel_id": panel.get("id"),
        "name": panel.get("name"),
        "chart_type": panel.get("chart_type"),
        "chart_config": panel.get("chart_config") or {},
        "result": {
            "status": "failed",
            "error": panel_entry.get("error") or "面板执行失败",
            "results": [],
        },
    }
    if not query_result_succeeded((result_data.get("result") or {})):
        await dashboard_runtime().update_query_task(
            task_id,
            status="failed",
            progress_text="执行失败",
            result_payload=result_data,
            error_message=(result_data.get("result") or {}).get("error") or "面板执行失败",
            finished_time=datetime.now(),
        )
        return
    await dashboard_runtime().update_query_task(
        task_id,
        status="succeeded",
        progress_text="执行完成",
        result_payload=result_data,
        error_message=None,
        finished_time=datetime.now(),
    )


def build_dashboard_batch_result(dashboard_id: str, panel_ids: List[str]) -> Dict[str, Any]:
    """构造看板批量任务初始结果。"""
    return {
        "dashboard_id": dashboard_id,
        "total": len(panel_ids),
        "completed": 0,
        "succeeded": 0,
        "failed": 0,
        "results": {
            panel_id: {"status": "pending"}
            for panel_id in panel_ids
        },
    }


async def execute_dashboard_panel_task_item(
    panel_id: str,
    member_datasource_id: Optional[str] = None,
    semaphore: Optional[asyncio.Semaphore] = None,
    panel: Optional[Dict[str, Any]] = None,
    execution_target: Optional[Dict[str, Any]] = None,
    query_task_id: Optional[str] = None,
    user_id: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> tuple[str, Dict[str, Any]]:
    """执行批量任务中的单个面板。"""
    context_token = None
    if semaphore is not None and _dashboard_sql_semaphore.get() is None:
        context_token = _dashboard_sql_semaphore.set(semaphore)
    started_at = time.perf_counter()
    try:
        resolved_panel = panel or await load_dashboard_panel_or_raise(panel_id, user_id=user_id, include_public=True)
        panel = resolved_panel
        payload = await execute_dashboard_panel_task_payload(
            panel_id,
            member_datasource_id=member_datasource_id,
            panel=resolved_panel,
            execution_target=execution_target,
            query_task_id=query_task_id,
            user_id=user_id,
            operator_name=operator_name,
        )
        log_dashboard_panel_timing(resolved_panel, int((time.perf_counter() - started_at) * 1000), "succeeded", member_datasource_id)
        return panel_id, {"status": "succeeded", "payload": payload}
    except Exception as e:
        log_dashboard_panel_timing(panel, int((time.perf_counter() - started_at) * 1000), "failed", member_datasource_id, str(e))
        logger.error("执行看板面板任务失败: panel_id=%s err=%s", panel_id, e)
        return panel_id, {"status": "failed", "error": str(e)}
    finally:
        if context_token is not None:
            _dashboard_sql_semaphore.reset(context_token)


def _parse_dashboard_batch_payload(task: Dict[str, Any]) -> tuple[str, str, str, List[str], Dict[str, str]]:
    """解析批量任务的请求载荷。"""
    request_payload = json_loads_dict(task.get("request_payload"))
    dashboard_id = str(request_payload.get("dashboard_id") or "")
    user_id = str(request_payload.get("user_id") or "").strip()
    operator_name = str(request_payload.get("operator_name") or "").strip()
    panel_ids = [str(panel_id).strip() for panel_id in (request_payload.get("panel_ids") or []) if str(panel_id).strip()]
    panel_member_datasource_ids = {
        str(panel_id): str(member_id).strip()
        for panel_id, member_id in (request_payload.get("panel_member_datasource_ids") or {}).items()
        if str(panel_id).strip() and str(member_id).strip()
    }
    return dashboard_id, user_id, operator_name, panel_ids, panel_member_datasource_ids


async def _load_dashboard_batch_panels(
    service,
    dashboard_id: str,
    user_id: str,
    panel_ids: List[str],
) -> tuple[Optional[Dict[str, Any]], List[str]]:
    """未指定面板时回读大屏全部面板；返回 (dashboard, panel_ids)。"""
    dashboard: Optional[Dict[str, Any]] = None
    if not panel_ids:
        dashboard = await service.get_dashboard(dashboard_id, user_id=user_id, include_public=True)
        if not dashboard:
            raise ValueError(f"大屏不存在: {dashboard_id}")
        panel_ids = [str(panel.get("id") or "").strip() for panel in (dashboard.get("panels") or []) if str(panel.get("id") or "").strip()]
    return dashboard, panel_ids


def _spawn_dashboard_panel_tasks(
    panel_ids: List[str],
    panel_map: Dict[str, Dict[str, Any]],
    execution_target_map: Dict[tuple[str, str], Dict[str, Any]],
    effective_panel_member_datasource_ids: Dict[str, str],
    task_id: str,
    user_id: str,
    operator_name: str,
) -> List[asyncio.Task]:
    """在面板并发信号量保护下创建全部面板执行任务。"""
    context_token = _dashboard_sql_semaphore.set(asyncio.Semaphore(DASHBOARD_PANEL_MAX_CONCURRENCY))
    try:
        return [
            asyncio.create_task(
                execute_dashboard_panel_task_item(
                    panel_id,
                    effective_panel_member_datasource_ids.get(panel_id),
                    panel=panel_map.get(panel_id),
                    execution_target=execution_target_map.get(
                        build_dashboard_execution_target_cache_key(
                            str((panel_map.get(panel_id) or {}).get("datasource_id") or ""),
                            effective_panel_member_datasource_ids.get(panel_id),
                        )
                    ),
                    query_task_id=task_id,
                    user_id=user_id,
                    operator_name=operator_name,
                )
            )
            for panel_id in panel_ids
        ]
    finally:
        _dashboard_sql_semaphore.reset(context_token)


async def _collect_dashboard_panel_results(task_id: str, tasks: List[asyncio.Task], batch_result: Dict[str, Any]) -> bool:
    """逐个收集面板结果并推进进度；返回 False 表示任务已取消。"""
    for completed_task in asyncio.as_completed(tasks):
        if await cancel_dashboard_batch_if_needed(task_id, tasks):
            return False
        panel_id, entry = await completed_task
        update_dashboard_batch_result(batch_result, panel_id, entry)
        await dashboard_runtime().update_query_task(
            task_id,
            progress_text=f"已完成 {batch_result['completed']}/{batch_result['total']}",
            result_payload=batch_result,
        )
        if await cancel_dashboard_batch_if_needed(task_id, tasks):
            return False
    return True


async def run_dashboard_batch_task(task_id: str, task: Dict[str, Any]) -> None:
    """后台执行看板批量任务。"""
    if await dashboard_runtime().is_query_task_cancelled(task_id):
        return
    await dashboard_runtime().update_query_task(
        task_id,
        status="running",
        progress_text="看板执行中",
        started_time=datetime.now(),
        error_message=None,
    )
    if await dashboard_runtime().is_query_task_cancelled(task_id):
        return
    dashboard_id, user_id, operator_name, panel_ids, panel_member_datasource_ids = _parse_dashboard_batch_payload(task)

    service = get_dashboard_service()
    dashboard, panel_ids = await _load_dashboard_batch_panels(service, dashboard_id, user_id, panel_ids)

    batch_result = build_dashboard_batch_result(dashboard_id, panel_ids)
    await dashboard_runtime().update_query_task(task_id, result_payload=batch_result, progress_text="看板执行中")
    if not panel_ids:
        await dashboard_runtime().update_query_task(
            task_id,
            status="succeeded",
            progress_text="无面板需要执行",
            result_payload=batch_result,
            error_message=None,
            finished_time=datetime.now(),
        )
        return

    panel_map, execution_target_map = await prepare_dashboard_batch_context(
        dashboard_id,
        panel_ids,
        panel_member_datasource_ids,
        preloaded_dashboard=dashboard,
        user_id=user_id,
        operator_name=operator_name,
        include_public=True,
    )
    effective_panel_member_datasource_ids = build_effective_panel_member_datasource_ids(
        panel_ids,
        panel_map,
        panel_member_datasource_ids,
    )
    tasks = _spawn_dashboard_panel_tasks(
        panel_ids,
        panel_map,
        execution_target_map,
        effective_panel_member_datasource_ids,
        task_id,
        user_id,
        operator_name,
    )
    if not await _collect_dashboard_panel_results(task_id, tasks, batch_result):
        return

    if await dashboard_runtime().is_query_task_cancelled(task_id):
        return
    await dashboard_runtime().update_query_task(
        task_id,
        **build_dashboard_batch_final_update(batch_result),
        result_payload=batch_result,
        finished_time=datetime.now(),
    )


async def execute_dashboard_stream_panel(
    panel_id: str,
    panel_map: Dict[str, Dict[str, Any]],
    execution_target_map: Dict[tuple[str, str], Dict[str, Any]],
    effective_panel_member_datasource_ids: Dict[str, str],
    task_id: str,
    user_id: Optional[str],
    operator_name: Optional[str],
    semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    """执行流式批量接口中的单个面板，返回前端 SSE payload。"""
    context_token = None
    if _dashboard_sql_semaphore.get() is None:
        context_token = _dashboard_sql_semaphore.set(semaphore)
    panel = panel_map.get(panel_id)
    started_at = time.perf_counter()
    try:
        if not panel:
            return {"panel_id": panel_id, "error": f"面板不存在: {panel_id}"}
        member_datasource_id = effective_panel_member_datasource_ids.get(panel_id)
        execution_target = execution_target_map.get(
            build_dashboard_execution_target_cache_key(
                str(panel.get("datasource_id") or ""),
                member_datasource_id,
            )
        )
        execution_id = None
        if not is_panel_multi_member_execution(panel, member_datasource_id):
            execution_id = await create_dashboard_panel_execution(
                task_id=task_id,
                panel=panel,
                execution_target=execution_target,
                user_id=user_id,
                operator_name=operator_name,
            )
        payload = await build_dashboard_panel_execution_result(
            panel=panel,
            member_datasource_id=member_datasource_id,
            execution_target=execution_target,
            user_id=user_id,
            operator_name=operator_name,
            query_task_id=task_id,
            execution_id=execution_id,
        )
        raise_if_dashboard_panel_execution_failed(payload)
        log_dashboard_panel_timing(panel, int((time.perf_counter() - started_at) * 1000), "succeeded", member_datasource_id)
        return payload
    except Exception as exc:
        member_datasource_id = effective_panel_member_datasource_ids.get(panel_id)
        log_dashboard_panel_timing(panel, int((time.perf_counter() - started_at) * 1000), "failed", member_datasource_id, str(exc))
        logger.error("执行面板 %s 失败: %s", panel_id, exc)
        return {"panel_id": panel_id, "error": str(exc)}
    finally:
        if context_token is not None:
            _dashboard_sql_semaphore.reset(context_token)


def create_dashboard_stream_tasks(
    panel_ids: List[str],
    panel_map: Dict[str, Dict[str, Any]],
    execution_target_map: Dict[tuple[str, str], Dict[str, Any]],
    effective_panel_member_datasource_ids: Dict[str, str],
    task_id: str,
    user_id: Optional[str],
    operator_name: Optional[str],
) -> Dict[asyncio.Task, str]:
    """创建流式批量面板执行任务。"""
    semaphore = asyncio.Semaphore(DASHBOARD_PANEL_MAX_CONCURRENCY)
    context_token = _dashboard_sql_semaphore.set(semaphore)
    try:
        return {
            asyncio.create_task(
                execute_dashboard_stream_panel(
                    panel_id,
                    panel_map,
                    execution_target_map,
                    effective_panel_member_datasource_ids,
                    task_id,
                    user_id,
                    operator_name,
                    semaphore,
                )
            ): panel_id
            for panel_id in panel_ids
        }
    finally:
        _dashboard_sql_semaphore.reset(context_token)


def build_sse_data(payload: Any) -> str:
    """构造 SSE data 行。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_sse_done() -> str:
    """构造 SSE 结束标记。"""
    return "data: [DONE]\n\n"


async def cancel_dashboard_stream_if_needed(
    task_id: str,
    task_map: Dict[asyncio.Task, str],
    pending_tasks: set[asyncio.Task],
) -> bool:
    """任务被取消时终止流式批量面板任务。"""
    if not await dashboard_runtime().is_query_task_cancelled(task_id):
        return False
    for task_item in pending_tasks:
        task_item.cancel()
    await asyncio.gather(*task_map.keys(), return_exceptions=True)
    return True


def update_dashboard_batch_result_from_stream_result(
    batch_result: Dict[str, Any],
    panel_id: str,
    result: Dict[str, Any],
) -> None:
    """按流式面板执行 payload 更新批量结果。"""
    success = query_result_succeeded((result.get("result") or {}))
    entry = {"status": "succeeded", "payload": result} if success else {
        "status": "failed",
        "error": result.get("error") or (result.get("result") or {}).get("error"),
    }
    update_dashboard_batch_result(batch_result, panel_id, entry)


async def handle_dashboard_stream_completed_task(
    completed_task: asyncio.Task,
    task_map: Dict[asyncio.Task, str],
    batch_result: Dict[str, Any],
) -> str:
    """处理已完成面板任务并返回 SSE 字符串。"""
    panel_id = task_map[completed_task]
    try:
        result = completed_task.result()
        update_dashboard_batch_result_from_stream_result(batch_result, panel_id, result)
        return build_sse_data(result)
    except Exception as exc:
        logger.error("面板 %s 执行异常: %s", panel_id, exc, exc_info=True)
        update_dashboard_batch_result(batch_result, panel_id, {"status": "failed", "error": str(exc)})
        return build_sse_data({"panel_id": panel_id, "error": str(exc)})


async def update_dashboard_stream_cancelled_task(task_id: str, batch_result: Dict[str, Any]) -> None:
    """客户端断开时更新流式批量任务状态。"""
    completed = int(batch_result.get("completed") or 0)
    total = int(batch_result.get("total") or 0)
    await dashboard_runtime().update_query_task(
        task_id,
        status="cancelled" if completed == 0 else "partial_success",
        progress_text="已取消" if completed == 0 else f"已取消，完成 {completed}/{total}",
        result_payload=batch_result,
        error_message="客户端取消看板执行",
        finished_time=datetime.now(),
    )


async def stream_dashboard_batch_execution(
    *,
    dashboard_id: str,
    panel_ids: List[str],
    panel_map: Dict[str, Dict[str, Any]],
    execution_target_map: Dict[tuple[str, str], Dict[str, Any]],
    effective_panel_member_datasource_ids: Dict[str, str],
    task_id: str,
    user_id: Optional[str],
    operator_name: Optional[str],
) -> AsyncIterator[str]:
    """流式执行看板批量面板，并按 SSE 输出每个面板结果。"""
    batch_result = build_dashboard_batch_result(dashboard_id, panel_ids)
    task_map = create_dashboard_stream_tasks(
        panel_ids,
        panel_map,
        execution_target_map,
        effective_panel_member_datasource_ids,
        task_id,
        user_id,
        operator_name,
    )
    pending_tasks = set(task_map.keys())
    try:
        while pending_tasks:
            done_tasks, pending_tasks = await asyncio.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)
            if await cancel_dashboard_stream_if_needed(task_id, task_map, pending_tasks):
                yield build_sse_data({"cancelled": True, "message": "已人工取消"})
                yield build_sse_done()
                return
            for completed_task in done_tasks:
                yield await handle_dashboard_stream_completed_task(completed_task, task_map, batch_result)
            if await cancel_dashboard_stream_if_needed(task_id, task_map, pending_tasks):
                yield build_sse_data({"cancelled": True, "message": "已人工取消"})
                yield build_sse_done()
                return
    except asyncio.CancelledError:
        for task_item in pending_tasks:
            task_item.cancel()
        await update_dashboard_stream_cancelled_task(task_id, batch_result)
        raise

    if await dashboard_runtime().is_query_task_cancelled(task_id):
        yield build_sse_done()
        return
    await dashboard_runtime().update_query_task(
        task_id,
        **build_dashboard_batch_final_update(batch_result),
        result_payload=batch_result,
        finished_time=datetime.now(),
    )
    yield build_sse_done()


def json_loads_dict(value: Any) -> Dict[str, Any]:
    """解析 JSON 对象字符串，空值返回空字典。"""
    parsed = json.loads(value or "{}")
    return parsed if isinstance(parsed, dict) else {}


async def cancel_dashboard_batch_if_needed(task_id: str, tasks: List[asyncio.Task]) -> bool:
    """任务被取消时终止未完成的批量面板任务。"""
    if not await dashboard_runtime().is_query_task_cancelled(task_id):
        return False
    for task_item in tasks:
        if not task_item.done():
            task_item.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    return True


def update_dashboard_batch_result(batch_result: Dict[str, Any], panel_id: str, entry: Dict[str, Any]) -> None:
    """写入单个面板执行结果并更新批量计数。"""
    batch_result["results"][panel_id] = entry
    batch_result["completed"] += 1
    if entry["status"] == "succeeded":
        batch_result["succeeded"] += 1
    else:
        batch_result["failed"] += 1


def build_dashboard_batch_final_update(batch_result: Dict[str, Any]) -> Dict[str, Any]:
    """构造批量任务最终状态字段。"""
    if batch_result["failed"] >= batch_result["total"] and batch_result["total"] > 0:
        return {
            "status": "failed",
            "progress_text": "执行失败",
            "error_message": "所有面板执行失败",
        }
    if batch_result["failed"] > 0:
        return {
            "status": "partial_success",
            "progress_text": f"部分完成，失败 {batch_result['failed']} 个面板",
            "error_message": f"{batch_result['failed']} 个面板执行失败",
        }
    return {
        "status": "succeeded",
        "progress_text": "执行完成",
        "error_message": None,
    }
