"""Semantic config routes for ChatData."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Request
from langchain_core.messages import HumanMessage

from auth import AuthenticationError

from utils.error_codes import ErrorCode
from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.logger import get_logger
from utils.llm_json import parse_llm_json
from utils.response import business_response
from ..agent.prompts.dim_metric_auto_prefill import build_metric_recommendation_prompt
from ..schema import (
    SemanticAutoPrefillRequest,
    SemanticConfigCreateRequest,
    SemanticConfigUpdateRequest,
    SemanticHistoryLearningRequest,
    SemanticPrefillRequest,
)
from ..services.datasource.manager import get_datasource_manager
from ..services.llm_config import get_chat_data_llm
from ..services.semantic.run.semantic_history_learning_service import (
    SemanticHistoryLearningService,
    _generate_history_learning_suggestions_by_llm as _service_generate_history_learning_suggestions_by_llm,
    _history_belongs_to_config as _service_history_belongs_to_config,
    _load_history_conversation_context,
    build_semantic_history_learning_service,
)
from ..services.semantic.run.semantic_config_service import (
    can_mutate_semantic_config,
    get_semantic_config_service,
    is_private_semantic_config_owner,
    is_public_semantic_config,
    validate_semantic_config_for_save,
)
from ..services.sql_run.parser import extract_physical_tables
from .dependencies import _get_current_user

logger = get_logger(__name__)
router = APIRouter()

PREFILL_MAX_RECOMMENDED_METRICS = 10
PREFILL_ALLOWED_METRIC_AGGS = {"SUM", "AVG", "MAX", "MIN", "COUNT"}
PREFILL_DISPLAY_NAME_MAX_LENGTH = 15


def _can_manage_semantic_config(config: Dict[str, Any], user_id: str) -> bool:
    """普通用户只能管理自己创建的个人指标集。"""
    return is_private_semantic_config_owner(config, user_id)


def _with_semantic_config_permissions(config: Dict[str, Any], user_id: str, username: str = "", admin_manage: bool = False) -> Dict[str, Any]:
    """补充指标集权限字段。"""
    item = dict(config or {})
    item["scope_type"] = item.get("scope_type") or "public"
    item["can_manage"] = can_mutate_semantic_config(item, user_id, admin_manage)
    return item


async def _get_semantic_configs_for_user(semantic_service: Any, datasource_id: str, user_id: str, username: str = "") -> List[Dict[str, Any]]:
    """读取查询链路可见指标集：公共指标集 + 当前用户个人指标集。"""
    configs = await semantic_service.get_configs_async(datasource_id, user_id)
    return [_with_semantic_config_permissions(config, user_id, username) for config in (configs or [])]


async def _get_my_semantic_configs(semantic_service: Any, datasource_id: str, user_id: str, username: str = "") -> List[Dict[str, Any]]:
    """读取普通维护页可维护的个人指标集。"""
    configs = await semantic_service.get_configs_async(datasource_id, user_id)
    return [_with_semantic_config_permissions(config, user_id, username) for config in (configs or []) if is_private_semantic_config_owner(config, user_id)]


async def _get_admin_semantic_configs(semantic_service: Any, datasource_id: str, user_id: str, username: str = "") -> List[Dict[str, Any]]:
    """读取后台维护页可维护的公共指标集。"""
    configs = await semantic_service.get_configs_async(datasource_id)
    return [_with_semantic_config_permissions(config, user_id, username, admin_manage=True) for config in (configs or []) if is_public_semantic_config(config)]


def _quote_identifier(name: str) -> str:
    """转义 SQL 标识符。"""
    safe = (name or "").replace("`", "``")
    return f"`{safe}`"


def _history_table_parser(sql: str, source_type: str):
    return extract_physical_tables(sql, source_type=source_type)


def _history_belongs_to_config(
    query_type: str,
    task_payload: Dict[str, Any],
    sql: str,
    config_id: str,
    config: Dict[str, Any],
    source_type: str,
) -> tuple[bool, str, List[str], str]:
    """判断历史记录是否属于当前指标集。"""
    return _service_history_belongs_to_config(
        query_type,
        task_payload,
        sql,
        config_id,
        config,
        source_type,
        table_parser=_history_table_parser,
    )


async def _generate_history_learning_suggestions_by_llm(
    datasource: Dict[str, Any],
    config: Dict[str, Any],
    samples: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """调用大模型基于历史生成指标集维护建议。"""
    return await _service_generate_history_learning_suggestions_by_llm(
        datasource,
        config,
        samples,
        llm_factory=get_chat_data_llm,
    )


def _build_history_learning_service() -> SemanticHistoryLearningService:
    """构建历史学习服务。"""
    return build_semantic_history_learning_service(
        context_loader=_load_history_conversation_context,
        suggestion_loader=_generate_history_learning_suggestions_by_llm,
        history_matcher=_history_belongs_to_config,
    )


def _normalize_prefill_display_name(value: Any, fallback: str) -> str:
    """归一化自动预填返回的短显示名。"""
    source = str(value or fallback or "").strip()
    if not source:
        return ""
    first_part = re.split(r"[，,。；;：:\n\r]", source, maxsplit=1)[0].strip() or source
    without_relation = re.sub(r"(?:同样)?关联\s+[A-Za-z_][A-Za-z0-9_.]*.*$", "", first_part, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"[`\"'“”‘’、/\\|()（）\[\]【】{}<>《》.!！?？~@#$%^&*+=-]", "", without_relation or first_part)
    cleaned = re.sub(r"\s+", "", cleaned).strip()
    display_name = cleaned or str(fallback or "").strip()
    return display_name[:PREFILL_DISPLAY_NAME_MAX_LENGTH]


def _build_prefill_alias_map(tables: List[Dict]) -> Dict[str, List[str]]:
    """构造物理表到表角色别名列表的映射。"""
    alias_map: Dict[str, List[str]] = {}
    for table in tables or []:
        table_name = str((table or {}).get("table_name") or (table or {}).get("table") or "").strip()
        table_alias = str((table or {}).get("table_alias") or table_name).strip()
        if not table_name or not table_alias:
            continue
        aliases = alias_map.setdefault(table_name, [])
        if table_alias not in aliases:
            aliases.append(table_alias)
    return alias_map


def _dedupe_prefill_display_names(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """自动预填字段重名时追加表角色前缀。"""

    def build_prefixed_name(table_alias: str, name: str) -> str:
        alias = re.sub(r"[`\"'“”‘’、/\\|()（）\[\]【】{}<>《》.!！?？~@#$%^&*+=-]", "", str(table_alias or "").strip())
        alias = re.sub(r"\s+", "", alias)
        if not alias:
            return name
        candidate = _normalize_prefill_display_name(f"{alias}_{name}", name)
        if len(f"{alias}_{name}") <= PREFILL_DISPLAY_NAME_MAX_LENGTH:
            return candidate
        budget = PREFILL_DISPLAY_NAME_MAX_LENGTH - len(name) - 1
        if budget <= 0:
            return name[:PREFILL_DISPLAY_NAME_MAX_LENGTH]
        alias_parts = [part for part in alias.split("_") if part]
        alias_part = alias_parts[-1] if alias_parts and len(alias_parts[-1]) >= 2 else alias
        return _normalize_prefill_display_name(f"{alias_part[:budget]}_{name}", name)

    name_counts: Dict[str, int] = {}
    for item in items:
        name = str(item.get("name") or "").strip()
        if name:
            name_counts[name] = name_counts.get(name, 0) + 1
    used_names: Set[str] = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        table_alias = str(item.get("table_alias") or item.get("table") or "").strip()
        if name_counts.get(name, 0) > 1 and table_alias:
            name = build_prefixed_name(table_alias, name)
        candidate = name
        index = 2
        while candidate in used_names:
            suffix = str(index)
            candidate = f"{name[:max(PREFILL_DISPLAY_NAME_MAX_LENGTH - len(suffix), 1)]}{suffix}"
            index += 1
        item["name"] = candidate
        used_names.add(candidate)
    return items


def _build_prefill_table_payload(tables: List[Dict]) -> List[Dict[str, Any]]:
    """构造自动预填使用的紧凑表结构。"""
    table_payload = []
    seen_tables: Set[str] = set()
    for table in tables or []:
        table_name = str(table.get("table_name") or "").strip()
        if not table_name or table_name in seen_tables:
            continue
        seen_tables.add(table_name)
        columns = []
        for col in table.get("columns", []) or []:
            col_name = str(col.get("name") or "").strip()
            if not col_name:
                continue
            columns.append({
                "name": col_name,
                "type": col.get("mysql_type") or col.get("hive_type") or col.get("type") or "",
                "description": col.get("description") or "",
            })
        table_payload.append({
            "table": table_name,
            "description": table.get("description") or "",
            "columns": columns,
        })
    return table_payload


def _parse_prefill_dimension_name_map(parsed: Dict[str, Any], table_payload: List[Dict[str, Any]]) -> Dict[str, str]:
    """解析模型生成的物理字段显示名称。"""
    raw_fields = parsed.get("fields", []) if isinstance(parsed, dict) else []
    if not isinstance(raw_fields, list):
        return {}
    table_columns = {
        table.get("table", ""): {col.get("name", "") for col in table.get("columns", []) if col.get("name")}
        for table in table_payload
        if table.get("table")
    }
    name_map: Dict[str, str] = {}
    for item in raw_fields:
        if not isinstance(item, dict):
            continue
        table_name = str(item.get("table") or "").strip()
        column_name = str(item.get("column") or "").strip()
        if table_name not in table_columns or column_name not in table_columns[table_name]:
            continue
        name_map[f"{table_name}.{column_name}"] = _normalize_prefill_display_name(item.get("name"), column_name)
    return name_map


def _build_prefill_dimension_fields(
    table_payload: List[Dict[str, Any]],
    table_alias_map: Optional[Dict[str, List[str]]] = None,
    dimension_name_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    """将物理字段全部作为维度预填。"""
    fields: List[Dict[str, str]] = []
    alias_map = table_alias_map or {table.get("table", ""): [table.get("table", "")] for table in table_payload if table.get("table")}
    name_map = dimension_name_map or {}
    for table in table_payload:
        table_name = table.get("table", "")
        if not table_name:
            continue
        aliases = alias_map.get(table_name) or [table_name]
        for col in table.get("columns", []):
            column_name = col.get("name", "")
            if not column_name:
                continue
            display_name = name_map.get(f"{table_name}.{column_name}") or _normalize_prefill_display_name(column_name, column_name)
            for table_alias in aliases:
                fields.append({
                    "table": table_name,
                    "table_alias": table_alias,
                    "column": column_name,
                    "role": "dimension",
                    "expression": _quote_identifier(column_name),
                    "name": display_name,
                })
    return fields


def _parse_metric_items(
    parsed: Dict[str, Any],
    table_payload: List[Dict[str, Any]],
    max_metrics: int = PREFILL_MAX_RECOMMENDED_METRICS,
    table_alias_map: Optional[Dict[str, List[str]]] = None,
) -> List[Dict[str, str]]:
    """解析并校验模型推荐的原子指标列表。"""
    raw_metrics = parsed.get("metrics", []) if isinstance(parsed, dict) else []
    if not isinstance(raw_metrics, list):
        return []
    table_columns = {
        table.get("table", ""): {col.get("name", "") for col in table.get("columns", []) if col.get("name")}
        for table in table_payload
        if table.get("table")
    }
    alias_map = table_alias_map or {table.get("table", ""): [table.get("table", "")] for table in table_payload if table.get("table")}
    normalized: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for item in raw_metrics:
        if not isinstance(item, dict):
            continue
        table_name = str(item.get("table") or "").strip()
        column_name = str(item.get("column") or item.get("source_column") or "").strip()
        agg = str(item.get("agg") or "").strip().upper()
        if table_name not in table_columns or column_name not in table_columns[table_name] or agg not in PREFILL_ALLOWED_METRIC_AGGS:
            continue
        key = f"{table_name}.{column_name}.{agg}"
        if key in seen:
            continue
        seen.add(key)
        expression = f"{agg}({_quote_identifier(column_name)})"
        aliases = alias_map.get(table_name) or [table_name]
        for table_alias in aliases:
            normalized.append({
                "table": table_name,
                "table_alias": table_alias,
                "column": column_name,
                "source_column": column_name,
                "role": "metric",
                "field_name": str(item.get("field_name") or f"{column_name}_{agg.lower()}").strip() or f"{column_name}_{agg.lower()}",
                "name": _normalize_prefill_display_name(item.get("name"), column_name),
                "agg": agg,
                "expression": expression,
            })
            if len(normalized) >= max_metrics:
                break
        if len(normalized) >= max_metrics:
            break
    return _dedupe_prefill_display_names(normalized)


def _parse_metric_recommendation_response(
    content: str,
    table_payload: List[Dict[str, Any]],
    max_metrics: int = PREFILL_MAX_RECOMMENDED_METRICS,
    table_alias_map: Optional[Dict[str, List[str]]] = None,
) -> List[Dict[str, str]]:
    """解析并校验模型推荐的原子指标。"""
    parsed = parse_llm_json(str(content))
    return _parse_metric_items(parsed, table_payload, max_metrics, table_alias_map)


def _parse_prefill_recommendation_response(
    content: str,
    table_payload: List[Dict[str, Any]],
    max_metrics: int = PREFILL_MAX_RECOMMENDED_METRICS,
    table_alias_map: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, List[Dict[str, str]]]:
    """解析模型返回的字段显示名称和推荐指标。"""
    parsed = parse_llm_json(str(content))
    dimension_name_map = _parse_prefill_dimension_name_map(parsed, table_payload)
    fields = _build_prefill_dimension_fields(table_payload, table_alias_map, dimension_name_map)
    metric_fields = _parse_metric_items(parsed, table_payload, max_metrics, table_alias_map)
    return {"fields": fields, "metrics": metric_fields}


async def _call_metric_recommendation_llm(
    table_payload: List[Dict[str, Any]],
    business_context: str = "",
    max_metrics: int = PREFILL_MAX_RECOMMENDED_METRICS,
    table_alias_map: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, List[Dict[str, str]]]:
    """调用大模型推荐少量原子指标。"""
    prompt = build_metric_recommendation_prompt(
        table_payload=table_payload,
        business_context=business_context,
        max_metrics=max_metrics,
    )
    llm = get_chat_data_llm(streaming=False, max_tokens=2000)
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:
        emit_llm_connection_alarm_if_needed(
            exc,
            ext1={"purpose": "semantic_metric_recommendation"},
        )
        raise
    content = response.content if hasattr(response, "content") else str(response)
    return _parse_prefill_recommendation_response(str(content), table_payload, max_metrics, table_alias_map)


async def _prefill_by_llm(
    tables: List[Dict],
    business_context: str = "",
) -> Optional[Dict[str, Any]]:
    """优化字段显示名称，并推荐少量高置信度原子指标。"""
    if not tables:
        return {"fields": []}
    table_alias_map = _build_prefill_alias_map(tables)
    table_payload = _build_prefill_table_payload(tables)
    if not table_payload:
        return {"fields": []}
    fields = _build_prefill_dimension_fields(table_payload, table_alias_map)
    try:
        recommendation = await _call_metric_recommendation_llm(
            table_payload=table_payload,
            business_context=business_context,
            max_metrics=PREFILL_MAX_RECOMMENDED_METRICS,
            table_alias_map=table_alias_map,
        )
        if isinstance(recommendation, dict):
            fields = recommendation.get("fields") or fields
            metric_fields = recommendation.get("metrics") or []
        else:
            metric_fields = recommendation or []
    except Exception as e:
        logger.warning("自动预填调用大模型失败: %s", e)
        metric_fields = []
    return {"fields": _dedupe_prefill_display_names(fields + metric_fields)}


def _resolve_prefill_tables(datasource: Dict, selected_tables: List[str]) -> List[Dict]:
    """根据勾选的表名过滤待预填表结构。"""
    selected = set(selected_tables or [])
    target_tables = []
    for table in datasource.get("ds_tables", []) or []:
        table_name = table.get("table_name")
        if not table_name:
            continue
        if selected and table_name not in selected:
            continue
        target_tables.append(table)
    return target_tables


@router.get("/datasources/{datasource_id}/semantic-configs")
async def list_semantic_configs(datasource_id: str, request: Request):
    """获取查询链路可见指标集：公共指标集 + 当前用户个人指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        if not await ds_manager.has_datasource_async(datasource_id):
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        configs = await _get_semantic_configs_for_user(get_semantic_config_service(), datasource_id, user_id, username)
        return business_response(data={"configs": configs, "total": len(configs)}, msg="获取成功")
    except Exception as e:
        logger.error(f"获取语义配置列表失败: {str(e)}", exc_info=True)
        return business_response(msg=f"获取失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.get("/datasources/{datasource_id}/my-semantic-configs")
async def list_my_semantic_configs(datasource_id: str, request: Request):
    """获取普通维护页可维护的个人指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        if not await ds_manager.has_datasource_async(datasource_id):
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        configs = await _get_my_semantic_configs(get_semantic_config_service(), datasource_id, user_id, username)
        return business_response(data={"configs": configs, "total": len(configs)}, msg="获取成功")
    except Exception as e:
        logger.error(f"获取个人语义配置列表失败: {str(e)}", exc_info=True)
        return business_response(msg=f"获取失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.get("/admin/datasources/{datasource_id}/semantic-configs")
async def list_admin_semantic_configs(datasource_id: str, request: Request):
    """后台获取可维护的公共指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        if not await ds_manager.has_datasource_async(datasource_id):
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        configs = await _get_admin_semantic_configs(get_semantic_config_service(), datasource_id, user_id, username)
        return business_response(data={"configs": configs, "total": len(configs)}, msg="获取成功")
    except Exception as e:
        logger.error(f"获取后台语义配置列表失败: {str(e)}", exc_info=True)
        return business_response(msg=f"获取失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


async def _create_semantic_config_with_scope(
    datasource_id: str,
    payload: SemanticConfigCreateRequest,
    request: Request,
    *,
    scope_type: str,
    admin_manage: bool = False,
):
    """按指定范围创建指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        metrics = [m.model_dump() for m in payload.metrics]
        dimensions = [d.model_dump() for d in payload.dimensions]
        relationships = [r.model_dump() for r in payload.relationships]
        validation_error = await validate_semantic_config_for_save(
            ds_manager,
            datasource_id,
            datasource,
            metrics,
            dimensions,
            relationships,
        )
        if validation_error:
            return business_response(msg=validation_error, code=ErrorCode.CHATDATA_VALIDATION_FAILED, status="error")

        config = await get_semantic_config_service().create_async(
            datasource_id=datasource_id,
            name=payload.name,
            description=payload.description,
            ontology_rules=payload.ontology_rules,
            enabled=payload.enabled,
            metrics=metrics,
            dimensions=dimensions,
            relationships=relationships,
            scope_type=scope_type,
            create_by=user_id,
            create_by_name=username,
        )
        if not config:
            return business_response(msg="创建失败", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")

        logger.info(f"创建语义配置: {config['id']} - {config['name']}")
        return business_response(data=_with_semantic_config_permissions(config, user_id, username, admin_manage=admin_manage), msg="创建成功")
    except Exception as e:
        logger.error(f"创建语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"创建失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/datasources/{datasource_id}/semantic-configs")
async def create_semantic_config(datasource_id: str, payload: SemanticConfigCreateRequest, request: Request):
    """创建个人指标集。"""
    return await _create_semantic_config_with_scope(datasource_id, payload, request, scope_type="private")


@router.post("/admin/datasources/{datasource_id}/semantic-configs")
async def create_admin_semantic_config(datasource_id: str, payload: SemanticConfigCreateRequest, request: Request):
    """创建公共指标集。"""
    _get_current_user(request)
    return await _create_semantic_config_with_scope(datasource_id, payload, request, scope_type="public", admin_manage=True)


async def _generate_basic_semantic_configs_route(
    datasource_id: str,
    request: Request,
    *,
    scope_type: str,
):
    """按物理表批量创建禁用的基础单表模型。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        result = await get_semantic_config_service().generate_basic_single_table_configs_async(
            datasource_id,
            scope_type=scope_type,
            create_by=user_id,
            create_by_name=username,
        )
        logger.info(
            "批量生成基础语义模型: datasource_id=%s scope_type=%s created=%s skipped=%s failed=%s",
            datasource_id,
            scope_type,
            result["created"],
            result["skipped"],
            result["failed"],
        )
        return business_response(data=result, msg="基础模型生成完成")
    except ValueError as e:
        code = ErrorCode.CHATDATA_NOT_FOUND if str(e).startswith("数据源不存在") else ErrorCode.CHATDATA_VALIDATION_FAILED
        return business_response(msg=str(e), code=code, status="error")
    except Exception as e:
        logger.error("批量生成基础语义模型失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(msg=f"生成失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/datasources/{datasource_id}/semantic-configs/generate-basic")
async def generate_basic_semantic_configs(datasource_id: str, request: Request):
    """为当前用户批量创建禁用的个人基础单表模型。"""
    return await _generate_basic_semantic_configs_route(datasource_id, request, scope_type="private")


@router.post("/admin/datasources/{datasource_id}/semantic-configs/generate-basic")
async def generate_admin_basic_semantic_configs(datasource_id: str, request: Request):
    """后台批量创建禁用的公共基础单表模型。"""
    _get_current_user(request)
    return await _generate_basic_semantic_configs_route(datasource_id, request, scope_type="public")


async def _auto_prefill_semantic_fields_route(
    datasource_id: str,
    config_id: str,
    payload: SemanticAutoPrefillRequest,
    request: Request,
    *,
    admin_manage: bool = False,
):
    """按规则自动预填维度/指标/表达式。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        config = await get_semantic_config_service().get_config_async(datasource_id, config_id)
        if not config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        if not can_mutate_semantic_config(config, user_id, admin_manage):
            return business_response(msg="无权限操作该指标集", code=ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED, status="error")
        target_tables = _resolve_prefill_tables(datasource, payload.selected_tables)

        prefill_result = await _prefill_by_llm(
            target_tables,
            payload.business_context or "",
        )
        if prefill_result is None:
            return business_response(msg="自动预填失败：大模型返回结果不完整或不可解析，请重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")
        fields = prefill_result.get("fields", [])

        return business_response(data={"fields": fields, "total": len(fields)}, msg="自动预填完成")
    except Exception as e:
        logger.error(f"自动预填字段失败: {str(e)}", exc_info=True)
        return business_response(msg=f"自动预填失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/datasources/{datasource_id}/semantic-configs/{config_id}/auto-prefill")
async def auto_prefill_semantic_fields(datasource_id: str, config_id: str, payload: SemanticAutoPrefillRequest, request: Request):
    """按个人指标集自动预填维度/指标/表达式。"""
    return await _auto_prefill_semantic_fields_route(datasource_id, config_id, payload, request, admin_manage=False)


@router.post("/admin/datasources/{datasource_id}/semantic-configs/{config_id}/auto-prefill")
async def auto_prefill_admin_semantic_fields(datasource_id: str, config_id: str, payload: SemanticAutoPrefillRequest, request: Request):
    """后台按公共指标集自动预填维度/指标/表达式。"""
    _get_current_user(request)
    return await _auto_prefill_semantic_fields_route(datasource_id, config_id, payload, request, admin_manage=True)


async def _semantic_prefill_route(payload: SemanticPrefillRequest, request: Request, *, admin_manage: bool = False):
    """语义字段预填短路由，前端直接传待预填表结构。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(payload.datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {payload.datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        config = await get_semantic_config_service().get_config_async(payload.datasource_id, payload.config_id)
        if not config:
            return business_response(msg=f"语义配置不存在: {payload.config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        if not can_mutate_semantic_config(config, user_id, admin_manage):
            return business_response(msg="无权限操作该指标集", code=ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED, status="error")

        target_tables = [table.model_dump() for table in payload.tables]
        prefill_result = await _prefill_by_llm(
            target_tables,
            payload.business_context or "",
        )
        if prefill_result is None:
            return business_response(msg="自动预填失败：大模型返回结果不完整或不可解析，请重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")
        fields = prefill_result.get("fields", [])

        return business_response(data={"fields": fields, "total": len(fields)}, msg="自动预填完成")
    except Exception as e:
        logger.error(f"语义字段预填失败: {str(e)}", exc_info=True)
        return business_response(msg=f"自动预填失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/semantic/prefill")
async def semantic_prefill(payload: SemanticPrefillRequest, request: Request):
    """按个人指标集预填语义字段。"""
    return await _semantic_prefill_route(payload, request, admin_manage=False)


@router.post("/admin/semantic/prefill")
async def admin_semantic_prefill(payload: SemanticPrefillRequest, request: Request):
    """后台按公共指标集预填语义字段。"""
    _get_current_user(request)
    return await _semantic_prefill_route(payload, request, admin_manage=True)


async def _learn_semantic_config_from_history_route(
    datasource_id: str,
    config_id: str,
    payload: SemanticHistoryLearningRequest,
    request: Request,
    *,
    admin_manage: bool = False,
):
    """基于当前指标集的智能问数/可视化查询历史生成维护建议。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        config = await get_semantic_config_service().get_config_async(datasource_id, config_id)
        if not config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        if not can_mutate_semantic_config(config, user_id, admin_manage):
            return business_response(msg="无权限操作该指标集", code=ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED, status="error")

        data = await _build_history_learning_service().learn(
            datasource_id=datasource_id,
            config_id=config_id,
            user_id=user_id,
            datasource=datasource,
            config=config,
            days=payload.days,
            limit=payload.limit,
            sources=payload.sources,
            include_failed=payload.include_failed,
        )
        return business_response(data=data, msg="学习完成")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_VALIDATION_FAILED, status="error")
    except Exception as e:
        logger.error("历史学习失败: datasource_id=%s config_id=%s error=%s", datasource_id, config_id, e, exc_info=True)
        return business_response(msg=f"历史学习失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/datasources/{datasource_id}/semantic-configs/{config_id}/history-learning")
async def learn_semantic_config_from_history(
    datasource_id: str,
    config_id: str,
    payload: SemanticHistoryLearningRequest,
    request: Request,
):
    """基于个人指标集的查询历史生成维护建议。"""
    return await _learn_semantic_config_from_history_route(datasource_id, config_id, payload, request, admin_manage=False)


@router.post("/admin/datasources/{datasource_id}/semantic-configs/{config_id}/history-learning")
async def learn_admin_semantic_config_from_history(
    datasource_id: str,
    config_id: str,
    payload: SemanticHistoryLearningRequest,
    request: Request,
):
    """后台基于公共指标集的查询历史生成维护建议。"""
    _get_current_user(request)
    return await _learn_semantic_config_from_history_route(datasource_id, config_id, payload, request, admin_manage=True)


async def _update_semantic_config(
    datasource_id: str,
    config_id: str,
    payload: SemanticConfigUpdateRequest,
    request: Request,
    *,
    admin_manage: bool = False,
):
    """更新指标集。普通入口仅允许维护本人个人指标集；后台入口允许维护公共指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        semantic_service = get_semantic_config_service()
        current_config = await semantic_service.get_config_async(datasource_id, config_id)
        if not current_config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        if not can_mutate_semantic_config(current_config, user_id, admin_manage):
            return business_response(msg="无权限操作该指标集", code=ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED, status="error")

        update_kwargs = {}
        if payload.name is not None:
            update_kwargs["name"] = payload.name
        if payload.description is not None:
            update_kwargs["description"] = payload.description
        if payload.ontology_rules is not None:
            update_kwargs["ontology_rules"] = payload.ontology_rules
        if payload.enabled is not None:
            update_kwargs["enabled"] = payload.enabled
        if payload.metrics is not None:
            update_kwargs["metrics"] = [m.model_dump() for m in payload.metrics]
        if payload.dimensions is not None:
            update_kwargs["dimensions"] = [d.model_dump() for d in payload.dimensions]
        if payload.relationships is not None:
            update_kwargs["relationships"] = [r.model_dump() for r in payload.relationships]

        merged_metrics = update_kwargs.get("metrics", current_config.get("metrics", []))
        merged_dimensions = update_kwargs.get("dimensions", current_config.get("dimensions", []))
        merged_relationships = update_kwargs.get("relationships", current_config.get("relationships", []))
        validation_error = await validate_semantic_config_for_save(
            ds_manager,
            datasource_id,
            datasource,
            merged_metrics,
            merged_dimensions,
            merged_relationships,
        )
        if validation_error:
            return business_response(msg=validation_error, code=ErrorCode.CHATDATA_VALIDATION_FAILED, status="error")

        config = await semantic_service.update_async(
            datasource_id=datasource_id,
            config_id=config_id,
            update_by=user_id,
            update_by_name=username,
            **update_kwargs,
        )
        if not config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        logger.info(f"更新语义配置: {config_id}")
        return business_response(data=_with_semantic_config_permissions(config, user_id, username, admin_manage=admin_manage), msg="更新成功")
    except Exception as e:
        logger.error(f"更新语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"更新失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


async def _delete_semantic_config(datasource_id: str, config_id: str, request: Request, *, admin_manage: bool = False):
    """删除指标集。普通入口仅允许维护本人个人指标集；后台入口允许维护公共指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        semantic_service = get_semantic_config_service()
        current_config = await semantic_service.get_config_async(datasource_id, config_id)
        if not current_config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        if not can_mutate_semantic_config(current_config, user_id, admin_manage):
            return business_response(msg="无权限操作该指标集", code=ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED, status="error")

        success = await semantic_service.delete_async(datasource_id, config_id, update_by=user_id, update_by_name=username)
        if not success:
            return business_response(msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        logger.info(f"删除语义配置: {config_id}")
        return business_response(data={"config_id": config_id}, msg="删除成功")
    except Exception as e:
        logger.error(f"删除语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"删除失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


async def _toggle_semantic_config(datasource_id: str, config_id: str, enabled: bool, request: Request, *, admin_manage: bool = False):
    """切换指标集启用状态。普通入口仅允许维护本人个人指标集；后台入口允许维护公共指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        semantic_service = get_semantic_config_service()
        current_config = await semantic_service.get_config_async(datasource_id, config_id)
        if not current_config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        if not can_mutate_semantic_config(current_config, user_id, admin_manage):
            return business_response(msg="无权限操作该指标集", code=ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED, status="error")

        config = await semantic_service.update_async(
            datasource_id=datasource_id,
            config_id=config_id,
            enabled=enabled,
            update_by=user_id,
            update_by_name=username,
        )
        if not config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        logger.info(f"切换语义配置: {config_id}, enabled={enabled}")
        data = _with_semantic_config_permissions(config, user_id, username, admin_manage=admin_manage)
        return business_response(data=data, msg=f"语义配置已{'启用' if enabled else '禁用'}")
    except Exception as e:
        logger.error(f"切换语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"操作失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.put("/datasources/{datasource_id}/semantic-configs/{config_id}")
async def update_semantic_config(
    datasource_id: str,
    config_id: str,
    payload: SemanticConfigUpdateRequest,
    request: Request,
):
    """更新个人指标集。"""
    return await _update_semantic_config(datasource_id, config_id, payload, request, admin_manage=False)


@router.put("/admin/datasources/{datasource_id}/semantic-configs/{config_id}")
async def update_admin_semantic_config(
    datasource_id: str,
    config_id: str,
    payload: SemanticConfigUpdateRequest,
    request: Request,
):
    """后台更新公共指标集。"""
    _get_current_user(request)
    return await _update_semantic_config(datasource_id, config_id, payload, request, admin_manage=True)


@router.delete("/datasources/{datasource_id}/semantic-configs/{config_id}")
async def delete_semantic_config(datasource_id: str, config_id: str, request: Request):
    """删除个人指标集。"""
    return await _delete_semantic_config(datasource_id, config_id, request, admin_manage=False)


@router.delete("/admin/datasources/{datasource_id}/semantic-configs/{config_id}")
async def delete_admin_semantic_config(datasource_id: str, config_id: str, request: Request):
    """后台删除公共指标集。"""
    _get_current_user(request)
    return await _delete_semantic_config(datasource_id, config_id, request, admin_manage=True)


@router.patch("/datasources/{datasource_id}/semantic-configs/{config_id}/toggle")
async def toggle_semantic_config(datasource_id: str, config_id: str, enabled: bool, request: Request):
    """切换个人指标集启用状态。"""
    return await _toggle_semantic_config(datasource_id, config_id, enabled, request, admin_manage=False)


@router.patch("/admin/datasources/{datasource_id}/semantic-configs/{config_id}/toggle")
async def toggle_admin_semantic_config(datasource_id: str, config_id: str, enabled: bool, request: Request):
    """后台切换公共指标集启用状态。"""
    _get_current_user(request)
    return await _toggle_semantic_config(datasource_id, config_id, enabled, request, admin_manage=True)
