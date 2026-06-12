"""
Word Fit App 路由定义
提供独立的 HTTP 接口
"""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from utils.logger import get_logger
from utils.timing import log_route_timing, timing_elapsed_ms, timing_now
from utils.response import business_response
from .handlers import word_fit_completion_handler
from utils.wordfit_trace_storage import build_trace_base_id
from utils.storage_paths import resolve_complaint_scope
import uuid

logger = get_logger(__name__)

router = APIRouter(prefix="/api/word-fit", tags=["word-fit"])

class ExtractRequest(BaseModel):
    """提取请求参数"""
    file_id: Optional[str] = None
    file_ids: Optional[List[str]] = None
    extract_fields: List[Dict[str, Any]]
    
    # 可选参数
    conversation_id: Optional[str] = None
    locate_enabled: bool = False
    analysis_scene: Optional[str] = None
    analysis_user_id: Optional[str] = None
    analysis_match_strategy: Optional[str] = None
    org_id: Optional[str] = None
    entity_id: Optional[str] = None
    scope: Optional[str] = None
    force: bool = False
    extract_model: Optional[str] = None
    ocr_model: Optional[str] = None
    ocr_metrics_batch_id: Optional[str] = None


def normalize_extract_file_ids(file_id: str | None, file_ids: list[str] | None) -> list[str]:
    """规范化字段提取文件列表，兼容旧 file_id 入参。"""
    normalized = sorted({str(value or "").strip() for value in file_ids or [] if str(value or "").strip()})
    single = str(file_id or "").strip()
    if normalized:
        if single and single not in normalized:
            raise HTTPException(status_code=400, detail="file_id 与 file_ids 不能同时指向不同文件")
        return normalized
    if single:
        return [single]
    raise HTTPException(status_code=400, detail="file_id 或 file_ids 不能为空")


@router.post("/extract")
async def extract_info(request: ExtractRequest):
    """
    提取文件信息接口
    
    Stream 返回 LLM 的提取结果
    """
    started_at = timing_now()
    request_file_ids = normalize_extract_file_ids(request.file_id, request.file_ids)
    primary_file_id = request_file_ids[0]
    base_id = build_trace_base_id(request_file_ids)
    user_id = str(request.analysis_user_id or "").strip()
    try:
        scope_context = resolve_complaint_scope(request.scope, request.org_id, request.entity_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    logger.info("[WordFit] Received extract request: user_id=%s base_id=%s scope=%s org_id=%s entity_id=%s file_ids=%s force=%s extract_model=%s ocr_model=%s", user_id, base_id, scope_context.scope, scope_context.org_id, scope_context.entity_id, request_file_ids, request.force, request.extract_model or "", request.ocr_model or "")
    
    # 如果没有 conversation_id，生成一个新的
    conversation_id = request.conversation_id or str(uuid.uuid4())
    
    # 构造 options
    options = {
        "file_id": primary_file_id,
        "file_ids": request_file_ids,
        "extract_fields": request.extract_fields,
        "locate_enabled": request.locate_enabled,
        "analysis_scene": request.analysis_scene,
        "analysis_user_id": request.analysis_user_id,
        "analysis_match_strategy": request.analysis_match_strategy,
        "scope": scope_context.scope,
        "org_id": scope_context.org_id,
        "entity_id": scope_context.entity_id,
        "scope_path": scope_context.scope_path,
        "force": request.force,
        "extract_model": request.extract_model,
        "ocr_model": request.ocr_model,
        "ocr_metrics_batch_id": request.ocr_metrics_batch_id,
    }
    
    try:
        # 复用 handler 逻辑
        return await word_fit_completion_handler(
            conversation_id=conversation_id,
            query="Extract Info", # 占位 query
            options=options,
            file_ids=request_file_ids
        )
    except Exception as e:
        log_route_timing(logger, "word_fit_app", "POST /api/word-fit/extract", timing_elapsed_ms(started_at), False, {"conversation_id": conversation_id, "file_id": ",".join(request_file_ids), "base_id": base_id, "user_id": user_id, **scope_context.to_meta()})
        logger.error("[WordFit] Error in extract endpoint user_id=%s base_id=%s scope=%s org_id=%s entity_id=%s file_ids=%s err=%s", user_id, base_id, scope_context.scope, scope_context.org_id, scope_context.entity_id, request_file_ids, e)
        raise HTTPException(status_code=500, detail=str(e))
