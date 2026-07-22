"""查询历史路由。"""

from __future__ import annotations

from typing import Annotated, Any, Awaitable, Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import AuthenticationError
from utils.error_codes import ErrorCode, chatdata_http_exception_error_code
from utils.logger import get_logger
from utils.response import business_response
from ..services.query_history_service import QueryHistoryService
from ..services.query_providers import (
    get_query_history_remote_result_fetcher,
    get_query_history_service,
    get_query_task_service,
)
from ..services.query_task_service import QueryTaskNotFoundError, QueryTaskService
from .dependencies import _get_current_user

logger = get_logger(__name__)
router = APIRouter()

RemoteResultFetcher = Callable[..., Awaitable[Dict[str, Any]]]


def _resolve_current_user(request: Request, current_user: Any | None) -> Any:
    return current_user if current_user is not None else _get_current_user(request)


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
    history_service: Annotated[QueryHistoryService | None, Depends(get_query_history_service)] = None,
):
    """获取当前用户的查询历史。"""
    try:
        current_user = _resolve_current_user(request, None)
        data = await (history_service or get_query_history_service()).list_user_history(
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
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=chatdata_http_exception_error_code(e.status_code), status="error")
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
    history_service: Annotated[QueryHistoryService | None, Depends(get_query_history_service)] = None,
):
    """获取后台全局 SQL 查询历史。"""
    try:
        _get_current_user(request)
        data = await (history_service or get_query_history_service()).list_admin_history(
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
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=chatdata_http_exception_error_code(e.status_code), status="error")
    except Exception as e:
        logger.error("获取全局查询历史失败: %s", e, exc_info=True)
        return business_response(msg="获取全局查询历史失败，请稍后重试", code=ErrorCode.CHATDATA_QUERY_HISTORY_FAILED, status="error")


@router.post("/query-history/cancel-running")
async def cancel_my_running_query_history(
    request: Request,
    datasource_id: Optional[str] = None,
    task_service: Annotated[QueryTaskService | None, Depends(get_query_task_service)] = None,
):
    """停止当前用户所有执行中的 SQL。"""
    try:
        current_user = _resolve_current_user(request, None)
        result = await (task_service or get_query_task_service()).cancel_running_query_history(
            datasource_id=datasource_id,
            user_id=str(current_user.user_id),
            operator_name=str(getattr(current_user, "username", "") or "").strip(),
        )
        return business_response(data=result, msg="停止请求已提交")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=chatdata_http_exception_error_code(e.status_code), status="error")
    except Exception as e:
        logger.error("批量停止个人查询历史失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(msg="批量停止失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/query-history/{execution_id}/cancel")
async def cancel_query_history_execution(
    execution_id: str,
    request: Request,
    task_service: Annotated[QueryTaskService | None, Depends(get_query_task_service)] = None,
):
    """停止当前用户的一条 SQL 执行明细。"""
    try:
        current_user = _resolve_current_user(request, None)
        result = await (task_service or get_query_task_service()).cancel_query_execution_for_user(
            execution_id,
            user_id=str(current_user.user_id),
            operator_name=str(getattr(current_user, "username", "") or "").strip(),
        )
        return business_response(data=result.data, msg=result.message)
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except QueryTaskNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=chatdata_http_exception_error_code(e.status_code), status="error")
    except Exception as e:
        logger.error("停止查询历史明细失败: execution_id=%s error=%s", execution_id, e, exc_info=True)
        return business_response(msg="停止查询明细失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/admin/query-history/cancel-running")
async def cancel_admin_running_query_history(
    request: Request,
    datasource_id: Optional[str] = None,
    task_service: Annotated[QueryTaskService | None, Depends(get_query_task_service)] = None,
):
    """停止全局所有执行中的 SQL。"""
    try:
        current_user = _get_current_user(request)
        result = await (task_service or get_query_task_service()).cancel_running_query_history(
            datasource_id=datasource_id,
            operator_name=str(getattr(current_user, "username", "") or "").strip(),
        )
        return business_response(data=result, msg="停止请求已提交")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=chatdata_http_exception_error_code(e.status_code), status="error")
    except Exception as e:
        logger.error("批量停止全局查询历史失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(msg="批量停止失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.get("/query-history/{execution_id}/result")
async def get_query_history_result(
    execution_id: str,
    request: Request,
    history_service: Annotated[QueryHistoryService | None, Depends(get_query_history_service)] = None,
    remote_result_fetcher: Annotated[RemoteResultFetcher | None, Depends(get_query_history_remote_result_fetcher)] = None,
):
    """按执行ID获取查询历史结果。"""
    try:
        current_user = _resolve_current_user(request, None)
        data = await (history_service or get_query_history_service()).get_history_result(
            execution_id=execution_id,
            user_id=str(current_user.user_id),
            fetch_remote_result=remote_result_fetcher or get_query_history_remote_result_fetcher(),
        )
        if not data:
            return business_response(msg="查询历史不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        return business_response(data=data, msg="获取成功")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=chatdata_http_exception_error_code(e.status_code), status="error")
    except Exception as e:
        logger.error("获取查询历史结果失败: execution_id=%s error=%s", execution_id, e, exc_info=True)
        return business_response(msg="获取查询历史结果失败，请稍后重试", code=ErrorCode.CHATDATA_QUERY_HISTORY_FAILED, status="error")
