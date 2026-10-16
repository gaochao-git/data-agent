"""ChatData 元数据管理路由（Admin）：全局枚举的增删改查与引用方查询。

入口由 routes.py 的 service token 统一保护；管理权限由可信代理的前端入口控制
（与数据源管理/配置变量管理模式一致）。写操作记录操作人四件套。
将来新增其他元数据类型（单位、存储格式等）时在本路由模块内扩展资源段。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..schema import MetaGlobalEnumGenerateRequest, MetaGlobalEnumUpsertRequest
from ..services.datasource.column_dict_generate import generate_dict_candidates_from_text
from ..services.meta.global_enum_service import (
    MetaGlobalEnumAlreadyExistsError,
    MetaGlobalEnumInUseError,
    MetaGlobalEnumInvalidError,
    MetaGlobalEnumNameInvalidError,
    MetaGlobalEnumNotFoundError,
    global_enum_service,
)
from . import JSON_API_RESPONSES
from .dependencies import _get_current_user

logger = get_logger(__name__)

router = APIRouter(tags=["ChatData 元数据管理（Admin）"], responses=JSON_API_RESPONSES)


@router.get("/admin/meta/enums")
async def list_meta_global_enums(
    request: Request, keyword: str | None = None, business_domain: str | None = None
) -> JSONResponse:
    """全局枚举列表（keyword 按名称/说明模糊过滤；business_domain 按域过滤，不传返回全部域）。"""
    try:
        _get_current_user(request)
        data = await global_enum_service.list_enums_async(keyword or "", business_domain)
        return business_response(data=data, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取全局枚举列表失败: %s", e, exc_info=True)
        return business_response(
            msg="获取全局枚举列表失败，请稍后重试", code=ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED, status="error"
        )


@router.get("/admin/meta/enums/domains")
async def list_meta_global_enum_domains(request: Request) -> JSONResponse:
    """已有全局枚举的业务域去重清单（供域选择器）。"""
    try:
        _get_current_user(request)
        domains = await global_enum_service.list_enum_domains_async()
        return business_response(data={"domains": domains}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取全局枚举业务域清单失败: %s", e, exc_info=True)
        return business_response(
            msg="获取业务域清单失败，请稍后重试", code=ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED, status="error"
        )


@router.get("/admin/meta/enums/{enum_name}")
async def get_meta_global_enum(enum_name: str, request: Request, business_domain: str | None = None) -> JSONResponse:
    """全局枚举详情（按业务域 + 枚举名定位）。"""
    try:
        _get_current_user(request)
        data = await global_enum_service.get_enum_async(enum_name, business_domain or "")
        if data is None:
            return business_response(
                msg=f"全局枚举不存在: {enum_name}", code=ErrorCode.CHATDATA_META_ENUM_NOT_FOUND, status="error"
            )
        return business_response(data=data, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取全局枚举 %s 失败: %s", enum_name, e, exc_info=True)
        return business_response(
            msg="获取全局枚举失败，请稍后重试", code=ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED, status="error"
        )


@router.get("/admin/meta/enums/{enum_name}/refs")
async def list_meta_global_enum_refs(
    enum_name: str, request: Request, business_domain: str | None = None
) -> JSONResponse:
    """查询引用指定全局枚举的数据字典列（删除前确认引用范围）。"""
    try:
        _get_current_user(request)
        domain = business_domain or ""
        refs = await global_enum_service.list_enum_refs_async(enum_name, domain)
        return business_response(
            data={"business_domain": domain, "enum_name": enum_name, "refs": refs, "ref_count": len(refs)},
            msg="获取成功",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("查询全局枚举 %s 引用方失败: %s", enum_name, e, exc_info=True)
        return business_response(
            msg="查询引用方失败，请稍后重试", code=ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED, status="error"
        )


@router.post("/admin/meta/enums/generate")
async def generate_meta_global_enum_items(payload: MetaGlobalEnumGenerateRequest, request: Request) -> JSONResponse:
    """从文本提取枚举值候选（复用字段字典生成链路，无表/字段上下文），不直接保存。"""
    try:
        _get_current_user(request)
        column_dict = await generate_dict_candidates_from_text(payload.text)
        return business_response(data={"enum_items": column_dict.get("enum_values") or []}, msg="生成成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("生成全局枚举值候选失败: %s", e, exc_info=True)
        return business_response(
            msg="生成失败，请稍后重试", code=ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED, status="error"
        )


@router.put("/admin/meta/enums/{enum_name}")
async def upsert_meta_global_enum(
    enum_name: str, payload: MetaGlobalEnumUpsertRequest, request: Request
) -> JSONResponse:
    """按 (business_domain, enum_name) 创建/更新全局枚举（软删行自动复用恢复）。"""
    try:
        current_user = _get_current_user(request)
        data = await global_enum_service.upsert_enum_async(
            enum_name=enum_name,
            enum_desc=payload.enum_desc,
            enum_items=[item.model_dump() for item in payload.enum_items],
            business_domain=payload.business_domain,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
            allow_overwrite=payload.allow_overwrite,
        )
        return business_response(data=data, msg="保存成功")
    except MetaGlobalEnumNameInvalidError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_META_ENUM_NAME_INVALID, status="error")
    except MetaGlobalEnumInvalidError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_META_ENUM_INVALID, status="error")
    except MetaGlobalEnumAlreadyExistsError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_META_ENUM_ALREADY_EXISTS, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("保存全局枚举 %s 失败: %s", enum_name, e, exc_info=True)
        return business_response(
            msg="保存全局枚举失败，请稍后重试", code=ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED, status="error"
        )


@router.delete("/admin/meta/enums/{enum_name}")
async def delete_meta_global_enum(enum_name: str, request: Request, business_domain: str | None = None) -> JSONResponse:
    """删除全局枚举（软删）；仍被数据字典引用时拒绝并返回引用位置。"""
    try:
        current_user = _get_current_user(request)
        domain = business_domain or ""
        await global_enum_service.delete_enum_async(
            enum_name,
            domain,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data={"business_domain": domain, "enum_name": enum_name}, msg="删除成功")
    except MetaGlobalEnumNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_META_ENUM_NOT_FOUND, status="error")
    except MetaGlobalEnumInUseError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_META_ENUM_IN_USE, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("删除全局枚举 %s 失败: %s", enum_name, e, exc_info=True)
        return business_response(
            msg="删除全局枚举失败，请稍后重试", code=ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED, status="error"
        )
