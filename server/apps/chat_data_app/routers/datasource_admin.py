"""数据源后台管理路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response, error_response

from ..schema import (
    DatasourceColumnDictGenerateRequest,
    DatasourceColumnDictUpdateRequest,
    DatasourceGroupCreateRequest,
    DatasourceGroupMetadataSyncRequest,
    DatasourceGroupUpdateRequest,
    DatasourceTableBusinessDescUpdateRequest,
    DatasourceTableGroupsUpdateRequest,
    DatasourceTableIndexesUpdateRequest,
    DatasourceUpdateRequest,
    DbConnectionRequest,
)
from ..services.datasource.manager import get_datasource_manager, get_datasource_sync_timeout_seconds
from ..services.meta.global_enum_service import (
    MetaGlobalEnumInvalidError,
    MetaGlobalEnumNameInvalidError,
    MetaGlobalEnumNotFoundError,
)
from ..services.semantic.run.semantic_config_service import get_semantic_config_service
from .datasource_helpers import (
    _build_datasource_schema_health,
    _build_datasource_semantic_health,
    _get_datasource_column_dict_response,
    _get_datasource_table_column_dicts_response,
    _merge_health_status,
    _normalize_source_item,
)
from .dependencies import _get_current_user

logger = get_logger(__name__)
from . import JSON_API_RESPONSES

router = APIRouter(tags=["ChatData 数据源管理（Admin）"], responses=JSON_API_RESPONSES)


def _selected_table_names(raw_table_names: list[str]) -> list[str]:
    selected_table_names = []
    for table_name in raw_table_names or []:
        normalized_table_name = str(table_name or "").strip()
        if normalized_table_name and normalized_table_name not in selected_table_names:
            selected_table_names.append(normalized_table_name)
    return selected_table_names


def _normalize_group_sources(
    body: DatasourceGroupCreateRequest | DatasourceGroupUpdateRequest,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    template_source = _normalize_template_source(body)
    member_sources = [
        _normalize_source_item(
            body.source_type,
            item.host,
            item.port,
            item.database,
            item.org_name,
            item.org_code,
            item.query_executor_type,
        )
        for item in (body.member_sources or [])
    ]
    return template_source, member_sources


def _normalize_template_source(body: DatasourceGroupCreateRequest | DatasourceGroupUpdateRequest) -> dict[str, Any]:
    return _normalize_source_item(
        body.source_type,
        body.template_source.host,
        body.template_source.port,
        body.template_source.database,
        body.template_source.org_name,
        body.template_source.org_code,
        body.template_source.query_executor_type,
    )


@router.get("/admin/datasources")
async def get_admin_datasources(request: Request, include_tables: bool = True) -> JSONResponse:
    """获取后台配置页数据源列表，不按执行机构权限过滤。"""
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasources = await ds_manager.get_all_datasources_async(include_tables=include_tables)
        return business_response(data={"datasources": datasources}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取后台数据源列表失败: %s", e, exc_info=True)
        return error_response(e, fallback="获取失败", code=ErrorCode.CHATDATA_DATASOURCE_LIST_FAILED)


@router.post("/admin/datasources/groups")
async def create_datasource_group(body: DatasourceGroupCreateRequest, request: Request) -> JSONResponse:
    """创建数据源组。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(current_user.username or "")
        group_name = (body.group_name or "").strip()
        if not group_name:
            return business_response(msg="数据源组名称不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        ds_manager = get_datasource_manager()
        exists = await ds_manager.get_datasource_meta_by_group_name_async(group_name)
        if exists:
            return business_response(
                msg=f"数据源组名称已存在: {group_name}", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error"
            )

        template_source, member_sources = _normalize_group_sources(body)
        datasource = await ds_manager.create_datasource_group_from_sources_async(
            group_name=group_name,
            group_desc=(body.group_desc or "").strip(),
            source_type=body.source_type,
            template_source_config=template_source,
            member_source_configs=member_sources,
            query_context=(body.query_context or "").strip(),
            recommended_questions=body.recommended_questions,
            create_by=user_id,
            create_by_name=username,
        )
        return business_response(data=datasource, msg="创建成功")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建数据源组失败: {str(e)}", exc_info=True)
        return error_response(e, fallback="创建失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.patch("/admin/datasources/{datasource_id}")
async def update_datasource_meta(datasource_id: str, body: DatasourceUpdateRequest, request: Request) -> JSONResponse:
    """更新数据源元信息（名称、描述）。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_meta_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )

        name = None
        if body.name is not None:
            name = str(body.name).strip()
            if not name:
                return business_response(msg="数据源名称不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
            exists = await ds_manager.get_datasource_meta_by_group_name_async(name)
            if exists and exists.get("id") != datasource_id:
                return business_response(
                    msg=f"数据源名称已存在: {name}", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error"
                )

        updated = await ds_manager.update_datasource_async(
            datasource_id,
            name=name,
            description=body.description,
            update_by=str(current_user.user_id),
            update_by_name=str(current_user.username or ""),
        )
        if not updated:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        return business_response(data=updated, msg="更新成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新数据源失败: {str(e)}", exc_info=True)
        return error_response(e, fallback="更新失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.put("/admin/datasources/{datasource_id}/group-config")
async def update_datasource_group_config(
    datasource_id: str, body: DatasourceGroupUpdateRequest, request: Request
) -> JSONResponse:
    """更新数据源组配置。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(current_user.username or "")
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_meta_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )

        group_name = str(body.group_name or "").strip()
        if not group_name:
            return business_response(msg="数据源组名称不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        exists = await ds_manager.get_datasource_meta_by_group_name_async(group_name)
        if exists and exists.get("id") != datasource_id:
            return business_response(
                msg=f"数据源组名称已存在: {group_name}", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error"
            )

        template_source, member_sources = _normalize_group_sources(body)
        updated = await ds_manager.update_datasource_group_from_sources_async(
            datasource_id=datasource_id,
            group_name=group_name,
            group_desc=(body.group_desc or "").strip(),
            source_type=body.source_type,
            template_source_config=template_source,
            member_source_configs=member_sources,
            query_context=(body.query_context or "").strip(),
            recommended_questions=body.recommended_questions,
            update_by=user_id,
            update_by_name=username,
        )
        if not updated:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        return business_response(data=updated, msg="更新成功")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新数据源组失败: {str(e)}", exc_info=True)
        return error_response(e, fallback="更新失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.post("/admin/datasources/{datasource_id}/metadata-sync")
async def sync_datasource_group_metadata(
    datasource_id: str, body: DatasourceGroupMetadataSyncRequest, request: Request
) -> JSONResponse:
    """按模板数据源同步数据源组表结构元数据。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        selected_table_names = _selected_table_names(body.template_table_names)
        if not selected_table_names:
            return business_response(msg="请选择需要同步的表", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        updated = await ds_manager.sync_datasource_schema_from_source_async(
            datasource_id=datasource_id,
            table_names=selected_table_names,
            update_by=str(current_user.user_id),
            update_by_name=str(current_user.username or ""),
            user_id=current_user.user_id,
            username=current_user.username,
        )
        if not updated:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        tables = updated.get("ds_tables") or []
        return business_response(
            data={"datasource": updated, "table_count": len(tables)},
            msg=f"同步成功，共 {len(tables)} 个表",
        )
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except TimeoutError:
        sync_timeout = await get_datasource_sync_timeout_seconds()
        logger.error("同步数据源组元数据超时: datasource_id=%s timeout=%ss", datasource_id, sync_timeout)
        return business_response(
            msg=f"同步超时（{sync_timeout}秒），请检查模板数据源配置或稍后重试",
            code=ErrorCode.CHATDATA_DATASOURCE_CONNECTION_FAILED,
            status="error",
        )
    except ConnectionError as e:
        logger.error("同步数据源组元数据连接失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(
            msg="同步失败，请检查模板数据源配置或稍后重试",
            code=ErrorCode.CHATDATA_DATASOURCE_CONNECTION_FAILED,
            status="error",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"同步数据源组元数据失败: {str(e)}", exc_info=True)
        return business_response(
            msg="同步失败，请检查模板数据源配置或稍后重试",
            code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED,
            status="error",
        )


@router.patch("/admin/datasources/{datasource_id}/mapped-modules")
async def update_mapped_modules(datasource_id: str, body: dict, request: Request) -> JSONResponse:
    """更新数据源的模块映射。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        mapped_modules = body.get("mapped_modules", [])
        updated = await ds_manager.update_datasource_async(
            datasource_id,
            mapped_modules=mapped_modules,
            update_by=str(current_user.user_id),
            update_by_name=str(current_user.username or ""),
        )
        if not updated:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        return business_response(
            data={"datasource_id": datasource_id, "mapped_modules": mapped_modules}, msg="更新成功"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新模块映射失败: {str(e)}", exc_info=True)
        return error_response(e, fallback="更新失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.get("/admin/datasources/{datasource_id}/table-groups")
async def get_datasource_table_groups(datasource_id: str, request: Request) -> JSONResponse:
    """获取数据源内表分组。"""
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        # 存在性检查用轻量 meta：重接口会把全量表结构拉进来，而这个请求只需要分组关系
        datasource = await ds_manager.get_datasource_meta_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        groups = await ds_manager.get_table_groups_by_group_id_async(str(datasource.get("group_id") or ""))
        return business_response(data={"datasource_id": datasource_id, "groups": groups}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取表分组失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return error_response(e, fallback="获取失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.get("/admin/datasources/{datasource_id}/semantic-health")
async def get_datasource_semantic_health(datasource_id: str, request: Request) -> JSONResponse:
    """校验数据源物理表结构与指标集配置是否一致。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        configs = await get_semantic_config_service().get_configs_async(datasource_id)
        schema_health = None
        if datasource.get("source_type") in ("mysql", "hive"):
            try:
                physical_tables = await ds_manager.get_live_schema_async(
                    datasource_id,
                    user_id=str(current_user.user_id),
                    username=str(current_user.username or ""),
                )
                schema_health = _build_datasource_schema_health(datasource, physical_tables=physical_tables)
            except Exception as e:
                logger.error("物理表结构校验失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
                schema_health = _build_datasource_schema_health(datasource, error=e)
        else:
            schema_health = _build_datasource_schema_health(datasource)
        semantic_health = _build_datasource_semantic_health(datasource, configs)
        return business_response(
            data={
                **semantic_health,
                "status": _merge_health_status(schema_health.get("status"), semantic_health.get("status")),
                "schema": schema_health,
                "semantic": semantic_health,
            },
            msg="校验完成",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("指标集健康校验失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return error_response(e, fallback="校验失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.put("/admin/datasources/{datasource_id}/table-groups")
async def update_datasource_table_groups(
    datasource_id: str, body: DatasourceTableGroupsUpdateRequest, request: Request
) -> JSONResponse:
    """覆盖保存数据源内表分组。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        groups = [item.model_dump() for item in body.groups or []]
        success = await ds_manager.replace_table_groups_async(
            datasource_id, groups, operator=str(current_user.user_id), operator_name=str(current_user.username or "")
        )
        if not success:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        saved_groups = await ds_manager.get_table_groups_async(datasource_id)
        return business_response(data={"datasource_id": datasource_id, "groups": saved_groups}, msg="保存成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("保存表分组失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return error_response(e, fallback="保存失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.put("/admin/datasources/{datasource_id}/tables/{table_name}/business-desc")
async def update_datasource_table_business_desc(
    datasource_id: str, table_name: str, body: DatasourceTableBusinessDescUpdateRequest, request: Request
) -> JSONResponse:
    """更新数据源内单张表的业务说明。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        table = await ds_manager.update_table_business_desc_async(
            datasource_id,
            table_name,
            body.business_desc,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        if table is None:
            return business_response(
                msg=f"数据源或表不存在: {datasource_id}/{table_name}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        return business_response(data={"datasource_id": datasource_id, "table": table}, msg="保存成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "保存表业务说明失败: datasource_id=%s table_name=%s error=%s", datasource_id, table_name, e, exc_info=True
        )
        return error_response(e, fallback="保存失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.put("/admin/datasources/{datasource_id}/tables/{table_name}/columns/{column_name}/dict")
async def update_datasource_column_dict(
    datasource_id: str, table_name: str, column_name: str, body: DatasourceColumnDictUpdateRequest, request: Request
) -> JSONResponse:
    """覆盖保存数据源字段数据字典。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        saved_dict = await ds_manager.replace_column_dict_async(
            datasource_id,
            table_name,
            column_name,
            body.column_dict.model_dump(),
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        if saved_dict is None:
            return business_response(
                msg=f"数据源、表或字段不存在: {datasource_id}/{table_name}/{column_name}",
                code=ErrorCode.CHATDATA_NOT_FOUND,
                status="error",
            )
        return business_response(
            data={
                "datasource_id": datasource_id,
                "table_name": table_name,
                "column_name": column_name,
                "dict": saved_dict,
            },
            msg="保存成功",
        )
    except HTTPException:
        raise
    except MetaGlobalEnumNameInvalidError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_META_ENUM_NAME_INVALID, status="error")
    except MetaGlobalEnumInvalidError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_META_ENUM_INVALID, status="error")
    except MetaGlobalEnumNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_META_ENUM_NOT_FOUND, status="error")
    except Exception as e:
        logger.error(
            "保存字段数据字典失败: datasource_id=%s table_name=%s column_name=%s error=%s",
            datasource_id,
            table_name,
            column_name,
            e,
            exc_info=True,
        )
        return error_response(e, fallback="保存失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.get("/admin/datasources/{datasource_id}/tables/{table_name}/columns/{column_name}/dict")
async def get_admin_datasource_column_dict(
    datasource_id: str, table_name: str, column_name: str, request: Request
) -> JSONResponse:
    """获取后台数据源字段数据字典。"""
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        return await _get_datasource_column_dict_response(ds_manager, datasource_id, table_name, column_name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "后台获取字段数据字典失败: datasource_id=%s table_name=%s column_name=%s error=%s",
            datasource_id,
            table_name,
            column_name,
            e,
            exc_info=True,
        )
        return error_response(e, fallback="获取失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.get("/admin/datasources/{datasource_id}/tables/{table_name}/column-dicts")
async def get_admin_datasource_table_column_dicts(
    datasource_id: str, table_name: str, request: Request
) -> JSONResponse:
    """批量获取后台数据源单张表已维护字段数据字典。"""
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        return await _get_datasource_table_column_dicts_response(ds_manager, datasource_id, table_name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "后台批量获取字段数据字典失败: datasource_id=%s table_name=%s error=%s",
            datasource_id,
            table_name,
            e,
            exc_info=True,
        )
        return error_response(e, fallback="获取失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


# 字段字典候选生成的 LLM 编排已下沉 column_dict_generate；此处保留兼容导出，
# 供既有测试按原模块属性名引用。
from ..services.datasource.column_dict_generate import (  # noqa: E402
    find_datasource_column as _find_datasource_column,
)
from ..services.datasource.column_dict_generate import (
    generate_column_dict_candidates,
)
from ..services.datasource.column_dict_generate import (
    # 本模块未直接使用，但测试按 routers.datasource_admin._parse_... 打桩，属有意重导出
    parse_column_dict_generate_response as _parse_column_dict_generate_response,  # noqa: F401
)


@router.post("/admin/datasources/{datasource_id}/tables/{table_name}/columns/{column_name}/dict/generate")
async def generate_datasource_column_dict(
    datasource_id: str, table_name: str, column_name: str, body: DatasourceColumnDictGenerateRequest, request: Request
) -> JSONResponse:
    """根据文本生成字段数据字典候选值，不直接保存。"""
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        target_column = _find_datasource_column(datasource, table_name, column_name)
        if not datasource or target_column is None:
            return business_response(
                msg=f"数据源、表或字段不存在: {datasource_id}/{table_name}/{column_name}",
                code=ErrorCode.CHATDATA_NOT_FOUND,
                status="error",
            )
        column_dict = await generate_column_dict_candidates(
            target_column,
            table_name,
            column_name,
            body.text,
            datasource_id,
        )
        return business_response(
            data={
                "datasource_id": datasource_id,
                "table_name": table_name,
                "column_name": column_name,
                "dict": column_dict,
            },
            msg="生成成功",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "生成字段数据字典失败: datasource_id=%s table_name=%s column_name=%s error=%s",
            datasource_id,
            table_name,
            column_name,
            e,
            exc_info=True,
        )
        return error_response(e, fallback="生成失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.get("/admin/datasources/{datasource_id}/tables/{table_name}/indexes")
async def get_datasource_table_indexes(datasource_id: str, table_name: str, request: Request) -> JSONResponse:
    """获取数据源表的手动索引配置。"""
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        indexes = await ds_manager.get_table_indexes_async(datasource_id, table_name)
        if indexes is None:
            return business_response(
                msg=f"数据源或数据表不存在: {datasource_id}/{table_name}",
                code=ErrorCode.CHATDATA_NOT_FOUND,
                status="error",
            )
        return business_response(
            data={
                "datasource_id": datasource_id,
                "table_name": table_name,
                "indexes": indexes,
            },
            msg="获取成功",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "获取表索引配置失败: datasource_id=%s table_name=%s error=%s", datasource_id, table_name, e, exc_info=True
        )
        return business_response(
            msg="获取表索引配置失败，请稍后重试", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error"
        )


@router.put("/admin/datasources/{datasource_id}/tables/{table_name}/indexes")
async def update_datasource_table_indexes(
    datasource_id: str, table_name: str, body: DatasourceTableIndexesUpdateRequest, request: Request
) -> JSONResponse:
    """覆盖保存数据源表的手动索引配置。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        saved = await ds_manager.replace_table_indexes_async(
            datasource_id,
            table_name,
            [item.model_dump() for item in body.indexes],
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        if saved is None:
            return business_response(
                msg=f"数据源或数据表不存在: {datasource_id}/{table_name}",
                code=ErrorCode.CHATDATA_NOT_FOUND,
                status="error",
            )
        return business_response(
            data={
                "datasource_id": datasource_id,
                "table_name": table_name,
                "indexes": saved,
            },
            msg="保存成功",
        )
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_VALIDATION_FAILED, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "保存表索引配置失败: datasource_id=%s table_name=%s error=%s", datasource_id, table_name, e, exc_info=True
        )
        return business_response(
            msg="保存表索引配置失败，请稍后重试", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error"
        )


@router.post("/admin/datasources/{datasource_id}/set-default")
async def set_default_datasource(datasource_id: str, request: Request) -> JSONResponse:
    """设置默认数据源。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        success = await ds_manager.set_default_datasource_async(
            datasource_id, update_by=str(current_user.user_id), update_by_name=str(current_user.username or "")
        )
        if not success:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        return business_response(data={"datasource_id": datasource_id, "is_default": True}, msg="设置成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置默认数据源失败: {str(e)}", exc_info=True)
        return error_response(e, fallback="设置失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.delete("/admin/datasources/{datasource_id}/set-default")
async def clear_default_datasource(datasource_id: str, request: Request) -> JSONResponse:
    """取消默认数据源。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        success = await ds_manager.clear_default_datasource_async(
            datasource_id, update_by=str(current_user.user_id), update_by_name=str(current_user.username or "")
        )
        if not success:
            return business_response(
                msg=f"数据源不存在或非默认数据源: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        return business_response(data={"datasource_id": datasource_id, "is_default": False}, msg="取消成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"取消默认数据源失败: {str(e)}", exc_info=True)
        return error_response(e, fallback="取消失败", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED)


@router.delete("/admin/datasources/{datasource_id}")
async def delete_datasource(datasource_id: str, request: Request) -> JSONResponse:
    """删除数据源组。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(msg="数据源不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        success = await ds_manager.delete_datasource_async(
            datasource_id, update_by=str(current_user.user_id), update_by_name=str(current_user.username or "")
        )
        if not success:
            return business_response(
                msg="删除失败，数据源不存在或无法删除", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error"
            )
        return business_response(data={"datasource_id": datasource_id}, msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        if isinstance(e, ValueError):
            return error_response(e, fallback="删除失败", code=ErrorCode.CHATDATA_BAD_REQUEST)
        logger.error(f"删除数据源失败: {str(e)}", exc_info=True)
        return error_response(e, fallback="删除失败", code=ErrorCode.CHATDATA_DATASOURCE_DELETE_FAILED)


@router.post("/admin/datasources/db-tables")
async def get_db_tables(request: DbConnectionRequest, http_request: Request) -> JSONResponse:
    """获取数据库中的表列表（用于选择要同步的表）。"""
    port = request.port
    try:
        current_user = _get_current_user(http_request)
        user_params = {
            "user_id": str(current_user.user_id),
            "username": str(current_user.username or ""),
        }
        source_type = request.source_type
        if request.query_executor_type == "direct" and port is None:
            port = 10000 if source_type == "hive" else 3306

        logger.info(
            "获取 %s 表列表: executor=%s host=%s port=%s database=%s org_code=%s",
            source_type.upper(),
            request.query_executor_type,
            request.host,
            port,
            request.database,
            request.org_code,
        )

        source_config: dict[str, Any] = {
            "database": request.database,
            "org_code": request.org_code,
            "org_name": request.org_name,
            "query_executor_type": request.query_executor_type,
        }
        if request.query_executor_type == "direct":
            source_config["host"] = request.host
            source_config["port"] = port
        tables = await get_datasource_manager().get_physical_table_names_async(
            source_type,
            source_config,
            **user_params,
        )

        return business_response(data={"tables": tables, "total": len(tables)}, msg=f"获取成功，共 {len(tables)} 个表")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except TimeoutError:
        sync_timeout = await get_datasource_sync_timeout_seconds()
        logger.error(
            "获取表列表超时: source_type=%s host=%s port=%s database=%s timeout=%ss",
            request.source_type,
            request.host,
            port,
            request.database,
            sync_timeout,
        )
        return business_response(
            msg=f"连接超时（{sync_timeout}秒），请检查数据源配置或稍后重试",
            code=ErrorCode.CHATDATA_DATASOURCE_CONNECTION_FAILED,
            status="error",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取表列表失败: {str(e)}", exc_info=True)
        return business_response(
            msg="连接失败，请检查数据源配置或稍后重试",
            code=ErrorCode.CHATDATA_DATASOURCE_CONNECTION_FAILED,
            status="error",
        )
