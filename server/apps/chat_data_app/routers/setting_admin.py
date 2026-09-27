"""ChatData 后台配置变量路由：列表、覆盖（限注册白名单）、重置单个/全部。

入口由 routes.py 的 service token 统一保护；管理权限由可信代理的前端入口控制
（与数据源管理/定时任务管理模式一致）。写操作记录操作人四件套。
「重置」= 软删覆盖行，变量回退 env（.env 的运行时形态）/ 代码默认。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response
from ..schema import ChatDataSettingUpsertRequest
from ..services.setting_service import (
    SettingNotFoundError,
    SettingValidationError,
    delete_setting,
    list_settings,
    reset_all_settings,
    upsert_setting,
)
from .dependencies import _get_current_user
from . import JSON_API_RESPONSES

logger = get_logger(__name__)

router = APIRouter(tags=["ChatData 运行配置（Admin）"], responses=JSON_API_RESPONSES)


@router.get("/admin/settings")
async def list_chat_data_settings(request: Request, keyword: str | None = None):
    """配置变量列表（= 注册表白名单全量），含生效值与来源徽标（table/env/default）。"""
    _ = request
    try:
        data = await list_settings(keyword=keyword)
        return business_response(data=data, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取配置变量列表失败: %s", e, exc_info=True)
        return business_response(msg="获取配置变量列表失败，请稍后重试", code=ErrorCode.CHATDATA_SETTING_LIST_FAILED, status="error")


@router.put("/admin/settings/{setting_key}")
async def upsert_chat_data_setting(setting_key: str, payload: ChatDataSettingUpsertRequest, request: Request):
    """覆盖已注册变量（key 限注册白名单）；已重置的 key 自动复用原行恢复。"""
    try:
        current_user = _get_current_user(request)
        data = await upsert_setting(
            setting_key=setting_key,
            setting_value=payload.setting_value,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(data=data, msg="保存成功")
    except SettingValidationError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SETTING_INVALID, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("保存配置变量 %s 失败: %s", setting_key, e, exc_info=True)
        return business_response(msg="保存配置变量失败，请稍后重试", code=ErrorCode.CHATDATA_SETTING_SAVE_FAILED, status="error")


@router.delete("/admin/settings")
async def reset_all_chat_data_settings(request: Request):
    """全部重置：软删所有覆盖行，所有变量回退 env / 代码默认。"""
    try:
        current_user = _get_current_user(request)
        data = await reset_all_settings(
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(data=data, msg=f"已重置 {data['reset_count']} 个变量覆盖")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("全部重置配置变量失败: %s", e, exc_info=True)
        return business_response(msg="全部重置失败，请稍后重试", code=ErrorCode.CHATDATA_SETTING_DELETE_FAILED, status="error")


@router.delete("/admin/settings/{setting_key}")
async def reset_chat_data_setting(setting_key: str, request: Request):
    """重置单个变量：软删覆盖行，该变量回退 env / 代码默认值。"""
    try:
        current_user = _get_current_user(request)
        data = await delete_setting(
            setting_key=setting_key,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(data=data, msg="已重置，该变量回退 env/默认值")
    except SettingNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SETTING_NOT_FOUND, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("重置配置变量 %s 失败: %s", setting_key, e, exc_info=True)
        return business_response(msg="重置配置变量失败，请稍后重试", code=ErrorCode.CHATDATA_SETTING_DELETE_FAILED, status="error")
