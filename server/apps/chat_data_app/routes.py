"""
ChatData API 路由定义

提供 ChatData 查询与配置管理相关接口
"""

import asyncio
import json
import re
from typing import Optional, Dict, List, Set, Any, Tuple
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from sqlalchemy import select

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
from .services import dashboard_runtime, query_runtime
from .services.query_history_service import QueryHistoryService, QUERY_TASK_FINAL_STATUSES
from .daos.query_history_dao import QueryHistoryDao
from .services.semantic_builder_compiler import compile_semantic_builder_sql
from .agent import get_all_tables_async, get_tables_create_format
from .agent.sql_assist_graph import get_sql_assist_graph
from utils.database import get_session
from utils import conversation
from utils.models import (
    SqlQueryFavorite,
    ChatDataQueryTask,
    ChatDataQueryExecution,
)
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
import time

logger = get_logger(__name__)
SQL_ASSIST_AGENT_ID = "chat_data_sql_assist"
DATA_ANALYZE_AGENT_ID = "chat_data_data_analyze"
QUERY_TASK_POLL_INTERVAL_MS = 1500
CANCEL_TARGET_WAIT_SECONDS = 3
CANCEL_TARGET_POLL_INTERVAL_SECONDS = 0.1
_query_task_background_tasks: set[asyncio.Task] = set()
query_history_service = QueryHistoryService(QueryHistoryDao(session_context=lambda: get_session()))


# 代理标识
AGENT_ID = "chat_data"

# 创建路由器
router = APIRouter(
    prefix="/api/chatdata",
    tags=["ChatData NL2SQL"]
)
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


def _normalize_query_history_source_filter(source: Optional[str]) -> Optional[List[str]]:
    """规整查询历史来源筛选参数。"""
    return QueryHistoryService.normalize_source_filter(source)


def _normalize_query_history_status_filter(status: Optional[str]) -> Optional[str]:
    """规整查询历史执行状态筛选参数。"""
    return QueryHistoryService.normalize_status_filter(status)


def _clean_optional_text(value: Optional[str]) -> str:
    """规整可选文本筛选条件。"""
    return QueryHistoryService.clean_optional_text(value)


def _get_query_history_source(query_type: Optional[str]) -> str:
    """将查询类型映射为前端展示来源。"""
    return QueryHistoryService.get_history_source(query_type)


def _build_query_history_payload(item: ChatDataQueryExecution, task: Optional[ChatDataQueryTask]) -> Dict[str, Any]:
    """构造查询历史列表展示数据，不包含大结果集。"""
    return QueryHistoryService.build_history_payload(item, task)


def _is_http_query_execution(execution: ChatDataQueryExecution) -> bool:
    """判断执行明细是否为 HTTP 远端查询。"""
    return QueryHistoryService.is_http_query_execution(execution)


def _is_http_execution_target(execution_target: Dict[str, Any]) -> bool:
    """判断执行目标是否走 HTTP 查询。"""
    return str((execution_target or {}).get("query_executor_type") or "").strip().lower() == "http"


def _resolve_remote_history_source_type(execution: ChatDataQueryExecution, task: Optional[ChatDataQueryTask]) -> str:
    """从任务远端映射里解析历史 HTTP 查询的数据源类型。"""
    return QueryHistoryService.resolve_remote_history_source_type(execution, task)


async def _build_remote_history_result_payload(execution: ChatDataQueryExecution, task: Optional[ChatDataQueryTask] = None) -> Optional[Dict[str, Any]]:
    """按远端任务 ID 实时读取历史查询结果。"""
    return await query_history_service.build_remote_history_result_payload(
        execution,
        task,
        fetch_remote_result=fetch_http_query_task_result,
    )


def _get_sql_dialect_label(source_type: Optional[str]) -> str:
    """将数据源类型映射为提示词使用的 SQL 方言标签。"""
    normalized_type = str(source_type or "").strip().lower()
    if normalized_type == "hive":
        return "Hive SQL"
    return "MySQL"


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
        async with get_session() as db:
            stmt = select(SqlQueryFavorite).filter_by(datasource_id=datasource_id, delete_flag=0)
            stmt = stmt.filter_by(user_id=user_id)
            if query_type:
                stmt = stmt.filter(SqlQueryFavorite.query_type == query_type)
            stmt = stmt.order_by(SqlQueryFavorite.update_time.desc(), SqlQueryFavorite.create_time.desc()).limit(limit)
            result = await db.execute(stmt)
            favorites = result.scalars().all()
            results = [q.to_dict() for q in favorites]
            return business_response(data={"queries": results, "total": len(results)}, msg="获取成功")
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
        result = await _cancel_running_query_history(
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
        result = await _cancel_running_query_history(
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
        if payload.query_type == "builder" and payload.builder_config is None:
            return business_response(msg="builder 类型收藏必须包含 builder_config", code=ErrorCode.CHATDATA_INVALID_FAVORITE_CONFIG, status="error")

        builder_config_json = None
        if payload.builder_config is not None:
            builder_config_json = json.dumps(payload.builder_config, ensure_ascii=False)

        now = datetime.now()
        async with get_session() as db:
            item = SqlQueryFavorite(
                datasource_id=payload.datasource_id,
                user_id=user_id,
                semantic_config_id=payload.semantic_config_id,
                query_type=payload.query_type,
                favorite_title=payload.favorite_title.strip(),
                query_text=payload.query_text,
                builder_config=builder_config_json,
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=user_id,
                create_by_name=username,
                update_by=user_id,
                update_by_name=username,
            )
            db.add(item)
            await db.flush()
            return business_response(data=item.to_dict(), msg="收藏成功")
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
        async with get_session() as db:
            stmt = select(SqlQueryFavorite).filter_by(id=favorite_id, user_id=user_id, delete_flag=0)
            result = await db.execute(stmt)
            item = result.scalar_one_or_none()
            if item is None:
                return business_response(msg="收藏不存在", code=ErrorCode.CHATDATA_FAVORITE_NOT_FOUND, status="error")

            if payload.favorite_title is not None:
                item.favorite_title = payload.favorite_title.strip()

            if item.query_type == "builder":
                # builder 收藏原始配置不可编辑
                if payload.query_text is not None or payload.builder_config is not None:
                    return business_response(msg="builder 收藏不支持编辑原始内容", code=ErrorCode.CHATDATA_OPERATION_NOT_ALLOWED, status="error")
            else:
                if payload.query_text is not None:
                    item.query_text = payload.query_text
                if payload.builder_config is not None:
                    item.builder_config = json.dumps(payload.builder_config, ensure_ascii=False)

            if payload.semantic_config_id is not None:
                item.semantic_config_id = payload.semantic_config_id

            item.update_time = datetime.now()
            item.update_by = user_id
            item.update_by_name = username
            await db.flush()
            return business_response(data=item.to_dict(), msg="更新成功")
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
        async with get_session() as db:
            stmt = select(SqlQueryFavorite).filter_by(id=favorite_id, user_id=user_id, delete_flag=0)
            result = await db.execute(stmt)
            item = result.scalar_one_or_none()
            if item is None:
                return business_response(msg="收藏不存在", code=ErrorCode.CHATDATA_FAVORITE_NOT_FOUND, status="error")
            item.delete_flag = 1
            item.update_time = datetime.now()
            item.update_by = user_id
            item.update_by_name = username
            await db.flush()
            return business_response(data={"id": favorite_id}, msg="删除成功")
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
            sql_dialect = _get_sql_dialect_label((datasource or {}).get("source_type"))

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
                    sql_dialect=sql_dialect,
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


async def _execute_sql_for_member(
    execution_target: Dict[str, Any],
    sql: str,
    limit: int,
    timeout: Optional[int],
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    query_task_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    skip_query_cache: bool = False,
    datasource_id: Optional[str] = None,
) -> Dict[str, Any]:
    """执行单个成员数据源 SQL。"""
    return await query_runtime.execute_sql_for_member(
        execution_target=execution_target,
        sql=sql,
        limit=limit,
        timeout=timeout,
        user_id=user_id,
        username=username,
        query_task_id=query_task_id,
        execution_id=execution_id,
        skip_query_cache=skip_query_cache,
        datasource_id=datasource_id,
    )


def _normalize_sql_cache_text(sql: str) -> str:
    """归一化 SQL 缓存键文本。"""
    return query_runtime.normalize_sql_cache_text(sql)


def _build_sql_result_cache_identity(execution_target: Dict[str, Any], effective_sql: str, datasource_id: Optional[str] = None) -> Dict[str, Any]:
    """构造 SQL 结果缓存身份字段。"""
    return query_runtime.build_sql_result_cache_identity(execution_target, effective_sql, datasource_id=datasource_id)


def _is_sql_result_cache_enabled(skip_query_cache: bool) -> bool:
    """判断本次 SQL 执行是否允许使用结果缓存。"""
    return query_runtime.is_sql_result_cache_enabled(skip_query_cache)


async def _get_sql_result_cache(cache_identity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """读取未过期的 SQL 查询结果缓存。"""
    return await query_runtime.get_sql_result_cache(cache_identity)


async def _save_sql_result_cache(cache_identity: Dict[str, Any], result: Dict[str, Any], operator_id: Optional[str] = None, operator_name: Optional[str] = None) -> None:
    """保存成功的 SQL 查询结果缓存。"""
    await query_runtime.save_sql_result_cache(cache_identity, result, operator_id=operator_id, operator_name=operator_name)




def _get_execution_id_by_member(execution_map: Dict[str, str], execution_target: Dict[str, Any]) -> Optional[str]:
    """根据成员数据源获取执行记录ID。"""
    return query_runtime.get_execution_id_by_member(execution_map, execution_target)


async def _create_query_executions_for_task(task_id: str, request: ExecuteSqlRequest, operator_name: Optional[str] = None) -> Dict[str, str]:
    """为任务预创建每个成员数据源的 SQL 执行记录。"""
    return await query_runtime.create_query_executions_for_task(task_id, request, operator_name=operator_name)




async def _update_query_execution(
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
    await query_runtime.update_query_execution(
        execution_id,
        sql_status=sql_status,
        remote_task_id=remote_task_id,
        db_connection_id=db_connection_id,
        execution_time_ms=execution_time_ms,
        result_row_count=result_row_count,
        result_payload=result_payload,
        error_message=error_message,
        started_time=started_time,
        finished_time=finished_time,
    )


async def _update_query_execution_from_result(execution_id: str, result: Dict[str, Any], store_result_payload: bool = True) -> None:
    """按 SQL 执行结果更新执行明细。"""
    await query_runtime.update_query_execution_from_result(execution_id, result, store_result_payload=store_result_payload)


async def _build_execute_sql_result_data(
    request: ExecuteSqlRequest,
    query_task_id: Optional[str] = None,
    execution_map: Optional[Dict[str, str]] = None,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    """执行 SQL 并返回统一结果数据。"""
    return await query_runtime.build_execute_sql_result_data(
        request,
        query_task_id=query_task_id,
        execution_map=execution_map,
        username=username,
    )


def _build_query_task_runtime_data(task: ChatDataQueryTask) -> Dict[str, Any]:
    """将任务模型转换为可脱离 session 使用的字典。"""
    return query_runtime.build_query_task_runtime_data(task)


def _load_remote_tasks(raw_payload: Any) -> List[Dict[str, Any]]:
    """解析远端任务映射列表。"""
    return query_runtime.load_remote_tasks(raw_payload)


async def _are_all_sql_execution_targets_http(request: ExecuteSqlRequest) -> bool:
    """判断 SQL 任务选中的执行对象是否全部为 HTTP 执行。"""
    return await query_runtime.are_all_sql_execution_targets_http(request)


async def _get_http_execution_member_ids(request: ExecuteSqlRequest) -> Set[str]:
    """读取本次 SQL 任务中走 HTTP 的成员数据源 ID。"""
    return await query_runtime.get_http_execution_member_ids(request)


async def _build_persistent_query_task_result_for_request(request: ExecuteSqlRequest, result_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """按执行配置构建任务落库结果，HTTP 结果不落库。"""
    return await query_runtime.build_persistent_query_task_result_for_request(request, result_data)


def _build_persistent_query_task_result_payload(result_data: Dict[str, Any], http_member_ids: Set[str]) -> Optional[Dict[str, Any]]:
    """构建可落库的任务结果，过滤 HTTP 返回数据。"""
    return query_runtime.build_persistent_query_task_result_payload(result_data, http_member_ids)


def _build_cancelled_execution_map(executions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """按成员数据源整理本地已取消的执行记录。"""
    cancelled_map: Dict[str, Dict[str, Any]] = {}
    for execution in executions:
        status = str((execution or {}).get("sql_status") or "").strip().lower()
        member_datasource_id = str((execution or {}).get("member_datasource_id") or "").strip()
        if member_datasource_id and status in {"cancelled", "kill_failed"}:
            cancelled_map[member_datasource_id] = execution
    return cancelled_map


def _build_execution_map_by_member(executions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """按成员数据源整理本地执行记录。"""
    execution_map: Dict[str, Dict[str, Any]] = {}
    for execution in executions:
        member_datasource_id = str((execution or {}).get("member_datasource_id") or "").strip()
        if member_datasource_id:
            execution_map[member_datasource_id] = execution
    return execution_map


def _resolve_member_execution_time_ms(remote_result: Dict[str, Any], local_execution: Optional[Dict[str, Any]]) -> Optional[int]:
    """解析成员远端结果耗时，远端缺失时使用本地执行记录兜底。"""
    if not local_execution:
        return query_runtime.resolve_execution_time_ms((remote_result or {}).get("execution_time_ms"))
    return query_runtime.resolve_execution_time_ms(
        (remote_result or {}).get("execution_time_ms") if (remote_result or {}).get("execution_time_ms") is not None else local_execution.get("execution_time_ms"),
        local_execution.get("started_time"),
        local_execution.get("finished_time"),
    )


async def _build_live_remote_sql_result(response_data: Dict[str, Any], executions: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """基于远端 HTTP 任务实时拉取 SQL 结果。"""
    remote_tasks = list(response_data.get("remote_tasks") or [])
    if not remote_tasks:
        return None
    local_execution_map = _build_execution_map_by_member(executions or [])
    cancelled_execution_map = _build_cancelled_execution_map(executions or [])
    request_payload = response_data.get("request_payload") or {}
    remote_user_id = str((request_payload or {}).get("user_id") or "").strip()
    remote_username = str((request_payload or {}).get("operator_name") or "").strip()

    remote_member_results = await asyncio.gather(*[
        fetch_http_query_task_result(
            str(remote_task.get("remote_task_id") or ""),
            str(remote_task.get("source_type") or "hive"),
            user_id=remote_user_id,
            username=remote_username,
        )
        for remote_task in remote_tasks
    ])

    remote_results: List[Dict[str, Any]] = []
    for remote_task, remote_result in zip(remote_tasks, remote_member_results):
        member_datasource_id = str(remote_task.get("member_datasource_id") or "").strip()
        local_execution = local_execution_map.get(member_datasource_id)
        local_cancelled_execution = cancelled_execution_map.get(member_datasource_id)
        if local_cancelled_execution:
            local_status = str(local_cancelled_execution.get("sql_status") or "cancelled").strip().lower()
            remote_results.append({
                "member_datasource_id": remote_task.get("member_datasource_id"),
                "member_name": remote_task.get("member_name"),
                "sql": ((response_data.get("request_payload") or {}).get("sql") or ""),
                "success": False,
                "status": local_status,
                "columns": [],
                "data": [],
                "total_count": 0,
                "execution_time_ms": _resolve_member_execution_time_ms(remote_result, local_cancelled_execution),
                "error": local_cancelled_execution.get("error_message") or ("已人工取消" if local_status == "cancelled" else "停止执行失败"),
            })
            continue
        remote_results.append({
            "member_datasource_id": remote_task.get("member_datasource_id"),
            "member_name": remote_task.get("member_name"),
            "sql": ((response_data.get("request_payload") or {}).get("sql") or ""),
            "success": bool(remote_result.get("success")),
            "status": remote_result.get("status"),
            "columns": remote_result.get("columns") or [],
            "data": remote_result.get("data") or [],
            "total_count": int(remote_result.get("total_count") or 0),
            "execution_time_ms": _resolve_member_execution_time_ms(remote_result, local_execution),
            "error": remote_result.get("error"),
        })

    local_result_payload = response_data.get("result_payload")
    local_results = []
    if isinstance(local_result_payload, dict):
        local_results = list(local_result_payload.get("results") or [])
    remote_member_ids = {
        str(item.get("member_datasource_id") or "").strip()
        for item in remote_tasks
        if str(item.get("member_datasource_id") or "").strip()
    }
    direct_results = [
        item for item in local_results
        if str((item or {}).get("member_datasource_id") or "").strip() not in remote_member_ids
    ]
    merged_results = direct_results + remote_results

    statuses = {str(item.get("status") or "").strip().lower() for item in remote_results}
    if "pending" in statuses or "running" in statuses:
        task_status = "running"
        progress_text = "SQL 执行中"
    elif statuses and statuses <= {"cancelled"}:
        task_status = "cancelled"
        progress_text = "已停止"
    elif any(item.get("success") for item in merged_results):
        task_status = "succeeded"
        progress_text = "执行完成"
    else:
        task_status = "failed"
        progress_text = "执行失败"

    primary_result = next((item for item in merged_results if item.get("success")), merged_results[0] if merged_results else {})
    successful_results = [item for item in merged_results if item.get("success")]
    if task_status == "running":
        error_message = None
    elif successful_results:
        error_message = None
    else:
        error_messages = [
            str(item.get("error") or "").strip()
            for item in merged_results
            if str(item.get("error") or "").strip()
        ]
        error_message = "；".join(error_messages) if error_messages else "所有执行对象均执行失败"

    result_success = bool(successful_results) and task_status != "running"
    return {
        "status": task_status,
        "progress_text": progress_text,
        "error_message": error_message,
        "result_payload": {
            "success": result_success,
            "sql": primary_result.get("sql") or ((response_data.get("request_payload") or {}).get("sql") or ""),
            "columns": primary_result.get("columns") or [],
            "data": primary_result.get("data") or [],
            "total_count": int(primary_result.get("total_count") or 0),
            "execution_time_ms": primary_result.get("execution_time_ms"),
            "member_datasource_id": primary_result.get("member_datasource_id"),
            "member_name": primary_result.get("member_name"),
            "error": error_message,
            "results": merged_results,
        },
    }


def _build_query_task_response_data(task_data: Dict[str, Any]) -> Dict[str, Any]:
    """将任务运行态字典转换为接口返回结构。"""
    return query_runtime.build_query_task_response_data(task_data)


async def _get_query_executions(task_id: str) -> List[Dict[str, Any]]:
    """读取任务下的 SQL 执行明细。"""
    return await query_runtime.get_query_executions(task_id)


async def _get_query_execution_models(task_id: str) -> List[ChatDataQueryExecution]:
    """读取任务下的 SQL 执行模型。"""
    return await query_history_service.get_execution_models(task_id)


async def _get_query_execution_model(execution_id: str) -> Optional[ChatDataQueryExecution]:
    """按执行ID读取 SQL 执行模型。"""
    return await query_history_service.get_execution_model(execution_id)


def _get_task_user_id(task_data: Dict[str, Any]) -> str:
    """从任务请求负载中解析发起用户ID。"""
    return QueryHistoryService.task_user_id(task_data)


def _is_query_task_owned_by_user(task_data: Dict[str, Any], executions: List[Dict[str, Any]], user_id: str) -> bool:
    """判断查询任务是否归属于当前用户。"""
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return False
    owner_ids = {
        str(item.get("user_id") or item.get("create_by") or item.get("update_by") or "").strip()
        for item in executions or []
        if str(item.get("user_id") or item.get("create_by") or item.get("update_by") or "").strip()
    }
    task_user_id = _get_task_user_id(task_data)
    if task_user_id:
        owner_ids.add(task_user_id)
    return not owner_ids or normalized_user_id in owner_ids


def _get_task_operator_name(task_data: Dict[str, Any]) -> str:
    """从任务请求负载中解析发起用户名。"""
    return QueryHistoryService.task_operator_name(task_data)


async def _resolve_execution_target(execution: ChatDataQueryExecution) -> Optional[Dict[str, Any]]:
    """根据执行记录解析实际成员数据源配置。"""
    ds_manager = get_datasource_manager()
    member_ids = [execution.member_datasource_id] if execution.member_datasource_id else None
    targets = await ds_manager.get_execution_datasources_async(
        execution.datasource_id,
        member_ids,
    )
    return targets[0] if targets else None


def _copy_execution_identity(source: ChatDataQueryExecution, target: ChatDataQueryExecution) -> None:
    """复制执行记录中的可停止标识。"""
    QueryHistoryService.copy_execution_identity(source, target)


async def _wait_for_cancel_target(execution: ChatDataQueryExecution) -> ChatDataQueryExecution:
    """短暂等待执行记录写入远端任务ID或数据库连接ID。"""
    if execution.remote_task_id or execution.db_connection_id or str(execution.sql_status or "").strip().lower() != "running":
        return execution
    deadline = time.monotonic() + CANCEL_TARGET_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(CANCEL_TARGET_POLL_INTERVAL_SECONDS)
        latest = await _get_query_execution_model(execution.execution_id)
        if latest is None:
            return execution
        _copy_execution_identity(execution, latest)
        if execution.remote_task_id or execution.db_connection_id or str(execution.sql_status or "").strip().lower() != "running":
            return execution
    return execution


async def _cancel_query_execution(execution: ChatDataQueryExecution, *, cleanup_without_target: bool = False, operator_name: Optional[str] = None) -> Dict[str, Any]:
    """停止一条 SQL 执行记录对应的远端任务或直连查询。"""
    current_status = str(execution.sql_status or "").strip().lower()
    if current_status not in {"pending", "running"}:
        return {
            "execution_id": execution.execution_id,
            "status": current_status,
            "success": True,
            "message": "无需停止",
        }
    target = None
    stop_result: Dict[str, Any]
    execution = await _wait_for_cancel_target(execution)
    current_status = str(execution.sql_status or "").strip().lower()
    if current_status not in {"pending", "running"}:
        return {
            "execution_id": execution.execution_id,
            "status": current_status,
            "success": True,
            "message": "无需停止",
        }
    if execution.remote_task_id:
        try:
            target = await _resolve_execution_target(execution)
        except Exception as err:
            logger.warning("解析 HTTP 停止数据源失败: execution_id=%s error=%s", execution.execution_id, err)
        source_type = str((target or {}).get("source_type") or "hive").strip().lower() or "hive"
        stop_result = await stop_http_query_task(
            execution.remote_task_id,
            source_type,
            user_id=str(execution.user_id or "").strip(),
            username=str(operator_name or "").strip(),
        )
    elif execution.db_connection_id:
        try:
            target = await _resolve_execution_target(execution)
        except Exception as err:
            logger.error("解析 MySQL 停止数据源失败: execution_id=%s error=%s", execution.execution_id, err, exc_info=True)
            stop_result = {"success": False, "error": "解析执行数据源失败"}
        else:
            stop_result = await kill_mysql_query(target or {}, execution.db_connection_id)
    elif current_status == "pending" or cleanup_without_target:
        stop_result = {"success": True, "error": None}
    else:
        stop_result = {"success": False, "error": "缺少可停止的远端任务ID或数据库连接ID"}

    success = bool(stop_result.get("success"))
    await _update_query_execution(
        execution.execution_id,
        sql_status=("cancelled" if success else "kill_failed"),
        error_message=("已人工取消" if success else str(stop_result.get("error") or "停止执行失败")),
        finished_time=datetime.now(),
    )
    return {
        "execution_id": execution.execution_id,
        "status": "cancelled" if success else "kill_failed",
        "success": success,
        "message": None if success else str(stop_result.get("error") or "停止执行失败"),
    }


def _build_cancel_task_status(execution_results: List[Dict[str, Any]]) -> str:
    """根据执行停止结果汇总任务状态。"""
    return QueryHistoryService.build_cancel_task_status(execution_results)


async def _list_running_query_executions(
    *,
    datasource_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> List[Tuple[ChatDataQueryExecution, Optional[ChatDataQueryTask]]]:
    """读取待批量停止的 SQL 执行记录。"""
    return await query_history_service.dao.list_running_executions(
        running_statuses={"pending", "running"},
        datasource_id=_clean_optional_text(datasource_id),
        user_id=_clean_optional_text(user_id),
    )


def _group_cancel_results_by_task(rows: List[Tuple[ChatDataQueryExecution, Optional[ChatDataQueryTask]]], execution_results: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """按任务归并停止结果。"""
    return QueryHistoryService.group_cancel_results_by_task(rows, execution_results)


def _get_execution_cancel_operator_name(
    execution: ChatDataQueryExecution,
    task: Optional[ChatDataQueryTask],
    fallback: Optional[str],
) -> str:
    """解析停止远端任务时使用的用户名称。"""
    return QueryHistoryService.execution_cancel_operator_name(execution, task, fallback)


async def _cancel_query_execution_for_batch(
    execution: ChatDataQueryExecution,
    *,
    operator_name: Optional[str] = None,
) -> Dict[str, Any]:
    """批量停止场景下保护单条异常，避免一条失败中断整批。"""
    return await query_history_service.cancel_query_execution_for_batch(
        execution,
        cancel_execution=_cancel_query_execution,
        update_execution=_update_query_execution,
        operator_name=operator_name,
    )


async def _cancel_running_query_history(
    *,
    datasource_id: Optional[str] = None,
    user_id: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> Dict[str, Any]:
    """批量停止执行中的 SQL，并同步更新关联任务状态。"""
    return await query_history_service.cancel_running_query_history(
        datasource_id=datasource_id,
        user_id=user_id,
        operator_name=operator_name,
        cancel_execution=_cancel_query_execution,
        update_execution=_update_query_execution,
        update_task=_update_query_task,
    )


async def _create_task_record(
    *,
    task_type: str,
    datasource_id: str,
    query_type: str,
    request_payload: Dict[str, Any],
    progress_text: str = "任务已创建，等待执行",
) -> Dict[str, Any]:
    """创建异步任务记录。"""
    return await query_runtime.create_task_record(
        task_type=task_type,
        datasource_id=datasource_id,
        query_type=query_type,
        request_payload=request_payload,
        progress_text=progress_text,
    )


async def _create_query_task(payload: QueryTaskCreateRequest) -> Dict[str, Any]:
    """创建 SQL 异步查询任务。"""
    return await query_runtime.create_query_task(payload)


async def _prepare_sql_request_for_execution(request: ExecuteSqlRequest | QueryTaskCreateRequest, datasource: Optional[Dict[str, Any]] = None):
    """保留 SQL 原文；安全校验统一在执行器边界完成。"""
    return await query_runtime.prepare_sql_request_for_execution(request, datasource)


async def _create_task_for_execute_request(
    request: ExecuteSqlRequest,
    *,
    query_type: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> Dict[str, Any]:
    """为直接执行或智能体执行创建任务主记录。"""
    return await query_runtime.create_task_for_execute_request(
        request,
        query_type=query_type,
        operator_name=operator_name,
    )


async def _get_query_task(task_id: str) -> Optional[Dict[str, Any]]:
    """读取异步查询任务。"""
    return await query_runtime.get_query_task(task_id)


async def _is_query_task_cancelled(task_id: str) -> bool:
    """判断异步查询任务是否已取消。"""
    return await query_runtime.is_query_task_cancelled(task_id)


async def _get_query_execution_status(execution_id: str) -> str:
    """读取 SQL 执行明细当前状态。"""
    return await query_runtime.get_query_execution_status(execution_id)


async def _append_query_task_remote_task(task_id: str, remote_task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """向异步任务中追加远端 HTTP 任务映射。"""
    return await query_runtime.append_query_task_remote_task(task_id, remote_task)


async def _update_query_task(
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
    return await query_runtime.update_query_task(
        task_id,
        status=status,
        progress_text=progress_text,
        result_payload=result_payload,
        error_message=error_message,
        started_time=started_time,
        finished_time=finished_time,
    )


async def _run_sql_query_task(task_id: str, task: Dict[str, Any]) -> None:
    """后台执行 SQL 查询任务。"""
    await query_runtime.run_sql_query_task(task_id, task)




async def _run_query_task(task_id: str) -> None:
    """后台执行查询任务。"""
    task = await _get_query_task(task_id)
    if not task:
        logger.warning("异步查询任务不存在: %s", task_id)
        return
    try:
        if task.get("task_type") == "sql":
            await _run_sql_query_task(task_id, task)
            return
        if task.get("task_type") == "dashboard_panel":
            await dashboard_runtime.run_dashboard_panel_task(task_id, task)
            return
        if task.get("task_type") == "dashboard_batch":
            await dashboard_runtime.run_dashboard_batch_task(task_id, task)
            return
        raise ValueError(f"不支持的任务类型: {task.get('task_type')}")
    except ValueError as e:
        logger.error("异步查询任务失败: %s", e)
        await _update_query_task(
            task_id,
            status="failed",
            progress_text="执行失败",
            error_message=str(e),
            finished_time=datetime.now(),
        )
    except Exception as e:
        logger.error("异步查询任务异常: %s", e, exc_info=True)
        await _update_query_task(
            task_id,
            status="failed",
            progress_text="执行失败",
            error_message="执行失败，请稍后重试或联系管理员",
            finished_time=datetime.now(),
        )


def _schedule_query_task(task_id: str) -> None:
    """提交后台异步查询任务。"""
    background_task = asyncio.create_task(_run_query_task(task_id))
    _query_task_background_tasks.add(background_task)
    background_task.add_done_callback(_query_task_background_tasks.discard)


dashboard_runtime.configure_dashboard_runtime(dashboard_runtime.DashboardRuntime(
    get_datasource_manager=lambda: get_datasource_manager(),
    execute_sql_for_member=lambda **kwargs: _execute_sql_for_member(**kwargs),
    create_task_record=lambda **kwargs: _create_task_record(**kwargs),
    update_query_task=lambda *args, **kwargs: _update_query_task(*args, **kwargs),
    is_query_task_cancelled=lambda task_id: _is_query_task_cancelled(task_id),
    schedule_query_task=lambda task_id: _schedule_query_task(task_id),
    query_task_poll_interval_ms=QUERY_TASK_POLL_INTERVAL_MS,
))


@router.post("/query-tasks")
async def create_query_task(request: Request, body: QueryTaskCreateRequest):
    """创建异步查询任务并返回任务 ID。"""
    try:
        current_user = _get_current_user(request)
        body = body.model_copy(update={"user_id": current_user.user_id, "operator_name": current_user.username})
        logger.info(
            "query-tasks create: datasource_id=%s",
            body.datasource_id,
        )
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(body.datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {body.datasource_id}",
                code=ErrorCode.CHATDATA_NOT_FOUND,
                status="error"
            )
        body = await _prepare_sql_request_for_execution(body, datasource)
        task_info = await _create_query_task(body)
        _schedule_query_task(task_info["task_id"])
        return business_response(
            data={
                "task_id": task_info["task_id"],
                "task_type": task_info["task_type"],
                "status": task_info["status"],
                "poll_interval_ms": QUERY_TASK_POLL_INTERVAL_MS,
            },
            msg="任务已创建"
        )
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
        current_user_id = str(current_user.user_id)
        task_data = await _get_query_task(task_id)
        if not task_data:
            return business_response(
                msg=f"任务不存在: {task_id}",
                code=ErrorCode.CHATDATA_NOT_FOUND,
                status="error"
            )
        response_data = _build_query_task_response_data(task_data)
        executions = await _get_query_executions(response_data["task_id"])
        if not _is_query_task_owned_by_user(task_data, executions, current_user_id):
            return business_response(msg=f"任务不存在: {task_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        if response_data["task_type"] == "sql" and response_data["remote_tasks"]:
            live_remote_payload = await _build_live_remote_sql_result(response_data, executions)
            if live_remote_payload:
                response_data = {
                    **response_data,
                    "status": live_remote_payload["status"],
                    "progress_text": live_remote_payload["progress_text"],
                    "error_message": live_remote_payload["error_message"],
                    "result_payload": live_remote_payload["result_payload"],
                }
        result_payload = response_data["result_payload"]
        task_type = response_data["task_type"]
        return business_response(
            data={
                "task_id": response_data["task_id"],
                "task_type": task_type,
                "datasource_id": response_data["datasource_id"],
                "query_type": response_data["query_type"],
                "status": response_data["status"],
                "progress_text": response_data["progress_text"],
                "remote_tasks": response_data["remote_tasks"],
                "executions": executions,
                "result": (result_payload if task_type != "sql" else None),
                "results": ((result_payload or {}).get("results") if task_type == "sql" and isinstance(result_payload, dict) else None),
                "error_message": response_data["error_message"],
                "started_time": response_data["started_time"],
                "finished_time": response_data["finished_time"],
                "create_time": response_data["create_time"],
                "update_time": response_data["update_time"],
            },
            msg="查询成功"
        )
    except AuthenticationError as e:
        return business_response(
            msg=e.message,
            code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED,
            status="error"
        )
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
        current_user_id = str(current_user.user_id)
        task_data = await _get_query_task(task_id)
        if not task_data:
            return business_response(msg=f"任务不存在: {task_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        executions = await _get_query_execution_models(task_id)
        owner_ids = {str(item.user_id or "").strip() for item in executions if str(item.user_id or "").strip()}
        if not owner_ids:
            task_user_id = _get_task_user_id(task_data)
            if task_user_id:
                owner_ids.add(task_user_id)
        if owner_ids and current_user_id not in owner_ids:
            return business_response(msg="无权停止该查询任务", code=ErrorCode.CHATDATA_PERMISSION_DENIED, status="error")
        operator_name = _get_task_operator_name(task_data) or str(getattr(current_user, "username", "") or "").strip()
        cancellable_executions = [
            item for item in executions
            if str(item.sql_status or "").strip().lower() in {"pending", "running"}
        ]
        task_status = str(task_data.get("status") or "").strip().lower()
        if task_status in QUERY_TASK_FINAL_STATUSES and not cancellable_executions:
            return business_response(
                data={
                    "task_id": task_id,
                    "status": task_data.get("status"),
                    "executions": [item.to_dict() for item in executions],
                },
                msg="任务已结束"
            )
        cleanup_without_target = task_status in QUERY_TASK_FINAL_STATUSES
        execution_results = await asyncio.gather(*[
            _cancel_query_execution(item, cleanup_without_target=cleanup_without_target, operator_name=operator_name)
            for item in (cancellable_executions or executions)
        ])
        cancel_status = _build_cancel_task_status(execution_results)
        if task_status not in QUERY_TASK_FINAL_STATUSES:
            await _update_query_task(
                task_id,
                status=cancel_status,
                progress_text=("已停止" if cancel_status == "cancelled" else "部分停止失败"),
                error_message=(None if cancel_status == "cancelled" else "部分 SQL 停止失败"),
                finished_time=datetime.now(),
            )
        return business_response(
            data={
                "task_id": task_id,
                "status": cancel_status,
                "executions": execution_results,
            },
            msg="停止请求已提交"
        )
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
        body = await _prepare_sql_request_for_execution(body)
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


# ============ 对话接口 ============
# 会话管理（创建、列表、删除、详情、completion）统一由公共接口 /api/chatdata/conversations 处理
# sql_generator 的 completion handler 已在 __init__.py 中注册
