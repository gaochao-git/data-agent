"""Logical Dataset 管理路由：Dataset 身份、版本、分支、member 绑定与 Pipeline。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..schema import (
    DatasetBootstrapRequest,
    DatasetBranchCreateRequest,
    DatasetPipelineUpsertRequest,
    DatasetSemanticLinkUpsertRequest,
    DatasetUpsertRequest,
)
from ..services.dataset.dataset_semantic_link_service import (
    DatasetSemanticLinkInvalidError,
    DatasetSemanticLinkNotFoundError,
    dataset_semantic_link_service,
)
from ..services.dataset.dataset_service import (
    DatasetAlreadyExistsError,
    DatasetInvalidError,
    DatasetNotFoundError,
    logical_dataset_service,
)
from ..services.dataset.pipeline_service import dataset_pipeline_service
from . import JSON_API_RESPONSES
from .dependencies import _get_current_user

logger = get_logger(__name__)

router = APIRouter(tags=["ChatData Logical Dataset（Admin）"], responses=JSON_API_RESPONSES)


def _error_response(error: Exception, fallback: str) -> JSONResponse:
    if isinstance(error, DatasetInvalidError):
        return business_response(msg=str(error), code=ErrorCode.CHATDATA_DATASET_INVALID, status="error")
    if isinstance(error, DatasetAlreadyExistsError):
        return business_response(msg=str(error), code=ErrorCode.CHATDATA_DATASET_ALREADY_EXISTS, status="error")
    if isinstance(error, DatasetNotFoundError):
        return business_response(msg=str(error), code=ErrorCode.CHATDATA_DATASET_NOT_FOUND, status="error")
    if isinstance(error, DatasetSemanticLinkInvalidError):
        return business_response(msg=str(error), code=ErrorCode.CHATDATA_METRIC_LINK_INVALID, status="error")
    if isinstance(error, DatasetSemanticLinkNotFoundError):
        return business_response(msg=str(error), code=ErrorCode.CHATDATA_METRIC_LINK_NOT_FOUND, status="error")
    logger.error("%s: %s", fallback, error, exc_info=True)
    return business_response(msg=fallback, code=ErrorCode.CHATDATA_DATASET_OPERATION_FAILED, status="error")


def _dataset_payload(body: DatasetUpsertRequest) -> dict:
    payload = body.model_dump()
    for column in payload.get("columns") or []:
        if not column.get("physical_column"):
            column["physical_column"] = column["name"]
    return payload


@router.get("/admin/meta/datasets")
async def list_datasets(
    request: Request,
    dataset_layer: str | None = Query(default=None, description="按 Pipeline 分层筛选"),
    dataset_origin: str | None = Query(default=None, description="按 Dataset 来源筛选"),
) -> JSONResponse:
    _get_current_user(request)
    try:
        data = await logical_dataset_service.list_datasets_async(
            dataset_layer=dataset_layer,
            dataset_origin=dataset_origin,
        )
        return business_response(data={"datasets": data}, msg="获取成功")
    except Exception as error:
        return _error_response(error, "获取 Dataset 失败")


@router.get("/admin/meta/datasets/{dataset_name}")
async def get_dataset(dataset_name: str, request: Request) -> JSONResponse:
    _get_current_user(request)
    try:
        return business_response(data=await logical_dataset_service.get_dataset_async(dataset_name), msg="获取成功")
    except Exception as error:
        return _error_response(error, "获取 Dataset 失败")


@router.put("/admin/meta/datasets/{dataset_name}")
async def upsert_dataset(dataset_name: str, body: DatasetUpsertRequest, request: Request) -> JSONResponse:
    user = _get_current_user(request)
    try:
        data = await logical_dataset_service.upsert_dataset_async(
            dataset_name,
            _dataset_payload(body),
            operator=str(getattr(user, "user_id", "") or ""),
            operator_name=str(getattr(user, "username", "") or ""),
        )
        return business_response(data=data, msg="保存成功")
    except Exception as error:
        return _error_response(error, "保存 Dataset 失败")


@router.delete("/admin/meta/datasets/{dataset_name}")
async def delete_dataset(dataset_name: str, request: Request) -> JSONResponse:
    _get_current_user(request)
    try:
        await logical_dataset_service.delete_dataset_async(dataset_name)
        return business_response(msg="删除成功")
    except Exception as error:
        return _error_response(error, "删除 Dataset 失败")


@router.post("/admin/meta/datasets/bootstrap")
async def bootstrap_datasets(body: DatasetBootstrapRequest, request: Request) -> JSONResponse:
    user = _get_current_user(request)
    try:
        data = await logical_dataset_service.bootstrap_from_datasource_async(
            body.datasource_group_id,
            table_names=body.table_names,
            operator=str(getattr(user, "user_id", "") or ""),
            operator_name=str(getattr(user, "username", "") or ""),
        )
        return business_response(data={"datasets": data}, msg="注册完成")
    except Exception as error:
        return _error_response(error, "注册 Dataset 失败")


@router.post("/admin/meta/datasets/{dataset_name}/branches")
async def create_dataset_branch(dataset_name: str, body: DatasetBranchCreateRequest, request: Request) -> JSONResponse:
    user = _get_current_user(request)
    try:
        data = await logical_dataset_service.create_branch_async(
            dataset_name,
            branch_name=body.branch_name,
            base_version_id=body.base_version_id,
            operator=str(getattr(user, "user_id", "") or ""),
            operator_name=str(getattr(user, "username", "") or ""),
        )
        return business_response(data=data, msg="创建成功")
    except Exception as error:
        return _error_response(error, "创建 Dataset 分支失败")


# ============ Dataset Pipeline ============


@router.get("/admin/meta/pipelines")
async def list_pipelines(request: Request) -> JSONResponse:
    _get_current_user(request)
    try:
        return business_response(
            data={"pipelines": await dataset_pipeline_service.list_pipelines_async()}, msg="获取成功"
        )
    except Exception as error:
        return _error_response(error, "获取 Pipeline 失败")


@router.get("/admin/meta/pipelines/{pipeline_name}")
async def get_pipeline(pipeline_name: str, request: Request) -> JSONResponse:
    _get_current_user(request)
    try:
        return business_response(data=await dataset_pipeline_service.get_pipeline_async(pipeline_name), msg="获取成功")
    except Exception as error:
        return _error_response(error, "获取 Pipeline 失败")


@router.put("/admin/meta/pipelines/{pipeline_name}")
async def upsert_pipeline(pipeline_name: str, body: DatasetPipelineUpsertRequest, request: Request) -> JSONResponse:
    user = _get_current_user(request)
    try:
        data = await dataset_pipeline_service.upsert_pipeline_async(
            pipeline_name,
            body.model_dump(),
            operator=str(getattr(user, "user_id", "") or ""),
            operator_name=str(getattr(user, "username", "") or ""),
        )
        return business_response(data=data, msg="保存成功")
    except Exception as error:
        return _error_response(error, "保存 Pipeline 失败")


@router.delete("/admin/meta/pipelines/{pipeline_name}")
async def delete_pipeline(pipeline_name: str, request: Request) -> JSONResponse:
    _get_current_user(request)
    try:
        await dataset_pipeline_service.delete_pipeline_async(pipeline_name)
        return business_response(msg="删除成功")
    except Exception as error:
        return _error_response(error, "删除 Pipeline 失败")


@router.post("/admin/meta/pipelines/{pipeline_name}/build")
async def build_pipeline(pipeline_name: str, request: Request) -> JSONResponse:
    user = _get_current_user(request)
    try:
        data = await dataset_pipeline_service.build_pipeline_async(
            pipeline_name,
            operator=str(getattr(user, "user_id", "") or ""),
            operator_name=str(getattr(user, "username", "") or ""),
            user_id=str(getattr(user, "user_id", "") or ""),
            username=str(getattr(user, "username", "") or ""),
        )
        return business_response(data=data, msg="构建完成")
    except Exception as error:
        return _error_response(error, "构建 Pipeline 失败")


@router.get("/admin/meta/pipelines/{pipeline_name}/runs")
async def list_pipeline_runs(
    pipeline_name: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100, description="返回条数"),
) -> JSONResponse:
    _get_current_user(request)
    try:
        data = await dataset_pipeline_service.list_runs_async(pipeline_name, limit=limit)
        return business_response(data={"runs": data}, msg="获取成功")
    except Exception as error:
        return _error_response(error, "获取 Pipeline 运行记录失败")


@router.get("/admin/meta/pipeline-runs/{run_id}")
async def get_pipeline_run(run_id: str, request: Request) -> JSONResponse:
    _get_current_user(request)
    try:
        return business_response(data=await dataset_pipeline_service.get_run_async(run_id), msg="获取成功")
    except Exception as error:
        return _error_response(error, "获取 Pipeline 运行详情失败")


@router.get("/admin/meta/datasets/{dataset_name}/semantic-links")
async def list_dataset_semantic_links(
    dataset_name: str,
    request: Request,
    datasource_group_id: str | None = Query(default=None, description="按数据源组筛选"),
) -> JSONResponse:
    """Dataset ↔ 语义模型关联列表（只存引用与列对照，指标口径在语义层）。"""
    _get_current_user(request)
    try:
        data = await dataset_semantic_link_service.list_links_async(
            dataset_name,
            datasource_group_id=datasource_group_id,
        )
        return business_response(data={"links": data}, msg="获取成功")
    except Exception as error:
        return _error_response(error, "获取 Dataset 语义模型关联失败")


@router.put("/admin/meta/datasets/{dataset_name}/semantic-links/{semantic_config_id}")
async def upsert_dataset_semantic_link(
    dataset_name: str,
    semantic_config_id: str,
    body: DatasetSemanticLinkUpsertRequest,
    request: Request,
) -> JSONResponse:
    """创建/更新 Dataset ↔ 语义模型关联；保存前校验 Dataset 列、语义维度、指标与物理表。"""
    user = _get_current_user(request)
    try:
        data = await dataset_semantic_link_service.upsert_link_async(
            dataset_name,
            semantic_config_id,
            body.model_dump(),
            operator=str(getattr(user, "user_id", "") or ""),
            operator_name=str(getattr(user, "username", "") or ""),
        )
        return business_response(data=data, msg="保存成功")
    except Exception as error:
        return _error_response(error, "保存 Dataset 语义模型关联失败")


@router.delete("/admin/meta/datasets/{dataset_name}/semantic-links/{semantic_config_id}")
async def delete_dataset_semantic_link(
    dataset_name: str,
    semantic_config_id: str,
    request: Request,
    datasource_group_id: str | None = Query(default=None, description="按数据源组限定"),
) -> JSONResponse:
    """删除 Dataset ↔ 语义模型关联（软删）。"""
    user = _get_current_user(request)
    try:
        await dataset_semantic_link_service.delete_link_async(
            dataset_name,
            semantic_config_id,
            datasource_group_id=datasource_group_id,
            operator=str(getattr(user, "user_id", "") or ""),
            operator_name=str(getattr(user, "username", "") or ""),
        )
        return business_response(data={"semantic_config_id": semantic_config_id}, msg="删除成功")
    except Exception as error:
        return _error_response(error, "删除 Dataset 语义模型关联失败")
