"""查询收藏路由。"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from utils.error_codes import ErrorCode, chatdata_http_exception_error_code
from utils.logger import get_logger
from utils.response import business_response
from ..schema import QueryFavoriteCreateRequest, QueryFavoriteUpdateRequest
from ..services.sql_run.providers import get_query_favorite_service
from ..services.sql_run.favorite_service import (
    QueryFavoriteNotFoundError,
    QueryFavoriteOperationNotAllowedError,
    QueryFavoriteService,
    QueryFavoriteValidationError,
)
from .dependencies import _get_current_user

logger = get_logger(__name__)
router = APIRouter()

def _resolve_current_user(request: Request, current_user: Any | None) -> Any:
    return current_user if current_user is not None else _get_current_user(request)


@router.get("/query-favorites")
async def get_query_favorites(
    request: Request,
    datasource_id: str,
    query_type: Optional[str] = None,
    limit: int = 50,
    favorite_service: Annotated[QueryFavoriteService | None, Depends(get_query_favorite_service)] = None,
):
    """获取收藏查询卡片。"""
    try:
        current_user = _resolve_current_user(request, None)
        data = await (favorite_service or get_query_favorite_service()).list_favorites(
            datasource_id=datasource_id,
            user_id=str(current_user.user_id),
            query_type=query_type,
            limit=limit,
        )
        return business_response(data=data, msg="获取成功")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=chatdata_http_exception_error_code(e.status_code), status="error")
    except Exception as e:
        logger.error("获取收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="获取收藏查询失败，请稍后重试", code=ErrorCode.CHATDATA_FAVORITE_LIST_FAILED, status="error")


@router.post("/query-favorites")
async def create_query_favorite(
    payload: QueryFavoriteCreateRequest,
    request: Request,
    favorite_service: Annotated[QueryFavoriteService | None, Depends(get_query_favorite_service)] = None,
):
    """创建查询收藏。"""
    try:
        current_user = _resolve_current_user(request, None)
        data = await (favorite_service or get_query_favorite_service()).create_favorite(
            payload,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(data=data, msg="收藏成功")
    except QueryFavoriteValidationError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_INVALID_FAVORITE_CONFIG, status="error")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=chatdata_http_exception_error_code(e.status_code), status="error")
    except Exception as e:
        logger.error("创建收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="创建收藏查询失败，请稍后重试", code=ErrorCode.CHATDATA_FAVORITE_CREATE_FAILED, status="error")


@router.put("/query-favorites/{favorite_id}")
async def update_query_favorite(
    favorite_id: int,
    payload: QueryFavoriteUpdateRequest,
    request: Request,
    favorite_service: Annotated[QueryFavoriteService | None, Depends(get_query_favorite_service)] = None,
):
    """更新查询收藏。"""
    try:
        current_user = _resolve_current_user(request, None)
        data = await (favorite_service or get_query_favorite_service()).update_favorite(
            favorite_id,
            payload,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(data=data, msg="更新成功")
    except QueryFavoriteNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_FAVORITE_NOT_FOUND, status="error")
    except QueryFavoriteOperationNotAllowedError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_OPERATION_NOT_ALLOWED, status="error")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=chatdata_http_exception_error_code(e.status_code), status="error")
    except Exception as e:
        logger.error("更新收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="更新收藏查询失败，请稍后重试", code=ErrorCode.CHATDATA_FAVORITE_UPDATE_FAILED, status="error")


@router.delete("/query-favorites/{favorite_id}")
async def delete_query_favorite(
    favorite_id: int,
    request: Request,
    favorite_service: Annotated[QueryFavoriteService | None, Depends(get_query_favorite_service)] = None,
):
    """删除查询收藏（软删除）。"""
    try:
        current_user = _resolve_current_user(request, None)
        data = await (favorite_service or get_query_favorite_service()).delete_favorite(
            favorite_id=favorite_id,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(data=data, msg="删除成功")
    except QueryFavoriteNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_FAVORITE_NOT_FOUND, status="error")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=chatdata_http_exception_error_code(e.status_code), status="error")
    except Exception as e:
        logger.error("删除收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="删除收藏查询失败，请稍后重试", code=ErrorCode.CHATDATA_FAVORITE_DELETE_FAILED, status="error")
