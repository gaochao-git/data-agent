"""Feature gates for ChatData runtime capabilities."""

from __future__ import annotations

from fastapi import HTTPException

from utils.error_codes import ErrorCode
from utils.lang_util.conversation_router import AgentAvailabilityResult, get_agent_availability
from utils.response import business_response
from .runtime_config import read_bool_env

DATA_ANALYZE_AGENT_ID = "chat_data_data_analyze"
DATA_ANALYZE_DISABLED_CODE = ErrorCode.CHATDATA_DATA_ANALYZE_DISABLED
DATA_ANALYZE_DISABLED_MESSAGE = "智能分析功能暂未开放"
_FEATURE_DISABLED_MESSAGES = {DATA_ANALYZE_DISABLED_MESSAGE}


def is_data_analyze_enabled() -> bool:
    """读取智能分析功能开关。"""
    return read_bool_env("CHAT_DATA_ANALYZE_ENABLED", False)


def get_data_analyze_availability() -> AgentAvailabilityResult:
    """返回智能分析 agent 可用性。"""
    return AgentAvailabilityResult(
        enabled=is_data_analyze_enabled(),
        message=DATA_ANALYZE_DISABLED_MESSAGE,
        code=DATA_ANALYZE_DISABLED_CODE,
    )


def get_feature_unavailable_response(agent_id: str):
    """构造指定 agent 不可用响应；可用时返回 None。"""
    availability = get_agent_availability(agent_id)
    if availability.enabled:
        return None
    return business_response(
        msg=availability.message or "功能暂未开放",
        code=availability.code,
        status="error",
    )


def get_data_analyze_unavailable_response():
    """构造智能分析不可用响应；可用时返回 None。"""
    return get_feature_unavailable_response(DATA_ANALYZE_AGENT_ID)


def ensure_agent_available(agent_id: str) -> None:
    """不可用时抛 HTTPException，供路由前置校验复用。"""
    availability = get_agent_availability(agent_id)
    if not availability.enabled:
        raise HTTPException(status_code=403, detail=availability.message or "功能暂未开放")


def ensure_agent_available_for_internal_run(agent_id: str) -> None:
    """不可用时抛 ValueError，供内部 runner 使用。"""
    availability = get_agent_availability(agent_id)
    if not availability.enabled:
        raise ValueError(availability.message or "功能暂未开放")


def feature_disabled_error_code(exc: HTTPException) -> int | None:
    """返回功能开关拒绝对应业务码；非功能开关拒绝返回 None。"""
    if exc.status_code == 403 and str(exc.detail) in _FEATURE_DISABLED_MESSAGES:
        return DATA_ANALYZE_DISABLED_CODE
    return None
