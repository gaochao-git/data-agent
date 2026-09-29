"""智能体定时任务后台管理路由：全局概览、一键暂停、全量任务与执行统计。

入口由 routes.py 的 service token 统一保护；服务端无角色模型，
管理权限由可信代理的前端入口控制（与数据源管理模式一致）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..schema import AgentScheduleEnabledRequest, AgentSchedulePauseRequest
from ..services.agent_schedule.schedule_service import (
    AgentScheduleNotFoundError,
    AgentScheduleValidationError,
    agent_schedule_service,
)
from . import JSON_API_RESPONSES
from .dependencies import _get_current_user

logger = get_logger(__name__)

router = APIRouter(tags=["ChatData 智能体定时任务（Admin）"], responses=JSON_API_RESPONSES)


@router.get("/admin/agent-schedules/overview")
async def get_agent_schedule_overview(request: Request):
    """全局概览：暂停状态、任务总数、执行统计。"""
    _ = request
    try:
        data = await agent_schedule_service.get_admin_overview()
        return business_response(data=data, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取定时任务概览失败: %s", e, exc_info=True)
        return business_response(
            msg="获取定时任务概览失败，请稍后重试", code=ErrorCode.CHATDATA_SCHEDULE_LIST_FAILED, status="error"
        )


@router.put("/admin/agent-schedules/pause")
async def set_agent_schedule_paused(payload: AgentSchedulePauseRequest, request: Request):
    """停止/恢复任务调度（全局开关，不改用户任务的启停状态）。"""
    try:
        current_user = _require_user(request)
        data = await agent_schedule_service.set_paused(
            paused=payload.paused,
            reason=payload.reason or "",
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(data=data, msg="已停止任务调度" if payload.paused else "已恢复任务调度")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("切换定时任务全局暂停失败: %s", e, exc_info=True)
        return business_response(
            msg="切换全局暂停失败，请稍后重试", code=ErrorCode.CHATDATA_SCHEDULE_UPDATE_FAILED, status="error"
        )


@router.get("/admin/agent-schedules")
async def list_agent_schedules_admin(
    request: Request,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
    enabled: bool | None = None,
    expired: bool | None = None,
):
    """全量任务分页列表（跨用户），附执行统计；支持启停/是否过期过滤。"""
    _ = request
    try:
        data = await agent_schedule_service.list_all_jobs(
            keyword=keyword,
            limit=max(1, min(limit, 200)),
            offset=max(0, offset),
            enabled=enabled,
            expired=expired,
        )
        return business_response(data=data, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取全量定时任务失败: %s", e, exc_info=True)
        return business_response(
            msg="获取定时任务列表失败，请稍后重试", code=ErrorCode.CHATDATA_SCHEDULE_LIST_FAILED, status="error"
        )


@router.put("/admin/agent-schedules/{job_id}/enabled")
async def set_agent_schedule_enabled_admin(job_id: str, payload: AgentScheduleEnabledRequest, request: Request):
    """后台强制启停任意任务。"""
    try:
        current_user = _require_user(request)
        data = await agent_schedule_service.admin_set_job_enabled(
            job_id=job_id,
            enabled_flag=payload.enabled_flag,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(data=data, msg="更新成功")
    except AgentScheduleNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SCHEDULE_NOT_FOUND, status="error")
    except AgentScheduleValidationError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SCHEDULE_INVALID, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("后台启停定时任务失败: %s", e, exc_info=True)
        return business_response(
            msg="启停定时任务失败，请稍后重试", code=ErrorCode.CHATDATA_SCHEDULE_UPDATE_FAILED, status="error"
        )


@router.get("/admin/agent-schedules/{job_id}/runs")
async def list_agent_schedule_runs_admin(job_id: str, request: Request, limit: int = 50, offset: int = 0):
    """查看任意任务的执行历史分页。"""
    _ = request
    try:
        data = await agent_schedule_service.admin_list_job_runs(
            job_id=job_id,
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


def _require_user(request: Request) -> Any:
    return _get_current_user(request)
