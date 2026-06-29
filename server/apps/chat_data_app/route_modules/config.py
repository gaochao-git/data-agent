"""Runtime configuration routes for ChatData."""

from __future__ import annotations

from fastapi import APIRouter

from utils.logger import get_logger
from utils.response import business_response
from ..services.llm_config import get_chat_data_model_options
from ..services.runtime_config import get_chat_data_runtime_config

logger = get_logger(__name__)
router = APIRouter()


@router.get("/models")
async def list_chat_data_models():
    """获取智能问数可选模型列表。"""
    try:
        return business_response(data=get_chat_data_model_options(), msg="获取成功")
    except Exception as e:
        logger.error("获取智能问数模型列表失败: %s", e, exc_info=True)
        return business_response(msg="获取模型列表失败，请稍后重试", code=50036, status="error")


@router.get("/config")
async def get_chat_data_config():
    """获取智能问数前端运行时配置。"""
    try:
        return business_response(data=get_chat_data_runtime_config(), msg="获取成功")
    except Exception as e:
        logger.error("获取智能问数运行时配置失败: %s", e, exc_info=True)
        return business_response(msg="获取运行时配置失败，请稍后重试", code=50037, status="error")
