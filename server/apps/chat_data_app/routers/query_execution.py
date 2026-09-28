"""SQL 查询执行相关路由。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status as http_status

from auth import AuthenticationError
from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response
from ..schema import (
    CompileSemanticBuilderSqlRequest,
    QueryTaskCreateRequest,
)
from ..services.sql_run.task_service import (
    QueryTaskNotFoundError,
    QueryTaskPermissionDeniedError,
    QueryTaskService,
)
from ..services.sql_run.providers import get_query_task_service
from ..services.semantic.run.semantic_query_service import compile_semantic_builder_sql
from .dependencies import _get_current_user

logger = get_logger(__name__)
from . import JSON_API_RESPONSES

router = APIRouter(tags=["ChatData 查询执行"], responses=JSON_API_RESPONSES)

def _resolve_current_user(request: Request, current_user: Any | None) -> Any:
    return current_user if current_user is not None else _get_current_user(request)


def _current_user_identity(request: Request | None) -> tuple[str, str]:
    if request is None:
        return "system", "system"
    current_user = _get_current_user(request)
    user_id = str(getattr(current_user, "user_id", None) or getattr(current_user, "username", None) or "system")
    username = str(getattr(current_user, "username", None) or user_id)
    return user_id, username




@router.post("/query-tasks")
async def create_query_task(
    request: Request,
    body: QueryTaskCreateRequest,
    task_service: Annotated[QueryTaskService | None, Depends(get_query_task_service)] = None,
):
    """创建异步查询任务并返回任务 ID。"""
    try:
        current_user = _resolve_current_user(request, None)
        task_info = await (task_service or get_query_task_service()).create_query_task_for_user(
            body,
            user_id=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data=task_info, msg="任务已创建")
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except HTTPException:
        raise
    except QueryTaskNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error("创建异步查询任务失败: %s", e, exc_info=True)
        return business_response(msg="创建异步查询任务失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.get("/query-tasks/{task_id}")
async def get_query_task(
    task_id: str,
    request: Request,
    task_service: Annotated[QueryTaskService | None, Depends(get_query_task_service)] = None,
):
    """查询异步任务状态与结果。"""
    try:
        current_user = _resolve_current_user(request, None)
        data = await (task_service or get_query_task_service()).get_query_task_for_user(task_id, user_id=str(current_user.user_id))
        return business_response(data=data, msg="查询成功")
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except QueryTaskNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("查询异步任务失败: %s", e, exc_info=True)
        return business_response(msg="查询异步任务失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/query-tasks/{task_id}/cancel")
async def cancel_query_task(
    task_id: str,
    request: Request,
    task_service: Annotated[QueryTaskService | None, Depends(get_query_task_service)] = None,
):
    """停止异步查询任务及其 SQL 执行。"""
    try:
        current_user = _resolve_current_user(request, None)
        result = await (task_service or get_query_task_service()).cancel_query_task_for_user(
            task_id,
            user_id=str(current_user.user_id),
            operator_name=str(getattr(current_user, "username", "") or "").strip(),
        )
        return business_response(data=result.data, msg=result.message)
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except QueryTaskNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except QueryTaskPermissionDeniedError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_PERMISSION_DENIED, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("停止查询任务失败: task_id=%s error=%s", task_id, e, exc_info=True)
        return business_response(msg="停止查询任务失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/compile-semantic-builder-sql")
async def compile_semantic_builder_sql_endpoint(
    request: Request,
    body: CompileSemanticBuilderSqlRequest,
):
    """编译语义可视化 DSL 为 SQL（仅预览，不执行）。"""
    try:
        current_user = _resolve_current_user(request, None)
        result = await compile_semantic_builder_sql(
            datasource_id=body.datasource_id,
            semantic_config_id=body.semantic_config_id,
            semantic_builder_config=body.semantic_builder_config,
            user_id=str(current_user.user_id),
        )
        return business_response(data={"sql": result["sql"], "builder_config": result["builder_config"]}, msg="编译成功")
    except AuthenticationError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message) from err
    except HTTPException:
        raise
    except LookupError as err:
        return business_response(msg=str(err), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except ValueError as err:
        return business_response(msg=str(err), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as err:
        logger.error("Semantic Builder DSL 编译失败: %s", err, exc_info=True)
        return business_response(msg="Semantic Builder DSL 编译失败，请检查配置", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")
