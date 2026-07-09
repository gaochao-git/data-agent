"""查询历史路由。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import APIRouter, Request

from auth import AuthenticationError
from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response
from ..services.query_history_service import QueryHistoryService
from ..services.query_task_service import QueryTaskService
from .dependencies import _get_current_user

logger = get_logger(__name__)
router = APIRouter()

_query_history_service: QueryHistoryService | None = None
_query_task_service: QueryTaskService | None = None
_fetch_remote_result: Callable[..., Awaitable[Dict[str, Any]]] | None = None
_current_user_getter: Callable[[Request], Any] = _get_current_user


def configure_query_history_router(
    *,
    query_history_service: QueryHistoryService,
    query_task_service: QueryTaskService,
    fetch_remote_result: Callable[..., Awaitable[Dict[str, Any]]],
    current_user_getter: Optional[Callable[[Request], Any]] = None,
) -> None:
    """注入查询历史路由依赖。"""
    global _query_history_service, _query_task_service, _fetch_remote_result, _current_user_getter
    _query_history_service = query_history_service
    _query_task_service = query_task_service
    _fetch_remote_result = fetch_remote_result
    if current_user_getter is not None:
        _current_user_getter = current_user_getter


def _history_service() -> QueryHistoryService:
    if _query_history_service is None:
        raise RuntimeError("query history service 未初始化")
    return _query_history_service


def _task_service() -> QueryTaskService:
    if _query_task_service is None:
        raise RuntimeError("query task service 未初始化")
    return _query_task_service


def _remote_result_fetcher() -> Callable[..., Awaitable[Dict[str, Any]]]:
    if _fetch_remote_result is None:
        raise RuntimeError("query history remote result fetcher 未初始化")
    return _fetch_remote_result


@router.get("/query-history")
async def get_query_history(
    request: Request,
    datasource_id: str,
    page: int = 1,
    page_size: int = 10,
    source: Optional[str] = None,
    status: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
):
    """获取当前用户的查询历史。"""
    try:
        current_user = _current_user_getter(request)
        data = await _history_service().list_user_history(
            datasource_id=datasource_id,
            user_id=str(current_user.user_id),
            page=page,
            page_size=page_size,
            source=source,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        return business_response(data=data, msg="获取成功")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error("获取查询历史失败: %s", e, exc_info=True)
        return business_response(msg="获取查询历史失败，请稍后重试", code=ErrorCode.CHATDATA_QUERY_HISTORY_FAILED, status="error")


@router.get("/admin/query-history")
async def get_admin_query_history(
    request: Request,
    datasource_id: Optional[str] = None,
    semantic_config_id: Optional[str] = None,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    source: Optional[str] = None,
    status: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
):
    """获取后台全局 SQL 查询历史。"""
    try:
        _current_user_getter(request)
        data = await _history_service().list_admin_history(
            datasource_id=datasource_id,
            semantic_config_id=semantic_config_id,
            user_id=user_id,
            username=username,
            keyword=keyword,
            page=page,
            page_size=page_size,
            source=source,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        return business_response(data=data, msg="获取成功")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error("获取全局查询历史失败: %s", e, exc_info=True)
        return business_response(msg="获取全局查询历史失败，请稍后重试", code=ErrorCode.CHATDATA_QUERY_HISTORY_FAILED, status="error")


@router.post("/query-history/cancel-running")
async def cancel_my_running_query_history(
    request: Request,
    datasource_id: Optional[str] = None,
):
    """停止当前用户所有执行中的 SQL。"""
    try:
        current_user = _current_user_getter(request)
        result = await _task_service().cancel_running_query_history(
            datasource_id=datasource_id,
            user_id=str(current_user.user_id),
            operator_name=str(getattr(current_user, "username", "") or "").strip(),
        )
        return business_response(data=result, msg="停止请求已提交")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except Exception as e:
        logger.error("批量停止个人查询历史失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(msg="批量停止失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/admin/query-history/cancel-running")
async def cancel_admin_running_query_history(
    request: Request,
    datasource_id: Optional[str] = None,
):
    """停止全局所有执行中的 SQL。"""
    try:
        current_user = _current_user_getter(request)
        result = await _task_service().cancel_running_query_history(
            datasource_id=datasource_id,
            operator_name=str(getattr(current_user, "username", "") or "").strip(),
        )
        return business_response(data=result, msg="停止请求已提交")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except Exception as e:
        logger.error("批量停止全局查询历史失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(msg="批量停止失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.get("/query-history/{execution_id}/result")
async def get_query_history_result(execution_id: str, request: Request):
    """按执行ID获取查询历史结果。"""
    try:
        current_user = _current_user_getter(request)
        data = await _history_service().get_history_result(
            execution_id=execution_id,
            user_id=str(current_user.user_id),
            fetch_remote_result=_remote_result_fetcher(),
        )
        if not data:
            return business_response(msg="查询历史不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        return business_response(data=data, msg="获取成功")
    except Exception as e:
        logger.error("获取查询历史结果失败: execution_id=%s error=%s", execution_id, e, exc_info=True)
        return business_response(msg="获取查询历史结果失败，请稍后重试", code=ErrorCode.CHATDATA_QUERY_HISTORY_FAILED, status="error")
