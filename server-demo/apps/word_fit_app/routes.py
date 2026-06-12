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
import uuid

logger = get_logger(__name__)

router = APIRouter(prefix="/api/word-fit", tags=["word-fit"])

class ExtractRequest(BaseModel):
    """提取请求参数"""
    file_id: str
    extract_fields: List[Dict[str, Any]]
    
    # 可选参数
    conversation_id: Optional[str] = None
    locate_enabled: bool = False
    analysis_scene: Optional[str] = None
    analysis_user_id: Optional[str] = None
    analysis_match_strategy: Optional[str] = None

@router.post("/extract")
async def extract_info(request: ExtractRequest):
    """
    提取文件信息接口
    
    Stream 返回 LLM 的提取结果
    """
    started_at = timing_now()
    logger.info(f"[WordFit] Received extract request: file_id={request.file_id}")
    
    # 如果没有 conversation_id，生成一个新的
    conversation_id = request.conversation_id or str(uuid.uuid4())
    
    # 构造 options
    options = {
        "file_id": request.file_id,
        "extract_fields": request.extract_fields,
        "locate_enabled": request.locate_enabled,
        "analysis_scene": request.analysis_scene,
        "analysis_user_id": request.analysis_user_id,
        "analysis_match_strategy": request.analysis_match_strategy,
    }
    
    try:
        # 复用 handler 逻辑
        return await word_fit_completion_handler(
            conversation_id=conversation_id,
            query="Extract Info", # 占位 query
            options=options,
            file_ids=[request.file_id]
        )
    except Exception as e:
        log_route_timing(logger, "word_fit_app", "POST /api/word-fit/extract", timing_elapsed_ms(started_at), False, {"conversation_id": conversation_id, "file_id": request.file_id})
        logger.error(f"[WordFit] Error in extract endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))
