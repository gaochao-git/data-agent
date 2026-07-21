"""数据源后台管理路由。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Set

from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import HumanMessage

from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..schema import (
    DbConnectionRequest,
    DatasourceColumnDictGenerateRequest,
    DatasourceColumnDictUpdateRequest,
    DatasourceGroupCreateRequest,
    DatasourceGroupMetadataSyncRequest,
    DatasourceGroupUpdateRequest,
    DatasourceTableBusinessDescUpdateRequest,
    DatasourceTableGroupsUpdateRequest,
    DatasourceUpdateRequest,
)
from ..services.semantic_json_parser import parse_llm_json
from ..services.datasource_manager import DATASOURCE_SYNC_TIMEOUT_SECONDS, get_datasource_manager
from ..services.llm_config import get_chat_data_llm
from .dependencies import _get_current_user
from .datasource_helpers import (
    _build_datasource_schema_health,
    _build_datasource_semantic_health,
    _merge_health_status,
    _normalize_source_item,
)

logger = get_logger(__name__)
router = APIRouter()


def _selected_table_names(raw_table_names: List[str]) -> List[str]:
    selected_table_names = []
    for table_name in raw_table_names or []:
        normalized_table_name = str(table_name or "").strip()
        if normalized_table_name and normalized_table_name not in selected_table_names:
            selected_table_names.append(normalized_table_name)
    return selected_table_names


def _normalize_group_sources(body: DatasourceGroupCreateRequest | DatasourceGroupUpdateRequest) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
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


def _normalize_template_source(body: DatasourceGroupCreateRequest | DatasourceGroupUpdateRequest) -> Dict[str, Any]:
    return _normalize_source_item(
        body.source_type,
        body.template_source.host,
        body.template_source.port,
        body.template_source.database,
        body.template_source.org_name,
        body.template_source.org_code,
        body.template_source.query_executor_type,
    )


@router.post("/admin/datasources/groups")
async def create_datasource_group(body: DatasourceGroupCreateRequest, request: Request):
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
            return business_response(msg=f"数据源组名称已存在: {group_name}", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

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
        return business_response(msg=f"创建失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


@router.patch("/admin/datasources/{datasource_id}")
async def update_datasource_meta(datasource_id: str, body: DatasourceUpdateRequest, request: Request):
    """更新数据源元信息（名称、描述）。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_meta_async(datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        name = None
        if body.name is not None:
            name = str(body.name).strip()
            if not name:
                return business_response(msg="数据源名称不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
            exists = await ds_manager.get_datasource_meta_by_group_name_async(name)
            if exists and exists.get("id") != datasource_id:
                return business_response(msg=f"数据源名称已存在: {name}", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        updated = await ds_manager.update_datasource_async(
            datasource_id,
            name=name,
            description=body.description,
            update_by=str(current_user.user_id),
            update_by_name=str(current_user.username or ""),
        )
        if not updated:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        return business_response(data=updated, msg="更新成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新数据源失败: {str(e)}", exc_info=True)
        return business_response(msg=f"更新失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


@router.put("/admin/datasources/{datasource_id}/group-config")
async def update_datasource_group_config(datasource_id: str, body: DatasourceGroupUpdateRequest, request: Request):
    """更新数据源组配置。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(current_user.username or "")
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_meta_async(datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        group_name = str(body.group_name or "").strip()
        if not group_name:
            return business_response(msg="数据源组名称不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        exists = await ds_manager.get_datasource_meta_by_group_name_async(group_name)
        if exists and exists.get("id") != datasource_id:
            return business_response(msg=f"数据源组名称已存在: {group_name}", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

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
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        return business_response(data=updated, msg="更新成功")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新数据源组失败: {str(e)}", exc_info=True)
        return business_response(msg=f"更新失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


@router.post("/admin/datasources/{datasource_id}/metadata-sync")
async def sync_datasource_group_metadata(datasource_id: str, body: DatasourceGroupMetadataSyncRequest, request: Request):
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
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        tables = updated.get("ds_tables") or []
        return business_response(
            data={"datasource": updated, "table_count": len(tables)},
            msg=f"同步成功，共 {len(tables)} 个表",
        )
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except asyncio.TimeoutError:
        logger.error("同步数据源组元数据超时: datasource_id=%s timeout=%ss", datasource_id, DATASOURCE_SYNC_TIMEOUT_SECONDS)
        return business_response(
            msg=f"同步超时（{DATASOURCE_SYNC_TIMEOUT_SECONDS}秒），请检查模板数据源配置或稍后重试",
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
        return business_response(msg="同步失败，请检查模板数据源配置或稍后重试", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


@router.patch("/admin/datasources/{datasource_id}/mapped-modules")
async def update_mapped_modules(datasource_id: str, body: dict, request: Request):
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
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        return business_response(data={"datasource_id": datasource_id, "mapped_modules": mapped_modules}, msg="更新成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新模块映射失败: {str(e)}", exc_info=True)
        return business_response(msg=f"更新失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


@router.get("/admin/datasources/{datasource_id}/table-groups")
async def get_datasource_table_groups(datasource_id: str, request: Request):
    """获取数据源内表分组。"""
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        groups = await ds_manager.get_table_groups_async(datasource_id)
        return business_response(data={"datasource_id": datasource_id, "groups": groups}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取表分组失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(msg=f"获取失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


@router.get("/admin/datasources/{datasource_id}/semantic-health")
async def get_datasource_semantic_health(datasource_id: str, request: Request):
    """校验数据源物理表结构与指标集配置是否一致。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        configs = await ds_manager.get_semantic_configs_async(datasource_id)
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
            msg="校验完成"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("指标集健康校验失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(msg=f"校验失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


@router.put("/admin/datasources/{datasource_id}/table-groups")
async def update_datasource_table_groups(datasource_id: str, body: DatasourceTableGroupsUpdateRequest, request: Request):
    """覆盖保存数据源内表分组。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        groups = [item.model_dump() for item in body.groups or []]
        success = await ds_manager.replace_table_groups_async(datasource_id, groups, operator=str(current_user.user_id), operator_name=str(current_user.username or ""))
        if not success:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        saved_groups = await ds_manager.get_table_groups_async(datasource_id)
        return business_response(data={"datasource_id": datasource_id, "groups": saved_groups}, msg="保存成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("保存表分组失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(msg=f"保存失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


@router.put("/admin/datasources/{datasource_id}/tables/{table_name}/business-desc")
async def update_datasource_table_business_desc(datasource_id: str, table_name: str, body: DatasourceTableBusinessDescUpdateRequest, request: Request):
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
            return business_response(msg=f"数据源或表不存在: {datasource_id}/{table_name}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        return business_response(data={"datasource_id": datasource_id, "table": table}, msg="保存成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("保存表业务说明失败: datasource_id=%s table_name=%s error=%s", datasource_id, table_name, e, exc_info=True)
        return business_response(msg=f"保存失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


@router.put("/admin/datasources/{datasource_id}/tables/{table_name}/columns/{column_name}/dict")
async def update_datasource_column_dict(datasource_id: str, table_name: str, column_name: str, body: DatasourceColumnDictUpdateRequest, request: Request):
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
            return business_response(msg=f"数据源、表或字段不存在: {datasource_id}/{table_name}/{column_name}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        return business_response(
            data={
                "datasource_id": datasource_id,
                "table_name": table_name,
                "column_name": column_name,
                "dict": saved_dict,
            },
            msg="保存成功"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("保存字段数据字典失败: datasource_id=%s table_name=%s column_name=%s error=%s", datasource_id, table_name, column_name, e, exc_info=True)
        return business_response(msg=f"保存失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


def _build_column_dict_generate_prompt(table_name: str, column_name: str, column: Dict[str, Any], text: str) -> str:
    """构造字段数据字典生成提示词。"""
    payload = {
        "table_name": table_name,
        "column_name": column_name,
        "column_type": column.get("mysql_type") or column.get("hive_type") or column.get("type") or "",
        "column_description": column.get("description") or "",
        "user_text": str(text or "").strip()[:10000],
    }
    return (
        "你是数据字典维护助手。请从用户文本中抽取当前字段的数据字典。\n"
        "要求：\n"
        "1) 只抽取文本中明确出现或能直接对应的信息，不要猜测、不要扩展。\n"
        "2) enum_values 表示字段真实入库值与业务含义，value 必须是真实入库值，例如 0、1、RUNNING；label 是业务含义，例如 下线、在线、运行中。\n"
        "3) unit 表示数值单位，例如 元、万元、秒；如果文本没有明确单位，返回空字符串。\n"
        "4) 不要生成 storage_format，column_type 只是数据库字段类型，不代表字段真实数据存储格式。\n"
        "5) 不要生成同义词。\n"
        "6) 只输出 JSON，不要解释文本。格式：{\"dict\":{\"enum_values\":[{\"value\":\"...\",\"label\":\"...\"}],\"unit\":\"...\"}}\n"
        "输入数据：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _parse_column_dict_generate_response(content: str) -> Dict[str, Any]:
    """解析字段数据字典生成模型返回。"""
    parsed = parse_llm_json(str(content or ""), strict=False)
    raw_dict = parsed.get("dict") if isinstance(parsed, dict) else {}
    if not isinstance(raw_dict, dict):
        raw_dict = {}
    raw_enums = raw_dict.get("enum_values") or raw_dict.get("enums") or []
    enum_values: List[Dict[str, str]] = []
    seen: Set[str] = set()
    if isinstance(raw_enums, list):
        for item in raw_enums:
            if not isinstance(item, dict):
                continue
            enum_value = str(item.get("value") or item.get("enum_value") or "").strip()[:128]
            enum_label = str(item.get("label") or item.get("enum_label") or "").strip()[:255]
            if not enum_value or enum_value in seen:
                continue
            seen.add(enum_value)
            enum_values.append({"value": enum_value, "label": enum_label})
    return {
        "enum_values": enum_values[:100],
        "synonyms": [],
        "storage_format": "",
        "unit": str(raw_dict.get("unit") or "").strip()[:100],
    }


@router.post("/admin/datasources/{datasource_id}/tables/{table_name}/columns/{column_name}/dict/generate")
async def generate_datasource_column_dict(datasource_id: str, table_name: str, column_name: str, body: DatasourceColumnDictGenerateRequest, request: Request):
    """根据文本生成字段数据字典候选值，不直接保存。"""
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        target_column = None
        for table in (datasource or {}).get("ds_tables") or []:
            if str(table.get("table_name") or "").strip() != table_name:
                continue
            for column in table.get("columns") or []:
                if str(column.get("name") or "").strip() == column_name:
                    target_column = column
                    break
        if not datasource or target_column is None:
            return business_response(msg=f"数据源、表或字段不存在: {datasource_id}/{table_name}/{column_name}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        prompt = _build_column_dict_generate_prompt(table_name, column_name, target_column, body.text)
        llm = get_chat_data_llm(streaming=False, max_tokens=1200)
        try:
            response = await llm.ainvoke([HumanMessage(content=prompt)])
        except Exception as exc:
            emit_llm_connection_alarm_if_needed(
                exc,
                ext1={
                    "purpose": "datasource_column_dict_generate",
                    "datasource_id": datasource_id,
                    "table_name": table_name,
                    "column_name": column_name,
                },
            )
            raise
        content = response.content if hasattr(response, "content") else str(response)
        column_dict = _parse_column_dict_generate_response(str(content))
        return business_response(
            data={
                "datasource_id": datasource_id,
                "table_name": table_name,
                "column_name": column_name,
                "dict": column_dict,
            },
            msg="生成成功"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("生成字段数据字典失败: datasource_id=%s table_name=%s column_name=%s error=%s", datasource_id, table_name, column_name, e, exc_info=True)
        return business_response(msg=f"生成失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


@router.post("/admin/datasources/{datasource_id}/set-default")
async def set_default_datasource(datasource_id: str, request: Request):
    """设置默认数据源。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        success = await ds_manager.set_default_datasource_async(datasource_id, update_by=str(current_user.user_id), update_by_name=str(current_user.username or ""))
        if not success:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        return business_response(data={"datasource_id": datasource_id, "is_default": True}, msg="设置成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置默认数据源失败: {str(e)}", exc_info=True)
        return business_response(msg=f"设置失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


@router.delete("/admin/datasources/{datasource_id}/set-default")
async def clear_default_datasource(datasource_id: str, request: Request):
    """取消默认数据源。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        success = await ds_manager.clear_default_datasource_async(datasource_id, update_by=str(current_user.user_id), update_by_name=str(current_user.username or ""))
        if not success:
            return business_response(msg=f"数据源不存在或非默认数据源: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        return business_response(data={"datasource_id": datasource_id, "is_default": False}, msg="取消成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"取消默认数据源失败: {str(e)}", exc_info=True)
        return business_response(msg=f"取消失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED, status="error")


@router.delete("/admin/datasources/{datasource_id}")
async def delete_datasource(datasource_id: str, request: Request):
    """删除数据源组。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(msg="数据源不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        success = await ds_manager.delete_datasource_async(datasource_id, update_by=str(current_user.user_id), update_by_name=str(current_user.username or ""))
        if not success:
            return business_response(msg="删除失败，数据源不存在或无法删除", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
        return business_response(data={"datasource_id": datasource_id}, msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        if isinstance(e, ValueError):
            return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
        logger.error(f"删除数据源失败: {str(e)}", exc_info=True)
        return business_response(msg=f"删除失败: {str(e)}", code=ErrorCode.CHATDATA_DATASOURCE_DELETE_FAILED, status="error")


@router.post("/admin/datasources/db-tables")
async def get_db_tables(request: DbConnectionRequest, http_request: Request):
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

        source_config = {
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
    except asyncio.TimeoutError:
        logger.error(
            "获取表列表超时: source_type=%s host=%s port=%s database=%s timeout=%ss",
            request.source_type,
            request.host,
            port,
            request.database,
            DATASOURCE_SYNC_TIMEOUT_SECONDS,
        )
        return business_response(
            msg=f"连接超时（{DATASOURCE_SYNC_TIMEOUT_SECONDS}秒），请检查数据源配置或稍后重试",
            code=ErrorCode.CHATDATA_DATASOURCE_CONNECTION_FAILED,
            status="error"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取表列表失败: {str(e)}", exc_info=True)
        return business_response(msg="连接失败，请检查数据源配置或稍后重试", code=ErrorCode.CHATDATA_DATASOURCE_CONNECTION_FAILED, status="error")
