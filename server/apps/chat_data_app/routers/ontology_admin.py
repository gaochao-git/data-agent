"""ChatData 本体管理路由（Admin）：对象类型/属性/关系类型三等实体的治理接口。

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

from ..daos.datasource_dao import DataSourceDao
from ..daos.ontology_dao import OntologyDao
from ..schema import (
    OntologyObjectTypeOntologyRequest,
    OntologyObjectTypeStatusRequest,
    OntologyObjectTypeUpsertRequest,
    OntologyRelationTypeStatusRequest,
    OntologyRelationTypeUpsertRequest,
    OntologyUpsertRequest,
)
from ..services.ontology import (
    OntologyAlreadyExistsError,
    OntologyInvalidError,
    OntologyNameInvalidError,
    OntologyNotFoundError,
    OntologyStateInvalidError,
    ontology_service,
)
from ..services.ontology.ontology_consistency import check_ontology_model_consistency as _run_consistency_check
from ..services.semantic.run.semantic_config_service import (
    get_semantic_config_service,
    is_public_semantic_config,
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


@router.get("/admin/meta/ontology/ontologies")
async def list_ontologies(request: Request) -> JSONResponse:
    """本体列表（本体=对象类型容器，一对一绑定数据源组）。"""
    try:
        _get_current_user(request)
        data = await ontology_service.list_ontologies_async()
        return business_response(data={"ontologies": data}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "获取本体列表失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.put("/admin/meta/ontology/ontologies/{ontology_name}")
async def upsert_ontology(ontology_name: str, payload: OntologyUpsertRequest, request: Request) -> JSONResponse:
    """创建/编辑本体（绑定数据源组；编辑活跃本体需 allow_overwrite）。"""
    try:
        current_user = _get_current_user(request)
        data = await ontology_service.upsert_ontology_async(
            ontology_name,
            payload.model_dump(),
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
            allow_overwrite=payload.allow_overwrite,
        )
        return business_response(data=data, msg="保存成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "保存本体失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.delete("/admin/meta/ontology/ontologies/{ontology_name}")
async def delete_ontology(ontology_name: str, request: Request) -> JSONResponse:
    """删除本体（软删；挂有活跃对象类型时拒绝）。"""
    try:
        current_user = _get_current_user(request)
        await ontology_service.delete_ontology_async(
            ontology_name,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data={"ontology_name": ontology_name}, msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "删除本体失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.get("/admin/meta/ontology/object-types")
async def list_ontology_object_types(
    request: Request, keyword: str | None = None, status: str | None = None, ontology_id: int | None = None
) -> JSONResponse:
    """本体对象类型列表（keyword 按名称/展示名/描述模糊过滤；status/ontology_id 精确过滤）。"""
    try:
        _get_current_user(request)
        data = await ontology_service.list_object_types_async(keyword or "", status or "", ontology_id)
        return business_response(data={"object_types": data}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "获取本体对象类型列表失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.get("/admin/meta/ontology/object-types/{object_type_name}")
async def get_ontology_object_type(object_type_name: str, request: Request) -> JSONResponse:
    """本体对象类型详情（含属性集与绑定）。"""
    try:
        _get_current_user(request)
        data = await ontology_service.get_object_type_async(object_type_name)
        if data is None:
            return business_response(
                msg=f"本体对象类型不存在: {object_type_name}",
                code=ErrorCode.CHATDATA_ONTOLOGY_NOT_FOUND,
                status="error",
            )
        return business_response(data=data, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "获取本体对象类型详情失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.put("/admin/meta/ontology/object-types/{object_type_name}")
async def upsert_ontology_object_type(
    object_type_name: str, payload: OntologyObjectTypeUpsertRequest, request: Request
) -> JSONResponse:
    """创建/整体编辑本体对象类型（元数据 + 属性集 + 绑定一次性提交）。"""
    try:
        current_user = _get_current_user(request)
        data = await ontology_service.upsert_object_type_async(
            object_type_name,
            payload.model_dump(),
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
            allow_overwrite=payload.allow_overwrite,
        )
        return business_response(data=data, msg="保存成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "保存本体对象类型失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.delete("/admin/meta/ontology/object-types/{object_type_name}")
async def delete_ontology_object_type(object_type_name: str, request: Request) -> JSONResponse:
    """删除本体对象类型（软删；certified 拒绝，级联软删属性与关系类型）。"""
    try:
        current_user = _get_current_user(request)
        await ontology_service.delete_object_type_async(
            object_type_name,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data={"object_type_name": object_type_name}, msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "删除本体对象类型失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.post("/admin/meta/ontology/object-types/{object_type_name}/status")
async def change_ontology_object_type_status(
    object_type_name: str, payload: OntologyObjectTypeStatusRequest, request: Request
) -> JSONResponse:
    """本体对象类型状态迁移（升级/降级/废弃；关系类型状态按对象类型级联，降级自动、升级手动）。"""
    try:
        current_user = _get_current_user(request)
        data = await ontology_service.set_object_type_status_async(
            object_type_name,
            payload.status,
            payload.model_dump(),
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data=data, msg="状态更新成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "本体对象类型状态变更失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.post("/admin/meta/ontology/object-types/{object_type_name}/ontology")
async def change_ontology_object_type_ontology(
    object_type_name: str, payload: OntologyObjectTypeOntologyRequest, request: Request
) -> JSONResponse:
    """对象类型本体归属变更（拖拽换本体窄写路径；ontology_id=null 表示移出本体）。"""
    try:
        current_user = _get_current_user(request)
        data = await ontology_service.set_object_type_ontology_async(
            object_type_name,
            payload.ontology_id,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data=data, msg="本体归属已更新")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "对象类型本体归属变更失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.get("/admin/meta/ontology/relation-types")
async def list_ontology_relations(
    request: Request, keyword: str | None = None, from_object_type: str | None = None, to_object_type: str | None = None
) -> JSONResponse:
    """本体关系类型列表（keyword 模糊过滤；from_object_type/to_object_type 按 API 名过滤）。"""
    try:
        _get_current_user(request)
        data = await ontology_service.list_relations_async(keyword or "", from_object_type or "", to_object_type or "")
        return business_response(data={"relation_types": data}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "获取本体关系类型列表失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.put("/admin/meta/ontology/relation-types/{relation_type_name}")
async def upsert_ontology_relation(
    relation_type_name: str, payload: OntologyRelationTypeUpsertRequest, request: Request
) -> JSONResponse:
    """创建/编辑本体关系类型（连接对象类型、关联列、基数变更需破坏性确认）。"""
    try:
        current_user = _get_current_user(request)
        data = await ontology_service.upsert_relation_async(
            relation_type_name,
            payload.model_dump(),
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
            allow_overwrite=payload.allow_overwrite,
        )
        return business_response(data=data, msg="保存成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "保存本体关系类型失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.delete("/admin/meta/ontology/relation-types/{relation_type_name}")
async def delete_ontology_relation(relation_type_name: str, request: Request) -> JSONResponse:
    """删除本体关系类型（软删）。"""
    try:
        current_user = _get_current_user(request)
        await ontology_service.delete_relation_async(
            relation_type_name,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data={"relation_type_name": relation_type_name}, msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "删除本体关系类型失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


@router.post("/admin/meta/ontology/relation-types/{relation_type_name}/status")
async def change_ontology_relation_status(
    relation_type_name: str, payload: OntologyRelationTypeStatusRequest, request: Request
) -> JSONResponse:
    """本体关系类型状态手动迁移（升级/降级；上限=两侧对象类型状态的较低者，deprecated 仅随对象类型级联）。"""
    try:
        current_user = _get_current_user(request)
        data = await ontology_service.set_relation_status_async(
            relation_type_name,
            payload.status,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data=data, msg="状态更新成功")
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(e, "本体关系类型状态变更失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)


async def _collect_consistency_scope() -> tuple[list[dict], dict[str, str], set[str]]:
    """一致性检查比较域收集：全部数据源组的公共语义配置去重合并。

    返回 (configs, {config_id: group_id}, config_ids)——本体不绑数据源组
    （2026-10-20 解耦），比较域取全部组的公共配置；CON_CROSS_MODEL_* 查重
    仍以配置自身所属组为比较域（跨组不互比）。
    """
    service = get_semantic_config_service()
    group_ids = sorted(
        {str((group or {}).get("group_id") or "").strip() for group in await DataSourceDao().get_all_datasources_async(include_tables=False)} - {""}
    )
    configs: list[dict] = []
    config_group_ids: dict[str, str] = {}
    seen_config_ids: set[str] = set()
    for group_id in group_ids:
        for config in await service.get_configs_async(group_id) or []:
            config_id = str((config or {}).get("id") or "").strip()
            if config_id and config_id not in seen_config_ids and is_public_semantic_config(config):
                seen_config_ids.add(config_id)
                configs.append(config)
                config_group_ids[config_id] = group_id
    return configs, config_group_ids, seen_config_ids, group_ids


def _severity_summary(findings: list[dict]) -> dict[str, int]:
    """findings 按 severity 计数（仅 error/warning/info 三档）。"""
    summary = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        severity = str(finding.get("severity") or "")
        if severity in summary:
            summary[severity] += 1
    return summary


@router.get("/admin/meta/ontology/consistency-check")
async def check_ontology_model_consistency(request: Request) -> JSONResponse:
    """本体↔语义模型一致性健康巡检（管理端手动触发）。

    比较域 = 全部数据源组的公共语义配置 × AI 可见本体面
    （bundle 与注入同源：活跃、certified/experimental、双端在集合内）。
    只巡检不评分、不阻断任何操作——产出失修清单（映射维度失效/关系类型列失效/
    类型冲突/属性未映射），供管理员修正映射或恢复维度。
    """
    try:
        _get_current_user(request)
        configs, config_group_ids, seen_config_ids, group_ids = await _collect_consistency_scope()
        bundle = await OntologyDao().load_injection_bundle_async(sorted(seen_config_ids)) if seen_config_ids else {}
        findings = _run_consistency_check(bundle, configs, config_group_ids=config_group_ids)
        summary = _severity_summary(findings)
        return business_response(
            data={
                "group_count": len(group_ids),
                "config_count": len(configs),
                "object_type_count": len(bundle.get("object_types") or []),
                "findings": findings,
                "summary": summary,
                "healthy": summary["error"] == 0 and summary["warning"] == 0,
            },
            msg="一致性检查完成",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("本体一致性检查失败: %s", e, exc_info=True)
        return _error_response(e, "本体一致性检查失败，请稍后重试", ErrorCode.CHATDATA_ONTOLOGY_OPERATION_FAILED)
