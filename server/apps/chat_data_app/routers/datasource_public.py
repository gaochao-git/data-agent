"""数据源公共读取路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..services.datasource.manager import get_datasource_manager
from .dependencies import _get_current_user
from .datasource_helpers import (
    _get_datasource_column_dict_response,
    _get_datasource_table_column_dicts_response,
)

logger = get_logger(__name__)
router = APIRouter()


@router.get("/datasources")
async def get_datasources(request: Request, include_tables: bool = True):
    """
    获取所有数据源列表

    Returns:
        数据源列表
    """
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasources = await ds_manager.get_authorized_datasources_async(
            include_tables=include_tables,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(
            data={"datasources": datasources},
            msg="获取成功"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取数据源列表失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=ErrorCode.CHATDATA_DATASOURCE_LIST_FAILED,
            status="error"
        )


@router.get("/datasources/{datasource_id}/tables/{table_name}/columns/{column_name}/dict")
async def get_datasource_column_dict(datasource_id: str, table_name: str, column_name: str, request: Request):
    """获取数据源字段数据字典。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        if not await ds_manager.can_access_datasource_async(
            datasource_id,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        ):
            return business_response(
                msg="无权限查看该数据源字段字典",
                code=ErrorCode.CHATDATA_PERMISSION_DENIED,
                status="error",
            )
        return await _get_datasource_column_dict_response(ds_manager, datasource_id, table_name, column_name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取字段数据字典失败: datasource_id=%s table_name=%s column_name=%s error=%s", datasource_id, table_name, column_name, e, exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED,
            status="error"
        )


@router.get("/datasources/{datasource_id}/tables/{table_name}/column-dicts")
async def get_datasource_table_column_dicts(datasource_id: str, table_name: str, request: Request):
    """批量获取数据源单张表已维护字段数据字典。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        if not await ds_manager.can_access_datasource_async(
            datasource_id,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        ):
            return business_response(
                msg="无权限查看该数据源字段字典",
                code=ErrorCode.CHATDATA_PERMISSION_DENIED,
                status="error",
            )
        return await _get_datasource_table_column_dicts_response(ds_manager, datasource_id, table_name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("批量获取字段数据字典失败: datasource_id=%s table_name=%s error=%s", datasource_id, table_name, e, exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED,
            status="error"
        )


@router.get("/datasources/{datasource_id}/schema")
async def get_schema(
    datasource_id: str,
    tables: str = None,
    request: Request = None,
):
    """获取数据源缓存表结构。实时物理元数据只允许管理接口主动拉取/同步。"""
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        table_names = [t.strip() for t in tables.split(",") if t.strip()] if tables else None
        schema_tables = await ds_manager.get_cached_schema_async(
            datasource_id,
            table_names=table_names,
        )
        return business_response(
            data={
                "tables": schema_tables,
                "count": len(schema_tables),
                "source": "cache"
            },
            msg="获取成功"
        )
    except ValueError as e:
        return business_response(
            msg=str(e),
            code=ErrorCode.CHATDATA_BAD_REQUEST,
            status="error"
        )
    except ConnectionError as e:
        return business_response(
            msg=str(e),
            code=ErrorCode.CHATDATA_QUERY_FAILED,
            status="error"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取表结构失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=ErrorCode.CHATDATA_QUERY_FAILED,
            status="error"
        )
