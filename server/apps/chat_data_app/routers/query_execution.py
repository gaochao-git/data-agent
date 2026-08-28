"""SQL 查询执行相关路由。"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status as http_status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from auth import AuthenticationError
from utils import conversation
from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response
from .. import agent as agent_module
from ..agent.sql_assist_agent import create_sql_assist_agent
from ..schema import (
    CompileSemanticBuilderSqlRequest,
    QueryTaskCreateRequest,
    QuickSqlRequest,
)
from ..services.datasource.manager import get_datasource_manager
from ..agent.run.conversation_event_service import conversation_event_service
from ..services.sql_run.task_service import (
    QueryTaskNotFoundError,
    QueryTaskPermissionDeniedError,
    QueryTaskService,
)
from ..services.sql_run.providers import get_query_task_service
from ..services.semantic.run.semantic_query_service import compile_semantic_builder_sql
from ..services.sql_assist_context import get_all_tables_async, get_tables_create_format
from .dependencies import _get_current_user

logger = get_logger(__name__)
from . import JSON_API_RESPONSES, SSE_RESPONSES

router = APIRouter(tags=["ChatData 查询执行"], responses=JSON_API_RESPONSES)
SQL_ASSIST_AGENT_ID = "chat_data_sql_assist"


def _sse_event(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_error(message: str) -> str:
    return _sse_event({"type": "error", "message": message})


def _resolve_current_user(request: Request, current_user: Any | None) -> Any:
    return current_user if current_user is not None else _get_current_user(request)


def _current_user_identity(request: Request | None) -> tuple[str, str]:
    if request is None:
        return "system", "system"
    current_user = _get_current_user(request)
    user_id = str(getattr(current_user, "user_id", None) or getattr(current_user, "username", None) or "system")
    username = str(getattr(current_user, "username", None) or user_id)
    return user_id, username


async def _load_quick_sql_context(
    payload: QuickSqlRequest,
    *,
    datasource_manager: Any | None = None,
) -> tuple[str | None, str | None, str | None]:
    manager = datasource_manager or get_datasource_manager()
    datasource = await manager.get_datasource_async(payload.datasource_id)
    if not datasource:
        return None, None, "数据源不存在"

    tables = await get_all_tables_async(payload.datasource_id)
    if not tables:
        return None, None, "数据源暂无表结构信息"

    table_names = [table.get("table_name") for table in tables]
    return str(datasource["source_type"]), get_tables_create_format(tables, table_names), None


async def _ensure_sql_assist_conversation(payload: QuickSqlRequest, user_id: str) -> str | None:
    existing_agent_id = await conversation.get_agent_id("", payload.conversation_id, user_id)
    if existing_agent_id and existing_agent_id != SQL_ASSIST_AGENT_ID:
        return "对话不存在"

    if not existing_agent_id:
        unowned_agent_id = await conversation.get_agent_id("", payload.conversation_id)
        if unowned_agent_id:
            return "对话不存在"

    await conversation.get_or_create_conversation("", SQL_ASSIST_AGENT_ID, payload.conversation_id, user_id)
    return None


def _build_quick_sql_user_message(payload: QuickSqlRequest) -> HumanMessage:
    return HumanMessage(
        content=payload.query,
        id=str(uuid.uuid4()),
        additional_kwargs={"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
    )


def _extract_sql_from_content(content: str) -> str:
    sql_match = re.search(r"```sql\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
    return sql_match.group(1).strip() if sql_match else content.strip()


async def _persist_quick_sql_assistant_turn(
    *,
    conversation_id: str,
    user_id: str,
    username: str,
    user_message: HumanMessage,
    content: str,
) -> None:
    """幂等保存 SQL 助手已输出的正文，包括异常中断时的部分内容。"""
    if not content:
        return
    turn_id = str(user_message.id or "")
    await conversation_event_service.persist_turn(
        conversation_id=conversation_id,
        user_id=user_id,
        operator_name=username,
        turn_id=turn_id,
        messages=[
            user_message,
            AIMessage(
                content=content,
                id=f"{turn_id}:assistant",
                additional_kwargs={"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
            ),
        ],
        reasoning_segments=[],
    )


async def _persist_quick_sql_assistant_turn_shielded(**kwargs: Any) -> None:
    """客户端取消流时仍等待权威历史写入完成。"""
    task = asyncio.create_task(_persist_quick_sql_assistant_turn(**kwargs))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            logger.exception(
                "SQL 助手流取消后保存部分历史失败: conversation_id=%s",
                kwargs.get("conversation_id"),
            )
        raise


async def _stream_sql_assist_chunks(
    conversation_id: str,
    user_message: HumanMessage,
    *,
    source_type: str,
    tables_ddl_info: str,
):
    config = {"configurable": {"thread_id": conversation_id, "checkpoint_ns": ""}}
    async with agent_module.get_checkpointer() as checkpointer:
        agent = create_sql_assist_agent(
            checkpointer,
            sql_dialect=source_type,
            tables_ddl_info=tables_ddl_info,
        )
        async for chunk, metadata in agent.astream(
            {"messages": [user_message]},
            config=config,
            stream_mode="messages",
        ):
            if metadata.get("langgraph_node") == "model" and getattr(chunk, "content", None):
                yield chunk.content


async def _generate_quick_sql_stream(
    payload: QuickSqlRequest,
    *,
    user_id: str,
    username: str,
    datasource_manager: Any,
):
    try:
        logger.info("收到快速SQL生成请求: %s...", payload.query[:100])
        source_type, tables_ddl_info, context_error = await _load_quick_sql_context(
            payload,
            datasource_manager=datasource_manager,
        )
        if context_error:
            yield _sse_error(context_error)
            return

        if not payload.conversation_id:
            yield _sse_error("缺少 SQL 辅助会话ID")
            return
        conversation_error = await _ensure_sql_assist_conversation(payload, user_id)
        if conversation_error:
            yield _sse_error(conversation_error)
            return

        conversation_id = str(payload.conversation_id or "")
        user_message = _build_quick_sql_user_message(payload)
        await conversation_event_service.persist_user_message(
            conversation_id=conversation_id,
            user_id=user_id,
            operator_name=username,
            message=user_message,
        )
        full_content = ""
        try:
            async for content in _stream_sql_assist_chunks(
                conversation_id=conversation_id,
                user_message=user_message,
                source_type=source_type or "",
                tables_ddl_info=tables_ddl_info or "",
            ):
                full_content += content
                yield _sse_event({"type": "chunk", "content": content})
        finally:
            await _persist_quick_sql_assistant_turn_shielded(
                conversation_id=conversation_id,
                user_id=user_id,
                username=username,
                user_message=user_message,
                content=full_content,
            )
        sql = _extract_sql_from_content(full_content)
        logger.info("生成的 SQL: %s...", sql[:200])
        yield _sse_event({"type": "complete", "sql": sql})
    except Exception as e:
        emit_llm_connection_alarm_if_needed(
            e,
            ext1={"conversation_id": payload.conversation_id, "datasource_id": payload.datasource_id, "purpose": "quick_sql"},
        )
        logger.error("快速SQL生成失败: %s", e, exc_info=True)
        yield _sse_error(str(e))


@router.post("/nl2sql", responses=SSE_RESPONSES)
async def quick_sql_endpoint(payload: QuickSqlRequest, request: Request):
    """快速 SQL 生成接口（流式输出，用于 SQL 编辑器的 AI 辅助）。"""
    current_user = _get_current_user(request)
    user_id = str(current_user.user_id)
    username = str(current_user.username or "")
    datasource_manager = get_datasource_manager()
    if not await datasource_manager.can_access_datasource_async(
        payload.datasource_id,
        user_id=user_id,
        username=username,
    ):
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="无权限访问该数据源")
    return StreamingResponse(
        _generate_quick_sql_stream(
            payload,
            user_id=user_id,
            username=username,
            datasource_manager=datasource_manager,
        ),
        media_type="text/event-stream",
    )


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
