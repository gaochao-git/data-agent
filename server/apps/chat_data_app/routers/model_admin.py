"""ChatData 模型注册表管理路由：列表、保存、启停、设默认、删除、连接测试。

入口由 routes.py 的 service token 统一保护；管理权限由可信代理的前端入口
控制（与运行配置/数据源管理模式一致）。写操作记录操作人四件套。行按
model_key（注册键，唯一）定位；model_id 是上游请求名，同名可接多个供应商。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..schema import ChatDataModelDiscoverRequest, ChatDataModelEnabledRequest, ChatDataModelUpsertRequest
from ..services.model_registry_service import (
    ModelRegistryConflict,
    ModelRegistryError,
    ModelRegistryNotFoundError,
    create_model,
    delete_model,
    discover_models,
    list_models,
    set_model_default,
    set_model_enabled,
    test_model_connection,
    update_model,
)
from . import JSON_API_RESPONSES
from .dependencies import _get_current_user

logger = get_logger(__name__)

router = APIRouter(tags=["ChatData 模型注册表（Admin）"], responses=JSON_API_RESPONSES)


@router.get("/admin/models")
async def list_chat_data_models(request: Request) -> JSONResponse:
    """模型注册表列表（含密文回填字段，编辑框直接带回）。"""
    _ = request
    try:
        data = await list_models()
        return business_response(data={"models": data}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取模型注册表列表失败: %s", e, exc_info=True)
        return business_response(
            msg="获取模型列表失败，请稍后重试", code=ErrorCode.CHATDATA_MODEL_LIST_FAILED, status="error"
        )


@router.post("/admin/models")
async def create_chat_data_model(payload: ChatDataModelUpsertRequest, request: Request) -> JSONResponse:
    """新建模型（注册键服务端自动生成；业务唯一性=供应商+请求名）；成功后缓存即时刷新。"""
    try:
        current_user = _get_current_user(request)
        data = await create_model(
            payload.model_dump(), user_id=str(current_user.user_id), username=str(current_user.username or "")
        )
        return business_response(data=data, msg="保存成功")
    except ModelRegistryConflict as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_MODEL_DUPLICATED, status="error")
    except ModelRegistryError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_MODEL_INVALID, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("新建模型失败: %s", e, exc_info=True)
        return business_response(
            msg="保存模型失败，请稍后重试", code=ErrorCode.CHATDATA_MODEL_SAVE_FAILED, status="error"
        )


@router.put("/admin/models/{model_key}")
async def upsert_chat_data_model(
    model_key: str, payload: ChatDataModelUpsertRequest, request: Request
) -> JSONResponse:
    """更新模型（按注册键定位身份）；成功后缓存即时刷新。"""
    try:
        current_user = _get_current_user(request)
        data = await update_model(
            model_key,
            payload.model_dump(),
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(data=data, msg="保存成功")
    except ModelRegistryConflict as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_MODEL_DUPLICATED, status="error")
    except ModelRegistryError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_MODEL_INVALID, status="error")
    except ModelRegistryNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_MODEL_NOT_FOUND, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("保存模型 %s 失败: %s", model_key, e, exc_info=True)
        return business_response(
            msg="保存模型失败，请稍后重试", code=ErrorCode.CHATDATA_MODEL_SAVE_FAILED, status="error"
        )


@router.patch("/admin/models/{model_key}/enabled")
async def toggle_chat_data_model(
    model_key: str, payload: ChatDataModelEnabledRequest, request: Request
) -> JSONResponse:
    """启停模型（enabled 为严格布尔，字符串 "false" 会被 Pydantic 拒绝）。"""
    try:
        current_user = _get_current_user(request)
        await set_model_enabled(
            model_key,
            enabled=payload.enabled,
            user_id=str(current_user.user_id),
            username=str(current_user.username or ""),
        )
        return business_response(data={"model_key": model_key, "enabled": payload.enabled}, msg="设置成功")
    except ModelRegistryNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_MODEL_NOT_FOUND, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("启停模型 %s 失败: %s", model_key, e, exc_info=True)
        return business_response(
            msg="启停模型失败，请稍后重试", code=ErrorCode.CHATDATA_MODEL_OPERATION_FAILED, status="error"
        )


@router.patch("/admin/models/{model_key}/default")
async def set_default_chat_data_model(model_key: str, request: Request) -> JSONResponse:
    """设唯一默认模型（同时启用该行）。"""
    try:
        current_user = _get_current_user(request)
        await set_model_default(
            model_key, user_id=str(current_user.user_id), username=str(current_user.username or "")
        )
        return business_response(data={"model_key": model_key, "is_default": True}, msg="默认模型已设置")
    except ModelRegistryNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_MODEL_NOT_FOUND, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("设置默认模型 %s 失败: %s", model_key, e, exc_info=True)
        return business_response(
            msg="设置默认模型失败，请稍后重试", code=ErrorCode.CHATDATA_MODEL_OPERATION_FAILED, status="error"
        )


@router.delete("/admin/models/{model_key}")
async def delete_chat_data_model(model_key: str, request: Request) -> JSONResponse:
    """删除模型（软删；重加同注册键模型自动复活原行）。"""
    try:
        current_user = _get_current_user(request)
        await delete_model(
            model_key, user_id=str(current_user.user_id), username=str(current_user.username or "")
        )
        return business_response(data={"model_key": model_key}, msg="已删除")
    except ModelRegistryNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_MODEL_NOT_FOUND, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("删除模型 %s 失败: %s", model_key, e, exc_info=True)
        return business_response(
            msg="删除模型失败，请稍后重试", code=ErrorCode.CHATDATA_MODEL_OPERATION_FAILED, status="error"
        )


@router.post("/admin/models/discover")
async def discover_chat_data_models(payload: ChatDataModelDiscoverRequest, request: Request) -> JSONResponse:
    """探测上游 OpenAI 兼容接口的可用模型清单（GET {base_url}/models）。"""
    _ = request
    try:
        models = await discover_models(payload.model_dump())
        return business_response(data={"models": models}, msg="探测完成")
    except ModelRegistryError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_MODEL_INVALID, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("模型探测失败: %s", e, exc_info=True)
        return business_response(
            msg="模型探测执行失败，请稍后重试", code=ErrorCode.CHATDATA_MODEL_TEST_FAILED, status="error"
        )


@router.post("/admin/models/test-connection")
async def test_chat_data_model_connection(payload: ChatDataModelUpsertRequest, request: Request) -> JSONResponse:
    """用表单当前值发一次最小请求验证连通性（不要求模型已保存）。"""
    _ = request
    try:
        data = await test_model_connection(payload.model_dump())
        return business_response(data=data, msg="测试完成")
    except ModelRegistryError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_MODEL_INVALID, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("模型连接测试失败: %s", e, exc_info=True)
        return business_response(
            msg="连接测试执行失败，请稍后重试", code=ErrorCode.CHATDATA_MODEL_TEST_FAILED, status="error"
        )
