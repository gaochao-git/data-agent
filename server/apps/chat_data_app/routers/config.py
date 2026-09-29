"""Runtime configuration routes for ChatData."""

from __future__ import annotations

from fastapi import APIRouter

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..services.runtime_config import get_chat_data_runtime_config
from . import JSON_API_RESPONSES

logger = get_logger(__name__)
router = APIRouter(tags=["ChatData 配置与模型"], responses=JSON_API_RESPONSES)


@router.get("/config")
async def get_chat_data_config():
    """获取智能问数前端运行时配置。"""
    try:
        return business_response(data=await get_chat_data_runtime_config(), msg="获取成功")
    except Exception as e:
        logger.error("获取智能问数运行时配置失败: %s", e, exc_info=True)
        return business_response(msg="获取运行时配置失败，请稍后重试", code=ErrorCode.CHATDATA_CONFIG_RUNTIME_FAILED, status="error")
