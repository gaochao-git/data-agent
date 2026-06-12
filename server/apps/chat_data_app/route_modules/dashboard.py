"""Dashboard routes for ChatData."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from utils.lang_util.database import get_session
from utils.lang_util.models import ChatDataQueryExecution
from utils.logger import get_logger
from ..agent.tools import execute_sql_direct as execute_sql_tool_direct
from ..chart_types import get_chart_type_label
from ..schema import (
    DashboardCreateRequest,
    DashboardFromSpecRequest,
    DashboardUpdateRequest,
    LayoutUpdateRequest,
    PanelBatchExecuteRequest,
    PanelCreateRequest,
    PanelExecuteRequest,
    PanelNameGenerateRequest,
    PanelUpdateRequest,
)
from ..services.dashboard_spec_service import create_dashboard_from_spec_payload
from ..services.dashboard_service import get_dashboard_service
from ..services.query_metadata import generate_query_metadata
from ..services.runtime_config import DEFAULT_ROW_LIMIT, read_positive_int_env
from ..services.sql_executor import resolve_query_timeout
from .common import _get_current_user, business_response

logger = get_logger(__name__)
router = APIRouter()
DASHBOARD_PANEL_MAX_CONCURRENCY = read_positive_int_env("CHAT_DATA_DASHBOARD_PANEL_MAX_CONCURRENCY", 8)


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


def _dashboard_runtime() -> DashboardRuntime:
    if _dashboard_runtime_value is None:
        raise RuntimeError("Dashboard runtime is not configured")
    return _dashboard_runtime_value


def _current_user(request: Request) -> Any:
    return _get_current_user(request)


def _with_dashboard_permission(dashboard: Optional[Dict[str, Any]], user_id: str) -> Optional[Dict[str, Any]]:
    """补充当前用户对看板的管理权限。"""
    if not dashboard:
        return dashboard
    item = dict(dashboard)
    item["can_manage"] = str(item.get("user_id") or "").strip() == str(user_id or "").strip()
    return item


def _with_dashboards_permission(dashboards: List[Dict[str, Any]], user_id: str) -> List[Dict[str, Any]]:
    """批量补充当前用户对看板的管理权限。"""
    return [_with_dashboard_permission(item, user_id) for item in dashboards]


async def _dashboard_permission_error(service: Any, dashboard_id: str):
    """区分看板不存在与非本人无管理权限。"""
    existing_dashboard = await service.get_dashboard(dashboard_id, user_id=None, include_public=False)
    if existing_dashboard:
        return business_response(msg="无权限操作该看板", code=40304, status="error")
    return business_response(msg=f"大屏不存在: {dashboard_id}", code=40404, status="error")


async def _execute_dashboard_panel_sql(
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
        result = await execute_sql_tool_direct(sql, None)
        return {
            "member_datasource_id": None,
            "member_name": None,
            "sql": sql,
            "success": bool(result.get("success")),
            "columns": result.get("columns") or [],
            "data": result.get("data") or [],
            "total_count": int(result.get("total_count") or 0),
            "execution_time_ms": result.get("execution_time_ms"),
            "error": result.get("error"),
        }

    ds_manager = _dashboard_runtime().get_datasource_manager()
    selected_member_ids = [member_datasource_id] if member_datasource_id else None
    execution_targets = await ds_manager.get_execution_datasources_async(datasource_id, selected_member_ids)
    execution_target = execution_targets[0]
    return await _dashboard_runtime().execute_sql_for_member(
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


async def _execute_dashboard_panel_sql_with_target(
    panel: Dict[str, Any],
    member_datasource_id: Optional[str] = None,
    execution_target: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    operator_name: Optional[str] = None,
    query_task_id: Optional[str] = None,
    execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """执行看板面板 SQL，支持复用预解析执行对象。"""
    if execution_target is None:
        return await _execute_dashboard_panel_sql(
            panel,
            member_datasource_id,
            user_id=user_id,
            operator_name=operator_name,
            query_task_id=query_task_id,
            execution_id=execution_id,
        )
    sql = panel.get("sql") or ""
    return await _dashboard_runtime().execute_sql_for_member(
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


async def _build_dashboard_panel_execution_result(
    panel: Dict[str, Any],
    member_datasource_id: Optional[str] = None,
    execution_target: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    operator_name: Optional[str] = None,
    query_task_id: Optional[str] = None,
    execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构造看板面板执行返回。"""
    result = await _execute_dashboard_panel_sql_with_target(
        panel,
        member_datasource_id,
        execution_target,
        user_id=user_id,
        operator_name=operator_name,
        query_task_id=query_task_id,
        execution_id=execution_id,
    )
    return {
        "panel_id": panel["id"],
        "name": panel["name"],
        "chart_type": panel["chart_type"],
        "chart_config": panel["chart_config"],
        "member_datasource_id": result.get("member_datasource_id"),
        "member_name": result.get("member_name"),
        "result": result,
    }


def _raise_if_dashboard_panel_execution_failed(payload: Dict[str, Any]) -> None:
    """校验面板执行结果，失败时抛错，统一单面板/批量/同步接口口径。"""
    result = (payload or {}).get("result") or {}
    if result.get("success"):
        return
    error_message = (
        result.get("error")
        or payload.get("error")
        or f"面板执行失败: {(payload or {}).get('panel_id') or 'unknown'}"
    )
    raise RuntimeError(str(error_message))


async def _create_dashboard_panel_execution(
    *,
    task_id: str,
    panel: Dict[str, Any],
    execution_target: Optional[Dict[str, Any]],
    user_id: Optional[str],
    operator_name: Optional[str],
) -> Optional[str]:
    """为看板面板执行创建 SQL 执行记录。"""
    datasource_id = str((panel or {}).get("datasource_id") or "").strip()
    sql_text = str((panel or {}).get("sql") or "").strip()
    if not task_id or not datasource_id or not sql_text:
        return None
    target = execution_target or {}
    member_datasource_id = str(target.get("member_datasource_id") or "").strip()
    member_name = str(target.get("member_name") or target.get("group_name") or "").strip()
    executor_type = str(target.get("query_executor_type") or "direct").strip().lower() or "direct"
    operator_user_id = str(user_id or "").strip()
    now = datetime.now()
    execution_id = uuid.uuid4().hex
    async with get_session() as db:
        db.add(ChatDataQueryExecution(
            execution_id=execution_id,
            task_id=task_id,
            user_id=str(user_id or "").strip(),
            datasource_id=datasource_id,
            member_datasource_id=member_datasource_id,
            member_name=member_name,
            executor_type=executor_type,
            sql_text=sql_text,
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
    return execution_id


async def _load_dashboard_panel_or_raise(
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


def _build_dashboard_execution_target_cache_key(datasource_id: str, member_datasource_id: Optional[str]) -> tuple[str, str]:
    """构造看板执行对象缓存键。"""
    return str(datasource_id or "").strip(), str(member_datasource_id or "").strip()


def _build_dashboard_panel_sql_preview(sql: Any, max_length: int = 160) -> str:
    """构造面板 SQL 日志预览。"""
    preview = " ".join(str(sql or "").split())
    if len(preview) <= max_length:
        return preview
    return f"{preview[:max_length]}..."


def _log_dashboard_panel_timing(
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
        _build_dashboard_panel_sql_preview(panel_data.get("sql")),
        str(error or "").strip(),
    )


async def _prepare_dashboard_batch_context(
    dashboard_id: str,
    panel_ids: List[str],
    panel_member_datasource_ids: Dict[str, str],
    preloaded_dashboard: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    include_public: bool = True,
) -> tuple[Dict[str, Dict[str, Any]], Dict[tuple[str, str], Dict[str, Any]]]:
    """预加载批量执行需要的面板与执行对象，减少每个卡片的重复查库。"""
    service = get_dashboard_service()
    dashboard = preloaded_dashboard
    if dashboard is None:
        dashboard = await service.get_dashboard(dashboard_id, user_id=user_id, include_public=include_public)
    if not dashboard:
        raise ValueError(f"大屏不存在: {dashboard_id}")

    panel_map = {
        str(panel.get("id") or "").strip(): panel
        for panel in (dashboard.get("panels") or [])
        if str(panel.get("id") or "").strip()
    }
    missing_panel_ids = [panel_id for panel_id in panel_ids if panel_id not in panel_map]
    if missing_panel_ids:
        raise ValueError(f"面板不存在: {missing_panel_ids[0]}")

    ds_manager = _dashboard_runtime().get_datasource_manager()
    execution_target_map: Dict[tuple[str, str], Dict[str, Any]] = {}
    for panel_id in panel_ids:
        panel = panel_map[panel_id]
        datasource_id = str(panel.get("datasource_id") or "").strip()
        if not datasource_id:
            continue
        member_datasource_id = panel_member_datasource_ids.get(panel_id)
        cache_key = _build_dashboard_execution_target_cache_key(datasource_id, member_datasource_id)
        if cache_key in execution_target_map:
            continue
        selected_member_ids = [member_datasource_id] if member_datasource_id else None
        execution_targets = await ds_manager.get_execution_datasources_async(datasource_id, selected_member_ids)
        execution_target_map[cache_key] = execution_targets[0]

    return panel_map, execution_target_map


async def _run_dashboard_panel_task(task_id: str, task: Dict[str, Any]) -> None:
    """后台执行单面板任务。"""
    await _dashboard_runtime().update_query_task(
        task_id,
        status="running",
        progress_text="面板执行中",
        started_time=datetime.now(),
        error_message=None,
    )
    request_payload = json.loads(task.get("request_payload") or "{}")
    dashboard_id = str(request_payload.get("dashboard_id") or "").strip()
    user_id = str(request_payload.get("user_id") or "").strip()
    panel = await _load_dashboard_panel_or_raise(
        str(request_payload.get("panel_id") or ""),
        dashboard_id=dashboard_id,
        user_id=user_id,
        include_public=True,
    )
    result_data = await _build_dashboard_panel_execution_result(
        panel=panel,
        member_datasource_id=request_payload.get("member_datasource_id"),
    )
    if not (result_data.get("result") or {}).get("success"):
        await _dashboard_runtime().update_query_task(
            task_id,
            status="failed",
            progress_text="执行失败",
            result_payload=result_data,
            error_message=(result_data.get("result") or {}).get("error") or "面板执行失败",
            finished_time=datetime.now(),
        )
        return
    await _dashboard_runtime().update_query_task(
        task_id,
        status="succeeded",
        progress_text="执行完成",
        result_payload=result_data,
        error_message=None,
        finished_time=datetime.now(),
    )


def _build_dashboard_batch_result(dashboard_id: str, panel_ids: List[str]) -> Dict[str, Any]:
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


async def _execute_dashboard_panel_task_item(
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
    if semaphore is not None:
        async with semaphore:
            return await _execute_dashboard_panel_task_item(
                panel_id,
                member_datasource_id,
                panel=panel,
                execution_target=execution_target,
                query_task_id=query_task_id,
                user_id=user_id,
                operator_name=operator_name,
            )
    started_at = time.perf_counter()
    try:
        panel = panel or await _load_dashboard_panel_or_raise(panel_id, user_id=user_id, include_public=True)
        execution_id = await _create_dashboard_panel_execution(
            task_id=query_task_id or "",
            panel=panel,
            execution_target=execution_target,
            user_id=user_id,
            operator_name=operator_name,
        )
        payload = await _build_dashboard_panel_execution_result(
            panel=panel,
            member_datasource_id=member_datasource_id,
            execution_target=execution_target,
            user_id=user_id,
            operator_name=operator_name,
            query_task_id=query_task_id,
            execution_id=execution_id,
        )
        _raise_if_dashboard_panel_execution_failed(payload)
        _log_dashboard_panel_timing(panel, int((time.perf_counter() - started_at) * 1000), "succeeded", member_datasource_id)
        return panel_id, {"status": "succeeded", "payload": payload}
    except Exception as e:
        _log_dashboard_panel_timing(panel, int((time.perf_counter() - started_at) * 1000), "failed", member_datasource_id, str(e))
        logger.error("执行看板面板任务失败: panel_id=%s err=%s", panel_id, e)
        return panel_id, {"status": "failed", "error": str(e)}


async def _run_dashboard_batch_task(task_id: str, task: Dict[str, Any]) -> None:
    """后台执行看板批量任务。"""
    if await _dashboard_runtime().is_query_task_cancelled(task_id):
        return
    await _dashboard_runtime().update_query_task(
        task_id,
        status="running",
        progress_text="看板执行中",
        started_time=datetime.now(),
        error_message=None,
    )
    if await _dashboard_runtime().is_query_task_cancelled(task_id):
        return
    request_payload = json.loads(task.get("request_payload") or "{}")
    dashboard_id = str(request_payload.get("dashboard_id") or "")
    user_id = str(request_payload.get("user_id") or "").strip()
    operator_name = str(request_payload.get("operator_name") or "").strip()
    panel_ids = [str(panel_id).strip() for panel_id in (request_payload.get("panel_ids") or []) if str(panel_id).strip()]
    panel_member_datasource_ids = {
        str(panel_id): str(member_id).strip()
        for panel_id, member_id in (request_payload.get("panel_member_datasource_ids") or {}).items()
        if str(panel_id).strip() and str(member_id).strip()
    }

    service = get_dashboard_service()
    dashboard: Optional[Dict[str, Any]] = None
    if not panel_ids:
        dashboard = await service.get_dashboard(dashboard_id, user_id=user_id, include_public=True)
        if not dashboard:
            raise ValueError(f"大屏不存在: {dashboard_id}")
        panel_ids = [str(panel.get("id") or "").strip() for panel in (dashboard.get("panels") or []) if str(panel.get("id") or "").strip()]

    batch_result = _build_dashboard_batch_result(dashboard_id, panel_ids)
    await _dashboard_runtime().update_query_task(task_id, result_payload=batch_result, progress_text="看板执行中")
    if not panel_ids:
        await _dashboard_runtime().update_query_task(
            task_id,
            status="succeeded",
            progress_text="无面板需要执行",
            result_payload=batch_result,
            error_message=None,
            finished_time=datetime.now(),
        )
        return

    panel_map, execution_target_map = await _prepare_dashboard_batch_context(
        dashboard_id,
        panel_ids,
        panel_member_datasource_ids,
        preloaded_dashboard=dashboard,
        user_id=user_id,
        include_public=True,
    )
    semaphore = asyncio.Semaphore(DASHBOARD_PANEL_MAX_CONCURRENCY)
    tasks = [
        asyncio.create_task(
            _execute_dashboard_panel_task_item(
                panel_id,
                panel_member_datasource_ids.get(panel_id),
                semaphore,
                panel=panel_map.get(panel_id),
                execution_target=execution_target_map.get(
                    _build_dashboard_execution_target_cache_key(
                        str((panel_map.get(panel_id) or {}).get("datasource_id") or ""),
                        panel_member_datasource_ids.get(panel_id),
                    )
                ),
                query_task_id=task_id,
                user_id=user_id,
                operator_name=operator_name,
            )
        )
        for panel_id in panel_ids
    ]
    for completed_task in asyncio.as_completed(tasks):
        if await _dashboard_runtime().is_query_task_cancelled(task_id):
            for task_item in tasks:
                if not task_item.done():
                    task_item.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return
        panel_id, entry = await completed_task
        batch_result["results"][panel_id] = entry
        batch_result["completed"] += 1
        if entry["status"] == "succeeded":
            batch_result["succeeded"] += 1
        else:
            batch_result["failed"] += 1
        await _dashboard_runtime().update_query_task(
            task_id,
            progress_text=f"已完成 {batch_result['completed']}/{batch_result['total']}",
            result_payload=batch_result,
        )
        if await _dashboard_runtime().is_query_task_cancelled(task_id):
            for task_item in tasks:
                if not task_item.done():
                    task_item.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return

    final_status = "succeeded"
    final_progress_text = "执行完成"
    final_error_message = None
    if batch_result["failed"] >= batch_result["total"] and batch_result["total"] > 0:
        final_status = "failed"
        final_progress_text = "执行失败"
        final_error_message = "所有面板执行失败"
    elif batch_result["failed"] > 0:
        final_status = "partial_success"
        final_progress_text = f"部分完成，失败 {batch_result['failed']} 个面板"
        final_error_message = f"{batch_result['failed']} 个面板执行失败"

    if await _dashboard_runtime().is_query_task_cancelled(task_id):
        return
    await _dashboard_runtime().update_query_task(
        task_id,
        status=final_status,
        progress_text=final_progress_text,
        result_payload=batch_result,
        error_message=final_error_message,
        finished_time=datetime.now(),
    )


# ============ 分析大屏接口 ============

def _truncate_text(value: str, max_length: int) -> str:
    """裁剪文本长度并去除首尾空白。"""
    text = (value or "").strip()
    if not text:
        return ""
    return text[:max_length].strip()


def _chart_type_label(chart_type: str) -> str:
    """将图表类型转换为中文标签。"""
    return get_chart_type_label(chart_type)


def _build_panel_name_fallback(chart_type: str, max_length: int = 24) -> str:
    """构建兜底面板标题。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fallback = f"{_chart_type_label(chart_type)}_{timestamp}"
    return _truncate_text(fallback, max_length)


async def _resolve_panel_name(sql: str, chart_type: str, provided_name: Optional[str], max_length: int = 24) -> str:
    """生成面板标题：优先显式名称，其次LLM，最后本地兜底。"""
    explicit_name = _truncate_text(provided_name or "", max_length)
    if explicit_name:
        return explicit_name
    try:
        metadata = await generate_query_metadata(sql)
        llm_title = _truncate_text(str(metadata.get("title", "")), max_length)
        if llm_title and llm_title != "SQL查询":
            return llm_title
    except Exception as e:
        logger.warning("保存面板时生成标题失败: %s", e)
    return _build_panel_name_fallback(chart_type, max_length)


@router.get("/dashboards")
async def list_dashboards(request: Request, scope: str = "mine"):
    """
    获取大屏列表

    Args:
        user_id: 用户ID（可选，默认 default）

    Returns:
        大屏列表
    """
    try:
        current_user = _current_user(request)
        user_id = str(current_user.user_id)
        normalized_scope = str(scope or "mine").strip().lower()
        if normalized_scope not in {"mine", "public"}:
            normalized_scope = "mine"
        service = get_dashboard_service()
        dashboards = await service.list_dashboards(user_id=user_id, scope=normalized_scope)
        dashboards = _with_dashboards_permission(dashboards, user_id)

        return business_response(
            data={"dashboards": dashboards, "total": len(dashboards)},
            msg="获取成功"
        )

    except Exception as e:
        logger.error(f"获取大屏列表失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=50020,
            status="error"
        )


@router.post("/dashboards")
async def create_dashboard(request: DashboardCreateRequest, http_request: Request):
    """
    创建新的分析大屏

    Args:
        request: 创建请求
        user_id: 用户ID

    Returns:
        创建的大屏信息
    """
    try:
        current_user = _current_user(http_request)
        user_id = str(current_user.user_id)
        operator_name = str(current_user.username or "")
        service = get_dashboard_service()
        dashboard = await service.create_dashboard(
            name=request.name,
            user_id=user_id,
            description=request.description,
            datasource_id=request.datasource_id,
            theme=request.theme,
            operator_name=operator_name,
        )
        dashboard = _with_dashboard_permission(dashboard, user_id)

        return business_response(
            data=dashboard,
            msg="创建成功"
        )

    except Exception as e:
        logger.error(f"创建大屏失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"创建失败: {str(e)}",
            code=50021,
            status="error"
        )


@router.post("/dashboards/from-spec")
async def create_dashboard_from_spec(request: DashboardFromSpecRequest, http_request: Request):
    """根据 AI 生成的看板规格创建大屏和面板。"""
    try:
        current_user = _current_user(http_request)
        datasource_id = str(request.datasource_id or "").strip()
        if not datasource_id:
            return business_response(msg="缺少数据源ID，无法创建看板", code=40010, status="error")
        spec = request.dashboard_spec or {}
        if spec.get("need_clarify"):
            return business_response(msg="当前方案仍需补充确认，暂不能写入看板", code=40013, status="error")
        cards = spec.get("cards") if isinstance(spec.get("cards"), list) else []
        if not cards:
            return business_response(msg="看板规格缺少卡片，无法创建看板", code=40011, status="error")
        semantic_config_id = str(request.semantic_config_id or spec.get("semantic_config_id") or "").strip()
        if not semantic_config_id:
            return business_response(msg="看板规格缺少指标集ID，无法创建看板", code=40014, status="error")

        result = await create_dashboard_from_spec_payload(
            dashboard_spec=spec,
            datasource_id=datasource_id,
            semantic_config_id=semantic_config_id,
            conversation_id=request.conversation_id,
            user_id=str(current_user.user_id),
            username=str(current_user.username),
        )
        if not result.get("panels"):
            return business_response(msg="看板创建失败：没有可创建的有效卡片", data={"warnings": result.get("warnings") or []}, code=40012, status="error")
        return business_response(data=result, msg="看板创建成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("根据 AI 看板规格创建大屏失败: %s", e, exc_info=True)
        return business_response(msg=f"创建失败: {str(e)}", code=50035, status="error")


@router.post("/dashboards/{dashboard_id}/publish")
async def publish_dashboard(dashboard_id: str, request: Request):
    """发布个人看板为公共看板。"""
    try:
        current_user = _current_user(request)
        user_id = str(current_user.user_id)
        operator_name = str(current_user.username or "")
        service = get_dashboard_service()
        dashboard = await service.set_dashboard_public(dashboard_id, user_id=user_id, is_public=True, operator_name=operator_name)
        if not dashboard:
            return await _dashboard_permission_error(service, dashboard_id)
        dashboard = _with_dashboard_permission(dashboard, user_id)
        return business_response(data=dashboard, msg="发布成功")
    except Exception as e:
        logger.error("发布大屏失败: %s", e, exc_info=True)
        return business_response(msg=f"发布失败: {str(e)}", code=50036, status="error")


@router.post("/dashboards/{dashboard_id}/unpublish")
async def unpublish_dashboard(dashboard_id: str, request: Request):
    """取消公共看板发布。"""
    try:
        current_user = _current_user(request)
        user_id = str(current_user.user_id)
        operator_name = str(current_user.username or "")
        service = get_dashboard_service()
        dashboard = await service.set_dashboard_public(dashboard_id, user_id=user_id, is_public=False, operator_name=operator_name)
        if not dashboard:
            return await _dashboard_permission_error(service, dashboard_id)
        dashboard = _with_dashboard_permission(dashboard, user_id)
        return business_response(data=dashboard, msg="已取消发布")
    except Exception as e:
        logger.error("取消发布大屏失败: %s", e, exc_info=True)
        return business_response(msg=f"取消发布失败: {str(e)}", code=50037, status="error")


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(dashboard_id: str, request: Request):
    """
    获取大屏详情（含所有面板）

    Args:
        dashboard_id: 大屏ID

    Returns:
        大屏详情及面板列表
    """
    try:
        current_user = _current_user(request)
        user_id = str(current_user.user_id)
        service = get_dashboard_service()
        dashboard = await service.get_dashboard(dashboard_id, user_id=user_id, include_public=True)

        if not dashboard:
            return business_response(
                msg=f"大屏不存在: {dashboard_id}",
                code=40404,
                status="error"
            )

        return business_response(data=_with_dashboard_permission(dashboard, user_id), msg="获取成功")

    except Exception as e:
        logger.error(f"获取大屏详情失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=50022,
            status="error"
        )


@router.put("/dashboards/{dashboard_id}")
async def update_dashboard(dashboard_id: str, request: DashboardUpdateRequest, http_request: Request):
    """
    更新大屏信息

    Args:
        dashboard_id: 大屏ID
        request: 更新请求

    Returns:
        更新后的大屏信息
    """
    try:
        current_user = _current_user(http_request)
        user_id = str(current_user.user_id)
        operator_name = str(current_user.username or "")
        service = get_dashboard_service()
        dashboard = await service.update_dashboard(
            dashboard_id=dashboard_id,
            name=request.name,
            description=request.description,
            theme=request.theme,
            background_id=request.background_id,
            user_id=user_id,
            operator_name=operator_name,
        )

        if not dashboard:
            return await _dashboard_permission_error(service, dashboard_id)

        return business_response(data=_with_dashboard_permission(dashboard, user_id), msg="更新成功")

    except Exception as e:
        logger.error(f"更新大屏失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"更新失败: {str(e)}",
            code=50023,
            status="error"
        )


@router.delete("/dashboards/{dashboard_id}")
async def delete_dashboard(dashboard_id: str, request: Request):
    """
    删除大屏（同时删除所有面板）

    Args:
        dashboard_id: 大屏ID

    Returns:
        删除结果
    """
    try:
        current_user = _current_user(request)
        user_id = str(current_user.user_id)
        operator_name = str(current_user.username or "")
        service = get_dashboard_service()
        success = await service.delete_dashboard(dashboard_id, user_id=user_id, operator_name=operator_name)

        if not success:
            return await _dashboard_permission_error(service, dashboard_id)

        return business_response(
            data={"dashboard_id": dashboard_id},
            msg="删除成功"
        )

    except Exception as e:
        logger.error(f"删除大屏失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"删除失败: {str(e)}",
            code=50024,
            status="error"
        )


# ============ 面板接口 ============

@router.post("/dashboards/{dashboard_id}/panels")
async def add_panel(dashboard_id: str, request: PanelCreateRequest, http_request: Request):
    """
    添加面板到大屏

    Args:
        dashboard_id: 大屏ID
        request: 面板创建请求

    Returns:
        创建的面板信息
    """
    try:
        current_user = _current_user(http_request)
        operator_name = str(current_user.username or "")
        service = get_dashboard_service()
        panel_name = _truncate_text(request.name or "", 24) or _build_panel_name_fallback(request.chart_type, 24)
        panel = await service.add_panel(
            dashboard_id=dashboard_id,
            name=panel_name,
            sql=request.sql,
            chart_type=request.chart_type,
            chart_config=request.chart_config,
            layout_x=request.layout_x,
            layout_y=request.layout_y,
            layout_w=request.layout_w,
            layout_h=request.layout_h,
            refresh_interval=request.refresh_interval,
            source_thread_id=request.source_thread_id,
            datasource_id=request.datasource_id,
            user_id=str(current_user.user_id),
            operator_name=operator_name,
        )

        if not panel:
            return business_response(
                msg=f"大屏不存在: {dashboard_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data=panel,
            msg="添加成功"
        )

    except Exception as e:
        logger.error(f"添加面板失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"添加失败: {str(e)}",
            code=50025,
            status="error"
        )


@router.post("/panel-name-generate")
async def generate_panel_name(request: PanelNameGenerateRequest):
    """生成面板标题。"""
    try:
        panel_name = await _resolve_panel_name(
            sql=request.sql,
            chart_type=request.chart_type,
            provided_name=request.name,
            max_length=24
        )
        return business_response(
            data={"name": panel_name},
            msg="生成成功"
        )
    except Exception as e:
        logger.error(f"生成面板标题失败: {str(e)}", exc_info=True)
        return business_response(
            msg="生成面板标题失败，请稍后重试",
            code=50031,
            status="error"
        )


@router.put("/dashboards/{dashboard_id}/panels/{panel_id}")
async def update_panel(dashboard_id: str, panel_id: str, request: PanelUpdateRequest, http_request: Request):
    """
    更新面板

    Args:
        dashboard_id: 大屏ID
        panel_id: 面板ID
        request: 更新请求

    Returns:
        更新后的面板信息
    """
    try:
        current_user = _current_user(http_request)
        operator_name = str(current_user.username or "")
        service = get_dashboard_service()
        panel = await service.update_panel(
            dashboard_id=dashboard_id,
            panel_id=panel_id,
            name=request.name,
            sql=request.sql,
            chart_type=request.chart_type,
            chart_config=request.chart_config,
            layout_x=request.layout_x,
            layout_y=request.layout_y,
            layout_w=request.layout_w,
            layout_h=request.layout_h,
            refresh_interval=request.refresh_interval,
            user_id=str(current_user.user_id),
            operator_name=operator_name,
        )

        if not panel:
            return business_response(
                msg=f"面板不存在: {panel_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data=panel,
            msg="更新成功"
        )

    except Exception as e:
        logger.error(f"更新面板失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"更新失败: {str(e)}",
            code=50026,
            status="error"
        )


@router.delete("/dashboards/{dashboard_id}/panels/{panel_id}")
async def delete_panel(dashboard_id: str, panel_id: str, request: Request):
    """
    删除面板

    Args:
        dashboard_id: 大屏ID
        panel_id: 面板ID

    Returns:
        删除结果
    """
    try:
        current_user = _current_user(request)
        operator_name = str(current_user.username or "")
        service = get_dashboard_service()
        success = await service.delete_panel(dashboard_id, panel_id, user_id=str(current_user.user_id), operator_name=operator_name)

        if not success:
            return business_response(
                msg=f"面板不存在: {panel_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data={"panel_id": panel_id},
            msg="删除成功"
        )

    except Exception as e:
        logger.error(f"删除面板失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"删除失败: {str(e)}",
            code=50027,
            status="error"
        )


@router.put("/dashboards/{dashboard_id}/layout")
async def update_layout(dashboard_id: str, request: LayoutUpdateRequest, http_request: Request):
    """
    批量更新面板布局

    用于拖拽调整面板位置和大小后保存

    Args:
        dashboard_id: 大屏ID
        request: 布局更新请求

    Returns:
        更新结果
    """
    try:
        current_user = _current_user(http_request)
        operator_name = str(current_user.username or "")
        service = get_dashboard_service()
        updated_count = await service.update_panels_layout(
            dashboard_id=dashboard_id,
            layouts=request.layouts,
            user_id=str(current_user.user_id),
            operator_name=operator_name,
        )
        if updated_count is None:
            return business_response(
                msg=f"大屏不存在: {dashboard_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data={"dashboard_id": dashboard_id, "updated": updated_count},
            msg="布局更新成功"
        )

    except Exception as e:
        logger.error(f"更新布局失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"更新失败: {str(e)}",
            code=50028,
            status="error"
        )


@router.post("/dashboards/{dashboard_id}/panels/{panel_id}/execute")
async def execute_panel(
    dashboard_id: str,
    panel_id: str,
    http_request: Request,
    request: Optional[PanelExecuteRequest] = None,
):
    """
    执行面板 SQL 并返回数据

    用于大屏查看时刷新单个面板数据

    Args:
        dashboard_id: 大屏ID
        panel_id: 面板ID

    Returns:
        查询结果数据
    """
    try:
        current_user = _current_user(http_request)
        service = get_dashboard_service()
        panel = await service.get_panel(panel_id, dashboard_id=dashboard_id, user_id=str(current_user.user_id), include_public=True)

        if not panel:
            return business_response(
                msg=f"面板不存在: {panel_id}",
                code=40404,
                status="error"
            )

        task_info = await _dashboard_runtime().create_task_record(
            task_type="dashboard_batch",
            datasource_id=str(panel.get("datasource_id") or ""),
            query_type="dashboard",
            request_payload={
                "dashboard_id": dashboard_id,
                "panel_ids": [panel_id],
                "panel_member_datasource_ids": (
                    {panel_id: request.member_datasource_id}
                    if request and request.member_datasource_id
                    else {}
                ),
                "user_id": current_user.user_id,
                "operator_name": current_user.username,
            },
            progress_text="面板执行中",
        )
        await _dashboard_runtime().update_query_task(task_info["task_id"], status="running", progress_text="面板执行中", started_time=datetime.now(), error_message=None)
        execution_target = None
        if panel.get("datasource_id"):
            ds_manager = _dashboard_runtime().get_datasource_manager()
            selected_member_ids = [request.member_datasource_id] if request and request.member_datasource_id else None
            execution_targets = await ds_manager.get_execution_datasources_async(panel.get("datasource_id"), selected_member_ids)
            execution_target = execution_targets[0] if execution_targets else None
        execution_id = await _create_dashboard_panel_execution(
            task_id=task_info["task_id"],
            panel=panel,
            execution_target=execution_target,
            user_id=current_user.user_id,
            operator_name=current_user.username,
        )
        payload = await _build_dashboard_panel_execution_result(
            panel=panel,
            member_datasource_id=(request.member_datasource_id if request else None),
            execution_target=execution_target,
            user_id=current_user.user_id,
            operator_name=current_user.username,
            query_task_id=task_info["task_id"],
            execution_id=execution_id,
        )
        _raise_if_dashboard_panel_execution_failed(payload)
        await _dashboard_runtime().update_query_task(
            task_info["task_id"],
            status="succeeded",
            progress_text="执行完成",
            result_payload=payload,
            error_message=None,
            finished_time=datetime.now(),
        )

        return business_response(data=payload, msg="执行成功")

    except ValueError as e:
        return business_response(
            msg=str(e),
            code=40001,
            status="error"
        )
    except RuntimeError as e:
        if "task_info" in locals():
            await _dashboard_runtime().update_query_task(
                task_info["task_id"],
                status="failed",
                progress_text="执行失败",
                result_payload=locals().get("payload"),
                error_message=str(e),
                finished_time=datetime.now(),
            )
        return business_response(
            msg=str(e),
            code=50029,
            status="error"
        )

    except Exception as e:
        if "task_info" in locals():
            await _dashboard_runtime().update_query_task(
                task_info["task_id"],
                status="failed",
                progress_text="执行失败",
                error_message=str(e) or "面板执行失败",
                finished_time=datetime.now(),
            )
        logger.error(f"执行面板SQL失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"执行失败: {str(e)}",
            code=50029,
            status="error"
        )


@router.post("/dashboards/{dashboard_id}/panels/{panel_id}/query-tasks")
async def create_panel_query_task(
    dashboard_id: str,
    panel_id: str,
    http_request: Request,
    request: Optional[PanelExecuteRequest] = None,
):
    """创建单面板异步执行任务，兼容代理到批量任务接口。"""
    batch_request = PanelBatchExecuteRequest(
        panel_ids=[panel_id],
        panel_member_datasource_ids=(
            {panel_id: request.member_datasource_id}
            if request and request.member_datasource_id
            else {}
        ),
    )
    return await create_dashboard_query_task(dashboard_id, http_request, batch_request)


@router.post("/dashboards/{dashboard_id}/panels/execute-batch")
async def execute_panels_batch(
    dashboard_id: str,
    http_request: Request,
    request: Optional[PanelBatchExecuteRequest | List[str]] = None,
):
    """
    批量执行面板 SQL 并流式返回数据

    使用 SSE 流式返回，每完成一个面板就立即返回结果

    Args:
        dashboard_id: 大屏ID
        panel_ids: 面板ID列表，为空时执行所有面板

    Returns:
        SSE 流，每个事件包含一个面板的执行结果
    """
    current_user = _current_user(http_request)
    service = get_dashboard_service()
    dashboard: Optional[Dict[str, Any]] = None

    # 如果没有指定 panel_ids，获取该大屏下所有面板
    panel_ids: Optional[List[str]] = None
    panel_member_datasource_ids: Dict[str, str] = {}
    if isinstance(request, list):
        panel_ids = request
    elif request is not None:
        panel_ids = request.panel_ids or None
        panel_member_datasource_ids = {
            str(panel_id): str(member_id).strip()
            for panel_id, member_id in (request.panel_member_datasource_ids or {}).items()
            if str(panel_id).strip() and str(member_id).strip()
        }

    if not panel_ids:
        dashboard = await service.get_dashboard(dashboard_id, user_id=str(current_user.user_id), include_public=True)
        if not dashboard:
            return business_response(
                msg=f"大屏不存在: {dashboard_id}",
                code=40404,
                status="error"
            )
        panel_ids = [p["id"] for p in dashboard.get("panels", [])]

    if not panel_ids:
        return business_response(
            data={"results": {}},
            msg="无面板需要执行"
        )

    panel_map, execution_target_map = await _prepare_dashboard_batch_context(
        dashboard_id,
        panel_ids,
        panel_member_datasource_ids,
        preloaded_dashboard=dashboard,
        user_id=str(current_user.user_id),
        include_public=True,
    )
    task_datasource_id = str((dashboard or {}).get("datasource_id") or "").strip()
    if not task_datasource_id:
        task_datasource_id = next(
            (
                str((panel_map.get(panel_id) or {}).get("datasource_id") or "").strip()
                for panel_id in panel_ids
                if str((panel_map.get(panel_id) or {}).get("datasource_id") or "").strip()
            ),
            "",
        )

    task_info = await _dashboard_runtime().create_task_record(
        task_type="dashboard_batch",
        datasource_id=task_datasource_id,
        query_type="dashboard",
        request_payload={
            "dashboard_id": dashboard_id,
            "panel_ids": panel_ids,
            "panel_member_datasource_ids": panel_member_datasource_ids,
            "user_id": current_user.user_id,
            "operator_name": current_user.username,
        },
        progress_text="看板执行中",
    )
    await _dashboard_runtime().update_query_task(task_info["task_id"], status="running", progress_text="看板执行中", started_time=datetime.now(), error_message=None)

    async def generate():
        """流式生成器，每完成一个面板就 yield 结果"""
        semaphore = asyncio.Semaphore(DASHBOARD_PANEL_MAX_CONCURRENCY)
        batch_result = _build_dashboard_batch_result(dashboard_id, panel_ids)

        async def execute_single_panel(panel_id: str):
            """执行单个面板并返回结果"""
            async with semaphore:
                panel = panel_map.get(panel_id)
                started_at = time.perf_counter()
                try:
                    if not panel:
                        return {
                            "panel_id": panel_id,
                            "error": f"面板不存在: {panel_id}"
                        }
                    execution_target = execution_target_map.get(
                        _build_dashboard_execution_target_cache_key(
                            str(panel.get("datasource_id") or ""),
                            panel_member_datasource_ids.get(panel_id),
                        )
                    )
                    execution_id = await _create_dashboard_panel_execution(
                        task_id=task_info["task_id"],
                        panel=panel,
                        execution_target=execution_target,
                        user_id=current_user.user_id,
                        operator_name=current_user.username,
                    )
                    payload = await _build_dashboard_panel_execution_result(
                        panel=panel,
                        member_datasource_id=panel_member_datasource_ids.get(panel_id),
                        execution_target=execution_target,
                        user_id=current_user.user_id,
                        operator_name=current_user.username,
                        query_task_id=task_info["task_id"],
                        execution_id=execution_id,
                    )
                    _raise_if_dashboard_panel_execution_failed(payload)
                    _log_dashboard_panel_timing(
                        panel,
                        int((time.perf_counter() - started_at) * 1000),
                        "succeeded",
                        panel_member_datasource_ids.get(panel_id),
                    )
                    return payload
                except ValueError as e:
                    _log_dashboard_panel_timing(
                        panel,
                        int((time.perf_counter() - started_at) * 1000),
                        "failed",
                        panel_member_datasource_ids.get(panel_id),
                        str(e),
                    )
                    return {
                        "panel_id": panel_id,
                        "error": str(e)
                    }
                except Exception as e:
                    _log_dashboard_panel_timing(
                        panel,
                        int((time.perf_counter() - started_at) * 1000),
                        "failed",
                        panel_member_datasource_ids.get(panel_id),
                        str(e),
                    )
                    logger.error(f"执行面板 {panel_id} 失败: {str(e)}")
                    return {
                        "panel_id": panel_id,
                        "error": str(e)
                    }

        tasks = {asyncio.create_task(execute_single_panel(pid)): pid for pid in panel_ids}
        pending = set(tasks.keys())
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                if await _dashboard_runtime().is_query_task_cancelled(task_info["task_id"]):
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*tasks.keys(), return_exceptions=True)
                    yield f"data: {json.dumps({'cancelled': True, 'message': '已人工取消'}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                for task in done:
                    panel_id = tasks[task]
                    try:
                        result = task.result()
                        batch_result["results"][panel_id] = {"status": "succeeded", "payload": result} if result.get("result", {}).get("success") else {"status": "failed", "error": result.get("error") or (result.get("result") or {}).get("error")}
                        batch_result["completed"] += 1
                        if batch_result["results"][panel_id]["status"] == "succeeded":
                            batch_result["succeeded"] += 1
                        else:
                            batch_result["failed"] += 1
                        yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
                    except Exception as e:
                        logger.error("面板 %s 执行异常: %s", panel_id, e, exc_info=True)
                        batch_result["results"][panel_id] = {"status": "failed", "error": str(e)}
                        batch_result["completed"] += 1
                        batch_result["failed"] += 1
                        yield f"data: {json.dumps({'panel_id': panel_id, 'error': str(e)}, ensure_ascii=False)}\n\n"
                if await _dashboard_runtime().is_query_task_cancelled(task_info["task_id"]):
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*tasks.keys(), return_exceptions=True)
                    yield f"data: {json.dumps({'cancelled': True, 'message': '已人工取消'}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
        except asyncio.CancelledError:
            for task in pending:
                task.cancel()
            await _dashboard_runtime().update_query_task(
                task_info["task_id"],
                status="cancelled" if batch_result["completed"] == 0 else "partial_success",
                progress_text="已取消" if batch_result["completed"] == 0 else f"已取消，完成 {batch_result['completed']}/{batch_result['total']}",
                result_payload=batch_result,
                error_message="客户端取消看板执行",
                finished_time=datetime.now(),
            )
            raise

        final_status = "succeeded"
        final_progress_text = "执行完成"
        final_error_message = None
        if batch_result["failed"] >= batch_result["total"] and batch_result["total"] > 0:
            final_status = "failed"
            final_progress_text = "执行失败"
            final_error_message = "所有面板执行失败"
        elif batch_result["failed"] > 0:
            final_status = "partial_success"
            final_progress_text = f"部分完成，失败 {batch_result['failed']} 个面板"
            final_error_message = f"{batch_result['failed']} 个面板执行失败"
        if await _dashboard_runtime().is_query_task_cancelled(task_info["task_id"]):
            yield "data: [DONE]\n\n"
            return
        await _dashboard_runtime().update_query_task(
            task_info["task_id"],
            status=final_status,
            progress_text=final_progress_text,
            result_payload=batch_result,
            error_message=final_error_message,
            finished_time=datetime.now(),
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/dashboards/{dashboard_id}/query-tasks")
async def create_dashboard_query_task(
    dashboard_id: str,
    http_request: Request,
    request: Optional[PanelBatchExecuteRequest | List[str]] = None,
):
    """创建看板批量异步执行任务。"""
    try:
        current_user = _current_user(http_request)
        service = get_dashboard_service()
        dashboard = await service.get_dashboard(dashboard_id, user_id=str(current_user.user_id), include_public=True)
        if not dashboard:
            return business_response(
                msg=f"大屏不存在: {dashboard_id}",
                code=40404,
                status="error"
            )

        panel_ids: Optional[List[str]] = None
        panel_member_datasource_ids: Dict[str, str] = {}
        if isinstance(request, list):
            panel_ids = request
        elif request is not None:
            panel_ids = request.panel_ids or None
            panel_member_datasource_ids = {
                str(panel_id): str(member_id).strip()
                for panel_id, member_id in (request.panel_member_datasource_ids or {}).items()
                if str(panel_id).strip() and str(member_id).strip()
            }

        task_info = await _dashboard_runtime().create_task_record(
            task_type="dashboard_batch",
            datasource_id=str(dashboard.get("datasource_id") or ""),
            query_type="dashboard",
            request_payload={
                "dashboard_id": dashboard_id,
                "panel_ids": panel_ids or [],
                "panel_member_datasource_ids": panel_member_datasource_ids,
                "user_id": current_user.user_id,
                "operator_name": current_user.username,
            },
            progress_text="看板任务已创建，等待执行",
        )
        _dashboard_runtime().schedule_query_task(task_info["task_id"])
        return business_response(
            data={
                "task_id": task_info["task_id"],
                "task_type": task_info["task_type"],
                "status": task_info["status"],
                "poll_interval_ms": _dashboard_runtime().query_task_poll_interval_ms,
            },
            msg="任务已创建"
        )
    except Exception as e:
        logger.error(f"创建看板异步任务失败: {str(e)}", exc_info=True)
        return business_response(
            msg="创建看板异步任务失败，请稍后重试",
            code=50029,
            status="error"
        )
