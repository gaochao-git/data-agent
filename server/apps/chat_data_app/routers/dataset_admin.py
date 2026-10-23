"""Logical Dataset 管理路由：Dataset 身份、版本、分支和 member 绑定。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..schema import DatasetBranchCreateRequest, DatasetUpsertRequest
from ..services.dataset.dataset_service import (
    DatasetAlreadyExistsError,
    DatasetInvalidError,
    DatasetNotFoundError,
    logical_dataset_service,
)
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
    logger.error("%s: %s", fallback, error, exc_info=True)
    return business_response(msg=fallback, code=ErrorCode.CHATDATA_DATASET_OPERATION_FAILED, status="error")


def _dataset_payload(body: DatasetUpsertRequest) -> dict:
    payload = body.model_dump()
    for column in payload.get("columns") or []:
        if not column.get("physical_column"):
            column["physical_column"] = column["name"]
    return payload


@router.get("/admin/meta/datasets")
async def list_datasets(request: Request) -> JSONResponse:
    _get_current_user(request)
    try:
        return business_response(data={"datasets": await logical_dataset_service.list_datasets_async()}, msg="获取成功")
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
