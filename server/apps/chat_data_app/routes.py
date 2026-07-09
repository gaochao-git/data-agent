"""
ChatData API 路由定义

提供 ChatData 查询与配置管理相关接口
"""

import json
import re
from typing import Optional, Dict, List, Any
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from auth import AuthenticationError
from .schema import (
    QuickSqlRequest,
    ExecuteSqlRequest,
    QueryTaskCreateRequest,
    CompileSemanticBuilderSqlRequest,
    QueryFavoriteCreateRequest,
    QueryFavoriteUpdateRequest,
)
from .services.datasource_manager import get_datasource_manager
from .services.sql_executor import fetch_http_query_task_result, stop_http_query_task, kill_mysql_query
from .services import query_runtime
from .services.query_history_service import QueryHistoryService
from .services.query_task_service import (
    QueryTaskNotFoundError,
    QueryTaskPermissionDeniedError,
    QueryTaskService,
)
from .daos.query_history_dao import QueryHistoryDao
from .daos.query_favorite_dao import QueryFavoriteDao
from .services.query_favorite_service import (
    QueryFavoriteNotFoundError,
    QueryFavoriteOperationNotAllowedError,
    QueryFavoriteService,
    QueryFavoriteValidationError,
)
from .services.semantic_builder_compiler import compile_semantic_builder_sql
from .agent import get_all_tables_async, get_tables_create_format
from .agent.sql_assist_graph import get_sql_assist_graph
from utils.database import get_session
from utils import conversation
from utils.error_codes import ErrorCode, chatdata_http_exception_error_code
from utils.logger import get_logger
from utils.response import business_response
from .routers.common import _get_current_user
from .routers.config import router as config_router
from .routers.conversations import router as conversations_router
from .routers.datasource_admin import router as datasource_admin_router
from .routers.datasource_public import router as datasource_public_router
from .routers.evaluation import router as evaluation_router
from .routers.data_analyze_workspace import router as data_analyze_workspace_router
from .routers.skills import router as skills_router
from .routers.dashboard import router as dashboard_router
from .routers.semantic_config import router as semantic_config_router
from .routers.uploaded_files import router as uploaded_files_router

logger = get_logger(__name__)
AGENT_ID = "chat_data"
SQL_ASSIST_AGENT_ID = "chat_data_sql_assist"
DATA_ANALYZE_AGENT_ID = "chat_data_data_analyze"
query_history_service = QueryHistoryService(QueryHistoryDao(session_context=lambda: get_session()))
query_task_service = QueryTaskService(
    query_history_service=query_history_service,
    datasource_manager_factory=lambda: get_datasource_manager(),
    fetch_http_query_task_result=lambda *args, **kwargs: fetch_http_query_task_result(*args, **kwargs),
    stop_http_query_task=lambda *args, **kwargs: stop_http_query_task(*args, **kwargs),
    kill_mysql_query=lambda *args, **kwargs: kill_mysql_query(*args, **kwargs),
)
query_task_service.configure_dashboard_runtime()
query_favorite_service = QueryFavoriteService(QueryFavoriteDao(session_context=lambda: get_session()))

# 创建路由器
router = APIRouter(prefix="/api/chatdata",tags=["ChatData NL2SQL"])
router.include_router(conversations_router)
router.include_router(config_router)
router.include_router(evaluation_router)
router.include_router(data_analyze_workspace_router)
router.include_router(skills_router)
router.include_router(dashboard_router)
router.include_router(semantic_config_router)
router.include_router(datasource_public_router)
router.include_router(datasource_admin_router)
router.include_router(uploaded_files_router)


# ============ 路由接口 ============

@router.get("/query-favorites")
async def get_query_favorites(
    request: Request,
    datasource_id: str,
    query_type: Optional[str] = None,
    limit: int = 50
):
    """获取收藏查询卡片。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        data = await query_favorite_service.list_favorites(
            datasource_id=datasource_id,
            user_id=user_id,
            query_type=query_type,
            limit=limit,
        )
        return business_response(data=data, msg="获取成功")
    except Exception as e:
        logger.error("获取收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="获取收藏查询失败，请稍后重试", code=ErrorCode.CHATDATA_FAVORITE_LIST_FAILED, status="error")


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
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        data = await query_history_service.list_user_history(
            datasource_id=datasource_id,
            user_id=user_id,
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
        _get_current_user(request)
        data = await query_history_service.list_admin_history(
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
        current_user = _get_current_user(request)
        result = await query_task_service.cancel_running_query_history(
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
        current_user = _get_current_user(request)
        result = await query_task_service.cancel_running_query_history(
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
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        data = await query_history_service.get_history_result(
            execution_id=execution_id,
            user_id=user_id,
            fetch_remote_result=fetch_http_query_task_result,
        )
        if not data:
            return business_response(msg="查询历史不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        return business_response(data=data, msg="获取成功")
    except Exception as e:
        logger.error("获取查询历史结果失败: execution_id=%s error=%s", execution_id, e, exc_info=True)
        return business_response(msg="获取查询历史结果失败，请稍后重试", code=ErrorCode.CHATDATA_QUERY_HISTORY_FAILED, status="error")


@router.post("/query-favorites")
async def create_query_favorite(payload: QueryFavoriteCreateRequest, request: Request):
    """创建查询收藏。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(current_user.username or "")
        data = await query_favorite_service.create_favorite(payload, user_id=user_id, username=username)
        return business_response(data=data, msg="收藏成功")
    except QueryFavoriteValidationError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_INVALID_FAVORITE_CONFIG, status="error")
    except Exception as e:
        logger.error("创建收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="创建收藏查询失败，请稍后重试", code=ErrorCode.CHATDATA_FAVORITE_CREATE_FAILED, status="error")


@router.put("/query-favorites/{favorite_id}")
async def update_query_favorite(favorite_id: int, payload: QueryFavoriteUpdateRequest, request: Request):
    """更新查询收藏。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(current_user.username or "")
        data = await query_favorite_service.update_favorite(
            favorite_id,
            payload,
            user_id=user_id,
            username=username,
        )
        return business_response(data=data, msg="更新成功")
    except QueryFavoriteNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_FAVORITE_NOT_FOUND, status="error")
    except QueryFavoriteOperationNotAllowedError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_OPERATION_NOT_ALLOWED, status="error")
    except Exception as e:
        logger.error("更新收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="更新收藏查询失败，请稍后重试", code=ErrorCode.CHATDATA_FAVORITE_UPDATE_FAILED, status="error")


@router.delete("/query-favorites/{favorite_id}")
async def delete_query_favorite(favorite_id: int, request: Request):
    """删除查询收藏（软删除）。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(current_user.username or "")
        data = await query_favorite_service.delete_favorite(favorite_id=favorite_id, user_id=user_id, username=username)
        return business_response(data=data, msg="删除成功")
    except QueryFavoriteNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_FAVORITE_NOT_FOUND, status="error")
    except Exception as e:
        logger.error("删除收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="删除收藏查询失败，请稍后重试", code=ErrorCode.CHATDATA_FAVORITE_DELETE_FAILED, status="error")


@router.post("/nl2sql")
async def quick_sql_endpoint(payload: QuickSqlRequest, request: Request = None):
    """
    快速 SQL 生成接口（流式输出，用于 SQL 编辑器的 AI 辅助）

    简化的 NL2SQL 接口，流式返回生成的 SQL，不走完整的 Agent 流程。

    Args:
        request: 快速 SQL 请求对象
            - query: 自然语言查询
            - datasource_id: 数据源ID

    Returns:
        SSE 流式响应，包含生成的 SQL
    """
    async def generate_stream():
        try:
            logger.info(f"收到快速SQL生成请求: {payload.query[:100]}...")
            ds_manager = get_datasource_manager()
            datasource = await ds_manager.get_datasource_async(payload.datasource_id)
            if not datasource:
                yield f"data: {json.dumps({'type': 'error', 'message': '数据源不存在'}, ensure_ascii=False)}\n\n"
                return
            source_type = datasource["source_type"]

            # 获取表结构
            tables = await get_all_tables_async(payload.datasource_id)
            if not tables:
                yield f"data: {json.dumps({'type': 'error', 'message': '数据源暂无表结构信息'}, ensure_ascii=False)}\n\n"
                return

            # 获取所有表的 DDL 格式
            table_names = [t.get("table_name") for t in tables]
            tables_ddl_info = get_tables_create_format(tables, table_names)

            if not payload.conversation_id:
                yield f"data: {json.dumps({'type': 'error', 'message': '缺少 SQL 辅助会话ID'}, ensure_ascii=False)}\n\n"
                return

            # 流式输出
            full_content = ""
            from .agent import get_checkpointer
            current_user_id = "system"
            if request is not None:
                current_user = _get_current_user(request)
                current_user_id = getattr(current_user, "user_id", None) or getattr(current_user, "username", None) or "system"
            existing_agent_id = await conversation.get_agent_id("", payload.conversation_id, current_user_id)
            if existing_agent_id and existing_agent_id != SQL_ASSIST_AGENT_ID:
                yield f"data: {json.dumps({'type': 'error', 'message': '对话不存在'}, ensure_ascii=False)}\n\n"
                return
            if not existing_agent_id:
                unowned_agent_id = await conversation.get_agent_id("", payload.conversation_id)
                if unowned_agent_id:
                    yield f"data: {json.dumps({'type': 'error', 'message': '对话不存在'}, ensure_ascii=False)}\n\n"
                    return
            await conversation.get_or_create_conversation("", SQL_ASSIST_AGENT_ID, payload.conversation_id, current_user_id)

            config = {"configurable": {"thread_id": payload.conversation_id, "checkpoint_ns": ""}}
            initial_state = {
                "messages": [HumanMessage(content=payload.query, additional_kwargs={"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})],
                "tables_ddl_info": tables_ddl_info,
            }
            async with get_checkpointer() as checkpointer:
                graph = get_sql_assist_graph(
                    checkpointer,
                    sql_dialect=source_type,
                )
                async for chunk, metadata in graph.astream(initial_state, config=config, stream_mode="messages"):
                    if metadata.get("langgraph_node") != "sql_assist":
                        continue
                    if hasattr(chunk, 'content') and chunk.content:
                        full_content += chunk.content
                        yield f"data: {json.dumps({'type': 'chunk', 'content': chunk.content}, ensure_ascii=False)}\n\n"

            # 提取 SQL（从 ```sql ... ``` 代码块中提取）
            sql_match = re.search(r'```sql\s*(.*?)\s*```', full_content, re.DOTALL | re.IGNORECASE)
            if sql_match:
                sql = sql_match.group(1).strip()
            else:
                # 如果没有找到代码块，尝试直接返回内容
                sql = full_content.strip()

            logger.info(f"生成的 SQL: {sql[:200]}...")

            # 发送完成事件（包含提取的纯 SQL）
            yield f"data: {json.dumps({'type': 'complete', 'sql': sql}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"快速SQL生成失败: {str(e)}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream"
    )


@router.post("/query-tasks")
async def create_query_task(request: Request, body: QueryTaskCreateRequest):
    """创建异步查询任务并返回任务 ID。"""
    try:
        current_user = _get_current_user(request)
        task_info = await query_task_service.create_query_task_for_user(
            body,
            user_id=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(
            data=task_info,
            msg="任务已创建"
        )
    except AuthenticationError as e:
        return business_response(
            msg=e.message,
            code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED,
            status="error"
        )
    except QueryTaskNotFoundError as e:
        return business_response(
            msg=str(e),
            code=ErrorCode.CHATDATA_NOT_FOUND,
            status="error"
        )
    except ValueError as e:
        return business_response(
            msg=str(e),
            code=ErrorCode.CHATDATA_BAD_REQUEST,
            status="error"
        )
    except Exception as e:
        logger.error(f"创建异步查询任务失败: {str(e)}", exc_info=True)
        return business_response(
            msg="创建异步查询任务失败，请稍后重试",
            code=ErrorCode.CHATDATA_SERVICE_ERROR,
            status="error"
        )


@router.get("/query-tasks/{task_id}")
async def get_query_task(task_id: str, request: Request):
    """查询异步任务状态与结果。"""
    try:
        current_user = _get_current_user(request)
        data = await query_task_service.get_query_task_for_user(task_id, user_id=str(current_user.user_id))
        return business_response(
            data=data,
            msg="查询成功"
        )
    except AuthenticationError as e:
        return business_response(
            msg=e.message,
            code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED,
            status="error"
        )
    except QueryTaskNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=chatdata_http_exception_error_code(e.status_code), status="error")
    except Exception as e:
        logger.error(f"查询异步任务失败: {str(e)}", exc_info=True)
        return business_response(
            msg="查询异步任务失败，请稍后重试",
            code=ErrorCode.CHATDATA_SERVICE_ERROR,
            status="error"
        )


@router.post("/query-tasks/{task_id}/cancel")
async def cancel_query_task(task_id: str, request: Request):
    """停止异步查询任务及其 SQL 执行。"""
    try:
        current_user = _get_current_user(request)
        result = await query_task_service.cancel_query_task_for_user(
            task_id,
            user_id=str(current_user.user_id),
            operator_name=str(getattr(current_user, "username", "") or "").strip(),
        )
        return business_response(
            data=result.data,
            msg=result.message
        )
    except AuthenticationError as e:
        return business_response(
            msg=e.message,
            code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED,
            status="error"
        )
    except QueryTaskNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except QueryTaskPermissionDeniedError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_PERMISSION_DENIED, status="error")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=chatdata_http_exception_error_code(e.status_code), status="error")
    except Exception as e:
        logger.error("停止查询任务失败: task_id=%s error=%s", task_id, e, exc_info=True)
        return business_response(msg="停止查询任务失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/execute-sql")
async def execute_sql_direct(request: Request, body: ExecuteSqlRequest):
    """直接执行 SQL（用于 SQL 编辑器模式）。"""
    try:
        current_user = _get_current_user(request)
        body = body.model_copy(update={"user_id": current_user.user_id})
        body = await query_runtime.prepare_sql_request_for_execution(body)
        logger.info(
            "execute-sql request: datasource_id=%s",
            body.datasource_id,
        )
        result_data = await query_runtime.execute_sql_request_with_task(
            body,
            query_type=body.query_type or "sql",
            operator_name=current_user.username,
        )
        if not result_data.get("success"):
            return business_response(
                data=result_data,
                msg=result_data.get("error") or "SQL 执行失败，请稍后重试或联系管理员",
                code=ErrorCode.CHATDATA_SERVICE_ERROR,
                status="error"
            )
        return business_response(data=result_data, msg="执行完成")
    except AuthenticationError as e:
        return business_response(
            msg=e.message,
            code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED,
            status="error"
        )
    except ValueError as e:
        return business_response(
            msg=str(e),
            code=ErrorCode.CHATDATA_BAD_REQUEST,
            status="error"
        )
    except Exception as e:
        logger.error(f"执行 SQL 失败: {str(e)}", exc_info=True)
        return business_response(
            msg="SQL 执行失败，请稍后重试或联系管理员",
            code=ErrorCode.CHATDATA_SERVICE_ERROR,
            status="error"
        )


@router.post("/compile-semantic-builder-sql")
async def compile_semantic_builder_sql_endpoint(request: CompileSemanticBuilderSqlRequest):
    """编译语义可视化 DSL 为 SQL（仅预览，不执行）。"""
    try:
        result = await compile_semantic_builder_sql(
            datasource_id=request.datasource_id,
            semantic_config_id=request.semantic_config_id,
            semantic_builder_config=request.semantic_builder_config,
        )
        return business_response(data={"sql": result["sql"], "builder_config": result["builder_config"]}, msg="编译成功")
    except LookupError as err:
        return business_response(msg=str(err), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except ValueError as err:
        return business_response(msg=str(err), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as err:
        logger.error(f"Semantic Builder DSL 编译失败: {err}", exc_info=True)
        return business_response(msg="Semantic Builder DSL 编译失败，请检查配置", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")
