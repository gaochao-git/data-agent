"""ChatData 本体管理路由（Admin）：对象/属性/关系三等实体的治理接口。

入口由 routes.py 的 service token 统一保护；管理权限由可信代理的前端入口控制
（与元数据管理模式一致）。写操作记录操作人四件套。破坏性变更与状态门禁在
service 层执行，本路由只做 HTTP 入参、权限入口、统一响应与异常→错误码映射。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..schema import (
    OntologyDomainUpsertRequest,
    OntologyObjectDomainRequest,
    OntologyObjectStatusRequest,
    OntologyObjectUpsertRequest,
    OntologyRelationStatusRequest,
    OntologyRelationUpsertRequest,
)
from ..services.ontology import (
    OntologyAlreadyExistsError,
    OntologyInvalidError,
    OntologyNameInvalidError,
    OntologyNotFoundError,
    OntologyStateInvalidError,
    ontology_service,
)
from . import JSON_API_RESPONSES
from .dependencies import _get_current_user

logger = get_logger(__name__)

router = APIRouter(tags=["ChatData 本体管理（Admin）"], responses=JSON_API_RESPONSES)


def _error_response(e: Exception, fallback_msg: str, fallback_code: ErrorCode) -> JSONResponse:
    """本体业务异常 → 统一错误码映射。"""
    if isinstance(e, OntologyNameInvalidError):
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_ONTOLOGY_NAME_INVALID, status="error")
    if isinstance(e, OntologyInvalidError):
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_ONTOLOGY_INVALID, status="error")
    if isinstance(e, OntologyAlreadyExistsError):
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_ONTOLOGY_ALREADY_EXISTS, status="error")
    if isinstance(e, OntologyStateInvalidError):
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_ONTOLOGY_STATE_INVALID, status="error")
    if isinstance(e, OntologyNotFoundError):
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_ONTOLOGY_NOT_FOUND, status="error")
    logger.error("%s: %s", fallback_msg, e, exc_info=True)
    return business_response(msg=fallback_msg, code=fallback_code, status="error")


@router.get("/admin/meta/ontology/domains")
async def list_ontology_domains(request: Request) -> JSONResponse:
    """本体领域列表（领域=业务域实体，一对一绑定数据源组）。"""
    try:
        _get_current_user(request)
        data = await ontology_service.list_domains_async()
        return business_response(data={"domains": data}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "获取本体领域列表失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.put("/admin/meta/ontology/domains/{domain_name}")
async def upsert_ontology_domain(
    domain_name: str, payload: OntologyDomainUpsertRequest, request: Request
) -> JSONResponse:
    """创建/编辑本体领域（绑定数据源组；编辑活跃领域需 allow_overwrite）。"""
    try:
        current_user = _get_current_user(request)
        data = await ontology_service.upsert_domain_async(
            domain_name,
            payload.model_dump(),
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
            allow_overwrite=payload.allow_overwrite,
        )
        return business_response(data=data, msg="保存成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "保存本体领域失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.delete("/admin/meta/ontology/domains/{domain_name}")
async def delete_ontology_domain(domain_name: str, request: Request) -> JSONResponse:
    """删除本体领域（软删；挂有活跃对象时拒绝）。"""
    try:
        current_user = _get_current_user(request)
        await ontology_service.delete_domain_async(
            domain_name,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data={"domain_name": domain_name}, msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "删除本体领域失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.get("/admin/meta/ontology/objects")
async def list_ontology_objects(
    request: Request, keyword: str | None = None, status: str | None = None, domain_id: int | None = None
) -> JSONResponse:
    """本体对象列表（keyword 按名称/展示名/描述模糊过滤；status/domain_id 精确过滤）。"""
    try:
        _get_current_user(request)
        data = await ontology_service.list_objects_async(keyword or "", status or "", domain_id)
        return business_response(data={"objects": data}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "获取本体对象列表失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.get("/admin/meta/ontology/objects/{object_name}")
async def get_ontology_object(object_name: str, request: Request) -> JSONResponse:
    """本体对象详情（含属性集与绑定）。"""
    try:
        _get_current_user(request)
        data = await ontology_service.get_object_async(object_name)
        if data is None:
            return business_response(
                msg=f"本体对象不存在: {object_name}", code=ErrorCode.CHATDATA_ONTOLOGY_NOT_FOUND, status="error"
            )
        return business_response(data=data, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "获取本体对象详情失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.put("/admin/meta/ontology/objects/{object_name}")
async def upsert_ontology_object(
    object_name: str, payload: OntologyObjectUpsertRequest, request: Request
) -> JSONResponse:
    """创建/整体编辑本体对象（元数据 + 属性集 + 绑定一次性提交）。"""
    try:
        current_user = _get_current_user(request)
        data = await ontology_service.upsert_object_async(
            object_name,
            payload.model_dump(),
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
            allow_overwrite=payload.allow_overwrite,
        )
        return business_response(data=data, msg="保存成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "保存本体对象失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.delete("/admin/meta/ontology/objects/{object_name}")
async def delete_ontology_object(object_name: str, request: Request) -> JSONResponse:
    """删除本体对象（软删；certified 拒绝，级联软删属性与关系）。"""
    try:
        current_user = _get_current_user(request)
        await ontology_service.delete_object_async(
            object_name,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data={"object_name": object_name}, msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "删除本体对象失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.post("/admin/meta/ontology/objects/{object_name}/status")
async def change_ontology_object_status(
    object_name: str, payload: OntologyObjectStatusRequest, request: Request
) -> JSONResponse:
    """本体对象状态迁移（升级/降级/废弃；关系状态按对象级联，降级自动、升级手动）。"""
    try:
        current_user = _get_current_user(request)
        data = await ontology_service.set_object_status_async(
            object_name,
            payload.status,
            payload.model_dump(),
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data=data, msg="状态更新成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "本体对象状态变更失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.post("/admin/meta/ontology/objects/{object_name}/domain")
async def change_ontology_object_domain(
    object_name: str, payload: OntologyObjectDomainRequest, request: Request
) -> JSONResponse:
    """对象领域归属变更（拖拽换域窄写路径；domain_id=null 表示移出领域）。"""
    try:
        current_user = _get_current_user(request)
        data = await ontology_service.set_object_domain_async(
            object_name,
            payload.domain_id,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data=data, msg="领域归属已更新")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "对象领域归属变更失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.get("/admin/meta/ontology/relations")
async def list_ontology_relations(
    request: Request, keyword: str | None = None, from_object: str | None = None, to_object: str | None = None
) -> JSONResponse:
    """本体关系列表（keyword 模糊过滤；from_object/to_object 按 API 名过滤）。"""
    try:
        _get_current_user(request)
        data = await ontology_service.list_relations_async(keyword or "", from_object or "", to_object or "")
        return business_response(data={"relations": data}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "获取本体关系列表失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.put("/admin/meta/ontology/relations/{relation_name}")
async def upsert_ontology_relation(
    relation_name: str, payload: OntologyRelationUpsertRequest, request: Request
) -> JSONResponse:
    """创建/编辑本体关系（连接对象、关联列、基数变更需破坏性确认）。"""
    try:
        current_user = _get_current_user(request)
        data = await ontology_service.upsert_relation_async(
            relation_name,
            payload.model_dump(),
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
            allow_overwrite=payload.allow_overwrite,
        )
        return business_response(data=data, msg="保存成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "保存本体关系失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.delete("/admin/meta/ontology/relations/{relation_name}")
async def delete_ontology_relation(relation_name: str, request: Request) -> JSONResponse:
    """删除本体关系（软删）。"""
    try:
        current_user = _get_current_user(request)
        await ontology_service.delete_relation_async(
            relation_name,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data={"relation_name": relation_name}, msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "删除本体关系失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.post("/admin/meta/ontology/relations/{relation_name}/status")
async def change_ontology_relation_status(
    relation_name: str, payload: OntologyRelationStatusRequest, request: Request
) -> JSONResponse:
    """本体关系状态手动迁移（升级/降级；上限=两侧对象状态的较低者，deprecated 仅随对象级联）。"""
    try:
        current_user = _get_current_user(request)
        data = await ontology_service.set_relation_status_async(
            relation_name,
            payload.status,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data=data, msg="状态更新成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "本体关系状态变更失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)
