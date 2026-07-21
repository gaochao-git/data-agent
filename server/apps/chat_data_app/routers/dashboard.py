"""Dashboard routes for ChatData."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response
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
from ..services import dashboard_runtime
from ..services.dashboard_spec_service import create_dashboard_from_spec_payload
from ..services.dashboard_service import get_dashboard_service
from ..services.query_metadata import generate_query_metadata
from .dependencies import _get_current_user

logger = get_logger(__name__)
router = APIRouter()


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
        return business_response(msg="无权限操作该看板", code=ErrorCode.CHATDATA_DASHBOARD_PERMISSION_DENIED, status="error")
    return business_response(msg=f"大屏不存在: {dashboard_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")


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
            code=ErrorCode.CHATDATA_DASHBOARD_LIST_FAILED,
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
            code=ErrorCode.CHATDATA_DASHBOARD_CREATE_FAILED,
            status="error"
        )


@router.post("/dashboards/from-spec")
async def create_dashboard_from_spec(request: DashboardFromSpecRequest, http_request: Request):
    """根据 AI 生成的看板规格创建大屏和面板。"""
    try:
        current_user = _current_user(http_request)
        datasource_id = str(request.datasource_id or "").strip()
        if not datasource_id:
            return business_response(msg="缺少数据源ID，无法创建看板", code=ErrorCode.CHATDATA_VALIDATION_FAILED, status="error")
        spec = request.dashboard_spec or {}
        if spec.get("need_clarify"):
            return business_response(msg="当前方案仍需补充确认，暂不能写入看板", code=ErrorCode.CHATDATA_OPERATION_NOT_ALLOWED, status="error")
        cards = spec.get("cards") if isinstance(spec.get("cards"), list) else []
        if not cards:
            return business_response(msg="看板规格缺少卡片，无法创建看板", code=ErrorCode.CHATDATA_DASHBOARD_CARD_REQUIRED, status="error")
        semantic_config_id = str(request.semantic_config_id or spec.get("semantic_config_id") or "").strip()
        if not semantic_config_id:
            return business_response(msg="看板规格缺少指标集ID，无法创建看板", code=ErrorCode.CHATDATA_SEMANTIC_CONFIG_REQUIRED, status="error")

        result = await create_dashboard_from_spec_payload(
            dashboard_spec=spec,
            datasource_id=datasource_id,
            semantic_config_id=semantic_config_id,
            conversation_id=request.conversation_id,
            user_id=str(current_user.user_id),
            username=str(current_user.username),
        )
        if not result.get("panels"):
            return business_response(msg="看板创建失败：没有可创建的有效卡片", data={"warnings": result.get("warnings") or []}, code=ErrorCode.CHATDATA_DASHBOARD_NO_VALID_CARD, status="error")
        return business_response(data=result, msg="看板创建成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("根据 AI 看板规格创建大屏失败: %s", e, exc_info=True)
        return business_response(msg=f"创建失败: {str(e)}", code=ErrorCode.CHATDATA_DASHBOARD_AI_CREATE_FAILED, status="error")


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
        return business_response(msg=f"发布失败: {str(e)}", code=ErrorCode.CHATDATA_DASHBOARD_PUBLISH_FAILED, status="error")


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
        return business_response(msg=f"取消发布失败: {str(e)}", code=ErrorCode.CHATDATA_DASHBOARD_UNPUBLISH_FAILED, status="error")


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
                code=ErrorCode.CHATDATA_NOT_FOUND,
                status="error"
            )

        return business_response(data=_with_dashboard_permission(dashboard, user_id), msg="获取成功")

    except Exception as e:
        logger.error(f"获取大屏详情失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=ErrorCode.CHATDATA_DASHBOARD_GET_FAILED,
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
            code=ErrorCode.CHATDATA_DASHBOARD_UPDATE_FAILED,
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
            code=ErrorCode.CHATDATA_DASHBOARD_DELETE_FAILED,
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
            member_datasource_id=request.member_datasource_id,
            user_id=str(current_user.user_id),
            operator_name=operator_name,
        )

        if not panel:
            return business_response(
                msg=f"大屏不存在: {dashboard_id}",
                code=ErrorCode.CHATDATA_NOT_FOUND,
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
            code=ErrorCode.CHATDATA_DASHBOARD_PANEL_LIST_FAILED,
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
            code=ErrorCode.CHATDATA_DASHBOARD_LAYOUT_FAILED,
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
            member_datasource_id=request.member_datasource_id,
            user_id=str(current_user.user_id),
            operator_name=operator_name,
        )

        if not panel:
            return business_response(
                msg=f"面板不存在: {panel_id}",
                code=ErrorCode.CHATDATA_NOT_FOUND,
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
            code=ErrorCode.CHATDATA_DASHBOARD_PANEL_CREATE_FAILED,
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
                code=ErrorCode.CHATDATA_NOT_FOUND,
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
            code=ErrorCode.CHATDATA_DASHBOARD_PANEL_UPDATE_FAILED,
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
                code=ErrorCode.CHATDATA_NOT_FOUND,
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
            code=ErrorCode.CHATDATA_DASHBOARD_PANEL_DELETE_FAILED,
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
                code=ErrorCode.CHATDATA_NOT_FOUND,
                status="error"
            )
        panel_ids = [p["id"] for p in dashboard.get("panels", [])]

    if not panel_ids:
        return business_response(
            data={"results": {}},
            msg="无面板需要执行"
        )

    try:
        panel_map, execution_target_map = await dashboard_runtime.prepare_dashboard_batch_context(
            dashboard_id,
            panel_ids,
            panel_member_datasource_ids,
            preloaded_dashboard=dashboard,
            user_id=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
            include_public=True,
        )
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    effective_panel_member_datasource_ids = dashboard_runtime.build_effective_panel_member_datasource_ids(
        panel_ids,
        panel_map,
        panel_member_datasource_ids,
    )
    panel_execution_plan = dashboard_runtime.build_panel_execution_plan(
        panel_ids,
        panel_map,
        panel_member_datasource_ids,
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

    task_info = await dashboard_runtime.dashboard_runtime().create_task_record(
        task_type="dashboard_batch",
        datasource_id=task_datasource_id,
        query_type="dashboard",
        request_payload={
            "dashboard_id": dashboard_id,
            "panel_ids": panel_ids,
            "panel_member_datasource_ids": effective_panel_member_datasource_ids,
            "panel_execution_plan": panel_execution_plan,
            "user_id": current_user.user_id,
            "operator_name": current_user.username,
        },
        progress_text="看板执行中",
    )
    await dashboard_runtime.dashboard_runtime().update_query_task(task_info["task_id"], status="running", progress_text="看板执行中", started_time=datetime.now(), error_message=None)

    return StreamingResponse(
        dashboard_runtime.stream_dashboard_batch_execution(
            dashboard_id=dashboard_id,
            panel_ids=panel_ids,
            panel_map=panel_map,
            execution_target_map=execution_target_map,
            effective_panel_member_datasource_ids=effective_panel_member_datasource_ids,
            task_id=task_info["task_id"],
            user_id=str(current_user.user_id),
            operator_name=str(current_user.username),
        ),
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
                code=ErrorCode.CHATDATA_NOT_FOUND,
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

        effective_panel_ids = panel_ids or [
            str(panel.get("id") or "").strip()
            for panel in (dashboard.get("panels") or [])
            if str(panel.get("id") or "").strip()
        ]
        panel_map = {
            str(panel.get("id") or "").strip(): panel
            for panel in (dashboard.get("panels") or [])
            if str(panel.get("id") or "").strip()
        }
        effective_panel_member_datasource_ids = dashboard_runtime.build_effective_panel_member_datasource_ids(
            effective_panel_ids,
            panel_map,
            panel_member_datasource_ids,
        )
        panel_execution_plan = dashboard_runtime.build_panel_execution_plan(
            effective_panel_ids,
            panel_map,
            panel_member_datasource_ids,
        )

        task_info = await dashboard_runtime.dashboard_runtime().create_task_record(
            task_type="dashboard_batch",
            datasource_id=str(dashboard.get("datasource_id") or ""),
            query_type="dashboard",
            request_payload={
                "dashboard_id": dashboard_id,
                "panel_ids": panel_ids or [],
                "panel_member_datasource_ids": effective_panel_member_datasource_ids,
                "panel_execution_plan": panel_execution_plan,
                "user_id": current_user.user_id,
                "operator_name": current_user.username,
            },
            progress_text="看板任务已创建，等待执行",
        )
        dashboard_runtime.dashboard_runtime().schedule_query_task(task_info["task_id"])
        return business_response(
            data={
                "task_id": task_info["task_id"],
                "task_type": task_info["task_type"],
                "status": task_info["status"],
                "poll_interval_ms": dashboard_runtime.dashboard_runtime().query_task_poll_interval_ms,
            },
            msg="任务已创建"
        )
    except Exception as e:
        logger.error(f"创建看板异步任务失败: {str(e)}", exc_info=True)
        return business_response(
            msg="创建看板异步任务失败，请稍后重试",
            code=ErrorCode.CHATDATA_DASHBOARD_EXECUTE_FAILED,
            status="error"
        )
