"""Specialist app routes (policy fetch + AI chat)."""
import logging
import os
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

# 确保 __init__.py 中的 agent 注册被执行
from . import AGENT_ID  # noqa: F401

from .ai_chat import (
    get_conversation_detail_for_user,
    list_conversations_for_user,
    stream_ai_chat_response,
)
from auth import AuthenticationError, authenticate_request
from utils.policy_service import fetch_policy_payload
from .trace_utils import read_trace

router = APIRouter(prefix="/api", tags=["specialist"])

POLICY_SERVICE_BASE_URL = os.getenv("POLICY_SERVICE_BASE_URL")
_DEFAULT_POLICY_SERVICE_TIMEOUT = 30


def _env_flag(key: str, default: bool = True) -> bool:
    """Parse boolean-like env flags such as 'true', '1', 'yes', 'y', 'on'."""
    value = os.getenv(key)
    if value is None:
        return default
    trimmed = value.strip()
    if not trimmed:
        return default
    return trimmed.lower() in {"1", "true", "yes", "y", "on"}


POLICY_SERVICE_VERIFY_TLS = _env_flag("POLICY_SERVICE_VERIFY_TLS", True)
try:
    POLICY_SERVICE_TIMEOUT = int(
        os.getenv("POLICY_SERVICE_TIMEOUT", _DEFAULT_POLICY_SERVICE_TIMEOUT)
    )
    if POLICY_SERVICE_TIMEOUT <= 0:
        raise ValueError
except (TypeError, ValueError):
    POLICY_SERVICE_TIMEOUT = _DEFAULT_POLICY_SERVICE_TIMEOUT

DEBUG_ENABLED = _env_flag("DEBUG", False)

policy_logger = logging.getLogger("fxcjg.policy_service")


@router.get("/policies/{policy_id}")
def fetch_policy_text(policy_id: str):
    """Proxy policy text fetching to the upstream knowledge service (FastAPI)."""
    payload, status = fetch_policy_payload(
        policy_id,
        POLICY_SERVICE_BASE_URL,
        timeout=POLICY_SERVICE_TIMEOUT,
        verify_tls=POLICY_SERVICE_VERIFY_TLS,
        logger=policy_logger,
    )
    return JSONResponse(payload, status_code=status)


@router.get("/ai_chat/conversations")
def ai_chat_conversations(req: Request):
    """返回当前用户的对话列表 (FastAPI 版)。"""
    try:
        current_user = authenticate_request(req)
    except AuthenticationError as exc:
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)

    payload = list_conversations_for_user(current_user)
    return JSONResponse(payload)


@router.get("/ai_chat/conversations/{conversation_id}")
def ai_chat_conversation_detail(conversation_id: str, req: Request):
    """返回指定会话的详细内容 (FastAPI 版)。"""
    try:
        current_user = authenticate_request(req)
    except AuthenticationError as exc:
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)

    normalized_conversation_id = (conversation_id or "").strip()
    if not normalized_conversation_id:
        return JSONResponse({"error": "无效的会话 ID"}, status_code=400)

    detail = get_conversation_detail_for_user(
        current_user, normalized_conversation_id
    )
    if detail is None:
        return JSONResponse({"error": "会话不存在或无权访问"}, status_code=404)

    return JSONResponse(detail)


@router.get("/ai_chat/traces/{conversation_id}")
def ai_chat_trace(conversation_id: str, req: Request):
    """Return trace events for a conversation (FastAPI 版)。"""
    if not DEBUG_ENABLED:
        return JSONResponse({"error": "调试功能未开启"}, status_code=404)
    try:
        current_user = authenticate_request(req)
    except AuthenticationError as exc:
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)

    normalized_conversation_id = (conversation_id or "").strip()
    if not normalized_conversation_id:
        return JSONResponse({"error": "无效的会话 ID"}, status_code=400)

    detail = get_conversation_detail_for_user(
        current_user, normalized_conversation_id
    )
    if detail is None:
        return JSONResponse({"error": "会话不存在或无权访问"}, status_code=404)

    events = read_trace(normalized_conversation_id)
    return JSONResponse(
        {
            "conversation_id": normalized_conversation_id,
            "event_count": len(events),
            "events": events,
        }
    )


@router.post("/ai_chat")
async def ai_chat(req: Request):
    """通过 OpenAI 接口生成流式回答 (FastAPI 版)。"""
    try:
        current_user = authenticate_request(req)
    except AuthenticationError as exc:
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)

    payload = await req.json()
    generator, headers = stream_ai_chat_response(
        payload,
        req.headers.get("Origin", ""),
        current_user,
    )
    return StreamingResponse(
        generator, media_type="text/event-stream", headers=headers
    )
