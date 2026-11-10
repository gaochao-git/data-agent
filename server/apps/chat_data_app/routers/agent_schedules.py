"""智能体定时任务路由：任务 CRUD 与执行历史查询。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..agent.run.agent_schedule_runtime import agent_schedule_runtime
from ..schema import AgentScheduleCreateRequest, AgentScheduleUpdateRequest
from ..services.agent_schedule.schedule_service import (
    AgentScheduleConversationInvalidError,
    AgentScheduleLimitExceededError,
    AgentScheduleNotFoundError,
    AgentScheduleValidationError,
    ScheduleFieldsUpdate,
    UpdateOperator,
    agent_schedule_service,
)
from .dependencies import _get_current_user

logger = get_logger(__name__)
from . import JSON_API_RESPONSES

router = APIRouter(tags=["ChatData 智能体定时任务"], responses=JSON_API_RESPONSES)


def _resolve_current_user(request: Request) -> Any:
    return _get_current_user(request)


@router.get("/agent-schedules")
async def list_agent_schedules(request: Request, limit: int = 50, offset: int = 0) -> JSONResponse:
    """获取当前用户的定时任务分页列表。"""
    try:
        current_user = _resolve_current_user(request)
        data = await agent_schedule_service.list_jobs(
            user_id=str(current_user.user_id),
            limit=max(1, min(limit, 200)),
            offset=max(0, offset),
        )
        return business_response(data=data, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取定时任务列表失败: %s", e, exc_info=True)
        return business_response(
            msg="获取定时任务列表失败，请稍后重试", code=ErrorCode.CHATDATA_SCHEDULE_LIST_FAILED, status="error"
        )


@router.post("/agent-schedules")
async def create_agent_schedule(payload: AgentScheduleCreateRequest, request: Request) -> JSONResponse:
    """创建定时任务。"""
    try:
        current_user = _resolve_current_user(request)
        data = await agent_schedule_service.create_job(
            title=payload.title,
            prompt=payload.prompt,
            conversation_id=payload.conversation_id or "",
            conversation_mode=payload.conversation_mode,
            schedule_type=payload.schedule_type,
            cron_expr=payload.cron_expr,
            plan_time=payload.plan_time,
            effective_end_time=payload.effective_end_time,
            datasource_id=payload.datasource_id,
            options=payload.options,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(data=data, msg="创建成功")
    except AgentScheduleConversationInvalidError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SCHEDULE_CONVERSATION_INVALID, status="error")
    except AgentScheduleValidationError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SCHEDULE_INVALID, status="error")
    except AgentScheduleLimitExceededError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SCHEDULE_LIMIT_EXCEEDED, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("创建定时任务失败: %s", e, exc_info=True)
        return business_response(
            msg="创建定时任务失败，请稍后重试", code=ErrorCode.CHATDATA_SCHEDULE_CREATE_FAILED, status="error"
        )


@router.put("/agent-schedules/{job_id}")
async def update_agent_schedule(job_id: str, payload: AgentScheduleUpdateRequest, request: Request) -> JSONResponse:
    """更新定时任务；调度字段变更后按服务端时钟重新计算下一次触发时间。"""
    try:
        current_user = _resolve_current_user(request)
        data = await agent_schedule_service.update_job(
            job_id=job_id,
            title=payload.title,
            prompt=payload.prompt,
            conversation_mode=payload.conversation_mode,
            conversation_id=payload.conversation_id,
            schedule=ScheduleFieldsUpdate(
                schedule_type=payload.schedule_type,
                cron_expr=payload.cron_expr,
                plan_time=payload.plan_time,
                effective_end_time=payload.effective_end_time,
                # 更新请求里 None 既可能是「未提供」也可能是「显式清空」：只有字段真的
                # 出现在请求体中且为 null 时才视为清除有效期（Pydantic 可区分二者）
                clear_effective_end_time=(
                    "effective_end_time" in payload.model_fields_set and payload.effective_end_time is None
                ),
            ),
            enabled_flag=payload.enabled_flag,
            datasource_id=payload.datasource_id,
            options=payload.options,
            operator=UpdateOperator(
                user_id=str(current_user.user_id),
                username=str(current_user.username or ""),
            ),
        )
        return business_response(data=data, msg="更新成功")
    except AgentScheduleNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SCHEDULE_NOT_FOUND, status="error")
    except AgentScheduleValidationError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SCHEDULE_INVALID, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("更新定时任务失败: %s", e, exc_info=True)
        return business_response(
            msg="更新定时任务失败，请稍后重试", code=ErrorCode.CHATDATA_SCHEDULE_UPDATE_FAILED, status="error"
        )


@router.delete("/agent-schedules/{job_id}")
async def delete_agent_schedule(job_id: str, request: Request) -> JSONResponse:
    """删除定时任务（软删除，同时停止调度）。"""
    try:
        current_user = _resolve_current_user(request)
        data = await agent_schedule_service.delete_job(
            job_id=job_id,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(data=data, msg="删除成功")
    except AgentScheduleNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SCHEDULE_NOT_FOUND, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("删除定时任务失败: %s", e, exc_info=True)
        return business_response(
            msg="删除定时任务失败，请稍后重试", code=ErrorCode.CHATDATA_SCHEDULE_DELETE_FAILED, status="error"
        )


@router.get("/agent-schedules/{job_id}/runs")
async def list_agent_schedule_runs(job_id: str, request: Request, limit: int = 50, offset: int = 0) -> JSONResponse:
    """获取定时任务的执行历史分页（含关联 Agent Run 状态）。"""
    try:
        current_user = _resolve_current_user(request)
        data = await agent_schedule_service.list_job_runs(
            job_id=job_id,
            user_id=str(current_user.user_id),
            limit=max(1, min(limit, 200)),
            offset=max(0, offset),
        )
        return business_response(data=data, msg="获取成功")
    except AgentScheduleNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SCHEDULE_NOT_FOUND, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取定时任务执行历史失败: %s", e, exc_info=True)
        return business_response(
            msg="获取执行历史失败，请稍后重试", code=ErrorCode.CHATDATA_SCHEDULE_HISTORY_FAILED, status="error"
        )


@router.post("/agent-schedules/{job_id}/trigger-now")
async def trigger_agent_schedule_now(job_id: str, request: Request) -> JSONResponse:
    """立即执行：创建手动触发实例并同步尝试派发；会话正忙时转后台队列自动重试。"""
    try:
        current_user = _resolve_current_user(request)
        if not await agent_schedule_runtime.is_executor_allowed():
            return business_response(
                msg="当前节点不执行智能体定时任务，请在 Active 机房操作",
                code=ErrorCode.CHATDATA_SCHEDULE_TRIGGER_FAILED,
                status="error",
            )
        created = await agent_schedule_service.trigger_now(
            job_id=job_id,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        dispatch = await agent_schedule_runtime.dispatch_trigger(created["trigger_id"])
        data = {**created, **dispatch}
        msg = "已触发执行，结果将写入绑定会话" if dispatch["run_id"] else "会话正忙，已加入队列稍后自动执行"
        return business_response(data=data, msg=msg)
    except AgentScheduleNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SCHEDULE_NOT_FOUND, status="error")
    except AgentScheduleValidationError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SCHEDULE_INVALID, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("立即执行定时任务失败: %s", e, exc_info=True)
        return business_response(
            msg="立即执行失败，请稍后重试", code=ErrorCode.CHATDATA_SCHEDULE_TRIGGER_FAILED, status="error"
        )
